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
