"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  socket: null, connected: false, recording: null, upload: null, startingMic: false,
  info: null, context: null, workletLoaded: false, playing: new Set(), playAt: 0,
  receiveAudio: true, ttsFinished: true, turnAt: 0, firstAudio: false,
  audioSamples: 0, assistant: null, logs: [], inputKind: "audio", inputText: "",
  continuous: false, cameraStream: null, cameraStarting: false, cameraRequest: 0, cameraMcpSocket: null, continuousResumePending: false,
  continuousPhase: "started",
  voiceSessionId: null, cameraStartPromise: null, cameraReconnectTimer: null, devicesTimer: null, closing: false,
  historyDeviceId: "", expandedDeviceTools: new Set(), deviceToolResults: new Map(), devicesLoaded: false,
  cameraBound: false, deviceCache: new Map(),
};

function isFilePage() { return location.protocol === "file:"; }

function log(kind, message) {
  const time = new Date().toLocaleTimeString("zh-CN", {hour12: false});
  state.logs.push(`${time}  ${kind.padEnd(5)}  ${message}`);
  if (state.logs.length > 400) state.logs.shift();
  $("event-log").textContent = state.logs.join("\n");
  $("event-log").scrollTop = $("event-log").scrollHeight;
}

function showMessage(role, text) {
  $("empty-transcript")?.remove();
  const entry = document.createElement("div");
  entry.className = `message ${role}`;
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = {user: "你", assistant: "语音助手", system: "提示"}[role];
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  entry.append(label, body);
  $("transcript").append(entry);
  while ($("transcript").children.length > 120) $("transcript").firstElementChild.remove();
  $("transcript").scrollTop = $("transcript").scrollHeight;
  return body;
}

function showHistoryMessage(message) {
  const body = showMessage(message.role === "assistant" ? "assistant" : "user", message.content || "");
  body.dataset.history = "true";
}

async function loadHistory(deviceId = state.historyDeviceId || $("device-id").value.trim()) {
  if (isFilePage()) return;
  deviceId = String(deviceId || "").trim();
  if (!deviceId) {
    $("history-status").textContent = "请先填写设备 ID。";
    return;
  }
  state.historyDeviceId = deviceId;
  $("history-status").textContent = `正在加载 ${deviceId} 的历史记录…`;
  try {
    const response = await fetch(`/api/history?device_id=${encodeURIComponent(deviceId)}&limit=50`);
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.detail || "历史记录接口响应异常");
    $("transcript").replaceChildren();
    const messages = Array.isArray(payload.messages) ? payload.messages : [];
    if (payload.summary) showMessage("system", `记忆摘要：${payload.summary}`);
    for (const message of messages) showHistoryMessage(message);
    if (!messages.length && !payload.summary) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "这个设备还没有历史对话";
      $("transcript").append(empty);
    }
    $("history-status").textContent = `已加载 ${deviceId} 的 ${messages.length} 条历史消息。`;
    $("transcript").scrollTop = $("transcript").scrollHeight;
  } catch (error) {
    $("history-status").textContent = error.message || "历史记录暂不可用";
  }
}

function updateControls() {
  const inputBusy = Boolean(state.recording || state.upload || state.startingMic);
  $("connect").disabled = isFilePage() || Boolean(state.socket);
  $("disconnect").disabled = !state.socket;
  $("device-id").disabled = Boolean(state.socket);
  $("token").disabled = Boolean(state.socket);
  for (const id of ["send-text", "record", "upload", "continuous"]) $(id).disabled = !state.connected || inputBusy;
  $("stop-record").textContent = state.continuous ? "提交这一句" : "结束并发送";
  $("stop-record").disabled = !state.recording || (state.continuous && state.continuousPhase !== "listening");
  $("stop-continuous").disabled = !state.continuous;
  $("abort").disabled = !state.connected;
  $("recording-dot").classList.toggle("active", inputBusy);
}

function connectionStatus(text, kind = "") {
  $("connection-status").textContent = `本页${text}`;
  $("connection-status").className = `badge ${kind}`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  return `${minutes} 分 ${total % 60} 秒`;
}

function deviceText(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function toolResultText(result) {
  if (Array.isArray(result?.content)) {
    return result.content.filter(part => part.type === "text" && typeof part.text === "string")
      .map(part => part.text).join("\n").slice(0, 1600) || "工具已返回结果";
  }
  return typeof result === "string" ? result.slice(0, 1600) : JSON.stringify(result ?? {}, null, 2).slice(0, 1600);
}

async function testDeviceTool(sessionId, name) {
  if (state.deviceToolResults.get(sessionId)?.pending || state.lastDevices?.sessions.some(session => session.session_id === sessionId && session.offline)) return;
  state.deviceToolResults.set(sessionId, {pending: true, name, text: "正在读取设备…"});
  renderDevices(state.lastDevices);
  try {
    const response = await fetch(`/api/devices/${encodeURIComponent(sessionId)}/tools`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name, arguments: {}}),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      const detail = payload.result ? toolResultText(payload.result) : "";
      throw new Error([payload.detail, detail].filter(Boolean).join("\n") || "工具调用失败");
    }
    state.deviceToolResults.set(sessionId, {name, text: toolResultText(payload.result), error: Boolean(payload.result?.isError)});
  } catch (error) {
    state.deviceToolResults.set(sessionId, {name, text: error.message || "设备未响应", error: true});
  }
  renderDevices(state.lastDevices);
}

