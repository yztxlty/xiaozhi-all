const test = require('node:test');
const assert = require('node:assert/strict');

const { MASCOT_ASSETS, getMascotAsset } = require('../mascot-assets.js');

test('所有角色状态使用统一高清底图和独立表现层', () => {
  const states = ['idle', 'listening', 'hearing', 'recognizing', 'thinking', 'speaking', 'happy', 'sad', 'comforting', 'surprised', 'error', 'shy', 'laughing', 'crying'];
  for (const state of states) {
    const asset = getMascotAsset(state);
    assert.match(asset.base, /assets\/mascot\/youguang-base\.png$/);
    assert.equal(asset.width, 280);
    assert.equal(asset.height, 280);
    assert.equal(asset.transparent, true);
    assert.equal(asset.frames.length, 3);
    assert.match(asset.frames[0].src, /assets\/mascot\/layers\/[a-z]+-[123]\.png$/);
    assert.ok(asset.frames[0].layout, `${state} 必须有裁剪定位元数据`);
  }
  assert.notEqual(MASCOT_ASSETS.happy.overlay, MASCOT_ASSETS.sad.overlay);
  assert.match(MASCOT_ASSETS.happy.frames[0].src, /laughing-1\.png$/);
  assert.match(MASCOT_ASSETS.happy.frames[2].src, /laughing-3\.png$/);
  assert.match(MASCOT_ASSETS.listening.frames[1].src, /listening-2\.png$/);
  assert.match(MASCOT_ASSETS.speaking.frames[1].src, /speaking-2\.png$/);
  assert.match(MASCOT_ASSETS.shy.frames[2].src, /shy-3\.png$/);
});

test('未知状态安全回退待机', () => {
  assert.deepEqual(getMascotAsset('unknown'), getMascotAsset('idle'));
});
