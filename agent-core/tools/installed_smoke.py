"""Copied out of the checkout and run with the installed venv's Python -I."""

from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
from time import monotonic, sleep
import urllib.error
import urllib.request


def verify(directory):
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError(f"distribution check failed: {name}")

    report = {"overall": "FAIL", "python": platform.python_version(), "platform": platform.platform(),
              "checks": checks}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    executable = Path(sys.executable).parent / ("agent-core-server.exe" if os.name == "nt" else "agent-core-server")

    def request(base, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = opener.open(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read()

    def api(base, path, payload=None):
        status, body = request(base, path, payload)
        check(f"HTTP {path}", status == 200)
        return json.loads(body)

    @contextmanager
    def server(name, configs):
        command = [str(executable), "--host", "127.0.0.1", "--port", "0", "--store-dir", "runs"]
        for config in configs:
            command.extend(["--config", str(config)])
        log_path = directory / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = monotonic() + 20
                base = None
                while monotonic() < deadline and process.poll() is None:
                    match = re.search(r"listening on (http://127\.0\.0\.1:\d+)",
                                      log_path.read_text(encoding="utf-8", errors="replace"))
                    if match:
                        base = match.group(1)
                        break
                    sleep(0.05)
                check(f"{name}: CLI started", base is not None)
                yield base
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    try:
        import agent_core
        import agent_lite
        from agent_core import AgentBuilder, NodeCallPolicy, NodeResult, Tool, ToolRegistry

        environment = Path(sys.prefix).resolve()
        check("isolated venv", sys.prefix != sys.base_prefix and bool(sys.flags.isolated))
        for module in (agent_core, agent_lite):
            check(f"{module.__name__} imported from venv", Path(module.__file__).resolve().is_relative_to(environment))
        metadata = importlib.metadata.distribution("agent-core")
        report["version"] = agent_core.__version__
        report["package_location"] = str(Path(agent_core.__file__).resolve())
        check("metadata version", metadata.version == agent_core.__version__)
        tools = ToolRegistry()
        tools.register(Tool("echo", lambda args: args))
        agent = (AgentBuilder("installed-api", tools=tools)
                 .add_node("echo", lambda ctx: NodeResult.success({"echo": dict(ctx.calls.tool("echo", {"text": "ok"}))}),
                           calls=NodeCallPolicy(allowed_tools=frozenset({"echo"})))
                 .set_entry("echo").build())
        result = agent.run({})
        check("public managed tool API", result.status.value == "COMPLETED" and result.state["echo"] == {"text": "ok"})
        help_result = subprocess.run([str(executable), "--help"], capture_output=True, text=True, timeout=10)
        check("console entry point", help_result.returncode == 0 and "--store-dir" in help_result.stdout)
        assets = Path(sysconfig.get_path("data")) / "share" / "agent-core"
        configs = [assets / "examples" / name for name in ("support_agent.json", "assistant_agent.json", "recovery_agent.json")]
        check("installed example configs", all(path.is_file() for path in configs))
        failure_config = Path.cwd() / "failure.json"
        failure_config.write_text(json.dumps({"name": "installed-failure", "entry": "missing",
            "nodes": [{"id": "missing", "type": "template", "params": {"updates": {"value": "{absent}"}}}],
            "edges": []}), encoding="utf-8")
        configs.append(failure_config)
        with server("first-start", configs) as base:
            health = api(base, "/api/health")
            check("no external model configured", health["model_configured"] is False)
            check("all configs loaded", set(health["agents"])
                  == {"workbench", "support-triage", "assistant", "recovery-router", "installed-failure"})
            for url, filename in (("/", "index.html"), ("/app.js", "app.js"), ("/styles.css", "styles.css"),
                                  ("/vendor/lucide-0.468.0.min.js", "vendor/lucide-0.468.0.min.js")):
                status, body = request(base, url)
                check(f"installed frontend {filename}", status == 200 and body == (assets / "frontend" / filename).read_bytes())
            check("installed icon license", (assets / "frontend/vendor/lucide-LICENSE.txt").is_file())
            payload = {"agent": "support-triage", "input": {"task": "check wheel; inspect result"}}
            completed = api(base, "/api/runs", payload)["run"]
            check("declarative tool workflow", completed["status"] == "COMPLETED"
                  and completed["state"]["step_count"] == 2 and "summary" in completed["state"])
            workbench = api(base, "/api/runs", {"agent": "workbench", "input": {"task": "installed workbench"}})["run"]
            check("bundled workbench run", workbench["status"] == "COMPLETED")
            blocked = api(base, "/api/runs", {"agent": "workbench", "input": {}})["run"]
            check("blocked input reason", (blocked["status"], blocked["reason_code"]) == ("BLOCKED", "EMPTY_TASK"))
            failed = api(base, "/api/runs", {"agent": "installed-failure", "input": {}})["run"]
            check("failed workflow reason", (failed["status"], failed["reason_code"]) == ("FAILED", "TEMPLATE_KEY_MISSING"))
            missing_model = api(base, "/api/runs", {"agent": "assistant", "session_id": "smoke-session",
                                                  "input": {"task": "no model call expected"}})["run"]
            check("unconfigured model blocks", (missing_model["status"], missing_model["reason_code"])
                  == ("BLOCKED", "MODEL_NOT_CONFIGURED"))
            status, body = request(base, "/api/runs/unknown-smoke-run")
            check("unknown run query", status == 404 and json.loads(body)["error"]["code"] == "UNKNOWN_RUN")
            events = api(base, f"/api/runs/{completed['run_id']}/harness-events")["events"]
            check("persisted tool audit", any(event["event_type"] == "tool.completed" for event in events))
        # A new CLI process reads the same persisted files after the first exits.
        with server("restart", configs) as base:
            restored = api(base, f"/api/runs/{completed['run_id']}")["run"]
            check("result survives process restart", restored["state"] == completed["state"] and restored["status"] == "COMPLETED")
            restored = api(base, f"/api/runs/{blocked['run_id']}")["run"]
            check("blocked result query after restart", restored["reason_code"] == "EMPTY_TASK")
            restored = api(base, f"/api/runs/{failed['run_id']}")["run"]
            check("failed result query after restart", (restored["status"], restored["reason_code"])
                  == ("FAILED", "TEMPLATE_KEY_MISSING"))
        report["overall"] = "PASS"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (directory / "installed.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve())
