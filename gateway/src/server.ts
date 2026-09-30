import { serve } from '@hono/node-server'
import { Hono, type MiddlewareHandler } from 'hono'
import { discovery } from 'mppx/hono'
import { Mppx, evm, stripe } from 'mppx/server'

const recipient = requireEnv('PAYMENT_RECIPIENT') as `0x${string}`
const secretKey = requireEnv('MPP_SECRET_KEY')
const internalApiKey = requireEnv('WEB_EVIDENCE_API_KEY')
const upstreamUrl = process.env.UPSTREAM_URL ?? 'http://web-evidence:8000'
const publicBaseUrl = process.env.PUBLIC_BASE_URL ?? 'https://api.aisoup.net'
const price = process.env.WEB_EVIDENCE_PRICE_USD ?? '0.05'
const facilitator = process.env.X402_FACILITATOR_URL ?? 'https://facilitator.openx402.ai'
const stripeSecretKey = process.env.STRIPE_SECRET_KEY
const stripeNetworkId = process.env.STRIPE_NETWORK_ID ?? 'agentic-services'

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
  required: ['verificationId', 'claim', 'status', 'observedAt', 'conclusion', 'atomicFacts', 'evidence', 'conflicts', 'limitations', 'provenance'],
  properties: {
    verificationId: { type: 'string' },
    claim: { type: 'string' },
    status: { enum: ['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous'] },
    observedAt: { type: 'string', format: 'date-time' },
    conclusion: { type: 'string' },
    atomicFacts: { type: 'array', items: { type: 'object' } },
    evidence: { type: 'array', items: { type: 'object' } },
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
  evidence: [],
  conflicts: [],
  limitations: [],
  provenance: {},
}

const evmCharge = evm.charge({
  currency: evm.assets.base.USDC,
  recipient,
  x402: { facilitator, routeBinding: 'resource' },
})

const app = new Hono()

app.use('/openapi.json', async (c, next) => {
  await next()
  if (!c.res.ok) return

  const document = await c.res.json() as Record<string, any>
  document.info['x-guidance'] =
    'Use POST /v1/claims/verify to verify one factual claim against current web evidence. Send a JSON body with claim and optional source, freshness, jurisdiction, and language constraints.'
  document.info.contact = { url: 'https://aisoup.net' }

  const operation = document.paths['/v1/claims/verify'].post
  operation.operationId = 'verifyClaim'
  operation.tags = ['Web Evidence']
  operation['x-payment-info'] = {
    ...operation['x-payment-info'],
    price: { mode: 'fixed', currency: 'USD', amount: price },
    protocols: [
      { x402: {} },
      { mpp: { method: 'evm', intent: 'charge', currency: evm.assets.base.USDC.address } },
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

  c.res = c.json(document)
  c.header('Cache-Control', 'public, max-age=300')
})

app.get('/.well-known/x402', (c) => c.json({
  version: 1,
  resources: [`${publicBaseUrl}/v1/claims/verify`],
  ownershipProofs: [recipient],
  instructions: 'POST a JSON claim-verification request. The endpoint returns x402 and MPP payment challenges before execution.',
}))

app.get('/llms.txt', (c) => c.text(`# Web Evidence

Web Evidence verifies factual claims against current web sources and returns structured, cited results.

Base URL: ${publicBaseUrl}
OpenAPI: ${publicBaseUrl}/openapi.json
Service manifest: ${publicBaseUrl}/.well-known/agent-service.json
x402 discovery: ${publicBaseUrl}/.well-known/x402

## Paid operation

POST /v1/claims/verify
Price: $${price} per request
Payment: x402 or MPP using USDC on Base (eip155:8453)${stripeSecretKey ? '; MPP Stripe USD is also accepted' : ''}

Minimum request body:
{"claim":"A factual statement to verify","minimumSources":1}

The initial unauthenticated request returns HTTP 402. Complete one advertised payment challenge and retry with the resulting payment credential.
`))

app.get('/healthz', async (c) => {
  const upstream = await fetch(`${upstreamUrl}/healthz`)
  return c.json({ status: upstream.ok ? 'ok' : 'degraded', upstream: upstream.status }, upstream.ok ? 200 : 503)
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
  const paidVerify = toHonoPayment(payments.compose(
    [evmCharge, paymentOptions()],
    [stripeCharge, paymentOptions()],
  ))
  mountPaidRoute(payments, paidVerify)
} else {
  const payments = Mppx.create({ methods: [evmCharge], secretKey })
  const paidVerify = toHonoPayment(payments.evm.charge(paymentOptions()))
  mountPaidRoute(payments, paidVerify)
}

app.all('*', async (c) => proxyRequest(c.req.raw, new URL(c.req.url).pathname))

serve({ fetch: app.fetch, hostname: '0.0.0.0', port: 8010 })

function paymentOptions() {
  return {
    amount: price,
    description: 'Verify one factual claim against current web evidence',
  }
}

type PaymentHandler = ((request: Request) => Promise<
  | { status: 402; challenge: Response }
  | { status: 200; withReceipt(response?: Response): Response }
>) & { _internal?: unknown }

function toHonoPayment(handler: PaymentHandler): MiddlewareHandler & { _internal?: unknown } {
  const middleware: MiddlewareHandler = async (c, next) => {
    const result = await handler(withPublicUrl(c.req.raw))
    if (result.status === 402) return withBazaarSchema(result.challenge)
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

function withBazaarSchema(response: Response): Response {
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
          body: claimRequestExample,
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
              body: claimRequestSchema,
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

function mountPaidRoute(
  payments: Parameters<typeof discovery>[1],
  paidVerify: MiddlewareHandler,
): void {
  app.post('/v1/claims/verify', paidVerify, async (c) => {
    return proxyRequest(c.req.raw, '/v1/claims/verify')
  })

  discovery(app, payments, {
    path: '/openapi.json',
    info: { title: 'Agentic Services Web Evidence', version: '0.1.0' },
    serviceInfo: {
      description: 'Paid, structured verification of factual claims using current web evidence.',
      name: 'Web Evidence',
      url: publicBaseUrl,
    },
    routes: [
      {
        handler: paidVerify,
        method: 'POST',
        path: '/v1/claims/verify',
        summary: 'Verify one claim and return atomic facts with cited evidence.',
        requestBody: {
          required: true,
          content: {
            'application/json': {
              schema: claimRequestSchema,
            },
          },
        },
      },
    ],
  })
}

function requireEnv(name: string): string {
  const value = process.env[name]
  if (!value) throw new Error(`${name} is required`)
  return value
}

async function proxyRequest(request: Request, path: string): Promise<Response> {
  const url = new URL(request.url)
  const upstream = new URL(path + url.search, upstreamUrl)
  const headers = new Headers(request.headers)
  headers.set('Authorization', `Bearer ${internalApiKey}`)
  headers.set('Host', upstream.host)
  headers.delete('Payment-Signature')
  headers.delete('Payment-Authorization')

  return fetch(upstream, {
    method: request.method,
    headers,
    body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
    duplex: 'half',
  } as RequestInit)
}
