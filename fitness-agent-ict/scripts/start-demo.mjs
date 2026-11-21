import { spawn } from "node:child_process";
import path from "node:path";
import { createInterface } from "node:readline";
import {
  assertPortAvailable,
  checkPrerequisites,
  javaRoot,
  parseStartArgs,
  powerShellPath,
  webRoot,
} from "./demo-tools.mjs";

const children = new Set();
let stopped = false;
let exitCode = 0;
let finish;
const untilStopped = new Promise((resolve) => {
  finish = resolve;
});
const input = createInterface({ input: process.stdin, terminal: false });
input.on("line", (line) => {
  if (line.trim().toLowerCase() === "q") stop();
});

function stop(code = 0) {
  if (stopped) return;
  stopped = true;
  exitCode = code;
  for (const child of children) if (child.exitCode === null) child.kill();
  finish();
}
process.once("SIGINT", () => stop());
process.once("SIGTERM", () => stop());
process.once("SIGHUP", () => stop());
process.once("exit", () => {
  for (const child of children) if (child.exitCode === null) child.kill();
});

function startChild(
  label,
  command,
  args,
  { cwd, env = process.env, service = true },
) {
  if (stopped) throw new Error("Startup cancelled.");
  const child = spawn(command, args, {
    cwd,
    env,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  children.add(child);
  child.stdout.on("data", (chunk) =>
    process.stdout.write(`[${label}] ${chunk}`),
  );
  child.stderr.on("data", (chunk) =>
    process.stderr.write(`[${label}] ${chunk}`),
  );
  child.once("error", (error) => {
    console.error(`${label}: ${error.message}`);
    stop(1);
  });
  child.once("close", (code) => {
    children.delete(child);
    if (service && !stopped) {
      console.error(
        `${label} stopped (${code}); closing this launcher's other service.`,
      );
      stop(1);
    }
  });
  return child;
}

async function waitFor(url, validate) {
  for (let attempt = 0; attempt < 60; attempt++) {
    if (stopped) throw new Error("Startup cancelled.");
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(800) });
      if (response.ok && (await validate(response))) return;
    } catch {
      /* The owned service is still starting. */
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Service did not become ready: ${url}`);
}

try {
  const options = parseStartArgs(process.argv.slice(2));
  await checkPrerequisites(options);
  await assertPortAvailable(options.javaPort);
  await assertPortAvailable(options.webPort);
  const build = startChild(
    "BUILD",
    powerShellPath(),
    [
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      path.join(javaRoot, "scripts", "build.ps1"),
    ],
    { cwd: javaRoot, service: false },
  );
  await new Promise((resolve, reject) => {
    build.once("error", reject);
    build.once("close", (code) =>
      code === 0
        ? resolve()
        : reject(new Error("Java build failed or was cancelled.")),
    );
  });
  if (stopped) throw new Error("Startup cancelled.");
  startChild(
    "JAVA",
    "java",
    [
      "--add-modules",
      "jdk.httpserver",
      "-jar",
      path.join(javaRoot, "build", "fitness-query-service.jar"),
      `--port=${options.javaPort}`,
    ],
    { cwd: javaRoot },
  );
  const health = `http://127.0.0.1:${options.javaPort}/api/v1/health`;
  await waitFor(health, async (response) => {
    const body = await response.json();
    return (
      body.data?.status === "ok" && body.meta?.contractVersion === "personal-v1"
    );
  });
  startChild(
    "WEB",
    process.execPath,
    [
      path.join(webRoot, "node_modules", "vite", "bin", "vite.js"),
      ...(options.preview ? ["preview"] : []),
      "--host",
      "127.0.0.1",
      "--port",
      String(options.webPort),
      "--strictPort",
    ],
    {
      cwd: webRoot,
      env: {
        ...process.env,
        VITE_QUERY_PROXY_TARGET: `http://127.0.0.1:${options.javaPort}`,
      },
    },
  );
  const address = `http://127.0.0.1:${options.webPort}/`;
  await waitFor(address, async (response) =>
    (await response.text()).includes('id="app"'),
  );
  console.log(
    `\nREADY: ${address}\nJAVA:  ${health}\nClick the Java catalog button in the page. Video analysis remains a local mock.\nPress Ctrl+C or enter q to stop both services started by this launcher.\n`,
  );
  await untilStopped;
} catch (error) {
  if (!stopped) {
    console.error(`Could not start demo: ${error.message}`);
    stop(1);
  }
} finally {
  input.close();
  stop(exitCode);
  await Promise.all(
    [...children].map(
      (child) =>
        new Promise((resolve) => {
          if (child.exitCode !== null) {
            resolve();
            return;
          }
          const timer = setTimeout(resolve, 3000);
          child.once("close", () => {
            clearTimeout(timer);
            resolve();
          });
        }),
    ),
  );
  process.exitCode = exitCode;
}
