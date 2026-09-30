import { serve } from '@hono/node-server'
import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { Hono, type MiddlewareHandler } from 'hono'
import { discovery } from 'mppx/hono'
import { Mppx, evm, stripe } from 'mppx/server'
import { createMcpHandler } from './mcp.js'

const recipient = requireEnv('PAYMENT_RECIPIENT') as `0x${string}`
const secretKey = requireEnv('MPP_SECRET_KEY')
const internalApiKey = requireEnv('WEB_EVIDENCE_API_KEY')
const upstreamUrl = process.env.UPSTREAM_URL ?? 'http://web-evidence:8000'
const publicBaseUrl = process.env.PUBLIC_BASE_URL ?? 'https://api.aisoup.net'
const standardPrice = process.env.WEB_EVIDENCE_PRICE_USD ?? '0.05'
const facilitator = process.env.X402_FACILITATOR_URL ?? 'https://facilitator.openx402.ai'
const stripeSecretKey = process.env.STRIPE_SECRET_KEY
const stripeNetworkId = process.env.STRIPE_NETWORK_ID ?? 'agentic-services'
const stripeMinimumPrice = process.env.STRIPE_MINIMUM_PRICE_USD ?? '0.50'
const providerContact = process.env.WEB_EVIDENCE_PROVIDER_CONTACT ?? 'services@example.com'
const indexNowKey = process.env.INDEXNOW_KEY

const tiers = [
  {
    id: 'quick',
    path: '/v1/claims/verify/quick',
    price: process.env.WEB_EVIDENCE_QUICK_PRICE_USD ?? '0.02',
    maxToolCalls: 1,
    maxOutputTokens: 1500,
    maxSources: 3,
    snapshotMode: 'none',
    maxSnapshots: 0,
    summary: 'Quick verification for a narrow claim using up to 3 cited sources.',
  },
  {
    id: 'standard',
    path: '/v1/claims/verify',
    price: standardPrice,
    maxToolCalls: 3,
    maxOutputTokens: 3000,
    maxSources: 8,
    snapshotMode: 'cited',
    maxSnapshots: 3,
    summary: 'Standard verification with balanced evidence coverage.',
  },
  {
    id: 'deep',
    path: '/v1/claims/verify/deep',
    price: process.env.WEB_EVIDENCE_DEEP_PRICE_USD ?? '0.12',
    maxToolCalls: 7,
    maxOutputTokens: 6000,
    maxSources: 15,
    snapshotMode: 'cited',
    maxSnapshots: 8,
    summary: 'Deep verification for compound or contested claims using up to 15 cited sources.',
  },
  {
    id: 'research',
    path: '/v1/claims/verify/research',
    price: process.env.WEB_EVIDENCE_RESEARCH_PRICE_USD ?? '0.25',
    maxToolCalls: 15,
    maxOutputTokens: 12000,
    maxSources: 20,
    snapshotMode: 'all_sources',
    maxSnapshots: 20,
    summary: 'Research-grade verification with the largest search and evidence budget.',
  },
] as const

type VerificationTier = (typeof tiers)[number]

const claimRequestSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['claim'],
  properties: {
    claim: { type: 'string', minLength: 3, maxLength: 4000 },
    asOf: { type: ['string', 'null'], format: 'date' },
    jurisdiction: { type: ['string', 'null'] },
    freshnessHours: { type: ['integer', 'null'], minimum: 1, maximum: 8760 },
    sourcePolicy: { enum: ['official_only', 'authoritative', 'open_web'] },
    minimumSources: { type: 'integer', minimum: 1, maximum: 10 },
    maxSources: { type: 'integer', minimum: 1, maximum: 20 },
    allowedDomains: { type: 'array', items: { type: 'string' } },
    blockedDomains: { type: 'array', items: { type: 'string' } },
    includeConflicts: { type: 'boolean' },
    language: { type: 'string' },
  },
}

const claimResponseSchema = {
  type: 'object',
  required: ['verificationId', 'claim', 'status', 'observedAt', 'conclusion', 'atomicFacts', 'providerSources', 'evidence', 'snapshots', 'conflicts', 'limitations', 'provenance'],
  properties: {
    verificationId: { type: 'string' },
    claim: { type: 'string' },
    status: { enum: ['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous'] },
    observedAt: { type: 'string', format: 'date-time' },
    conclusion: { type: 'string' },
    atomicFacts: { type: 'array', items: { type: 'object' } },
    providerSources: { type: 'array', items: { type: 'object' } },
    evidence: { type: 'array', items: { type: 'object' } },
    snapshots: { type: 'array', items: { type: 'object' } },
    conflicts: { type: 'array', items: { type: 'object' } },
    limitations: { type: 'array', items: { type: 'string' } },
    provenance: { type: 'object' },
  },
}

