# Web Evidence

Web Evidence verifies a factual claim against current web sources and returns a structured, time-stamped result. It is designed for agents that need a decision-ready answer with provenance rather than a page of search results.

The HTTP implementation lives in `src/agentic_services`. The API contract, evidence levels, and planned operations are documented in [`../../docs/web-evidence-api.md`](../../docs/web-evidence-api.md).

The first operation is `POST /v1/claims/verify`. It supports source policy, domain allow and block lists, freshness targets, minimum evidence counts, conflict reporting, durable result retrieval, and idempotent retries.

The discovery document at `/.well-known/agent-service.json` embeds the operation's input and output JSON Schemas. The production gateway advertises x402 and MPP payment options for the same operation and forwards a request to the evidence service only after payment settlement.
