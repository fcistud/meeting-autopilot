const state = {
  sessionId: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `session-${Date.now()}`,
  currentPlan: [],
  recording: {
    active: false,
    stream: null,
    audioContext: null,
    sourceNode: null,
    processorNode: null,
    muteNode: null,
    sampleRate: 16000,
    targetSampleRate: 16000,
    buffers: [],
    tailSamples: new Float32Array(0),
    transcriptContext: "",
    flushTimer: null,
    pending: Promise.resolve(),
  },
};

const ui = {
  sessionBadge: document.getElementById("sessionBadge"),
  transcribeStatus: document.getElementById("transcribeStatus"),
  sourceBadge: document.getElementById("sourceBadge"),
  routeReason: document.getElementById("routeReason"),
  transcriptInput: document.getElementById("transcriptInput"),
  expectedCallsInput: document.getElementById("expectedCallsInput"),
  thresholdInput: document.getElementById("thresholdInput"),
  thresholdValue: document.getElementById("thresholdValue"),
  allowCloudInput: document.getElementById("allowCloudInput"),
  planBtn: document.getElementById("planBtn"),
  executeBtn: document.getElementById("executeBtn"),
  liveToggleBtn: document.getElementById("liveToggleBtn"),
  clearTranscriptBtn: document.getElementById("clearTranscriptBtn"),
  planList: document.getElementById("planList"),
  executionLog: document.getElementById("executionLog"),
  fallbackViz: document.getElementById("fallbackViz"),
  latencyCurrent: document.getElementById("latencyCurrent"),
  latencyAvg: document.getElementById("latencyAvg"),
  onDeviceRatio: document.getElementById("onDeviceRatio"),
  f1StyleAvg: document.getElementById("f1StyleAvg"),
  exactF1Avg: document.getElementById("exactF1Avg"),
  turnCount: document.getElementById("turnCount"),
};

function setPill(el, text, mode = "neutral") {
  el.textContent = text;
  el.className = `pill ${mode}`;
}

function setTranscribeState(text, mode) {
  setPill(ui.transcribeStatus, text, mode);
}

function addLog(message) {
  const item = document.createElement("div");
  item.className = "log-item";
  const now = new Date().toLocaleTimeString();
  item.innerHTML = `<strong>${message}</strong> <span>(${now})</span>`;
  ui.executionLog.prepend(item);
}

function jsonPretty(value) {
  return JSON.stringify(value, null, 2);
}

function parseExpectedCalls() {
  const raw = ui.expectedCallsInput.value.trim();
  if (!raw) {
    return null;
  }
  return JSON.parse(raw);
}

function updateThresholdLabel() {
  ui.thresholdValue.textContent = Number(ui.thresholdInput.value).toFixed(2);
}

function toPercent(confidence) {
  if (confidence === null || confidence === undefined || Number.isNaN(confidence)) {
    return 0;
  }
  return Math.max(0, Math.min(100, Number(confidence) * 100));
}

function renderPlan(previewSteps) {
  ui.planList.innerHTML = "";
  if (!previewSteps || previewSteps.length === 0) {
    const empty = document.createElement("article");
    empty.className = "plan-item";
    empty.innerHTML = "<strong>No executable actions found.</strong><p>Try clearer verbs like remind, message, timer, or search.</p>";
    ui.planList.appendChild(empty);
    return;
  }

  previewSteps.forEach((step) => {
    const article = document.createElement("article");
    article.className = "plan-item";
    const title = document.createElement("strong");
    title.textContent = `${step.index}. ${step.tool}`;

    const desc = document.createElement("p");
    desc.textContent = step.description;

    const json = document.createElement("pre");
    json.textContent = jsonPretty(step.arguments);

    article.appendChild(title);
    article.appendChild(desc);
    article.appendChild(json);
    ui.planList.appendChild(article);
  });
}

