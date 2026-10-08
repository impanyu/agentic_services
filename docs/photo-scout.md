# Current execution model

Photo Scout uses a fixed image scoring pipeline. The agent-loop descriptions below
are historical and superseded by the fixed-pipeline section at the end.

# Photo Scout

Human URL: `https://aisoup.net/photo-scout/`. Agent endpoint:
`POST https://api.aisoup.net/photo-scout/v1/discover`; OpenAPI and service manifest
under `/photo-scout/`. English UI, click on a Leaflet map or enter coordinates.

## Sources and evidence

- Wikimedia Commons: location search, file metadata, allowlisted CC BY / CC BY-SA /
  CC0 / public-domain images only. Attribution and license travel with each result.
  Geotags may locate depicted subjects rather than camera positions.
- Panoramax: federated STAC catalog with approved IGN/OSM France image hosts,
  explicit per-photo open licenses and producer attribution. Paris real-image retrieval
  and a multi-source multimodal run verified; provider kill switch supported.
- Mapillary: adapter implemented, needs `PHOTO_SCOUT_MAPILLARY_TOKEN`; source-linked
  contributor credit and CC BY-SA license retained. Not live-verified without token.
- OpenStreetMap Overpass: separate nearby tourism POIs; attribution/ODbL returned.
  These entries are explicitly NOT visually evaluated recommendations.
- Google Street View: optional server-only metadata discovery and image inspection,
  enabled with `PHOTO_SCOUT_GOOGLE_ENABLED=1` and a dedicated IP/API-restricted key.
  Queries up to 25 circular-grid locations, filters actual camera points by an adaptive 80–250 m
  minimum separation, and provides at most two opposing 120-degree views per panorama. Google imagery is sent to
  the model transiently and never cached in reports. Google image cards retain their own attribution; Google recommendation pins are not added to the selection map.
  Results include a Google panorama link and inspected heading. Google-specific
  commercial inference permission has NOT been verified; technical access is not
  evidence of that permission. Enabled following the owner's explicit instruction.
- KartaView: official endpoint probe timed out; not connected. Future alternatives:
  Flickr geotag search (API/license review), licensed tourism-board open imagery,
  owner-authorized location-specific photo collections, commercial imagery licenses.

Primary references:
https://www.mediawiki.org/wiki/API:Geosearch
https://www.mediawiki.org/wiki/API:Imageinfo
https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data
https://cloud.google.com/maps-platform/terms (sections 3.2.3(c), 3.2.3(e))
https://kartaview.org/terms
https://operations.osmfoundation.org/policies/tiles/

## Agent

Existing Python Agents SDK; one agent chooses image-inspection tools and returns
structured visual judgments. Reuses configured OpenAI key/model. Up to 24 diverse
image candidates, 12 inspection attempts, 14 turns, 240 seconds of agent work
(270 seconds for the complete discovery), 2500 output tokens/turn.
Server rejects unseen IDs, invented locations and duplicate mapped viewpoints.
Image fetching is provider-host allowlisted, HTTPS only, only bounded Panoramax redirects to verified hosts, max 3 MB,
JPEG/PNG/WebP signatures only. No arbitrary user image URL is fetched. Source/model
errors are not replaced with fabricated recommendations. Provider captions are
untrusted, HTML stripped; recommendations do not identify people or infer access.
Subjective aesthetic score is separate from confidence; timestamps and missing
coverage explicit. Exact standing point, current safety, hours and route are unknown.

## Payment and delivery

`PHOTO_SCOUT_ENABLED=1` turns on exploration; pricing is configurable and stays closed unless
`PHOTO_SCOUT_PRICE_CENTS>=50`. Human Checkout uses `PHOTO_SCOUT_STRIPE_SECRET_KEY`,
falling back to the existing `CONTRACTOR_STRIPE_SECRET_KEY`. It must be in the same
Stripe account as the shared signed webhook `/v1/stripe/checkout-webhook`.
The gateway exposes MPP Base-USDC (and MPP Stripe if configured) per call, at the
same configured cents price. The private upstream service key never enters the UI.

