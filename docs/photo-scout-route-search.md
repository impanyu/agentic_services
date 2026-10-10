# Photo Scout: along-route photography

The existing circle search remains available. In Search settings choose **Along a route**, enter From/To (From can use the selected map pin), choose walking or driving, and set a corridor width. Walking and a 300 m corridor are the defaults. The prompt and photo moods specify what to photograph along the journey.

The same `route` argument is accepted by the website's durable jobs, REST discovery and MCP `discover_photo_spots`:

```json
{
  "lat": 37.808,
  "lon": -122.417,
  "route": {
    "origin": {"query": "Fisherman's Wharf, San Francisco"},
    "destination": {"query": "Ferry Building, San Francisco"},
    "travelMode": "walk",
    "corridorMeters": 300
  },
  "query": "Scenic waterfront views and interesting architecture",
  "photoStyles": ["waterside", "urban"]
}
```

Endpoints accept either an address/name in `query` or both `lat` and `lon`. Origin may be omitted/null to use the map pin. Explicit natural-language route instructions override conflicting controls. Empty prompts preserve current controls. Routes are computed using Google Routes; failed routing does not invent a straight-line substitute. Configure `PHOTO_SCOUT_GOOGLE_ROUTES_API_KEY` or reuse the server-restricted existing Google key with Routes API enabled and allowed.

Retrieval uses at most six windows along the returned road polyline, queries the existing Places/OSM tools concurrently, and filters both POIs and actual camera/image coordinates to the corridor. Scenic requests reserve 20 evenly spaced road viewpoints alongside mood-guided POIs. Specific target/category requests retain their constraints. A global limit of 50 target locations and 424 images applies to the whole route, with eight Google panorama directions per location. Images are deduplicated and assessed using the existing scoring/cache module once for the whole journey, rather than scored separately per window.

Results include `route.geometry` (GeoJSON LineString; longitude first), endpoints, travel mode, route distance, duration, provider warnings, corridor and coverage/sample metadata. Result views also include route progress and offset meters. Ranking remains by photographic score; progress metadata permits ordering by travel sequence. The map shows the route plus the existing clickable result pins and Take a selfie actions. Route geometry and results are retained with the durable search/history under the existing signed-in/guest retention rules.

The first release supports routes up to 200 km, corridor widths of 100–2,000 m, and automatic candidate selection. Manual signed POI catalogs remain limited to circle searches. Coverage is a bounded sample, not every scene or the most scenic possible route. The computed route is a walking/driving route between the chosen endpoints; photographic ranking does not reroute the journey. Street imagery availability, safe access and permission to stop are not guaranteed. Google walking warnings are displayed with results.
