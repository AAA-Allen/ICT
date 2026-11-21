import { spawn } from "node:child_process";
import { access } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const workspace = fileURLToPath(new URL("../", import.meta.url));
export const webRoot = path.join(workspace, "fitness-agent-web");
export const javaRoot = path.join(workspace, "fitness-query-service");

export function supportsNodeVersion(version) {
  const [major, minor] = version.replace(/^v/, "").split(".").map(Number);
  return (
    (major === 22 && minor >= 18) ||
    (major === 24 && minor >= 12) ||
    major >= 25
  );
}

export function parseStartArgs(args) {
  const options = { javaPort: 8081, webPort: 5173, preview: false };
  const seen = new Set();
  for (const arg of args) {
    const [key, value, extra] = arg.split("=");
    if (seen.has(key) || extra !== undefined)
      throw new Error(`Repeated or invalid option: ${key}`);
    seen.add(key);
    if (key === "--preview" && value === undefined) {
      options.preview = true;
      continue;
    }
    if (
      !["--java-port", "--web-port"].includes(key) ||
      !/^\d+$/.test(value ?? "")
    )
      throw new Error("Use --java-port=8081 --web-port=5173 [--preview]");
    const port = Number(value);
    if (port < 1024 || port > 65535)
      throw new Error("Ports must be 1024..65535");
    options[key === "--java-port" ? "javaPort" : "webPort"] = port;
  }
  if (options.javaPort === options.webPort)
    throw new Error("Java and web ports must differ");
  return options;
}

export async function assertPortAvailable(port) {
  await new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", (error) =>
      reject(
        new Error(
          `Port ${port} is unavailable (${error.code}). Keep the existing service or choose another port; nothing was stopped.`,
        ),
      ),
    );
    probe.listen({ host: "127.0.0.1", port, exclusive: true }, () =>
      probe.close(resolve),
    );
  });
}

export function powerShellPath() {
  if (process.platform !== "win32" || !process.env.SystemRoot)
    throw new Error(
      "These launch scripts target Windows PowerShell and JDK 17+.",
    );
  return path.join(
    process.env.SystemRoot,
    "System32",
    "WindowsPowerShell",
    "v1.0",
    "powershell.exe",
  );
}

export async function npmCliPath() {
  const candidate = path.join(
    path.dirname(process.execPath),
    "node_modules",
    "npm",
    "bin",
    "npm-cli.js",
  );
  await access(candidate).catch(() => {
    throw new Error(
      "npm CLI was not found beside Node. Use the documented manual npm.cmd commands.",
    );
  });
  return candidate;
}

export async function checkPrerequisites({ preview = false } = {}) {
  if (!supportsNodeVersion(process.versions.node))
    throw new Error(
      "Node 22.18.x+ or 24.12+ is required; see fitness-agent-web/package.json.",
    );
  const required = [
    path.join(webRoot, "node_modules", "vite", "bin", "vite.js"),
    path.join(javaRoot, "src", "main", "resources", "catalog.json"),
  ];
  if (preview) required.push(path.join(webRoot, "dist", "index.html"));
  for (const file of required)
    await access(file).catch(() => {
      throw new Error(
        `Missing ${path.relative(workspace, file)}. Install existing web dependencies or run check-all.cmd before preview.`,
      );
    });
  await npmCliPath();
}

export async function runTool(
  command,
  args,
  { cwd = workspace, timeoutMs = 180_000 } = {},
) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    const record = (chunk, stream) => {
      const text = chunk.toString();
      output = (output + text).slice(-200_000);
      stream.write(text);
    };
    child.stdout.on("data", (chunk) => record(chunk, process.stdout));
    child.stderr.on("data", (chunk) => record(chunk, process.stderr));
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Command exceeded its time limit."));
    }, timeoutMs);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code) => {
      clearTimeout(timer);
      if (code !== 0)
        reject(
          new Error(`Command exited with ${code}: ${output.slice(-3000)}`),
        );
      else resolve(output);
    });
  });
}