Human Checkout checks eligible imagery before charging, then persists coordinates
and price with the Stripe session. Verified webhook re-retrieves payment, validates
session/amount/currency/livemode/service metadata, and queues generation in the shared
persistent delivery worker. Generation works without returning to the success page.
Polling can queue a verified paid report if webhook is delayed; completed reports are
reused. Worker retries with existing lease/backoff; report reads do not invoke models.

Reports require a random token hashed in storage. Browser keeps token in tab-only
sessionStorage, not the success URL; use the original checkout tab. Coordinates and
preferences are shared with providers/OpenAI with affirmative UI consent. Report
access expires after 30 days; expired rows removed on subsequent checkout. No device
location or identity is requested. Source lookup throttled to 10/min/process;
`PHOTO_SCOUT_DAILY_RUN_LIMIT=30` is persisted and shared across workers, reservations
count failures too. Single-process exploration lock avoids unbounded concurrency.

Agent payments receive the gateway's MPP receipt; Photo Scout does not advertise
Web Evidence order/ledger URLs or create its signed commercial receipts yet.
Paid API errors need a support/refund review with the payment receipt. A zero-result
report is possible: recommendation quality cannot be guaranteed before visual analysis.
No marketplace listing or claim of full street-view coverage is implied by deployment.

## Operations

Use the existing compose app and gateway; no additional daemon. Deploy Caddy's
`/photo-scout/` static route, preserving unrelated host configuration. External tile
service requests disclose browser IP; OSM tiles are for interactive viewing only,
no prefetch/download. Self-host or use a commercial tile provider at scale.

Tests: `python -m pytest tests/test_photo_scout.py`; provider tests mocked and labeled.
A bounded real-model Chicago smoke run saw four actual images and returned three spots;
that is a smoke check, not evaluation of broad geographical quality.

## October 8 release verification

Production preview uses $2 per exploration for humans and agents, with a 30-run UTC
daily cap. Company catalog lists the service as both and Preview. Wikimedia imagery
and OSM POI queries were verified live; Mapillary token is not configured and Google
Street View remains disabled. Real-model Chicago example inspected four images and
returned three recommendations. Source dates can be upload dates; they are not
unconditionally presented as capture timestamps.

92 Python tests and gateway TypeScript check passed. Public UI/sample/OpenAPI/status
returned 200; unpaid agent call 402 with MPP challenge; Checkout CORS 204; actual
Stripe live-mode Checkout creation 200; unpaid private report 402; test Checkout
expired 200 without paying; unsigned webhook 400. Shared webhook queue delivery and
replay were tested with mocked paid Stripe responses, not a new live paid transaction.
No marketplace listings were submitted.

## Multi-source expansion

Source register: [photo-scout-sources.md](photo-scout-sources.md). Selection rotates
image sources and preserves cross-provider images at a shared coordinate for comparison.
Final recommended locations still deduplicate. Panoramax is enabled by default with
`PHOTO_SCOUT_PANORAMAX_ENABLED=0` as its kill switch. The Paris model smoke run inspected
six actual images and returned one Panoramax 2025 street scene and one Wikimedia
landscape photograph. Public free sample is `sample-paris.json`. No paid transaction
was made for this source expansion; existing pricing is unchanged. KartaView timed
out in both local and VM probes and remains unconnected.

## Google Street View credential setup

`PHOTO_SCOUT_GOOGLE_API_KEY` is a server-only production credential in the existing
`impanyu` Google Cloud project. Street View Static API is enabled. The dedicated key
is restricted to the production VM outbound IP and
`street-view-image-backend.googleapis.com`; never put it in a browser URL, sample,
report, log or version control. Rotate/restrict the key when the VM outbound IP changes.
The status endpoint exposes only `credentialConfigured` and `imageAnalysisEnabled`
booleans. Credential availability alone does not enable image analysis; the explicit feature
flag is also required. Commercial inference rights have not been verified. Metadata probes incur no image
charges; actual image fetches are a separate billable operation.

