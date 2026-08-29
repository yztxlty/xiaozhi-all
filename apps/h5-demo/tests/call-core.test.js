const test = require('node:test');
const assert = require('node:assert/strict');

const CallCore = require('../call-core.js');
const MascotController = require('../mascot-controller.js');

test('情绪只在 TTS 播放期间展示，完成后恢复默认表情', () => {
  let state = { phase: 'listening', emotion: 'neutral', emotionReady: false, visible: 'listening' };
  state = MascotController.reduceMascotState(state, { type: 'assistant.emotion', emotion: 'happy' });
  assert.equal(state.visible, 'listening');
  state = MascotController.reduceMascotState(state, { type: 'phase', phase: 'speaking' });
  assert.equal(state.visible, 'happy');
  state = MascotController.reduceMascotState(state, { type: 'tts.done' });
  assert.equal(state.phase, 'listening');
  assert.equal(state.visible, 'listening');
  assert.equal(state.emotion, 'neutral');
});

test('默认表情慢速轮播，播报表情保持快速口型', () => {
  assert.equal(MascotController.DEFAULT_FRAME_INTERVAL, 360);
  assert.equal(MascotController.ACTIVE_FRAME_INTERVAL, 180);
});

test('非法情绪回退为 neutral，不污染状态', () => {
  const state = MascotController.reduceMascotState(
    { phase: 'listening', emotion: 'neutral', emotionReady: false, visible: 'listening' },
    { type: 'assistant.emotion', emotion: '<script>' },
  );
  assert.equal(state.emotion, 'neutral');
  assert.equal(state.visible, 'listening');
});

test('协议事件驱动真实通话状态', () => {
  let state = CallCore.createInitialState();
  assert.equal(state.phase, 'disconnected');

  state = CallCore.reduceCallState(state, { type: 'hello' });
  assert.equal(state.phase, 'listening');

  state = CallCore.reduceCallState(state, { type: 'microphone.preparing' });
  assert.equal(state.phase, 'preparing');

  state = CallCore.reduceCallState(state, { type: 'speech.start' });
  assert.equal(state.phase, 'hearing');

  state = CallCore.reduceCallState(state, { type: 'asr.partial', text: '你好' });
  assert.equal(state.phase, 'recognizing');

  state = CallCore.reduceCallState(state, { type: 'asr.final', text: '你好幽光' });
  assert.equal(state.phase, 'thinking');
  assert.equal(state.userText, '你好幽光');

  state = CallCore.reduceCallState(state, { type: 'llm.text.delta', text: '我在' });
  state = CallCore.reduceCallState(state, { type: 'llm.text.delta', text: '呢' });
  assert.equal(state.assistantText, '我在呢');

  state = CallCore.reduceCallState(state, { type: 'tts.audio' });
  assert.equal(state.phase, 'speaking');

  state = CallCore.reduceCallState(state, { type: 'interrupt.local' });
  assert.equal(state.phase, 'listening');
  assert.equal(state.dropTtsUntilDone, true);

  state = CallCore.reduceCallState(state, { type: 'tts.done' });
  assert.equal(state.dropTtsUntilDone, false);
});

test('旧轮次 tts.done 不能覆盖已经开始的新一轮拾音', () => {
  let state = CallCore.reduceCallState(CallCore.createInitialState(), { type: 'hello' });
  state = CallCore.reduceCallState(state, { type: 'tts.audio' });
  state = CallCore.reduceCallState(state, { type: 'interrupt.local' });
  state = CallCore.reduceCallState(state, { type: 'speech.start' });
  state = CallCore.reduceCallState(state, { type: 'tts.done' });

  assert.equal(state.phase, 'hearing');
  assert.equal(state.dropTtsUntilDone, false);
});

test('日志脱敏隐藏密钥和长会话标识', () => {
  const value = CallCore.sanitizeLog({
    authorization: 'Bearer sk-demo-secret',
    api_key: 'top-secret-key',
    session_id: 's_428ed1b64808440697825295b89ca45f',
    event: 'hello',
  });

  assert.doesNotMatch(value, /sk-demo-secret|top-secret-key|428ed1b64808440697825295b89ca45f/);
  assert.match(value, /\*\*\*/);
  assert.match(value, /s_428e…a45f/);
  assert.match(value, /hello/);
});

