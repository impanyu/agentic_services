# Web Evidence API

## Boundary

Web Evidence sells a verified research outcome, not a search-results page. Its primary resource is a **claim verification**: a time-stamped assessment of one claim, decomposed into atomic facts and connected to supporting, contradicting, or contextual sources.

The service distinguishes three levels of evidence:

1. `consulted` — the search system retrieved the source.
2. `cited` — the analysis relies on the source.
3. `snapshotted` — the platform fetched and hashed the source content itself.

The MVP returns consulted and cited sources. It must not claim a content hash or archival proof until the snapshot endpoint is implemented.

## Implemented API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/healthz` | Process health without calling upstream services |
| `GET` | `/readyz` | Configuration and storage readiness |
| `GET` | `/v1/capabilities` | Machine-readable operation and lifecycle metadata |
| `GET` | `/.well-known/agent-service.json` | Canonical agent-service discovery document |
| `POST` | `/v1/claims/verify` | Verify a claim synchronously and persist the result |
| `GET` | `/v1/claims/verifications/{verification_id}` | Retrieve an immutable prior result |

`POST /v1/claims/verify` accepts an optional `Idempotency-Key` header. The same key and request return the stored result without repeating search or model charges. Reusing the key with a different request returns `409 Conflict`.

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
| `POST` | `/v1/url-snapshots` | Fetch, normalize, timestamp, and hash one URL |
| `GET` | `/v1/url-snapshots/{snapshot_id}` | Retrieve an immutable snapshot record |
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
