# Photo Scout workflow experience

This first version records search execution experience and supplies bounded aggregates to the intent planner. It does not automatically change model tiers, batch sizes, concurrency limits, search coverage or scoring thresholds. Existing DAG execution continues to parallelize independent steps.

## Controls

`PHOTO_SCOUT_EXPERIENCE_MODE` accepts:

- `advise` (default): record executions and include eligible aggregates in planning instructions.
- `observe`: record executions without changing planner instructions.
- `off`: disable recording and planner advice. Existing rows are retained.

Restart the service after changing environment configuration. Missing, locked or corrupt experience storage fails open. Reads and writes use short SQLite lock timeouts.

## Records and comparisons

The existing application SQLite database gains `photo_workflow_experience`. The outer tracked search writes one record, including nested worker activity. Records include estimated API costs and unknown-cost events, actual model calls and token counts, wall time, tool timings, cache counts, source failures and execution outcome. Cancelled, failed and partial executions are not successful cost/latency samples. Costs use the application's price table, not an invoice.

Only allowlisted structural/numeric metadata is retained: workflow tools and dependencies, condition counts, radius bands and coarse 20-degree location cells. Raw queries, precise locations, POI names, images, account identifiers and exception messages are excluded from this experience table and planner snapshot.

History is isolated by code/configuration revision, workflow structure, workload and cache bands. At least five comparable observations are required for a group; cost and median latency require five eligible successful observations. P95 requires twenty successful observations. These are descriptive statistics, not guarantees or causal comparisons. Recent groups are included as context; the planner must judge applicability to the new task.

Rows older than 30 days are excluded and removed on subsequent writes. Storage is capped at 10,000 rows; reads inspect at most 1,000 current-revision search records. Advice contains at most four groups and a 7,000-byte JSON snapshot, plus fixed instructions. New code/configuration starts with no eligible history.

## Quality and rollout

Quality is explicitly **unmeasured**. Completion and match counts do not establish accuracy or recall. Instructions forbid skipping required sources, shrinking coverage or removing review to match historical costs. All tests use isolated temporary databases and mocked model calls; they do not establish production savings.

Evaluate on representative fixed queries before enabling automatic model routing or concurrency tuning. Compare total cost (including retries and planner overhead), end-to-end median/P95, source coverage and independently assessed result quality. Keep model and resource limits enforced by the runtime even when a future planner proposes alternatives.

## Cost optimizations (2026-10-12)

Places requests differing only in query whitespace/case share one in-flight request within the task, including across parallel workflow branches. Distinct categories, search regions, pagination tokens and request options remain separate. Query aliases within one branch are removed before allocating category shares. This does not add cross-task Google content storage.

Score cache version 3 normalizes workflow step identifiers and their declared references in requirements and image eligibility paths. Mere step renaming can reuse a result; topology, operators, query text, visual intent, eligible paths, image version, model and evaluation instructions remain in the key. Existing version 2 entries become cold once. The cache does not assume synonyms or similar images have identical meaning.

Matching-image output instructions request concise evidence, reasons and tips without repeating observations, while retaining all distinct condition evidence and uncertainty. Schema fields, model tiers, output budget and concurrency limits remain fixed. This is not an in-run adaptive policy.

Validation covers request deduplication, cache reuse/invalidation and provider delivery with mocked APIs. Before claiming real-world improvement, compare the same representative queries with advice off/on under separately controlled cold/warm caches, count all attempts and unknown costs, and review quality independently. Include planner overhead and failures. Changes to cache or provider configuration must not be attributed to experience advice alone.
