# Photo Scout search planning

The web app and Agent API share `resolve_intent` / `parse_intent`. The model
produces a complete bounded program once; the backend executes typed tools in
parallel and batches image matching/scoring. This is not an autonomous agent loop.

## Protocol

The model-facing `PlannerIntent` has no legacy retrieval fields or branches.
`PlannerProgram.steps` uses an `anyOf` union of tool-specific schemas. It compiles
to the stable `SearchProgram` used by existing API callers. The compiler validates
references, data types, tool arguments, reachability, budgets and the single
collect → score → rank delivery chain. Legacy API formats remain compatible.

`requirements` records the photographic conditions, their strength, destination
and implementing step IDs. `required` must match; `forbidden` must not be present;
`preferred` affects ranking without excluding an otherwise matching image. An
OR expression remains one grouped expression. Location/radius remain separately
resolved/executed parameters and do not need to be visually proven from pixels.

Every image carries successful retrieval paths and the requirement indexes
applicable to each path. Shared requirements apply to all paths, branch-specific
conditions only to their branch. The scorer satisfies one complete successful
path, never mixes requirements from different alternatives. Ledger strengths
are authoritative over ambiguous free text. The ledger is included in cache
identity, preventing required/preferred differences from sharing assessments.

## Input interpretation

Empty input preserves the map center, radius and selected moods. There is no
conversation context or inherited previous query. Explicit photographic text
replaces UI mood; address/radius-only input preserves mood. Arbitrary categories,
brands and provider-searchable facts go to Places. Geographic relations go to
geography tools; mapped objects go to OSM feature tools. Pixel-only properties
stay in visual review. Proper names and category subtypes retain their meaning.

A bare address checks one panorama within 50m and its eight horizontal views;
if unavailable, the existing point tool falls back around the same address.

## Mood discovery

Nature and Adventure combine named hints with optional geographic samples by
union. Their mapped environments are optional discovery alternatives, not hard
requirements for every candidate. Waterside combines water-filtered named hints
with shore samples; both sources satisfy the water relation and image review
confirms visible water. Other broad aesthetic moods can use existing hint-guided
area imagery. Explicit subjects are never diluted by unrelated mood candidates.

Source failure for a geometry/feature search explicitly marked preferred in the
ledger may yield an empty optional source; required spatial searches still fail
rather than silently discarding the requirement. Source status remains visible.

## Models and repair

`PHOTO_SCOUT_INTENT_MODEL` and `PHOTO_SCOUT_INTENT_REASONING` configure only the
planner. `PHOTO_SCOUT_MODEL` remains the image scorer configuration. Existing
OpenAI credentials are reused. The evaluation harness supports `--model` and
records the prompt hash, model, reasoning setting, synthetic inputs, raw plans,
partial semantic checks and latency.

An invalid schema/program is regenerated once with the original input and safe
validation diagnostics. Both malformed-plan attempts share a 60-second deadline. The planner consumes Responses streaming events, validates only the complete response, and records header/first-text/final timings, provider request ID, token counts and failure phase without retaining generated text in timing logs. The stream retains a 40-second inactivity limit; a progressing response is no longer cut off merely because its total generation exceeded 40 seconds. The direct resolve endpoint allows 105 seconds for planning plus geocoding/route endpoint resolution. Auth, quota,
network errors and refusals do not cause an automatic retry. A failed second
plan fails explicitly; no generic replacement search silently changes intent.

Regression checks and human review are required. Passing the structural schema
or synthetic suite is not proof of arbitrary-query accuracy, provider coverage,
safe access, high-quality images or a ten-second end-to-end search.

## Capability-aware discovery and geographic hypotheses

The planner distinguishes indexed Places names/categories, mapped OSM objects and
geometry, and attributes that only imagery can establish. Difficult visual
subjects must not become literal business-name queries or generic scenery.
Named estates, historic residences, converted house museums and plausible
neighborhoods can be discovery leads. They are assumptions, never claims that a
particular property exists. Providers resolve their locations; broad independent
alternatives preserve coverage if an assumed neighborhood is wrong.

`sample_near_places` accepts a provider-resolved Places set and a 100–2000 m
sampling radius. It samples around at most six anchors, interleaves their points,
deduplicates nearby positions, and clips every point to the original user search
circle. It makes no additional source API calls itself and shares the existing
50-location imagery budget. Downstream imagery remains near verified anchors;
all candidates still receive the original subject/visual requirements.

OSM building queries optionally prioritize large/small footprints or more
storeys inside spatial cells. These are ordering hints, not invented mandatory
thresholds or proof of luxury, architecture or visibility. Unknown metadata
remains eligible. Explicit numeric requirements retain their usual filters.

An independent candidate-source union may continue if one provider is unavailable
and another succeeds, with partial coverage reported. A required spatial filter,
intersection, or wholly unavailable union fails explicitly; provider failure
must never remove a user's required condition. Exact-query cache fallback does
not change the subject or constraints.

OSM subtype metadata can be sparse: a building-existence alternative can cover
untyped buildings without claiming that they are homes. Footprint output may
omit an explicit center; the decoder derives bounding-box centers from geometry.
The derived point is an approximate discovery anchor, not an access/standing
location or proof that a polygon contains it. Geometric-query cache versioning
prevents reuse of results emptied by the previous center-only decoder.
