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
| Google Maps Grounding Lite | Official LLM place context | Not connected; not an image analysis substitute | Evaluate alongside a Google-compliant display and storage design |
| Flickr | Geotagged photographer images | Candidate; no adapter/key | Review API commercial use, file licenses and removal rules; do not treat all public photos as reusable |
| Tourism boards, parks, museums and cities | Open/authorized location-specific photo collections | Candidate | Connect only after confirming collection-level access plus file-level rights |
| Owner-authorized image collections | Local-business/photographer site images with explicit coordinates | Candidate | Signed scope, provenance, permission and deletion flow; do not scrape arbitrary websites |
| Licensed commercial imagery providers | Regional street-level imagery and freshness | Candidate | Compare coverage, redistribution/inference/API permissions and actual pricing before purchase |

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
