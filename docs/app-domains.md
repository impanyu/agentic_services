# App subdomains and compatible API migration

Released October 11, 2026. The company catalog now links to:

| App | Website | API |
| --- | --- | --- |
| Photo Scout | https://photo-scout.aisoup.net/ | https://photo-scout.aisoup.net/api |
| Web Evidence | https://web-evidence.aisoup.net/ | https://web-evidence.aisoup.net/api |
| Contractor Check | https://contractor-check.aisoup.net/ | https://contractor-check.aisoup.net/api |
| Niche Discovery | https://niche-discovery.aisoup.net/ | https://niche-discovery.aisoup.net/api |
| Agentic Wiki | https://wiki.aisoup.net/ | https://wiki.aisoup.net/api |

## Compatibility

Old `aisoup.net/<service>/` pages and `api.aisoup.net` API paths remain operational. Requests to old human endpoints retain their old checkout/portal return URLs. Existing Stripe callback destinations, signed image/report URLs, opaque MCP payment resources, prices, and model settings remain unchanged. Wiki was verified through its existing `/api/health` and was not modified.

`deploy/app-subdomains.caddy` maps `/api/*` to existing gateway routes. Documentation and HTTP payment resources advertise the requesting App's canonical URL. Caddy rewrites service-scoped Cookie paths to `/api/`, preserving the remainder of the path. The regex uses literal service names because snippet arguments inside quoted regex strings were not expanded by the installed Caddy adapter. JSON discovery response replacement deletes Content-Length after assigning Hono's response, which otherwise merges the old header.

Photo Scout's Google OAuth client has both callback URLs registered; login and token exchange use the same callback selected by the trusted forwarded host. The Maps browser key keeps its previous API target and adds the new website to its allowed referrers.

## Photo Scout identities and history

The old cookie owner is exported through a short-lived, single-use ticket. Import requires the canonical Origin and a browser-bound HttpOnly nonce. Old cookie values are not copied to the new host. Guest aliases resolve to the same backend owner; existing new-host guest records merge without discarding either history. Signed-in users keep the same account ID and permanent retention. A currently signed-in destination account cannot be replaced by another legacy account.

Only sessions linked across these aliases share a sign-out family; signing out does not revoke unrelated devices or delete account data. Same-user OAuth reauthentication retains this family. Explicit new-host sign-out prevents automatic legacy sign-in on the next visit.

A hidden iframe transfers Photo Scout browser preferences from the legacy website, with exact origin/source/nonce checks. This does not block initial map rendering. Identity-dependent requests wait for the bounded migration attempt; failed transfers retry on the next visit. Source records remain available on the old entry point.

## Live validation

- All four new websites, `/api/healthz`, OpenAPI documents, and agent-service documents return 200 over valid HTTPS. Wiki `/api/health` returns 200.
- Old website paths still return 200. Photo Scout, Web Evidence, and Contractor Check return payment-required 402 at old and new API routes without charging anything. Their new MCP endpoints accept initialize requests.
- Niche Discovery's pay-per-call search returns 503 at both old and new routes: this existing availability/configuration issue is not fixed by URL migration.
- `/api/stripe/webhook` reaches the signature-checking handler and returns 400 for unsigned probes, rather than 404. This does not prove actual payment delivery.
- Anonymous migration returns 200 with `/api/` guest Cookie scope. The OAuth login redirect advertises the new callback.
- A real Chrome visit imported the existing signed-in account without another login, showing 77 searches and 25 photos. A saved photo loaded at 1024 px and a recovered search's Street View preview loaded at 512 px. No production user records were deleted during validation.
- Backend regression tests: 427 passed. Frontend/domain tests: 156 passed. Gateway TypeScript compilation passed.

## Deployment and rollback

The VM checkout contains independent production changes: do not reset or pull over it. Release files were staged with SHA guards, then the backend and gateway images were built. Both search and photo queues were checked idle before restarting. Static helper files were installed before HTML entry points. The actual Caddy file retained its existing VM-only services and imports the new domain file.

Private file/database backups remain on the VM under `/tmp/aisoup-app-domains-backup-1791762978` and `/tmp/aisoup-app-domains-backup-1791763436`; keep these private. The original Caddy file is `/etc/caddy/Caddyfile.before-app-domains`. Original running image tags were saved as `aisoup-web-evidence:domains-backup-1791762978` and `aisoup-payment-gateway:domains-backup-1791762978`.

Rollback must preserve new user data: restore affected source/configuration files and switch image tags only when queues are idle. Do not replace the live database with its old snapshot merely to roll back code. The original entry points remain available.
