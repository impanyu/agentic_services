import asyncio
import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from agentic_services.photo_scout import route_search as route
from agentic_services.photo_scout.routes import ExploreRequest
from agentic_services.photo_scout.sources import distance


def geometry(points,**changes):
    return dict(geometry={'type':'LineString','coordinates':[[lon,lat] for lat,lon in points]},
                distanceMeters=round(sum(distance(a,b) for a,b in zip(points,points[1:]))),corridorMeters=300,travelMode='walk',**changes)

def test_route_geometry_sampling_uses_distance_not_vertex_count():
    points=route.decode_polyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    assert points==[(38.5,-120.2),(40.7,-120.95),(43.252,-126.453)]
    samples=route.sample_route([(0,0),(0,.001),(0,.01)],3)
    assert samples[1]==pytest.approx((0,.005))
    offset,progress=route.along_route((.001,.005),[(0,0),(0,.01)])
    assert offset==pytest.approx(111.32);assert progress==pytest.approx(556,abs=2)
    with pytest.raises(ValueError):route.decode_polyline('_p~iF')

def test_route_date_line_is_a_short_journey():
    points=[(0,179.99),(0,-179.99)]
    assert abs(route.sample_route(points,3)[1][1])==pytest.approx(180)
    assert route.along_route((0,180),points)[0]<1
    assert route.along_route((0,0),points)[0]>1e7

def test_endpoint_validation_and_resolution():
    with pytest.raises(ValidationError):route.RouteEndpoint(lat=30)
    with pytest.raises(ValidationError):route.RouteEndpoint(query=' ')
    with pytest.raises(ValidationError):route.RouteRequest(destination={'lat':40,'lon':-96},travelMode='bike')
    calls=[]
    async def geocode(q):calls.append(q);return [{'lat':40,'lon':-95.99,'label':'Finish'}]
    r=asyncio.run(route.resolve_endpoints(route.RouteRequest(destination={'query':'Finish'}),40,-96,geocode))
    assert r.travelMode=='walk' and r.origin.lat==40 and r.destination.label=='Finish'
    assert calls==['Finish']
    async def none(q):return []
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.resolve_endpoints(route.RouteRequest(destination={'query':'not-found'}),40,-96,none))
    assert error.value.status_code==422


def test_google_routing_transport_uses_real_polyline_and_redacts_failure(monkeypatch):
    monkeypatch.setenv('PHOTO_SCOUT_GOOGLE_API_KEY','private-key')
    real=httpx.AsyncClient;requests=[]
    def transport(request):
        requests.append(request)
        return httpx.Response(200,json={'routes':[{'distanceMeters':1000,'duration':'600s','polyline':{'encodedPolyline':'??o}@?'}}]})
    monkeypatch.setattr(route.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport)))
    input=route.RouteRequest(origin={'lat':0,'lon':0},destination={'lat':.01,'lon':0})
    result=asyncio.run(route.compute_route(input))
    assert result['geometry']['type']=='LineString' and result['travelMode']=='walk'
    assert requests[0].headers['X-Goog-Api-Key']=='private-key'
    assert b'WALK' in requests[0].content and 'private-key' not in str(result)
    monkeypatch.setattr(route.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(403))))
    with pytest.raises(HTTPException) as error:asyncio.run(route.compute_route(input))
    assert error.value.status_code==503 and 'private-key' not in str(error.value.detail)


def test_corridor_filters_pois_and_actual_camera_positions_global_cap():
    payload=ExploreRequest(lat=0,lon=0,poiQueries=['cafes'],route={'destination':{'lat':0,'lon':.1}})
    r=geometry([(0,0),(0,.1)])
    found=[];fetches=[]
    async def lookup(local,include_geometry=False):
        assert local.route is None and include_geometry
        rows=[{'id':f'cafe:{local.lon}:{i}','name':'Cafe','lat':0,'lon':local.lon+i*.00005} for i in range(60)]
        rows.append({'id':'off-route','name':'Far cafe','lat':.02,'lon':local.lon})
        return rows,{'status':'ok'}
    async def images(lat,lon,radius,pois):
        fetches.extend(pois)
        rows=[{'id':p['id'],'provider':'google-street-view','lat':p['lat'],'lon':p['lon'],'poi':p} for p in pois]
        rows.append({'id':'camera-away','provider':'google-street-view','lat':.02,'lon':lon})
        return rows,{'google-street-view':{'status':'ok','eligibleImages':len(rows),'queriedLocations':len(pois)}}
    rows,status,pois=asyncio.run(route.discover_route(payload,r,lookup,images))
    assert len(pois)==50 and len(fetches)==50 and len(rows)==50
    assert len({round(p['lon'],2) for p in pois})>=3,'must cover multiple route sections'
    assert all(p['id']!='off-route' for p in pois) and all(x['id']!='camera-away' for x in rows)
    assert not any(p.get('routeSample') for p in fetches),'category requests do not add unrelated road points'
    assert all(row['routeOffsetMeters']==0 for row in rows)


