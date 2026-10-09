# Photo Scout fixed workflow

## Current search path (2026-10-09)

Search now always uses the fixed pipeline, for both website jobs and paid HTTP/MCP requests. `PHOTO_SCOUT_EXPLORER_ENABLED` no longer enables the historical autonomous explorer. The explorer module remains available for offline experimentation, but is not called by production search.

1. Resolve natural-language `query` to location, arbitrary `poiQueries`, moods, radius, preferences, `scoringIntent` and spatial `geographicKinds`. Text overrides conflicting structured controls. An empty query skips text interpretation.
2. Run Google Places and OSM geometry lookup concurrently. Geographic kinds are lake, sea, river, peak, forest and generic waterside. Multiple kinds are intersected. Lake shore uses polygon boundaries (including reconstructed multipolygon holes), not a lake centroid. Shore/river proximity is a discovery hypothesis, not proof of visibility.
3. Filter named POIs by spatial requirements. For requests without an explicit POI category/business, also generate positions along eligible mapped roads/paths near geographic features. Exclude mapped private/no-foot access and motorway/trunk paths. Access and safe standing positions remain unverified. Google Places paginates up to 30 results per query (20 per page), with queries running in parallel; failed subsequent pages retain successful earlier results. Merge/deduplicate up to 30 candidate places; this is bounded coverage, not all places in the region. Source feature proximity defaults are 150 m for lake/sea/waterside, 100 m for river, 300 m for peak, and containment for forest.
4. Query Street View metadata in parallel, deduplicate panoramas and retain eight horizontal headings at 120 degrees. Recheck actual camera positions against geographic constraints. Commons and Panoramax are retrieved in parallel, associated with nearby candidates and checked by the same visual model. Up to 264 directional/static images enter scoring.
5. Check subject/style requirements and score together in each structured multimodal response. Default 24 images per batch, up to 12 concurrent model calls (`PHOTO_SCOUT_SCORING_BATCH_SIZE`: 1–32; `PHOTO_SCOUT_SCORING_CONCURRENCY`: 1–16). Downloads prepare independently of model-call slots, with 32 concurrent downloads (`PHOTO_SCOUT_IMAGE_DOWNLOAD_CONCURRENCY`: 1–64). Street View metadata allows 24 concurrent queries (`PHOTO_SCOUT_STREETVIEW_CONCURRENCY`: 1–32); vector tile fetching allows 12. Larger batches receive proportionally larger output budgets and are checked for complete, unique image IDs. Rejected images return a brief exclusion reason with empty unused description/tip fields; matched images retain full descriptions. This presentation-only change preserves the existing assessment cache criteria. Successful assessments reuse the existing context-sensitive cache. Every image-backed matching place is returned, including low scores; unverified candidates do not enter `spots`.

Successful OSM geometry is cached in SQLite for 24 hours. Live Overpass requests have a four-second overall retrieval deadline with a backup endpoint. If unavailable, cached OpenFreeMap/OpenMapTiles vector geometry provides a five-second fallback: water, waterways, woods, mountain peaks and roads/paths are decoded from up to 16 tiles. Adjacent polygons are stitched before extracting shores, and an outer border prevents clipped tile edges becoming false shores. This geometry is generalized and may omit small features. Tile data is cached for seven days and fallback provenance is included in source status. An unavailable geographic source fails explicitly instead of silently returning geographically unrelated places. Named Places queries preserve successful responses when another query fails. Results expose retrieval/scoring/total timing measurements; cold searches with many uncached images are not guaranteed to complete in ten seconds. Queue time and website text parsing happen outside these result timing measurements.

MCP and HTTP accept `query`, `poiQueries`, `geographicKinds` and `scoringIntent`; `limit` is deprecated and ignored. Existing asynchronous job ownership, history, account retention, image attribution and selfie generation remain in place.

## Earlier implementation notes

Photo Scout is a deterministic application pipeline. Models do not choose tools or execute code.

