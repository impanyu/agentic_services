import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js'
import { HTTPFacilitatorClient, x402ResourceServer } from '@x402/core/server'
import { ExactEvmScheme } from '@x402/evm/exact/server'
import { bazaarResourceServerExtension, declareDiscoveryExtension } from '@x402/extensions/bazaar'
import { createPaymentWrapper } from '@x402/mcp'
import { z } from 'zod'
import { createHash, randomBytes, randomUUID } from 'node:crypto'

export interface McpTier {
  id: string
  path: string
  price: string
  maxSources: number
  summary: string
}

export interface McpHandlerOptions {
  facilitator: string
  recipient: `0x${string}`
  upstreamUrl: string
  internalApiKey: string
  publicBaseUrl: string
  tiers: readonly McpTier[]
}

const claimArguments = {
  claim: z.string().min(3).max(4000).describe('The factual claim to verify.'),
  asOf: z.string().optional().describe('Optional YYYY-MM-DD date at which the claim should be evaluated.'),
  jurisdiction: z.string().max(100).optional().describe('Optional country, state, or legal jurisdiction that should frame the verification.'),
  freshnessHours: z.number().int().min(1).max(8760).optional().describe('Maximum preferred source age in hours. Defaults to 168 hours.'),
  sourcePolicy: z.enum(['official_only', 'authoritative', 'open_web']).optional().describe('Source selection policy. Defaults to authoritative; official_only requires allowedDomains.'),
  minimumSources: z.number().int().min(1).max(10).optional().describe('Minimum number of sources requested. Defaults to 2 and cannot exceed maxSources.'),
  maxSources: z.number().int().min(1).max(20).optional().describe('Maximum number of evidence sources to return, capped by the selected verification tier.'),
  allowedDomains: z.array(z.string()).max(100).optional().describe('Optional hostname allowlist without schemes or paths, for example openai.com.'),
  blockedDomains: z.array(z.string()).max(100).optional().describe('Optional hostname blocklist without schemes or paths.'),
  includeConflicts: z.boolean().optional().describe('Whether to search for and report conflicting evidence. Defaults to true.'),
  language: z.string().min(2).max(35).optional().describe('Preferred response language or auto for automatic selection. Defaults to auto.'),
}

const tierListOutputSchema = z.object({
  tiers: z.array(z.object({
    tool: z.string().describe('MCP tool name for this verification tier.'),
    priceUsd: z.string().describe('Per-call price in US dollars.'),
    maxSources: z.number().int().describe('Maximum evidence sources available in this tier.'),
    summary: z.string().describe('Human-readable tier capabilities.'),
  })),
})

const verificationOutputSchema = z.object({
  verificationId: z.string(),
  claim: z.string(),
  status: z.enum(['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous']),
  observedAt: z.string().datetime(),
  conclusion: z.string(),
  atomicFacts: z.array(z.object({
    statement: z.string(),
    status: z.enum(['confirmed', 'partially_confirmed', 'contradicted', 'insufficient_evidence', 'ambiguous']),
    confidence: z.number().min(0).max(1),
    explanation: z.string(),
    evidenceIds: z.array(z.string()),
  })),
  providerSources: z.array(z.object({
    sourceId: z.string(),
    url: z.string().url(),
    title: z.string().nullable(),
    provider: z.string(),
    searchCallIds: z.array(z.string()),
    actions: z.array(z.string()),
    queries: z.array(z.string()),
    evidenceIds: z.array(z.string()),
    cited: z.boolean(),
    snapshotId: z.string().nullable(),
  })),
  evidence: z.array(z.object({
    id: z.string(),
    url: z.string().url(),
    title: z.string(),
    publisher: z.string(),
    publishedAt: z.string().nullable(),
    retrievedAt: z.string().datetime(),
    excerpt: z.string(),
    relationship: z.enum(['supports', 'contradicts', 'context']),
    sourceType: z.string(),
    qualityReason: z.string(),
    providerSourceMatched: z.boolean(),
    cited: z.boolean(),
    snapshotId: z.string().nullable(),
    consulted: z.boolean(),
    snapshotted: z.boolean(),
  })),
  snapshots: z.array(z.object({
    snapshotId: z.string(),
    requestedUrl: z.string().url(),
    finalUrl: z.string().url().nullable(),
    retrievedAt: z.string().datetime(),
    status: z.enum(['captured', 'failed', 'blocked', 'too_large', 'unsupported']),
    httpStatus: z.number().int().nullable(),
    contentType: z.string().nullable(),
    contentLength: z.number().int().nullable(),
    rawSha256: z.string().nullable(),
    normalizedSha256: z.string().nullable(),
    failureReason: z.string().nullable(),
  })),
  conflicts: z.array(z.object({
    summary: z.string(),
    evidenceIds: z.array(z.string()),
  })),
  limitations: z.array(z.string()),
  provenance: z.object({
    provider: z.string(),
    model: z.string(),
    providerResponseId: z.string(),
    searchedWeb: z.boolean(),
    consultedSourceCount: z.number().int().min(0),
    citedSourceCount: z.number().int().min(0),
    providerSourceCount: z.number().int().min(0),
    matchedEvidenceCount: z.number().int().min(0),
    snapshottedSourceCount: z.number().int().min(0),
    webSearchCallCount: z.number().int().min(0),
    inputTokens: z.number().int().min(0),
    cachedInputTokens: z.number().int().min(0),
    outputTokens: z.number().int().min(0),
    cacheHit: z.boolean(),
  }),
  commerce: z.object({
    orderId: z.string(),
    orderToken: z.string(),
    receiptId: z.string().nullable(),
    orderUrl: z.string().url(),
  }),
})

const claimInputSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['claim'],
  properties: {
    claim: { type: 'string', minLength: 3, maxLength: 4000, description: 'The factual claim to verify.' },
    asOf: { type: 'string', format: 'date', description: 'Optional date at which the claim should be evaluated.' },
    jurisdiction: { type: 'string', maxLength: 100, description: 'Optional country, state, or legal jurisdiction.' },
    freshnessHours: { type: 'integer', minimum: 1, maximum: 8760, description: 'Maximum preferred source age in hours.' },
    sourcePolicy: { enum: ['official_only', 'authoritative', 'open_web'], description: 'Policy controlling which kinds of web sources may be used.' },
    minimumSources: { type: 'integer', minimum: 1, maximum: 10, description: 'Minimum number of evidence sources requested.' },
    maxSources: { type: 'integer', minimum: 1, maximum: 20, description: 'Maximum number of evidence sources to return.' },
    allowedDomains: { type: 'array', items: { type: 'string' }, maxItems: 100, description: 'Optional hostname allowlist without schemes or paths.' },
    blockedDomains: { type: 'array', items: { type: 'string' }, maxItems: 100, description: 'Optional hostname blocklist without schemes or paths.' },
    includeConflicts: { type: 'boolean', description: 'Whether to search for and report conflicting evidence.' },
    language: { type: 'string', minLength: 2, maxLength: 35, description: 'Preferred response language or auto.' },
  },
}

