// Run with: node --test tests/browser/local_test.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const assets = path.resolve(__dirname, '../../local_test');

function page() {
  const elements = new Map();
  function element() {
    return {
      textContent: '', value: '', disabled: false, children: [],
      classList: {toggle() {}}, addEventListener() {}, removeEventListener() {}, remove() {},
      append(...items) { this.children.push(...items); },
      replaceChildren(...items) { this.children = items; },
      querySelector() { return null; },
      getContext() { return {drawImage() {}}; },
      toDataURL() { return 'data:image/jpeg;base64,test-frame'; },
    };
  }
  const html = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
  for (const match of html.matchAll(/id="([^"]+)"/g)) elements.set(match[1], element());
  const sent = [];
  const tracks = [];
  const sounds = [];
  const stream = () => {
    const track = {stopped: false, stop() { this.stopped = true; }};
    tracks.push(track);
    return {getTracks: () => [track]};
  };
  class AudioContext {
    state = 'running'; sampleRate = 48000; currentTime = 0; destination = {};
    audioWorklet = {addModule: async () => {}};
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    createBuffer(channels, length, rate) {
      return {duration: length / rate, getChannelData: () => new Float32Array(length)};
    }
    createBufferSource() {
      const source = {stopped: false, connect() {}, disconnect() {}, start() {}, stop() { this.stopped = true; }};
      sounds.push(source);
      return source;
    }
  }
  class AudioWorkletNode {
    port = {postMessage: () => {}, onmessage: null};
    connect() {} disconnect() {}
  }
  const context = vm.createContext({
    console, performance, AudioContext, AudioWorkletNode,
    navigator: {mediaDevices: {getUserMedia: async () => stream()}},
    document: {
      getElementById(id) { assert.ok(elements.has(id), `Missing HTML element: ${id}`); return elements.get(id); },
      createElement: element,
    },
    window: {addEventListener() {}}, WebSocket: {OPEN: 1},
    fetch: () => new Promise(() => {}),
    setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
  });
  for (const item of elements.values()) item.parentElement = element();
  vm.runInContext(fs.readFileSync(path.join(assets, 'app.js'), 'utf8'), context);
  context.testSocket = {readyState: 1, send(value) { sent.push(typeof value === 'string' ? JSON.parse(value) : value); }};
  vm.runInContext('state.connected = true; state.socket = testSocket;', context);
  return {context, elements, sent, tracks, sounds, stream, run: code => vm.runInContext(code, context)};
}

test('continuous capture stays open over turns; playback must drain before resume', async () => {
  const ui = page();
  await ui.run('startContinuous()');
  assert.equal(ui.tracks.length, 1);
  assert.ok(ui.sent.some(message => message.type === 'continuous.start'));
  ui.run(`handleEvent({type:'test.continuous',state:'listening'});
    handleEvent({type:'test.continuous',state:'submitted'});
    playPCM(new Int16Array(1440).buffer);
    handleEvent({type:'tts',state:'stop'});
    handleEvent({type:'test.continuous',state:'reply_done'});`);
  assert.equal(ui.sounds.length, 1);
  assert.equal(ui.sent.filter(message => message.type === 'continuous.resume').length, 0);
  ui.sounds[0].onended();
  assert.equal(ui.sent.filter(message => message.type === 'continuous.resume').length, 1);
  assert.equal(ui.tracks[0].stopped, false);
  // Empty recognition still completes a turn and must continue listening.
  ui.run(`handleEvent({type:'test.continuous',state:'submitted'});
    handleEvent({type:'test.continuous',state:'reply_done'});`);
  assert.equal(ui.sent.filter(message => message.type === 'continuous.resume').length, 2);
  await ui.run('stopContinuous()');
  assert.equal(ui.tracks[0].stopped, true);
});

test('abort during continuous mode clears audio but preserves microphone', async () => {
  const ui = page();
  await ui.run('startContinuous()');
  const startCount = ui.sent.filter(message => message.type === 'continuous.start').length;
  await ui.run('abort()');
  assert.equal(ui.tracks[0].stopped, false);
  assert.equal(ui.sent.filter(message => message.type === 'continuous.start').length, startCount + 1);
  assert.equal(ui.run('state.continuous'), true);
  await ui.run('stopContinuous()');
  assert.equal(ui.tracks[0].stopped, true);
});

test('VAD interruption stops queued playback and keeps the microphone for the next turn', async () => {
  const ui = page();
  await ui.run('startContinuous()');
  ui.run(`handleEvent({type:'test.continuous',state:'submitted'});
    playPCM(new Int16Array(1440).buffer);
    handleEvent({type:'test.continuous',state:'reply_done'});
    handleEvent({type:'test.continuous',state:'interrupted'});
    playPCM(new Int16Array(1440).buffer);
    handleEvent({type:'tts',state:'sentence_start',text:'stale reply'});
    handleEvent({type:'tts',state:'stop'});`);
  assert.equal(ui.sounds[0].stopped, true);
  assert.equal(ui.sounds[0].onended, null);
  assert.equal(ui.run('state.playing.size'), 0);
  assert.equal(ui.run('state.continuousResumePending'), false);
  assert.equal(ui.tracks[0].stopped, false);
  assert.equal(ui.sounds.length, 1, 'stale audio must not be played');
  assert.equal(ui.sent.filter(message => message.type === 'continuous.resume').length, 0);
  ui.run(`handleEvent({type:'test.continuous',state:'listening'});
    handleEvent({type:'test.continuous',state:'submitted'});
    handleEvent({type:'tts',state:'sentence_start',text:'new reply'});
    playPCM(new Int16Array(1440).buffer);`);
  assert.equal(ui.run('state.assistant.textContent'), 'new reply');
  assert.equal(ui.sounds.length, 2);
  await ui.run('stopContinuous()');
});

