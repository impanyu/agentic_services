const status = document.getElementById('report-status');
const target = document.getElementById('report');
const sessionId = new URLSearchParams(location.search).get('session_id');
function add(tag, value, parent = target, className = '') {
  const node = document.createElement(tag);
  node.textContent = value ?? '';
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}
async function load() {
  if (!sessionId) { status.textContent = 'No Checkout session was provided.'; return; }
  try {
    const response = await fetch(`https://api.aisoup.net/web-evidence/v1/report?session_id=${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Report unavailable.');
    const report = data.report;
    status.textContent = `Payment confirmed · Order ${data.orderId}`;
    add('h2', report.claim);
    add('p', `Assessment: ${report.status.replaceAll('_', ' ')} · ${report.observedAt}`);
    add('p', report.conclusion);
    add('h2', 'Sources');
    for (const source of report.evidence || []) {
      const card = add('article', '');
      add('h3', source.title || source.url, card);
      add('p', source.excerpt, card);
      const link = add('a', 'Open original source ↗', card);
      if (source.url?.startsWith('https://') || source.url?.startsWith('http://')) { link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      add('p', `Relationship: ${source.relationship} · Cited: ${source.cited ? 'yes' : 'no'} · Snapshot: ${source.snapshotted ? 'saved' : 'unavailable'}`, card, 'report-meta');
    }
    add('h2', 'Limitations');
    for (const item of report.limitations || []) add('p', item);
    target.hidden = false;
  } catch (failure) { status.textContent = failure.message; }
}
load();
