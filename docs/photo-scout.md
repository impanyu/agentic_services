# Photo Scout

Human URL: `https://aisoup.net/photo-scout/`. Agent endpoint:
`POST https://api.aisoup.net/photo-scout/v1/discover`; OpenAPI and service manifest
under `/photo-scout/`. English UI, click on a Leaflet map or enter coordinates.

## Sources and evidence

- Wikimedia Commons: location search, file metadata, allowlisted CC BY / CC BY-SA /
  CC0 / public-domain images only. Attribution and license travel with each result.
  Geotags may locate depicted subjects rather than camera positions.
- Mapillary: adapter implemented, needs `PHOTO_SCOUT_MAPILLARY_TOKEN`; source-linked
  contributor credit and CC BY-SA license retained. Not live-verified without token.
- OpenStreetMap Overpass: separate nearby tourism POIs; attribution/ODbL returned.
  These entries are explicitly NOT visually evaluated recommendations.
- Google Street View: deliberately disabled pending appropriate usage authorization.
  Do not send Google imagery to the multimodal agent or put it beside the OSM map.
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
structured visual judgments. Reuses configured OpenAI key/model. Up to 12 diverse
image candidates, 6 actual inspections, 8 turns, 240 seconds, 2500 output tokens/turn.
Server rejects unseen IDs, invented locations and duplicate mapped viewpoints.
Image fetching is provider-host allowlisted, HTTPS only, no redirects, max 3 MB,
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