- Text input: a structured-output model extracts a place, photo mood, radius, and preferences. Photon resolves the place to coordinates; the first provider-ranked match is used, preferring a named city point over same-named boundary centroids when resolving a city and its resolved name is displayed. The model never generates coordinates. Inputs use best-effort interpretation without clarification or candidate selection. When no place can be resolved, the selected map coordinate is used with an explicit explanation. Mentioned photo moods, search radius and descriptive preferences are applied automatically. All visually verified matching results are returned and ranked; there is no three- or five-result target. Text explicitly overrides conflicting UI values; omitted parameters preserve current controls. Radius, moods and preferences remain available for manual input as overlays on the full-page map. Coordinates are display-only and selected through the map, text or device location; mood selection supports multiple choices. Distances are converted to meters and clamped to the supported 100–20000 meter range.
- Device location: browser geolocation supplies coordinates directly. The same submit handler then applies any typed text, overriding conflicting parameters; with an empty text box the text model and geocoder are skipped. Permission failure never substitutes the default Chicago center.
- Discovery follows intent. Explicit POI queries/categories or a signed manual POI selection use POI-first retrieval. Surprise me and mood-only requests use visual area exploration with optional named-place context. Google examines up to 25 locations: spatially spread area samples plus at most five POI anchors, eight horizontal directions per panorama. Commons/Panoramax images within the radius can participate even more than 250 m from a POI. Named-place lookup failure does not block visual exploration.
- A single multimodal call per six-image batch judges request relevance first, returning a null score and exclusion reason for mismatches, and numeric photography scores for matches. Matching low scores remain in the rankings; there is no numeric score threshold. An unnamed visually supported scene is represented as a `photo-location` at its camera geotag with a descriptive label, not an invented named business/attraction. The best eligible direction represents each viewpoint. Responses expose `discoveryMethod`, `photoLocationCount` and per-image filtering reasons; scoring/cache are shared by human and agent entry points.
- The map fills the viewport; all input controls and the collapsible report float on it. The map marks independently sourced POI coordinates; visual evidence, scores, attribution and camera links are shown in result cards. A POI listing alone is not a visually verified recommendation.
- Active work runs in a durable server queue. Switching tabs does not interrupt it. Active job tokens stay in page memory; refresh does not resume unfinished work. Completed search history (last 30) is saved in browser sessionStorage for guests and SQLite account history for signed-in users, without images or signed image URLs; checked records restore map overlays. Each record supports individual selection and the all-record control. Legacy Photo Scout localStorage/sessionStorage entries are removed on load. Delivery jobs expire after 24 hours. Text-model response storage is disabled and original text is not saved as server query history; guest session/account history retains the input.
- Website testing remains free. Agent discovery keeps its existing per-call payment gateway. Natural-language resolution is available while free website testing is enabled, with a persistent daily capacity limit (PHOTO_SCOUT_DAILY_INTENT_LIMIT, default 100).

- Result markers depict a photographer and a clockwise bearing from north, taken from the inspected image camera heading. The marker stays at the independently sourced POI coordinate; its position is not a verified tripod/standing point. Clicking a marker opens the corresponding shortlist card; clicking its image opens the provider view at the inspected heading. Unknown headings are explicitly labeled. Each Google panorama contributes eight scored views: N, NE, E, SE, S, SW, W, NW at pitch 0, all with 120-degree horizontal field of view. All eight survive sampling; highest eligible score wins per POI. Other providers contribute up to 24 images. Batches contain 24 images with up to 12 concurrent batches; server timeout is 660 seconds. This samples the sphere, not every possible camera angle. City-scale searches still use bounded source samples and at most 264 scored images, not an exhaustive city inventory. Ranges above 5 km query five bounded regions with up to 40 POIs per region in one Overpass request; candidates rotate across regions and categories before selecting at most 30.

- Successful per-image model assessments (including unsuitable views) are cached in SQLite for 30 days, configurable with `PHOTO_SCOUT_SCORE_CACHE_DAYS` (0 disables). The key hashes source/image/panorama identity, date, direction, POI context, photo moods, preferences, model and scoring instructions. Radius, center and shortlist size do not invalidate an otherwise identical assessment. Only cache misses download images and call the model; errors are not cached. No image bytes, credentials or raw preference strings are stored in this cache. Ranking is recomputed for each request. Responses distinguish `cachedImages` and `newlyScoredImages`; legacy `scoredImages` includes both.

- Reports include `poiResults` for every discovered POI: a best identity-supported scored view or an explicit `no_verified_view` entry. The selected Top 3 or Top 5 use photographer icons, ranked by score even if every score is low. Other image-backed scored POIs use clickable directional dots. POIs without scored images are hidden from both map and shortlist. Model suitability flags remain advisory notes; they do not filter rankings. The response preserves the selected count in `topLimit`. Selected history entries overlay independently and clicking any marker opens its corresponding report and card.

- Google sign-in uses an independent OAuth web client configured by `PHOTO_SCOUT_GOOGLE_CLIENT_ID`, `PHOTO_SCOUT_GOOGLE_CLIENT_SECRET`, callback `https://api.aisoup.net/photo-scout/v1/auth/callback`. Scopes: openid/email/profile. Code flow validates state, PKCE, ID token signature, issuer, audience, nonce and expiration. HttpOnly Secure SameSite=Lax sessions use host-only cookies. Account writes require matching frontend Origin and a session CSRF token. Gateway passes redirects without following and permits credentialed CORS only for configured company origins. Guest history migrates into account history after successful sign-in; sign-out clears account views from the browser.

## Historical agent exploration (not used by current searches)

Enable `PHOTO_SCOUT_EXPLORER_ENABLED=1`; set `PHOTO_SCOUT_EXPLORER_MODEL`
(default `gpt-6.1-sol`). Scoring keeps `PHOTO_SCOUT_MODEL` (currently Luna).
The caller-owned Agents SDK runtime uses ten tools: `search_places`,
`query_geography`, `analyze_position`, `view_map`, `find_streetview`, `search_photos`, `inspect_view`,
`manage_candidate`, `list_candidates`, and terminal `submit_candidates`.