function renderDeviceTools(session, item) {
  const mcp = session.mcp;
  const tools = Array.isArray(mcp?.tools) ? mcp.tools : [];
  const status = mcp?.status || "unknown";
  const labels = {unsupported: "未声明 MCP 支持", initializing: "工具初始化中", ready: "工具就绪", error: "工具连接异常", closed: "工具通道已关闭", unknown: "工具状态未上报"};
  const summary = deviceText("div", "device-tool-heading", "");
  summary.append(deviceText("span", `tool-state ${session.offline ? "offline" : status}`, `${session.offline ? "上次状态 · " : ""}${labels[status] || "工具状态未知"}`));
  summary.append(deviceText("span", "small", `${tools.length} 个工具`));
  item.append(summary);
  if (status === "unsupported" || status === "unknown") {
    item.append(deviceText("p", "hint", session.client_type === "browser"
      ? "本页测试会话使用下方的浏览器摄像头通道。"
      : (status === "unsupported" ? "设备握手未声明 MCP；请检查实机固件是否启用工具。" : "服务未提供工具诊断；请更新并重启语音服务。")));
  }
  if (status === "initializing") item.append(deviceText("p", "hint", session.offline ? "设备断开时，工具初始化尚未完成。" : "正在与设备握手并读取工具列表…"));
  if (status === "ready" && !tools.length) item.append(deviceText("p", "hint", "设备已响应，但没有返回可调用工具。"));
  if (mcp?.error) item.append(deviceText("p", "device-error", mcp.error));
  if (tools.length) {
    const details = document.createElement("details");
    details.className = "device-tools";
    details.open = state.expandedDeviceTools.has(session.session_id);
    details.addEventListener("toggle", () => {
      if (details.open) state.expandedDeviceTools.add(session.session_id);
      else state.expandedDeviceTools.delete(session.session_id);
    });
    details.append(deviceText("summary", "", "查看设备工具"));
    const toolsList = document.createElement("ul");
    for (const tool of tools) {
      const row = document.createElement("li");
      row.append(deviceText("code", "", tool.name || "未命名工具"));
      if (tool.description) row.append(deviceText("span", "", tool.description));
      toolsList.append(row);
    }
    details.append(toolsList);
    item.append(details);
    const readTools = [["self.motion.get_status", "读取底盘状态"], ["self.sensor.get_imu", "读取姿态"], ["self.get_device_status", "读取设备状态"]];
    const actions = document.createElement("div");
    actions.className = "button-row device-tool-actions";
    for (const [name, label] of readTools) {
      if (!tools.some(tool => tool.name === name)) continue;
      const button = deviceText("button", "", label);
      button.disabled = session.offline || status !== "ready" || Boolean(state.deviceToolResults.get(session.session_id)?.pending);
      button.addEventListener("click", () => void testDeviceTool(session.session_id, name));
      actions.append(button);
    }
    item.append(actions);
  }
  const lastCall = mcp?.last_call;
  if (lastCall) {
    const labels = {running: "执行中", success: "成功", error: "失败"};
    const box = deviceText("div", `device-last-call ${lastCall.status === "error" ? "failed" : ""}`, "");
    box.append(deviceText("div", "small", `最近调用 · ${labels[lastCall.status] || lastCall.status}`));
    box.append(deviceText("code", "", lastCall.name || "—"));
    if (lastCall.error || lastCall.summary) box.append(deviceText("p", "hint", lastCall.error || lastCall.summary));
    item.append(box);
  }
  const testResult = state.deviceToolResults.get(session.session_id);
  if (testResult) {
    const box = deviceText("div", `device-test-result${testResult.error ? " failed" : ""}`, "");
    box.append(deviceText("div", "small", `本页读取${testResult.pending ? "中" : testResult.error ? "失败" : "结果"} · ${testResult.name}`));
    box.append(deviceText("pre", "", testResult.text));
    item.append(box);
  }
}

function renderDeviceTurn(turn, item) {
  if (!turn) return;
  const labels = {running: "进行中", success: "完成", error: "失败", cancelled: "已打断"};
  const box = deviceText("div", "device-turn", "");
  box.append(deviceText("div", "small", `最近一轮 · ${labels[turn.status] || turn.status || "未知"}`));
  const metrics = document.createElement("div");
  metrics.className = "device-turn-metrics";
  for (const [key, label] of [["asr_ms", "ASR 识别"], ["first_text_ms", "首段文字"], ["first_audio_ms", "首段语音"], ["total_ms", "总耗时"]]) {
    const metric = document.createElement("div");
    const value = turn[key];
    metric.append(deviceText("span", "small", label));
    metric.append(deviceText("strong", "", Number.isFinite(value) ? `${(value / 1000).toFixed(2)} s` : "—"));
    metrics.append(metric);
  }
  box.append(metrics);
  box.append(deviceText("p", "hint", "ASR 为识别耗时；其余从收到本轮输入起计时。"));
  if (turn.input_text) box.append(deviceText("p", "hint", `本轮识别 / 输入：${turn.input_text}`));
  for (const call of turn.tool_calls || []) {
    box.append(deviceText("p", call.status === "error" ? "device-error" : "hint",
      `${call.name} · ${call.status === "error" ? "失败" : "成功"}${call.error ? `：${call.error}` : ""}`));
  }
  if (turn.error_stage || turn.error) {
    const stages = {asr: "语音识别", dialogue: "对话 / 工具", tool: "工具调用", llm: "模型回复", tts: "语音合成", pipeline: "语音处理"};
    const stage = stages[turn.error_stage] || turn.error_stage || "未知阶段";
    box.append(deviceText("p", "device-error", `${stage}：${turn.error || "发生错误"}`));
  }
  item.append(box);
}