export async function createMcpHandler(options: McpHandlerOptions): Promise<(request: Request) => Promise<Response>> {
  const publicHost = new URL(options.publicBaseUrl).host
  const facilitatorClient = new HTTPFacilitatorClient({ url: options.facilitator })
  const resourceServer = new x402ResourceServer(facilitatorClient)
  resourceServer.register('eip155:8453', new ExactEvmScheme())
  resourceServer.registerExtension(bazaarResourceServerExtension)
  await resourceServer.initialize()

  const wrappers = new Map<string, ReturnType<typeof createPaymentWrapper>>()
  for (const tier of options.tiers) {
    const toolName = toolNameForTier(tier.id)
    const accepts = await resourceServer.buildPaymentRequirements({
      scheme: 'exact',
      network: 'eip155:8453',
      payTo: options.recipient,
      price: `$${tier.price}`,
    })
    wrappers.set(tier.id, createPaymentWrapper(resourceServer, {
      accepts,
      resource: {
        url: `mcp://${publicHost}/${toolName}`,
        description: tier.summary,
        mimeType: 'application/json',
        serviceName: 'Web Evidence',
        tags: ['web', 'evidence', 'fact-checking', 'research'],
      },
      extensions: declareDiscoveryExtension({
        toolName,
        description: `${tier.summary} Price: $${tier.price} USDC on Base per call.`,
        transport: 'streamable-http',
        inputSchema: claimInputSchema,
        example: {
          claim: 'The Base mainnet chain ID is 8453.',
          sourcePolicy: 'authoritative',
          minimumSources: 2,
          maxSources: Math.min(4, tier.maxSources),
        },
      }),
    }))
  }

  return async (request: Request): Promise<Response> => {
    const server = new McpServer({
      name: 'web-evidence',
      title: 'Web Evidence by Agentic Services',
      version: '0.3.1',
      description: 'Paid claim verification with cited web evidence, complete provider-source provenance, reproducible snapshots, and content hashes.',
      websiteUrl: `${options.publicBaseUrl}/web-evidence/`,
      icons: [{ src: `${options.publicBaseUrl}/favicon.ico`, mimeType: 'image/svg+xml' }],
    })

    server.registerTool(
      'list_verification_tiers',
      {
        title: 'List verification tiers',
        description: 'List Web Evidence verification tools, prices, and research limits. This tool is free.',
        outputSchema: tierListOutputSchema,
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async () => {
        const tiers = options.tiers.map((tier) => ({
            tool: toolNameForTier(tier.id),
            priceUsd: tier.price,
            maxSources: tier.maxSources,
            summary: tier.summary,
        }))
        return {
          content: [{ type: 'text', text: JSON.stringify(tiers) }],
          structuredContent: { tiers },
        }
      },
    )

    for (const tier of options.tiers) {
      const wrapper = wrappers.get(tier.id)
      if (!wrapper) throw new Error(`Missing payment wrapper for ${tier.id}`)
      const toolName = toolNameForTier(tier.id)
      server.registerTool(
        toolName,
        {
          title: tier.id === 'standard' ? 'Verify claim' : `Verify claim (${tier.id})`,
          description: `${tier.summary} Costs $${tier.price} USDC on Base per call.`,
          inputSchema: claimArguments,
          outputSchema: verificationOutputSchema,
          annotations: {
            readOnlyHint: false,
            destructiveHint: false,
            idempotentHint: false,
            openWorldHint: true,
          },
        },
        wrapper(async (args) => callVerification(options, tier, args)),
      )
    }

    const transport = new WebStandardStreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    })
    await server.connect(transport)
    return transport.handleRequest(request)
  }
}

function toolNameForTier(id: string): string {
  return id === 'standard' ? 'verify_claim' : `verify_claim_${id}`
}

async function callVerification(
  options: McpHandlerOptions,
  tier: McpTier,
  args: Record<string, unknown>,
) {
  const requestBody = { ...args }
  if (typeof requestBody.maxSources === 'number') {
    requestBody.maxSources = Math.min(requestBody.maxSources, tier.maxSources)
  }

  const orderId = `ord_${randomUUID().replaceAll('-', '')}`
  const orderToken = `ort_${randomBytes(32).toString('base64url')}`
  const response = await fetch(new URL(tier.path, options.upstreamUrl), {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${options.internalApiKey}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
      'X-Agentic-Order-Id': orderId,
      'X-Agentic-Order-Token-Hash': createHash('sha256').update(orderToken).digest('hex'),
      'X-Agentic-Payment-Protocol': 'x402-mcp',
      'X-Agentic-Order-Tier': tier.id,
    },
    body: JSON.stringify(requestBody),
  })
  const body = await response.text()

  if (!response.ok) {
    return {
      isError: true,
      content: [{ type: 'text' as const, text: `Verification failed (${response.status}): ${body}` }],
    }
  }

  let structuredContent: Record<string, unknown> | undefined
  try {
    structuredContent = JSON.parse(body) as Record<string, unknown>
    structuredContent.commerce = {
      orderId,
      orderToken,
      receiptId: response.headers.get('X-Agentic-Receipt-Id'),
      orderUrl: `${options.publicBaseUrl}/v1/orders/${orderId}`,
    }
  } catch {
    structuredContent = undefined
  }
  return {
    content: [{ type: 'text' as const, text: body }],
    ...(structuredContent ? { structuredContent } : {}),
  }
}
