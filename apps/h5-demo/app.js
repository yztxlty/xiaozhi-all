(function startYouguangDemo() {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const PHASE_COPY = {
    disconnected: ['等待连接', '点击下方按钮开始通话'],
    connecting: ['正在连接幽光…', '正在建立安全语音链路'],
    preparing: ['正在准备麦克风…', '首次加载语音检测模型，请稍候'],
    listening: ['我在听', '直接说话，幽光会认真听'],
    hearing: ['听到你了', '正在接收你的声音'],
    recognizing: ['听懂中…', '正在识别你说的话'],
    thinking: ['想一想…', '幽光正在组织回答'],
    speaking: ['幽光说话中', '你可以随时开口打断'],
    error: ['暂时走神了', '请稍后重试或查看调试日志'],
  };
  const ASR_SAMPLE_RATE = 16000;
  const TTS_SAMPLE_RATE = 16000;
  // LiveKit 式打断确认需要 500 ms；保留 1 s 单源前滚音频，避免确认期间丢掉句首。
  const PRE_ROLL_MS = 1000;
  const VOICE_ASSET_BASE = new URL('vendor/voice/', document.baseURI).href;
  const VAD_BASE = VOICE_ASSET_BASE;
  const ONNX_BASE = VOICE_ASSET_BASE;
  const ORT_SCRIPT_URL = `${ONNX_BASE}ort.wasm.min.js`;
  const VAD_SCRIPT_URL = `${VAD_BASE}bundle.min.js`;
  const VOICE_PRELOAD_ASSETS = [
    `${VAD_BASE}silero_vad_v5.onnx`,
    `${VAD_BASE}vad.worklet.bundle.min.js`,
    `${ONNX_BASE}ort-wasm-simd-threaded.mjs`,
    `${ONNX_BASE}ort-wasm-simd-threaded.wasm`,
  ];

  let callState = CallCore.createInitialState();
  let ws = null;
  let connectPromise = null;
  let audioCtx = null;
  let callStartedAt = 0;
  let callTimer = null;
  let speechEndedAt = 0;
  let firstAudioSeen = false;
  let partialMessage = null;
  let assistantMessage = null;
  let toastTimer = null;
  let voiceLibrariesPromise = null;
  let activeReplay = null;
  let binaryFrameChain = Promise.resolve();
  let shouldReconnect = false;
  let reconnectTimer = null;
  let reconnectAttempt = 0;
  let heartbeatTimer = null;
  let lastPongAt = 0;
  const voiceTurnGate = CallCore.createVoiceTurnGate();
  const interruptionGate = CallCore.createInterruptionGate({ minDurationMs: 500 });
  const mascotController = window.MascotController
    ? window.MascotController.createMascotController($('mascot'), {
      preload: (asset) => { const image = new Image(); image.src = asset.base; },
    })
    : null;

  function loadExternalScript(url, ready) {
    if (ready()) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = url;
      script.async = true;
      script.onload = () => ready() ? resolve() : reject(new Error(`语音库初始化失败：${url}`));
      script.onerror = () => reject(new Error(`语音库加载失败：${url}`));
      document.head.append(script);
    });
  }

  function loadVoiceLibraries() {
    if (window.vad && window.vad.MicVAD) return Promise.resolve();
    if (!voiceLibrariesPromise) {
      voiceLibrariesPromise = loadExternalScript(ORT_SCRIPT_URL, () => Boolean(window.ort))
        .then(() => loadExternalScript(VAD_SCRIPT_URL, () => Boolean(window.vad && window.vad.MicVAD)))
        .catch((error) => { voiceLibrariesPromise = null; throw error; });
    }
    return voiceLibrariesPromise;
  }

  function warmVoiceAssets() {
    return Promise.allSettled(VOICE_PRELOAD_ASSETS.map(async (url) => {
      const response = await fetch(url, { cache: 'force-cache', mode: 'cors' });
      if (!response.ok) throw new Error(`语音资源预加载失败：${response.status}`);
      return response.arrayBuffer();
    }));
  }

  function appendDebug(type, detail = '') {
    const row = document.createElement('div');
    row.className = 'debug-item';
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    row.innerHTML = `<span class="debug-time">${time}</span><span class="debug-type"></span><span class="debug-detail"></span>`;
    row.querySelector('.debug-type').textContent = type;
    row.querySelector('.debug-detail').textContent = CallCore.sanitizeLog(detail);
    $('debug-log').append(row);
    $('debug-log').scrollTop = $('debug-log').scrollHeight;
  }

  function toast(message) {
    $('toast').textContent = message;
    $('toast').classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $('toast').classList.remove('show'), 2200);
  }

  function scrollMessagesToLatest() {
    requestAnimationFrame(() => {
      const messages = $('messages');
      messages.scrollTop = messages.scrollHeight;
    });
  }

  function addMessage(role, text, kind = '') {
    const article = document.createElement('article');
    article.className = `message ${role} ${kind}`.trim();
    const content = document.createElement('div');
    content.className = 'message-content';
    if (role === 'assistant') {
      const avatar = document.createElement('div');
      avatar.className = 'message-avatar';
      avatar.textContent = '✦';
      article.append(avatar);
      const name = document.createElement('span');
      name.className = 'message-name';
      name.textContent = '幽光';
      content.append(name);
    }
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.textContent = text;
    content.append(bubble);
    article.append(content);
    $('messages').append(article);
    scrollMessagesToLatest();
    return { article, bubble, content };
  }

  function setState(event) {
    callState = CallCore.reduceCallState(callState, event);
    mascotController?.setPhase(callState.phase);
    const [label, hint] = PHASE_COPY[callState.phase] || PHASE_COPY.error;
    $('call-status').textContent = label;
    $('call-hint').textContent = hint;
    $('call-screen').dataset.phase = callState.phase;
    $('waveform').classList.toggle('is-active', ['listening', 'hearing', 'recognizing', 'speaking'].includes(callState.phase));
    $('connection-label').textContent = callState.connected ? '在线 · 随时可以聊' : label;
    $('connection-dot').classList.toggle('online', callState.connected);
    if (callState.userText) $('user-subtitle').textContent = `你：${callState.userText}`;
    if (callState.assistantText) $('assistant-subtitle').textContent = callState.assistantText;
  }

  function ensureAudioContext() {
    if (!audioCtx || audioCtx.state === 'closed') audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') void audioCtx.resume();
    return audioCtx;
  }

  async function ensureAudioContextRunning(context = ensureAudioContext()) {
    if (context.state !== 'running') await context.resume();
    if (context.state !== 'running') throw new Error(`浏览器音频引擎未启动（${context.state}）`);
    return context;
  }

  function unlockAudioPlayback() {
    const context = ensureAudioContext();
    const buffer = context.createBuffer(1, 1, TTS_SAMPLE_RATE);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.start(0);
    if (context.state !== 'running') {
      return context.resume().then(() => appendDebug('音频播放', '移动端音频输出已解锁'));
    }
    appendDebug('音频播放', '音频输出已就绪');
    return Promise.resolve();
  }

  function stopConnectionMaintenance() {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  function scheduleReconnect() {
    if (!shouldReconnect || reconnectTimer) return;
    const delay = Math.min(10000, 500 * (2 ** reconnectAttempt));
    reconnectAttempt += 1;
    appendDebug('连接恢复', `${delay} ms 后重新建立语音通道`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect().catch((error) => {
        appendDebug('连接恢复失败', error.message || String(error));
        scheduleReconnect();
      });
    }, delay);
  }

  function startHeartbeat() {
    clearInterval(heartbeatTimer);
    lastPongAt = performance.now();
    const beat = () => {
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (performance.now() - lastPongAt > 25000) {
        appendDebug('连接检测', '心跳超时，主动重建语音通道');
        ws.close(4000, 'heartbeat timeout');
        return;
      }
      ws.send(JSON.stringify({ type: 'ping' }));
    };
    beat();
    heartbeatTimer = setInterval(beat, 10000);
  }

  function connect() {
    if (ws && ws.readyState === WebSocket.OPEN && callState.connected) return Promise.resolve();
    if (connectPromise) return connectPromise;
    setState({ type: 'connection.opening' });
    appendDebug('连接', CallCore.buildWebSocketUrl(location));

    connectPromise = new Promise((resolve, reject) => {
      let settled = false;
      const socket = new WebSocket(CallCore.buildWebSocketUrl(location));
      ws = socket;
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        appendDebug('WebSocket', '已连接，发送小智兼容握手');
        socket.send(JSON.stringify({
          type: 'hello', version: 1, transport: 'websocket', device_type: 't5',
          audio_params: { format: 'pcm', sample_rate: 16000, channels: 1, frame_duration: 60 },
        }));
      };
      socket.onmessage = handleServerMessage;
      socket.onerror = () => {
        appendDebug('错误', 'WebSocket 连接失败');
        if (!settled) { settled = true; reject(new Error('无法连接语音服务')); }
      };
      socket.onclose = (event) => {
        if (ws !== socket) return;
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
        appendDebug('连接关闭', { code: event.code, reason: event.reason || '无' });
        connectPromise = null;
        setState({ type: 'connection.closed', reason: event.reason });
        if (!settled) { settled = true; reject(new Error('语音服务连接已关闭')); }
        scheduleReconnect();
      };
      socket._resolveHello = () => {
        if (!settled) { settled = true; resolve(); }
      };
    }).finally(() => { connectPromise = null; });
    return connectPromise;
  }

  function handleBinaryAudio(data) {
      if (callState.dropTtsUntilDone) return;
      AudioArchive.push(data);
      if (!firstAudioSeen) {
        firstAudioSeen = true;
        setState({ type: 'tts.audio' });
        if (speechEndedAt) {
          const latency = Math.round(performance.now() - speechEndedAt);
          $('latency-value').textContent = `${latency} ms`;
          appendDebug('首音频', `${latency} ms（说完到浏览器收到）`);
        }
      }
    Playback.push(data);
  }

  async function finishTts() {
    if (callState.dropTtsUntilDone) Playback.reset(); else await Playback.flush();
    if (callState.dropTtsUntilDone) AudioArchive.abort(); else AudioArchive.complete();
    mascotController?.markTtsDone();
    setState({ type: 'tts.done' });
    firstAudioSeen = false;
    assistantMessage = null;
    scrollMessagesToLatest();
  }

  function handleServerMessage(event) {
    if (event.data instanceof ArrayBuffer) {
      handleBinaryAudio(event.data);
      return;
    }
    if (typeof Blob !== 'undefined' && event.data instanceof Blob) {
      // 移动端常把 WebSocket 二进制帧交付为 Blob；串行解码，保证 tts.done 不抢跑。
      binaryFrameChain = binaryFrameChain.then(() => event.data.arrayBuffer()).then(handleBinaryAudio).catch((error) => {
        appendDebug('音频帧解析失败', error.message || String(error));
      });
      return;
    }

    let message;
    try { message = JSON.parse(event.data); }
    catch { appendDebug('协议错误', '收到非 JSON 控制消息'); return; }
    if (message.type === 'pong') {
      lastPongAt = performance.now();
      return;
    }
    appendDebug(message.type || '事件', message);
    if (message.type === 'assistant.emotion') mascotController?.setEmotion(message.emotion);

    switch (message.type) {
      case 'hello':
        reconnectAttempt = 0;
        startHeartbeat();
        setState(message);
        if (ws && ws._resolveHello) ws._resolveHello();
        addMessage('system', '已接通幽光，语音全链路准备完成');
        break;
      case 'asr.partial':
        setState(message);
        if (!partialMessage) partialMessage = addMessage('system', `听到：${message.text}`);
        else partialMessage.bubble.textContent = `听到：${message.text}`;
        break;
      case 'asr.final':
        if (partialMessage) { partialMessage.article.remove(); partialMessage = null; }
        setState(message);
        addMessage('user', message.text);
        AudioArchive.abort();
        assistantMessage = null;
        break;
      case 'asr.empty':
        if (partialMessage) { partialMessage.article.remove(); partialMessage = null; }
        setState(message);
        toast('这次没听清，再说一次吧');
        break;
      case 'llm.text.delta':
        setState(message);
        if (!assistantMessage) {
          assistantMessage = addMessage('assistant', '');
          AudioArchive.bind(assistantMessage);
        }
        assistantMessage.bubble.textContent += message.text || '';
        scrollMessagesToLatest();
        break;
      case 'tts.done':
        void binaryFrameChain.then(finishTts);
        break;
      case 'error':
        setState({ type: 'error', code: message.code, message: friendlyError(message.code) });
        addMessage('assistant', friendlyError(message.code), 'error');
        toast(friendlyError(message.code));
        break;
      default:
        break;
    }
  }

  function friendlyError(code) {
    if (/ASR/.test(code || '')) return '我刚才没听清，可以再说一次吗？';
    if (/LLM/.test(code || '')) return '我暂时没想好，等一下再聊好吗？';
    if (/TTS/.test(code || '')) return '我的声音暂时出不来，但还可以文字聊天。';
    return '连接有一点波动，请稍后重试。';
  }

  const Playback = (() => {
    const MIN_BLOCK_SAMPLES = Math.floor(TTS_SAMPLE_RATE * 0.04);
    const START_BUFFER_SAMPLES = Math.floor(TTS_SAMPLE_RATE * 0.18);
    let carry = new Uint8Array(0);
    let pending = [];
    let pendingCount = 0;
    let cursor = 0;
    let sources = [];
    let resuming = false;
    let started = false;
    let idleWaiters = [];

    function isIdle() {
      return pendingCount === 0 && sources.length === 0 && !resuming;
    }

    function resolveIdle() {
      if (!isIdle()) return;
      const waiters = idleWaiters;
      idleWaiters = [];
      waiters.forEach((resolve) => resolve());
    }

    function schedule(force = false) {
      if (!pendingCount || (!force && !started && pendingCount < START_BUFFER_SAMPLES)) return;
      const context = ensureAudioContext();
      if (context.state !== 'running') {
        if (!resuming) {
          resuming = true;
          context.resume().then(schedule).catch((error) => {
            appendDebug('音频播放失败', error.message || String(error));
            toast('浏览器阻止了声音播放，请点击页面后重试');
            pending = []; pendingCount = 0;
          }).finally(() => { resuming = false; resolveIdle(); });
        }
        return;
      }
      const merged = new Float32Array(pendingCount);
      let offset = 0;
      pending.forEach((part) => { merged.set(part, offset); offset += part.length; });
      pending = []; pendingCount = 0;
      started = true;
      const buffer = context.createBuffer(1, merged.length, TTS_SAMPLE_RATE);
      buffer.copyToChannel(merged, 0);
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const now = context.currentTime;
      if (cursor < now + 0.012) cursor = now + 0.012;
      source.start(cursor);
      cursor += buffer.duration;
      sources.push(source);
      source.onended = () => {
        sources = sources.filter((item) => item !== source);
        resolveIdle();
      };
    }

    return {
      push(arrayBuffer) {
        const incoming = new Uint8Array(arrayBuffer);
        let bytes = incoming;
        if (carry.length) {
          bytes = new Uint8Array(carry.length + incoming.length);
          bytes.set(carry); bytes.set(incoming, carry.length); carry = new Uint8Array(0);
        }
        const usable = bytes.length - (bytes.length % 2);
        if (usable < bytes.length) carry = bytes.slice(usable);
        if (!usable) return;
        const ints = new Int16Array(bytes.buffer, bytes.byteOffset, usable / 2);
        const floats = new Float32Array(ints.length);
        for (let i = 0; i < ints.length; i += 1) floats[i] = ints[i] / 32768;
        pending.push(floats); pendingCount += floats.length;
        if (pendingCount >= MIN_BLOCK_SAMPLES) schedule();
      },
      flush() {
        carry = new Uint8Array(0);
        schedule(true);
        if (isIdle()) return Promise.resolve();
        return new Promise((resolve) => idleWaiters.push(resolve));
      },
      reset() {
        sources.forEach((source) => { try { source.stop(); } catch {} });
        sources = []; pending = []; pendingCount = 0; carry = new Uint8Array(0); cursor = 0; started = false;
        resuming = false;
        resolveIdle();
      },
      isIdle,
    };
  })();

  const AudioArchive = (() => {
    let chunks = [];
    let target = null;
    const urls = new Set();

    function attach(message, wav) {
      if (!message || !wav.byteLength) return;
      const url = URL.createObjectURL(new Blob([wav], { type: 'audio/wav' }));
      urls.add(url);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'message-audio';
      button.textContent = '▶ 播放';
      button.setAttribute('aria-label', '播放幽光的这条语音回复');
      const audio = new Audio(url);
      audio.preload = 'metadata';
      button.addEventListener('click', async () => {
        if (!audio.paused) {
          audio.pause();
          button.textContent = '▶ 继续';
          return;
        }
        if (activeReplay && activeReplay !== audio) activeReplay.pause();
        if (audio.ended) audio.currentTime = 0;
        activeReplay = audio;
        try {
          await audio.play();
          button.textContent = '❚❚ 暂停';
        } catch (error) {
          appendDebug('语音回放失败', error.message || String(error));
          toast('浏览器暂时无法播放这条语音');
        }
      });
      audio.addEventListener('pause', () => {
        if (!audio.ended) button.textContent = '▶ 继续';
      });
      audio.addEventListener('ended', () => {
        button.textContent = '↻ 重播';
        if (activeReplay === audio) activeReplay = null;
      });
      message.content.append(button);
      scrollMessagesToLatest();
    }

    return {
      bind(message) { target = message; },
      push(arrayBuffer) { chunks.push(new Uint8Array(arrayBuffer.slice(0))); },
      complete() {
        if (target && chunks.length) attach(target, CallCore.pcm16ToWav(chunks, TTS_SAMPLE_RATE));
        chunks = [];
        target = null;
      },
      abort() { chunks = []; target = null; },
      dispose() {
        if (activeReplay) activeReplay.pause();
        urls.forEach((url) => URL.revokeObjectURL(url));
        urls.clear();
      },
    };
  })();

  async function requestMicrophone(constraints) {
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
      return navigator.mediaDevices.getUserMedia(constraints);
    }
    const legacy = navigator.getUserMedia || navigator.webkitGetUserMedia || navigator.mozGetUserMedia || navigator.msGetUserMedia;
    if (legacy) return new Promise((resolve, reject) => legacy.call(navigator, constraints, resolve, reject));
    const context = window.isSecureContext ? '安全上下文' : '非安全上下文';
    throw new Error(`当前浏览器不支持麦克风采集（${context}），请使用 HTTPS 和最新版系统浏览器。`);
  }

  function isVirtualAudioInput(label = '') {
    return /EShareAudio|virtual|BlackHole|Loopback|Soundflower|Aggregate|OBS|虚拟/i.test(label);
  }

  function physicalInputScore(device) {
    const label = device.label || '';
    if (/MacBook|Built-in|Internal|内建|内置/i.test(label)) return 100;
    if (/microphone|麦克风/i.test(label)) return 50;
    return 10;
  }

  async function requestPreferredMicrophone(constraints) {
    const initial = await requestMicrophone(constraints);
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== 'function') return initial;

    const currentTrack = initial.getAudioTracks()[0];
    const currentLabel = currentTrack?.label || '';
    if (currentLabel && !isVirtualAudioInput(currentLabel)) return initial;

    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const preferred = devices
        .filter((device) => device.kind === 'audioinput' && device.deviceId && !isVirtualAudioInput(device.label))
        .sort((left, right) => physicalInputScore(right) - physicalInputScore(left))[0];
      if (!preferred) {
        appendDebug('麦克风选择', `未找到物理输入设备，继续使用 ${currentLabel || '浏览器默认设备'}`);
        return initial;
      }

      const selected = await requestMicrophone({
        audio: { ...constraints.audio, deviceId: { exact: preferred.deviceId } },
      });
      initial.getTracks().forEach((track) => track.stop());
      appendDebug('麦克风切换', `${currentLabel || '浏览器默认设备'} → ${selected.getAudioTracks()[0]?.label || preferred.label}`);
      return selected;
    } catch (error) {
      appendDebug('麦克风选择', `切换物理麦克风失败，继续使用当前设备：${error.message || error}`);
      return initial;
    }
  }

  const AudioCapture = (() => {
    let stream = null;
    let source = null;
    let processor = null;
    let sourceRate = ASR_SAMPLE_RATE;
    let leftover = new Float32Array(0);
    let mode = 'idle';
    let muted = false;
    let microphoneConfirmed = false;

    function resample(input) {
      if (sourceRate === ASR_SAMPLE_RATE) return new Float32Array(input);
      let buffer = input;
      if (leftover.length) {
        buffer = new Float32Array(leftover.length + input.length);
        buffer.set(leftover); buffer.set(input, leftover.length);
      }
      const ratio = sourceRate / ASR_SAMPLE_RATE;
      const outputLength = Math.floor(buffer.length / ratio);
      const output = new Float32Array(outputLength);
      for (let i = 0; i < outputLength; i += 1) {
        const start = Math.floor(i * ratio);
        const end = Math.min(buffer.length, Math.ceil((i + 1) * ratio));
        let sum = 0;
        for (let j = start; j < end; j += 1) sum += buffer[j];
        output[i] = sum / Math.max(1, end - start);
      }
      leftover = buffer.slice(Math.floor(outputLength * ratio));
      return output;
    }

    function toPcm(samples) {
      const pcm = new Int16Array(samples.length);
      for (let i = 0; i < samples.length; i += 1) {
        const value = Math.max(-1, Math.min(1, samples[i]));
        pcm[i] = value < 0 ? value * 32768 : value * 32767;
      }
      return pcm;
    }

    function sendFrame(samples) {
      if (!ws || ws.readyState !== WebSocket.OPEN || !samples.length) return false;
      const pcm = toPcm(samples);
      try {
        ws.send(pcm.buffer);
        return true;
      } catch (error) {
        appendDebug('音频上送失败', error.message || String(error));
        return false;
      }
    }

    const captureBuffer = CallCore.createPcmCaptureBuffer({
      sampleRate: ASR_SAMPLE_RATE,
      preRollMs: PRE_ROLL_MS,
      sendFrame,
      sendCommit: () => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'audio_commit' }));
      },
    });

    function observeInput(raw) {
      let peak = 0;
      for (let i = 0; i < raw.length; i += 1) peak = Math.max(peak, Math.abs(raw[i]));
      if (!microphoneConfirmed && peak > 0.002) {
        microphoneConfirmed = true;
        appendDebug('麦克风输入正常', `峰值 ${peak.toFixed(4)}`);
      }
      $('waveform').style.setProperty('--level', Math.min(1, peak * 4));
    }

    function detachManualProcessor() {
      if (processor) { processor.onaudioprocess = null; processor.disconnect(); processor = null; }
      if (source) { source.disconnect(); source = null; }
    }

    function attachManualProcessor(context) {
      if (processor || !stream) return;
      source = context.createMediaStreamSource(stream);
      processor = context.createScriptProcessor(2048, 1, 1);
      processor.onaudioprocess = (event) => {
        event.outputBuffer.getChannelData(0).fill(0);
        const raw = event.inputBuffer.getChannelData(0);
        observeInput(raw);
        if (!muted && mode === 'manual') captureBuffer.ingest(resample(raw));
      };
      source.connect(processor);
      processor.connect(context.destination);
    }

    async function ensure() {
      const context = ensureAudioContext();
      if (!stream || !stream.active || stream.getAudioTracks().every((track) => track.readyState !== 'live')) {
        detachManualProcessor();
        if (stream) stream.getTracks().forEach((track) => track.stop());
        stream = await requestPreferredMicrophone({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        microphoneConfirmed = false;
      }
      await ensureAudioContextRunning(context);
      sourceRate = context.sampleRate;
      leftover = new Float32Array(0);
      if (mode === 'manual') attachManualProcessor(context);
      const track = stream.getAudioTracks()[0];
      const settings = track && typeof track.getSettings === 'function' ? track.getSettings() : {};
      appendDebug('麦克风', `${track?.label || '默认输入设备'}；设备 ${sourceRate}Hz；轨道 ${track?.readyState || '未知'}；配置 ${JSON.stringify(settings)}`);
      return stream;
    }

    return {
      async startHandsFree() { mode = 'handsfree'; detachManualProcessor(); await ensure(); },
      ingestVadFrame(frame) {
        if (mode !== 'handsfree' || muted || !frame?.length) return;
        observeInput(frame);
        captureBuffer.ingest(frame);
      },
      markSpeechCandidate() { captureBuffer.markCandidate(); },
      cancelSpeechCandidate() { captureBuffer.cancelCandidate(); },
      beginDetectedSpeech() { captureBuffer.begin({ withPreRoll: true }); },
      finishDetectedSpeech() { return captureBuffer.commit(); },
      async startManual() { mode = 'manual'; await ensure(); captureBuffer.begin({ withPreRoll: false }); setState({ type: 'speech.start' }); },
      finishManual() { return captureBuffer.commit(); },
      getStream() { return stream; },
      setMuted(value) { muted = value; if (stream) stream.getAudioTracks().forEach((track) => { track.enabled = !value; }); },
      async close() {
        mode = 'idle';
        captureBuffer.reset();
        microphoneConfirmed = false;
        detachManualProcessor();
        if (stream) { stream.getTracks().forEach((track) => track.stop()); stream = null; }
      },
    };
  })();

  const HandsFreeConversation = (() => {
    let detector = null;
    let active = false;
    let muted = false;
    let vadInputConfirmed = false;
    let lastVadFrameAt = 0;
    let vadWatchdog = null;
    let vadRecovering = false;
    let speechCaptureActive = false;

    async function ensureCaptureStream() {
      const stream = AudioCapture.getStream();
      const track = stream?.getAudioTracks?.()[0];
      if (!track || track.readyState !== 'live' || !stream.active) {
        appendDebug('VAD', '麦克风音轨已失活，重新申请并挂接物理麦克风');
        await AudioCapture.startHandsFree();
      }
      return AudioCapture.getStream();
    }

    function startVadWatchdog() {
      clearInterval(vadWatchdog);
      vadWatchdog = setInterval(async () => {
        if (!active || muted || !detector || !lastVadFrameAt) return;
        if (audioCtx?.state !== 'running') {
          try { await ensureAudioContextRunning(audioCtx); } catch (error) { appendDebug('音频引擎恢复失败', error.message || String(error)); }
        }
        if (vadRecovering || performance.now() - lastVadFrameAt < 5000) return;
        vadRecovering = true;
        appendDebug('VAD', '检测帧超过 5 秒未更新，按官方生命周期重新挂接输入');
        try {
          await ensureCaptureStream();
          await detector.pause();
          await detector.start();
          lastVadFrameAt = performance.now();
          appendDebug('VAD', '输入已恢复，继续监听');
        } catch (error) {
          appendDebug('VAD 恢复失败', error.message || String(error));
        } finally {
          vadRecovering = false;
        }
      }, 2000);
    }

    function confirmSpeechStart(interruptingPlayback) {
      return voiceTurnGate.enqueue(async (turn) => {
        if (!active || muted) return;
        if (speechCaptureActive) return;
        // LiveKit 的连接恢复思路：先确保传输可用，候选语音在前滚缓冲中继续保留。
        await connect();
        mascotController?.markTurnStart();
        const shouldInterrupt = interruptingPlayback
          && (callState.phase === 'speaking' || !PlaybackIdle());
        // 与小智 listen/abort 生命周期一致：先释放旧输出，再发送保存在 pre-roll 中的新输入。
        CallCore.beginSpeechCapture({
          interruptingPlayback: shouldInterrupt,
          interrupt: () => interruptTurn(`确认用户持续说话后打断（轮次 ${turn}）`),
          beginCapture: () => {
            AudioCapture.beginDetectedSpeech();
            speechCaptureActive = true;
          },
        });
        firstAudioSeen = false;
        speechEndedAt = 0;
        setState({ type: 'speech.start' });
        appendDebug('VAD', `检测到用户开始说话，PCM 已流式发送（轮次 ${turn}）`);
      });
    }

    function onSpeechStart() {
      if (!active || muted) return;
      AudioCapture.markSpeechCandidate();
      const agentSpeaking = callState.phase === 'speaking' || !PlaybackIdle();
      const result = interruptionGate.begin({
        agentSpeaking,
        onConfirmed: (interruptingPlayback) => {
          void confirmSpeechStart(interruptingPlayback).catch((error) => {
            AudioCapture.cancelSpeechCandidate();
            appendDebug('VAD 错误', error.message || String(error));
          });
        },
        onFalseInterruption: () => {
          AudioCapture.cancelSpeechCandidate();
          appendDebug('VAD', '短噪声未达到 500 ms，恢复当前播报');
        },
      });
      if (result === 'pending') appendDebug('VAD', '检测到候选插话，等待 500 ms 确认');
    }

    function onSpeechEnd() {
      const interruptionResult = interruptionGate.end();
      if (interruptionResult === 'false-interruption') {
        AudioCapture.cancelSpeechCandidate();
        return;
      }
      void voiceTurnGate.enqueue(async (turn) => {
        if (!active || muted || !speechCaptureActive) return;
        speechCaptureActive = false;
        const duration = AudioCapture.finishDetectedSpeech();
        speechEndedAt = performance.now();
        setState({ type: 'asr.partial', text: callState.userText || '' });
        appendDebug('VAD', `用户说完，提交 ${duration} ms PCM（轮次 ${turn}）`);
      }).catch((error) => {
        appendDebug('VAD 错误', error.message || String(error));
        setState({ type: 'error', message: '语音轮次处理失败，请重新说一次' });
      });
    }

    function onSpeechMisfire() {
      const interruptionResult = interruptionGate.end();
      if (interruptionResult === 'false-interruption') {
        AudioCapture.cancelSpeechCandidate();
        return;
      }
      void voiceTurnGate.enqueue(async () => {
        if (!speechCaptureActive) return;
        speechCaptureActive = false;
        const duration = AudioCapture.finishDetectedSpeech();
        appendDebug('VAD', `忽略过短或无效语音，已关闭 ${duration} ms PCM 轮次`);
      });
    }

    function PlaybackIdle() { return Playback.isIdle(); }

    return {
      async start() {
        if (active) return;
        await loadVoiceLibraries();
        if (!window.vad || !window.vad.MicVAD) throw new Error('Silero VAD 未加载');
        await AudioCapture.startHandsFree();
        detector = await window.vad.MicVAD.new({
          model: 'v5',
          startOnLoad: false,
          processorType: 'ScriptProcessor',
          audioContext: ensureAudioContext(),
          positiveSpeechThreshold: 0.3,
          negativeSpeechThreshold: 0.25,
          redemptionMs: 180,
          preSpeechPadMs: 280,
          minSpeechMs: 260,
          baseAssetPath: VAD_BASE,
          onnxWASMBasePath: ONNX_BASE,
          getStream: async () => ensureCaptureStream(),
          pauseStream: async () => {},
          resumeStream: async () => ensureCaptureStream(),
          onFrameProcessed: (probabilities, frame) => {
            lastVadFrameAt = performance.now();
            // 单一音频源：VAD 分析帧同时驱动前滚缓冲和 ASR，避免双 ScriptProcessor 长时间后分叉。
            AudioCapture.ingestVadFrame(frame);
            if (vadInputConfirmed) return;
            let peak = 0;
            for (let i = 0; i < frame.length; i += 1) peak = Math.max(peak, Math.abs(frame[i]));
            if (peak > 0.002) {
              vadInputConfirmed = true;
              appendDebug('VAD 输入正常', `峰值 ${peak.toFixed(4)}；语音概率 ${Number(probabilities.isSpeech || 0).toFixed(3)}`);
            }
          },
          onSpeechStart,
          onSpeechEnd,
          onVADMisfire: onSpeechMisfire,
        });
        active = true;
        lastVadFrameAt = performance.now();
        await ensureAudioContextRunning(ensureAudioContext());
        await detector.start();
        startVadWatchdog();
        appendDebug('VAD', 'Silero VAD v5 已启动，连续对话开启');
      },
      async setMuted(value) {
        muted = value;
        if (value) interruptionGate.cancel();
        AudioCapture.setMuted(value);
        if (detector) {
          if (value) await detector.pause();
          else await detector.start();
        }
      },
      async recover() {
        if (!active || !detector || vadRecovering) return;
        vadRecovering = true;
        try {
          await ensureCaptureStream();
          await ensureAudioContextRunning(ensureAudioContext());
          await detector.pause();
          await detector.start();
          lastVadFrameAt = performance.now();
          appendDebug('VAD', '页面恢复后已重新挂接麦克风输入');
        } finally {
          vadRecovering = false;
        }
      },
      async stop() {
        active = false;
        clearInterval(vadWatchdog);
        vadWatchdog = null;
        lastVadFrameAt = 0;
        vadRecovering = false;
        interruptionGate.cancel();
        speechCaptureActive = false;
        voiceTurnGate.cancel();
        if (detector) { await detector.destroy(); detector = null; }
        vadInputConfirmed = false;
        await AudioCapture.close();
      },
    };
  })();

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void HandsFreeConversation.recover().catch((error) => {
      appendDebug('VAD 恢复失败', error.message || String(error));
    });
  });

  function interruptTurn(reason = '手动打断') {
    if (!CallCore.canInterrupt(callState)) {
      toast('当前没有正在播报的内容');
      return;
    }
    Playback.reset();
    AudioArchive.abort();
    setState({ type: 'interrupt.local' });
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'interrupt' }));
    assistantMessage = null;
    firstAudioSeen = false;
    appendDebug('打断', reason);
  }

  async function sendText(text) {
    const content = text.trim();
    if (!content) return;
    ensureAudioContext();
    await connect();
    mascotController?.markTurnStart();
    if (callState.phase === 'speaking') interruptTurn('文字输入打断');
    addMessage('user', content);
    AudioArchive.abort();
    assistantMessage = null;
    speechEndedAt = 0;
    ws.send(JSON.stringify({ type: 'text', text: content }));
    setState({ type: 'asr.final', text: content });
    appendDebug('文字输入', content);
  }

  function openDebugDrawer() {
    $('demo-layout').classList.add('debug-open');
    $('debug-drawer').classList.add('is-open');
    $('debug-drawer').setAttribute('aria-hidden', 'false');
    $('debug-toggle').setAttribute('aria-expanded', 'true');
    setTimeout(() => $('debug-close').focus(), 0);
  }

  function closeDebugDrawer() {
    $('demo-layout').classList.remove('debug-open');
    $('debug-drawer').classList.remove('is-open');
    $('debug-drawer').setAttribute('aria-hidden', 'true');
    $('debug-toggle').setAttribute('aria-expanded', 'false');
  }

  function showScreen(name) {
    const callVisible = name === 'call';
    $('chat-screen').classList.toggle('is-active', !callVisible);
    $('call-screen').classList.toggle('is-active', callVisible);
    $('chat-screen').setAttribute('aria-hidden', String(callVisible));
    $('call-screen').setAttribute('aria-hidden', String(!callVisible));
  }

  function startTimer() {
    callStartedAt = Date.now();
    clearInterval(callTimer);
    const update = () => {
      const seconds = Math.floor((Date.now() - callStartedAt) / 1000);
      $('call-timer').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    };
    update(); callTimer = setInterval(update, 1000);
  }

  async function startCall() {
    shouldReconnect = true;
    const audioUnlock = unlockAudioPlayback();
    $('latency-value').textContent = '—';
    showScreen('call');
    startTimer();
    $('manual-fallback').hidden = true;
    try {
      await audioUnlock;
      await connect();
      setState({ type: 'microphone.preparing' });
      await HandsFreeConversation.start();
      $('manual-fallback').hidden = true;
      setState({ type: 'hello' });
    } catch (error) {
      appendDebug('免按键模式失败', error.message || String(error));
      $('manual-fallback').hidden = false;
      toast('免按键模式不可用，已切换到按住说话');
      try { await AudioCapture.startHandsFree(); }
      catch (micError) { setState({ type: 'error', message: micError.message }); }
    }
  }

  async function endCall() {
    shouldReconnect = false;
    stopConnectionMaintenance();
    clearInterval(callTimer); callTimer = null;
    Playback.reset();
    await HandsFreeConversation.stop().catch(() => {});
    if (ws) { ws.close(1000, '用户挂断'); ws = null; }
    setState({ type: 'connection.closed' });
    showScreen('chat');
    addMessage('system', '语音通话已结束');
  }

  $('text-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('text-input');
    try { await sendText(input.value); input.value = ''; }
    catch (error) { toast(error.message); appendDebug('发送失败', error.message); }
  });
  $('call-text-panel').addEventListener('submit', async (event) => {
    event.preventDefault();
    const input = $('call-text-input');
    try { await sendText(input.value); input.value = ''; $('call-text-panel').hidden = true; }
    catch (error) { toast(error.message); }
  });
  $('start-call').addEventListener('click', () => { void startCall(); });
  $('hangup').addEventListener('click', () => { void endCall(); });
  $('back-to-chat').addEventListener('click', () => { void endCall(); });
  $('manual-interrupt').addEventListener('click', () => interruptTurn());
  $('show-call-text').addEventListener('click', () => { $('call-text-panel').hidden = !$('call-text-panel').hidden; if (!$('call-text-panel').hidden) $('call-text-input').focus(); });
  $('mute').addEventListener('click', async () => {
    const enabled = !$('mute').classList.contains('is-on');
    $('mute').classList.toggle('is-on', enabled);
    $('mute').querySelector('small').textContent = enabled ? '已静音' : '静音';
    await HandsFreeConversation.setMuted(enabled).catch(() => {});
    appendDebug('麦克风', enabled ? '已静音' : '已恢复');
  });

  let manualPressed = false;
  let manualStarting = false;
  $('manual-talk').addEventListener('pointerdown', async (event) => {
    event.preventDefault(); manualPressed = true; manualStarting = true; $('manual-talk').textContent = '松开结束';
    try {
      await AudioCapture.startManual();
      manualStarting = false;
      if (!manualPressed) finishManualRecording();
    } catch (error) {
      toast(error.message); manualPressed = false; manualStarting = false; $('manual-talk').textContent = '按住说话';
    }
  });
  function finishManualRecording() {
    $('manual-talk').textContent = '按住说话';
    const duration = AudioCapture.finishManual();
    speechEndedAt = performance.now();
    appendDebug('手动录音', `提交 ${duration} ms PCM`);
  }
  document.addEventListener('pointerup', () => {
    if (!manualPressed) return;
    manualPressed = false;
    if (!manualStarting) finishManualRecording();
  });

  $('debug-toggle').addEventListener('click', openDebugDrawer);
  $('call-debug-toggle').addEventListener('click', openDebugDrawer);
  $('debug-close').addEventListener('click', closeDebugDrawer);
  $('debug-clear').addEventListener('click', () => { $('debug-log').replaceChildren(); appendDebug('日志', '已清空'); });
  $('debug-copy').addEventListener('click', async () => {
    const text = $('debug-log').innerText;
    try { await navigator.clipboard.writeText(text); toast('调试日志已复制'); }
    catch { toast('浏览器未允许复制，请手动选择日志'); }
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDebugDrawer(); });
  window.addEventListener('error', (event) => appendDebug('页面异常', event.message));
  window.addEventListener('unhandledrejection', (event) => appendDebug('异步异常', event.reason?.message || event.reason));
  window.addEventListener('pagehide', () => {
    shouldReconnect = false;
    stopConnectionMaintenance();
    Playback.reset(); AudioArchive.dispose(); void HandsFreeConversation.stop(); if (ws) ws.close();
  });

  appendDebug('页面', '幽光陪伴式语音 Demo 已加载');
  void warmVoiceAssets();
  void loadVoiceLibraries().catch((error) => appendDebug('语音资源', error.message));
  void connect().catch((error) => appendDebug('自动连接', error.message));
})();
