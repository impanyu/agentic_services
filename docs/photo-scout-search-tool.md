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
| Coffee shops among high-rise buildings | coffee shops | none | places-only; check towers visually |
| Lakeside vintage coffee shops | coffee shops | lake shore | spatial-intersection |
| Lake views | lakeside parks, lake viewpoints (supporting hints) | lake shore | spatial-union with road/path viewpoints |
| One address | none | none | area-imagery centered on the geocoded address |
| Attractions near an address | tourist attractions | none | places-only centered on the address |
| Surprise me / broad photo exploration | none | none | area-imagery |

Addresses specify the search center, not a business category. Arbitrary business/category phrases remain free text rather than an enum or a keyword classifier. Mood/photographic descriptions are retained for image evaluation; they are not proof of a factual category or location. A proper place name such as Lakeview must not itself imply a lake requirement.

Supported deterministic geometry kinds are lake, sea, river, peak, forest and waterside. Their combination is ALL (intersection). Lake/sea/waterside proximity defaults to 150 m, river 100 m, peak 300 m; forest uses containment. Lake boundaries, rather than centroids, define shores. Geometry comes from OSM/Overpass with a generalized OSM vector-tile fallback. These data cannot establish every spatial relation or prove visibility/access; unsupported visual surroundings remain in the image-evaluation intent rather than being silently claimed as verified geometry.

With geography, Places may retrieve up to 60 raw candidates per query; filtering precedes the final cap of 30. Multiple Places queries run in parallel with geometry retrieval; pagination within each query is sequential. Explicit POI targets never receive unrelated generated road points. Geography-only requests interleave filtered named places and mapped road/path points. Named places deduplicate by ID; sampled viewpoints within 50 m of a named place or another sampled viewpoint are suppressed. Distinct neighboring businesses are preserved.

Area imagery skips Places/geometry lookup and returns up to 25 spatial sample targets. An empty `nearbyPois` in this mode means no named POI lookup was requested, not that no imagery exists. Imagery providers can still have no coverage. All modes retain the selected radius and existing image/provider limits.

`searchPlan` exposes routed queries, target-versus-hint roles, geographic constraints, merge strategy and limits. `searchCounts` exposes raw named candidates, spatial matches, generated viewpoints and final candidates. Failed required geography never silently returns unfiltered POIs. Geography-only discovery can continue with road viewpoints when named Places is unavailable, with the named-source failure explicitly reported.
