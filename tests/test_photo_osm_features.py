import asyncio
import pytest
from pydantic import ValidationError
from agentic_services.photo_scout.osm_features import OSMFeatureQuery,TagFilter,NumericFilter,decode,make_query,number,fetch_features
from agentic_services.photo_scout.search import SearchParameters,SearchProviders,search_locations,SearchUnavailable


def feature(**kwargs):return OSMFeatureQuery(label='Buildings',filters=[TagFilter(key='building')],**kwargs)
def node(id,lat=0,lon=0,**tags):return {'type':'node','id':id,'lat':lat,'lon':lon,'tags':tags}

def test_numeric_unknown_is_retained_and_known_nonmatch_is_excluded():
 q=feature(numericFilters=[NumericFilter(key='height',minimum=30)])
 groups=decode({'elements':[node(1,building='yes',height='15 m'),node(2,building='yes',height='120 ft'),node(3,building='yes')]},[q],0,0,1000)
 assert [p['id'] for p in groups[0]]==['osm:node:2','osm:node:3']
 assert groups[0][1]['unknownAttributes']==['height']
 assert number('120 ft','height')==pytest.approx(36.576)
 assert number('unknown','height') is None

def test_generic_attributes_and_radius():
 q=OSMFeatureQuery(label='Glass',filters=[TagFilter(key='building'),TagFilter(key='building:material',value='glass',required=False)])
 groups=decode({'elements':[node(1,building='yes',**{'building:material':'glass'}),node(2,building='yes'),node(3,lat=1,building='yes',**{'building:material':'glass'})]},[q],0,0,100)
 assert [p['id'] for p in groups[0]]==['osm:node:1','osm:node:2']
 assert groups[0][1]['unknownAttributes']==['building:material']

def test_bounded_queries_escape_values_and_reject_unbounded_or_injected_keys():
 with pytest.raises(ValidationError):OSMFeatureQuery(label='Everything')
 with pytest.raises(ValidationError):TagFilter(key='building];out;')
 with pytest.raises(ValidationError):NumericFilter(key='height',minimum=40,maximum=20)
 q=OSMFeatureQuery(label='Name',filters=[TagFilter(key='name',value='a";out;')])
 query=make_query(0,0,100,[q]);assert '["name"="a\\";out;"]' in query and '1200' in query and 'maxsize' in query

def test_intersections_use_shared_node_topology_not_coordinate_overlap():
 def road(id,refs,coords):return {'type':'way','id':id,'nodes':refs,'geometry':[{'lat':lat,'lon':lon} for lat,lon in coords],'tags':{'highway':'residential'}}
 roads=[road(1,[1,2,3],[(0,-.001),(0,0),(0,.001)]),road(2,[2,4],[(0,0),(.001,0)]),road(3,[5,6],[(-.001,0),(0,0)])]
 q=OSMFeatureQuery(label='Intersection',kind='intersection')
 groups=decode({'elements':roads},[q],0,0,1000)
 assert [p['id'] for p in groups[0]]==['osm:node:2']

@pytest.mark.parametrize('poi', [False,True])
def test_shared_search_features_target_or_filter_pois(tmp_path,poi):
 q=OSMFeatureQuery(label='Lights',filters=[TagFilter(key='highway',value='traffic_signals')],proximityMeters=30)
 matched={'id':'osm:node:1','name':'Lights','lat':0,'lon':0}
 async def named(*args,**kw):
  assert poi
  return [dict(matched,id='cafe'),dict(matched,id='far',lon=.01)],{'status':'ok'}
 async def geo(*args):return [],[],{'status':'not_requested'}
 async def osm(*args):return [[matched]],{'status':'ok'}
 params=SearchParameters(lat=0,lon=0,osmFeatures=[q],poiQueries=['coffee shops'] if poi else [])
 result=asyncio.run(search_locations(params,database_path=tmp_path/'db',providers=SearchProviders(named,named,geo,osm)))
 assert result.plan.mergeStrategy=='feature-search'
 assert [p['id'] for p in result.places]==(['cafe'] if poi else ['osm:node:1'])
 assert result.imagery_targets[0]['kind']=='poi'

def test_multiple_feature_requirements_are_all_required_and_failures_are_explicit(tmp_path):
 q=feature()
 async def named(*args,**kw):return [],{'status':'not_requested'}
 async def geo(*args):return [],[],{'status':'not_requested'}
 async def osm(*args):return [],{'status':'unavailable'}
 with pytest.raises(SearchUnavailable):asyncio.run(search_locations(SearchParameters(lat=0,lon=0,osmFeatures=[q]),database_path=tmp_path/'db',providers=SearchProviders(named,named,geo,osm)))

