"""Stream complete, validated plans and retain safe timing evidence."""
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from .costs import current

PLANNING_SECONDS = 60

@dataclass
class PlanningTrace:
    path: object
    model: str
    attempt: int
    started: float = field(default_factory=time.monotonic)
    metrics: dict = field(default_factory=dict)

    def mark(self, name):
        self.metrics.setdefault(name, round(time.monotonic()-self.started, 3))

    async def headers(self, response):
        self.mark('headersSeconds')
        self.metrics.update(requestId=response.headers.get('x-request-id'), httpStatus=response.status_code)

    def finish(self, error=None):
        context=current.get()
        self.metrics.update(seconds=round(time.monotonic()-self.started,3),
                            model=self.model,attempt=self.attempt,
                            jobId=getattr(context,'job_id',None),runId=getattr(context,'id',None))
        if error:
            self.metrics.update(errorType=type(error).__name__,causeType=type(error.__cause__).__name__ if error.__cause__ else None)
        logging.getLogger(__name__).info('Photo Scout planner trace: %s',json.dumps(self.metrics))
        if self.path is None:return
        try:
            with sqlite3.connect(self.path,timeout=5) as db:
                db.execute('CREATE TABLE IF NOT EXISTS photo_planning_traces (created REAL NOT NULL,job TEXT,metrics TEXT NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS photo_planning_trace_job ON photo_planning_traces(job,created)')
                db.execute('DELETE FROM photo_planning_traces WHERE created<?',(time.time()-7*86400,))
                db.execute('INSERT INTO photo_planning_traces VALUES(?,?,?)',(time.time(),self.metrics['jobId'],json.dumps(self.metrics)))
        except (sqlite3.Error,OSError):
            logging.getLogger(__name__).warning('Photo Scout planner timing write failed')

async def stream_plan(client, request, trace):
    async with client.responses.stream(**request) as stream:
        async for event in stream:
            trace.mark('firstEventSeconds')
            trace.metrics['lastEventType']=event.type
            trace.metrics['lastEventSeconds']=round(time.monotonic()-trace.started,3)
            if event.type=='response.output_text.delta':trace.mark('firstTextSeconds')
            if event.type=='response.output_text.delta':
                trace.metrics['outputCharacters']=trace.metrics.get('outputCharacters',0)+len(event.delta)
                trace.metrics['textDeltas']=trace.metrics.get('textDeltas',0)+1
            if event.type in ('response.completed','response.incomplete','response.failed'):
                trace.metrics['responseStatus']=event.response.status
                if event.response.usage:trace.metrics['usage']=event.response.usage.model_dump()
        trace.mark('streamCompleteSeconds')
        response=await stream.get_final_response()
        trace.mark('validatedSeconds')
        trace.metrics['responseStatus']=response.status
        if response.usage:trace.metrics['usage']=response.usage.model_dump()
        return response