October 8 credential verification: production VM metadata request returned HTTP 200,
provider status `OK`, and panorama imagery date `2025-10`. No actual Google image
was fetched or sent to OpenAI in this credential setup.

## Google imagery integration

With the source enabled, `inspect_image` fetches at most twelve total images across all
providers per run. Google fetches use a persistent UTC daily request cap (default
180; failed calls count), fixed 640x640 images with 120-degree field of view, strict internal panorama-reference
validation, no redirects, and a 3 MB size ceiling. A per-run image is used in memory
only. Report rows have `imageUrl: null` for Google and expose only source links,
provider credit/date, coordinates and camera heading. No public image proxy exists.
The agent must attempt a Google inspection if Google candidates exist, but it can
return zero Google recommendations when scenes are unsuitable or downloads fail.
The feature flag is also the operational kill switch.

October 8 Google integration verification: 97 tests passed. A production multi-source
Paris run inspected six real images across Google Street View, Panoramax and Commons;
Google image attempt count increased by four, with successful Google inspection
recorded by the agent. It recommended only a Commons scene; the agent did not force
a Google recommendation merely because the provider was present. This is an internal
smoke test, not a customer payment or proof of Google commercial permission.

A second Google-only Pont d Iena smoke run inspected six real Google images and
returned two photo spots with 180-degree camera headings. Sanitized report metadata
and recommendations are published as `sample-google.json`; source image bytes and
provider credentials are not in the sample. This sample is an explicitly labeled
internal Google-only test, while the paid product compares enabled sources.

## Spatial sampling refinement

Google camera spacing is `clamp(radius * 0.2, 80, 250)` meters. Metadata queries
are bounded to 25 circular-grid locations; spatial filtering applies after Google's
snapping, so different panorama IDs at nearby camera points do not consume repeated
inspection slots. First view points toward the requested area's center, the second
is opposite. Sampling offers one view per retained panorama before second views.
Image fetches still happen only when selected by the agent, within the shared
twelve-inspection budget. This is sparse geographic sampling, not exhaustive road or
360-degree coverage; source timestamps remain capture dates, not live conditions.
Historical free examples retain their original sampling/results.

## Increased search-area coverage

Google metadata lookup now uses up to 25 circular-grid points with at most five
concurrent requests, instead of five center/cardinal probes. Query points cover the
whole requested area, including diagonals, and are clipped at supported coordinate
boundaries. Small areas can have fewer probes. Panorama minimum spacing remains
80–250 m after snapping, so 25 query locations do not imply 25 distinct panoramas.
Source-balanced selection retains at most 24 candidates. Google panorama ordering
uses geographic spread and offers one view per location before second views. The
agent can attempt 12 image inspections and aims for 8–12 views when coverage permits.
The Google daily image cap remains 180; this change does not raise it or change price.
Older static examples remain historical results from their labeled test runs.
The total deadline stays below the existing five-minute fulfillment lease and gateway
response window, so increasing inspection count cannot outlive those boundaries.

## Temporary free website testing

Set `PHOTO_SCOUT_HUMAN_FREE_PREVIEW=1` to enable live human searches without Stripe.
The website calls `/photo-scout/v1/preview` through the existing authenticated
gateway; the backend refuses new Checkout sessions while this flag is enabled.
Existing paid reports and webhook delivery continue normally, and the agent
`/discover` endpoint retains its configured per-call price. Free results are
returned directly and are not persisted as paid reports; refreshing loses them.
The same 30-run daily budget, one-run concurrency limit, source lookup throttle,
Google image budget and 270-second timeout apply. Set the flag back to `0` to
restore human Checkout.

The human website also offers an optional “Use my current location” button. It
requests browser geolocation only on click, recenters the map, displays estimated
accuracy, and requires the user to review and submit the search separately.
Permission denial, unavailable location and timeouts retain manual selection.

## POI-first discovery (October 8 revision)