function renderFallback(route) {
  ui.fallbackViz.innerHTML = "";
  if (!route || !route.stages) {
    return;
  }

  route.stages.forEach((stage) => {
    const stageEl = document.createElement("article");
    stageEl.className = "fallback-stage";
    const pct = toPercent(stage.confidence);
    const thresholdPct = toPercent(route.threshold);

    stageEl.innerHTML = `
      <header>
        <h4>${stage.label}</h4>
        <span class="pill ${stage.selected ? "local" : "neutral"}">
          ${stage.confidence === null ? "n/a" : stage.confidence.toFixed(2)}
        </span>
      </header>
      <div class="stage-meter">
        <div class="stage-fill" style="width: ${pct}%;"></div>
        <span class="stage-threshold" style="left: ${thresholdPct}%;"></span>
      </div>
      <p>Status: <strong>${stage.status}</strong>. ${stage.details}</p>
    `;
    ui.fallbackViz.appendChild(stageEl);
  });
}

function updateMetrics(metrics) {
  if (!metrics) {
    return;
  }
  ui.latencyCurrent.textContent = `${metrics.latency_ms_current} ms`;
  ui.latencyAvg.textContent = `${metrics.latency_ms_avg} ms`;
  ui.onDeviceRatio.textContent = `${metrics.on_device_ratio}%`;
  ui.f1StyleAvg.textContent = metrics.f1_style_avg.toFixed(3);
  ui.exactF1Avg.textContent = metrics.exact_f1_avg === null ? "--" : metrics.exact_f1_avg.toFixed(3);
  ui.turnCount.textContent = String(metrics.turns);
}

async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}

async function generatePlan() {
  const transcript = ui.transcriptInput.value.trim();
  if (!transcript) {
    addLog("Transcript is empty.");
    return;
  }

  let expectedCalls = null;
  try {
    expectedCalls = parseExpectedCalls();
  } catch {
    addLog("Expected calls JSON is invalid.");
    return;
  }

  ui.planBtn.disabled = true;
  try {
    const data = await postJSON("/api/route", {
      session_id: state.sessionId,
      transcript,
      confidence_threshold: Number(ui.thresholdInput.value),
      allow_cloud: ui.allowCloudInput.checked,
      expected_calls: expectedCalls,
    });

    state.currentPlan = data.plan || [];
    renderPlan(data.preview_steps || []);
    renderFallback(data.route);
    updateMetrics(data.live_metrics);

    ui.routeReason.textContent = data.route.reason;
    if (data.source === "cloud") {
      setPill(ui.sourceBadge, `cloud @ ${data.confidence}`, "cloud");
    } else {
      setPill(ui.sourceBadge, `on-device @ ${data.confidence}`, "local");
    }

    ui.executeBtn.disabled = state.currentPlan.length === 0;
    addLog(`Plan generated (${state.currentPlan.length} calls, ${data.total_time_ms}ms).`);
  } catch (err) {
    addLog(`Plan generation failed: ${err.message}`);
  } finally {
    ui.planBtn.disabled = false;
  }
}

async function executePlan() {
  if (!state.currentPlan.length) {
    addLog("No plan to execute.");
    return;
  }

  ui.executeBtn.disabled = true;
  try {
    const data = await postJSON("/api/execute", {
      session_id: state.sessionId,
      plan: state.currentPlan,
    });

    data.results.forEach((result, index) => {
      addLog(`#${index + 1} ${result.tool}: ${result.result}`);
    });
    addLog(`Executed ${data.executed_count} actions.`);
  } catch (err) {
    addLog(`Execution failed: ${err.message}`);
  } finally {
    ui.executeBtn.disabled = false;
  }
}

function mergeBuffers(buffers) {
  const length = buffers.reduce((sum, arr) => sum + arr.length, 0);
  const output = new Float32Array(length);
  let offset = 0;
  buffers.forEach((chunk) => {
    output.set(chunk, offset);
    offset += chunk.length;
  });
  return output;
}

function concatFloat32(a, b) {
  const output = new Float32Array(a.length + b.length);
  output.set(a, 0);
  output.set(b, a.length);
  return output;
}

