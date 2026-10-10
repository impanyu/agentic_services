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

const geographicKinds=['lake','sea','river','peak','forest','waterside'] as const
const osmFeature=z.object({
 label:z.string().min(1).max(120),kind:z.enum(['tagged','intersection']).optional(),
 filters:z.array(z.object({key:z.string().min(1).max(80).regex(/^[A-Za-z0-9_:.-]+$/),value:z.string().max(160).nullable().optional(),required:z.boolean().optional()}).strict()).max(6).optional(),
 numericFilters:z.array(z.object({key:z.string().min(1).max(80).regex(/^[A-Za-z0-9_:.-]+$/),minimum:z.number().nullable().optional(),maximum:z.number().nullable().optional()}).strict()).max(4).optional(),
 proximityMeters:z.number().int().min(0).max(500).optional(),
}).strict()
const osmFeatureSchema={type:'object',additionalProperties:false,required:['label'],properties:{
 label:{type:'string',minLength:1,maxLength:120},kind:{enum:['tagged','intersection'],default:'tagged'},
 filters:{type:'array',maxItems:6,items:{type:'object',additionalProperties:false,required:['key'],properties:{key:{type:'string',minLength:1,maxLength:80,pattern:'^[A-Za-z0-9_:.-]+$'},value:{type:['string','null'],maxLength:160},required:{type:'boolean',default:true}}}},
 numericFilters:{type:'array',maxItems:4,items:{type:'object',additionalProperties:false,required:['key'],properties:{key:{type:'string',minLength:1,maxLength:80,pattern:'^[A-Za-z0-9_:.-]+$'},minimum:{type:['number','null']},maximum:{type:['number','null']}}}},
 proximityMeters:{type:'integer',minimum:0,maximum:500,default:150},
}}
const searchBranch=z.object({
 poiQueries:z.array(z.string().min(1).max(200)).max(4).optional(),
 geographicKinds:z.array(z.enum(geographicKinds)).max(6).optional(),
 geographicCombination:z.enum(['all','any']).optional(),
 osmFeatures:z.array(osmFeature).max(6).optional(),featureCombination:z.enum(['all','any']).optional(),
 visualIntent:z.string().max(1000).optional(),
}).strict()
const searchBranchSchema={type:'object',additionalProperties:false,properties:{
 poiQueries:{type:'array',maxItems:4,items:{type:'string',minLength:1,maxLength:200}},
 geographicKinds:{type:'array',maxItems:6,items:{enum:geographicKinds}},
 geographicCombination:{enum:['all','any'],default:'all'},
 osmFeatures:{type:'array',maxItems:6,items:osmFeatureSchema},featureCombination:{enum:['all','any'],default:'all'},
 visualIntent:{type:'string',maxLength:1000},
}}
const searchTools=['search_places','search_geography','search_features','sample_geography','feature_points','filter_geography','filter_features','union','intersection','area_imagery','point_imagery','collect_images','score_images','rank_results'] as const
const searchStep=z.object({
 id:z.string().regex(/^[a-zA-Z][a-zA-Z0-9_]{0,39}$/),tool:z.enum(searchTools),
 inputs:z.array(z.string().max(40)).max(6).optional(),queries:z.array(z.string().min(1).max(200)).max(4).optional(),
 geographicKinds:z.array(z.enum(geographicKinds)).max(6).optional(),osmFeatures:z.array(osmFeature).max(6).optional(),
 combination:z.enum(['all','any']).optional(),exclude:z.boolean().optional(),discoveryHints:z.boolean().optional(),
 weights:z.array(z.number().int().min(1).max(4)).max(6).optional(),visualIntent:z.string().max(1000).optional(),
}).strict()
const searchProgram=z.object({steps:z.array(searchStep).min(1).max(24),output:z.string().min(1).max(40)}).strict()
const searchProgramSchema={type:['object','null'],additionalProperties:false,required:['steps','output'],properties:{
 output:{type:'string',minLength:1,maxLength:40},steps:{type:'array',minItems:1,maxItems:24,items:{type:'object',additionalProperties:false,required:['id','tool'],properties:{
 id:{type:'string',pattern:'^[a-zA-Z][a-zA-Z0-9_]{0,39}$'},tool:{enum:searchTools},inputs:{type:'array',maxItems:6,items:{type:'string',maxLength:40}},
 queries:{type:'array',maxItems:4,items:{type:'string',minLength:1,maxLength:200}},geographicKinds:{type:'array',maxItems:6,items:{enum:geographicKinds}},
 osmFeatures:{type:'array',maxItems:6,items:osmFeatureSchema},combination:{enum:['all','any'],default:'all'},exclude:{type:'boolean',default:false},discoveryHints:{type:'boolean',default:false},
 weights:{type:'array',maxItems:6,items:{type:'integer',minimum:1,maximum:4}},visualIntent:{type:'string',maxLength:1000},
 }}}}}
