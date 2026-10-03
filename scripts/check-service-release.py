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


def request(url: str, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None):
    headers = {"User-Agent": "AgenticServices-ReleaseCheck/1.0"}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method=method), timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="Path to services/<id>/release.json")
    parser.add_argument("--live", action="store_true", help="Check public endpoints without making a payment")
    args = parser.parse_args()
    release = json.loads(args.release.read_text())
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
        status, _, data = request(base_url + "/mcp", method="POST", body=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        }).encode(), content_type="application/json")
        check(status == 200, f"public MCP tools/list HTTP {status}")
        if status == 200:
            check(release["paidTool"] in data.decode(errors="replace"), "paid MCP tool discoverable")
        status, headers, _ = request("https://api.aisoup.net" + paid_path, method="POST", body=b'{"licenseNumber":"1234567"}', content_type="application/json")
        check(status == 402, f"paid HTTP challenge HTTP {status}")
        check(bool(headers.get("PAYMENT-REQUIRED") or headers.get("WWW-Authenticate") or headers.get("Payment-Required")), "payment challenge header")

    print(f"Result: {len(errors)} failure(s). Directory listings and settled payments require independent evidence.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