function renderDevices(payload) {
  if (!payload) return;
  state.lastDevices = payload;
  const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
  $("device-count").textContent = String(payload.active_count ?? sessions.filter(session => !session.offline).length);
  const list = $("device-list");
  list.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "当前没有在线设备";
    list.append(empty);
    return;
  }
  const stateLabels = {connected: "已连接", idle: "空闲", listening: "正在听", recognizing: "识别中", thinking: "思考中", speaking: "播放中", closed: "已关闭"};
  for (const session of sessions) {
    const item = document.createElement("div");
    item.className = `device-item${session.offline ? " offline" : ""}`;
    const heading = document.createElement("div");
    heading.className = "device-heading";
    const name = document.createElement("strong");
    name.textContent = session.device_id || "未知设备";
    const badge = document.createElement("span");
    badge.className = `device-state${session.offline ? " offline" : ""}`;
    badge.textContent = session.offline ? "离线" : stateLabels[session.state] || session.state || "未知";
    heading.append(name, badge);
    const detail = document.createElement("div");
    detail.className = "device-detail";
    detail.textContent = session.offline
      ? `${session.client_type === "browser" ? "浏览器测试" : "实机"} · 已断开，保留最后诊断 · 会话 ${session.session_id || "—"}`
      : `${session.client_type === "browser" ? "浏览器测试" : session.client_type === "device" ? "实机" : "设备"} · 已连接 ${formatDuration(session.duration_s)} · 会话 ${session.session_id || "—"}`;
    const historyButton = document.createElement("button");
    historyButton.className = "quiet device-history";
    historyButton.textContent = "查看历史";
    historyButton.addEventListener("click", () => void loadHistory(session.device_id));
    item.append(heading, detail);
    renderDeviceTools(session, item);
    renderDeviceTurn(session.last_turn, item);
    item.append(historyButton);
    list.append(item);
  }
}

async function refreshDevices() {
  if (isFilePage()) return;
  try {
    const response = await fetch("/api/devices");
    if (!response.ok) throw new Error("设备状态接口响应异常");
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.detail || "设备状态暂不可用");
    const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    const activeIds = new Set(sessions.map(session => session.session_id));
    const activeDevices = new Set(sessions.map(session => session.device_id));
    for (const [sessionId, session] of state.deviceCache) {
      // Replace a previous connection of the same device, retaining concurrent live sessions.
      if (!activeIds.has(sessionId) && activeDevices.has(session.device_id)) {
        state.deviceCache.delete(sessionId);
        state.deviceToolResults.delete(sessionId);
        state.expandedDeviceTools.delete(sessionId);
      }
    }
    for (const session of sessions) {
      state.deviceCache.delete(session.session_id);
      state.deviceCache.set(session.session_id, session);
    }
    const disconnected = [...state.deviceCache.values()].reverse().filter(session => !activeIds.has(session.session_id));
    for (const session of disconnected.slice(10)) {
      state.deviceCache.delete(session.session_id);
      state.deviceToolResults.delete(session.session_id);
      state.expandedDeviceTools.delete(session.session_id);
    }
    const visibleSessions = [...sessions, ...disconnected.slice(0, 10).map(session => ({...session, offline: true}))];
    renderDevices({...payload, sessions: visibleSessions, active_count: payload.active_count ?? sessions.length});
    state.devicesLoaded = true;
    $("device-list").classList.toggle("stale", false);
    $("device-status").textContent = "每 2 秒刷新一次；本页保留最近 10 个离线会话的最后诊断，设备重连后更新。刷新页面会清除离线记录。";
  } catch (error) {
    if (!state.devicesLoaded) {
      $("device-count").textContent = "—";
      $("device-list").replaceChildren(deviceText("p", "empty-state", "暂时无法读取在线设备"));
    }
    $("device-list").classList.toggle("stale", true);
    $("device-status").textContent = `${error.message || "设备状态暂不可用"}${state.devicesLoaded ? "；上方保留上次状态，连接情况待确认。" : "；请检查语音服务和测试页是否已启动。"}`;
  }
}

function send(message) {
  if (!state.connected || state.socket?.readyState !== WebSocket.OPEN) return false;
  state.socket.send(JSON.stringify(message));
  log("发送", `${message.type}${message.state ? `.${message.state}` : ""}${message.text ? ` · ${message.text}` : ""}`);
  return true;
}

async function audioContext() {
  if (!state.context) state.context = new AudioContext({latencyHint: "interactive"});
  if (state.context.state === "suspended") await state.context.resume();
  return state.context;
}

function clearPlayback() {
  for (const source of state.playing) {
    source.onended = null;
    source.stop();
    source.disconnect();
  }
  state.playing.clear();
  state.playAt = 0;
  $("playback-status").textContent = "播放已停止";
}

