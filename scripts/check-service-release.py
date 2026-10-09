#!/usr/bin/env python3
"""Check a service release contract locally and, with --live, against the public site."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def request(url: str, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None, accept: str | None = None):
    headers = {"User-Agent": "AgenticServices-ReleaseCheck/1.0"}
    if content_type:
        headers["Content-Type"] = content_type
    if accept:
        headers["Accept"] = accept
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def check_photo_release(release,live):
    registry=json.loads((ROOT/release['mcpRegistry']).read_text());base=release['baseUrl'];errors=[]
    def check(value,label):
        print(('PASS ' if value else 'FAIL ')+label)
        if not value:errors.append(label)
    check(registry['name']=='io.github.impanyu/photo-scout' and registry['version']==release['version'],'Photo Scout registry identity and version')
    check(registry['remotes'][0]['url']==base+'/mcp','Photo Scout MCP URL')
    check(release['agentPriceUsd']=='2.00' and release['humanFreePreview'],'Existing agent price and free human test preserved')
    if live:
        status,_,data=request(base+'/.well-known/mcp/server.json');check(status==200 and json.loads(data)==registry,'Public MCP metadata matches release')
        status,_,data=request(base+'/.well-known/agent-service.json');manifest=json.loads(data)
        check(status==200 and manifest.get('mcpUrl')==base+'/mcp' and manifest['payment']['perCallUsd']==release['agentPriceUsd'],'Public manifest MCP and price')
        status,_,data=request(base+'/openapi.json');doc=json.loads(data)
        check(status==200 and doc['paths'][release['paidHttpPath']]['post']['x-payment-info']['amount']==release['agentPriceUsd'],'Public OpenAPI price')
        accept='application/json, text/event-stream'
        for method,params,label in [('tools/list',{},'MCP tools'),('tools/call',{'name':release['freeTool'],'arguments':{}},'Free pricing tool'),('tools/call',{'name':release['paidTool'],'arguments':{'lat':41.8827,'lon':-87.6233,'radius':500}},'Unpaid MCP challenge')]:
            status,_,data=request(base+'/mcp',method='POST',body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),content_type='application/json',accept=accept)
            check(status==200,label+' response')
            check((release['paidTool'].encode() in data and release['freeTool'].encode() in data) if method=='tools/list' else b'Payment required to access this tool' in data if label=='Unpaid MCP challenge' else release['agentPriceUsd'].encode() in data,label+' contents')
        status,headers,_=request('https://api.aisoup.net'+release['paidHttpPath'],method='POST',body=b'{"lat":41.8827,"lon":-87.6233,"radius":500}',content_type='application/json')
        check(status==402 and bool(headers.get('WWW-Authenticate') or headers.get('Payment-Required')),'Unpaid HTTP payment challenge')
        for name,url in release.get('directories',{}).items():
            status,_,data=request(url)
            check(status==200 and json.loads(data)['server']['name']==registry['name'] and json.loads(data)['server']['remotes']==registry['remotes'],name+' public listing identity and endpoint')
    print(f'Result: {len(errors)} failures. Settled payment and unlisted directories require separate verification.')
    return int(bool(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="Path to services/<id>/release.json")
    parser.add_argument("--live", action="store_true", help="Check public endpoints without making a payment")
    args = parser.parse_args()
    release = json.loads(args.release.read_text())
    if release["serviceId"]=="photo-scout":return check_photo_release(release,args.live)
    manifest = json.loads((ROOT / release["manifest"]).read_text())
    openapi = json.loads((ROOT / release["openapi"]).read_text())
    registry = json.loads((ROOT / release["mcpRegistry"]).read_text())
    errors: list[str] = []

    def check(value: bool, message: str) -> None:
        print(f"{'PASS' if value else 'FAIL'} {message}")
        if not value:
            errors.append(message)

    service_id = release["serviceId"]
    base_url = release["baseUrl"].rstrip("/")
    paid_path = release["paidHttpPath"]
    mcp_url = base_url + "/mcp"
    offers = manifest["offers"]
    transports = {item["id"]: item for item in manifest["transports"]}
    operations = {item["id"]: item for item in manifest["operations"]}
    check(manifest["service"]["id"] == base_url, "manifest service ID")
    check(manifest["service"]["version"] == registry["version"], "manifest and MCP versions")
    check(transports.get("mcp", {}).get("url") == mcp_url, "manifest MCP URL")
    check(registry["remotes"][0]["url"] == mcp_url, "registry MCP URL")
    check(any(item.get("tool") == release["paidTool"] and item["transport"] == "mcp" for item in operations.values()), "MCP tool metadata")
    check(any(item.get("path") == paid_path for item in operations.values()), "paid HTTP operation metadata")
    check(all(item["amount"] == release["agentPriceUsd"] for item in offers), "manifest offer prices")
    check(openapi["paths"][paid_path]["post"]["x-payment-info"]["amount"] == release["agentPriceUsd"], "OpenAPI offer price")
    if service_id == "contractor-check":
        gateway_source = (ROOT / "gateway/src/server.ts").read_text()
        backend_source = (ROOT / "src/agentic_services/contractor_routes.py").read_text()
        price_match = re.search(r"const contractorOperation = \{[^}]*price: '([^']+)'", gateway_source, re.S)
        agent_match = re.search(r"AGENT_PRICE_MICROUSD = ([\d_]+)", backend_source)
        human_match = re.search(r"HUMAN_PRICE_CENTS = ([\d_]+)", backend_source)
        check(bool(price_match and price_match.group(1) == release["agentPriceUsd"]), "gateway price")
        check(bool(agent_match and int(agent_match.group(1).replace("_", "")) == round(float(release["agentPriceUsd"]) * 1_000_000)), "backend agent price")
        check(bool(human_match and int(human_match.group(1).replace("_", "")) == round(float(release["humanPriceUsd"]) * 100)), "backend human price")

    if args.live:
        for path, label in [
            ("/.well-known/agent-service.json", "public manifest"),
            ("/.well-known/mcp/server.json", "public MCP registry metadata"),
            ("/openapi.json", "public OpenAPI"),
        ]:
            status, _, data = request(base_url + path)
            check(status == 200, label + f" HTTP {status}")
            if status == 200:
                try:
                    remote = json.loads(data)
                    expected = manifest if "agent-service" in path else registry if "server.json" in path else openapi
                    check(remote == expected, label + " matches local release")
                except json.JSONDecodeError:
                    check(False, label + " valid JSON")
        mcp_accept = "application/json, text/event-stream"
        status, _, data = request(base_url + "/mcp", method="POST", body=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        }).encode(), content_type="application/json", accept=mcp_accept)
        check(status == 200, f"public MCP tools/list HTTP {status}")
        if status == 200:
            check(release["paidTool"] in data.decode(errors="replace"), "paid MCP tool discoverable")
        status, _, data = request(base_url + "/mcp", method="POST", body=json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": release["paidTool"], "arguments": {"licenseNumber": "1234567"}},
        }).encode(), content_type="application/json", accept=mcp_accept)
        check(status == 200 and b'"Payment required to access this tool"' in data, "paid MCP tool returns x402 requirements without payment")
        status, headers, _ = request("https://api.aisoup.net" + paid_path, method="POST", body=b'{"licenseNumber":"1234567"}', content_type="application/json")
        check(status == 402, f"paid HTTP challenge HTTP {status}")
        check(bool(headers.get("PAYMENT-REQUIRED") or headers.get("WWW-Authenticate") or headers.get("Payment-Required")), "payment challenge header")
        for directory, url in release.get("directories", {}).items():
            if not url:
                continue
            status, _, data = request(url)
            check(status == 200, f"{directory} public listing HTTP {status}")
            if status != 200:
                continue
            if directory == "mcpRegistry":
                try:
                    listed = json.loads(data)["server"]
                    check(listed["name"] == registry["name"] and listed["version"] == registry["version"], "MCP Registry identity and version")
                except (ValueError, KeyError, TypeError):
                    check(False, "MCP Registry metadata shape")
            elif directory == "smithery":
                check(service_id.encode() in data and release["paidTool"].encode() in data, "Smithery service and tool visible")
            elif directory in ("x402Scan", "mppScan"):
                check(paid_path.encode() in data, f"{directory} paid route visible")

    print(f"Result: {len(errors)} failure(s). Unlisted directories and settled payments still require independent evidence.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
