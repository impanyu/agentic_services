# Lincoln architectural search audit — October 11, 2026

## Original evidence and limits

Job `ps_b3243d395d20bcc9e16eccadd8f6cc74`, query `豪华大别墅，Lincoln Nebraska，20公里`:
412 merged candidates, 50 selected locations, 39 Google panoramas / 312 directions
and 3 other images. All 315 images were assessed; 310 were rejected. Five accepted
Google directions represented two locations (a historic brick residence and Whitehall).
The final ranking did not discard additional matching locations: the principal losses
were upstream selection, visibility and visual classification.

The private audit export and original reasons are in `/tmp/photo-villa-audit.json`.
Downloaded projections and index metadata are in
`/tmp/photo-villa-audit-local/photo-villa-images`. No images, credentials or report
tokens are committed. P0–P38 below refer to that local metadata order.

Every panorama received a representative-view inspection across the original session
and this continuation. Seven suspicious panoramas received all-eight-direction review
(P3, P12, P22, P23, P24, P32, P38); recovered examples were also viewed at 512px.
This is not a claim that every original direction received independent human review.
The old audit did not persist camera coordinates or source candidate associations.
Nearest selected POIs in the downloaded metadata are reconstructed leads, not proof of
which POI was supplied with the original image.

## What the pictures and original reasons establish

| Panorama | Observation and original exclusion | Diagnosis |
| --- | --- | --- |
| P0–P1 | Substantial historic residences; five directions accepted | Original positive controls |
| P2 | International Quilt Museum's modern institutional form | Named-source candidate mismatch; rejection supported |
| P3 | 135/180/225° show the classical residential facade; 0° shows the Capitol | Residential facade incorrectly dismissed as institutional; opposite directions must remain rejected |
| P4–P6 | Small/plain suburban residences, some obstruction | Weak selected candidates; no demonstrated luxury |
| P7 | Federal Building parking structure | Broad footprint source selected wrong subject |
| P8–P11 | Ordinary single-storey homes/garages and streets | Weak selected candidates; rejection supported |
| P12 | Kennard house visible, partly cropped/tree-obscured | Borderline appearance/scale judgment; stronger review accepts the historic details |
| P13 | Multi-storey block and parking area at House Pioneer | Named lead does not establish villa-like form |
| P14 | Ordinary suburban house and parked car | No demonstrated requested scale/appearance |
| P15 | Broad contemporary residence, multiple garages, formal drive | Original reason recognized breadth but denied residential luxury; stronger review accepts contemporary form |
| P16 | Ordinary low house/driveway | No demonstrated luxury |
| P17 | Trees/fields near an approximately 860 m² mapped house | Target visibility problem; footprint is not visual evidence |
| P18–P20 | Distant/ordinary residential views | Insufficient demonstrated appearance; do not accept from proximity |
| P21 | Culver's restaurant | Wrong candidate subject |
| P22 | Clark-Leonard lead resolves to lane, fence and obscured rear building | Camera position/occlusion; name alone cannot rescue the image |
| P23 | Substantial gabled contemporary house in spacious grounds | Subjective boundary: smaller model calls it conventional, stronger model consistently finds upscale form |
| P24 | Large mapped footprint but low, distant/obscured frontage | Visibility and appearance unsupported; remains rejected |
| P25 | Tree-dominated ordinary residential view | Obstruction/weak evidence; remains rejected |
| P26 | Road and vegetation, nearest selected target 385 m away | Provider lookup distance violation, not Tile-vs-Static coordinate drift |
| P27 | Duncan Aviation offices/hangar complex | Wrong candidate subject |
| P28 | Ironworks Loft urban brick block | Named-source candidate mismatch for villa form |
| P29 | Distant residence/garage across lawn | Weak target-facing evidence; remains rejected |
| P30–P31 | Rural roads and vegetation | Target not identifiable |
| P32 | Lancaster County Mansion lead shows embankment and fields in every direction | Unusable camera/lead location; not a scoring false negative |
| P33–P36 | Ordinary homes, garage fronts and residential roads | Weak selected candidates; remains rejected |
| P37 | Hospital parking garage | Broad footprint source selected wrong subject |
| P38 | Hayward Place's long institutional facade | Historic/current-use label does not establish villa form; remains rejected |