function beginTurn(inputKind = "audio", inputText = "") {
  clearPlayback();
  state.receiveAudio = true;
  state.ttsFinished = false;
  state.turnAt = performance.now();
  state.firstAudio = false;
  state.audioSamples = 0;
  state.assistant = null;
  state.inputKind = inputKind;
  state.inputText = inputText;
  $("metric-stt").textContent = inputKind === "text" ? "已跳过" : "—";
  $("metric-audio").textContent = "—";
  $("metric-duration").textContent = "0.00 s";
  $("playback-status").textContent = "等待回复…";
}

function startAudioInput(mode = "manual") {
  state.receiveAudio = false;
  state.ttsFinished = true;
  clearPlayback();
  send({type: "abort"});
  send({type: "listen", state: "start", mode});
}

function recordingLimitSamples() {
  // Align to complete 60 ms Opus frames so bridge padding cannot exceed the limit.
  return Math.floor(Math.min(30, state.info?.max_recording_seconds || 30) * 16000 / 960) * 960;
}

function continuousSilenceMs() {
  return state.info?.vad_min_silence_duration_ms || 700;
}

function elapsed() {
  return `${((performance.now() - state.turnAt) / 1000).toFixed(2)} s`;
}

function updateContinuousStatus() {
  if (!state.continuous) return;
  const labels = {started: "等待你说话", listening: "正在听，停顿后自动提交", submitted: "等待回复，可以说话打断", reply_done: "正在播放，可以说话打断", interrupted: "已检测到你说话，正在打断"};
  const seconds = state.recording ? Math.floor((performance.now() - state.recording.started) / 1000) : 0;
  $("recording-status").textContent = `持续对话 ${seconds} 秒 · ${labels[state.continuousPhase] || "准备中"}`;
}

function resumeContinuousIfReady() {
  if (!state.continuousResumePending || state.playing.size || !state.continuous || !state.recording || !state.connected) return;
  state.continuousResumePending = false;
  state.receiveAudio = false;
  state.continuousPhase = "started";
  send({type: "continuous.resume"});
  updateContinuousStatus();
}

function playPCM(buffer) {
  if (!state.receiveAudio || !state.context) return;
  const pcm = new Int16Array(buffer);
  if (!pcm.length) return;
  if (!state.firstAudio) {
    state.firstAudio = true;
    $("metric-audio").textContent = state.turnAt ? elapsed() : "—";
    log("音频", `收到首段音频${state.turnAt ? ` · ${elapsed()}` : ""}`);
  }
  const context = state.context;
  const sampleRate = state.info?.output_sample_rate || 24000;
  const audio = context.createBuffer(1, pcm.length, sampleRate);
  const channel = audio.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
  const source = context.createBufferSource();
  source.buffer = audio;
  source.connect(context.destination);
  state.playAt = Math.max(state.playAt, context.currentTime + 0.015);
  source.start(state.playAt);
  state.playAt += audio.duration;
  state.playing.add(source);
  source.onended = () => {
    source.disconnect();
    state.playing.delete(source);
    if (!state.playing.size) $("playback-status").textContent = state.ttsFinished ? "播放完成" : "等待后续音频…";
    resumeContinuousIfReady();
  };
  state.audioSamples += pcm.length;
  $("metric-duration").textContent = `${(state.audioSamples / sampleRate).toFixed(2)} s`;
  $("playback-status").textContent = "正在播放回复…";
}

function handleEvent(message) {
  if (message.type === "hello") {
    state.voiceSessionId = message.session_id || null;
    bindCameraSession();
    log("握手", `会话 ${message.session_id || "—"} · 服务端 ${message.audio_params?.sample_rate} Hz`);
  } else if (message.type === "test.connected") {
    state.connected = true;
    connectionStatus("已连接", "connected");
    log("连接", "设备协议握手完成，可开始测试");
    updateControls();
  } else if (message.type === "test.error") {
    log("错误", message.message || "测试连接出错");
    showMessage("system", message.message || "测试连接出错");
  } else if (message.type === "test.continuous") {
    log("持续", message.state || "状态更新");
    if (state.continuous) state.continuousPhase = message.state;
    if (message.state === "interrupted" && state.continuous) {
      state.continuousResumePending = false;
      state.receiveAudio = false;
      state.ttsFinished = true;
      clearPlayback();
      $("playback-status").textContent = "已自动打断，正在听你说话…";
    }
    if (message.state === "reply_done" && state.continuous && state.recording) {
      state.continuousResumePending = true;
      state.ttsFinished = true;
      resumeContinuousIfReady();
    }
    if (message.state === "submitted" && state.continuous) beginTurn();
    if (message.state === "stopped") {
      void stopContinuous(false);
      $("recording-status").textContent = "持续对话已停止";
    }
    updateContinuousStatus();
    updateControls();
  } else if (message.type === "stt") {
    if (state.inputKind !== "text" || state.inputText !== message.text) showMessage("user", message.text || "（识别结果为空）");
    if (state.turnAt && state.inputKind !== "text") $("metric-stt").textContent = elapsed();
    log("识别", message.text || "（空）");
  } else if (message.type === "tts") {
    if (!state.receiveAudio) return;
    log("接收", `tts.${message.state}${message.text ? ` · ${message.text}` : ""}`);
    if (message.state === "start") {
      if (state.receiveAudio) state.ttsFinished = false;
    } else if (message.state === "sentence_start" && message.text) {
      if (!state.assistant) state.assistant = showMessage("assistant", "");
      state.assistant.textContent += message.text;
      $("transcript").scrollTop = $("transcript").scrollHeight;
    } else if (message.state === "stop") {
      // The last frame may still be playing. Only explicit abort clears it.
      state.ttsFinished = true;
      $("playback-status").textContent = state.playing.size ? "回复接收完成，继续播放…" : "回复结束";
    }
  } else if (message.type === "llm") {
    log("模型", `${message.text || ""}${message.emotion ? ` · ${message.emotion}` : ""}`);
  } else {
    log("接收", JSON.stringify(message));
  }
}

