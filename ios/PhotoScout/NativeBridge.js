(() => {
  'use strict';
  if (location.origin !== 'https://photo-scout.aisoup.net' || window.top !== window) return;
  const send = message => window.webkit.messageHandlers.scout.postMessage(message);
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
      send({action: 'location', precise: Boolean(options.enableHighAccuracy)}).then(result => {
        if (result.error) failure?.({code: result.code, message: result.error});
        else success(result);
      }, error => failure?.({code: 2, message: String(error)}));
    };
  }
})();