test('camera calls share pending permission and opening twice reports success', async () => {
  const ui = page();
  let grant;
  ui.context.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const first = ui.run('startCamera()');
  const second = ui.run('startCamera()');
  grant(ui.stream());
  assert.equal(await first, true);
  assert.equal(await second, true);
  ui.run('state.cameraMcpSocket = testSocket');
  await ui.run(`handleCameraMcpCall({id:'test',tool:'self_camera_start'})`);
  assert.equal(ui.sent.at(-1).result.success, true);
  assert.equal(ui.tracks.length, 1);
  ui.run('stopCamera()');
});

test('camera binding follows the voice hello', () => {
  const ui = page();
  ui.run(`state.cameraMcpSocket = testSocket;
    handleEvent({type:'hello',session_id:'current-session'});`);
  assert.equal(ui.sent.at(-1).type, 'camera.mcp.bind');
  assert.equal(ui.sent.at(-1).session_id, 'current-session');
});

test('submitting a continuous turn preserves capture; stopping pending permission releases the late stream', async () => {
  const ui = page();
  await ui.run('startContinuous()');
  ui.run('submitRecording()');
  assert.ok(ui.sent.some(message => message.type === 'continuous.submit'));
  assert.equal(ui.tracks[0].stopped, false);
  assert.ok(ui.run('state.recording'));
  await ui.run('stopContinuous()');

  let grant;
  ui.context.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const request = ui.run('startContinuous()');
  await new Promise(resolve => setImmediate(resolve));
  await ui.run('stopContinuous()');
  grant(ui.stream());
  await request;
  assert.equal(ui.tracks[1].stopped, true);
  assert.equal(ui.run('state.recording'), null);
  assert.equal(ui.run('state.continuous'), false);
});

test('camera snapshot is local and closing releases video tracks', async () => {
  const ui = page();
  await ui.run('startCamera()');
  Object.assign(ui.elements.get('camera-preview'), {videoWidth: 1280, videoHeight: 720, readyState: 2});
  ui.run('captureCamera()');
  assert.equal(ui.elements.get('camera-snapshot').hidden, false);
  assert.match(ui.elements.get('camera-snapshot').src, /^data:image\/jpeg/);
  assert.equal(ui.sent.length, 0, 'Camera must not upload a frame to the text service');
  ui.run('stopCamera()');
  assert.equal(ui.tracks[0].stopped, true);
  assert.equal(ui.elements.get('camera-preview').srcObject, null);
});

test('closing camera while permission is pending stops the late stream', async () => {
  const ui = page();
  let grant;
  ui.context.navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
  const request = ui.run('startCamera()');
  ui.run('stopCamera()');
  grant(ui.stream());
  await request;
  assert.equal(ui.tracks[0].stopped, true);
  assert.equal(ui.run('state.cameraStream'), null);
});

test('photo tool opens the camera and waits for image data before capturing', async () => {
  const ui = page();
  const video = ui.elements.get('camera-preview');
  const listeners = new Map();
  Object.assign(video, {videoWidth: 1280, videoHeight: 720, readyState: 1,
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name) { listeners.delete(name); },
  });
  ui.run('state.cameraMcpSocket = testSocket');
  const call = ui.run(`handleCameraMcpCall({id:'first-photo',tool:'self_camera_take_photo'})`);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.tracks.length, 1, 'photo tool must open a closed camera');
  assert.equal(ui.sent.length, 0, 'metadata alone is not an image');
  assert.equal(ui.elements.get('camera-snapshot').src, undefined);
  video.readyState = 2;
  listeners.get('loadeddata')();
  await call;
  assert.equal(ui.sent.at(-1).result.success, true);
  assert.match(ui.sent.at(-1).result.photo_data, /^data:image\/jpeg/);
  assert.equal(listeners.size, 0);
  ui.run('stopCamera()');
});

test('worklet continuous capture exceeds 30 seconds with bounded frame buffering', () => {
  const messages = [];
  let Recorder;
  const context = vm.createContext({
    sampleRate: 48000,
    AudioWorkletProcessor: class { port = {postMessage: data => messages.push(data)}; },
    registerProcessor(name, value) { Recorder = value; },
  });
  vm.runInContext(fs.readFileSync(path.join(assets, 'pcm-worklet.js'), 'utf8'), context);
  const recorder = new Recorder({processorOptions: {maxSamples: 0}});
  const input = new Float32Array(48000).fill(0.05);
  for (let second = 0; second < 65; second++) recorder.process([[input]]);
  recorder.port.onmessage({data: 'stop'});
  const packets = messages.filter(message => message.type === 'pcm');
  assert.equal(packets.reduce((sum, message) => sum + message.buffer.byteLength / 2, 0), 65 * 16000);
  assert.ok(packets.every(message => message.buffer.byteLength <= 1920));
  assert.equal(messages.some(message => message.type === 'limit'), false);
  assert.equal(recorder.active, false);
});