The explorer receives the original user query plus resolved location, radius,
keywords, scoring intent and mood. It can repeat searches, pan/zoom a schematic
map of queried OSM geometry, inspect real source images, and adjust horizontal
Street View heading (0–359) and fov (30–120). Map images show queried geometry,
not satellite imagery; missing geometry is not evidence of empty terrain.
Multipolygon member parts/roles remain separate. Geometry is cropped to a local bounding box and retained by the tool; missing
coordinate gaps stay separate. Simplified parts are returned to the model. Old
large tool bodies and previews are compacted between turns to avoid repeated
input cost; the server-owned evidence remains available through tools.

Only server-discovered, in-radius, actually inspected images can enter the list.
Submission freezes it and stops the SDK immediately; the existing batch scorer
then filters/matches/scores against the complete user preferences and returns
ranked locations. No agent turn occurs after scoring. Candidate state, geography
and action audit persist by private task ID for worker restart recovery; expired
jobs' exploration records are pruned. Browser disconnection does not cancel work.

Exploration limits: 100 tool calls, 96 exploratory image inspections, 48 candidates,
64 model turns and 420 seconds exploration, within the existing 660-second task
limit. Deadline/turn exhaustion submits already selected evidence with an explicit
partial-coverage note; it never fabricates candidates. Provider failure is exposed
to the agent so it can select another search/source. Setting the flag to 0 keeps
the previous workflow available for rollback.


### Coverage review and structured action logs

Exploration breadth, image-driven follow-up and stopping are encouraged through the
prompt, not enforced by a fixed sequence, count threshold or submission gate.
The Agent decides which locations/directions to inspect and whether another lookup
is likely to improve its shortlist. `inspect_views` compares up to four chosen
headings in one call (serial provider reads, one model turn). `record_view_decisions`
and `review_exploration` are optional reflection/logging aids. Adding a candidate
also records its keep reason. Review reports evidence and unexamined views without
judging readiness; submission works with or without a review. Only user bounds,
actual inspected-image provenance and resource budgets are enforced. Successful
submission ends the Agent and triggers scoring; hard deadlines preserve selected
evidence with an explicit partial-coverage note.

Each tool audit event records call ID, parsed parameters, start time, duration,
outcome, sanitized result metadata, errors and evidence counts. Image bytes,
credential-bearing provider URLs and bulk geometry are excluded from audit.
OSM errors include endpoint and exception/HTTP status, not raw request URLs.
Candidate decisions and final review persist with the private job; this is an
operational action log, not a recording of hidden model reasoning. Existing
records cannot retroactively acquire parameters or rejection reasons.

### Selfie background preparation

Before editing with a Google Street View background, request the same panorama
and heading at level pitch with FOV min(original, 60) and min(original, 45).
This obtains fresh provider-rendered views rather than locally warping/cropping
imagery or attribution. A small vision model (`PHOTO_SCOUT_BACKGROUND_MODEL`,
default `gpt-6-luna`) compares the available views for optical/stitching distortion,
scene identity and usable foreground. Existing narrower views are never widened.
Severe defects in the best view fail before image generation with an actionable
message; moderate residual distortion is recorded rather than guaranteed corrected.
The selected FOV, direction, assessment and reason are saved in private photo
context and shown in Background info. The source link opens the view actually used;
originalSourceUrl retains the originally requested view. Other providers keep their
original image. The synthesis prompt calls for natural camera perspective while
preserving location geometry and provider marks. Image generation quality stays
unchanged. See Google Street View's [FOV documentation](https://developers.google.com/maps/documentation/streetview/request-streetview).

Street View discovery defaults to 120° at eight compass headings for wide context. The selfie panel offers Auto, 90°, 60°, and 45° framing. Auto compares provider-rendered 90°/60°/45° projections (never wider than the selected input view); explicit choices use the selected FOV and still check distortion. Framing applies only to Google Street View; static photo sources keep their original background. Saved photo context retains the requested and actual framing.

Legacy `limit` input remains accepted for old API clients and saved tasks but is ignored: it is excluded from the intent model input/output and explorer request, and cannot truncate scored results. The explorer independently balances coverage, diverse viewpoints and useful evidence within its resource budget; finding a few good candidates is not itself a completion target.

Exploration aims to return roughly 24 distinct matching places when imagery and the region support that many. This is a best-effort breadth goal, not a minimum image count. Multiple angles/zooms of one place do not count as extra places; fewer genuine matches are acceptable with an explanation. There is no hard submission gate. `inspect_batch` accepts up to eight mixed-provider views across locations/headings, downloading at concurrency four with per-image metadata and partial-failure reporting. `inspect_views` now uses the same batch loader for up to eight directions. Model context retains recent batch images and reports the distinct inspection count.

Batch exploration tools now cover `search_places_batch`, `find_streetview_batch`, `search_photos_batch`, `query_geography_batch`, `analyze_positions`, `view_maps`, and `manage_candidates`, in addition to batch image inspection. Each accepts at most eight operations with labeled per-item results and partial failure handling. External I/O concurrency is four (Overpass two); local map/position/candidate operations run in input order. Identical read requests are deduplicated within a batch; candidate mutations preserve order. Each executed item plus the batch call consumes the existing tool-call budget. Listing/review already returns whole lists and submission remains a single terminal action.