const claimRequestExample = {
  claim: 'The Base mainnet chain ID is 8453.',
  sourcePolicy: 'authoritative',
  minimumSources: 2,
  maxSources: 4,
  includeConflicts: true,
  language: 'en',
}

const claimResponseExample = {
  verificationId: 'cv_example',
  claim: 'The Base mainnet chain ID is 8453.',
  status: 'confirmed',
  observedAt: '2026-01-01T00:00:00Z',
  conclusion: 'Authoritative sources confirm that Base mainnet uses chain ID 8453.',
  atomicFacts: [],
  providerSources: [],
  evidence: [],
  snapshots: [],
  conflicts: [],
  limitations: [],
  provenance: {},
}

const evmCharge = evm.charge({
  currency: evm.assets.base.USDC,
  recipient,
  x402: { facilitator, routeBinding: 'resource' },
})

const mcpHandler = await createMcpHandler({
  facilitator,
  recipient,
  upstreamUrl,
  internalApiKey,
  publicBaseUrl,
  tiers,
})

const app = new Hono()

app.all('/mcp', (c) => mcpHandler(withPublicUrl(c.req.raw)))

app.get('/', (c) => c.html(landingPage()))

app.get('/favicon.ico', (c) => c.body(faviconSvg(), 200, {
  'Content-Type': 'image/svg+xml; charset=UTF-8',
  'Cache-Control': 'public, max-age=86400',
}))

app.get('/googlebcb2306719d8bc8d.html', (c) => c.text(
  'google-site-verification: googlebcb2306719d8bc8d.html',
))

if (indexNowKey) {
  app.get('/.well-known/indexnow-key.txt', (c) => c.text(indexNowKey))
}

app.get('/robots.txt', (c) => c.text(`User-agent: *\nAllow: /\nSitemap: ${publicBaseUrl}/sitemap.xml\n`))

