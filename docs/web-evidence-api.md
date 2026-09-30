# Web Evidence API

## Boundary

Web Evidence sells a verified research outcome, not a search-results page. Its primary resource is a **claim verification**: a time-stamped assessment of one claim, decomposed into atomic facts and connected to supporting, contradicting, or contextual sources.

The service preserves three separate facts instead of treating them as synonyms:

1. `providerSources` — every URL returned in OpenAI Web Search source metadata, including results not used in the conclusion.
2. `evidence[].cited` — the model referenced this evidence ID from an atomic fact or conflict.
3. `evidence[].snapshotted` — this service independently fetched and stored the URL content.

`evidence[].providerSourceMatched` reports whether an evidence URL matches provider source metadata. The old `consulted` field is retained only as a compatibility alias for that value. It does not prove that a human-like reader opened the page.

Captured snapshots include both `rawSha256` (exact response bytes) and `normalizedSha256` (stable text or canonical JSON where supported). PDF and other binary captures have a raw hash only. Failed, blocked, oversized, and unsupported fetch attempts remain in `snapshots` as explicit records.

## Implemented API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Process health without calling upstream services |
| `GET` | `/readyz` | Configuration and storage readiness |
| `GET` | `/v1/capabilities` | Machine-readable operation and lifecycle metadata |
| `GET` | `/.well-known/agent-service.json` | Canonical agent-service discovery document |
| `GET` | `/.well-known/mcp/server.json` | MCP Registry-compatible remote-server metadata |
| `POST` | `/mcp` | MCP Streamable HTTP endpoint; paid tools use x402 |
| `GET` | `/.well-known/agent-card.json` | A2A 1.0 Agent Card |
| `POST` | `/a2a` | A2A JSON-RPC `SendMessage`; Standard-tier payment |
| `POST` | `/v1/claims/verify/quick` | Quick verification: $0.02, 1 tool action, up to 3 cited sources |
| `POST` | `/v1/claims/verify` | Standard verification: $0.05, 3 tool actions, up to 8 cited sources |
| `POST` | `/v1/claims/verify/deep` | Deep verification: $0.12, 7 tool actions, up to 15 cited sources |
| `POST` | `/v1/claims/verify/research` | Research verification: $0.25, 15 tool actions, up to 20 cited sources |
| `GET` | `/v1/claims/verifications/{verification_id}` | Retrieve an immutable prior result |
| `GET` | `/v1/url-snapshots/{snapshot_id}` | Retrieve snapshot metadata, status, and hashes |
| `GET` | `/v1/url-snapshots/{snapshot_id}/content` | Retrieve exact captured bytes and `X-Content-SHA256` |

`POST /v1/claims/verify` accepts an optional `Idempotency-Key` header. The same key and request return the stored result without repeating search or model charges. Reusing the key with a different request returns `409 Conflict`.

The four paid paths return the same response schema. Their separate URLs make the price, search budget, and snapshot policy deterministic and independently discoverable. Tool actions include web searches, page opens, and find-in-page operations; cited-source limits control the evidence returned in the final result rather than the search engine's internal result count.

| Tier | Snapshot policy |
| --- | --- |
| Quick | Preserve all provider source metadata; do not fetch snapshots |
| Standard | Snapshot up to 3 matched sources cited by the final analysis |
| Deep | Snapshot up to 8 matched sources cited by the final analysis |
| Research | Snapshot up to 20 provider-reported sources, whether cited or not |

Snapshot capture rejects private, loopback, link-local, and otherwise non-public addresses; validates redirects; accepts text, JSON, XML, HTML, and PDF; caps each response at 5 MB; and runs at most four fetches concurrently.

## Claim verification request

```json
{
  "claim": "Example API supports commercial use at $20 per month.",
  "asOf": "2026-09-29",
  "jurisdiction": "US",
  "freshnessHours": 168,
  "sourcePolicy": "authoritative",
  "minimumSources": 2,
  "maxSources": 8,
  "allowedDomains": [],
  "blockedDomains": [],
  "includeConflicts": true
}
```

`sourcePolicy` values:

- `official_only` — restrict the search to caller-supplied official domains; `allowedDomains` is required.
- `authoritative` — prefer first-party, government, standards, academic, and primary reporting sources.
- `open_web` — search broadly while preserving source-quality metadata.

The output status is one of:

- `confirmed`
- `partially_confirmed`
- `contradicted`
- `insufficient_evidence`
- `ambiguous`

Each atomic fact receives its own status. The top-level status summarizes the whole claim.

## Planned API

These operations should be added only when their evidence guarantees are implemented:

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/page-comparisons` | Compare two snapshots and return structured changes |
| `POST` | `/v1/claim-monitors` | Schedule recurring verification of a claim |
| `GET` | `/v1/claim-monitors/{monitor_id}` | Read monitor state and latest result |
| `DELETE` | `/v1/claim-monitors/{monitor_id}` | Stop monitoring without deleting evidence |
| `POST` | `/v1/verification-jobs` | Start an asynchronous deep verification |
| `GET` | `/v1/verification-jobs/{job_id}` | Poll a long-running verification |
| `GET` | `/v1/evidence/{evidence_id}` | Retrieve a cited or snapshotted evidence object |
| `POST` | `/v1/quotes` | Resolve price before a paid operation |
| `GET` | `/v1/receipts/{receipt_id}` | Retrieve the signed payment and delivery receipt |

## Commercial boundary

Payment enforcement belongs at the Node payment gateway. The verification implementation receives a private Bearer credential and never handles wallet keys or raw payment credentials. The public `POST /v1/claims/verify` route advertises both x402 and MPP challenges and settles Base USDC before forwarding the request to the Python service.
