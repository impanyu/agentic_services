const status = document.getElementById('report-status');
const target = document.getElementById('report');
const sessionId = new URLSearchParams(window.location.search).get('session_id');
function add(tag, content, parent = target, className = '') {
  const node = document.createElement(tag);
  node.textContent = content ?? 'Not recorded';
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}
async function load() {
  if (!sessionId) { status.textContent = 'No checkout session was provided.'; return; }
  try {
    const response = await fetch(`/contractor-check/v1/report?session_id=${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Report unavailable.');
    const report = data.report;
    status.textContent = 'Payment confirmed · Order ' + data.orderId;
    add('h2', report.businessName);
    add('p', `License #${report.licenseNumber} · ${report.assessment.replaceAll('_', ' ')}`, target, 'result-summary');
    const list = add('div', '', target, 'report-grid');
    const labels = { licenseActive: 'License active', c10Classification: 'C-10 classification', contractorBondOnRecord: 'Contractor bond on record', workersCompOnRecord: "Workers' comp on record" };
    for (const [key, label] of Object.entries(labels)) {
      const item = add('div', '', list, 'report-check');
      add('span', label, item);
      add('strong', report.checks[key] ? 'Recorded' : 'Not confirmed', item, report.checks[key] ? 'yes' : 'no');
    }
    add('h3', 'Source details');
    add('p', `Status: ${report.details.licenseStatus}`);
    add('p', `Classifications: ${report.details.classifications.join(', ') || 'None listed'}`);
    add('p', `Bond record: ${report.details.bondRecord || 'Not shown'}`);
    add('p', `Workers' compensation: ${report.details.workersCompRecord || 'Not shown'}`);
    add('p', `CSLB states: ${report.source.sourceAsOf || 'time not provided'} · Retrieved: ${report.source.retrievedAt}`);
    add('p', `Source HTML SHA-256: ${report.source.htmlSha256}`);
    const link = add('a', 'Review the original CSLB record ↗');
    link.href = report.source.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    add('h3', 'Limitations');
    for (const limitation of report.limitations) add('p', limitation);
    target.hidden = false;
  } catch (failure) { status.textContent = failure.message; }
}
load();
