(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.MascotAssets = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const base = './assets/mascot/youguang-base.png';
  const layoutMap = typeof module === 'object' && module.exports
    ? require('./mascot-layer-layout.js')
    : (typeof MASCOT_LAYER_LAYOUT !== 'undefined' ? MASCOT_LAYER_LAYOUT : {});
  const layout = (name) => layoutMap[name] || null;
  const layer = (state) => ({ src: `./assets/mascot/layers/${state}.png`, layout: layout(`${state}.png`) });
  const common = { base, width: 280, height: 280, transparent: true, loop: true };
  const frames = (name) => [1, 2, 3].map((index) => layer(`${name}-${index}`));
  const neutralFrames = frames('neutral');
  const speakingFrames = frames('speaking');
  const listeningFrames = frames('listening');
  const laughingFrames = frames('laughing');
  const cryingFrames = frames('crying');
  const shyFrames = frames('shy');
  const surprisedFrames = frames('surprised');
  const sadFrames = frames('sad');
  const stateAsset = (state, stateFrames) => ({ ...common, overlay: stateFrames[0], frames: stateFrames, state });
  const MASCOT_ASSETS = Object.freeze({
    idle: stateAsset('idle', neutralFrames),
    listening: stateAsset('listening', listeningFrames),
    hearing: stateAsset('hearing', neutralFrames),
    recognizing: stateAsset('recognizing', neutralFrames),
    thinking: stateAsset('thinking', neutralFrames),
    speaking: stateAsset('speaking', speakingFrames),
    happy: stateAsset('happy', laughingFrames),
    sad: stateAsset('sad', sadFrames),
    laughing: stateAsset('laughing', laughingFrames),
    crying: stateAsset('crying', cryingFrames),
    shy: stateAsset('shy', shyFrames),
    comforting: stateAsset('comforting', sadFrames),
    surprised: stateAsset('surprised', surprisedFrames),
    error: stateAsset('error', sadFrames),
  });

  function getMascotAsset(state) {
    return MASCOT_ASSETS[state] || MASCOT_ASSETS.idle;
  }

  return { MASCOT_ASSETS, getMascotAsset };
}));
