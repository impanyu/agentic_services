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
validation diagnostics. Both attempts share a 45-second deadline. Auth, quota,
network errors and refusals do not cause an automatic retry. A failed second
plan fails explicitly; no generic replacement search silently changes intent.

Regression checks and human review are required. Passing the structural schema
or synthetic suite is not proof of arbitrary-query accuracy, provider coverage,
safe access, high-quality images or a ten-second end-to-end search.
