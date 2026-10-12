const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {File} = require('node:buffer');
const script = fs.readFileSync('ios/PhotoScout/NativeBridge.js', 'utf8');
function boot({origin = 'https://photo-scout.aisoup.net', child = false, receive = async () => ({completed: true})} = {}) {
  const calls = [];
  const window = {webkit: {messageHandlers: {scout: {postMessage: async payload => { calls.push(payload); return receive(payload); }}}}};
  window.top = child ? {} : window;
  const navigator = {geolocation: {}};
  class FileReader {
    async readAsDataURL(file) { this.result = `data:${file.type};base64,${Buffer.from(await file.arrayBuffer()).toString('base64')}`; this.onload(); }
  }
  vm.runInNewContext(script, {window, navigator, location: {origin}, File, FileReader, DOMException});
  return {navigator, calls};
}
test('bridge is absent on other origins and child frames', () => {
  assert.equal(boot({origin: 'https://evil.test'}).navigator.share, undefined);
  assert.equal(boot({child: true}).navigator.share, undefined);
});
test('image sharing transports bytes and propagates cancellation', async () => {
  const {navigator, calls} = boot();
  const files = [new File(['pixels'], 'selfie.png', {type: 'image/png'})];
  assert.equal(navigator.canShare({files}), true);
  await navigator.share({files});
  assert.equal(calls[0].mime, 'image/png');
  assert.equal(Buffer.from(calls[0].data, 'base64').toString(), 'pixels');
  const cancelled = boot({receive: async () => ({completed: false})}).navigator;
  await assert.rejects(cancelled.share({files}), error => error.name === 'AbortError');
});
test('unsupported or oversized files never reach the native handler', async () => {
  const {navigator, calls} = boot();
  for (const files of [[], [new File(['x'], 'x.html', {type: 'text/html'})], [new File([new Uint8Array(20 * 1024 * 1024 + 1)], 'x.png', {type: 'image/png'})]]) {
    assert.equal(navigator.canShare({files}), false);
    await assert.rejects(navigator.share({files}));
  }
  assert.equal(calls.length, 0);
});
test('location success and denied authorization reach existing web callbacks', async () => {
  const success = boot({receive: async () => ({coords: {latitude: 40, longitude: -96, accuracy: 10}, timestamp: 123})});
  const position = await new Promise((resolve, reject) => success.navigator.geolocation.getCurrentPosition(resolve, reject, {enableHighAccuracy: true}));
  assert.equal(position.coords.latitude, 40);
  assert.equal(success.calls[0].precise, true);
  const denied = boot({receive: async () => ({error: 'Permission denied', code: 1})});
  const error = await new Promise(resolve => denied.navigator.geolocation.getCurrentPosition(() => assert.fail('unexpected success'), resolve));
  assert.equal(error.code, 1);
});
