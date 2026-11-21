import assert from "node:assert/strict";
import { createServer } from "node:net";
import {
  assertPortAvailable,
  parseStartArgs,
  supportsNodeVersion,
} from "./demo-tools.mjs";

let passed = 0;
for (const [version, expected] of [
  ["22.17.0", false],
  ["22.18.0", true],
  ["23.0.0", false],
  ["24.11.0", false],
  ["24.12.0", true],
  ["25.0.0", true],
]) {
  assert.equal(supportsNodeVersion(version), expected);
  passed++;
}
assert.deepEqual(parseStartArgs([]), {
  javaPort: 8081,
  webPort: 5173,
  preview: false,
});
passed++;
assert.deepEqual(
  parseStartArgs(["--java-port=18081", "--web-port=4188", "--preview"]),
  { javaPort: 18081, webPort: 4188, preview: true },
);
passed++;
for (const args of [
  ["--java-port=80"],
  ["--java-port=65536"],
  ["--web-port=x"],
  ["--java-port=8081", "--web-port=8081"],
  ["--preview=x"],
  ["--java-port=8081", "--java-port=8082"],
  ["--unknown=1"],
]) {
  assert.throws(() => parseStartArgs(args));
  passed++;
}
const occupied = createServer();
await new Promise((resolve) => occupied.listen(0, "127.0.0.1", resolve));
const port = occupied.address().port;
try {
  await assert.rejects(assertPortAvailable(port), /nothing was stopped/);
  passed++;
} finally {
  await new Promise((resolve) => occupied.close(resolve));
}
await assertPortAvailable(port);
passed++;
console.log(`Launcher checks passed: ${passed}`);