Search now retrieves named POIs before images, including parks, gardens, nature
reserves, viewpoints, artwork, museums, historic places and selected natural
features. The main FOSSGIS endpoint is primary and Private.coffee is a fallback.
The query requests only 16 MiB of server memory instead of the 512 MiB default;
a live VM probe returned 504 with default allocation and 200 with the smaller
allocation. The independent Private.coffee status endpoint returned 500 during
verification. Failed attempts return error type and HTTP status where
available. Candidate counts describe mapped places, not verified recommendations.
Up to 24 category-balanced POIs guide Google panorama metadata lookups; the
primary heading faces the POI, with an opposite view for comparison. Other
imagery is associated only within 250 m of a candidate. The agent must verify
that the nearby image supports the candidate, rather than assuming proximity
proves visibility. Top 3/5 is selected from inspected evidence; duplicate POIs
are suppressed. A park centroid is a representative map point, not an entrance.

Google Places is not connected; this implementation uses OSM POIs and the
existing Google Street View credential. No additional paid provider is enabled.
Google result images use 20-minute signed URLs to an authenticated server route,
fetch on demand under the existing daily image budget, and return no-store JPEGs.
Source bytes are not stored in reports. The result screen hides the OSM selection
map; choosing another location removes result images before showing the map.
Preserve Google imagery attribution and source-date caveats. Existing uncertainty
about permission for commercial model analysis is unchanged by this UI revision.

When one panorama is near multiple queried POIs, the image catalog retains those
possible matches. The agent chooses a supplied `poi_id` supported by the visible
scene; the server rejects unlisted POIs and displays the chosen source name.

Image inspection is disabled dynamically after 12 attempts, leaving the final
agent turn without tools so it must produce its structured recommendation.

## User-selected POIs

The website now retrieves a candidate list before visual analysis. Users can
select all, clear, or individually select places. Names, categories and straight-line
distances come from OpenStreetMap; selection alone is not visual verification.
Changing coordinates or radius invalidates the list. Zero selections disable analysis.

`POST /photo-scout/v1/pois` with `lat`, `lon`, `radius` returns `nearbyPois` and a
signed `poiCatalogToken`, valid for one hour. This lookup does not fetch imagery or
invoke the agent. Pass that token and `selectedPoiIds` to `discover` (agent paid API)
or `preview` (temporary free website testing). Only selected, server-verified POIs
are passed to imagery discovery and analysis; the provider lookup is not repeated.
Tokens are bound to coordinates and radius; unknown IDs, duplicates, empty
selections, modified tokens and expired selections are rejected. Omit both fields
for the existing automatic POI selection. Already-paid jobs retain their authenticated
selection when fulfilled later, even after the interactive token expiry.

### POI category filters

The website offers multiple category checkboxes before lookup: viewpoints, parks
and gardens, tourist attractions, museums, public art, historic places, nature and
landscapes, and recreation areas. All are selected by default. Changing categories
invalidates any loaded POI list; at least one category is required.

API requests accept `categories`: `viewpoint`, `park`, `attraction`, `museum`,
`artwork`, `historic`, `nature`, `recreation`. Omit the field to search all types.
For example, `{"lat":48.8603,"lon":2.2919,"radius":1000,"categories":["park","viewpoint"]}`.
Filters constrain the Overpass query and returned records before the 24-place cap.
Pass the same categories when using a signed catalog and selected POI IDs; a
changed category set requires a new lookup. Category labels group several OSM tags
(e.g. parks includes gardens and nature reserves); they are metadata, not ratings.

## Photography moods (current website selection)

The main website control now asks for the desired photo mood, rather than POI
categories. Choose one of eight moods or “Surprise me” (unfiltered). Free-text
composition preferences remain optional. Individual candidate selection remains
available after lookup. The server owns the mapping in `photo_scout/styles.py`;
`GET /photo-scout/v1/status` supplies the English UI labels and descriptions.

| Mood | Candidate categories |
| --- | --- |
| Nature & calm | Parks/gardens, nature, viewpoints |
| Urban & architectural | Historic places, attractions, viewpoints |
| Vintage & nostalgic | Historic places, museums, attractions |
| Iconic & cinematic | Attractions, viewpoints, historic places |
| Artsy & colorful | Public art, museums, historic places |
| Water & reflections | Nature, viewpoints, parks/gardens |
| Clean & minimal | Public art, museums, historic places, parks/gardens |
| Wild & adventurous | Nature, viewpoints, recreation areas |

