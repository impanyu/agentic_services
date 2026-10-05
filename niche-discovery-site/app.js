const API = 'https://api.aisoup.net/niche-discovery/v1';
const results = document.querySelector('#results');
const detail = document.querySelector('#detail');
let accessToken = '';
const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function loadNiches() {
  results.innerHTML = '<p class="empty">正在读取已复核的需求…</p>';
  const params = new URLSearchParams({q: document.querySelector('#query').value.trim(), sort: document.querySelector('#sort').value});
  try {
    const response = await fetch(`${API}/niches?${params}`);
    if (!response.ok) throw new Error('服务暂时不可用');
    const {niches} = await response.json();
    if (!niches.length) {
      results.innerHTML = '<p class="empty">目前没有符合条件的已复核市场假设。你可以提交第一条线索；我们不会用虚构数据填充榜单。</p>';
      return;
    }
    results.innerHTML = niches.map(n => `<button class="card" type="button" data-id="${escapeHtml(n.id)}"><span class="score">${n.score}<small>/100</small></span><small>${escapeHtml(n.category)}</small><h3>${escapeHtml(n.title)}</h3><p>目标买家：${escapeHtml(n.buyer)} · 编辑评分</p></button>`).join('');
  } catch (_) { results.innerHTML = '<p class="empty">需求数据暂时无法读取，请稍后重试。</p>'; }
}

results.addEventListener('click', async event => {
  const card = event.target.closest('[data-id]');
  if (!card) return;
  const response = await fetch(`${API}/niches/${encodeURIComponent(card.dataset.id)}`, {headers: accessToken ? {Authorization: `Bearer ${accessToken}`} : {}});
  if (response.status === 401) { detail.hidden = false; detail.innerHTML = '<p>完整评估需要有效订阅。请在上方输入邮件收到的凭证。</p>'; detail.scrollIntoView({behavior:'smooth'}); return; }
  if (!response.ok) return;
  const n = await response.json();
  const evidence = n.evidence.map(s => `<li>${s.source_url ? `<a href="${escapeHtml(s.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.source_domain)} ↗</a>` : escapeHtml(s.source_domain)} · ${escapeHtml(s.kind)} · ${escapeHtml(s.observed_at?.slice(0,10))}</li>`).join('');
  detail.innerHTML = `<h3>${escapeHtml(n.title)}</h3><p>${escapeHtml(n.problem)}</p><h4>目标买家</h4><p>${escapeHtml(n.buyer)}</p><h4>可能的服务</h4><p>${escapeHtml(n.solutionHypothesis)}</p><h4>评估依据</h4><p>${escapeHtml(n.evaluationNotes)}</p><p>编辑评分 ${n.score}/100 · ${n.signalCount} 条信号 · ${n.sourceDomainCount} 个来源域 · 近 30 天 ${n.recent30Days} 条 · 趋势：${escapeHtml(n.trend)}</p><h4>证据链接</h4><ul>${evidence}</ul><p>尚无可靠预测时，不提供增长率预测。</p>`;
  detail.hidden = false;
  detail.scrollIntoView({behavior:'smooth',block:'start'});
});

document.querySelector('#search').addEventListener('click', loadNiches);
fetch(`${API}/subscription/status`).then(r => r.ok ? r.json() : null).then(status => {
  if (status?.available) {
    const button = document.querySelector('#subscribe');
    button.disabled = false;
    button.textContent = '使用 Stripe 订阅 ↗';
    document.querySelector('#subscription-message').textContent = '';
  }
}).catch(() => {});
document.querySelector('#access-token').addEventListener('input', e => { accessToken = e.target.value.trim(); document.querySelector('#manage').hidden = !accessToken; });
document.querySelector('#manage').addEventListener('click', async e => {
  e.preventDefault();
  const message = document.querySelector('#subscription-message');
  try {
    const response = await fetch(`${API}/subscription/portal`, {method:'POST',headers:{Authorization:`Bearer ${accessToken}`}});
    if (!response.ok) throw new Error('无法打开客户门户，请核对凭证或联系客服。');
    window.location.assign((await response.json()).portalUrl);
  } catch (error) { message.textContent = error.message; }
});
document.querySelector('#subscribe').addEventListener('click', async () => {
  const message = document.querySelector('#subscription-message');
  message.textContent = '正在打开 Stripe Checkout…';
  try {
    const response = await fetch(`${API}/subscription/checkout`, {method:'POST'});
    if (!response.ok) throw new Error('订阅暂不可用，请稍后再试。');
    const data = await response.json();
    window.location.assign(data.checkoutUrl);
  } catch (error) { message.textContent = error.message; }
});
if (new URLSearchParams(location.search).get('subscription') === 'success') {
  document.querySelector('#subscription-message').textContent = '付款流程已返回。请检查邮箱中的访问凭证；如果尚未收到，请稍后再看。';
}
document.querySelector('#query').addEventListener('keydown', e => { if (e.key === 'Enter') loadNiches(); });
document.querySelector('#submission-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector('#form-message');
  const data = Object.fromEntries(new FormData(form));
  if (!data.source_url) delete data.source_url;
  message.textContent = '提交中…';
  try {
    const response = await fetch(`${API}/submissions`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    if (!response.ok) throw new Error(response.status === 429 ? '提交过于频繁，请稍后再试。' : '提交失败，请检查内容后重试。');
    form.reset();
    message.textContent = '已收到，审核后才可能公开。谢谢你的线索。';
  } catch (error) { message.textContent = error.message; }
});

loadNiches();
