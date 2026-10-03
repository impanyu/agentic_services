import { serve } from '@hono/node-server'
import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { Hono, type MiddlewareHandler } from 'hono'
import { generate as generatePaymentOpenApi } from 'mppx/discovery'
import { Mppx, evm, stripe } from 'mppx/server'
import { createMcpHandler } from './mcp.js'
import { createContractorMcpHandler } from './contractor-mcp.js'

const recipient = requireEnv('PAYMENT_RECIPIENT') as `0x${string}`
const secretKey = requireEnv('MPP_SECRET_KEY')
const internalApiKey = requireEnv('WEB_EVIDENCE_API_KEY')
const upstreamUrl = process.env.UPSTREAM_URL ?? 'http://web-evidence:8000'
const publicBaseUrl = process.env.PUBLIC_BASE_URL ?? 'https://api.aisoup.net'
const serviceBaseUrl = `${publicBaseUrl}/web-evidence`
const standardPrice = process.env.WEB_EVIDENCE_PRICE_USD ?? '0.05'
const facilitator = process.env.X402_FACILITATOR_URL ?? 'https://facilitator.openx402.ai'
const stripeSecretKey = process.env.STRIPE_SECRET_KEY
const stripeNetworkId = process.env.STRIPE_NETWORK_ID ?? 'agentic-services'
const stripeMinimumPrice = process.env.STRIPE_MINIMUM_PRICE_USD ?? '0.50'
const contractorOperation = {
  id: 'license-preflight',
  path: '/contractor-check/v1/check',
  price: '1.00',
}
const indexNowKey = process.env.INDEXNOW_KEY

const tiers = [
  {
    id: 'quick',
    path: '/v1/claims/verify/quick',
    servicePath: '/v1/services/web-evidence/claims/verify/quick',
    canonicalPath: '/web-evidence/v1/claims/verify/quick',
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
    servicePath: '/v1/services/web-evidence/claims/verify',
    canonicalPath: '/web-evidence/v1/claims/verify',
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
    servicePath: '/v1/services/web-evidence/claims/verify/deep',
    canonicalPath: '/web-evidence/v1/claims/verify/deep',
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
    servicePath: '/v1/services/web-evidence/claims/verify/research',
    canonicalPath: '/web-evidence/v1/claims/verify/research',
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
    claim: { type: 'string', minLength: 3, maxLength: 4000, description: 'The factual claim to verify.' },
    asOf: { type: ['string', 'null'], format: 'date', description: 'Optional date at which the claim should be evaluated.' },
    jurisdiction: { type: ['string', 'null'], description: 'Optional country, state, or legal jurisdiction.' },
    freshnessHours: { type: ['integer', 'null'], minimum: 1, maximum: 8760, description: 'Maximum preferred source age in hours.' },
    sourcePolicy: { enum: ['official_only', 'authoritative', 'open_web'], description: 'Policy controlling which kinds of web sources may be used.' },
    minimumSources: { type: 'integer', minimum: 1, maximum: 10, description: 'Minimum number of evidence sources requested.' },
    maxSources: { type: 'integer', minimum: 1, maximum: 20, description: 'Maximum number of evidence sources to return.' },
    allowedDomains: { type: 'array', items: { type: 'string' }, description: 'Optional hostname allowlist without schemes or paths.' },
    blockedDomains: { type: 'array', items: { type: 'string' }, description: 'Optional hostname blocklist without schemes or paths.' },
    includeConflicts: { type: 'boolean', description: 'Whether to search for and report conflicting evidence.' },
    language: { type: 'string', description: 'Preferred response language or auto.' },
  },
}

const claimResponseSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['verificationId', 'claim', 'status', 'observedAt', 'conclusion', 'atomicFacts', 'providerSources', 'evidence', 'snapshots', 'conflicts', 'limitations', 'provenance'],
  properties: {
    verificationId: { type: 'string' },
    claim: { type: 'string' },
    status: { enum: ['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous'] },
    observedAt: { type: 'string', format: 'date-time' },
    conclusion: { type: 'string' },
    atomicFacts: { type: 'array', items: atomicFactSchema() },
    providerSources: { type: 'array', items: providerSourceSchema() },
    evidence: { type: 'array', items: evidenceSchema() },
    snapshots: { type: 'array', items: snapshotSchema() },
    conflicts: { type: 'array', items: conflictSchema() },
    limitations: { type: 'array', items: { type: 'string' } },
    provenance: provenanceSchema(),
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
const contractorMcpHandler = await createContractorMcpHandler({
  facilitator, recipient, upstreamUrl, internalApiKey, publicBaseUrl,
  price: contractorOperation.price,
})

const app = new Hono()

app.all('/mcp', (c) => mcpHandler(withPublicUrl(c.req.raw)))
app.all('/contractor-check/mcp', (c) => contractorMcpHandler(withPublicUrl(c.req.raw)))
app.get('/contractor-check/.well-known/mcp/server.json', (c) => c.json({
  $schema: 'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json',
  name: 'io.github.impanyu/contractor-check',
  title: 'California C-10 Contractor Check',
  description: 'Paid source-linked California electrical contractor license preflight.',
  websiteUrl: 'https://aisoup.net/contractor-check/',
  repository: { url: 'https://github.com/impanyu/agentic_services', source: 'github' },
  version: '0.1.0',
  remotes: [{ type: 'streamable-http', url: `${publicBaseUrl}/contractor-check/mcp` }],
}))

app.get('/', (c) => c.html(landingPage()))

app.get('/favicon.ico', (c) => c.body(faviconSvg(), 200, {
  'Content-Type': 'image/svg+xml; charset=UTF-8',
  'Cache-Control': 'public, max-age=86400',
}))

app.get('/googlebcb2306719d8bc8d.html', (c) => c.text(
  'google-site-verification: googlebcb2306719d8bc8d.html',
))

if (indexNowKey) {
  app.get(`/${indexNowKey}.txt`, (c) => c.text(indexNowKey))
}

app.get('/robots.txt', (c) => c.text(`User-agent: *\nAllow: /\nSitemap: ${publicBaseUrl}/sitemap.xml\n`))

app.get('/sitemap.xml', (c) => {
  c.header('Content-Type', 'application/xml; charset=utf-8')
  return c.body(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/sitemap/0.9">\n  <url><loc>${publicBaseUrl}/</loc></url>\n  <url><loc>${publicBaseUrl}/platform/openapi.json</loc></url>\n  <url><loc>${publicBaseUrl}/status/</loc></url>\n  <url><loc>https://aisoup.net/web-evidence/</loc></url>\n  <url><loc>${serviceBaseUrl}/openapi.json</loc></url>\n  <url><loc>${serviceBaseUrl}/.well-known/agent-service.json</loc></url>\n  <url><loc>${serviceBaseUrl}/.well-known/mcp/server.json</loc></url>\n  <url><loc>${serviceBaseUrl}/.well-known/agent-card.json</loc></url>\n  <url><loc>https://aisoup.net/contractor-check/</loc></url>\n  <url><loc>${publicBaseUrl}/contractor-check/openapi.json</loc></url>\n  <url><loc>${publicBaseUrl}/contractor-check/.well-known/agent-service.json</loc></url>\n  <url><loc>${publicBaseUrl}/contractor-check/.well-known/mcp/server.json</loc></url>\n</urlset>\n`)
})

app.get('/platform/openapi.json', async () => {
  const upstream = await fetch(new URL('/openapi.json', upstreamUrl), {
    headers: { Authorization: `Bearer ${internalApiKey}` },
  })
  if (!upstream.ok) return upstream
  const upstreamDocument = await upstream.json() as Record<string, any>
  const publicPrefixes = [
    '/v1/contact/messages',
    '/support/v1/',
    '/status/',
    '/v1/services',
  ]
  const paths = Object.fromEntries(
    Object.entries(upstreamDocument.paths ?? {}).filter(([path]) => (
      publicPrefixes.some((prefix) => path === prefix || path.startsWith(prefix))
    )),
  )
  const document = {
    ...upstreamDocument,
    info: {
      title: 'Dream Workshop Platform and Customer Communication API',
      version: '0.1.0',
      description: 'Public contact, authenticated agent support, service status, and status-subscription endpoints.',
      contact: { url: 'https://aisoup.net/#contact' },
    },
    servers: [{ url: publicBaseUrl }],
    paths,
  }
  return jsonDocumentResponse(document, 'public, max-age=300')
})

app.get('/.well-known/mcp/server.json', (c) => c.json(mcpRegistryDocument()))

app.get('/.well-known/agent-card.json', (c) => {
  c.header('Cache-Control', 'public, max-age=3600')
  c.header('ETag', '"web-evidence-a2a-0.3.1"')
  return c.json(agentCard())
})

app.get('/.well-known/agent.json', (c) => c.redirect('/.well-known/agent-card.json', 308))

app.get('/.well-known/agent-service.json', async () => {
  const upstream = await fetch(new URL('/.well-known/agent-service.json', upstreamUrl), {
    headers: { Authorization: `Bearer ${internalApiKey}` },
  })
  if (!upstream.ok) return upstream
  const document = await upstream.json() as Record<string, any>
  document.service.id = `${serviceBaseUrl}/.well-known/agent-service.json`
  document.service.version = '0.3.1'
  document.service.homepage = 'https://aisoup.net/web-evidence/'
  document.provider.name = 'Dream Workshop LLC'
  document.provider.contact = 'https://aisoup.net/#contact'
  for (const transport of (document.transports ?? [])) {
    if (transport.id === 'public-http') {
      transport.specification = `${serviceBaseUrl}/openapi.json`
    }
  }
  document.extensions = {
    ...document.extensions,
    paymentDiscovery: `${serviceBaseUrl}/.well-known/x402`,
  }
  for (const operation of (document.operations ?? [])) {
    const tier = tiers.find((candidate) => operation.id === `verify-claim-${candidate.id}`)
    if (!tier) continue
    operation.path = tier.canonicalPath
    operation.extensions = {
      ...operation.extensions,
      legacyPaths: [tier.servicePath, tier.path],
    }
  }
  document.transports = [
    ...(document.transports ?? []),
    {
      id: 'public-mcp',
      type: 'mcp',
      url: `${serviceBaseUrl}/mcp`,
      specification: `${serviceBaseUrl}/.well-known/mcp/server.json`,
      authorization: 'x402',
    },
    {
      id: 'public-a2a',
      type: 'a2a',
      url: `${serviceBaseUrl}/a2a`,
      specification: `${serviceBaseUrl}/.well-known/agent-card.json`,
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
    'Use POST /web-evidence/v1/claims/verify to verify one factual claim against current web evidence. Send a JSON body with claim and optional source, freshness, jurisdiction, and language constraints.'
  document.info.contact = { url: 'https://aisoup.net/#contact' }

  for (const tier of tiers) {
    const operation = document.paths[tier.canonicalPath]?.post
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
  resources: tiers.map((tier) => `${publicBaseUrl}${tier.canonicalPath}`),
  legacyResources: tiers.flatMap((tier) => [
    `${publicBaseUrl}${tier.servicePath}`,
    `${publicBaseUrl}${tier.path}`,
  ]),
  ownershipProofs: [recipient],
  instructions: 'POST a JSON claim-verification request. The endpoint returns x402 and MPP payment challenges before execution.',
}))

app.get('/llms.txt', (c) => c.text(`# Web Evidence

Web Evidence verifies factual claims against current web sources and returns structured, cited results.

Base URL: ${publicBaseUrl}
OpenAPI: ${serviceBaseUrl}/openapi.json
Service manifest: ${serviceBaseUrl}/.well-known/agent-service.json
x402 discovery: ${serviceBaseUrl}/.well-known/x402
MCP Streamable HTTP: ${serviceBaseUrl}/mcp
MCP registry metadata: ${serviceBaseUrl}/.well-known/mcp/server.json
A2A Agent Card: ${serviceBaseUrl}/.well-known/agent-card.json
A2A JSON-RPC: ${serviceBaseUrl}/a2a

## Paid operation

${tiers.map((tier) => `POST ${tier.canonicalPath}\nTier: ${tier.id}\nUSDC price: $${tier.price}\n${stripeSecretKey ? `Card price: $${stripePaymentOptions(tier).amount}\n` : ''}Maximum tool actions: ${tier.maxToolCalls}\nMaximum cited sources: ${tier.maxSources}\nSnapshot policy: ${tier.snapshotMode} (up to ${tier.maxSnapshots})\nLegacy aliases: ${tier.servicePath}, ${tier.path}`).join('\n\n')}
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
  const contractorHandler = payments.compose(
    [evmCharge, { amount: contractorOperation.price, description: 'California C-10 contractor license preflight' }],
    [stripeCharge, { amount: contractorOperation.price, description: 'California C-10 contractor license preflight' }],
  )
  mountContractorRoute(contractorHandler)
} else {
  const payments = Mppx.create({ methods: [evmCharge], secretKey })
  const paidTiers = tiers.map((tier) => ({
    tier,
    handler: toHonoPayment(payments.evm.charge(paymentOptions(tier)), tier),
  }))
  mountPaidRoutes(payments, paidTiers)
  mountContractorRoute(payments.evm.charge({
    amount: contractorOperation.price,
    description: 'California C-10 contractor license preflight',
  }))
}

app.all('*', async (c) => {
  const path = new URL(c.req.url).pathname
  const humanPath = path.startsWith('/contractor-check/v1/')
    || path === '/web-evidence/v1/checkout' || path === '/web-evidence/v1/report'
  const origin = c.req.header('Origin')
  const allowed = origin === 'https://aisoup.net' || origin === 'https://www.aisoup.net'
  if (humanPath && c.req.method === 'OPTIONS') {
    if (!allowed) return c.body(null, 403)
    return new Response(null, { status: 204, headers: {
      'Access-Control-Allow-Origin': origin,
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      Vary: 'Origin',
    } })
  }
  const upstream = await proxyRequest(c.req.raw, path)
  if (!humanPath || !allowed) return upstream
  const headers = new Headers(upstream.headers)
  headers.set('Access-Control-Allow-Origin', origin)
  headers.set('Vary', 'Origin')
  return new Response(upstream.body, {
    status: upstream.status, statusText: upstream.statusText, headers,
  })
})

serve({ fetch: app.fetch, hostname: '0.0.0.0', port: 8010 })

function paymentOptions(tier: VerificationTier) {
  return {
    amount: tier.price,
    description: `${tier.id} verification of one factual claim against current web evidence`,
  }
}

function mountContractorRoute(handler: PaymentHandler): void {
  app.post(contractorOperation.path, async (c, next) => {
    const payment = await handler(withPublicUrl(c.req.raw.clone()))
    if (payment.status === 402) return payment.challenge
    await next()
    c.res = payment.withReceipt(c.res)
  }, async (c) => proxyPaidRequest(c.req.raw, contractorOperation))
}

function stripePaymentOptions(tier: { id: string; price: string }) {
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
    // MPP validates a digest of the request body. Give it a cloned stream so
    // the original remains available to the upstream service after payment.
    const result = await handler(withPublicUrl(c.req.raw.clone()))
    if (result.status === 402) {
      return ([tier.path, tier.servicePath, tier.canonicalPath] as readonly string[]).includes(new URL(c.req.url).pathname)
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

function atomicFactSchema() {
  return {
    type: 'object', additionalProperties: false,
    required: ['statement', 'status', 'confidence', 'explanation', 'evidenceIds'],
    properties: {
      statement: { type: 'string' },
      status: verificationStatusSchema(),
      confidence: { type: 'number', minimum: 0, maximum: 1 },
      explanation: { type: 'string' },
      evidenceIds: { type: 'array', items: { type: 'string' } },
    },
  }
}

function providerSourceSchema() {
  return {
    type: 'object', additionalProperties: false,
    required: ['sourceId', 'url', 'title', 'provider', 'searchCallIds', 'actions', 'queries', 'evidenceIds', 'cited', 'snapshotId'],
    properties: {
      sourceId: { type: 'string' }, url: { type: 'string', format: 'uri' },
      title: { type: ['string', 'null'] }, provider: { type: 'string' },
      searchCallIds: { type: 'array', items: { type: 'string' } },
      actions: { type: 'array', items: { type: 'string' } },
      queries: { type: 'array', items: { type: 'string' } },
      evidenceIds: { type: 'array', items: { type: 'string' } },
      cited: { type: 'boolean' }, snapshotId: { type: ['string', 'null'] },
    },
  }
}

function evidenceSchema() {
  return {
    type: 'object', additionalProperties: false,
    required: ['id', 'url', 'title', 'publisher', 'publishedAt', 'retrievedAt', 'excerpt', 'relationship', 'sourceType', 'qualityReason', 'providerSourceMatched', 'cited', 'snapshotId', 'consulted', 'snapshotted'],
    properties: {
      id: { type: 'string' }, url: { type: 'string', format: 'uri' }, title: { type: 'string' }, publisher: { type: 'string' },
      publishedAt: { type: ['string', 'null'] }, retrievedAt: { type: 'string', format: 'date-time' }, excerpt: { type: 'string' },
      relationship: { enum: ['supports', 'contradicts', 'context'] }, sourceType: { type: 'string' }, qualityReason: { type: 'string' },
      providerSourceMatched: { type: 'boolean' }, cited: { type: 'boolean' }, snapshotId: { type: ['string', 'null'] },
      consulted: { type: 'boolean', deprecated: true, description: 'Compatibility alias for providerSourceMatched.' },
      snapshotted: { type: 'boolean' },
    },
  }
}

function snapshotSchema() {
  return {
    type: 'object', additionalProperties: false,
    required: ['snapshotId', 'requestedUrl', 'finalUrl', 'retrievedAt', 'status', 'httpStatus', 'contentType', 'contentLength', 'rawSha256', 'normalizedSha256', 'failureReason'],
    properties: {
      snapshotId: { type: 'string' }, requestedUrl: { type: 'string', format: 'uri' }, finalUrl: { type: ['string', 'null'], format: 'uri' },
      retrievedAt: { type: 'string', format: 'date-time' }, status: { enum: ['captured', 'failed', 'blocked', 'too_large', 'unsupported'] },
      httpStatus: { type: ['integer', 'null'] }, contentType: { type: ['string', 'null'] }, contentLength: { type: ['integer', 'null'] },
      rawSha256: { type: ['string', 'null'] }, normalizedSha256: { type: ['string', 'null'] }, failureReason: { type: ['string', 'null'] },
    },
  }
}

function conflictSchema() {
  return {
    type: 'object', additionalProperties: false, required: ['summary', 'evidenceIds'],
    properties: { summary: { type: 'string' }, evidenceIds: { type: 'array', items: { type: 'string' } } },
  }
}

function provenanceSchema() {
  return {
    type: 'object', additionalProperties: false,
    required: ['provider', 'model', 'providerResponseId', 'searchedWeb', 'consultedSourceCount', 'citedSourceCount', 'providerSourceCount', 'matchedEvidenceCount', 'snapshottedSourceCount', 'webSearchCallCount', 'inputTokens', 'cachedInputTokens', 'outputTokens', 'cacheHit'],
    properties: {
      provider: { type: 'string' }, model: { type: 'string' }, providerResponseId: { type: 'string' }, searchedWeb: { type: 'boolean' },
      consultedSourceCount: { type: 'integer', minimum: 0 }, citedSourceCount: { type: 'integer', minimum: 0 },
      providerSourceCount: { type: 'integer', minimum: 0 }, matchedEvidenceCount: { type: 'integer', minimum: 0 },
      snapshottedSourceCount: { type: 'integer', minimum: 0 }, webSearchCallCount: { type: 'integer', minimum: 0 },
      inputTokens: { type: 'integer', minimum: 0 }, cachedInputTokens: { type: 'integer', minimum: 0 },
      outputTokens: { type: 'integer', minimum: 0 }, cacheHit: { type: 'boolean' },
    },
  }
}

function verificationStatusSchema() {
  return { enum: ['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous'] }
}

function mountPaidRoutes(
  payments: Parameters<typeof generatePaymentOpenApi>[0],
  paidTiers: Array<{ tier: VerificationTier; handler: MiddlewareHandler }>,
): void {
  for (const { tier, handler } of paidTiers) {
    app.post(tier.path, handler, async (c) => proxyPaidRequest(c.req.raw, tier))
    app.post(tier.servicePath, handler, async (c) => proxyPaidRequest(c.req.raw, tier))
    app.post(tier.canonicalPath, handler, async (c) => proxyPaidRequest(c.req.raw, tier))
  }

  const standard = paidTiers.find(({ tier }) => tier.id === 'standard')
  if (!standard) throw new Error('Standard verification tier is required')
  app.post('/a2a', standard.handler, handleA2A)

  const discoveryRoutes = paidTiers.map(({ tier, handler }) => ({
    handler,
    method: 'POST',
    path: tier.canonicalPath,
    summary: tier.summary,
    requestBody: {
      required: true,
      content: {
        'application/json': {
          schema: requestSchemaForTier(tier),
        },
      },
    },
  }))
  const discoveryDocument = generatePaymentOpenApi(payments, {
    info: { title: 'Agentic Services Web Evidence', version: '0.3.1' },
    serviceInfo: {
      description: 'Paid, structured verification of factual claims using current web evidence.',
      name: 'Web Evidence',
      url: `${publicBaseUrl}/web-evidence/`,
      categories: ['research', 'fact-checking', 'web-evidence'],
      docs: {
        apiReference: `${serviceBaseUrl}/openapi.json`,
        homepage: 'https://aisoup.net/web-evidence/',
        llms: `${serviceBaseUrl}/llms.txt`,
      },
    },
    routes: discoveryRoutes,
  })
  const discoveryInfo = discoveryDocument.info as Record<string, unknown>
  discoveryInfo.description = 'Paid claim verification for agents with cited evidence, complete provider-source provenance, reproducible snapshots, content hashes, and signed receipts.'
  const discoveryPaths = discoveryDocument.paths as Record<string, Record<string, Record<string, any>>>
  for (const tier of tiers) {
    const operation = discoveryPaths[tier.canonicalPath]?.post
    if (!operation) continue
    operation.description = `${tier.summary} Returns a structured verdict, citations, source provenance, snapshots, hashes, and payment receipt metadata.`
    operation.responses = {
      ...operation.responses,
      '200': {
        description: 'Structured claim-verification result with cited evidence and provenance.',
        content: { 'application/json': { schema: claimResponseSchema } },
      },
    }
  }
  discoveryPaths[contractorOperation.path] = {
    post: {
      operationId: 'checkC10ContractorLicense',
      tags: ['Contractor Check'],
      summary: 'Check one California C-10 contractor license',
      description: 'Pay $1 in Base USDC, then receive a source-linked CSLB license, bond, and workers compensation preflight.',
      requestBody: {
        required: true,
        content: { 'application/json': { schema: {
          type: 'object', additionalProperties: false, required: ['licenseNumber'],
          properties: { licenseNumber: { type: 'string', pattern: '^[0-9]{1,8}$' } },
        } } },
      },
      responses: {
        '200': { description: 'Source-linked contractor check report' },
        '402': { description: 'x402 or MPP payment required' },
      },
      'x-payment-info': {
        price: { mode: 'fixed', currency: 'USD', amount: contractorOperation.price },
        protocols: [{ x402: {} }, { mpp: { method: 'evm', intent: 'charge', currency: evm.assets.base.USDC.address } }],
      },
    },
  }
  app.get('/openapi.json', (c) => jsonDocumentResponse(discoveryDocument, 'public, max-age=300'))
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
    websiteUrl: 'https://aisoup.net/web-evidence/',
    repository: {
      url: 'https://github.com/impanyu/agentic_services',
      source: 'github',
    },
    version: '0.3.1',
    remotes: [{ type: 'streamable-http', url: `${serviceBaseUrl}/mcp` }],
  }
}

function agentCard() {
  return {
    protocolVersion: '1.0',
    name: 'Web Evidence',
    description: 'Verifies factual claims against current web evidence and returns citations, provenance, snapshots, and hashes.',
    supportedInterfaces: [{
      url: `${serviceBaseUrl}/a2a`,
      protocolBinding: 'JSONRPC',
      protocolVersion: '1.0',
    }],
    provider: { organization: 'Dream Workshop LLC', url: 'https://aisoup.net' },
    version: '0.3.1',
    iconUrl: `${publicBaseUrl}/favicon.ico`,
    documentationUrl: `${publicBaseUrl}/web-evidence/`,
    capabilities: {
      extensions: [
        {
          uri: 'https://www.x402.org/',
          description: `Pay $${standardPrice} in USDC on Base before calling this agent.`,
          required: true,
          params: {
            discoveryUrl: `${serviceBaseUrl}/.well-known/x402`,
            priceUsd: standardPrice,
            network: 'eip155:8453',
            asset: 'USDC',
          },
        },
        {
          uri: 'https://paymentauth.org/',
          description: `Complete an MPP charge using Base USDC at $${standardPrice} or MPP Stripe at $${stripePaymentOptions(tiers.find((tier) => tier.id === 'standard')!).amount}.`,
          required: true,
          params: {
            methods: ['evm', ...(stripeSecretKey ? ['stripe'] : [])],
            endpoint: `${serviceBaseUrl}/a2a`,
          },
        },
        {
          uri: 'https://a2a-registry.org/extensions/registry/v1',
          description: 'Registry metadata and payment capabilities.',
          required: false,
          params: {
            payment: {
              model: 'paid',
              protocols: ['x402', ...(stripeSecretKey ? ['stripe'] : [])],
              direction: 'inbound',
              rails: [
                {
                  network: 'base',
                  token: 'USDC',
                  type: 'stablecoin',
                  protocol: 'x402',
                  caip2: 'eip155:8453',
                  contractAddress: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
                },
                ...(stripeSecretKey ? [{
                  network: 'stripe',
                  token: 'USD',
                  type: 'fiat',
                  protocol: 'stripe',
                }] : []),
              ],
            },
          },
        },
      ],
    },
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
  const structuredData = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'ItemList',
    name: 'Dream Workshop Agent Services',
    description: 'Machine-discoverable and autonomously purchasable services for AI agents.',
    url: publicBaseUrl,
    itemListElement: [{ '@type': 'ListItem', position: 1, url: 'https://aisoup.net/web-evidence/', name: 'Web Evidence' }],
  }).replace(/</g, '\\u003c')

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dream Workshop — Agent Service Network</title>
<meta name="description" content="Machine-discoverable services AI agents can call and purchase autonomously.">
<link rel="icon" href="/favicon.ico"><link rel="canonical" href="${publicBaseUrl}/"><script type="application/ld+json">${structuredData}</script>
<style>:root{--ink:#111411;--paper:#f1efe8;--lime:#c7ff68;--coral:#ff7657;--line:rgba(17,20,17,.18);--mono:ui-monospace,SFMono-Regular,Menlo,monospace}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 Inter,system-ui,sans-serif}a{color:inherit;text-decoration:none}.nav{height:78px;padding:0 clamp(22px,5vw,76px);border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}.brand{font-weight:700;letter-spacing:-.03em}.nav span,.nav a:last-child{font:11px var(--mono);text-transform:uppercase}.hero{min-height:560px;padding:90px clamp(22px,7vw,110px);display:grid;align-content:center;background-image:linear-gradient(to right,transparent calc(100% - 1px),var(--line) 1px);background-size:12.5% 100%}.eyebrow{font:11px var(--mono);text-transform:uppercase;letter-spacing:.12em}.eyebrow i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--coral);margin-right:9px}.hero h1{font-size:clamp(58px,8vw,124px);line-height:.86;letter-spacing:-.07em;margin:28px 0 34px;max-width:1050px}.hero h1 em{font-family:Georgia,serif;font-weight:400}.hero p{font-size:clamp(18px,1.7vw,24px);max-width:740px}.catalog{padding:100px clamp(22px,7vw,110px);border-top:1px solid var(--line)}.section-head{display:flex;justify-content:space-between;margin-bottom:38px;font:11px var(--mono);text-transform:uppercase;letter-spacing:.1em}.service{display:grid;grid-template-columns:1fr 1.25fr;border:1px solid var(--ink);min-height:410px;transition:transform .2s}.service:hover{transform:translateY(-5px)}.service-main{padding:38px;background:var(--coral);display:flex;flex-direction:column}.status{font:10px var(--mono);text-transform:uppercase}.service h2{font:500 clamp(52px,6vw,86px)/.9 Georgia,serif;letter-spacing:-.055em;margin:auto 0 22px}.service-main p{font-size:18px}.service-meta{padding:38px;display:flex;flex-direction:column}.tags{display:flex;gap:8px;flex-wrap:wrap}.tags span{border:1px solid;padding:7px 9px;font:10px var(--mono)}.service-meta p{font-size:18px;max-width:650px;margin:auto 0}.links{display:grid;grid-template-columns:repeat(3,1fr);border-top:1px solid;margin:28px -38px -38px}.links a{padding:18px;border-right:1px solid;font:10px var(--mono);text-transform:uppercase}.links a:last-child{border-right:0}.catalog>.feeds{margin-top:24px;font:11px var(--mono);text-transform:uppercase}.contract{background:var(--ink);color:var(--paper);padding:90px clamp(22px,7vw,110px);display:grid;grid-template-columns:1fr 1fr;gap:10vw}.contract h2{font-size:clamp(42px,5vw,72px);line-height:.95;letter-spacing:-.05em;margin:0}.contract ol{margin:0;padding:0;list-style:none}.contract li{border-top:1px solid #4a4e49;padding:18px 0;display:grid;grid-template-columns:50px 1fr}.contract b{color:var(--lime);font:11px var(--mono)}footer{padding:48px clamp(22px,7vw,110px);display:flex;justify-content:space-between;font:11px var(--mono);text-transform:uppercase}@media(max-width:760px){.service,.contract{grid-template-columns:1fr}.hero{min-height:500px}.links{grid-template-columns:1fr}.links a{border-right:0;border-bottom:1px solid}.section-head{gap:20px}}</style>
</head><body><header class="nav"><a class="brand" href="https://aisoup.net">Dream Workshop</a><span><a href="https://status.aisoup.net">Status ↗</a>&nbsp;&nbsp;&nbsp;<a href="https://aisoup.net#agent-services">Company & products ↗</a></span></header>
<main><section class="hero"><div class="eyebrow"><i></i> Agent service network</div><h1>Services built<br><em>for agents.</em></h1><p>A growing catalog of APIs, MCP tools, software, and data services that agents can discover, call, and purchase autonomously.</p></section>
<section class="catalog"><div class="section-head"><span>01 / Live services</span><span>Machine-native · Pay per use</span></div><article class="service"><a class="service-main" href="https://aisoup.net/web-evidence/"><span class="status">● Live · Service 01</span><h2>Web<br>Evidence</h2><p>Verify claims against the current web.</p></a><div class="service-meta"><div class="tags"><span>API</span><span>MCP</span><span>A2A</span><span>x402 + MPP</span></div><p>Cited evidence, complete provider source provenance, reproducible snapshots, content hashes, and signed commercial receipts.</p><div class="links"><a href="https://aisoup.net/web-evidence/">Product page ↗</a><a href="${serviceBaseUrl}/openapi.json">OpenAPI ↗</a><a href="${serviceBaseUrl}/.well-known/agent-service.json">Manifest ↗</a></div></div></article><article class="service" style="margin-top:24px"><a class="service-main" style="background:var(--lime)" href="https://aisoup.net/contractor-check/"><span class="status">● Live · Service 02</span><h2>Contractor<br>Check</h2><p>Preflight a California electrical contractor license.</p></a><div class="service-meta"><div class="tags"><span>Human UI</span><span>API</span><span>MCP</span><span>Stripe</span><span>x402 + MPP</span></div><p>Source-linked C-10 classification, bond, and workers' compensation check. $19 per human report or $1 per agent call.</p><div class="links"><a href="https://aisoup.net/contractor-check/">Product page ↗</a><a href="${publicBaseUrl}/contractor-check/openapi.json">OpenAPI ↗</a><a href="${publicBaseUrl}/contractor-check/.well-known/agent-service.json">Manifest ↗</a></div></div></article><div class="feeds"><a href="${publicBaseUrl}/platform/openapi.json">Platform &amp; support OpenAPI ↗</a> · <a href="https://status.aisoup.net">Service status ↗</a></div></section>
<section class="contract"><h2>One pattern for<br>every service.</h2><ol><li><b>01</b><span>Discover the service and its machine-readable contract.</span></li><li><b>02</b><span>Choose a capability, price, and supported payment rail.</span></li><li><b>03</b><span>Pay, execute, and retain a verifiable receipt.</span></li></ol></section></main>
<footer><span>Dream Workshop LLC · 2026</span><a href="https://aisoup.net#contact">Contact ↗</a></footer></body></html>`
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
    const lowerName = name.toLowerCase()
    if (
      lowerName === 'x-agentic-order-id'
      || lowerName === 'x-agentic-order-token-hash'
      || lowerName === 'x-agentic-order-amount-microusd'
      || lowerName === 'x-agentic-payment-protocol'
    ) headers.delete(name)
  }

  return fetch(upstream, {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    duplex: 'half',
  } as RequestInit)
}

async function proxyPaidRequest(request: Request, tier: { path: string; id: string; price: string }): Promise<Response> {
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

function commerceMetadata(request: Request, tier: { id: string; price: string }) {
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