const photoArguments={
  searchProgram:searchProgram.nullable().optional().describe('Validated data-flow program over search/spatial/set, collect_images, score_images and rank_results tools. Leave all other target/spatial fields empty; global center/radius/styles are shared. Up to 24 steps and 8 source queries.'),
  searchBranches:z.array(searchBranch).max(6).optional().describe('OR across independently constrained target groups; AND between target, geography and features inside a group. When set, leave top-level poiQueries/geographicKinds/osmFeatures empty.'),
  query:z.string().max(1000).optional().describe('Natural-language request. Explicit text overrides conflicting structured parameters.'),
  poiQueries:z.array(z.string().min(1).max(200)).max(4).optional().describe('Arbitrary POI categories or business names, e.g. coffee shops.'),
  osmFeatures:z.array(osmFeature).max(6).optional().describe('OSM physical features and attributes; combined using featureCombination. Tagged features use exact/existence filters; intersections use road topology.'),
  geographicCombination:z.enum(['all','any']).optional().describe('AND/all or OR/any across geographic requirements; default all.'),
  featureCombination:z.enum(['all','any']).optional().describe('AND/all or OR/any across OSM feature requirements; default all.'),
  geographicKinds:z.array(z.enum(geographicKinds)).max(6).optional().describe('Spatial requirements; combination is controlled by geographicCombination.'),
  scoringIntent:z.string().max(1000).optional().describe('Visual subject, style and requirements for matching and scoring.'),
  lat:z.number().min(-85).max(85).describe('Latitude of the search center.'),
  lon:z.number().min(-180).max(180).describe('Longitude of the search center.'),
  radius:z.number().int().min(100).max(20000).optional().describe('Search radius in meters; default 1000.'),
  limit:z.number().int().min(1).max(5).optional().describe('Deprecated compatibility field; ignored. All verified matching places are returned.'),
  photoStyles:z.array(z.enum(['nature','urban','vintage','iconic','artistic','waterside','minimal','adventure'])).min(1).max(8).optional().describe('Optional desired photo moods.'),
  preferences:z.string().max(500).optional().describe('Additional photography preferences.'),
}
export const photoInputSchema={type:'object',additionalProperties:false,required:['lat','lon'],properties:{
 searchProgram:searchProgramSchema,
 searchBranches:{type:'array',maxItems:6,items:searchBranchSchema},
 query:{type:'string',maxLength:1000},poiQueries:{type:'array',maxItems:4,items:{type:'string',minLength:1,maxLength:200}},
 osmFeatures:{type:'array',maxItems:6,items:osmFeatureSchema},
 geographicCombination:{enum:['all','any'],default:'all'},featureCombination:{enum:['all','any'],default:'all'},
 geographicKinds:{type:'array',maxItems:6,items:{enum:geographicKinds}},scoringIntent:{type:'string',maxLength:1000},
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
      const details = { priceUsd: options.price, network: 'Base USDC', scope: 'One nearby photo search; up to 24 candidate locations, eight horizontal directions per panorama; results depend on coverage.' }
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
