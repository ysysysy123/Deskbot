// Run with: node --test tests/browser/local_test.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const assets = path.resolve(__dirname, '../../local_test');

function page({protocol = 'http:'} = {}) {
  const elements = new Map();
  function element() {
    return {
      textContent: '', value: '', disabled: false, children: [], dataset: {},
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
  const requests = [];
  const intervals = [];
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
    window: {addEventListener() {}}, WebSocket: {OPEN: 1}, location: {protocol, host: '127.0.0.1:8006'},
    fetch: (url) => { requests.push(url); return new Promise(() => {}); },
    setInterval: (callback) => { intervals.push(callback); return 1; }, clearInterval() {}, setTimeout, clearTimeout,
  });
  for (const item of elements.values()) item.parentElement = element();
  vm.runInContext(fs.readFileSync(path.join(assets, 'app.js'), 'utf8'), context);
  context.testSocket = {readyState: 1, send(value) { sent.push(typeof value === 'string' ? JSON.parse(value) : value); }};
  vm.runInContext('state.connected = true; state.socket = testSocket;', context);
  return {context, elements, sent, tracks, sounds, stream, requests, intervals, run: code => vm.runInContext(code, context)};
}

function textOf(element) {
  return [element.textContent, ...element.children.map(textOf)].join('\n');
}

test('file page offers the local service link without API polling or connecting', async () => {
  const ui = page({protocol: 'file:'});
  assert.equal(ui.elements.get('file-warning').hidden, false);
  assert.equal(ui.elements.get('connect').disabled, true);
  assert.match(textOf(ui.elements.get('device-list')), /本地服务入口/);
  await ui.run('refreshHealth(); refreshDevices(); connect(); loadHistory("test-device");');
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.intervals.length, 0);
});

test('device cards distinguish voice connection, MCP initialization, failures and discovered tools', () => {
  const ui = page();
  ui.run(`renderDevices({sessions:[
    {device_id:'device-a',session_id:'a',client_type:'device',state:'idle',mcp:{supported:true,status:'initializing',tools:[]}},
    {device_id:'device-b',session_id:'b',client_type:'device',state:'listening',mcp:{supported:true,status:'ready',tools:[{name:'self.camera.take_photo',description:'实机拍照'},{name:'self.motion.get_status'}],last_call:{name:'self.camera.take_photo',status:'error',error:'相机未初始化'}}},
    {device_id:'browser-test',session_id:'c',client_type:'browser',state:'idle',mcp:{supported:false,status:'unsupported',tools:[]}}
  ]});`);
  const cards = ui.elements.get('device-list').children;
  assert.match(textOf(cards[0]), /工具初始化中/);
  assert.doesNotMatch(textOf(cards[0]), /工具就绪/);
  assert.match(textOf(cards[1]), /2 个工具/);
  assert.match(textOf(cards[1]), /self.camera.take_photo/);
  assert.match(textOf(cards[1]), /最近调用 · 失败/);
  assert.match(textOf(cards[1]), /相机未初始化/);
  assert.match(textOf(cards[2]), /浏览器测试/);
  assert.match(textOf(cards[2]), /本页测试会话使用/);
});

test('device turn timing shows server measurements and leaves missing audio unset', () => {
  const ui = page();
  ui.run(`renderDevices({sessions:[{device_id:'device-a',session_id:'a',last_turn:{status:'error',asr_ms:175,first_text_ms:2500,first_audio_ms:null,total_ms:8100,error_stage:'tts',error:'合成超时'}}]});`);
  const card = ui.elements.get('device-list').children[0];
  const metrics = card.children.find(child => child.className === 'device-turn').children[1];
  assert.match(textOf(card), /最近一轮 · 失败/);
  assert.match(textOf(metrics.children[0]), /ASR 识别\n0.17 s/);
  assert.match(textOf(metrics.children[1]), /首段文字\n2.50 s/);
  assert.match(textOf(metrics.children[2]), /首段语音\n—/);
  assert.match(textOf(metrics.children[3]), /总耗时\n8.10 s/);
  assert.match(textOf(card), /语音合成：合成超时/);
});

test('device cards keep the last MCP error visible after a device disconnects', async () => {
  const ui = page();
  const payloads = [
    {ok:true, active_count:1, sessions:[{device_id:'device-a',session_id:'a',state:'idle',client_type:'device',mcp:{status:'error',tools:[],error:'设备相机工具未注册'},last_turn:{status:'error',error_stage:'dialogue',error:'工具不可用'}}]},
    {ok:true, active_count:0, sessions:[]},
  ];
  ui.context.fetch = async () => ({ok:true, json:async () => payloads.shift()});
  await ui.run('(async()=>{await refreshDevices(); await refreshDevices()})()');
  const cards = ui.elements.get('device-list').children;
  assert.equal(cards.length, 1);
  assert.match(textOf(cards[0]), /已断开，保留最后诊断/);
  assert.match(textOf(cards[0]), /设备相机工具未注册/);
  assert.equal(ui.elements.get('device-count').textContent, '0');
});

test('read-only device tests use the selected session and keep errors visible after polling', async () => {
  const ui = page();
  ui.run(`renderDevices({ok:true,sessions:[{device_id:'test-device',session_id:'test/session',mcp:{status:'ready',tools:[{name:'self.sensor.get_imu'}]}}]});`);
  const calls = [];
  ui.context.fetch = async (url, options) => {
    calls.push({url, options});
    return {ok: false, json: async () => ({ok:false,detail:'设备已断开，请重新连接'})};
  };
  await ui.run(`testDeviceTool('test/session','self.sensor.get_imu')`);
  assert.equal(calls[0].url, '/api/devices/test%2Fsession/tools');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].options.body), {name:'self.sensor.get_imu',arguments:{}});
  ui.run('renderDevices(state.lastDevices)');
  assert.match(textOf(ui.elements.get('device-list')), /本页读取失败/);
  assert.match(textOf(ui.elements.get('device-list')), /设备已断开/);
});

test('a browser camera config switch alone does not claim the tool channel is ready', () => {
  const ui = page();
  ui.run(`state.info={camera_tools_enabled:true,vision_model:null};updateCameraCapability()`);
  assert.match(ui.elements.get('camera-capability').textContent, /通道未连接/);
  ui.run(`state.cameraMcpSocket=testSocket;updateCameraCapability()`);
  assert.match(ui.elements.get('camera-capability').textContent, /等待本页对话绑定/);
  ui.run(`state.voiceSessionId='session';state.cameraBound=true;updateCameraCapability()`);
  assert.match(ui.elements.get('camera-capability').textContent, /本页摄像头工具就绪/);
});

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
