(() => {
  'use strict';
  if (location.origin !== 'https://photo-scout.aisoup.net' || window.top !== window) return;
  const send = message => window.webkit.messageHandlers.scout.postMessage(message);
  // Refresh the account in place: reloading discards File inputs and studio edits.
  window.PhotoScoutNative = Object.freeze({
    purchasePoints: async product => {
      const response = await fetch('/api/v1/billing/apple/account', {method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken||''}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Apple purchases unavailable');
      const productId = Object.keys(data.products).find(id => data.products[id] === product);
      if (!productId) throw new Error('Unknown Apple point product');
      return await send({action:'purchasePoints',productId,appAccountToken:data.appAccountToken});
    },
    pointProducts: async () => await send({action:'pointProducts'}),
    restorePoints: async () => await send({action:'restorePoints'}),
    managePoints: async () => await send({action:'managePoints'}),
    acceptTransaction: async signedTransaction => {
      if (!authUser || !csrfToken) throw new Error('Sign in to recover this purchase');
      const response = await fetch('/api/v1/billing/apple/transaction', {method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},body:JSON.stringify({signedTransaction})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Purchase verification failed');
      await pointWallet?.load();
      return result;
    },
    finishSignIn: async (code, verifier) => {
    const response = await fetch('/api/v1/auth/native/exchange', {
      method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code, verifier})
    });
    if (!response.ok) throw new Error('Sign-in exchange failed');
    await loadAccount();
    if (!authUser) throw new Error('Account could not refresh');
    await pointWallet?.load();
    if (pointWallet) pointWallet.menu.open = true;
    window.dispatchEvent(new Event('focus'));
    return true;
  }});
  const supported = data => Boolean(data && Array.isArray(data.files) && data.files.length === 1 &&
    data.files[0] instanceof File && ['image/png', 'image/jpeg', 'image/webp'].includes(data.files[0].type) &&
    data.files[0].size > 0 && data.files[0].size <= 20 * 1024 * 1024);
  Object.defineProperty(navigator, 'canShare', {configurable: true, value: supported});
  Object.defineProperty(navigator, 'share', {configurable: true, value: async data => {
    if (!supported(data)) throw new TypeError('Share one PNG, JPEG or WebP image up to 20 MB.');
    const file = data.files[0];
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = () => reject(new Error('Could not read this photo.'));
      reader.readAsDataURL(file);
    });
    const result = await send({action: 'share', mime: file.type, data: encoded});
    if (!result.completed) throw new DOMException('Sharing cancelled.', 'AbortError');
  }});
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition = (success, failure, options = {}) => {
      let settled = false;
      const timeout = Number.isFinite(options.timeout) ? Math.max(0, options.timeout) : 30000;
      const finish = (callback, result) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback?.(result);
      };
      const timer = setTimeout(() => finish(failure, {code: 3, message: 'Location timed out'}), timeout);
      if (timeout === 0) return;
      send({action: 'location', precise: Boolean(options.enableHighAccuracy)}).then(result => {
        if (result.error) finish(failure, {code: result.code, message: result.error});
        else finish(success, result);
      }, error => finish(failure, {code: 2, message: String(error)}));
    };
  }
})();
