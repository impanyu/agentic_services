# Agent service release and charging

Use `services/<service-id>/release.json` as the release contract. The service manifest, OpenAPI document, MCP Registry metadata, gateway amount, backend amount, and human Stripe amount must agree before deployment. Run `python3 scripts/check-service-release.py services/<service-id>/release.json` to check this locally, then rerun with `--live` after deployment. The live check does not spend money.

For an agent service, expose a public HTTP 402 challenge and a Streamable HTTP MCP endpoint. The MCP tool list should state the per-call price and include one free pricing tool. A paid call must produce a result, order ID, order token, and signed receipt; the token must retrieve only that order. Keep Stripe Checkout for any human-facing UI. A Checkout redirect verifies payment before releasing the report, but a webhook is needed for reliable fulfillment when customers do not return to the site.

Release gates are distinct:

1. **Implemented:** local tests, manifest schema, pricing consistency, and gateway type check pass.
2. **Public:** deployed HTTPS manifest, OpenAPI, MCP metadata, MCP `tools/list`, and HTTP 402 challenge pass the live checker.
3. **Payment verified:** a settled test transaction proves result delivery, order access, receipt, and ledger entry. A 402 challenge alone does not establish this gate.
4. **Submitted:** submit the service's independent `server.json` to the official MCP Registry and submit its endpoint to free third-party directories. Record submission IDs or URLs.
5. **Listed:** retrieve each public directory listing without account privileges and verify its endpoint, tool names, version, and price. Enter its public URL in the release descriptor's `directories` field. A successful submission response or a crawler visit is not listing evidence.

For official MCP Registry publication use [`mcp-publisher`](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx) with GitHub authentication. Smithery may accept a remote server URL through its [publish API](https://smithery.mintlify.app/api-reference/servers/publish-a-server); use only its free path. Do not add paid verification or DNS TXT records for a directory. x402 Bazaar discovery depends on its [discovery extension](https://github.com/x402-foundation/x402/blob/main/docs/extensions/bazaar.mdx) and indexer observation; declare neither indexing nor payment success from the 402 response alone.

On every price or version change, update the release contract and all public metadata, rerun local and live checks, and verify the public directory entries again. Keep human and agent prices separate when they differ.
