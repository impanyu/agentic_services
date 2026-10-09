import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js'
import { HTTPFacilitatorClient, x402ResourceServer } from '@x402/core/server'
import { ExactEvmScheme } from '@x402/evm/exact/server'
import { bazaarResourceServerExtension, declareDiscoveryExtension } from '@x402/extensions/bazaar'
import { createPaymentWrapper } from '@x402/mcp'
import { z } from 'zod'

interface PhotoMcpOptions {
  facilitator: string
  recipient: `0x${string}`
  upstreamUrl: string
  internalApiKey: string
  publicBaseUrl: string
  price: string
}

const photoArguments={
  lat:z.number().min(-85).max(85).describe('Latitude of the search center.'),
  lon:z.number().min(-180).max(180).describe('Longitude of the search center.'),
  radius:z.number().int().min(100).max(20000).optional().describe('Search radius in meters; default 1000.'),
  limit:z.number().int().min(1).max(5).optional().describe('Number of top-ranked photo spots; default 3. All scored POIs remain in poiResults.'),
  photoStyles:z.array(z.enum(['nature','urban','vintage','iconic','artistic','waterside','minimal','adventure'])).min(1).max(8).optional().describe('Optional desired photo moods.'),
  preferences:z.string().max(500).optional().describe('Additional photography preferences.'),
}
export const photoInputSchema={type:'object',additionalProperties:false,required:['lat','lon'],properties:{
 lat:{type:'number',minimum:-85,maximum:85},lon:{type:'number',minimum:-180,maximum:180},
 radius:{type:'integer',minimum:100,maximum:20000,default:1000},limit:{type:'integer',minimum:1,maximum:5,default:3},
 photoStyles:{type:'array',minItems:1,maxItems:8,items:{enum:['nature','urban','vintage','iconic','artistic','waterside','minimal','adventure']}},preferences:{type:'string',maxLength:500},
}}

export async function createPhotoMcpHandler(options: PhotoMcpOptions): Promise<(request: Request) => Promise<Response>> {
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
      url: `mcp://${new URL(options.publicBaseUrl).host}/discover_photo_spots`,
      description: 'Nearby photo spots ranked from actual street-level and geolocated images.',
      mimeType: 'application/json',
      serviceName: 'Photo Scout',
      tags: ['photography', 'travel', 'maps', 'street-view'],
    },
    extensions: declareDiscoveryExtension({
      toolName: 'discover_photo_spots',
      description: `Find nearby photo spots from location coordinates. Price: $${options.price} USDC on Base per call.`,
      transport: 'streamable-http',
      inputSchema:photoInputSchema,
      example: { lat:41.8827,lon:-87.6233,radius:500,limit:3 },
    }),
  })

  return async (request: Request): Promise<Response> => {
    const server = new McpServer({
      name: 'photo-scout',
      title: 'Photo Scout',
      version: '0.1.0',
      description: 'Image-grounded photo spot discovery with ranked viewpoints and camera headings.',
      websiteUrl: 'https://aisoup.net/photo-scout/',
    })
    server.registerTool('list_photo_scout_prices', {
      title: 'List Photo Scout prices',
      description: 'Show per-search pricing, payment method and limitations. This tool is free.',
      outputSchema: z.object({ priceUsd: z.string(), network: z.string(), scope: z.string() }),
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    }, async () => {
      const details = { priceUsd: options.price, network: 'Base USDC', scope: 'One nearby photo search; up to 25 candidate POIs, eight horizontal directions per panorama; results depend on coverage.' }
      return { content: [{ type: 'text', text: JSON.stringify(details) }], structuredContent: details }
    })
    server.registerTool('discover_photo_spots', {
      title: 'Discover nearby photo spots',
      description: `Find nearby POIs, compare available images, and return ranked photo spots with scores, camera headings, reasons and source links. Costs $${options.price} USDC on Base per call.`,
      inputSchema: photoArguments,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    }, wrapper(async (args) => {
      const orderId = `ord_${randomUUID().replaceAll('-', '')}`
      const orderToken = `ort_${randomBytes(32).toString('base64url')}`
      const upstream = await fetch(new URL('/photo-scout/v1/discover', options.upstreamUrl), {
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
      if (!upstream.ok) return { isError: true, content: [{ type: 'text' as const, text: `Photo search failed (${upstream.status}): ${body}` }] }
      const result = JSON.parse(body) as Record<string, unknown>
      result.commerce = {
        ...(result.commerce as Record<string, unknown> || {}),
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