app.get('/sitemap.xml', (c) => {
  c.header('Content-Type', 'application/xml; charset=utf-8')
  return c.body(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/sitemap/0.9">\n  <url><loc>${publicBaseUrl}/</loc></url>\n  <url><loc>${publicBaseUrl}/openapi.json</loc></url>\n  <url><loc>${publicBaseUrl}/.well-known/agent-service.json</loc></url>\n  <url><loc>${publicBaseUrl}/.well-known/mcp/server.json</loc></url>\n  <url><loc>${publicBaseUrl}/.well-known/agent-card.json</loc></url>\n</urlset>\n`)
})

app.get('/.well-known/mcp/server.json', (c) => c.json(mcpRegistryDocument()))

app.get('/.well-known/agent-card.json', (c) => {
  c.header('Cache-Control', 'public, max-age=3600')
  c.header('ETag', '"web-evidence-a2a-0.3.0"')
  return c.json(agentCard())
})

app.get('/.well-known/agent.json', (c) => c.redirect('/.well-known/agent-card.json', 308))

app.get('/.well-known/agent-service.json', async () => {
  const upstream = await fetch(new URL('/.well-known/agent-service.json', upstreamUrl), {
    headers: { Authorization: `Bearer ${internalApiKey}` },
  })
  if (!upstream.ok) return upstream
  const document = await upstream.json() as Record<string, any>
  document.service.version = '0.3.0'
  document.transports = [
    ...(document.transports ?? []),
    {
      id: 'public-mcp',
      type: 'mcp',
      url: `${publicBaseUrl}/mcp`,
      specification: `${publicBaseUrl}/.well-known/mcp/server.json`,
      authorization: 'x402',
    },
    {
      id: 'public-a2a',
      type: 'a2a',
      url: `${publicBaseUrl}/a2a`,
      specification: `${publicBaseUrl}/.well-known/agent-card.json`,
      authorization: 'x402-or-mpp',
    },
  ]
  if (stripeSecretKey) {
    for (const offer of (document.offers ?? [])) {
      const tier = tiers.find((candidate) => offer.operation === `verify-claim-${candidate.id}`)
      if (!tier || !Array.isArray(offer.paymentMethods)) continue
      if (!offer.paymentMethods.some((method: Record<string, unknown>) => method.protocol === 'mpp' && method.network === 'stripe')) {
        offer.paymentMethods.push({ protocol: 'mpp', network: 'stripe', asset: 'USD', payTo: stripeNetworkId })
      }
    }
    document.extensions = {
      ...document.extensions,
      paymentOptions: tiers.flatMap((tier) => paymentOptionsForTier(tier)),
    }
  }
  return jsonDocumentResponse(document, 'public, max-age=300')
})

app.use('/openapi.json', async (c, next) => {
  await next()
  if (!c.res.ok) return

  const document = await c.res.json() as Record<string, any>
  document.info['x-guidance'] =
    'Use POST /v1/claims/verify to verify one factual claim against current web evidence. Send a JSON body with claim and optional source, freshness, jurisdiction, and language constraints.'
  document.info.contact = { url: 'https://aisoup.net', email: providerContact }

  for (const tier of tiers) {
    const operation = document.paths[tier.path]?.post
    if (!operation) continue
    operation.operationId = `verifyClaim${tier.id[0].toUpperCase()}${tier.id.slice(1)}`
    operation.tags = ['Web Evidence']
    operation['x-verification-tier'] = {
      id: tier.id,
      maxToolCalls: tier.maxToolCalls,
      maxOutputTokens: tier.maxOutputTokens,
      maxSources: tier.maxSources,
      snapshotMode: tier.snapshotMode,
      maxSnapshots: tier.maxSnapshots,
    }
    operation['x-payment-info'] = {
      ...operation['x-payment-info'],
      price: { mode: 'fixed', currency: 'USD', amount: tier.price },
      protocols: [
        { x402: {} },
        { mpp: { method: 'evm', intent: 'charge', currency: evm.assets.base.USDC.address } },
        ...(stripeSecretKey ? [{ mpp: { method: 'stripe', intent: 'charge', currency: 'usd', amount: stripePaymentOptions(tier).amount } }] : []),
      ],
    }
    operation.responses['200'] = {
      description: 'Structured claim-verification result with cited evidence',
      content: {
        'application/json': {
          schema: claimResponseSchema,
        },
      },
    }
  }

  document.paths['/v1/url-snapshots/{snapshot_id}'] = {
    get: {
      operationId: 'getEvidenceSnapshot',
      tags: ['Evidence Snapshots'],
      summary: 'Retrieve immutable snapshot metadata and content hashes',
      security: [],
      parameters: [{ name: 'snapshot_id', in: 'path', required: true, schema: { type: 'string' } }],
      responses: { '200': { description: 'Snapshot metadata' }, '404': { description: 'Snapshot not found' } },
    },
  }
  document.paths['/v1/url-snapshots/{snapshot_id}/content'] = {
    get: {
      operationId: 'getEvidenceSnapshotContent',
      tags: ['Evidence Snapshots'],
      summary: 'Retrieve the immutable raw bytes for a captured snapshot',
      security: [],
      parameters: [{ name: 'snapshot_id', in: 'path', required: true, schema: { type: 'string' } }],
      responses: { '200': { description: 'Raw snapshot content' }, '404': { description: 'Snapshot content not found' } },
    },
  }
  document.paths['/v1/quotes'] = {
    post: {
      operationId: 'createVerificationQuote', tags: ['Commerce'],
      summary: 'Create a 15-minute machine-readable quote for a verification tier', security: [],
      requestBody: { required: true, content: { 'application/json': { schema: {
        type: 'object', properties: { tier: { enum: tiers.map((tier) => tier.id), default: 'standard' } }, additionalProperties: false,
      } } } },
      responses: { '200': { description: 'Quote with price, expiry, operation, and supported payment methods' } },
    },
  }
  document.paths['/v1/orders/{order_id}'] = {
    get: {
      operationId: 'getOrderByAccessToken', tags: ['Commerce'], summary: 'Retrieve one order and receipt with its order access token',
      parameters: [
        { name: 'order_id', in: 'path', required: true, schema: { type: 'string' } },
        { name: 'X-Agentic-Order-Token', in: 'header', required: true, schema: { type: 'string' } },
      ], responses: { '200': { description: 'Customer-safe order and signed receipt' }, '404': { description: 'Order not found' } },
    },
  }
  document.paths['/v1/customer/orders'] = {
    get: {
      operationId: 'listCustomerOrders', tags: ['Commerce'], summary: 'List orders owned by a registered customer',
      parameters: [
        { name: 'X-Agentic-Customer-Key', in: 'header', required: true, schema: { type: 'string' } },
        { name: 'limit', in: 'query', schema: { type: 'integer', minimum: 1, maximum: 200, default: 50 } },
        { name: 'offset', in: 'query', schema: { type: 'integer', minimum: 0, default: 0 } },
      ], responses: { '200': { description: 'Customer order list' }, '401': { description: 'Invalid customer key' } },
    },
  }
  document.paths['/v1/customer/orders/{order_id}'] = {
    get: {
      operationId: 'getCustomerOrder', tags: ['Commerce'], summary: 'Retrieve one order owned by a registered customer',
      parameters: [
        { name: 'order_id', in: 'path', required: true, schema: { type: 'string' } },
        { name: 'X-Agentic-Customer-Key', in: 'header', required: true, schema: { type: 'string' } },
      ], responses: { '200': { description: 'Customer-safe order and signed receipt' }, '404': { description: 'Order not found' } },
    },
  }
  document.paths['/v1/receipts/{order_id}/verify'] = {
    post: {
      operationId: 'verifyOrderReceipt', tags: ['Commerce'], summary: 'Verify the server signature on an issued receipt', security: [],
      parameters: [{ name: 'order_id', in: 'path', required: true, schema: { type: 'string' } }],
      responses: { '200': { description: 'Receipt signature verification result' }, '404': { description: 'Receipt not found' } },
    },
  }

  c.res = jsonDocumentResponse(document, 'public, max-age=300')
})

app.get('/.well-known/x402', (c) => c.json({
  version: 1,
  resources: tiers.map((tier) => `${publicBaseUrl}${tier.path}`),
  ownershipProofs: [recipient],
  instructions: 'POST a JSON claim-verification request. The endpoint returns x402 and MPP payment challenges before execution.',
}))

app.get('/llms.txt', (c) => c.text(`# Web Evidence

Web Evidence verifies factual claims against current web sources and returns structured, cited results.

Base URL: ${publicBaseUrl}
OpenAPI: ${publicBaseUrl}/openapi.json
Service manifest: ${publicBaseUrl}/.well-known/agent-service.json
x402 discovery: ${publicBaseUrl}/.well-known/x402
MCP Streamable HTTP: ${publicBaseUrl}/mcp
MCP registry metadata: ${publicBaseUrl}/.well-known/mcp/server.json
A2A Agent Card: ${publicBaseUrl}/.well-known/agent-card.json
A2A JSON-RPC: ${publicBaseUrl}/a2a

## Paid operation

${tiers.map((tier) => `POST ${tier.path}\nTier: ${tier.id}\nUSDC price: $${tier.price}\n${stripeSecretKey ? `Card price: $${stripePaymentOptions(tier).amount}\n` : ''}Maximum tool actions: ${tier.maxToolCalls}\nMaximum cited sources: ${tier.maxSources}\nSnapshot policy: ${tier.snapshotMode} (up to ${tier.maxSnapshots})`).join('\n\n')}
Payment: x402 or MPP using USDC on Base (eip155:8453)${stripeSecretKey ? `; MPP Stripe card/USD is also accepted with a $${stripeMinimumPrice} minimum charge` : ''}

Minimum request body:
{"claim":"A factual statement to verify","minimumSources":1}

The initial unauthenticated request returns HTTP 402. Complete one advertised payment challenge and retry with the resulting payment credential.

Successful Standard, Deep, and Research responses include snapshot IDs. Retrieve metadata at GET /v1/url-snapshots/{snapshot_id} and immutable raw bytes at GET /v1/url-snapshots/{snapshot_id}/content.

Commerce: POST /v1/quotes returns a 15-minute quote. Successful paid HTTP and A2A calls return X-Agentic-Order-Id, X-Agentic-Order-Token, and X-Agentic-Receipt-Id headers. Query one order with GET /v1/orders/{order_id} and the X-Agentic-Order-Token header. Registered customers can query GET /v1/customer/orders with X-Agentic-Customer-Key.
`))

app.get('/healthz', async (c) => {
  const upstream = await fetch(`${upstreamUrl}/healthz`)
  return c.json({ status: upstream.ok ? 'ok' : 'degraded', upstream: upstream.status }, upstream.ok ? 200 : 503)
})

app.post('/v1/quotes', async (c) => {
  const upstream = await proxyRequest(c.req.raw, '/v1/quotes')
  if (!upstream.ok) return upstream
  const quote = await upstream.json() as Record<string, any>
  const tier = tiers.find((candidate) => candidate.id === quote.tier)
  if (tier) quote.paymentOptions = paymentOptionsForTier(tier)
  return jsonDocumentResponse(quote, 'private, no-store')
})

if (stripeSecretKey) {
  const stripeCharge = stripe.charge({
    secretKey: stripeSecretKey,
    networkId: stripeNetworkId,
    currency: 'usd',
    decimals: 2,
    paymentMethodTypes: ['card'],
  })
  const payments = Mppx.create({ methods: [evmCharge, stripeCharge], secretKey })
  const paidTiers = tiers.map((tier) => ({
    tier,
    handler: toHonoPayment(payments.compose(
      [evmCharge, paymentOptions(tier)],
      [stripeCharge, stripePaymentOptions(tier)],
    ), tier),
  }))
  mountPaidRoutes(payments, paidTiers)
} else {
  const payments = Mppx.create({ methods: [evmCharge], secretKey })
  const paidTiers = tiers.map((tier) => ({
    tier,
    handler: toHonoPayment(payments.evm.charge(paymentOptions(tier)), tier),
  }))
  mountPaidRoutes(payments, paidTiers)
}

app.all('*', async (c) => proxyRequest(c.req.raw, new URL(c.req.url).pathname))

serve({ fetch: app.fetch, hostname: '0.0.0.0', port: 8010 })

function paymentOptions(tier: VerificationTier) {
  return {
    amount: tier.price,
    description: `${tier.id} verification of one factual claim against current web evidence`,
  }
}

function stripePaymentOptions(tier: VerificationTier) {
  const amount = Math.max(Number(tier.price), Number(stripeMinimumPrice)).toFixed(2)
  return {
    amount,
    description: `${tier.id} verification of one factual claim against current web evidence (card price)`,
  }
}

function paymentOptionsForTier(tier: VerificationTier) {
  const baseOptions = [
    {
      protocol: 'x402', method: 'evm', network: 'eip155:8453', asset: 'USDC',
      amount: tier.price, amountMicrousd: Math.round(Number(tier.price) * 1_000_000), payTo: recipient,
    },
    {
      protocol: 'mpp', method: 'evm', network: 'eip155:8453', asset: 'USDC',
      amount: tier.price, amountMicrousd: Math.round(Number(tier.price) * 1_000_000), payTo: recipient,
    },
  ]
  if (!stripeSecretKey) return baseOptions
  const stripeAmount = stripePaymentOptions(tier).amount
  return [...baseOptions, {
    protocol: 'mpp', method: 'stripe', network: 'stripe', asset: 'USD',
    amount: stripeAmount, amountMicrousd: Math.round(Number(stripeAmount) * 1_000_000), payTo: stripeNetworkId,
  }]
}

type PaymentHandler = ((request: Request) => Promise<
  | { status: 402; challenge: Response }
  | { status: 200; withReceipt(response?: Response): Response }
>) & { _internal?: unknown }

function toHonoPayment(
  handler: PaymentHandler,
  tier: VerificationTier,
): MiddlewareHandler & { _internal?: unknown } {
  const middleware: MiddlewareHandler = async (c, next) => {
    const result = await handler(withPublicUrl(c.req.raw))
    if (result.status === 402) {
      return new URL(c.req.url).pathname === tier.path
        ? withBazaarSchema(result.challenge, tier)
        : result.challenge
    }
    await next()
    c.res = result.withReceipt(c.res)
  }
  Object.assign(middleware, { _internal: handler._internal })
  return middleware
}

function withPublicUrl(request: Request): Request {
  const incoming = new URL(request.url)
  const publicUrl = new URL(incoming.pathname + incoming.search, publicBaseUrl)
  return new Request(publicUrl, request)
}

function withBazaarSchema(response: Response, tier: VerificationTier): Response {
  const encoded = response.headers.get('Payment-Required')
  if (!encoded) return response

  const payload = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'))
  payload.extensions = {
    ...payload.extensions,
    bazaar: {
      info: {
        input: {
          type: 'http',
          method: 'POST',
          bodyType: 'json',
          body: { ...claimRequestExample, maxSources: tier.maxSources },
        },
        output: {
          type: 'json',
          example: claimResponseExample,
        },
      },
      schema: {
        $schema: 'https://json-schema.org/draft/2020-12/schema',
        type: 'object',
        properties: {
          input: {
            type: 'object',
            properties: {
              type: { type: 'string', const: 'http' },
              method: { type: 'string', enum: ['POST', 'PUT', 'PATCH'] },
              bodyType: { type: 'string', enum: ['json', 'form-data', 'text'] },
              body: requestSchemaForTier(tier),
            },
            required: ['type', 'method', 'bodyType', 'body'],
            additionalProperties: false,
          },
          output: {
            type: 'object',
            properties: {
              type: { type: 'string' },
              example: claimResponseSchema,
            },
            required: ['type'],
          },
        },
        required: ['input'],
      },
    },
  }

  const headers = new Headers(response.headers)
  headers.set('Payment-Required', Buffer.from(JSON.stringify(payload)).toString('base64'))
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  })
}

