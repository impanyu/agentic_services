from agentic_services.photo_scout.spatial_sampling import spread_points
from agentic_services.photo_scout.osm_features import OSMFeatureQuery,TagFilter,make_query,decode


def test_spatial_order_preserves_rows_and_prevents_one_dense_cluster_filling_all_slots():
    rows=[{'id':str(i),'lat':.001+i*.000001,'lon':.001,'distanceMeters':100+i} for i in range(200)]
    rows += [{'id':label,'lat':lat,'lon':lon,'distanceMeters':15000} for label,lat,lon in [('west',0,-.12),('east',0,.12),('south',-.12,0),('north',.12,0)]]
    original=list(rows)
    ordered=spread_points(rows,0,0,20000)
    assert {'west','east','south','north'} <= {p['id'] for p in ordered[:25]}
    assert len(ordered)==len(rows) and {p['id'] for p in ordered}=={p['id'] for p in rows}
    assert rows==original


def test_priority_budget_retains_strong_cluster_and_spatial_exploration():
    def row(id,lat,lon,area):
        return {'id':id,'lat':lat,'lon':lon,'distanceMeters':1000,
                'selectionHints':['large-footprint'],'selectionPriority':[(0,-area)]}
    strong=[row(f'strong-{i}',.001,.001,1000-i) for i in range(20)]
    weak=[row(f'weak-{i}',lat,lon,100) for i,(lat,lon) in enumerate([(0,-.12),(0,.12),(-.12,0),(.12,0)])]
    unknown=row('unknown',-.1,-.1,0);unknown['selectionPriority']=[(1,0)]
    rows=weak+strong+[unknown]
    ordered=spread_points(rows,0,0,20000)
    assert sum(p['id'].startswith('strong') for p in ordered[:12])>=8
    assert sum(p['id'].startswith('weak') for p in ordered[:12])>=3
    assert len(ordered)==len(rows) and {p['id'] for p in ordered}=={p['id'] for p in rows}
    # Reordering at a second executor stage must not undo proxy allocation.
    again=spread_points(ordered,0,0,20000)
    assert sum(p['id'].startswith('strong') for p in again[:12])>=8


def test_incomparable_priorities_keep_spatial_coverage():
    rows=[{'id':'a','lat':.001,'lon':.001,'selectionHints':['small-footprint'],'selectionPriority':[(0,100)]},
          {'id':'b','lat':0,'lon':-.12,'selectionHints':['large-footprint'],'selectionPriority':[(0,-500)]}]
    assert [p['id'] for p in spread_points(rows,0,0,20000)]==['a','b']


def test_dense_building_queries_cover_multiple_tiles_without_increasing_output_budget():
    q=OSMFeatureQuery(label='Houses',filters=[TagFilter(key='building',value='house')])
    query=make_query(40,-96,20000,[q])
    assert query.count('out center tags 75;')==16
    assert query.count('["building"="house"]')==16
    assert '[timeout:8]' in query and '[maxsize:16777216]' in query
    assert make_query(40,-96,100,[q]).count('out center tags 1200;')==1
    signal=OSMFeatureQuery(label='Signals',filters=[TagFilter(key='highway',value='traffic_signals')])
    assert make_query(40,-96,20000,[signal]).count('out center tags 1200;')==1


def test_decoded_feature_cap_retains_distant_matches_and_excludes_wrong_tags_and_radius():
    q=OSMFeatureQuery(label='Houses',filters=[TagFilter(key='building',value='house')])
    elements=[{'type':'node','id':i,'lat':.001+i*.000001,'lon':.001,'tags':{'building':'house'}} for i in range(200)]
    elements += [{'type':'node','id':1000+i,'lat':lat,'lon':lon,'tags':{'building':'house'}} for i,(lat,lon) in enumerate([(0,-.12),(0,.12),(-.12,0),(.12,0)])]
    elements += [{'type':'node','id':2000,'lat':0,'lon':0,'tags':{'building':'apartments'}},{'type':'node','id':2001,'lat':1,'lon':1,'tags':{'building':'house'}}]
    rows=decode({'elements':elements},[q],0,0,20000)[0]
    assert len(rows)==120
    assert {f'osm:node:{1000+i}' for i in range(4)} <= {r['id'] for r in rows[:25]}
    assert not {'osm:node:2000','osm:node:2001'} & {r['id'] for r in rows}

def test_provider_outage_preserves_same_query_cache_without_leaking_other_conditions(tmp_path,monkeypatch):
    import asyncio,hashlib,json,sqlite3,time
    import agentic_services.photo_scout.osm_features as module
    query=OSMFeatureQuery(label='Houses',filters=[TagFilter(key='building',value='house')])
    old_key=hashlib.sha256(json.dumps([40,-96,20000,[query.model_dump(exclude={'selectionHints'})]],sort_keys=True).encode()).hexdigest()
    path=tmp_path/'db';saved=[[{'id':'existing-house','lat':40,'lon':-96}]]
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE photo_osm_feature_cache (key TEXT PRIMARY KEY,data TEXT NOT NULL,expires REAL NOT NULL)')
        db.execute('INSERT INTO photo_osm_feature_cache VALUES(?,?,?)',(old_key,json.dumps(saved),time.time()+3600))
    async def unavailable(*a,**k):raise ValueError('Provider unavailable')
    monkeypatch.setattr(module,'get_json',unavailable)
    rows,status=asyncio.run(module.fetch_features(40,-96,20000,[query],path))
    assert rows==saved and status['spatialFallback']=='legacy_cache'
    other=OSMFeatureQuery(label='Signals',filters=[TagFilter(key='highway',value='traffic_signals')])
    rows,status=asyncio.run(module.fetch_features(40,-96,20000,[other],path))
    assert rows==[] and status['status']=='unavailable'
