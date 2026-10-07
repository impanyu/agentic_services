(() => {
  const target = document.querySelector('#sample-report');
  const add = (tag, text, parent = target) => { const node = document.createElement(tag); node.textContent = text ?? ''; parent.append(node); return node; };
  fetch('report.json').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(report => {
    document.querySelector('#sample-status').textContent = `Archived observation: ${report.observedAt}`;
    add('h2', report.claim); add('p', `Assessment: ${report.status.replaceAll('_', ' ')}`); add('p', report.conclusion);
    add('h2', 'Evidence and capture status');
    for (const source of report.evidence) {
      const card = add('article', ''); add('h3', source.title, card); add('p', source.excerpt, card);
      const link = add('a', 'Inspect original source ↗', card);
      if (/^https?:\/\//.test(source.url)) { link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      add('p', `Supports/opposes: ${source.relationship} · Provider-reported: ${source.consulted ? 'yes' : 'no'} · Model-cited: ${source.cited ? 'yes' : 'not recorded'} · Snapshot saved: ${source.snapshotted ? 'yes' : 'no'}`, card);
    }
    const captures = add('details', '');
    add('summary', 'Capture metadata and content hashes (technical)', captures);
    for (const snapshot of report.snapshots || []) {
      add('p', `${snapshot.requestedUrl} · ${snapshot.status} · ${snapshot.retrievedAt}`, captures);
      if (snapshot.rawSha256) add('p', `Raw SHA-256: ${snapshot.rawSha256}`, captures);
      if (snapshot.failureReason) add('p', snapshot.failureReason, captures);
    }
    add('h2', 'Limitations');
    for (const limitation of report.limitations || []) add('p', limitation);
    if (!report.limitations?.length) add('p', 'The archived run recorded no additional limitations. The report reflects its observation date, not a current guarantee.');
  }).catch(() => { document.querySelector('#sample-status').textContent = 'Sample unavailable. Please try again later.'; });
})();
