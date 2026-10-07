const API = 'https://api.aisoup.net/niche-discovery/v1';
const results = document.querySelector('#results');
const detail = document.querySelector('#detail');
let accessToken = '';
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function loadNiches() {
  results.innerHTML = '<p class="empty">Loading reviewed opportunities…</p>';
  const params = new URLSearchParams({q: document.querySelector('#query').value.trim(), sort: document.querySelector('#sort').value});
  try {
    const response = await fetch(`${API}/niches?${params}`);
    if (!response.ok) throw new Error('The service is temporarily unavailable');
    const {niches} = await response.json();
    if (!niches.length) {
      results.innerHTML = '<p class="empty">No reviewed opportunities match your search yet. We are collecting and reviewing source evidence before publishing evaluations.</p>';
      return;
    }
    results.innerHTML = niches.map(n => `<button class="card" type="button" data-id="${escapeHtml(n.id)}"><span class="score">${n.score}<small>/100</small></span><small>${escapeHtml(n.category)}</small><h3>${escapeHtml(n.title)}</h3><p>Target buyer: ${escapeHtml(n.buyer)} · editorial score</p></button>`).join('');
  } catch (_) { results.innerHTML = '<p class="empty">We could not load opportunities. Please try again later.</p>'; }
}

results.addEventListener('click', async event => {
  const card = event.target.closest('[data-id]');
  if (!card) return;
  const response = await fetch(`${API}/niches/${encodeURIComponent(card.dataset.id)}`, {headers: accessToken ? {Authorization: `Bearer ${accessToken}`} : {}});
  if (response.status === 401) { detail.hidden = false; detail.innerHTML = '<p>A full evaluation requires an active subscription. Enter the access token from your email above.</p>'; detail.scrollIntoView({behavior:'smooth'}); return; }
  if (!response.ok) return;
  const n = await response.json();
  const evidence = n.evidence.map(s => `<li>${s.source_url ? `<a href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.source_domain)} ↗</a>` : escapeHtml(s.source_domain)} · ${escapeHtml(s.kind)} · ${escapeHtml(s.observed_at?.slice(0,10))}${s.origin === 'gdelt_news' ? ' · <a href="https://www.gdeltproject.org/" target="_blank" rel="noopener noreferrer">Indexed by GDELT Project</a>' : ''}</li>`).join('');
  detail.innerHTML = `<h3>${escapeHtml(n.title)}</h3><p>${escapeHtml(n.problem)}</p><h4>Target buyer</h4><p>${escapeHtml(n.buyer)}</p><h4>Potential service</h4><p>${escapeHtml(n.solutionHypothesis)}</p><h4>Evaluation rationale</h4><p>${escapeHtml(n.evaluationNotes)}</p><p>Editorial score ${n.score}/100 · ${n.signalCount} signals · ${n.sourceDomainCount} source domains · ${n.recent30Days} in the last 30 days · Trend: ${escapeHtml(n.trend)}</p>${n.confidence ? `<h4>Confidence and counterevidence</h4><p>Confidence: ${escapeHtml(n.confidence)} · Revision ${Number(n.revision)}</p><p>${escapeHtml(n.counterevidence)}</p>` : ""}<h4>Evidence links</h4><ul>${evidence}</ul><p>A growth forecast is unavailable without enough reliable history.</p>`;
  detail.hidden = false;
  detail.scrollIntoView({behavior:'smooth',block:'start'});
});

document.querySelector('#search').addEventListener('click', loadNiches);
fetch(`${API}/subscription/status`).then(r => r.ok ? r.json() : null).then(status => {
  if (status?.available) {
    const button = document.querySelector('#subscribe');
    button.disabled = false;
    button.textContent = 'Subscribe with Stripe ↗';
    document.querySelector('#subscription-message').textContent = '';
  }
}).catch(() => {});
document.querySelector('#access-token').addEventListener('input', e => { accessToken = e.target.value.trim(); document.querySelector('#manage').hidden = !accessToken; });
document.querySelector('#manage').addEventListener('click', async e => {
  e.preventDefault();
  const message = document.querySelector('#subscription-message');
  try {
    const response = await fetch(`${API}/subscription/portal`, {method:'POST',headers:{Authorization:`Bearer ${accessToken}`}});
    if (!response.ok) throw new Error('Could not open the customer portal. Check your token or contact us.');
    window.location.assign((await response.json()).portalUrl);
  } catch (error) { message.textContent = error.message; }
});
document.querySelector('#subscribe').addEventListener('click', async () => {
  const message = document.querySelector('#subscription-message');
  message.textContent = 'Opening Stripe Checkout…';
  try {
    const response = await fetch(`${API}/subscription/checkout`, {method:'POST'});
    if (!response.ok) throw new Error('Subscriptions are not available yet. Please try again later.');
    const data = await response.json();
    window.location.assign(data.checkoutUrl);
  } catch (error) { message.textContent = error.message; }
});
if (new URLSearchParams(location.search).get('subscription') === 'success') {
  document.querySelector('#subscription-message').textContent = 'Checkout has returned. Check your email for an access token; delivery may take a moment.';
}
document.querySelector('#query').addEventListener('keydown', e => { if (e.key === 'Enter') loadNiches(); });

loadNiches();
