/** Canonical App URLs; legacy clients retain their existing behavior. */
export const appHosts: Record<string,string> = {
  'photo-scout.aisoup.net':'photo-scout', 'web-evidence.aisoup.net':'web-evidence',
  'niche-discovery.aisoup.net':'niche-discovery', 'contractor-check.aisoup.net':'contractor-check',
}
export const browserOrigins = new Set(['https://aisoup.net','https://www.aisoup.net',
  ...Object.keys(appHosts).map(host=>'https://'+host)])
export function allowedBrowserOrigin(origin: string,service?: string): boolean {
  if(origin==='https://aisoup.net'||origin==='https://www.aisoup.net')return true
  return service?origin==='https://'+service+'.aisoup.net':browserOrigins.has(origin)
}
export function forwardedApp(host: string | null | undefined): string | undefined {
  return appHosts[(host??'').toLowerCase()]
}
export function publicRequestURL(raw: string, host: string | null, legacy: string): URL {
  const incoming=new URL(raw), service=forwardedApp(host), prefix='/'+service
  if(service && (incoming.pathname===prefix || incoming.pathname.startsWith(prefix+'/')))
    return new URL('/api'+incoming.pathname.slice(prefix.length)+incoming.search,'https://'+host)
  if(service==='web-evidence')return new URL('/api'+incoming.pathname+incoming.search,'https://'+host)
  return new URL(incoming.pathname+incoming.search,legacy)
}
export function rewriteDiscovery(value: unknown,service: string): unknown {
  if(typeof value==='string'){const rewritten=value
    .replaceAll('https://api.aisoup.net/'+service,'https://'+service+'.aisoup.net/api')
    .replaceAll('https://aisoup.net/'+service+'/','https://'+service+'.aisoup.net/')
    .replaceAll('POST /'+service+'/','POST /')
    return rewritten.startsWith('/'+service+'/')?rewritten.slice(service.length+1):rewritten
  }
  if(Array.isArray(value))return value.map(item=>rewriteDiscovery(item,service))
  if(value && typeof value==='object')return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,rewriteDiscovery(item,service)]))
  return value
}
export function canonicalDiscovery(document: unknown,service: string): unknown {
  const result=rewriteDiscovery(document,service) as Record<string,unknown>
  if(result.paths && typeof result.paths==='object'){
    const prefix='/'+service
    result.paths=Object.fromEntries(Object.entries(result.paths).filter(([path])=>path.startsWith(prefix+'/') || (path.startsWith('/v1/') && !path.startsWith('/v1/services/'))).map(([path,value])=>[path.startsWith(prefix+'/')?path.slice(prefix.length):path,value]))
    result.servers=[{url:'https://'+service+'.aisoup.net/api'}]
  }
  return result
}
