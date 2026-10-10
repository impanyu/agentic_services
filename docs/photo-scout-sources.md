# Photo Scout imagery source register

Updated October 8, 2026. This list concerns Photo Scout, not Niche Discovery.
Enabled, implemented and commercially authorized are different states. Each report
must disclose actual sampled sources; no claim of whole-world street-view coverage.

| Source | Purpose | Current state | Next step |
|---|---|---|---|
| Wikimedia Commons | Geotagged landmark/scenic photos | Connected and live image/model test verified | Broaden regional quality tests; preserve file-level license, author and geotag caveat |
| Panoramax federated catalog | Street-level camera imagery from public institutions and community instances | Deployed; Paris metadata + actual image retrieval verified on production VM; six-image multimodal comparison and live browser example verified; approved image hosts IGN and OSM France only | Broaden regional coverage/freshness tests |
| Mapillary | Crowdsourced street-level imagery | Adapter exists; no token configured; not live-verified | Obtain developer token through secure configuration; verify real imagery and attribution |
| KartaView | Crowdsourced street imagery | Official public API probed twice; timeouts; not connected | Resolve network/API availability before enabling |
| Google Street View | Outdoor panorama discovery and agent image inspection | Deployed and enabled; actual Google images inspected in a production multimodal run alongside Panoramax and Commons; dedicated API/IP-restricted key and persistent daily image cap; commercial inference permission unverified | Verify real image/model path; request Google confirmation of commercial inference and downstream output scope |
| Google Places Photos | Place-associated contributor photos | October 10: real retrieval verified; now integrated with POI image discovery, batch scoring, report previews and selfie backgrounds | Preserve Google Maps and author attribution; resolve downstream-use authorization separately |
| Google Maps Grounding Lite | Official LLM place context | Not connected; not an image analysis substitute | Evaluate alongside a Google-compliant display and storage design |
| Flickr | Geotagged photographer images | Candidate; no adapter/key | Review API commercial use, file licenses and removal rules; do not treat all public photos as reusable |
| Tourism boards, parks, museums and cities | Open/authorized location-specific photo collections | Candidate | Connect only after confirming collection-level access plus file-level rights |
| Owner-authorized image collections | Local-business/photographer site images with explicit coordinates | Candidate | Signed scope, provenance, permission and deletion flow; do not scrape arbitrary websites |
| Baidu Panorama | Mainland China directional street imagery | Candidate; no credential or panorama permission verified | Evaluate approved trial and commercial/synthesis scope before connecting |
| AMap POI photos | Mainland place-associated photographs | Candidate; not street panoramas | Resolve permitted photo use and coordinate handling; pair with domestic POI search |
| Licensed commercial imagery providers | Regional street-level imagery and freshness | Candidate | Compare coverage, redistribution/inference/API permissions and actual pricing before purchase |

China-market support requires more than an imagery adapter. See the
[regional provider plan](photo-scout-regional-providers.md) for address/POI retrieval,
basemap delivery, coordinate systems, non-Google login and source fallbacks.

## Panoramax implementation boundary

Official catalog aggregates metadata; images stay on originating instances:
https://docs.panoramax.fr/federated-catalog/overview/
API and per-instance license configuration:
https://docs.panoramax.fr/backend/api/api/
https://docs.panoramax.fr/backend/install/settings/
Etalab license permits commercial reuse/adaptation with attribution:
https://ia.numerique.gouv.fr/licence-ouverte-open-licence/

Queries cover five bounded subareas, max eight items each. Accept explicit
CC BY / CC BY-SA / CC0 / Etalab 2.0 licenses and producer credits only. Ignore unknown
licenses, private visibility, invalid points, out-of-radius images and unknown asset
hosts. Retain only needed metadata; never keep whole EXIF data, device identifiers or
unrelated embedded fields. Camera point is not a safe standing point; current access
is unknown. This adapter does not connect every federated image-storage server.

IGN image URLs redirect to a verified CDN. Fetching checks every hop against an exact
host allowlist (max two followed hops) and retains the existing 3 MB image limit and
JPEG/PNG/WebP checks. Browser CSP permits the same verified provider/CDN hosts.

Candidate selection rotates sources instead of taking only the nearest from one
provider. Images from different providers at the same point can be compared, while
final recommendations still deduplicate nearby locations. The fixed pipeline scores every
successfully loaded candidate image; source diversity does not force a low-quality recommendation.

