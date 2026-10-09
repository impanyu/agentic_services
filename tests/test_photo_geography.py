import asyncio
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from shapely.geometry import Point

from agentic_services.photo_scout import geography as geo, routes
from agentic_services.config import Settings
from agentic_services.main import create_app


def lake():
    return {'id':'osm:way:1','name':'Example Lake','sourceUrl':'https://www.openstreetmap.org/way/1',
            'kinds':['lake'],'geometry':{'type':'Polygon','coordinates':[[[-.004,-.004],[.004,-.004],[.004,.004],[-.004,.004],[-.004,-.004]]]}}


def test_lakeside_candidates_use_paths_and_shore_not_lake_centroid():
    paths=[{'id':'road','name':'Shore Trail','geometry':{'type':'LineString','coordinates':[[-.005,-.005],[-.005,.005]]}}]
    rows=geo.geographic_places(0,0,2000,[lake()],paths,['lake'])
    assert len(rows)>1 and len(rows)<=30
    assert all(abs(p['lon']+.005)<.000001 for p in rows)
    assert all(geo.matches_position(p['lat'],p['lon'],[lake()],['lake']) for p in rows)
    assert not geo.matches_position(0,0,[lake()],['lake'])
    assert geo.filter_places([{'lat':0,'lon':0},{'lat':0,'lon':-.005}],[lake()],['lake'],0,0)==[{'lat':0,'lon':-.005}]


def test_relation_reconstructs_outer_segments_and_preserves_island_hole():
    def coords(points):return [{'lon':x,'lat':y} for x,y in points]
    element={'type':'relation','members':[
        {'role':'outer','geometry':coords([(0,0),(2,0),(2,2)])},
        {'role':'outer','geometry':coords([(2,2),(0,2),(0,0)])},
        {'role':'inner','geometry':coords([(.5,.5),(1.5,.5),(1.5,1.5),(.5,1.5),(.5,.5)])}]}
    polygon=geo.geometry(element)
    assert polygon.area==3 and polygon.covers(Point(.2,.2)) and not polygon.covers(Point(1,1))
    assert geo.geometry({'type':'relation','members':[element['members'][0]]}) is None


def test_multiple_geographic_requirements_are_intersected():
    forest={'id':'forest','kinds':['forest'],'geometry':{'type':'Polygon','coordinates':[[[-.01,-.01],[0,-.01],[0,.01],[-.01,.01],[-.01,-.01]]]}}
    assert geo.matches_position(0,-.005,[lake(),forest],['lake','forest'])
    assert not geo.matches_position(0,.005,[lake(),forest],['lake','forest'])


def test_private_paths_and_motorways_do_not_generate_candidate_positions():
    def road(tags):return {'type':'way','id':1,'tags':tags,'geometry':[{'lat':0,'lon':0},{'lat':0,'lon':.01}]}
    for tags in ({'highway':'footway','access':'private'},{'highway':'motorway'},{'highway':'path','foot':'no'}):
        assert geo.decode({'elements':[road(tags)]},['lake'])[1]==[]


def test_successful_geometry_is_cached_and_source_query_keeps_full_geometry(tmp_path,monkeypatch):
    calls=[]
    async def json_data(client,url,params,**kwargs):
        calls.append(params['data'])
        assert 'around.features:160' in params['data'] and 'out geom' in params['data']
        return {'elements':[{'type':'way','id':1,'tags':{'natural':'water','water':'lake'},
            'geometry':[{'lat':y,'lon':x} for x,y in lake()['geometry']['coordinates'][0]]}]}
    monkeypatch.setattr(geo,'get_json',json_data)
    async def run():
        first=await geo.fetch_region(0,0,2000,['lake'],tmp_path/'db')
        second=await geo.fetch_region(0,0,2000,['lake'],tmp_path/'db')
        assert first[0]==second[0] and not first[2]['cached'] and second[2]['cached']
    asyncio.run(run());assert len(calls)==1