function requestSchemaForTier(tier: VerificationTier) {
  return {
    ...claimRequestSchema,
    properties: {
      ...claimRequestSchema.properties,
      maxSources: {
        ...claimRequestSchema.properties.maxSources,
        maximum: tier.maxSources,
      },
    },
  }
}

function mountPaidRoutes(
  payments: Parameters<typeof discovery>[1],
  paidTiers: Array<{ tier: VerificationTier; handler: MiddlewareHandler }>,
): void {
  for (const { tier, handler } of paidTiers) {
    app.post(tier.path, handler, async (c) => proxyPaidRequest(c.req.raw, tier))
  }

  const standard = paidTiers.find(({ tier }) => tier.id === 'standard')
  if (!standard) throw new Error('Standard verification tier is required')
  app.post('/a2a', standard.handler, handleA2A)

  discovery(app, payments, {
    path: '/openapi.json',
    info: { title: 'Agentic Services Web Evidence', version: '0.3.0' },
    serviceInfo: {
      description: 'Paid, structured verification of factual claims using current web evidence.',
      name: 'Web Evidence',
      url: publicBaseUrl,
    },
    routes: paidTiers.map(({ tier, handler }) => (
      {
        handler,
        method: 'POST',
        path: tier.path,
        summary: tier.summary,
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: requestSchemaForTier(tier),
            },
          },
        },
      }
    )),
  })
}

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required`)
  return value
}

function mcpRegistryDocument() {
  return {
    $schema: 'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json',
    name: 'io.github.impanyu/web-evidence',
    title: 'Web Evidence',
    description: 'Paid claim verification with cited web evidence, source provenance, snapshots, and hashes.',
    websiteUrl: publicBaseUrl,
    repository: {
      url: 'https://github.com/impanyu/agentic_services',
      source: 'github',
    },
    version: '0.3.0',
    remotes: [{ type: 'streamable-http', url: `${publicBaseUrl}/mcp` }],
  }
}

function agentCard() {
  return {
    name: 'Web Evidence',
    description: 'Verifies factual claims against current web evidence and returns citations, provenance, snapshots, and hashes.',
    supportedInterfaces: [{
      url: `${publicBaseUrl}/a2a`,
      protocolBinding: 'JSONRPC',
      protocolVersion: '1.0',
    }],
    provider: { organization: 'Agentic Services', url: 'https://aisoup.net' },
    version: '0.3.0',
    documentationUrl: `${publicBaseUrl}/`,
    capabilities: {},
    defaultInputModes: ['text/plain'],
    defaultOutputModes: ['application/json', 'text/plain'],
    skills: [{
      id: 'verify-factual-claim',
      name: 'Verify a factual claim',
      description: 'Researches a claim on the current web and returns a structured verdict with complete source provenance.',
      tags: ['fact-checking', 'web-research', 'citations', 'evidence'],
      examples: ['Verify that the Base mainnet chain ID is 8453.'],
      inputModes: ['text/plain'],
      outputModes: ['application/json', 'text/plain'],
    }],
    extensions: [{
      uri: 'https://www.x402.org/',
      description: `Calls cost $${standardPrice} in USDC on Base; MPP Stripe card payments use a $${stripePaymentOptions(tiers.find((tier) => tier.id === 'standard')!).amount} card price.`,
      required: false,
      params: {
        discoveryUrl: `${publicBaseUrl}/.well-known/x402`,
        priceUsd: standardPrice,
        network: 'eip155:8453',
        asset: 'USDC',
      },
    }],
  }
}

async function handleA2A(c: any): Promise<Response> {
  let request: Record<string, any>
  try {
    request = await c.req.json()
  } catch {
    return c.json(jsonRpcError(null, -32700, 'Invalid JSON payload'), 400)
  }

  const id = request.id ?? null
  if (request.jsonrpc !== '2.0') return c.json(jsonRpcError(id, -32600, 'Request payload validation error'), 400)
  if (request.method !== 'SendMessage') return c.json(jsonRpcError(id, -32601, 'Method not found'), 404)

  const parts = request.params?.message?.parts
  const claim = Array.isArray(parts)
    ? parts.map((part: any) => typeof part?.text === 'string' ? part.text : '').filter(Boolean).join('\n')
    : ''
  if (claim.length < 3) return c.json(jsonRpcError(id, -32602, 'Invalid parameters: message.parts must contain text'), 400)

  const commerce = commerceMetadata(c.req.raw, tiers.find((tier) => tier.id === 'standard')!)
  const verificationResponse = await fetch(new URL('/v1/claims/verify', upstreamUrl), {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${internalApiKey}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': request.params?.message?.messageId ?? crypto.randomUUID(),
      ...commerce.headers,
    },
    body: JSON.stringify({ claim, minimumSources: 2, maxSources: 8 }),
  })
  if (!verificationResponse.ok) {
    return c.json(jsonRpcError(id, -32603, `Verification failed (${verificationResponse.status})`), 500)
  }

  const verification = await verificationResponse.json() as Record<string, unknown>
  const responseMessage: Record<string, unknown> = {
    messageId: crypto.randomUUID(),
    role: 'ROLE_AGENT',
    parts: [
      { text: String(verification.conclusion ?? 'Verification completed.') },
      { data: verification, metadata: { mediaType: 'application/json' } },
    ],
  }
  const contextId = request.params?.message?.contextId
  if (typeof contextId === 'string') responseMessage.contextId = contextId
  c.header('X-Agentic-Order-Id', commerce.orderId)
  c.header('X-Agentic-Order-Token', commerce.orderToken)
  const upstreamReceipt = verificationResponse.headers.get('X-Agentic-Receipt-Id')
  if (upstreamReceipt) c.header('X-Agentic-Receipt-Id', upstreamReceipt)
  return c.json({ jsonrpc: '2.0', id, result: { message: responseMessage } })
}

function jsonRpcError(id: unknown, code: number, message: string) {
  return { jsonrpc: '2.0', id, error: { code, message } }
}

function landingPage(): string {
  const offers = tiers.map((tier) => `
    <article>
      <h2>${escapeHtml(tier.id[0].toUpperCase() + tier.id.slice(1))} · $${escapeHtml(tier.price)}</h2>
      <p>${escapeHtml(tier.summary)}</p>
      <code>POST ${escapeHtml(tier.path)}</code>
    </article>`).join('')
  const structuredData = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'Service',
    name: 'Web Evidence',
    serviceType: 'Agent-facing claim verification API and MCP server',
    description: 'Paid claim verification with cited web evidence, complete provider source provenance, snapshots, and content hashes.',
    url: publicBaseUrl,
    provider: { '@type': 'Organization', name: 'Agentic Services', url: 'https://aisoup.net' },
    offers: tiers.map((tier) => ({
      '@type': 'Offer',
      name: `${tier.id} claim verification`,
      price: tier.price,
      priceCurrency: 'USD',
      url: `${publicBaseUrl}${tier.path}`,
    })),
  }).replace(/</g, '\\u003c')

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Web Evidence API and MCP Server</title>
<meta name="description" content="Agent-facing paid claim verification with cited sources, snapshots, hashes, x402 and MPP payments.">
<link rel="icon" href="/favicon.ico">
<link rel="canonical" href="${publicBaseUrl}/"><script type="application/ld+json">${structuredData}</script>
<style>body{font:16px/1.55 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:48px 24px;color:#17202a;background:#f7f9fb}header,article,section{background:#fff;border:1px solid #dfe6ee;border-radius:14px;padding:24px;margin:16px 0}h1{margin-top:0}a{color:#075bd8}code{overflow-wrap:anywhere}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.grid article{margin:0}</style>
</head><body><header><h1>Web Evidence</h1><p>Verify factual claims against current web evidence. Results include cited evidence, every source reported by the search provider, provenance, tier-dependent snapshots, and SHA-256 hashes.</p><p>Agents can pay per call with x402 or MPP using USDC on Base${stripeSecretKey ? `, or by card through MPP Stripe (minimum $${escapeHtml(stripeMinimumPrice)})` : ''}.</p></header>
<main><section><h2>Agent discovery</h2><ul><li><a href="/openapi.json">OpenAPI</a></li><li><a href="/.well-known/agent-service.json">Agent service manifest</a></li><li><a href="/.well-known/x402">x402 resources</a></li><li><a href="/.well-known/mcp/server.json">MCP server metadata</a></li><li><a href="/.well-known/agent-card.json">A2A Agent Card</a></li><li><a href="/llms.txt">llms.txt</a></li></ul><p>MCP Streamable HTTP endpoint: <code>${publicBaseUrl}/mcp</code></p><p>A2A JSON-RPC endpoint: <code>${publicBaseUrl}/a2a</code></p></section>
<section><h2>Pay-per-call tiers</h2><div class="grid">${offers}</div></section>
<section><h2>Try the protocol</h2><p>An unauthenticated request returns a payment challenge. After payment, retry with the credential supplied by an x402 or MPP client.</p><pre><code>curl -X POST ${publicBaseUrl}/v1/claims/verify/quick \\
  -H 'content-type: application/json' \\
  -d '{"claim":"The Base mainnet chain ID is 8453."}'</code></pre></section></main></body></html>`
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character] ?? character)
}