function downsampleBuffer(samples, inputRate, outputRate) {
  if (!samples || !samples.length) {
    return new Float32Array(0);
  }
  if (!inputRate || inputRate <= 0 || inputRate === outputRate) {
    return samples;
  }
  if (outputRate > inputRate) {
    return samples;
  }

  const ratio = inputRate / outputRate;
  const newLength = Math.max(1, Math.round(samples.length / ratio));
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetInput = 0;

  while (offsetResult < newLength) {
    const nextOffsetInput = Math.min(samples.length, Math.round((offsetResult + 1) * ratio));
    let accum = 0;
    let count = 0;
    for (let i = offsetInput; i < nextOffsetInput; i += 1) {
      accum += samples[i];
      count += 1;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult += 1;
    offsetInput = nextOffsetInput;
  }
  return result;
}

function computeRms(samples) {
  if (!samples || !samples.length) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const v = samples[i];
    sum += v * v;
  }
  return Math.sqrt(sum / samples.length);
}

function buildPromptContext(text, maxWords = 48) {
  const words = String(text || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) {
    return "";
  }
  return words.slice(-maxWords).join(" ");
}

function stitchTranscript(existingText, incomingText) {
  const existing = String(existingText || "").trim();
  const incoming = String(incomingText || "").trim();
  if (!incoming) {
    return existing;
  }
  if (!existing) {
    return incoming;
  }

  const existingWords = existing.split(/\s+/);
  const incomingWords = incoming.split(/\s+/);
  const maxOverlap = Math.min(10, existingWords.length, incomingWords.length);
  let overlap = 0;

  for (let k = maxOverlap; k >= 1; k -= 1) {
    const tail = existingWords.slice(existingWords.length - k).join(" ").toLowerCase();
    const head = incomingWords.slice(0, k).join(" ").toLowerCase();
    if (tail === head) {
      overlap = k;
      break;
    }
  }

  return `${existing} ${incomingWords.slice(overlap).join(" ")}`.trim().replace(/\s+/g, " ");
}

function writeString(view, offset, text) {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}

