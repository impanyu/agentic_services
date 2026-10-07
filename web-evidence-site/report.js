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
  target.replaceChildren();
  document.querySelector('#retry-report').disabled = true;
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
    add('h2', 'Snapshot metadata');
    for (const snapshot of report.snapshots || []) {
      add('p', `${snapshot.requestedUrl} · ${snapshot.status} · Captured ${snapshot.retrievedAt}`);
      if (snapshot.rawSha256) add('p', `Raw SHA-256: ${snapshot.rawSha256}`, target, 'report-meta');
      if (snapshot.failureReason) add('p', snapshot.failureReason, target, 'report-meta');
    }
    const download = add('button', 'Download report JSON');
    download.type = 'button';
    download.onclick = () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
      const link = document.createElement('a'); link.href = url; link.download = 'web-evidence-report.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    add('h2', 'Limitations');
    for (const item of report.limitations || []) add('p', item);
    target.hidden = false;
    window.DWUsage?.event('report_view');
  } catch (failure) { status.textContent = failure.message; } finally { document.querySelector('#retry-report').disabled = false; }
}
document.querySelector('#retry-report').onclick = load;
load();
