/* Anonymous per-tab measurement. Copy to service roots with sync-usage-assets.py. */
(() => {
  const service = location.pathname.split('/')[1];
  if (!['web-evidence', 'contractor-check', 'niche-discovery'].includes(service)) return;
  let state;
  try {
    state = JSON.parse(sessionStorage.getItem('dw-usage-v1') || 'null');
    if (!state) {
      const params = new URLSearchParams(location.search);
      const allowedSources = ['direct', 'search', 'social', 'community', 'email', 'partner', 'other'];
      const allowedCampaigns = ['none', 'writer-pilot', 'developer-pilot', 'contractor-pilot'];
      const source = params.get('utm_source') || (document.referrer && new URL(document.referrer).hostname !== location.hostname ? 'other' : 'direct');
      const campaign = params.get('utm_campaign') || 'none';
      state = { id: crypto.randomUUID(), source: allowedSources.includes(source) ? source : 'other', campaign: allowedCampaigns.includes(campaign) ? campaign : 'none' };
    }
    const token = new URLSearchParams(location.hash.slice(1)).get('usage_test');
    if (token) { state.test = token; history.replaceState(null, '', location.pathname + location.search); }
    sessionStorage.setItem('dw-usage-v1', JSON.stringify(state));
  } catch (_) { state = null; }
  let optedOut = navigator.doNotTrack === '1' || navigator.globalPrivacyControl === true;
  try { optedOut ||= localStorage.getItem('dw-usage-optout') === '1'; } catch (_) { optedOut = true; }
  const headers = () => !state || optedOut ? {} : {
    'X-Usage-Session': state.id, 'X-Usage-Source': state.source,
    'X-Usage-Campaign': state.campaign, ...(state.test ? {'X-Usage-Test': state.test} : {}),
  };
  const event = stage => {
    if (!state || optedOut) return;
    fetch('https://api.aisoup.net/v1/usage/events', { method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers() },
      body: JSON.stringify({ service, stage }), keepalive: true,
    }).catch(() => {});
  };
  window.DWUsage = { headers, event };
  const controls = document.createElement('p');
  controls.className = 'usage-note';
  const note = document.createElement('span');
  note.textContent = 'Optional anonymous session measurement · 30-day retention · no claim text or stored IP addresses. ';
  const button = document.createElement('button');
  button.type = 'button';
  const label = () => { button.textContent = optedOut ? 'Measurement off' : 'Turn measurement off'; button.disabled = optedOut; };
  button.onclick = () => { optedOut = true; try { localStorage.setItem('dw-usage-optout', '1'); sessionStorage.removeItem('dw-usage-v1'); } catch (_) {} label(); };
  label(); controls.append(note, button); (document.querySelector('footer') || document.body).append(controls);
  document.addEventListener('submit', e => {
    if (['claim-form', 'checkout-form'].includes(e.target.id)) event('checkout_attempt');
  }, true);
  document.addEventListener('click', e => { if (e.target.closest('[data-usage="sample_view"]')) event('sample_view'); });
  if (!location.pathname.includes('/report/')) event('page_view');
  if (document.body.dataset.usage === 'sample_view') event('sample_view');
})();