The [official Governor's Residence description](https://governor.nebraska.gov/governors-residence)
identifies the property as a Georgian Colonial home. Combined with the visible wings,
portico and formal grounds, the original institutional-versus-residential exclusion is
not defensible merely because flags/fences or government use are present. The Capitol
in the reverse direction remains a different subject.

For P26, live Static API and Tile API metadata both returned
`40.82388583648149, -96.87296691481687` (0 m difference), 385 m from the nearest selected
candidate. The collector requested at most 200 m but previously only enforced the
user's overall search circle. It now enforces the requested anchor lookup distance too.

## Controlled comparisons

Twelve fixed image projections, the saved original required conditions, identical
image detail and independent model calls were used. Original POI/path input rows were
not retained, so this is a reconstructed comparison, not an exact original replay.

- With original instructions and no identity metadata, `gpt-6-luna` rejected all three
  visible Governor's Residence views; `gpt-6.1-sol` accepted those three, while rejecting
  the Capitol, parking garage, empty embankment and institutional block.
- A general form-versus-use clarification did not fix the smaller model's Governor's
  Residence decisions. Adding reconstructed nearest-candidate metadata also did not.
- The stronger model consistently accepted the Governor's Residence and P23 in these
  comparisons, but one run rejected the original P0 positive. This is model disagreement,
  not a ground-truth accuracy benchmark; “luxurious” retains subjective boundaries.
- An isolated-cache replay retained all original Google primary assessments, then used
  the implemented review selector. It reviewed 36 target-facing views, recovered four
  (P3/180°, P12/90°, P15/270°, P23/270°), and rejected 32. Accepted directions changed
  from 5 to 9, plausibly representing 2 to 6 locations. Review took 24.2 seconds.
  This used reconstructed nearest-candidate associations and did not mutate production.

The review pass uses the same criteria and does not see the primary conclusion in its
model input. Recoveries are not automatically recommended access locations; restricted
access, private residences and uncertain standing positions remain separate concerns.

## General changes

1. Comparable OSM size/storey priorities now receive two of every three ordering slots;
   the third retains spatial coverage. Every candidate remains eligible. Unhinted or
   incomparable priorities retain the previous spatial ordering. This prevents a low
   relevance candidate in each sparse cell from consuming the full imagery budget.
2. The interpreter distinguishes unknown subtype (`building=yes`) from any building
   (`building` existence), so subtype fallback need not preferentially retrieve known
   parking/industrial uses. It can still choose broad building discovery for genuinely
   unrestricted architectural searches and sampling near verified geographic leads.
3. Exact OSM tag equality is explicit in the schema and instructions. Alternative values
   become separate queries with OR; they must not become a literal `house / detached`.
   Common classification keys also validate identifier syntax, rejecting prose/Unicode
   generation debris before execution. This is not a category-value whitelist: unknown
   well-formed identifiers remain valid, and name/operator text keeps Unicode/punctuation.
4. Visual instructions distinguish architectural form from present use/ownership and
   avoid imposing unrequested ornamentation or floor counts. The original required
   visual conditions remain binding.
5. Fully assessed, all-rejected Google panoramas with a target within 200 m receive one
   target-facing review with `gpt-6.1-sol`, up to 50 views. Eight primary headings must
   be present; partial primary failures and already-matching panoramas are not reviewed.
   Results use separate model cache keys, reuse loaded pixels, retain original decisions
   and reviewer provenance, and fall back to primary assessments if review fails. A
   60-second secondary deadline bounds added latency. `PHOTO_SCOUT_REVIEW_MODEL` can
   override the reviewer; an empty value disables review.
6. Rejected-image audit records now retain camera coordinates, imagery profile,
   candidate IDs and credential-free Google references for future exact association
   checks. Non-Google provider URLs are not copied into the audit.

This does not yet replace a hidden rear camera with a better street-front camera.
OSM's per-tile source cap still samples provider ID order before local footprint
ranking, so candidate coverage remains bounded and incomplete.

## Validation

441 Photo Scout Python tests and 154 frontend tests passed. Meaningful regressions
cover priority-versus-coverage allocation, incomparable priorities, out-of-radius
provider metadata, full-negative review eligibility and heading selection, separate
review caching, recovery/rejection, provider failure fallback, original evidence
retention and avoiding credential-bearing URLs in audit records.

Live planner probes preserved distinct semantics for tall glass buildings, red-roof
motels, and cafes near a lake without requiring visible water. Two villa probes timed
out following streamed validation failures; a subsequent probe emitted a malformed
exact tag value. After clarifying exact-value semantics, a 19.1-second probe produced
separate `house`, `detached` and `yes` alternatives, named discovery, and sampling near
verified leads. This does not establish that planning timeouts are eliminated.

The first deployed probe, `ps_6d172a46408e5caa67a3fdc491ddd3b7`, completed in
71.7 seconds: 141 images, 11 matching directions, six ranked locations, 15 secondary
reviews and three recoveries; no image-download/scoring failures. All six returned
images were downloaded through the signed production endpoints and visually inspected.
This probe included an urban UI mood and is not an exact-input comparison. Its source
trace revealed malformed `building` tag text and zero OSM candidates despite valid
Places results. It is evidence for image delivery/review only, not successful OSM recall.
That finding prompted the identifier validator and five additional regressions.

Deployment applies only seven Photo Scout Python modules as a layer on the currently
running image, avoiding rebuilds of unrelated VM checkout changes. Source files are
SHA guarded with rollback backups. Before each backend replacement, the deployment
checks both search and portrait queues and refuses to restart with queued/checking/
running jobs. The first release's six installed module hashes matched the local
manifest and the backend was healthy. Frontend, OAuth, avatar and other service files
were not changed.

The exact original submission was then rerun as
`ps_0d303217e93681ba1a40ba7835d7e5fc`. It completed in 155.9 seconds after one planning
timeout and the existing background retry (planning 78.774 s). It checked all 387
images, accepted seven directions / four locations, and recovered three of 47 reviewed
views. Download/scoring failures were zero. Its correctly formed OSM predicates were
`building=house`, `detached`, and `yes`, but the combined provider query failed
(incomplete response / HTTP 500); the run used named places and guided regional samples.
This verifies source-outage fallback and the original required visual conditions,
not successful OSM retrieval or a latency improvement.

The source tool now executes and caches independent feature predicates independently
(up to six concurrent groups under their existing deadlines), avoiding a large combined
geometry request and allowing regrouped plans to reuse single-query caches. Partial results explicitly identify failed groups. OR sources can use successful
alternatives; AND sources still fail closed when a required group is unavailable.
Additional tests verify this behavior and cache reuse after reordering groups.
A production-host provider probe of the split implementation returned 120 detached
and 120 unspecified-type candidates in 6.0 seconds; the house query still encountered
an upstream timeout / HTTP 500. This motivated explicit partial-OR preservation rather
than discarding successful alternatives. Tests cover both OR continuation and AND
failure, without substituting a missing required condition.

The final controlled production execution reused that validated program and all its
original visual requirements, with raw query text cleared solely to avoid replanning.
The public preview endpoint returned HTTP 200 in 63.5 seconds (execution only).

- All three OSM groups succeeded fresh: 120 house, 120 detached and 120 unspecified
  candidates in 7.387 seconds. Twenty-eight OSM candidates entered the 50-location
  imagery allocation. This verifies the deployed source and ordering path, not complete
  city coverage or that every mapped object has a usable street-level view.
- All 321 sampled images were checked: 208 fresh downloads and 113 cached assessments;
  no download/scoring failures. Six directions represented four ranked locations.
- Thirty-nine target-facing decisions used secondary review, including 13 cached
  reviews; three matching decisions came from review. The retained locations were a
  large brick residence, Nebraska Governor's Mansion, William Jennings Bryan House,
  and a historic house behind a picket fence. Their images were already inspected in
  the immediately preceding original-input run.
- Retrieval took 14.423 seconds and scoring/review 48.651 seconds. There is no claim of
  a ten-second pipeline or a fully resolved planning-timeout problem.
- The final seven installed module hashes matched the local release manifest, and the
  backend health check passed. The final scoped suites passed 441 Python / 154 frontend
  tests. All three replacements checked both task queues before restarting; rollback
  source backups were retained on the VM.

Remaining limitations: provider outages, planner generation/validation variability,
subjective appearance judgments, bounded Overpass ID sampling, and nearest-camera
occlusion. The bounded review recovers missed locations; it does not exhaustively
rescore every angle or guarantee the best possible camera position. No villa-specific
query branch, acceptance override or reduced matching threshold was added.
