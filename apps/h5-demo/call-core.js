(function exposeCallCore(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.CallCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function createCallCore() {
  'use strict';

  const runtimeConfig = (typeof globalThis !== 'undefined' && globalThis.YOGUANG_RUNTIME_CONFIG) || {};
  const websocketRoute = String(runtimeConfig.websocketRoute || '/xiaozhi/v1/ws')
    .replace(/^\/?/, '/');
  const chatPrefix = String(runtimeConfig.chatPrefix || '/chat/')
    .replace(/^\/?/, '/')
    .replace(/\/?$/, '/');

  function createInitialState() {
    return {
      phase: 'disconnected',
      connected: false,
      userText: '',
      assistantText: '',
      dropTtsUntilDone: false,
      error: '',
    };
  }

  function reduceCallState(state, event) {
    const next = { ...state };
    switch (event.type) {
      case 'connection.opening':
        next.phase = 'connecting';
        next.error = '';
        return next;
      case 'hello':
        next.phase = 'listening';
        next.connected = true;
        next.error = '';
        return next;
      case 'microphone.preparing':
        next.phase = 'preparing';
        next.error = '';
        return next;
      case 'speech.start':
        next.phase = 'hearing';
        next.userText = '';
        next.assistantText = '';
        return next;
      case 'asr.partial':
        next.phase = 'recognizing';
        next.userText = event.text || '';
        return next;
      case 'asr.final':
        next.phase = 'thinking';
        next.userText = event.text || '';
        next.assistantText = '';
        return next;
      case 'llm.text.delta':
        next.phase = 'thinking';
        next.assistantText += event.text || '';
        return next;
      case 'tts.audio':
        next.phase = 'speaking';
        return next;
      case 'interrupt.local':
        next.phase = 'listening';
        next.dropTtsUntilDone = true;
        return next;
      case 'tts.done':
        if (!(next.dropTtsUntilDone && ['hearing', 'recognizing', 'thinking'].includes(next.phase))) {
          next.phase = 'listening';
        }
        next.dropTtsUntilDone = false;
        return next;
      case 'asr.empty':
        next.phase = 'listening';
        return next;
      case 'connection.closed':
        return { ...createInitialState(), error: event.reason || '' };
      case 'error':
        next.phase = 'error';
        next.error = event.message || event.code || '服务暂时不可用';
        return next;
      default:
        return next;
    }
  }

  function maskLongIdentifiers(value) {
    return value.replace(/\b(s_[A-Za-z0-9]{8,})\b/g, (id) => `${id.slice(0, 6)}…${id.slice(-4)}`);
  }

  function sanitizeLog(value) {
    const seen = new WeakSet();
    let text;
    if (typeof value === 'string') {
      text = value;
    } else {
      text = JSON.stringify(value, (key, item) => {
        if (/authorization|api[_-]?key|token|secret|password/i.test(key)) return '***';
        if (item && typeof item === 'object') {
          if (seen.has(item)) return '[循环引用]';
          seen.add(item);
        }
        return item;
      });
    }
    return maskLongIdentifiers(String(text || ''))
      .replace(/Bearer\s+[^\s",}]+/gi, 'Bearer ***')
      .replace(/\bsk-[A-Za-z0-9_-]+\b/g, '***');
  }

  function buildWebSocketUrl(locationLike) {
    const hostname = locationLike.hostname || '127.0.0.1';
    const pathname = locationLike.pathname || '';
    if (pathname === chatPrefix.slice(0, -1) || pathname.startsWith(chatPrefix)) {
      const authority = locationLike.port ? `${hostname}:${locationLike.port}` : hostname;
      return `wss://${authority}${chatPrefix}${websocketRoute.replace(/^\/+/, '')}`;
    }
    const authority = locationLike.port === '18765' ? `${hostname}:${locationLike.port}` : `${hostname}:18765`;
    return `wss://${authority}${websocketRoute}`;
  }

  function canInterrupt(state) {
    return ['recognizing', 'thinking', 'speaking'].includes(state.phase);
  }

  function pcm16ToWav(chunks, sampleRate = 16000) {
    const byteLength = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
    const wav = new Uint8Array(44 + byteLength);
    const view = new DataView(wav.buffer);
    const writeText = (offset, text) => {
      for (let index = 0; index < text.length; index += 1) wav[offset + index] = text.charCodeAt(index);
    };
    writeText(0, 'RIFF');
    view.setUint32(4, 36 + byteLength, true);
    writeText(8, 'WAVE');
    writeText(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeText(36, 'data');
    view.setUint32(40, byteLength, true);
    let offset = 44;
    chunks.forEach((chunk) => {
      const bytes = chunk instanceof Uint8Array
        ? chunk
        : new Uint8Array(chunk.buffer || chunk, chunk.byteOffset || 0, chunk.byteLength);
      wav.set(bytes, offset);
      offset += bytes.byteLength;
    });
    return wav;
  }

  function createVoiceTurnGate() {
    let running = false;
    let nextTurn = 1;
    const queue = [];

    async function pump() {
      if (running) return;
      const item = queue.shift();
      if (!item) return;
      if (item.cancelled) {
        item.resolve();
        return pump();
      }
      running = true;
      try {
        await item.task(item.turn);
        item.resolve();
      } catch (error) {
        item.reject(error);
      } finally {
        running = false;
        void pump();
      }
    }

    return {
      enqueue(task) {
        return new Promise((resolve, reject) => {
          queue.push({ task, resolve, reject, turn: nextTurn++, cancelled: false });
          void pump();
        });
      },
      cancel() {
        queue.forEach((item) => { item.cancelled = true; });
      },
      get pending() { return queue.length + (running ? 1 : 0); },
    };
  }

  function createInterruptionGate(options = {}) {
    const minDurationMs = options.minDurationMs ?? 500;
    const setTimer = options.setTimer || setTimeout;
    const clearTimer = options.clearTimer || clearTimeout;
    let status = 'idle';
    let timer = null;
    let falseInterruption = null;

    function cancel() {
      if (timer !== null) clearTimer(timer);
      timer = null;
      falseInterruption = null;
      status = 'idle';
    }

    return {
      begin({ agentSpeaking, onConfirmed, onFalseInterruption }) {
        cancel();
        if (!agentSpeaking) {
          status = 'confirmed';
          onConfirmed?.(false);
          return status;
        }
        status = 'pending';
        falseInterruption = onFalseInterruption || null;
        timer = setTimer(() => {
          if (status !== 'pending') return;
          timer = null;
          falseInterruption = null;
          status = 'confirmed';
          onConfirmed?.(true);
        }, minDurationMs);
        return status;
      },
      end() {
        if (status === 'pending') {
          if (timer !== null) clearTimer(timer);
          timer = null;
          const callback = falseInterruption;
          falseInterruption = null;
          status = 'idle';
          callback?.();
          return 'false-interruption';
        }
        if (status === 'confirmed') {
          status = 'idle';
          return 'confirmed';
        }
        return 'idle';
      },
      cancel,
      get status() { return status; },
    };
  }

  function beginSpeechCapture({ interruptingPlayback, interrupt, beginCapture }) {
    if (interruptingPlayback) interrupt();
    beginCapture();
  }

  function createPcmCaptureBuffer(options = {}) {
    const sampleRate = options.sampleRate || 16000;
    const preRollSamples = Math.round(sampleRate * ((options.preRollMs || 1000) / 1000));
    const maxCandidateSamples = Math.round(sampleRate * ((options.maxCandidateMs || 10000) / 1000));
    const sendFrame = options.sendFrame || (() => {});
    const sendCommit = options.sendCommit || (() => {});
    let preRoll = [];
    let preRollCount = 0;
    let streaming = false;
    let sentSamples = 0;
    let candidate = false;

    function remember(samples) {
      const snapshot = new Float32Array(samples);
      preRoll.push(snapshot);
      preRollCount += snapshot.length;
      const limit = candidate ? maxCandidateSamples : preRollSamples;
      while (preRollCount > limit && preRoll.length > 1) {
        preRollCount -= preRoll.shift().length;
      }
    }

    function send(samples) {
      if (!samples?.length) return;
      if (sendFrame(samples) !== false) sentSamples += samples.length;
    }

    return {
      ingest(samples) {
        if (!samples?.length) return;
        if (streaming) send(samples);
        else remember(samples);
      },
      markCandidate() { candidate = true; },
      cancelCandidate() {
        candidate = false;
        while (preRollCount > preRollSamples && preRoll.length > 1) {
          preRollCount -= preRoll.shift().length;
        }
      },
      begin({ withPreRoll = true } = {}) {
        sentSamples = 0;
        streaming = true;
        if (withPreRoll) preRoll.forEach(send);
        preRoll = [];
        preRollCount = 0;
        candidate = false;
      },
      commit() {
        if (!streaming) return 0;
        streaming = false;
        const duration = Math.round((sentSamples / sampleRate) * 1000);
        sendCommit();
        sentSamples = 0;
        return duration;
      },
      reset() {
        preRoll = [];
        preRollCount = 0;
        streaming = false;
        sentSamples = 0;
        candidate = false;
      },
      get streaming() { return streaming; },
    };
  }

  return {
    createInitialState,
    reduceCallState,
    sanitizeLog,
    buildWebSocketUrl,
    canInterrupt,
    pcm16ToWav,
    createVoiceTurnGate,
    createInterruptionGate,
    beginSpeechCapture,
    createPcmCaptureBuffer,
  };
});