async function connect() {
  if (isFilePage() || state.socket) return;
  if (!$("device-id").value.trim()) {
    $("device-id").focus();
    return;
  }
  try { await audioContext(); } catch (error) { log("音频", `无法启用音频播放：${error.message}`); }
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  state.socket = socket;
  socket.binaryType = "arraybuffer";
  connectionStatus("正在连接", "busy");
  updateControls();
  socket.onopen = () => {
    socket.send(JSON.stringify({type: "connect", device_id: $("device-id").value.trim(), token: $("token").value}));
    log("连接", "测试桥接已连接，等待语音服务握手…");
    void loadHistory($("device-id").value.trim());
  };
  socket.onmessage = ({data}) => {
    if (data instanceof ArrayBuffer) playPCM(data);
    else {
      try { handleEvent(JSON.parse(data)); }
      catch (error) { log("错误", `无法处理服务消息：${error.message}`); }
    }
  };
  socket.onerror = () => log("错误", "WebSocket 连接异常，请检查本地测试服务");
  socket.onclose = () => {
    state.connected = false;
    state.voiceSessionId = null;
    bindCameraSession();
    state.socket = null;
    state.startingMic = false;
    state.continuous = false;
    state.continuousResumePending = false;
    clearPlayback();
    if (state.upload) state.upload.cancelled = true;
    void stopRecording(false);
    connectionStatus("未连接");
    log("连接", "连接已断开");
    updateControls();
  };
}

async function sendText(event) {
  event.preventDefault();
  const text = $("text-input").value.trim();
  if (!text || !state.connected || state.recording || state.upload || state.startingMic) return;
  await audioContext();
  if (!state.connected || state.recording || state.upload || state.startingMic) return;
  beginTurn("text", text);
  showMessage("user", text);
  send({type: "listen", state: "detect", text});
  $("text-input").value = "";
}

async function startRecording() {
  if (!state.connected || state.recording || state.upload || state.startingMic) return;
  const request = {};
  state.startingMic = request;
  updateControls();
  let stream;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("麦克风需要 localhost 或 HTTPS 页面");
    const context = await audioContext();
    if (state.startingMic !== request) return;
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}, video: false});
    if (!state.connected || state.startingMic !== request) { stream.getTracks().forEach((track) => track.stop()); return; }
    if (!state.workletLoaded) {
      await context.audioWorklet.addModule("pcm-worklet.js");
      state.workletLoaded = true;
    }
    if (!state.connected || state.startingMic !== request) { stream.getTracks().forEach((track) => track.stop()); return; }
    const maxSamples = state.continuous ? 0 : recordingLimitSamples();
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "pcm-recorder", {numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, processorOptions: {maxSamples}});
    const recording = {stream, source, worklet, started: performance.now(), stopping: false, finish: null, timer: null, continuous: state.continuous};
    state.recording = recording;
    worklet.port.onmessage = ({data}) => {
      if (data.type === "pcm" && state.connected) state.socket.send(data.buffer);
      if (data.type === "level") $("audio-level").value = Math.min(1, data.value * 3);
      if (data.type === "stopped") recording.finish?.();
      if (data.type === "limit") void stopRecording(true);
    };
    if (state.continuous) {
      state.receiveAudio = false;
      state.ttsFinished = true;
      state.continuousResumePending = false;
      clearPlayback();
      send({type: "abort"});
      send({type: "continuous.start", silence_ms: continuousSilenceMs(), max_turn_seconds: 30});
    }
    else startAudioInput();
    source.connect(worklet);
    worklet.connect(context.destination); // Worklet output is silence.
    const maximum = maxSamples ? maxSamples / 16000 : 0;
    recording.timer = setInterval(() => {
      const seconds = (performance.now() - recording.started) / 1000;
      if (recording.continuous) updateContinuousStatus();
      else $("recording-status").textContent = `正在录音 ${seconds.toFixed(1)} / ${maximum} 秒`;
    }, recording.continuous ? 1000 : 100);
    log("录音", `${state.continuous ? "开始持续对话" : "开始录音"} · 浏览器 ${context.sampleRate} Hz → 16 kHz`);
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    log("错误", `录音失败：${error.message}`);
    showMessage("system", `录音失败：${error.message}`);
  } finally {
    if (state.startingMic === request) state.startingMic = false;
    updateControls();
  }
}

