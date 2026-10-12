import asyncio,base64,io,json,sqlite3
from types import SimpleNamespace
import pytest,httpx
from PIL import Image
from fastapi.testclient import TestClient
from agentic_services.config import Settings
from agentic_services.main import create_app
from agentic_services.photo_scout import portraits,image_generation


def photo(color):
    out=io.BytesIO();Image.new('RGB',(32,48),color).save(out,format='PNG');return out.getvalue()

def data(raw):return 'data:image/png;base64,'+base64.b64encode(raw).decode()

@pytest.mark.parametrize('empty',[False,True])
def test_group_upload_keeps_independent_references_and_cleans_private_bytes(tmp_path,monkeypatch,empty):
    monkeypatch.setenv('PHOTO_SCOUT_IMAGE_MODEL','gpt-image-2.5-sunburst')
    photos=[photo('red'),photo('blue')];checked=[];edits=[]
    class Client:
        def __init__(self,**kw):self.images=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def edit(self,**kw):
            edits.append(kw);return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(photo('green')).decode())])
    async def check(client,raw,model):
        checked.append(raw);return 0 if empty and raw==photos[1] else 2
    async def background(*args):return photo('green'),'https://upload.wikimedia.org/test.png',{'scene':{}}
    monkeypatch.setattr(portraits,'AsyncOpenAI',Client);monkeypatch.setattr(portraits,'check_subjects',check);monkeypatch.setattr(portraits,'prepare_background',background)
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    app=create_app(settings=settings);client=TestClient(app,base_url='https://api.test');auth={'Authorization':'Bearer private'}
    body={'portraits':list(map(data,photos)),'background':'https://upload.wikimedia.org/test.png','provider':'wikimedia-commons','place':'Test park'}
    job=client.post('/photo-scout/v1/portraits',json=body,headers=auth)
    assert job.status_code==202
    job=job.json()
    with sqlite3.connect(settings.database_path) as db:
        stored=db.execute('SELECT photo FROM photo_portraits').fetchone()[0]
        assert portraits.subject_photos(stored)==photos
    assert asyncio.run(app.state.process_photo_portrait())
    state=client.get('/photo-scout/v1/portraits/'+job['id'],headers=auth|{'X-Report-Token':job['token']}).json()
    assert checked==photos and state['context']['subjectReferenceCount']==2
    if empty:
        assert not edits and state['state']=='failed' and 'Reference photo 2' in state['error']
    else:
        assert state['state']=='complete'
        assert [image[1] for image in edits[0]['image']]==photos+[photo('green')]
        assert 'Input 3 is the only destination background' in edits[0]['prompt']
        assert 'from EVERY subject reference' in edits[0]['prompt']
    with sqlite3.connect(settings.database_path) as db:assert db.execute('SELECT photo,payload FROM photo_portraits').fetchone()==(None,None)
    assert 'base64' not in json.dumps(state)


def test_group_request_bounds_and_legacy_compatibility():
    common={'background':'url','provider':'wikimedia-commons','place':'Park'}
    for extra in ({},{'portraits':[]},{'portrait':'x','portraits':['y']},{'portraits':['x']*10},{'portraits':['']},{'portraits':['x'*14000000]*2}):
        with pytest.raises(ValueError):portraits.PortraitRequest(**common,**extra)
    assert portraits.PortraitRequest(**common,portrait='legacy').portrait=='legacy'
    assert len(portraits.PortraitRequest(**common,portraits=['a']*9).portraits)==9
    raw=photo('red');assert portraits.subject_photos(raw)==[raw]


def test_google_receives_all_subjects_then_single_background(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','fixture');real=httpx.AsyncClient;calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        return httpx.Response(200,json={})
    monkeypatch.setattr(image_generation.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    asyncio.run(image_generation.google_response(image_generation.DEFAULT_MODEL,[b'person-a',b'person-b'],b'background','image/jpeg','Group'))
    refs=calls[0]['contents'][0]['parts'][1:]
    assert [base64.b64decode(p['inlineData']['data']) for p in refs]==[b'person-a',b'person-b',b'background']
