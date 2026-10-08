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
  the agent transiently, never cached in reports or embedded beside the OSM map.
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