async function stopRecording(submit) {
  const recording = state.recording;
  if (!recording) return;
  if (recording.stopping) {
    if (!submit) recording.cancelled = true;
    return;
  }
  recording.stopping = true;
  clearInterval(recording.timer);
  $("stop-record").disabled = true;
  if (submit && state.connected) {
    // Flush the worklet's partial frame before telling the service to finalize ASR.
    await new Promise((resolve) => {
      const timeout = setTimeout(resolve, 500);
      recording.finish = () => { clearTimeout(timeout); resolve(); };
      recording.worklet.port.postMessage("stop");
    });
  }
  recording.worklet.port.onmessage = null;
  recording.source.disconnect();
  recording.worklet.disconnect();
  recording.stream.getTracks().forEach((track) => track.stop());
  state.recording = null;
  $("audio-level").value = 0;
  $("recording-status").textContent = "录音结束";
  if (submit && state.connected && !recording.cancelled) {
    beginTurn();
    send({type: "listen", state: "stop"});
  }
  state.continuous = false;
  updateControls();
}

async function uploadAudio(file) {
  if (!file || !state.connected || state.recording || state.upload || state.startingMic) return;
  const job = {cancelled: false};
  state.upload = job;
  updateControls();
  let started = false;
  try {
    const context = await audioContext();
    const decoded = await context.decodeAudioData(await file.arrayBuffer());
    const maxSamples = recordingLimitSamples();
    const maximum = maxSamples / 16000;
    if (decoded.duration > maximum) throw new Error(`音频超过 ${maximum} 秒，请先裁剪`);
    if (job.cancelled || !state.connected) return;
    const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const samples = (await offline.startRendering()).getChannelData(0).subarray(0, maxSamples);
    if (job.cancelled || !state.connected) return;
    startAudioInput();
    started = true;
    log("上传", `${file.name} · ${decoded.duration.toFixed(2)} 秒 → 16 kHz 单声道`);
    for (let offset = 0; offset < samples.length; offset += 960) {
      if (job.cancelled || !state.connected) return;
      const count = Math.min(960, samples.length - offset);
      const pcm = new Int16Array(count);
      for (let i = 0; i < count; i++) {
        const value = Math.max(-1, Math.min(1, samples[offset + i]));
        pcm[i] = Math.round(value * (value < 0 ? 32768 : 32767));
      }
      state.socket.send(pcm.buffer);
      $("recording-status").textContent = `发送音频 ${Math.min(decoded.duration, (offset + count) / 16000).toFixed(1)} / ${decoded.duration.toFixed(1)} 秒`;
      await new Promise((resolve) => setTimeout(resolve, count / 16));
    }
    if (!job.cancelled && state.connected) {
      beginTurn();
      send({type: "listen", state: "stop"});
    }
  } catch (error) {
    if (started) send({type: "abort"});
    log("错误", `音频上传失败：${error.message}`);
    showMessage("system", `音频上传失败：${error.message}`);
  } finally {
    state.upload = null;
    $("recording-status").textContent = job.cancelled ? "音频发送已取消" : "音频文件处理结束";
    updateControls();
  }
}

async function abort() {
  state.startingMic = false;
  const wasContinuous = state.continuous && Boolean(state.recording);
  if (state.upload) state.upload.cancelled = true;
  state.receiveAudio = false;
  state.ttsFinished = true;
  state.continuousResumePending = false;
  clearPlayback();
  send({type: "abort"});
  if (wasContinuous && state.connected) {
    state.continuousPhase = "started";
    send({type: "continuous.start", silence_ms: continuousSilenceMs(), max_turn_seconds: 30});
    $("recording-status").textContent = "持续监听已恢复";
    updateControls();
    return;
  }
  state.continuous = false;
  await stopRecording(false);
  $("recording-status").textContent = "当前交互已打断";
  updateControls();
}

async function startContinuous() {
  if (!state.connected || state.recording || state.upload || state.startingMic) return;
  state.continuous = true;
  state.continuousPhase = "started";
  await startRecording();
  if (!state.recording) state.continuous = false;
  updateControls();
}

async function stopContinuous(sendCommand = true) {
  if (!state.continuous) return;
  state.startingMic = false;
  state.continuous = false;
  state.continuousResumePending = false;
  state.receiveAudio = false;
  state.ttsFinished = true;
  clearPlayback();
  if (sendCommand) send({type: "continuous.stop"});
  await stopRecording(false);
  $("recording-status").textContent = "持续对话已停止";
  updateControls();
}

function submitRecording() {
  if (state.continuous) send({type: "continuous.submit"});
  else void stopRecording(true);
}

function startCamera() {
  if (state.cameraStream) return Promise.resolve(true);
  if (state.cameraStarting) return state.cameraStartPromise;
  state.cameraStartPromise = openCamera();
  return state.cameraStartPromise;
}

async function openCamera() {
  state.cameraStarting = true; $("camera-start").disabled = true; $("camera-status").textContent = "正在请求摄像头权限…";
  $("camera-stop").disabled = false;
  const request = ++state.cameraRequest;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("摄像头需要 localhost 或 HTTPS 页面");
    const stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "user", width: {ideal: 1280}, height: {ideal: 720}}, audio: false});
    if (request !== state.cameraRequest) { stream.getTracks().forEach((track) => track.stop()); return false; }
    state.cameraStream = stream;
    $("camera-preview").srcObject = state.cameraStream;
    $("camera-status").textContent = "本机摄像头已开启，可以抓取快照";
    $("camera-shot").disabled = false; $("camera-stop").disabled = false; $("camera-start").disabled = true;
    log("摄像头", "摄像头预览已开启");
    return true;
  } catch (error) {
    if (request === state.cameraRequest) {
      $("camera-status").textContent = `摄像头打开失败：${error.message}`;
      log("错误", `摄像头失败：${error.message}`);
    }
    return false;
  } finally {
    if (request === state.cameraRequest) {
      state.cameraStarting = false;
      if (!state.cameraStream) { $("camera-start").disabled = false; $("camera-stop").disabled = true; }
    }
  }
}

