import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js'
import { HTTPFacilitatorClient, x402ResourceServer } from '@x402/core/server'
import { ExactEvmScheme } from '@x402/evm/exact/server'
import { bazaarResourceServerExtension, declareDiscoveryExtension } from '@x402/extensions/bazaar'
import { createPaymentWrapper } from '@x402/mcp'
import { z } from 'zod'

interface ContractorMcpOptions {
  facilitator: string
  recipient: `0x${string}`
  upstreamUrl: string
  internalApiKey: string
  publicBaseUrl: string
  price: string
}

const inputSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['licenseNumber'],
  properties: {
    licenseNumber: { type: 'string', pattern: '^[0-9]{1,8}$', description: 'California CSLB license number.' },
  },
}

export async function createContractorMcpHandler(options: ContractorMcpOptions): Promise<(request: Request) => Promise<Response>> {
  const resourceServer = new x402ResourceServer(new HTTPFacilitatorClient({ url: options.facilitator }))
  resourceServer.register('eip155:8453', new ExactEvmScheme())
  resourceServer.registerExtension(bazaarResourceServerExtension)
  await resourceServer.initialize()
  const accepts = await resourceServer.buildPaymentRequirements({
    scheme: 'exact', network: 'eip155:8453', payTo: options.recipient, price: `$${options.price}`,
  })
  const wrapper = createPaymentWrapper(resourceServer, {
    accepts,
    resource: {
      url: `mcp://${new URL(options.publicBaseUrl).host}/check_c10_license`,
      description: 'Source-linked California C-10 contractor license, bond, and workers compensation check.',
      mimeType: 'application/json',
      serviceName: 'California C-10 Contractor Check',
      tags: ['contractor', 'license', 'california', 'construction'],
    },
    extensions: declareDiscoveryExtension({
      toolName: 'check_c10_license',
      description: `Check one California CSLB license. Price: $${options.price} USDC on Base per call.`,
      transport: 'streamable-http',
      inputSchema,
      example: { licenseNumber: '1234567' },
    }),
  })

  return async (request: Request): Promise<Response> => {
    const server = new McpServer({
      name: 'contractor-check',
      title: 'California C-10 Contractor Check',
      version: '0.1.0',
      description: 'Paid source-linked CSLB license preflight for California electrical contractors.',
      websiteUrl: `${options.publicBaseUrl}/contractor-check/`,
    })
    server.registerTool('list_contractor_check_prices', {
      title: 'List contractor check prices',
      description: 'Show the price and scope of the California C-10 check. This tool is free.',
      outputSchema: z.object({ priceUsd: z.string(), network: z.string(), scope: z.string() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    }, async () => {
      const details = { priceUsd: options.price, network: 'Base USDC', scope: 'One California CSLB license, C-10 classification, bond, and workers compensation' }
      return { content: [{ type: 'text', text: JSON.stringify(details) }], structuredContent: details }
    })
    server.registerTool('check_c10_license', {
      title: 'Check C-10 contractor license',
      description: `Fetch the current CSLB public detail and assess the C-10 classification, bond, and workers compensation fields. Costs $${options.price} USDC on Base per call.`,
      inputSchema: { licenseNumber: z.string().regex(/^[0-9]{1,8}$/).describe('California CSLB license number.') },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    }, wrapper(async (args) => {
      const orderId = `ord_${randomUUID().replaceAll('-', '')}`
      const orderToken = `ort_${randomBytes(32).toString('base64url')}`
      const upstream = await fetch(new URL('/contractor-check/v1/check', options.upstreamUrl), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${options.internalApiKey}`,
          'Content-Type': 'application/json',
          'X-Agentic-Order-Id': orderId,
          'X-Agentic-Order-Token-Hash': createHash('sha256').update(orderToken).digest('hex'),
          'X-Agentic-Order-Amount-Microusd': String(Math.round(Number(options.price) * 1_000_000)),
          'X-Agentic-Payment-Protocol': 'x402-mcp',
        },
        body: JSON.stringify(args),
      })
      const body = await upstream.text()
      if (!upstream.ok) return { isError: true, content: [{ type: 'text' as const, text: `Contractor check failed (${upstream.status}): ${body}` }] }
      const result = JSON.parse(body) as Record<string, unknown>
      result.commerce = {
        orderId, orderToken,
        receiptId: upstream.headers.get('X-Agentic-Receipt-Id'),
        orderUrl: `${options.publicBaseUrl}/v1/orders/${orderId}`,
      }
      return { content: [{ type: 'text' as const, text: JSON.stringify(result) }], structuredContent: result }
    }))
    const transport = new WebStandardStreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true })
    await server.connect(transport)
    return transport.handleRequest(request)
  }
}