def test_osm_cache_avoids_repeat_request(tmp_path,monkeypatch):
 import agentic_services.photo_scout.osm_features as module
 calls=[]
 async def get(*args,**kwargs):calls.append(1);return {'elements':[node(1,building='yes')]}
 monkeypatch.setattr(module,'get_json',get)
 q=feature();a=asyncio.run(fetch_features(0,0,100,[q],tmp_path/'db'));b=asyncio.run(fetch_features(0,0,100,[q],tmp_path/'db'))
 assert len(calls)==1 and a[0]==b[0] and b[1]['cached']

def test_feature_conditions_are_part_of_scoring_cache_identity():
 from agentic_services.photo_scout.routes import ExploreRequest
 from agentic_services.photo_scout.score_cache import ScoreCache
 base=ExploreRequest(lat=0,lon=0)
 request=ExploreRequest(lat=0,lon=0,osmFeatures=[feature()])
 assert ScoreCache.key({'id':'same-view'},base,'model','prompt')!=ScoreCache.key({'id':'same-view'},request,'model','prompt')


def test_footprint_hint_prioritizes_large_candidates_without_excluding_small_or_unknown():
    def building(id,size,levels=None):
        return {'type':'way','id':id,'center':{'lat':0,'lon':0},'tags':{'building':'house',**({'building:levels':str(levels)} if levels else {})},
            'geometry':[{'lat':0,'lon':0},{'lat':0,'lon':size},{'lat':size,'lon':size},{'lat':size,'lon':0},{'lat':0,'lon':0}]}
    q=OSMFeatureQuery(label='Homes',filters=[TagFilter(key='building',value='house')],selectionHints=['large-footprint'])
    elements=[building(1,.0001),building(2,.0004),{'type':'way','id':3,'center':{'lat':0,'lon':0},'tags':{'building':'house'}}]
    rows=decode({'elements':elements},[q],0,0,1000)[0]
    assert [p['id'] for p in rows]==['osm:way:2','osm:way:1','osm:way:3']
    assert rows[0]['footprintAreaMeters2']>rows[1]['footprintAreaMeters2']>0
    assert len(rows)==3 and rows[-1]['footprintAreaMeters2'] is None
    assert 'out center tags geom' in make_query(0,0,1000,[q])
    q=q.model_copy(update={'selectionHints':['small-footprint']})
    assert decode({'elements':elements},[q],0,0,1000)[0][0]['id']=='osm:way:1'


def test_storeys_hint_is_soft_and_does_not_add_a_guessed_threshold():
    q=feature(selectionHints=['more-storeys'])
    elements=[node(1,building='yes',**{'building:levels':'2'}),node(2,building='yes',**{'building:levels':'12'}),node(3,building='yes')]
    rows=decode({'elements':elements},[q],0,0,1000)[0]
    assert [p['id'] for p in rows]==['osm:node:2','osm:node:1','osm:node:3']
    assert not q.numericFilters
    assert ' geom ' not in make_query(0,0,1000,[q])
    with pytest.raises(ValidationError,match='Building selection hints'):
        OSMFeatureQuery(label='Lights',filters=[TagFilter(key='highway',value='traffic_signals')],selectionHints=['more-storeys'])


def test_nonfinite_provider_attribute_is_unknown_instead_of_a_serialized_priority():
    assert number('9'*400,'building:levels') is None


def test_geometry_only_buildings_and_relations_keep_positions_and_radius_filter():
    q=feature(selectionHints=['large-footprint'])
    points=[{'lat':0,'lon':0},{'lat':0,'lon':.002},{'lat':.002,'lon':.002},{'lat':.002,'lon':0},{'lat':0,'lon':0}]
    way={'type':'way','id':1,'tags':{'building':'yes'},'geometry':points}
    relation={'type':'relation','id':2,'tags':{'building':'yes'},'members':[{'type':'way','geometry':points}]}
    far={'type':'way','id':3,'tags':{'building':'yes'},'geometry':[{'lat':2,'lon':2},{'lat':2.001,'lon':2.001}]}
    rows=decode({'elements':[way,relation,far]},[q],0,0,1000)[0]
    assert {r['id'] for r in rows}=={'osm:way:1','osm:relation:2'}
    assert all(r['lat']==pytest.approx(.001) and r['lon']==pytest.approx(.001) for r in rows)
    assert next(r for r in rows if r['id']=='osm:way:1')['footprintAreaMeters2']>0


def test_geometry_position_handles_dateline_without_moving_building_to_greenwich():
    from agentic_services.photo_scout.osm_features import element_position
    p=element_position({'type':'way','geometry':[{'lat':0,'lon':179.999},{'lat':.002,'lon':-179.999}]})
    assert abs(p['lon'])==pytest.approx(180)
    assert p['lat']==pytest.approx(.001)
