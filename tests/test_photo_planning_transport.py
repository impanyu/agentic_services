import asyncio,json,sqlite3
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from agentic_services.photo_scout import intent
from agentic_services.photo_scout.planning_transport import PlanningTrace,stream_plan
from test_photo_planner import output

class Stream:
    def __init__(self,value,delay=0,error=None):self.value=value;self.delay=delay;self.error=error;self.closed=False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    async def __aiter__(self):
        yield SimpleNamespace(type='response.created')
        await asyncio.sleep(self.delay)
        yield SimpleNamespace(type='response.output_text.delta',delta='PRIVATE BODY MUST NOT BE LOGGED')
        yield SimpleNamespace(type='response.completed',response=SimpleNamespace(status='completed',usage=SimpleNamespace(model_dump=lambda:{'output_tokens':900})))
    async def get_final_response(self):
        if self.error:raise self.error
        return SimpleNamespace(output_parsed=intent.PlannerIntent.model_validate(self.value),status='completed',usage=SimpleNamespace(model_dump=lambda:{'output_tokens':900}))


def client(monkeypatch,streams):
    calls=[]
    class Client:
        def __init__(self,**kwargs):self.responses=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def stream(self,**request):calls.append(request);return streams[len(calls)-1]
    monkeypatch.setattr(intent,'AsyncOpenAI',Client)
    return calls


def settings(tmp_path):return SimpleNamespace(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db')
def payload():return intent.IntentRequest(query='motels',lat=40,lon=-96)


def test_complete_stream_validates_and_retains_timings_without_model_body(tmp_path,monkeypatch):
    stream=Stream(output());calls=client(monkeypatch,[stream])
    plan=asyncio.run(intent.parse_intent(settings(tmp_path),payload()))
    assert plan.searchProgram.complete and stream.closed and len(calls)==1
    with sqlite3.connect(tmp_path/'db') as db:
        record=db.execute('SELECT metrics FROM photo_planning_traces').fetchone()[0]
    assert 'PRIVATE BODY' not in record and 'motels' not in record
    trace=json.loads(record);assert trace['firstTextSeconds']<=trace['seconds'] and trace['usage']['output_tokens']==900
    assert trace['responseStatus']=='completed' and 'validatedSeconds' in trace


def test_stream_schema_error_is_repaired_once_under_same_deadline(tmp_path,monkeypatch):
    malformed=output();malformed['sourceCoverage']=[]
    streams=[Stream(malformed),Stream(output())];calls=client(monkeypatch,streams)
    plan=asyncio.run(intent.parse_intent(settings(tmp_path),payload()))
    assert plan.searchProgram.complete and len(calls)==2 and all(s.closed for s in streams)
    assert 'repair' in json.loads(calls[1]['input'])
    with sqlite3.connect(tmp_path/'db') as db:
        traces=[json.loads(r[0]) for r in db.execute('SELECT metrics FROM photo_planning_traces')]
    assert traces[0]['errorType']=='ValidationError' and traces[1]['attempt']==2
    assert traces[0]['usage']['output_tokens']==900


def test_overall_deadline_closes_stream_without_retrying_or_accepting_partial_plan(tmp_path,monkeypatch):
    stream=Stream(output(),delay=.05);calls=client(monkeypatch,[stream])
    monkeypatch.setattr(intent,'PLANNING_SECONDS',.01)
    with pytest.raises(TimeoutError):asyncio.run(intent.parse_intent(settings(tmp_path),payload()))
    assert stream.closed and len(calls)==1
    with sqlite3.connect(tmp_path/'db') as db:trace=json.loads(db.execute('SELECT metrics FROM photo_planning_traces').fetchone()[0])
    assert trace['lastEventType']=='response.created' and 'validatedSeconds' not in trace


def test_provider_failure_is_not_repaired_or_silently_replaced(tmp_path,monkeypatch):
    stream=Stream(output(),error=RuntimeError('provider failure'));calls=client(monkeypatch,[stream])
    with pytest.raises(RuntimeError):asyncio.run(intent.parse_intent(settings(tmp_path),payload()))
    assert stream.closed and len(calls)==1


def test_repair_has_no_fresh_wall_clock_budget(tmp_path,monkeypatch):
    malformed=output();malformed['sourceCoverage']=[]
    streams=[Stream(malformed,delay=.005),Stream(output(),delay=.05)];calls=client(monkeypatch,streams)
    monkeypatch.setattr(intent,'PLANNING_SECONDS',.02)
    with pytest.raises(TimeoutError):asyncio.run(intent.parse_intent(settings(tmp_path),payload()))
    assert len(calls)==2 and all(s.closed for s in streams)