function stopCamera() {
  state.cameraRequest++;
  state.cameraStarting = false;
  state.cameraStream?.getTracks().forEach((track) => track.stop());
  state.cameraStream = null; $("camera-preview").srcObject = null;
  $("camera-shot").disabled = true; $("camera-stop").disabled = true; $("camera-start").disabled = false;
  $("camera-status").textContent = "摄像头已关闭";
  return {success: true, message: "摄像头已关闭"};
}

async function captureCamera() {
  const video = $("camera-preview");
  if (!state.cameraStream) return {success: false, error: "摄像头未开启"};
  // Dimensions arrive before the first usable frame on a newly opened camera.
  if (video.readyState < 2 || !video.videoWidth) {
    await new Promise((resolve) => {
      const finish = () => {
        clearTimeout(timeout);
        video.removeEventListener("loadeddata", finish);
        resolve();
      };
      const timeout = setTimeout(finish, 3000);
      video.addEventListener("loadeddata", finish, {once: true});
    });
  }
  if (!state.cameraStream) return {success: false, error: "摄像头已关闭"};
  if (video.readyState < 2 || !video.videoWidth) return {success: false, error: "摄像头画面尚未就绪"};
  const canvas = $("camera-canvas");
  const scale = Math.min(1, 960 / video.videoWidth);
  canvas.width = Math.round(video.videoWidth * scale); canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const jpeg = canvas.toDataURL("image/jpeg", 0.78);
  const preview = $("camera-snapshot"); preview.src = jpeg; preview.hidden = false;
  $("camera-status").textContent = `快照已抓取（${canvas.width}×${canvas.height}），仅保留在本页`;
  log("摄像头", `快照已抓取 · ${canvas.width}×${canvas.height}`);
  const link = document.createElement("a"); link.href = jpeg; link.download = `deskbot-camera-${new Date().toISOString().replaceAll(":", "-")}.jpg`; link.textContent = "下载快照";
  const side = $("camera-status").parentElement; side.querySelector(".snapshot-download")?.remove(); link.className = "snapshot-download"; side.append(link);
  return {success: true, message: "照片已拍摄", photo_data: jpeg, mime_type: "image/jpeg", photo_width: canvas.width, photo_height: canvas.height};
}

async function handleCameraMcpCall(message, socket = state.cameraMcpSocket) {
  let result;
  log("MCP", `调用 ${message.tool}`);
  try {
    if (message.tool === "self_camera_start") {
      result = {success: await startCamera()};
      if (!result.success) result.error = $("camera-status").textContent;
    } else if (message.tool === "self_camera_take_photo" || message.tool === "self.camera.take_photo") {
      if (!state.cameraStream && !(await startCamera())) {
        result = {success: false, error: $("camera-status").textContent};
      } else {
        result = await captureCamera();
        if (result.success) $("camera-status").textContent = state.info?.vision_model
          ? `快照已发送给视觉模型 ${state.info.vision_model} 分析`
          : "快照已返回给 MCP 调用方";
      }
    } else if (message.tool === "self_camera_stop") {
      result = stopCamera();
    } else if (message.tool === "self_camera_status") {
      result = {success: true, active: Boolean(state.cameraStream)};
    } else {
      result = {success: false, error: `未知摄像头工具：${message.tool}`};
    }
  } catch (error) {
    result = {success: false, error: error.message};
  }
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "camera.mcp.result", id: message.id, result}));
  log("MCP", `${message.tool} · ${result.success ? "成功" : result.error || "失败"}`);
}

function bindCameraSession() {
  state.cameraBound = false;
  const socket = state.cameraMcpSocket;
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: "camera.mcp.bind", session_id: state.voiceSessionId}));
  updateCameraCapability();
}

function updateCameraCapability() {
  let description;
  if (isFilePage()) description = "请从本地服务入口打开页面";
  else if (!state.info) description = "等待服务配置…";
  else if (!state.info.camera_tools_enabled) description = "可本地预览 · 浏览器对话工具未启用";
  else if (state.cameraMcpSocket?.readyState !== WebSocket.OPEN) description = "浏览器工具通道未连接";
  else if (!state.voiceSessionId || !state.cameraBound) description = "工具通道已连接 · 等待本页对话绑定";
  else description = "本页摄像头工具就绪";
  if (state.info) description += state.info.vision_model ? ` · 视觉模型 ${state.info.vision_model}` : " · 画面分析未启用";
  $("camera-capability").textContent = description;
}

