# Search failure and page-close investigation

October 11, 2026 production investigation.

The two original luxury-villa failures (`ps_fea47ce6d880bf5ca29b5f5a710ed034` and `ps_67981ff1064c288f7f72f53830e8e62d`) failed during parameter planning with APITimeoutError, around 41 seconds after submission. Those requests predate client lifecycle event logging. These errors identify the failed stage but do not independently disprove the user's report that both failures followed closing the page.

## Controlled real browser closure

Search `ps_ed23d13f8ad4419b20c94708bc941360` was submitted at 23:26:59.520 UTC. Its Chrome tab was closed at 23:27:03.549 UTC, after queue admission, and remained closed until completion. The first planning attempt raised APITimeoutError. The bounded second attempt succeeded; sources and scoring ran while the tab stayed closed. Total pipeline time was 116.36 seconds: planning 59.717, retrieval 7.021, scoring 49.588. Reopening restored the completed result in history, including after migration to the new App domain.

This demonstrates that the current worker can survive page closure and a planning timeout. One controlled run does not establish the original failures' exact cause or guarantee every future search succeeds. Model/provider timeouts can still fail after the bounded retry.

## New correlation evidence

The frontend sends best-effort `page_hidden` / `page_left` beacons for at most five active search IDs. The backend accepts only owned job IDs, records the observed backend stage and server receipt time alongside client time, and never changes or cancels job state. Events contain no prompt, photo, or location payload and are pruned after seven days. Browser/OS termination can prevent beacon delivery; absence of an event is not proof the page stayed open.

Scoring validation diagnostics now record field locations/types and available provider request IDs without logging raw model inputs. Historical validation errors cannot be retroactively reconstructed when the original provider response was not retained.

## Separate villa coverage issue

The original successful villa search found 284 raw candidates, selected 50 locations, and checked 381 images. Only six directions at two locations matched. Its selected OSM residences were spatially concentrated; named Places candidates also included apartments with “Villa” in their names.

Dense building retrieval now divides the bounds into cells under the existing per-query total output budget, and candidate ordering round-robins spatial cells before truncation. Scoring requirements are unchanged. Fresh cache keys avoid reusing the old concentrated ordering; a still-valid legacy cache can supply the exact same query when providers fail.

The production-network fresh Overpass probe on October 11 returned unavailable, so increased live coverage has not yet been demonstrated. Do not claim the new release already returns more villa matches. The cached fallback preserves availability when an exact valid older entry exists.

## Streaming investigation and six additional lifecycle cases

A production-network synthetic probe with no browser reproduced the old
non-streaming failure: one response succeeded in 21.318 s and one hit ReadTimeout
at 40.393 s before response headers. Two streaming equivalents completed in
16.836/17.708 s, with headers in 0.450/0.499 s and first text in 3.406/4.480 s.
This isolates a provider/planning failure from browser closure in that probe;
it cannot establish causality for the two original failures.

Planning now streams under a 60-second wall deadline with a 40-second inactivity
limit. Only the final complete validated plan executes. Safe timings include
headers, first text, final response, output character/delta counts, usage,
provider request ID and exception type. Output is bounded to 4096 tokens and
prose is concise. There remains one bounded transient retry in the job worker;
this is not a guarantee that every provider response will finish.

The following six real Chrome cases ran after the core streaming deployment.
Each recovered the same persisted job; every queue claim count was exactly one.
Browser beacons identify owned active tasks and can associate several active
jobs with one leaving page, so action timestamps and job identities must be
checked together. Results were also confirmed visibly in Search history.

| Action | Job | Planning seconds | Pipeline seconds | Result locations | Planning attempts |
| --- | --- | ---: | ---: | ---: | ---: |
| Close | `ps_c0a452563431cc758a1bc6296cef18a0` | 13.611 | 49.470 | 4 | 1 |
| Refresh | `ps_3ae81de07ed19737711ba79cb619be80` | 13.319 | 55.990 | 32 | 1 |
| Close and reopen | `ps_4261a9cc85d3bf2e192c3fad57f9c047` | 7.071 | 39.535 | 23 | 1 |
| Close | `ps_d8d0402172b92dc611d258fb7602a552` | 12.847 | 53.370 | 3 | 1 |
| Refresh | `ps_1dfcce58f0f0f8d04bb46b7dabdbb2b2` | 69.071 | 106.218 | 22 | 2 |
| Close and reopen | `ps_c10d1c30b51bdf07506f8627d1feae4b` | 6.146 | 42.289 | 22 | 1 |

The fifth case kept emitting text until 59.937 s and reached the wall deadline;
it was not an idle connection. The retry succeeded. The old trace did not retain
partial output size, so its exact long-generation cause remains unknown. New
output character/delta counts address that diagnostic gap, without recording
raw user text or generated plan bodies in production timing logs.

The two new villa results (4 and 3 locations) used available Places candidates
while the independent OSM building source was unavailable. This demonstrates
partial-source delivery and unchanged visual filtering, not improved fresh OSM
coverage. Fresh Overpass probes still produced 500/504/unavailable responses.

Synthetic capability-aware planner probes generated building footprint hints,
named residence alternatives and, in one medium-reasoning run, guided sampling
around verified residence leads. A separate three-case probe had a 60 s villa
planning timeout; neither structural tests nor those successful examples justify
claiming all planning tails are resolved. Production reasoning remains low.

Three final low-reasoning synthetic plans completed in 16.584, 20.603 and
11.022 seconds. The explicit neighborhood exploration case proposed Sheridan
Boulevard Historic District and the Country Club neighborhood as assumptions,
looked them up with Places, then used `sample_near_places` alongside mapped
houses and named residence alternatives. This is planner evidence, not a live
verification that either neighborhood contains matching villas.

Local regression validation: 441 Python tests and 156 frontend/domain tests
passed. These counts cover scoped suites, not every test in the repository.

## Geometry-output coverage correction

The final guided-residential search `ps_b1725f5b2bcca13746a19479b8d0403b`
completed after tab closure with one queue claim, 21.696 s planning, 7.185 s
retrieval and 57.166 s scoring (86.067 s pipeline). It delivered four locations
from 331 checked images. It actually executed `sample_near_places`. Its hinted
OSM house groups decoded empty, prompting a raw-output inspection rather than
assuming no mapped homes existed.

A fresh production-network 1 km building probe returned 1200 objects, all with
geometry and none with center. The previous decoder required center and silently
lost these objects. The corrected decoder derives bounding-box centers from way
and relation geometry, preserves circular scope filtering and handles dateline
longitude wrapping. The fresh probe now decodes 120 bounded candidates with
known footprints. Footprint query cache keys are versioned so the faulty empty
results are not reused. This demonstrates corrected raw-geometry ingestion;
it is not a fresh full villa-search recall measurement.

The planner also now includes broad building-existence alternatives when
residential subtype coverage is uncertain, using them as discovery clues subject
to the same visual residential requirement. Regression totals after this fix:
443 scoped Python tests and the previously passing 156 frontend/domain tests.
