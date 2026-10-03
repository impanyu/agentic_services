# California C-10 Contractor Check

This human-and-agent service produces a first-pass report for one California contractor license. It reads the public CSLB license detail at execution time and reports license status, C-10 classification, contractor bond record, workers' compensation record, source timestamp, and a source hash. It does not authenticate insurance certificates or establish project-specific coverage.

- Human UI: `https://aisoup.net/contractor-check/`; $19 per report through Stripe Checkout.
- Agent API: `POST https://api.aisoup.net/contractor-check/v1/check`; $1 per call through x402 or MPP.
- Agent manifest: `https://api.aisoup.net/contractor-check/.well-known/agent-service.json`.
- OpenAPI: `https://api.aisoup.net/contractor-check/openapi.json`.

Stripe Checkout requires a service-scoped `CONTRACTOR_STRIPE_SECRET_KEY` and the human-sales account's `HUMAN_STRIPE_WEBHOOK_SECRET` in production. Do not use the gateway's `STRIPE_SECRET_KEY`: that belongs to the separate MPP payment rail. The signed Checkout webhook durably queues a paid report for fulfillment even if the buyer does not return to the site. The success page independently retrieves the Checkout session from Stripe and shares the same order, so a report is never released solely because a browser reached the success URL.

The launch check requires a real CSLB record to parse, a negative record test, a 402 challenge for the agent API, a Stripe Checkout session in live mode, and a completed payment verified end to end. Until the last check succeeds, do not report live card sales as verified.
