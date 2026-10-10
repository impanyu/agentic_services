# Input handling review — October 10, 2026

The planner now chooses search, help, unsupported, or uninterpretable. Only search produces an executable program. It records normalized input, a purpose summary, assumptions and subject role. Required evidence distinguishes visual, spatial, provider and combined conditions. Portrait backgrounds do not require people already present in the source imagery. Website feedback is persisted as a completed task and displayed without moving the map or calling imagery providers.

## Evidence and limits

- `actions-astra-2026-10-10.json` preserves the raw 51-case model evaluation: 45 passed automatic predicates, three timed out and three were incorrectly flagged by lexical predicates.
- `actions-reviewed-2026-10-10.json` records the predicate corrections after reviewing those plans: 48/51 passed; the three original timeouts remain recorded.
- `actions-recheck-2026-10-10.json` independently checks 15 cases against the revised prompt: 14 passed, including the three earlier timeout cases and `great lake view`. `café among high-rise buildings` timed out. Earlier failures are not overwritten.
- These are planner-only tests with partial semantic predicates. They do not prove arbitrary-input correctness, provider recall, image quality or payment delivery.
- Parsing median was about ten seconds and the timeout tail about forty seconds. The product goal of an average complete query under ten seconds remains unmet.
- Local verification: 362 Python tests, 102 frontend tests; candidate frontend feedback smoke checks at desktop and mobile widths. Deployment and live verification are reported separately.

Normal interpretation remains one model call. Only invalid plans get one repair within a shared 45-second deadline. Authentication, quota and network failures do not enter that repair path.