This is a broad candidate-search heuristic. Category membership does not establish
style suitability. The visual agent receives trusted descriptions of the selected
moods, evaluates actual images, prioritizes visible style fit in scores, and explains
matching visual features. It can return fewer results or none when evidence is weak.

API callers may send `photoStyles`, e.g. `["waterside"]`, to POI lookup and discovery.
Multiple moods map to the union of their candidate categories. Do not send both
`photoStyles` and the legacy `categories` field; the API rejects that ambiguity.
Catalog tokens bind both mapped categories and original mood IDs: changing from
nature to waterside invalidates a previous catalog even though their category
mappings overlap. Legacy category-only requests remain supported. Signed catalog
tokens are excluded from the model prompt.


## Fixed scoring pipeline (current)

POI lookup and mood-to-category mapping stay deterministic. Source discovery selects
at most 24 candidate images. The server attempts to load every selected image,
then scores all successfully loaded images directly with the multimodal Responses
API using structured output. No agent runner, tool calling, autonomous image
selection, or model-turn limit remains in Photo Scout. Other services are unchanged.

Images are processed in batches of six, at most two batches in parallel, with
four concurrent downloads. Each batch requires exactly one score and suitability
judgment for every supplied image ID. Missing, duplicated or invented IDs invalidate
that batch. Scores use a shared rubric with mood fit, visual evidence, composition,
photo tips and uncertainty. The server then filters unsuitable/unverified POIs,
sorts all eligible scores globally, deduplicates POIs and nearby camera positions,
and returns Top 3/5. The summary is generated deterministically from that shortlist.

The API includes `analysisMethod: fixed-batch-scoring`, all `imageAssessments` and
`scoring` counts for sampled, downloaded, scored and failed images. The results page
shows all scores in an expandable section. A failed download or batch is explicitly
reported; if no image can be scored, the request fails instead of returning a false
no-recommendation result. Processing remains bounded to 240 seconds for download
and scoring, 270 seconds for the overall request. Model retries are disabled.
Images remain transient, credentials and signed catalog tokens never enter prompts.

## Single-page website layout

The map, search form, task status and shortlist remain on one page. Rendering a result replaces only the shortlist section; it does not hide the workspace or sample buttons. Completed reports restore below the form without scrolling on entry. User-triggered views and newly completed searches scroll to the shortlist; an adjustment link scrolls back to the map without removing results. Viewing examples preserves the selected location.

### Additional map layers and geolocation feedback

OpenFreeMap Liberty, Positron, Dark and Bright are global OSM-based styles. USGSImageryOnly and USGSTopo are optional U.S. aerial/topographic basemaps with explicit coverage labels and visible attribution. Leaflet overlays toggle search radius, OpenStreetMap candidate POIs and non-Google photo locations. Basemap layers support location selection, not the model's image-scoring input.

Geolocation requests use a recent device position (up to 60 seconds old) without requiring a high-accuracy GPS fix. The button shows a busy state and actionable permission/error messages. A 20-second watchdog handles unresolved permission prompts; success updates coordinates and radius, clears analysis consent and scrolls to the map.


## Development image budget update (October 8, 2026)

The earlier 180-request application cap has been disabled for development.
`PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT=0` means no application daily limit (the default);
a positive value enables an optional operator cap. Usage counters remain for accounting.
Signed report authorization, per-request sampling and provider validation remain in place.
This setting does not change Google account quotas or billing.

## Photo studio

