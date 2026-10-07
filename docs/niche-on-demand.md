# Niche Discovery: broader sources and private on-demand research

An English human UI and subscriber HTTP API share one autonomous Niche Manager.
A search miss returns `researchPath`; GET search never starts a billable/model task.
The user explicitly requests exploration with POST. The agent selects tools, collects
source-linked observations, compares existing knowledge, revises records, and commits
an outcome. No prescribed list of research steps or automatic forced publication.

## Subscriber API

`POST /niche-discovery/v1/research` requires the existing active `Bearer nd_…`
subscription and a stable `Idempotency-Key`. Body:

```json
{"query":"irrigation maintenance for small farms","category":null,"buyer":null,"region":null}
```

An existing catalog match returns 200 without a new job. Otherwise 202 includes
`id`, `status`, and `statusUrl`. Poll `GET /niche-discovery/v1/research/{identifier}`
with the same subscriber's token. Requests/results are private, use no-store headers,
and share the subscriber customer's daily quota across token rotations. Another
subscriber gets 404. Extra body fields, including signal contributions, are rejected.

Three new research jobs per UTC day by default (`NICHE_RESEARCH_DAILY_QUOTA`).
Identical requests within 24 hours and idempotent retries reuse the job. A conflicting
idempotency key returns 409, quota exhaustion 429, full queue/disabled worker 503.
Queue is limited to 100 outstanding jobs. No extra real per-call research charge is
implemented; the existing $0.25 per-evaluation offer is unrelated to exploration.

States: queued, running, waiting_for_budget, completed, insufficient_evidence, blocked,
failed. Completed results may contain explicitly **provisional unpublished** drafts.
Context or missing sources may yield zero records. A model final message does not
acknowledge a job unless `complete_research` committed a valid outcome. Uncommitted
jobs retry, bounded to three failures. Budget exhaustion waits until next UTC day
without burning an attempt. Foreground events have priority, one objective per run.
They cannot preempt an already leased run. The same persisted daily budget reserves
up to 16 requests/200,000 conservative tokens above the 32/300,000 background ceiling;
background wakes cannot consume that reserve. This is not a dollar billing meter.

Research objectives never become demand evidence. Shared-memory writes and shared
follow-up objectives are disabled during private request wakes. Session transcripts,
research objectives, principal hashes and job receipts expire after 30 days. Independently
source-backed assessment records may persist; raw user queries are not public.

## Sources and subscriptions

`GET /niche-discovery/v1/sources` separates configuration, collection outcomes and
WebSub lease state. Current adapter families include GitHub, HN, GDELT, permitted
Reddit/search, Stack Exchange, CPSC, Federal Register, NYC 311, CFPB and registered
RSS/Atom. Enabled does not mean working; see current collection outcomes.

Stack Exchange questions sample DIY, gardening, finance, travel, bicycles and cooking;
store current CC BY-SA 4.0 headlines with author/source/license metadata, not bodies.
CPSC recalls and Federal Register notices are context. Licensed RSS/Atom ingests
headlines/links/bylines only, not articles/media. NYC 311 has an extra rights-reference
gate; only non-identifying administrative fields are selected. CFPB stores structured
complaint fields, never narratives. These adapters have bounded samples, 80 attempts
per provider per UTC day, 15-minute identical-request suppression, provider backoff,
2 MiB response caps, HTTPS public-address checks and no followed redirects. API tools
support agent-selected keywords; feeds/recalls filter a bounded recent sample locally.
Broader search engines, private communities and shopping platforms still need their
own authorized API/partner access. No arbitrary-site or whole-web crawl is claimed.

Publication requires three demand observations from two source groups and at least
medium confidence. News/regulatory/recall context cannot fill this threshold. Counts
and expert questions are not willingness-to-pay or market-size validation. Public
reports link evidence and provide required attribution/license details.

Feed registry entries require id, url, enabled, rightsReference, license, attribution.
A WebSub entry also includes its independently verified advertised `hub`, and optional
`secretEnv` referencing `NICHE_CALLBACK_*`. The worker registers with the hub, and
renews before lease expiry. 202 from a hub is **pending**, not active. Verification GET
checks topic, mode, an HMAC-derived capability URL and challenge; signed POST checks
the exact body using the standard X-Hub-Signature algorithm, deduplicates by body hash,
and queues source references. The payload is never treated as evidence or instructions;
the agent re-fetches the canonical feed. Polling remains available if push registration
fails. Disabled feeds unsubscribe and stop accepting delivery; configuration URL changes
first retire the old subscription. Registration/renewal is deterministic infrastructure,
while research decisions remain with the agent. No secrets enter model-visible schemas.

Reference: [W3C WebSub](https://www.w3.org/TR/websub/),
[Global Voices license and attribution](https://globalvoices.org/about/global-voices-attribution-policy/),
[Stack Exchange API terms](https://stackoverflow.com/legal/api-terms-of-use),
[CFPB public complaint data](https://www.consumerfinance.gov/data-research/consumer-complaints/).