test('WebSocket 地址固定使用同主机安全网关', () => {
  assert.equal(
    CallCore.buildWebSocketUrl({ hostname: '192.168.50.11', port: '18765' }),
    'wss://192.168.50.11:18765/xiaozhi/v1/ws',
  );
  assert.equal(
    CallCore.buildWebSocketUrl({ hostname: '127.0.0.1', port: '18080' }),
    'wss://127.0.0.1:18765/xiaozhi/v1/ws',
  );
  assert.equal(
    CallCore.buildWebSocketUrl({
      hostname: 'yomitest.gwcz.online',
      port: '',
      pathname: '/chat/',
    }),
    'wss://yomitest.gwcz.online/chat/xiaozhi/v1/ws',
  );
});

test('WebSocket 路由由运行配置提供默认契约', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const config = fs.readFileSync(path.join(__dirname, '..', '运行配置.js'), 'utf8');
  assert.match(config, /websocketRoute:\s*['"]\/xiaozhi\/v1\/ws['"]/);
  assert.match(config, /chatPrefix:\s*['"]\/chat\/['"]/);
});

test('只有进行中的识别、思考或播报可以被打断', () => {
  assert.equal(CallCore.canInterrupt({ phase: 'listening' }), false);
  assert.equal(CallCore.canInterrupt({ phase: 'recognizing' }), true);
  assert.equal(CallCore.canInterrupt({ phase: 'thinking' }), true);
  assert.equal(CallCore.canInterrupt({ phase: 'speaking' }), true);
});

test('PCM16 分片可以封装为浏览器可回放的单声道 WAV', () => {
  const first = new Uint8Array([1, 2, 3, 4]);
  const second = new Uint8Array([5, 6]);
  const wav = CallCore.pcm16ToWav([first, second], 16000);

  assert.equal(Buffer.from(wav.subarray(0, 4)).toString('ascii'), 'RIFF');
  assert.equal(Buffer.from(wav.subarray(8, 12)).toString('ascii'), 'WAVE');
  assert.equal(Buffer.from(wav.subarray(36, 40)).toString('ascii'), 'data');
  assert.equal(new DataView(wav.buffer, wav.byteOffset, wav.byteLength).getUint32(40, true), 6);
  assert.deepEqual([...wav.subarray(44)], [1, 2, 3, 4, 5, 6]);
});

test('语音轮次仲裁器按顺序执行并可丢弃已取消的旧轮次', async () => {
  const gate = CallCore.createVoiceTurnGate();
  const events = [];
  const first = gate.enqueue(async (turn) => {
    events.push(`start:${turn}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
    events.push(`end:${turn}`);
  });
  const second = gate.enqueue(async (turn) => {
    events.push(`start:${turn}`);
    events.push(`end:${turn}`);
  });
  gate.cancel();
  await Promise.all([first, second]);

  assert.deepEqual(events, ['start:1', 'end:1']);
});

test('播报期间短噪声按 LiveKit 误打断恢复策略忽略', () => {
  const timers = [];
  const gate = CallCore.createInterruptionGate({
    minDurationMs: 500,
    setTimer: (callback) => { timers.push(callback); return callback; },
    clearTimer: (callback) => {
      const index = timers.indexOf(callback);
      if (index >= 0) timers.splice(index, 1);
    },
  });
  const events = [];

  assert.equal(gate.begin({
    agentSpeaking: true,
    onConfirmed: () => events.push('confirmed'),
    onFalseInterruption: () => events.push('false-interruption'),
  }), 'pending');
  assert.equal(gate.end(), 'false-interruption');
  assert.deepEqual(events, ['false-interruption']);
  assert.equal(timers.length, 0);
});

test('播报期间持续讲话达到确认窗口后只打断一次', () => {
  const timers = [];
  const gate = CallCore.createInterruptionGate({
    minDurationMs: 500,
    setTimer: (callback) => { timers.push(callback); return callback; },
    clearTimer: () => {},
  });
  const events = [];

  gate.begin({ agentSpeaking: true, onConfirmed: () => events.push('confirmed') });
  timers[0]();
  assert.equal(gate.end(), 'confirmed');
  assert.deepEqual(events, ['confirmed']);
});

test('聆听状态起声立即进入正常拾音，不等待打断确认', () => {
  const gate = CallCore.createInterruptionGate();
  const events = [];

  assert.equal(gate.begin({
    agentSpeaking: false,
    onConfirmed: () => events.push('confirmed'),
  }), 'confirmed');
  assert.deepEqual(events, ['confirmed']);
  assert.equal(gate.end(), 'confirmed');
});

test('确认插话后先终止旧播报再发送带前置缓冲的新拾音', () => {
  const events = [];

  CallCore.beginSpeechCapture({
    interruptingPlayback: true,
    interrupt: () => events.push('interrupt'),
    beginCapture: () => events.push('begin-capture'),
  });

  assert.deepEqual(events, ['interrupt', 'begin-capture']);
});

test('单一 VAD 音频源在长时间空闲后仍能将前滚音频送入新轮次', () => {
  const sent = [];
  let commits = 0;
  const capture = CallCore.createPcmCaptureBuffer({
    sampleRate: 16000,
    preRollMs: 1000,
    sendFrame: (frame) => sent.push([...frame]),
    sendCommit: () => { commits += 1; },
  });

  // 模拟长时间空闲：缓冲始终只保留最后 1 秒，不会无限增长。
  for (let index = 0; index < 1200; index += 1) capture.ingest(new Float32Array(160));
  capture.ingest(new Float32Array(160).fill(0.25));
  capture.begin({ withPreRoll: true });
  capture.ingest(new Float32Array(320).fill(0.5));
  const duration = capture.commit();

  assert.ok(duration >= 1000);
  assert.ok(sent.length > 1);
  assert.ok(sent.some((frame) => frame.includes(0.25)));
  assert.ok(sent.some((frame) => frame.includes(0.5)));
  assert.equal(commits, 1);
});

test('打断确认期间的整段语音保留在前滚缓冲中', () => {
  const sent = [];
  const capture = CallCore.createPcmCaptureBuffer({
    sampleRate: 16000,
    preRollMs: 1000,
    sendFrame: (frame) => sent.push(frame),
    sendCommit: () => {},
  });

  // 700 ms 语音已在 LiveKit 式打断确认窗口内发生。
  for (let index = 0; index < 7; index += 1) capture.ingest(new Float32Array(1600).fill(index + 1));
  capture.begin({ withPreRoll: true });

  assert.equal(sent.length, 7);
  assert.deepEqual(sent.map((frame) => frame[0]), [1, 2, 3, 4, 5, 6, 7]);
});

test('断线重连期间的整段候选语音不受普通前滚窗口截断', () => {
  const sent = [];
  const capture = CallCore.createPcmCaptureBuffer({
    sampleRate: 16000,
    preRollMs: 1000,
    maxCandidateMs: 10000,
    sendFrame: (frame) => { sent.push(frame); return true; },
    sendCommit: () => {},
  });

  capture.markCandidate();
  for (let index = 0; index < 30; index += 1) {
    capture.ingest(new Float32Array(1600).fill(index + 1));
  }
  capture.begin({ withPreRoll: true });

  assert.equal(sent.length, 30);
  assert.deepEqual(sent.map((frame) => frame[0]), Array.from({ length: 30 }, (_, index) => index + 1));
});

test('未真正写入 WebSocket 的音频不能伪计入提交时长', () => {
  let commits = 0;
  const capture = CallCore.createPcmCaptureBuffer({
    sampleRate: 16000,
    sendFrame: () => false,
    sendCommit: () => { commits += 1; },
  });

  capture.begin({ withPreRoll: false });
  capture.ingest(new Float32Array(16000));

  assert.equal(capture.commit(), 0);
  assert.equal(commits, 1);
});