function faviconSvg(): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#075bd8"/><path d="M17 19h30v7H25v9h19v7H25v10h-8z" fill="white"/><circle cx="47" cy="48" r="5" fill="#70e1b1"/></svg>`
}

function jsonDocumentResponse(document: Record<string, any>, cacheControl: string): Response {
  return new Response(JSON.stringify(document), {
    headers: {
      'Content-Type': 'application/json; charset=UTF-8',
      'Cache-Control': cacheControl,
    },
  })
}

async function proxyRequest(request: Request, path: string): Promise<Response> {
  const url = new URL(request.url)
  const upstream = new URL(path + url.search, upstreamUrl)
  const headers = new Headers(request.headers)
  headers.set('Authorization', `Bearer ${internalApiKey}`)
  headers.set('Host', upstream.host)
  headers.delete('Payment-Signature')
  headers.delete('Payment-Authorization')
  headers.delete('X-Payment')
  for (const name of [...headers.keys()]) {
    if (name.toLowerCase().startsWith('x-agentic-order-') || name.toLowerCase() === 'x-agentic-payment-protocol') {
      headers.delete(name)
    }
  }

  return fetch(upstream, {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    duplex: 'half',
  } as RequestInit)
}

async function proxyPaidRequest(request: Request, tier: VerificationTier): Promise<Response> {
  const commerce = commerceMetadata(request, tier)
  const url = new URL(request.url)
  const upstream = new URL(tier.path + url.search, upstreamUrl)
  const headers = new Headers(request.headers)
  headers.set('Authorization', `Bearer ${internalApiKey}`)
  headers.set('Host', upstream.host)
  headers.delete('Payment-Signature')
  headers.delete('Payment-Authorization')
  headers.delete('X-Payment')
  for (const name of [...headers.keys()]) {
    if (name.toLowerCase().startsWith('x-agentic-order-') || name.toLowerCase() === 'x-agentic-payment-protocol') {
      headers.delete(name)
    }
  }
  for (const [name, value] of Object.entries(commerce.headers)) headers.set(name, value)
  const upstreamResponse = await fetch(upstream, {
    method: request.method,
    headers,
    body: request.body,
    duplex: 'half',
  } as RequestInit)
  const responseHeaders = new Headers(upstreamResponse.headers)
  responseHeaders.set('X-Agentic-Order-Id', commerce.orderId)
  responseHeaders.set('X-Agentic-Order-Token', commerce.orderToken)
  responseHeaders.set('Access-Control-Expose-Headers', 'X-Agentic-Order-Id, X-Agentic-Order-Token, X-Agentic-Receipt-Id')
  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  })
}