function connectCameraMcp() {
  if (isFilePage() || typeof WebSocket !== "function" || state.cameraMcpSocket) return;
  const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/camera-mcp`);
  state.cameraMcpSocket = socket;
  socket.onopen = () => {
    bindCameraSession();
    log("MCP", "摄像头通道已连接");
  };
  socket.onmessage = ({data}) => {
    try {
      const message = JSON.parse(data);
      if (message.type === "camera.mcp.call") void handleCameraMcpCall(message, socket);
      else if (message.type === "camera.mcp.bound") {
        state.cameraBound = Boolean(message.session_id && message.session_id === state.voiceSessionId);
        updateCameraCapability();
        if (state.cameraBound) log("MCP", "本机摄像头工具已绑定本页对话");
      }
    } catch (error) { log("MCP", `摄像头工具消息无效：${error.message}`); }
  };
  socket.onclose = () => {
    if (state.cameraMcpSocket !== socket) return;
    state.cameraMcpSocket = null;
    state.cameraBound = false;
    updateCameraCapability();
    if (!state.closing) state.cameraReconnectTimer = setTimeout(connectCameraMcp, 2000);
  };
}

async function refreshHealth() {
  if (isFilePage()) return;
  $("refresh-health").disabled = true;
  try {
    const [infoResponse, healthResponse] = await Promise.all([fetch("/api/info"), fetch("/api/health")]);
    if (!infoResponse.ok || !healthResponse.ok) throw new Error("本地测试服务响应异常");
    const [info, health] = await Promise.all([infoResponse.json(), healthResponse.json()]);
    state.info = info;
    updateCameraCapability();
    $("ws-address").textContent = info.websocket_url;
    $("ota-address").textContent = info.ota_url;
    $("health-status").replaceChildren();
    for (const [key, label] of [["voice", "语音"], ["ota", "OTA"], ["llm", "LLM"]]) {
      if (!health[key]) continue;
      const badge = document.createElement("span");
      badge.className = `health-item${health[key].ok ? " ok" : ""}`;
      badge.textContent = `${label}：${health[key].detail}`;
      $("health-status").append(badge);
    }
    const readiness = info.readiness || {};
    const checks = [["ASR 模型", readiness.asr_model], ["VAD 模型", readiness.vad_model], ["FFmpeg", readiness.ffmpeg], ...Object.entries(readiness.packages || {})];
    const missing = checks.filter(([, ok]) => !ok);
    $("readiness-summary").textContent = `${missing.length ? `语音运行环境缺少 ${missing.length} 项` : "语音模型文件与依赖检查通过"} · 对话模型 ${info.llm_model}`;
    $("readiness-details").replaceChildren();
    for (const [name, ok] of checks) {
      const item = document.createElement("span");
      item.className = ok ? "ok" : "";
      item.textContent = `${ok ? "✓" : "缺少"} ${name}`;
      $("readiness-details").append(item);
    }
    log("检查", "已更新服务状态与本机依赖；模型效果需实际对话验证");
  } catch (error) {
    $("health-status").textContent = error.message;
    log("错误", `状态检查失败：${error.message}`);
  } finally { $("refresh-health").disabled = false; }
}

$("connect").addEventListener("click", () => void connect());
$("disconnect").addEventListener("click", () => { void abort(); state.socket?.close(); });
$("text-form").addEventListener("submit", (event) => void sendText(event));
$("text-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("text-form").requestSubmit();
});
$("record").addEventListener("click", () => void startRecording());
$("stop-record").addEventListener("click", submitRecording);
$("continuous").addEventListener("click", () => void startContinuous());
$("stop-continuous").addEventListener("click", () => void stopContinuous());
$("abort").addEventListener("click", () => void abort());
$("upload").addEventListener("click", () => $("audio-file").click());
$("audio-file").addEventListener("change", () => {
  void uploadAudio($("audio-file").files[0]);
  $("audio-file").value = "";
});
$("refresh-health").addEventListener("click", () => void refreshHealth());
$("refresh-devices").addEventListener("click", () => void refreshDevices());
$("camera-start").addEventListener("click", () => void startCamera());
$("camera-shot").addEventListener("click", () => void captureCamera());
$("camera-stop").addEventListener("click", stopCamera);
$("clear-dialogue").addEventListener("click", () => { $("transcript").replaceChildren(); state.assistant = null; });
$("load-history").addEventListener("click", () => void loadHistory());
$("clear-log").addEventListener("click", () => { state.logs = []; $("event-log").textContent = ""; });
$("export-log").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([state.logs.join("\n")], {type: "text/plain;charset=utf-8"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = `deskbot-voice-${new Date().toISOString().replaceAll(":", "-")}.log`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener("beforeunload", () => {
  state.closing = true;
  clearInterval(state.devicesTimer);
  clearTimeout(state.cameraReconnectTimer);
  state.recording?.stream.getTracks().forEach((track) => track.stop());
  state.cameraStream?.getTracks().forEach((track) => track.stop());
  state.cameraMcpSocket?.close();
  state.socket?.close();
});
if (isFilePage()) {
  $("file-warning").hidden = false;
  $("health-status").textContent = "当前为本地文件页面，尚未连接测试服务。";
  $("ws-address").textContent = "请打开本地服务入口";
  $("readiness-summary").textContent = "等待通过服务入口检查";
  $("device-list").replaceChildren(deviceText("p", "empty-state", "请打开本地服务入口查看设备"));
  $("device-status").textContent = "未发起设备状态查询。";
  $("camera-status").textContent = "请通过上方入口打开本机摄像头测试。";
  for (const id of ["connect", "refresh-health", "refresh-devices", "load-history", "camera-start"]) $(id).disabled = true;
  updateCameraCapability();
} else {
  void refreshHealth();
  void refreshDevices();
  state.devicesTimer = setInterval(() => { if (!document.hidden) void refreshDevices(); }, 2000);
  connectCameraMcp();
}
