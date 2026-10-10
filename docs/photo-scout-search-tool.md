# Photo Scout search tool

`photo_scout.search.search_locations` is a deterministic, independently callable search tool. Website jobs, paid HTTP/MCP discovery and the candidate API use the same implementation. The model interprets intent; the tool owns provider routing, spatial filtering, merging and limits. There is no autonomous tool-selection agent.

## Contract

1. Interpret only the current text and current visible controls. Extract location/address, arbitrary `poiQueries`, supported `geographicKinds`, moods and the full photography intent. Text overrides conflicting controls. Never infer user requirements from previous reports. Retain composition, atmosphere, factual surroundings and exclusions even when a provider cannot search them directly.
2. Geocode the chosen location/address using a provider; do not use model-invented coordinates. With no location, use the map selection.
3. `resolve_intent` returns `searchParameters` and a deterministic `searchPlan`. Pass `searchParameters` unchanged to `search_locations` or `POST /photo-scout/v1/search` through the existing gateway. The upstream endpoint requires the existing private gateway credential and uses the existing source limits. It returns candidates, imagery sampling targets, plan, source status and counts; it does not score images or start paid image delivery.
4. The complete website/discovery workflow obtains imagery and evaluates the same `scoringIntent`, `preferences`, moods, POI requirements and geographic context. Match against the actual image first, then rank all matching places without a score threshold. Named POI identity controls labels, not eligibility. Explicit requested identities still require supporting visual evidence. All of this remains behind the existing asynchronous task/payment flow.

Example parsed parameters:

```json
{
  "lat": 41.88,
  "lon": -87.63,
  "radius": 5000,
  "poiQueries": ["coffee shops"],
  "geographicKinds": ["lake"],
  "photoStyles": ["vintage"],
  "categories": null,
  "scoringIntent": "Coffee shops with visible lakeside surroundings and a vintage look",
  "preferences": "Quiet outdoor seating"
}
```

Direct Python invocation:

```python
result = await search_locations(
    SearchParameters.model_validate(parsed["searchParameters"]),
    database_path=settings.database_path,
)
```

## Provider responsibilities and combination

| Request | Places terms | Geography | Merge strategy |
| --- | --- | --- | --- |
| Coffee shops among high-rise buildings | coffee shops | OSM building attributes | feature-search; nearby towers then visual confirmation |
| Lakeside vintage coffee shops | coffee shops | lake shore | spatial-intersection |
| Lake views | lakeside parks, lake viewpoints (supporting hints) | lake shore | spatial-union with road/path viewpoints |
| One address | none | none | area-imagery centered on the geocoded address |
| Attractions near an address | tourist attractions | none | places-only centered on the address |
| Surprise me / broad photo exploration | none | none | area-imagery |

Addresses specify the search center, not a business category. Arbitrary business/category phrases remain free text rather than an enum or a keyword classifier. Mood/photographic descriptions are retained for image evaluation; they are not proof of a factual category or location. A proper place name such as Lakeview must not itself imply a lake requirement.

Supported deterministic geometry kinds are lake, sea, river, peak, forest and waterside. Their combination is ALL (intersection). Lake/sea/waterside proximity defaults to 150 m, river 100 m, peak 300 m; forest uses containment. Lake boundaries, rather than centroids, define shores. Geometry comes from OSM/Overpass with a generalized OSM vector-tile fallback. These data cannot establish every spatial relation or prove visibility/access; unsupported visual surroundings remain in the image-evaluation intent rather than being silently claimed as verified geometry.

With geography, Places may retrieve up to 60 raw candidates per query; filtering precedes the final cap of 50. Multiple Places queries run in parallel with geometry retrieval; pagination within each query is sequential. Explicit POI targets never receive unrelated generated road points. Geography-only requests reserve four out of five available slots for mapped road/path samples; named POIs supplement them. Eligible paths are sampled at about 75 m with 35 m spatial cells before bounded spatial selection. Named places deduplicate by ID; sampled viewpoints within 50 m of a named place or another sampled viewpoint are suppressed. Distinct neighboring businesses are preserved.

Area imagery skips Places/geometry lookup and returns up to 50 spatial sample targets. An empty `nearbyPois` in this mode means no named POI lookup was requested, not that no imagery exists. Imagery providers can still have no coverage. All modes retain the selected radius and existing image/provider limits.