Google refinement: adaptive 80–250 m minimum distance between actual panorama
camera points; max two opposing 120-degree views, one per panorama before second
views. No dense road traversal or exhaustive angle sweep.

Current coverage: POI-first Google metadata lookup targets the selected places
(up to 24); minimum spacing still applies to snapped camera locations. Up to 24
combined candidate images are scored in fixed batches of six, with two batches
in parallel. Download and scoring failures are counted explicitly. Daily Google
image cap remains 180; no autonomous image-inspection tool or agent-turn loop remains.


## Development image budget update (October 8, 2026)

The earlier 180-request application cap has been disabled for development.
`PHOTO_SCOUT_GOOGLE_DAILY_IMAGE_LIMIT=0` means no application daily limit (the default);
a positive value enables an optional operator cap. Usage counters remain for accounting.
Signed report authorization, per-request sampling and provider validation remain in place.
This setting does not change Google account quotas or billing.

## Additional-source trial (October 10, 2026)

The rows and sampling descriptions above include earlier implementation snapshots;
this trial does not revalidate or change the active search workflow.

- **Google Places Photos:** reused the configured production Places credential for
  Battery Spencer, California. The API returned 10 available photos; the bounded
  probe fetched and decoded one JPEG at 800 × 600, credited to Vi Lai Vue.
  `place_photos.py` retrieves fresh photo references and validates the media host.
  It returns attribution and `cachePolicy: no-store`, with scoring, selfie-background
  and adjustable-view capabilities disabled. A photo associated with a POI does not
  establish its camera position. This adapter is not called by production search or UI.
- **Mapillary:** the user does not yet have a developer application; the production
  token is absent. Adapter tests now cover bounded pagination, circular geographic
  filtering, capture heading/date, duplicates and unsafe URLs. Only fixed photographs
  are accepted; spherical images await directional reprojection. No real retrieval is
  claimed. Configuration requires `PHOTO_SCOUT_MAPILLARY_TOKEN` in the service
  environment, never in chat or source control.
- **KartaView:** a query with radius 5000 returned HTTP 400 stating the maximum is
  2000. A corrected 2000-metre San Francisco query timed out after 25 seconds.
  This establishes neither provider-wide unavailability nor usable coverage; no
  adapter was enabled.

Run `PYTHONPATH=src python scripts/probe-photo-scout-sources.py` in a configured
service environment. It makes bounded provider requests (which may incur provider
charges), prints counts/dimensions/authors, never prints credentials or media URLs,
and makes no model calls or image writes. The production-container trial used the
new adapter through temporary probe files, without restarting the application.

References:
- [Places Photos documentation](https://developers.google.com/maps/documentation/places/web-service/place-photos)
- [Google Maps Platform terms](https://cloud.google.com/maps-platform/terms),
  sections 3.2.2(b) and 3.2.3(c): preserve attribution and restrictions on creating
  content from Maps content. Do not enable Places Photos composites without an
  applicable permission basis.
- [Mapillary API access](https://help.mapillary.com/hc/en-us/articles/360010234680-Accessing-imagery-and-data-through-the-Mapillary-API)

## Places Photos workflow integration (October 10, 2026)

The earlier display-only trial is superseded by an additive integration. Existing
Google Street View discovery, eight-angle scoring, framing and selfie composition
are unchanged. Set `PHOTO_SCOUT_GOOGLE_PLACES_PHOTOS_ENABLED=0` to disable only
this additional source; the default is enabled when a Google Places key and Google
POI anchors are present. Fetch at most two photos each for eight POIs, with four
parallel lookups and a 12-second lookup bound. Errors are source-level statuses and
do not disable the existing providers.

Place-associated photos enter the same visual relevance check/scoring as other
sources. Their POI association is retained, but camera coordinates and direction
are unknown; no synthetic heading or interactive panorama is claimed. Signed
previews and portrait input resolve a unique author/dimensions metadata selector from freshly retrieved
Places metadata. Expired media URLs and raw Google photo resource names are not
saved in reports. If a photo disappears or metadata is ambiguous, fail that preview instead of silently
substituting another. These photos do not reuse persistent visual-score caches.

Technical capabilities (`scorable`, `selfieBackground`) describe implemented
functionality, not a conclusion about provider authorization. Authorization review
remains separate. No authorization gate was added to the existing Street View
portrait API. Mapillary still needs a developer token; domestic providers remain
unconnected as documented in the regional plan.