def test_fixed_search_keeps_lakeside_cafes_and_excludes_displaced_panorama(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    monkeypatch.setenv('PHOTO_SCOUT_EXPLORER_ENABLED','1')  # Obsolete switch cannot enable agent execution.
    arrived=set();barrier=asyncio.Event()
    async def entered(name):
        arrived.add(name)
        if len(arrived)==2:barrier.set()
        await asyncio.wait_for(barrier.wait(),1)
    async def named(lat,lon,radius,queries,**kwargs):
        await entered('places');assert queries==['coffee shops']
        return [{'id':'shore','name':'Lake Cafe','lat':0,'lon':-.005},
                {'id':'far','name':'Inland Cafe','lat':0,'lon':-.015}],{'status':'ok'}
    async def geographic(*args):
        await entered('geography');return [lake()],[],{'status':'ok'}
    async def images(lat,lon,radius,targets):
        assert [p['id'] for p in targets]==['shore']
        return [{'id':'good','lat':0,'lon':-.005,'provider':'google-street-view'},
                {'id':'displaced','lat':0,'lon':-.007,'provider':'google-street-view'}],{'google-street-view':{'status':'ok'}}
    async def score(settings,payload,rows,statuses):
        assert [r['id'] for r in rows]==['good']
        assert statuses['google-street-view']['geographicallyExcludedImages']==1
        assert payload.scoringIntent=='Quiet lakeside coffee shops'
        return {'spots':[],'poiResults':[],'sources':statuses,'summary':'No suitable images'}
    monkeypatch.setattr(routes,'nearby_places',named);monkeypatch.setattr(routes,'fetch_region',geographic)
    monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',score)
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings))
    result=client.post('/photo-scout/v1/preview',headers={'Authorization':'Bearer private'},json={
        'lat':0,'lon':0,'radius':2000,'poiQueries':['coffee shops'],'geographicKinds':['lake'],'scoringIntent':'Quiet lakeside coffee shops'})
    assert result.status_code==200
    assert result.json()['discoveryMethod']=='fixed-geographic-and-poi'
    assert result.json()['candidatePoiCount']==1
    assert result.json()['spots']==[]


def test_natural_language_api_query_overrides_structured_fields(tmp_path,monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    async def resolve(settings,payload):
        assert payload.query=='seaside cafes within 2 km'
        return {'locations':[{'lat':1,'lon':2}],'radiusMeters':2000,'photoStyles':['waterside'],
                'preferences':'Visible sea','poiQueries':['coffee shops'],'scoringIntent':'Seaside cafes','geographicKinds':['sea']}
    async def geographic(*args):return [],[],{'status':'ok'}
    async def named(lat,lon,radius,queries,**kwargs):
        assert (lat,lon,radius)==(1,2,2000);return [],{'status':'ok'}
    async def images(*args,**kw):return [],{}
    async def score(settings,payload,*args):
        assert payload.poiQueries==['coffee shops'] and payload.geographicKinds==['sea']
        return {'spots':[],'summary':'No imagery'}
    monkeypatch.setenv('PHOTO_SCOUT_POI_PROVIDER','google-places')
    monkeypatch.setattr(routes,'resolve_intent',resolve);monkeypatch.setattr(routes,'fetch_region',geographic)
    monkeypatch.setattr(routes,'nearby_places',named);monkeypatch.setattr(routes,'candidates',images);monkeypatch.setattr(routes,'explore',score)
    settings=Settings(openai_api_key='test',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    client=TestClient(create_app(settings=settings))
    result=client.post('/photo-scout/v1/preview',headers={'Authorization':'Bearer private'},json={
        'query':'seaside cafes within 2 km','lat':0,'lon':0,'radius':500,'poiQueries':['motels'],'photoStyles':['urban']})
    assert result.status_code==200 and result.json()['geographicKinds']==['sea']


def test_vector_tile_shore_stitches_adjacent_polygons_without_false_internal_shore():
    from shapely.geometry import Polygon,LineString
    from agentic_services.photo_scout.geographic_tiles import stitch
    rows=[{'layer':'water','properties':{'class':'lake'},'kinds':['lake','waterside'],
           'geometry':Polygon([(-.004,-.004),(0,-.004),(0,.004),(-.004,.004)])},
          {'layer':'water','properties':{'class':'lake'},'kinds':['lake','waterside'],
           'geometry':Polygon([(0,-.004),(.004,-.004),(.004,.004),(0,.004)])},
          {'layer':'transportation','properties':{'class':'path'},'kinds':[],
           'geometry':LineString([(-.005,-.005),(-.005,.005)])}]
    features,paths=stitch(rows,['lake'])
    assert len(features)==1 and len(paths)==1
    assert not geo.matches_position(0,0,features,['lake'])
    places=geo.geographic_places(0,0,2000,features,paths,['lake'])
    assert places and all(geo.matches_position(p['lat'],p['lon'],features,['lake']) for p in places)


def test_overpass_failure_uses_vector_fallback_and_preserves_provenance_in_cache(tmp_path,monkeypatch):
    from agentic_services.photo_scout import geographic_tiles
    calls=[]
    async def fail(*args,**kwargs):raise ValueError('Unavailable')
    async def fallback(*args):
        calls.append(1);return [lake()],[],{'status':'ok','provider':'openfreemap-osm-vector','geometryQuality':'generalized-map-geometry'}
    monkeypatch.setattr(geo,'get_json',fail);monkeypatch.setattr(geographic_tiles,'fetch_tiles',fallback)
    async def run():
        first=await geo.fetch_region(0,0,2000,['lake'],tmp_path/'db')
        second=await geo.fetch_region(0,0,2000,['lake'],tmp_path/'db')
        assert first[2]['provider']==second[2]['provider']=='openfreemap-osm-vector'
        assert second[2]['cached'] and first[0]==second[0]
    asyncio.run(run());assert calls==[1]