Every imagery-backed shortlist card has **Take a selfie here**. The modal accepts a user portrait (JPG/PNG/WebP/HEIC, up to 20 MB and 80 MP; browser-decodable photos are resized to 2048 pixels before submission, with server-side HEIC conversion as fallback), previews the selected camera view and allows an optional pose instruction. `POST /photo-scout/v1/portraits` queues an image edit; `GET /photo-scout/v1/portraits/{id}` and `/image` require the private `X-Report-Token` returned on submission, plus the internal gateway credential. Portrait style is one of natural (default, keeps clothing), street, cinematic, vacation or editorial. Fixed server-side briefs map it to pose, expression and wardrobe while preserving identities, group membership and the original background/view/weather. Unsupported styles are rejected. The generated output is explicitly an AI composite. Model default: `PHOTO_SCOUT_IMAGE_MODEL=gpt-image-2.5-sunburst`, max quality, 1024 square. New image models omit `input_fidelity`; legacy GPT Image 1 models retain high fidelity and high quality.

Before fetching the background or editing an image, the worker checks for at least one visible real human, cartoon/illustrated character or animal with structured vision counts (`PHOTO_SCOUT_PERSON_MODEL`, default `gpt-6-astra`; street-view scoring independently uses `PHOTO_SCOUT_MODEL`, default `gpt-6-luna`). No eligible subjects causes an explicit failed-job message; classifier failures stop the edit with a retry message. Groups and mixed groups are accepted. The edit prompt preserves all subjects, retains cartoon art styles and animal markings, avoids humanizing animals, and integrates subjects with scene lighting, perspective and shadows while keeping the background intact. Upload bytes and payload are removed on either failure. The `checking` job state is shown in the UI. A separate durable worker runs sequentially; switching tabs or closing the dialog does not stop the job. Refreshing reconnects using the anonymous browser cookie or signed-in account. Upload metadata is removed; portrait bytes and job payload are removed after completion/failure. Output and job records expire after seven days by default and are pruned by the worker. No portrait/output is inserted into search history. Up to eight jobs may queue; `PHOTO_SCOUT_PORTRAIT_DAILY_LIMIT` defaults to 100 (0 disables this operator cap). Provider originals and copyright attribution should be retained; generated images must not be represented as actual visits.

Map detail controls default to place names and roads/railways only, using the Minimal vector basemap. Other styles remain available; toggling details on a raster basemap selects Minimal.

Photo Scout model overrides are independent of the platform-wide `OPENAI_MODEL`. Scoring caches include the model ID, so upgrading the vision model rescans images without reusing older-model scores. Vision scoring allows 180 seconds per batch and 12,000 output tokens; subject validation allows 4,000 output tokens.

Completed selfies provide **Save to Photos** using native file sharing (the PNG File is prepared before the button click to retain user activation), plus **Download PNG**. Browsers cannot silently write to a photo library or confirm the chosen share target. Unsupported browsers show long-press instructions on the displayed photo; download remains available. Canceled shares keep the result available and sharing failures show fallback instructions. Personal images remain local to the page when opening the save menu.
The displayed output uses a local data URL allowed by the existing image CSP; the blob download URL and prepared PNG file stay in page memory. No additional image host is authorized.


### Durable browser and account task recovery
`GET /photo-scout/v1/tasks` establishes a secure HttpOnly anonymous cookie and lists only owned search/selfie tasks. Job submissions bind to this server identity; authorized Google sign-in attaches existing anonymous tasks to the account. Logged-out visitors cannot read account-owned tasks with the old anonymous cookie. Legacy private-token API access remains supported; no task tokens or uploads are persisted in frontend storage. Search tasks retain data for 30 days. Selfies retain generated outputs for seven days (`PHOTO_SCOUT_PORTRAIT_RETENTION_DAYS`, bounded 1–30); original uploads are still removed after processing.

Human search submissions queue the entire fixed workflow before text interpretation, geocoding, POI lookup, image retrieval or model scoring. Query parameters override UI defaults in the worker. Reloaded pages reconnect to active tasks, import completed search reports into history without rerunning models, and expose pending/completed selfies under History. Background workers do not depend on browser polling. Anonymous recovery requires the same browser's cookie; clearing cookies loses guest access. Tasks expire rather than being retained indefinitely. Previously created tasks without ownership metadata cannot be retroactively assigned safely.
