import { mkdir, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  checkPrerequisites,
  javaRoot,
  npmCliPath,
  powerShellPath,
  runTool,
  webRoot,
  workspace,
} from "./demo-tools.mjs";

const report = {
  schemaVersion: 1,
  scope: "personal-local-module",
  startedAt: new Date().toISOString(),
  node: process.versions.node,
  status: "running",
  steps: [],
  limitations: [
    "Browser and real-phone acceptance are separate.",
    "No real video analysis, Agent, upload or formal teaching materials.",
  ],
};
const destination = path.join(workspace, "artifacts", "check-result.json");

async function step(name, action) {
  const started = Date.now();
  console.log(`\n[CHECK] ${name}`);
  try {
    const output = await action();
    const result = { name, status: "passed", durationMs: Date.now() - started };
    const count =
      typeof output === "string"
        ? output.match(/(?:Java|Launcher) checks passed: (\d+)/)
        : null;
    if (count) result.checkCount = Number(count[1]);
    report.steps.push(result);
  } catch (error) {
    report.steps.push({
      name,
      status: "failed",
      durationMs: Date.now() - started,
      message: error.message,
    });
    throw error;
  }
}

try {
  await step("Prerequisites", () => checkPrerequisites());
  await step("Launcher options and port protection", () =>
    runTool(process.execPath, [
      path.join(workspace, "scripts", "test-demo-tools.mjs"),
    ]),
  );
  await step("Catalog source consistency", () =>
    runTool(
      process.execPath,
      [path.join(javaRoot, "scripts", "sync-data.mjs"), "--check"],
      { cwd: javaRoot },
    ),
  );
  await step("Java compile and HTTP tests", () =>
    runTool(
      powerShellPath(),
      [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        path.join(javaRoot, "scripts", "test.ps1"),
      ],
      { cwd: javaRoot },
    ),
  );
  const npm = await npmCliPath();
  for (const script of ["build", "lint", "format:check"])
    await step(`Frontend ${script}`, () =>
      runTool(process.execPath, [npm, "run", script], { cwd: webRoot }),
    );
  const deliveryScripts = (await readdir(path.join(workspace, "scripts")))
    .filter((name) => name.endsWith(".mjs"))
    .map((name) => path.join(workspace, "scripts", name));
  await step("Delivery tool formatting", () =>
    runTool(process.execPath, [
      path.join(webRoot, "node_modules", "prettier", "bin", "prettier.cjs"),
      "--check",
      path.join(workspace, "README.md"),
      ...deliveryScripts,
    ]),
  );
  report.status = "passed";
  console.log("\nAll local code checks passed.");
} catch (error) {
  report.status = "failed";
  process.exitCode = 1;
  console.error(`\nCheck failed: ${error.message}`);
} finally {
  report.completedAt = new Date().toISOString();
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, JSON.stringify(report, null, 2) + "\n", "utf8");
  console.log(`Report: ${destination}`);
}