`searchPlan` exposes routed queries, target-versus-hint roles, geographic constraints, merge strategy and limits. `searchCounts` exposes raw named candidates, spatial matches, generated viewpoints and final candidates. Failed required geography never silently returns unfiltered POIs. Geography-only discovery can continue with road viewpoints when named Places is unavailable, with the named-source failure explicitly reported.

## General OSM features

`osmFeatures` adds generic tag queries and numeric attribute bounds, alongside natural geography. Each item has an English label, kind (`tagged` or `intersection`), filters, numericFilters and proximityMeters. Examples:

```json
{
  "lat": 41.892183, "lon": -87.632618, "radius": 1000,
  "osmFeatures": [{
    "label": "Traffic lights", "kind": "tagged",
    "filters": [{"key": "highway", "value": "traffic_signals"}],
    "proximityMeters": 30
  }]
}
```

Buildings can use `building` existence, `height` or `building:levels` numeric bounds, and material/colour attributes. Other standard tags support crossings, lamps, fountains, benches, artwork, steps, bridges and additional mapped objects. There is no fixed feature-category enum. Tags are validated and escaped; clients cannot submit raw Overpass code. Each query must have at least one required tag anchor. Optional secondary attributes (`required=false`) and numeric bounds retain missing/unparseable values as `unknownAttributes`; known mismatches are excluded. Height values in meters or feet are handled. Exact category tags must be mapped for a feature to be discovered. Missing mapping is never evidence of real-world absence.

Intersection discovery derives junctions from shared OSM road-node IDs with at least three distinct neighbors. Coordinate overlap alone does not establish an intersection; grade-separated crossings with different node IDs are not joined.

OSM features, Places and natural geometry are fetched concurrently. Multiple feature groups are AND proximity constraints; matches within a group are OR. With an explicit POI request, nearby feature matches filter those POIs. Otherwise mapped features themselves become imagery targets. Natural geographic constraints still apply. Required OSM failures are reported explicitly. Street View/scoring remains the visual confirmation, not a guarantee of visibility, access or a safe standing point.

Successful OSM queries are cached for 24 hours. Overpass output is bounded to 1,200 tagged objects or 1,800 road ways per selector, then 120 nearest candidates per feature group and 50 final locations. Large/poorly mapped areas are not exhaustively covered. Returned `osmFeatureSearch`, `osmFeatureCandidates`, `searchPlan`, tags and unknown attributes expose this distinction. The website and HTTP/MCP Agent interfaces carry the same field; it is included in scoring and cache identity.

### Logical relationships

`poiQueries` are alternative complete target descriptions (OR). Attributes of one target stay in the same query, e.g. `vegan coffee shops`. Explicit targets remain constrained by their environments; provider union is not permission to return unrelated shoreline points for a cafe query.

`geographicCombination` and `featureCombination` accept `all` (AND, default for existing callers) or `any` (OR). Geographic constraints and mapped-feature constraints are applied to the target with AND. The same geographic operator filters retrieved panorama locations, and both operators are sent to pixel matching and included in score-cache keys. Signed POI catalogs bind the operators, too.

Examples: cafes near a lake OR the sea => coffee-shop target, lake/sea, geographicCombination=any; lake AND forest => both geographic constraints, all; benches OR fountains => two feature queries, featureCombination=any. Negations and visual predicates remain explicitly grouped in scoringIntent for image verification, not positive geometry queries.

`searchBranches` supports up to six alternative constrained target groups (bounded disjunctive normal form). Each branch owns `poiQueries`, `geographicKinds`, `geographicCombination`, `osmFeatures`, `featureCombination` and `visualIntent`. Target, geographic and mapped-feature groups are intersected inside a branch; branch results are unioned. Top-level target/spatial fields must be empty when branches are supplied; ambiguous mixing fails validation instead of silently dropping conditions. Global center/radius/styles/preferences remain shared.

For `(lake cafes) OR (forest restaurants)`:
```json
{
  "lat": 41.88,
  "lon": -87.63,
  "radius": 5000,
  "searchBranches": [
    {"poiQueries": ["coffee shops"], "geographicKinds": ["lake"]},
    {"poiQueries": ["restaurants"], "geographicKinds": ["forest"]}
  ]
}
```

