const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const test = require('node:test');

const root = path.join(__dirname, '../frontend');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'styles.css'), 'utf8');

test('the workbench document and stylesheet load no remote resources', () => {
  assert.doesNotMatch(html, /(?:src|href)="(?:https?:)?\/\//i);
  assert.doesNotMatch(css, /(?:@import|url\()\s*['"]?(?:https?:)?\/\//i);
  for (const [, resource] of html.matchAll(/(?:src|href)="\.\/([^"?]+)(?:\?[^\"]*)?"/g)) {
    assert.ok(fs.statSync(path.join(root, resource)).isFile(), resource);
  }
  assert.match(html, /vendor\/lucide-0\.468\.0\.min\.js/);
});

test('the bundled icon bytes and upstream notice match their reviewed version', () => {
  const sha = name => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, 'vendor', name))).digest('hex');
  assert.equal(sha('lucide-0.468.0.min.js'), '3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e');
  assert.equal(sha('lucide-LICENSE.txt'), '1e7290b35280a048667bbf0ebabac1c7fd52a75300e8b2946ac165715997f2bc');
  assert.match(fs.readFileSync(path.join(root, 'vendor/lucide-LICENSE.txt'), 'utf8'), /ISC License/);
});