function floatTo16BitPCM(view, offset, input) {
  for (let i = 0; i < input.length; i += 1, offset += 2) {
    const sample = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
}

function encodeWav(samples, sampleRate) {
  const numChannels = 1;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, dataSize, true);
  floatTo16BitPCM(view, 44, samples);
  return new Blob([view], { type: "audio/wav" });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = String(reader.result || "");
      const parts = result.split(",");
      resolve(parts[1] || "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function sendAudioChunk(samples, sampleRate, promptContext = "") {
  const wavBlob = encodeWav(samples, sampleRate);
  const audioBase64 = await blobToBase64(wavBlob);

  const data = await postJSON("/api/transcribe", {
    session_id: state.sessionId,
    audio_wav_base64: audioBase64,
    prompt: promptContext,
  });

  const text = (data.transcript || "").trim();
  if (text) {
    const stitched = stitchTranscript(ui.transcriptInput.value, text);
    ui.transcriptInput.value = stitched;
    state.recording.transcriptContext = buildPromptContext(stitched, 48);
    addLog(`Transcribed chunk (${data.engine_time_ms}ms): "${text}"`);
  }
}

async function flushAudioBuffers(force = false) {
  const buffers = state.recording.buffers;
  if (!buffers.length) {
    return;
  }
  state.recording.buffers = [];

  const merged = mergeBuffers(buffers);
  const inputRate = state.recording.sampleRate || 16000;
  const targetRate = state.recording.targetSampleRate || 16000;
  const downsampled = downsampleBuffer(merged, inputRate, targetRate);

  // Keep chunks long enough to improve Whisper quality.
  if (!force && downsampled.length < Math.floor(targetRate * 2.2)) {
    return;
  }

  const rms = computeRms(downsampled);
  if (!force && rms < 0.003) {
    return;
  }

  let chunkToSend = downsampled;
  if (state.recording.tailSamples && state.recording.tailSamples.length) {
    chunkToSend = concatFloat32(state.recording.tailSamples, downsampled);
  }

  // Add a small overlap so words at chunk boundaries are preserved.
  const overlapSamples = Math.min(Math.floor(targetRate * 0.35), downsampled.length);
  state.recording.tailSamples = downsampled.slice(downsampled.length - overlapSamples);
  const promptContext = state.recording.transcriptContext || "";

  state.recording.pending = state.recording.pending
    .then(() => sendAudioChunk(chunkToSend, targetRate, promptContext))
    .catch((err) => {
      addLog(`Transcription chunk failed: ${err.message}`);
    });
}

async function startLiveCapture() {
  if (state.recording.active) {
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    const audioContext = new AudioCtx();
    if (audioContext.state === "suspended") {
      await audioContext.resume();
    }
    const sourceNode = audioContext.createMediaStreamSource(stream);
    const processorNode = audioContext.createScriptProcessor(4096, 1, 1);
    const muteNode = audioContext.createGain();
    muteNode.gain.value = 0;

    processorNode.onaudioprocess = (event) => {
      if (!state.recording.active) {
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const copy = new Float32Array(input.length);
      copy.set(input);
      state.recording.buffers.push(copy);
    };

    sourceNode.connect(processorNode);
    processorNode.connect(muteNode);
    muteNode.connect(audioContext.destination);

    state.recording.active = true;
    state.recording.stream = stream;
    state.recording.audioContext = audioContext;
    state.recording.sourceNode = sourceNode;
    state.recording.processorNode = processorNode;
    state.recording.muteNode = muteNode;
    state.recording.sampleRate = audioContext.sampleRate;
    state.recording.targetSampleRate = 16000;
    state.recording.buffers = [];
    state.recording.tailSamples = new Float32Array(0);
    state.recording.transcriptContext = buildPromptContext(ui.transcriptInput.value, 48);
    state.recording.flushTimer = setInterval(() => {
      flushAudioBuffers(false);
    }, 5000);

    ui.liveToggleBtn.textContent = "Stop Live Capture";
    setTranscribeState("live", "live");
    addLog("Live capture started.");
  } catch (err) {
    addLog(`Mic start failed: ${err.message}`);
    setTranscribeState("error", "cloud");
  }
}

async function stopLiveCapture() {
  if (!state.recording.active) {
    return;
  }

  state.recording.active = false;
  if (state.recording.flushTimer) {
    clearInterval(state.recording.flushTimer);
    state.recording.flushTimer = null;
  }

  await flushAudioBuffers(true);
  await state.recording.pending;

  if (state.recording.processorNode) {
    state.recording.processorNode.disconnect();
  }
  if (state.recording.sourceNode) {
    state.recording.sourceNode.disconnect();
  }
  if (state.recording.muteNode) {
    state.recording.muteNode.disconnect();
  }
  if (state.recording.stream) {
    state.recording.stream.getTracks().forEach((track) => track.stop());
  }
  if (state.recording.audioContext && state.recording.audioContext.state !== "closed") {
    await state.recording.audioContext.close();
  }

  state.recording.stream = null;
  state.recording.audioContext = null;
  state.recording.sourceNode = null;
  state.recording.processorNode = null;
  state.recording.muteNode = null;
  state.recording.buffers = [];
  state.recording.tailSamples = new Float32Array(0);
  state.recording.pending = Promise.resolve();

  ui.liveToggleBtn.textContent = "Start Live Capture";
  setTranscribeState("idle", "neutral");
  addLog("Live capture stopped.");
}

async function toggleLiveCapture() {
  if (state.recording.active) {
    await stopLiveCapture();
  } else {
    await startLiveCapture();
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!data.whisper_weights_found) {
      addLog(`Whisper weights not found at ${data.whisper_model_path}`);
      setTranscribeState("weights missing", "cloud");
    } else {
      setTranscribeState("idle", "neutral");
    }
  } catch {
    addLog("Health check failed.");
  }
}

function init() {
  ui.sessionBadge.textContent = `session ${state.sessionId.slice(0, 8)}`;
  updateThresholdLabel();

  ui.thresholdInput.addEventListener("input", updateThresholdLabel);
  ui.planBtn.addEventListener("click", generatePlan);
  ui.executeBtn.addEventListener("click", executePlan);
  ui.liveToggleBtn.addEventListener("click", toggleLiveCapture);
  ui.clearTranscriptBtn.addEventListener("click", () => {
    ui.transcriptInput.value = "";
    state.recording.transcriptContext = "";
    addLog("Transcript cleared.");
  });

  checkHealth();
}

init();
