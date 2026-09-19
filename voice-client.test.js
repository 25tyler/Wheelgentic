import test from 'node:test';
import assert from 'node:assert/strict';
import { openVoice, disposeVoice } from './voice-client.js';

function harness(t, getUserMedia, Recorder) {
  const elements = new Map();
  const node = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      textContent: '', value: '', disabled: false,
      classList: { add() {}, remove() {}, toggle() {} },
      setAttribute(name, value) { this[name] = value; },
      addEventListener() {}, removeEventListener() {},
    });
    return elements.get(selector);
  };
  const names = ['document', 'navigator', 'MediaRecorder', 'fetch'];
  const original = names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]);
  const requests = [];
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { querySelector: node, querySelectorAll: () => [] } });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia } } });
  Object.defineProperty(globalThis, 'MediaRecorder', { configurable: true, value: Recorder });
  Object.defineProperty(globalThis, 'fetch', { configurable: true, value: async (url, options) => {
    requests.push({ url, options });
    const data = url.endsWith('/status') ? { deepgram: true, meta: true, robotMode: 'demo' }
      : url.endsWith('/transcribe') ? { transcript: 'wash my left arm' }
      : { command: { category: 'showering', action: 'start', target: 'left_arm', item: 'none', response: 'Understood' }, response: 'Your showering request is ready.', delivery: { status: 'simulated' } };
    return new Response(JSON.stringify(data));
  } });
  t.after(() => {
    disposeVoice();
    for (const [name, descriptor] of original) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
    }
  });
  openVoice(() => {});
  return { node, requests };
}

class FakeRecorder {
  static isTypeSupported(type) { return type.startsWith('audio/webm'); }
  state = 'inactive';
  start() { this.state = 'recording'; }
  stop() {
    this.state = 'inactive';
    queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['audio']) }); this.onstop?.(); });
  }
}

test('microphone denial shows an error and re-enables the button', async t => {
  const { node } = harness(t, async () => { throw new DOMException('denied', 'NotAllowedError'); }, FakeRecorder);
  await node('#record-voice').onclick();
  assert.match(node('#voice-status').textContent, /access was denied/);
  assert.equal(node('#record-voice').disabled, false);
});

test('closing while microphone permission is pending releases subsequently acquired tracks', async t => {
  let resolve, stopped = 0;
  const { node, requests } = harness(t, () => new Promise(r => { resolve = r; }), FakeRecorder);
  const pending = node('#record-voice').onclick();
  disposeVoice();
  resolve({ getTracks: () => [{ stop: () => stopped++ }] });
  await pending;
  assert.equal(stopped, 1);
  assert.ok(!requests.some(r => r.url === '/api/transcribe'));
});

test('recording uploads real recorder MIME type, displays transcript and routes via command API', async t => {
  let stopped = 0;
  const { node, requests } = harness(t, async () => ({ getTracks: () => [{ stop: () => stopped++ }] }), FakeRecorder);
  await node('#record-voice').onclick();
  assert.match(node('#voice-status').textContent, /Listening/);
  assert.equal(node('#record-label').textContent, 'Stop microphone');
  assert.equal(node('#record-voice')['aria-pressed'], 'true');
  await node('#record-voice').onclick();
  await new Promise(resolve => setImmediate(resolve));
  const audioRequest = requests.find(r => r.url === '/api/transcribe');
  assert.equal(audioRequest.options.body.type, 'audio/webm;codecs=opus');
  assert.equal(JSON.parse(requests.find(r => r.url === '/api/command').options.body).transcript, 'wash my left arm');
  assert.equal(node('#voice-transcript').textContent, 'wash my left arm');
  assert.equal(node('#voice-response').textContent, 'Your showering request is ready.');
  assert.equal(node('#record-label').textContent, 'Start microphone');
  assert.equal(node('#record-voice')['aria-pressed'], 'false');
  assert.equal(node('#record-voice').disabled, false);
  assert.equal(stopped, 1);
});

test('closing an active recorder discards audio and releases the microphone', async t => {
  let stopped = 0;
  const { node, requests } = harness(t, async () => ({ getTracks: () => [{ stop: () => stopped++ }] }), FakeRecorder);
  await node('#record-voice').onclick();
  disposeVoice();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(stopped, 1);
  assert.ok(!requests.some(r => r.url === '/api/transcribe'));
});
