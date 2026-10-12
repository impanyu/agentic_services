const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {File} = require('node:buffer');
const script = fs.readFileSync('ios/PhotoScout/NativeBridge.js', 'utf8');
function boot({origin = 'https://photo-scout.aisoup.net', child = false, receive = async () => ({completed: true}), exchangeOK = true, nativeAuthReady = true, appleAuthReady = false} = {}) {
  const calls = [];
  const events = [], requests = [];
  let accountRefreshes = 0;
  const window = {dispatchEvent: event => events.push(event.type), webkit: {messageHandlers: {scout: {postMessage: async payload => { calls.push(payload); return receive(payload); }}}}};
  window.top = child ? {} : window;
  const navigator = {geolocation: {}};
  class FileReader {
    async readAsDataURL(file) { this.result = `data:${file.type};base64,${Buffer.from(await file.arrayBuffer()).toString('base64')}`; this.onload(); }
  }
  const context = {window, navigator, location: {origin, reload: () => assert.fail('must preserve editor')}, File, FileReader, DOMException, Event, setTimeout, clearTimeout,
    fetch: async (url, options) => {requests.push({url, options}); return {ok: exchangeOK,json:async()=>({nativeAuthReady,providers:{apple:appleAuthReady}})};},
    loadAccount: async () => {accountRefreshes++;}, authUser: {id: 'user'}, pointWallet:{load:async()=>{},finishSignIn:()=>{events.push('close-account-page')}}};
  vm.runInNewContext(script, context);
  return {navigator, calls, window, events, requests, context, accountRefreshes: () => accountRefreshes};
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
test('native login refreshes account without replacing unsaved editor state', async () => {
  const app = boot();
  const selectedPhoto = new File(['portrait'], 'selfie.png', {type: 'image/png'});
  app.window.selectedPhoto = selectedPhoto;
  await app.window.PhotoScoutNative.finishSignIn('code', 'verifier');
  assert.equal(app.window.selectedPhoto, selectedPhoto);
  assert.equal(app.accountRefreshes(), 1);
  assert.deepEqual(app.events, ['close-account-page','focus']);
  assert.equal(app.requests[0].url, '/api/v1/auth/native/exchange');
  assert.equal(app.requests[0].options.credentials, 'include');
  assert.deepEqual(JSON.parse(app.requests[0].options.body), {code:'code',verifier:'verifier'});
});
test('failed login exchange or account refresh does not report success', async () => {
  const app = boot({exchangeOK:false});
  await assert.rejects(app.window.PhotoScoutNative.finishSignIn('code','proof'));
  assert.equal(app.accountRefreshes(), 0);
  assert.deepEqual(app.events, []);
  const stale = boot(); stale.context.authUser = null;
  await assert.rejects(stale.window.PhotoScoutNative.finishSignIn('code','proof'));
  assert.deepEqual(stale.events, []);
});
test('location timeout completes once and ignores late native results', async () => {
  let resolveNative, successes = 0, failures = 0;
  const app = boot({receive: () => new Promise(resolve => {resolveNative=resolve;})});
  const result = await new Promise(resolve => app.navigator.geolocation.getCurrentPosition(
    () => successes++, error => {failures++; resolve(error);}, {timeout:5}));
  assert.equal(result.code, 3);
  resolveNative({coords:{latitude:40,longitude:-96}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(successes,0); assert.equal(failures,1);
});
test('zero location timeout does not request native permission', async () => {
  const app = boot();
  const error = await new Promise(resolve => app.navigator.geolocation.getCurrentPosition(
    () => assert.fail('unexpected position'), resolve, {timeout:0}));
  assert.equal(error.code,3); assert.equal(app.calls.length,0);
});

test('native login refuses a backend missing the handoff capability',async()=>{const app=boot({nativeAuthReady:false});await assert.rejects(app.window.PhotoScoutNative.signIn());assert.equal(app.calls.length,0);const ready=boot();await ready.window.PhotoScoutNative.signIn();assert.equal(ready.calls[0].action,'signIn');});

test('native Apple login selects its provider only when backend is ready',async()=>{const unavailable=boot();await assert.rejects(unavailable.window.PhotoScoutNative.signIn('apple'));assert.equal(unavailable.calls.length,0);const ready=boot({appleAuthReady:true});await ready.window.PhotoScoutNative.signIn('apple');assert.equal(ready.calls[0].provider,'apple');assert.ok(ready.window.PhotoScoutNative.signInProviders.includes('apple'));await assert.rejects(ready.window.PhotoScoutNative.signIn('unknown'));assert.equal(ready.calls.length,1);});