function commerceMetadata(request: Request, tier: VerificationTier) {
  const orderId = `ord_${randomUUID().replaceAll('-', '')}`
  const orderToken = `ort_${randomBytes(32).toString('base64url')}`
  const authorization = request.headers.get('Authorization') ?? ''
  const paymentAuthorization = request.headers.get('Payment-Authorization') ?? ''
  const mppMethod = paymentCredentialMethod(authorization) ?? paymentCredentialMethod(paymentAuthorization)
  const isStripe = mppMethod === 'stripe'
  const protocol = request.headers.has('Payment-Signature') || request.headers.has('X-Payment')
    ? 'x402'
    : request.headers.has('Payment-Authorization') || authorization.startsWith('Payment ')
      ? isStripe ? 'mpp-stripe' : 'mpp'
      : 'paid'
  const settledPrice = isStripe ? stripePaymentOptions(tier).amount : tier.price
  const headers: Record<string, string> = {
    'X-Agentic-Order-Id': orderId,
    'X-Agentic-Order-Token-Hash': createHash('sha256').update(orderToken).digest('hex'),
    'X-Agentic-Payment-Protocol': protocol,
    'X-Agentic-Order-Tier': tier.id,
    'X-Agentic-Order-Amount-Microusd': String(Math.round(Number(settledPrice) * 1_000_000)),
  }
  return { orderId, orderToken, headers }
}

function paymentCredentialMethod(value: string): string | undefined {
  const encoded = value.match(/^Payment\s+([A-Za-z0-9_-]+)$/i)?.[1]
  if (!encoded) return undefined
  try {
    const credential = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8'))
    return typeof credential?.challenge?.method === 'string'
      ? credential.challenge.method.toLowerCase()
      : undefined
  } catch {
    return undefined
  }
}
