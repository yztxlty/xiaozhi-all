(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./mascot-assets.js'));
  else root.MascotController = factory(root.MascotAssets);
}(typeof globalThis !== 'undefined' ? globalThis : this, function (MascotAssets) {
  'use strict';

  const STAGE_STATES = new Set(['idle', 'listening', 'hearing', 'recognizing', 'thinking', 'speaking', 'error']);
  const EMOTIONS = new Set(['neutral', 'happy', 'sad', 'comforting', 'surprised', 'shy', 'laughing', 'crying']);
  const DEFAULT_FRAME_INTERVAL = 360;
  const ACTIVE_FRAME_INTERVAL = 180;

  function normalizeEmotion(emotion) {
    return EMOTIONS.has(emotion) ? emotion : 'neutral';
  }

  function reduceMascotState(state, event) {
    const next = { ...state };
    if (event.type === 'phase' && STAGE_STATES.has(event.phase)) next.phase = event.phase;
    if (event.type === 'assistant.emotion') next.emotion = normalizeEmotion(event.emotion);
    if (event.type === 'tts.done') {
      next.phase = 'listening';
      next.emotionReady = false;
      next.emotion = 'neutral';
    }
    if (event.type === 'turn.start') {
      next.emotionReady = false;
      next.emotion = 'neutral';
    }
    next.visible = next.phase;
    if (next.phase === 'speaking' && next.emotion !== 'neutral') next.visible = next.emotion;
    return next;
  }

  function createMascotController(element, options = {}) {
    if (!element) throw new Error('角色容器不能为空');
    const image = document.createElement('img');
    image.className = 'mascot-image';
    image.alt = '';
    image.draggable = false;
    const overlay = document.createElement('img');
    overlay.className = 'mascot-expression';
    overlay.alt = '';
    overlay.draggable = false;
    element.replaceChildren(image, overlay);
    let state = { phase: 'idle', emotion: 'neutral', emotionReady: false, visible: 'idle' };
    let animationTimer = null;
    let animationIndex = 0;
    let renderedVisible = null;
    const preload = options.preload || (() => {});
    function frameSource(frame) { return typeof frame === 'string' ? frame : frame?.src; }
    function applyFrame(frame) {
      const source = frameSource(frame);
      if (!source) { overlay.removeAttribute('src'); return; }
      const geometry = typeof frame === 'string' ? null : frame.layout;
      if (geometry) {
        const scaleX = 100 / geometry.canvasWidth;
        const scaleY = 100 / geometry.canvasHeight;
        overlay.style.left = `${geometry.x * scaleX}%`;
        overlay.style.top = `${geometry.y * scaleY}%`;
        overlay.style.width = `${geometry.width * scaleX}%`;
        overlay.style.height = `${geometry.height * scaleY}%`;
      } else {
        overlay.style.left = '0'; overlay.style.top = '0';
        overlay.style.width = '100%'; overlay.style.height = '100%';
      }
      overlay.src = source;
    }

    function render() {
      const asset = MascotAssets.getMascotAsset(state.visible);
      if (animationTimer) {
        clearInterval(animationTimer);
        animationTimer = null;
      }
      animationIndex = 0;
      image.src = asset.base;
      applyFrame(asset.overlay);
      element.dataset.mascotState = state.visible;
      renderedVisible = state.visible;
      preload(asset);
      if (asset.frames?.length > 1) {
        const interval = ['idle', 'listening', 'hearing', 'recognizing', 'thinking'].includes(state.visible)
          ? DEFAULT_FRAME_INTERVAL : ACTIVE_FRAME_INTERVAL;
        animationTimer = setInterval(() => {
          animationIndex = (animationIndex + 1) % asset.frames.length;
          applyFrame(asset.frames[animationIndex]);
        }, interval);
      }
    }
    image.addEventListener('error', () => {
      if (image.src.endsWith('youguang-base.png')) return;
      image.src = MascotAssets.getMascotAsset('idle').base;
    });
    overlay.addEventListener('error', () => {
      overlay.removeAttribute('src');
    });
    function dispatch(event) {
      const previousVisible = state.visible;
      state = reduceMascotState(state, event);
      // 同一状态下只更新数据，不重置当前帧和计时器，避免协议事件造成闪烁。
      if (state.visible !== previousVisible || renderedVisible === null) render();
    }
    render();
    return {
      setPhase(phase) { dispatch({ type: 'phase', phase }); },
      setEmotion(emotion) { dispatch({ type: 'assistant.emotion', emotion }); },
      markTurnStart() { dispatch({ type: 'turn.start' }); },
      markTtsDone() { dispatch({ type: 'tts.done' }); },
      dispose() { if (animationTimer) clearInterval(animationTimer); element.replaceChildren(); },
      getState() { return { ...state }; },
    };
  }

  return { EMOTIONS, STAGE_STATES, DEFAULT_FRAME_INTERVAL, ACTIVE_FRAME_INTERVAL, normalizeEmotion, reduceMascotState, createMascotController };
}));
