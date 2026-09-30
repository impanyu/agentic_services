from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import urllib.request
from urllib.parse import urlparse
from html import escape
from typing import Any

from .config import Settings
from .contact import send_email


def status_page_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dream Workshop Status</title><meta name="description" content="Live status for Dream Workshop agent services.">
<style>:root{--ink:#111411;--paper:#f1efe8;--green:#58b36b;--amber:#e0a526;--red:#df5a47;--line:rgba(17,20,17,.18);--mono:ui-monospace,SFMono-Regular,Menlo,monospace}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 Inter,system-ui,sans-serif}a{color:inherit}.wrap{max-width:980px;margin:auto;padding:42px 24px 100px}header{display:flex;justify-content:space-between;align-items:center;padding-bottom:38px;border-bottom:1px solid var(--line)}header a{text-decoration:none;font-weight:700}.badge{font:11px var(--mono);text-transform:uppercase}.hero{padding:72px 0}.hero h1{font:500 clamp(54px,8vw,96px)/.9 Georgia,serif;letter-spacing:-.055em;margin:0 0 26px}.state{display:flex;gap:12px;align-items:center;font:12px var(--mono);text-transform:uppercase}.dot{width:10px;height:10px;border-radius:50%;background:var(--green)}.degraded .dot{background:var(--amber)}.downtime .dot{background:var(--red)}h2{font-size:13px;font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;margin:0 0 20px}.components{border-top:1px solid}.component{display:grid;grid-template-columns:1fr auto;gap:20px;padding:22px 0;border-bottom:1px solid}.component b{display:block}.component small{color:#6d726d}.component-status{font:11px var(--mono);text-transform:uppercase}.incidents,.subscribe{margin-top:72px}.incident{border:1px solid;padding:22px;margin-top:12px}.incident p{margin-bottom:0}.subscribe{display:grid;grid-template-columns:.8fr 1.2fr;gap:60px;border-top:1px solid;padding-top:55px}.subscribe h3{font:500 38px/1 Georgia,serif;margin:0 0 18px}.subscribe form{display:grid;gap:12px}.subscribe input,.subscribe select,.subscribe button{border:1px solid;background:transparent;padding:14px;font:14px inherit}.subscribe button{background:var(--ink);color:var(--paper);cursor:pointer}.feeds{display:flex;gap:18px;margin-top:30px;font:11px var(--mono);text-transform:uppercase}@media(max-width:700px){.subscribe{grid-template-columns:1fr}}</style>
</head><body><div class="wrap"><header><a href="https://aisoup.net">Dream Workshop</a><span class="badge">Service status</span></header><main><section class="hero"><h1 id="headline">Checking systems…</h1><div class="state" id="state"><i class="dot"></i><span>Loading current status</span></div></section><section><h2>Components</h2><div class="components" id="components"></div></section><section class="incidents"><h2>Recent incidents</h2><div id="incidents"><p>No incidents reported.</p></div></section><section class="subscribe"><div><h3>Get status updates.</h3><p>Subscribe by email, or register an HTTPS webhook for an agent or monitoring system.</p><div class="feeds"><a href="feed.rss">RSS ↗</a><a href="index.json">JSON ↗</a></div></div><form id="subscribe"><select name="channel"><option value="email">Email</option><option value="webhook">Webhook</option></select><input name="target" type="text" placeholder="Email or HTTPS webhook URL" required><input name="verificationEmail" type="email" placeholder="Verification email" required><button>Subscribe</button><output id="result"></output></form></section></main></div>
<script>const q=s=>document.querySelector(s),esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));fetch('index.json').then(r=>r.json()).then(d=>{const s=d.page.aggregateStatus;q('#headline').textContent=s==='operational'?'All systems operational.':s==='degraded'?'Some systems are degraded.':'Service disruption in progress.';q('#state').className='state '+s;q('#state span').textContent=s;q('#components').innerHTML=d.components.map(c=>`<div class="component"><div><b>${esc(c.name)}</b><small>${esc(c.description)}</small></div><span class="component-status">${esc(c.status)}</span></div>`).join('');q('#incidents').innerHTML=d.incidents.length?d.incidents.map(i=>`<article class="incident"><b>${esc(i.title)}</b><p>${esc(i.message)}</p><small>${esc(i.status)} · ${new Date(i.updatedAt).toLocaleString()}</small></article>`).join(''):'<p>No incidents reported.</p>'});q('#subscribe').addEventListener('submit',async e=>{e.preventDefault();const f=new FormData(e.target),body=Object.fromEntries(f);const r=await fetch('v1/subscriptions',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const d=await r.json();q('#result').textContent=r.ok?'Check your email to confirm the subscription.':(d.detail||'Subscription failed.');});</script></body></html>"""


def status_rss(document: dict[str, Any]) -> str:
    items = "".join(
        f"<item><guid>{escape(item['incidentId'])}</guid><title>{escape(item['title'])}</title>"
        f"<description>{escape(item['message'])}</description><pubDate>{escape(item['updatedAt'])}</pubDate>"
        f"<link>https://status.aisoup.net</link></item>"
        for item in document["incidents"]
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Dream Workshop Status</title>'
        '<link>https://status.aisoup.net</link><description>Agent service incidents and maintenance.</description>'
        f"{items}</channel></rss>"
    )


def send_subscription_verification(
    settings: Settings, *, verification_email: str, channel: str, token: str
) -> None:
    url = f"https://api.aisoup.net/status/v1/subscriptions/verify?token={token}"
    send_email(
        settings,
        recipient=verification_email,
        subject="Confirm Dream Workshop status updates",
        body=f"Confirm your {channel} status subscription:\n\n{url}\n\nIgnore this message if you did not request it.",
    )


def notify_status_subscribers(
    settings: Settings, *, subscribers: list[dict[str, Any]], incident: dict[str, Any]
) -> list[str]:
    failures: list[str] = []
    subject = f"[Dream Workshop Status] {incident['title']} — {incident['status']}"
    body = f"{incident['message']}\n\nStatus: {incident['status']}\nSeverity: {incident['severity']}\nhttps://status.aisoup.net"
    payload = json.dumps({"eventType": "incident", "incident": incident}, separators=(",", ":")).encode()
    for subscriber in subscribers:
        try:
            if subscriber["channel"] == "email":
                send_email(settings, recipient=subscriber["target"], subject=subject, body=body)
            else:
                if not _public_webhook_target(subscriber["target"]):
                    raise RuntimeError("Webhook target no longer resolves to public addresses")
                signature = hmac.new(subscriber["signing_secret"].encode(), payload, hashlib.sha256).hexdigest()
                request = urllib.request.Request(
                    subscriber["target"], data=payload, method="POST",
                    headers={"Content-Type": "application/json", "X-Dream-Workshop-Signature": f"sha256={signature}"},
                )
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=10) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"Webhook returned {response.status}")
        except Exception:
            failures.append(subscriber["subscriber_id"])
    return failures


def _public_webhook_target(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror:
        return False
    return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
