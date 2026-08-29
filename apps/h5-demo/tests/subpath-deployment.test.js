const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('H5 静态资源必须相对当前目录，兼容 /chat/ 子路径部署', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const rootRelativeAssets = [...html.matchAll(/(?:src|href)="\/(?!\/)([^"]+)"/g)]
    .map((match) => match[1]);

  assert.deepEqual(rootRelativeAssets, [], `发现站点根路径资源：${rootRelativeAssets.join(', ')}`);
  for (const asset of [
    'app.css',
    'call-core.js',
    'mascot-layer-layout.js',
    'mascot-assets.js',
    'mascot-controller.js',
    'app.js',
  ]) {
    assert.match(html, new RegExp(`(?:src|href)="\\./${asset.replace('.', '\\.')}"`));
  }
});