def test_generic_route_preserves_road_views_without_fake_named_pois():
    payload=ExploreRequest(lat=0,lon=0,route={'destination':{'lat':0,'lon':.01}})
    async def lookup(local,**kw):return [],{'status':'ok'}
    async def images(lat,lon,radius,pois):
        return [{'id':p['id'],'provider':'google-street-view','lat':p['lat'],'lon':p['lon'],'poi':p,'poiCandidates':[p]} for p in pois],{'google-street-view':{'status':'ok'}}
    r=geometry([(0,0),(0,.01)])
    rows,status,pois=asyncio.run(route.discover_route(payload,r,lookup,images))
    assert len(rows)==20 and not pois and r['sampledLocations']==20
    assert all(row['allowUnlistedPlace'] and 'poi' not in row for row in rows)


def test_blank_intent_preserves_route_ui_and_resolves_endpoints(monkeypatch):
    from agentic_services.photo_scout import intent
    async def parsed(settings,payload):
        return intent.PhotoIntent(locationQuery='stale',useMapCenter=False,radiusMeters=1000,
            photoStyles=['urban'],preferences='scenery',explanation='Route',clarification=None,
            route={'destination':{'query':'stale'}})
    seen=[]
    async def geocode(q):seen.append(q);return [{'lat':0,'lon':.01,'label':'Finish'}]
    monkeypatch.setattr(intent,'parse_intent',parsed);monkeypatch.setattr(intent,'geocode',geocode)
    payload=intent.IntentRequest(lat=0,lon=0,route={'destination':{'query':'Correct finish'},'travelMode':'drive'})
    result=asyncio.run(intent.resolve_intent(None,payload))
    assert seen==['Correct finish'] and result['route']['travelMode']=='drive'
    assert result['locations'][0]['label']=='Selected map location → Finish'
    assert result['photoStyles']==[]


def test_durable_route_survives_reload_and_scores_one_global_batch(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agentic_services.config import Settings
    from agentic_services.main import create_app
    from agentic_services.photo_scout import routes
    monkeypatch.setenv('PHOTO_SCOUT_ENABLED','1');monkeypatch.setenv('PHOTO_SCOUT_HUMAN_FREE_PREVIEW','1')
    settings=Settings(openai_api_key='fixture',openai_model='test',database_path=tmp_path/'db',base_url='https://api.test',service_api_key='private')
    async def resolve(settings,payload):
        return {**payload.model_dump(),'locations':[{'lat':0,'lon':0,'label':'Start → End'}],
            'radiusMeters':payload.radius,'preferences':'scenery','photoStyles':[]}
    async def computed(r):return geometry([(0,0),(0,.01)],origin=r.origin.model_dump(),destination=r.destination.model_dump())
    async def nearby(*args,**kw):return [],{'status':'ok'}
    async def candidates(lat,lon,radius,pois,**kw):
        return [{'id':p['id'],'provider':'google-street-view','lat':p['lat'],'lon':p['lon'],'poi':p} for p in pois],{'google-street-view':{'status':'ok'}}
    calls=[]
    async def score(settings,payload,rows,status):calls.append(len(rows));return {'spots':[],'poiResults':[],'summary':'Route result'}
    monkeypatch.setattr(routes,'resolve_intent',resolve);monkeypatch.setattr(routes,'compute_route',computed)
    monkeypatch.setattr(routes,'nearby_pois',nearby);monkeypatch.setattr(routes,'candidates',candidates);monkeypatch.setattr(routes,'explore',score)
    client=TestClient(create_app(settings=settings),base_url='https://api.test',headers={'Authorization':'Bearer private'})
    response=client.post('/photo-scout/v1/jobs',headers={'X-Request-Token':'r'*32},json={'lat':0,'lon':0,'route':{'destination':{'lat':0,'lon':.01}}})
    assert response.status_code==202;job=response.json()['jobId']
    restarted=create_app(settings=settings)
    assert asyncio.run(restarted.state.process_photo_preview())
    client=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'},cookies=client.cookies)
    report=client.get('/photo-scout/v1/report/'+job).json()
    assert report['state']=='complete' and report['result']['route']['geometry']['type']=='LineString'
    assert report['context']['routeGeometry']['travelMode']=='walk'
    assert calls==[20],'all sections must be scored together once'
    stranger=TestClient(restarted,base_url='https://api.test',headers={'Authorization':'Bearer private'})
    assert stranger.get('/photo-scout/v1/report/'+job).status_code==404
    schema=client.get('/photo-scout/openapi.json').json()['components']['schemas']['ExploreRequest']
    assert 'route' in schema['properties']


def test_route_image_outage_is_not_misreported_as_zero_matches():
    payload=ExploreRequest(lat=0,lon=0,route={'destination':{'lat':0,'lon':.01}})
    async def lookup(local,**kw):return [],{'status':'ok'}
    async def images(*args):return [],{'google-street-view':{'status':'unavailable'}}
    with pytest.raises(HTTPException) as error:
        asyncio.run(route.discover_route(payload,geometry([(0,0),(0,.01)]),lookup,images))
    assert error.value.status_code==503 and 'temporarily unavailable' in error.value.detail
