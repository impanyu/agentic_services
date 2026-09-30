from __future__ import annotations


def admin_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Web Evidence · Commerce</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2c;--line:#25304a;--text:#edf3ff;--muted:#9eacc6;--accent:#6ee7b7;--bad:#fb7185}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 5%,#163354 0,transparent 28%),var(--bg);font:15px/1.45 ui-sans-serif,system-ui;color:var(--text)}
main{max-width:1240px;margin:auto;padding:32px 22px 70px}header{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:25px}h1{font-size:30px;margin:0}h2{font-size:17px;margin:0 0 15px}.muted{color:var(--muted)}
.auth{display:flex;gap:8px}input,button,select{border:1px solid var(--line);border-radius:9px;background:#0e1628;color:var(--text);padding:10px 12px}input{min-width:260px}button{cursor:pointer;background:#1f6f5b;border-color:#2b9b7f;font-weight:700}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.card,.panel{background:color-mix(in srgb,var(--panel) 94%,transparent);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 18px 45px #0003}.card .value{font-size:27px;font-weight:750;margin-top:8px}.positive{color:var(--accent)}.negative{color:var(--bad)}.panel{margin-top:16px;overflow:auto}.toolbar{display:flex;justify-content:space-between;gap:10px;align-items:center}
table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:11px 10px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em}.pill{padding:4px 8px;border-radius:999px;background:#203047;font-size:12px}.error{color:var(--bad);min-height:22px;margin:9px 0}
@media(max-width:850px){header{align-items:stretch;flex-direction:column}.auth{flex-wrap:wrap}.auth input{flex:1;min-width:180px}.grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:470px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<header><div><div class="muted">Agentic Services</div><h1>Web Evidence commerce</h1><div class="muted">Revenue, provider cost, gross profit, and individual paid calls</div></div><div class="auth"><input id="key" type="password" placeholder="Admin API key"><select id="days"><option>7</option><option selected>30</option><option>90</option><option>365</option></select><button id="load">Load</button></div></header>
<div id="error" class="error"></div><section class="grid">
<div class="card"><div class="muted">Revenue</div><div id="revenue" class="value">—</div></div>
<div class="card"><div class="muted">OpenAI cost</div><div id="cost" class="value">—</div></div>
<div class="card"><div class="muted">Gross profit</div><div id="profit" class="value positive">—</div></div>
<div class="card"><div class="muted">Gross margin</div><div id="margin" class="value">—</div></div>
<div class="card"><div class="muted">Orders</div><div id="orders" class="value">—</div></div>
<div class="card"><div class="muted">Completed / failed</div><div id="completion" class="value">—</div></div>
<div class="card"><div class="muted">Web searches</div><div id="searches" class="value">—</div></div>
<div class="card"><div class="muted">Input / output tokens</div><div id="tokens" class="value">—</div></div>
</section>
<section class="panel"><h2>Performance by tier</h2><table><thead><tr><th>Tier</th><th>Orders</th><th>Revenue</th><th>Cost</th><th>Gross profit</th></tr></thead><tbody id="tiers"></tbody></table></section>
<section class="panel"><div class="toolbar"><h2>Recent orders</h2><span class="muted">Latest 100</span></div><table><thead><tr><th>Created</th><th>Order</th><th>Tier</th><th>Status</th><th>Payment</th><th>Revenue</th><th>Cost</th><th>Profit</th><th>Searches</th><th>Tokens in / out</th></tr></thead><tbody id="orderRows"></tbody></table></section>
</main><script>
const $=id=>document.getElementById(id), money=n=>'$'+((n||0)/1e6).toFixed(4), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const saved=sessionStorage.getItem('adminKey');if(saved)$('key').value=saved;
async function load(){const key=$('key').value.trim();if(!key)return $('error').textContent='Enter the admin API key.';sessionStorage.setItem('adminKey',key);$('error').textContent='';
try{const headers={'X-Admin-Key':key};const [s,o]=await Promise.all([fetch('/v1/admin/summary?days='+$('days').value,{headers}),fetch('/v1/admin/orders?limit=100',{headers})]);if(!s.ok)throw new Error((await s.json()).detail||'Unable to load dashboard');const summary=await s.json(),orders=(await o.json()).orders;
$('revenue').textContent=money(summary.revenueMicrousd);$('cost').textContent=money(summary.costMicrousd);$('profit').textContent=money(summary.grossProfitMicrousd);$('profit').className='value '+(summary.grossProfitMicrousd>=0?'positive':'negative');$('margin').textContent=summary.grossMargin==null?'—':(summary.grossMargin*100).toFixed(1)+'%';$('orders').textContent=summary.orderCount.toLocaleString();$('completion').textContent=summary.completedCount+' / '+summary.failedCount;$('searches').textContent=summary.webSearchCalls.toLocaleString();$('tokens').textContent=summary.inputTokens.toLocaleString()+' / '+summary.outputTokens.toLocaleString();
$('tiers').innerHTML=summary.byTier.map(r=>`<tr><td>${esc(r.tier)}</td><td>${r.orderCount}</td><td>${money(r.revenueMicrousd)}</td><td>${money(r.costMicrousd)}</td><td>${money(r.grossProfitMicrousd)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">No paid orders in this period.</td></tr>';
$('orderRows').innerHTML=orders.map(r=>`<tr><td>${esc(new Date(r.createdAt).toLocaleString())}</td><td title="${esc(r.orderId)}">${esc(r.orderId.slice(0,15))}…</td><td>${esc(r.tier)}</td><td><span class="pill">${esc(r.status)}</span></td><td>${esc(r.paymentProtocol)}</td><td>${money(r.amountMicrousd)}</td><td>${money(r.totalCostMicrousd)}</td><td>${money(r.grossProfitMicrousd)}</td><td>${r.webSearchCalls}</td><td>${r.inputTokens} / ${r.outputTokens}</td></tr>`).join('')||'<tr><td colspan="10" class="muted">No orders yet.</td></tr>';
}catch(e){$('error').textContent=e.message}}
$('load').onclick=load;$('days').onchange=load;if(saved)load();
</script></body></html>"""
