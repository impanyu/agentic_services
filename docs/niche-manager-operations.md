# Niche Manager operations

The application-owned OpenAI Agents SDK runtime is in `src/agentic_services/niche_agent/`.
It reuses `OPENAI_API_KEY`; the separate model defaults to `gpt-6-sol` with high reasoning.
The collector, user UI, paid API and manager share the existing SQLite database.

## Execution and continuity

Run `python -m agentic_services.niche_agent.worker`, or use the `niche-manager`
Compose service. `NICHE_AGENT_ENABLED=1` enables execution; absent/0 stays idle.
A six-hour tick creates a durable inbox event. Signed source callbacks, collection
batches, operator objectives, subscription polls and agent follow-ups also enqueue
events. Pending events are checked every five seconds by the independent worker.
One fenced lease protects manager mutations; heartbeat refreshes it every 30 seconds.
Expired leases recover unfinished events. Failed runs retry after 15 minutes;
three failed attempts become dead events visible to the admin. Successful runs
acknowledge only their leased batch. Events arriving during a run stay pending.

Each wake has a persisted SDK SQLite session. Persistent plans, decisions, hypotheses,
unresolved work and knowledge revisions bridge wakes/restarts. We do not blindly
concatenate the entire lifetime transcript or truncate arbitrary tool/result pairs.
The agent chooses tools and ordering, loops on their results, and can recover from
errors. There is no fixed analysis-stage graph.

Default limits: 20 model turns/wake, 4,000 output tokens/request, ten minutes/wake,
24 requests/day and 300,000 tokens/day. Before each model call, UTF-8 bytes of the
input, instructions and tool schemas plus overhead conservatively reserve tokens.
Successful responses settle to reported usage; failed/unknown requests keep their
reservation. Budget exhaustion defers events to the next UTC day instead of dropping
or repeatedly retrying them. These are request/token caps, not a dollar billing meter.
Automatic SDK/client retries are disabled. Model tracing/provider storage is disabled.

## Available tools

- Source configuration and recent health; bounded collection through existing adapters.
- Agent-selected Brave queries and discovery leads (same access/storage/quota gates).
- Permissioned HTTPS document reads and exact-excerpt evidence ingestion.
- Evidence search, domain/date metrics and knowledge-base search including drafts.
- Persistent memory/plan search and writes.
- Atomic, idempotent create/update/publish/withdraw/merge; atomic split.
- Follow-up scheduling and create/update/pause polling/callback subscriptions.
- Tool discovery with JSON schemas; operator-configured streamable HTTP MCP tools.

`NICHE_AGENT_FETCH_DOMAINS` grants exact domains only, with a nonempty
`NICHE_AGENT_FETCH_REFERENCE` documenting source access/storage permission.
HTTPS only; public-address and domain checks apply on every redirect. Reads cap
at 256 KiB and return at most 12,000 normalized characters. Ingestion requires
an exact 25–500 character substring of a successful run-local receipt, then removes
email addresses. It cannot fabricate text or ingest arbitrary callback content.
Direct Reddit document reads are blocked; use the authorized Reddit adapter.

`NICHE_AGENT_MCP_SERVERS` is an operator-managed JSON array of HTTPS endpoints:
`[{"name":"research","url":"https://tools.example/mcp","bearer_env":"NICHE_MCP_RESEARCH_KEY"}]`.
Credential references resolve outside model context. Configure trusted, scoped
servers only; external tool billing/permissions are managed by that connector.
MCP tools are discovered each run and share the tool catalog; none are configured
by default. Arbitrary host shell execution, browser automation and code installation
are not tools in this initial registry. Add isolated capabilities through approved
connectors rather than exposing the production host to generated code.

## Knowledge revisions

A stable operation ID has a persisted input hash and result receipt. Replay returns
the receipt; reusing it with changed input fails. Updates check expected revision.
Scores retain their existing editorial-hypothesis semantics. Publication requires
three extant signal IDs, two domains, medium/high confidence, substantive rationale
and counterevidence/gaps. These structural gates do not guarantee market validity;
the manager must assess coherence and independent evidence. Drafts may use one signal.
Revision history records assessments and rationale. A split validates every child
and withdraws its parent in one transaction. Merge retires redundant records in the
same transaction as the target revision. Human/API readers see the same current
published records; confidence and counterevidence appear in report details.

Reddit source deletion conservatively clears derived agent memory, revision history
and SDK transcripts, plus dependent niches. No direct Reddit collection is enabled
by this implementation. Search leads cannot satisfy publication evidence thresholds.

## Admin and callback routes

These routes are excluded from public OpenAPI and have no contribution UI:

- `GET /niche-discovery/v1/admin/manager` — X-Admin-Key; queue/runs/tool log/budget.
- `POST /niche-discovery/v1/admin/manager/wake` — X-Admin-Key;
  `{"operation_id":"stable-id","objective":"Research objective with enough detail"}`.
- `POST /niche-discovery/v1/admin/manager/subscriptions` — X-Admin-Key;
  `{"identifier":"github","source":"github","secret_env":"NICHE_CALLBACK_GITHUB", "interval_seconds":21600,"enabled":true}`.
- `POST /niche-discovery/v1/source-events/{identifier}` — configured active subscription.

Generic callbacks require `X-Niche-Delivery` (stable ID), `X-Niche-Timestamp`
(Unix seconds within five minutes), and `X-Niche-Signature` (hex HMAC-SHA256 of
`timestamp + '.' + exact raw body`). The secret is the referenced `NICHE_CALLBACK_*`
environment value. GitHub subscriptions alternatively accept `X-Hub-Signature-256`,
`X-GitHub-Delivery` and `X-GitHub-Event` for issues/issue_comment/ping, with configured
repository scope checks. Body limit is 64 KiB. Only source/delivery references are
queued; the agent fetches details through a permitted adapter. Callback verification
never grants source access. Creating a local subscription does **not** register an
external webhook at GitHub or any other platform.

## Verification boundary

Tests exercise real SDK multi-turn function calls with a synthetic model, persistent
memory/transcripts, retry/lease recovery, callback signatures/deduplication, revision
replay/conflicts, publication gates, atomic split/merge and source-read permissions.
Live model checks use actual bounded HN/GitHub observations; synthetic fixtures are
never deployed as market evidence. Source configuration, collection, verified evidence
and market demand remain distinct statuses. Billing stays disabled until real reports
and payment/webhook delivery are verified. External search credentials, Reddit approval,
source-document grants and third-party webhook registrations remain separate setup.

Live validation on 2026-10-06 used actual HN/GitHub observations and the reused
OpenAI key. GPT-6 Sol autonomously invoked seven tools, created one unpublished,
low-confidence WooCommerce fulfillment-migration draft, saved a follow-up plan,
and completed/acknowledged the event. This validates tool execution and database
maintenance, not a paid market opportunity. No fixture data was uploaded.
