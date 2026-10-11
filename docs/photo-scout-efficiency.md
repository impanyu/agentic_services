# Photo Scout efficiency pass — October 10, 2026

## Changes without reducing coverage or image quality

- Request-scoped, lazy HTTP connection pooling covers Street View tiles/static imagery, Places photos, Commons and Panoramax. Provider host validation, redirect boundaries, byte limits and timeouts remain in place. Independent tasks use separate pools. Shielded panorama downloads keep their connection lease until completion. Fully reused score results do not create an unused HTTP client.
- One search downloads each identical image reference once, while still binding and judging each candidate image ID independently. `scoring.uniqueImageLoads` and `duplicateImageLoadsAvoided` distinguish this from the number of directional images checked. Eight directions, candidate limits, matching rules and scoring batch sizes remain unchanged.
- Scoring JSON uses compact separators and Unicode directly. Parsing yields exactly the same objects; conditions, metadata, image detail and instructions remain present. Fewer JSON bytes do not establish a particular token or billing reduction.
- Selfie background decisions are reused only when the exact model inputs match: actual image bytes, FOV labels, instructions, model and output schema. Only validated non-severe decisions with a valid image index are stored, using the existing model-assessment store and expiry. Uploaded subjects, source pixels and prompt text are not stored in this reuse entry. The source images must still be obtained under existing provider rules before checking their identity. Portrait generation remains a fresh operation at the existing quality setting.
- `costAccounting.reusedBackgroundReviews` reports these skipped model checks without treating them as billed API calls.

## Measured limits

The latest instrumented search before this pass took 40.621 seconds in the pipeline and 2.205 seconds in the queue. Planning took 7.084 seconds, retrieval 1.581 seconds, and image preparation/scoring 31.937 seconds. It returned 34 places. Its known-cost estimate was $0.165817; it fetched only one fresh Street View tile, so this is not a cold-search cost baseline.

The latest three instrumented selfies took 128.111–131.679 seconds to process. Subject checks took 2.559–3.819 seconds, background model calls 10.625–13.337 seconds, and final image generation 111.421–117.925 seconds. Reusing an identical background skips the background call, not the final generation. These figures are historical task measurements, not new end-to-end benchmark results.

A local microbenchmark of 64 HTTP-client acquisitions (three runs) measured a median 0.1360 seconds before pooling and 0.0022 seconds with pooling. This measures client setup only, not remote TLS latency, model time or whole-search speed. It must not be presented as an end-to-end speedup.

All costs above are internal standard-list-price estimates, before free tiers and discounts, rather than billing statements. The average-search target of 10 seconds remains unmet. Further changes to model, quality, batching semantics or candidate selection need representative quality and latency comparisons; they are not part of this pass.

## Deployed smoke measurement

An independent anonymous search for `San Francisco architecture`, radius 5 km and urban mood, completed after deployment in 64.495 seconds including polling. Its pipeline took 60.523 seconds: planning 14.517, retrieval 7.559, scoring 38.428; queue time was 2.496 seconds. All 256 images downloaded and were assessed, with zero download/scoring failures, zero reused score checks and zero duplicate image references. There were 176 matching directional images and 29 ranked views, representing 22 photo locations. This verifies the deployed path and full assessment coverage, not an improvement in average latency or a controlled quality comparison.

The estimate was $0.357565: Places text $0.160, Places photos $0.028, Street View tiles $0.112, scoring $0.047771, planning $0.009794. All events were priced. This sample exceeds both the 10-second latency target and the $0.30 task-cost goal; it does not establish the current average. No paid image generation was run for this smoke check. Background-decision reuse was checked with exact-input, changed-input and severe-decision tests rather than claiming a measured production selfie speedup.

Validation: all 391 Photo Scout Python tests passed; deployed health check passed and the new transport/review modules and reuse metric were verified in the running container.