Branches run concurrently; identical in-flight provider lookups are shared. Each branch is spatially filtered before merging. A fair round-robin merge deduplicates POI IDs and caps the combined candidate list at 50, with `searchBranchIndexes` preserving provenance. `branchSearches` and branch-local plans expose retrieval diagnostics. A required branch-provider failure fails the request explicitly rather than reporting incomplete alternatives as complete.

Panorama coordinates are checked under their originating branch, with `eligibleSearchBranchIndexes` passed to the evaluator. Named targets must be associated with a candidate from that branch; geography/features must hold for the actual camera location. Scoring evaluates one complete eligible branch, its visualIntent and shared requirements, never a subject/environment mix from different branches. Score-cache keys and signed catalogs bind the full branch parameters. This behavior is shared by the website, structured HTTP API, and MCP.

Nested positive AND/OR conditions are normalized into these bounded groups. Negations and image-only requirements are checked visually rather than guaranteed by geometry subtraction. Searches beyond the six-group representation remain a limitation; this is not an unbounded logical query language.

### Model-written search programs

New text searches produce `searchProgram`: a bounded, typed data-flow program. It is executable tool composition, not arbitrary Python, SQL, HTTP URLs or Overpass source. Legacy flat parameters and searchBranches continue to work when no program is supplied; combining those retrieval fields with a program is rejected. Location geocoding remains separate; all tools share the resolved center and radius. Image acquisition and batch scoring stay downstream.

Small tools live in `photo_scout/program.py` and can be tested independently:

| Tool | Inputs | Output |
|---|---|---|
| search_places | Complete text queries | Named place set |
| search_geography | Geographic kinds | Geometry plus mapped paths |
| search_features | Typed OSM feature queries | Mapped feature groups |
| sample_geography | Geometry/path result | Spatially sampled viewpoints |
| feature_points | Mapped feature groups | Matching feature locations |
| filter_geography | Place set + geometry | Places inside/outside the specified spatial constraints |
| filter_features | Place set + feature groups | Places near/not near the specified mapped objects |
| union | Two or more place sets | Deduplicated OR |
| intersection | Two or more place sets | Shared POI IDs (AND by identity, not proximity) |
| area_imagery | No inputs | Surrounding imagery targets |

Example lakeside scenery program:

```json
{
  "steps": [
    {"id":"lake","tool":"search_geography","geographicKinds":["lake"]},
    {"id":"parks","tool":"search_places","queries":["lakeside parks","lake viewpoints"],"discoveryHints":true},
    {"id":"shore_parks","tool":"filter_geography","inputs":["parks","lake"]},
    {"id":"shore_points","tool":"sample_geography","inputs":["lake"]},
    {"id":"result","tool":"union","inputs":["shore_parks","shore_points"],"weights":[1,4]}
  ],
  "output":"result"
}
```

The executor validates known tool names, argument usage, input/output types, unique step IDs, earlier dependencies, complete reachability and an output place set/standalone area plan. Limits: 24 steps, 8 source-search steps, 50 combined imagery candidate targets, 64 logical paths per candidate. Independent source tools run concurrently and identical provider calls share an in-flight task. Union supports weights 1..4 per input for dense spatial sampling without losing named discoveries. Required source failures fail the search; explicitly marked scenic discovery hints may fail while other required paths still supply samples. No model loop runs during execution.

Every returned candidate retains its successful source/filter/visual paths internally. Actual panorama coordinates are rechecked against those paths; explicit targets also require POI association. Eligible paths are supplied to multimodal evaluation, preserving target groups, spatial AND/OR/exclusions and branch-specific visual requirements. Program content enters the score-cache key and signed catalog validation. `executionTrace` records each step's tool, dependencies, output count, provider status and execution time. `GET /photo-scout/v1/search-tools` exposes tool contracts, the full program schema and limits. HTTP API and MCP accept `searchProgram` as structured input; website and paid natural-language requests use the same program executor.

A geometry exclusion removes positions inside the known mapped feature buffers; it is not proof that unmapped real-world objects are absent. Pixel-only requirements remain visual checks. The provider and imagery coverage limits are unchanged.
