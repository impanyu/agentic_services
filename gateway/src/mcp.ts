import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js'
import { WebStandardStreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js'
import { HTTPFacilitatorClient, x402ResourceServer } from '@x402/core/server'
import { ExactEvmScheme } from '@x402/evm/exact/server'
import { bazaarResourceServerExtension, declareDiscoveryExtension } from '@x402/extensions/bazaar'
import { createPaymentWrapper } from '@x402/mcp'
import { z } from 'zod'

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
  jurisdiction: z.string().max(100).optional(),
  freshnessHours: z.number().int().min(1).max(8760).optional(),
  sourcePolicy: z.enum(['official_only', 'authoritative', 'open_web']).optional(),
  minimumSources: z.number().int().min(1).max(10).optional(),
  maxSources: z.number().int().min(1).max(20).optional(),
  allowedDomains: z.array(z.string()).max(100).optional(),
  blockedDomains: z.array(z.string()).max(100).optional(),
  includeConflicts: z.boolean().optional(),
  language: z.string().min(2).max(35).optional(),
}

const claimInputSchema = {
  type: 'object',
  additionalProperties: false,
  required: ['claim'],
  properties: {
    claim: { type: 'string', minLength: 3, maxLength: 4000 },
    asOf: { type: 'string', format: 'date' },
    jurisdiction: { type: 'string', maxLength: 100 },
    freshnessHours: { type: 'integer', minimum: 1, maximum: 8760 },
    sourcePolicy: { enum: ['official_only', 'authoritative', 'open_web'] },
    minimumSources: { type: 'integer', minimum: 1, maximum: 10 },
    maxSources: { type: 'integer', minimum: 1, maximum: 20 },
    allowedDomains: { type: 'array', items: { type: 'string' }, maxItems: 100 },
    blockedDomains: { type: 'array', items: { type: 'string' }, maxItems: 100 },
    includeConflicts: { type: 'boolean' },
    language: { type: 'string', minLength: 2, maxLength: 35 },
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
      version: '0.3.0',
      websiteUrl: options.publicBaseUrl,
    })

    server.tool(
      'list_verification_tiers',
      'List Web Evidence verification tools, prices, and research limits. This tool is free.',
      {},
      async () => ({
        content: [{
          type: 'text',
          text: JSON.stringify(options.tiers.map((tier) => ({
            tool: toolNameForTier(tier.id),
            priceUsd: tier.price,
            maxSources: tier.maxSources,
            summary: tier.summary,
          }))),
        }],
      }),
    )

    for (const tier of options.tiers) {
      const wrapper = wrappers.get(tier.id)
      if (!wrapper) throw new Error(`Missing payment wrapper for ${tier.id}`)
      const toolName = toolNameForTier(tier.id)
      server.tool(
        toolName,
        `${tier.summary} Costs $${tier.price} USDC on Base per call.`,
        claimArguments,
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

  const response = await fetch(new URL(tier.path, options.upstreamUrl), {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${options.internalApiKey}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
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
  } catch {
    structuredContent = undefined
  }
  return {
    content: [{ type: 'text' as const, text: body }],
    ...(structuredContent ? { structuredContent } : {}),
  }
}
