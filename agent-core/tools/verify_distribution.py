"""Verify one wheel in a temporary, non-editable environment outside the checkout.

Build first with ``python -m build --wheel``, then pass the resulting .whl.
Only the local wheel is installed; this check never calls a model or package index.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from uuid import uuid4
import venv


def run_smoke(command, *, timeout, **kwargs):
    """Keep CLI descendants in the smoke process's timeout/interrupt boundary."""
    process = subprocess.Popen(command, start_new_session=os.name != "nt", **kwargs)
    try:
        returncode = process.wait(timeout=timeout)
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    except BaseException:
        if os.name == "nt":
            if process.poll() is None:
                # Killing only Python leaves the CLI it launched running on Windows.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=10)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        raise


def verify(wheel, output):
    wheel = wheel.resolve(strict=True)
    if wheel.is_dir():
        wheels = sorted(wheel.glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("wheel directory must contain exactly one .whl; pass a specific file otherwise")
        wheel = wheels[0]
    if wheel.suffix != ".whl" or not wheel.is_file():
        raise ValueError("expected a wheel file")
    wheel_bytes = wheel.read_bytes()
    directory = output.resolve() / f"wheel-{uuid4().hex}"
    directory.mkdir(parents=True)
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel_bytes).hexdigest(),
              "overall": "FAIL", "checks": [], "network": "loopback HTTP only; no model calls"}
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ("PYTHONPATH", "PYTHONHOME") and not key.upper().startswith("AGENT_MODEL_")}
    try:
        with tempfile.TemporaryDirectory(prefix="agent-core-wheel-") as temporary:
            workspace = Path(temporary)
            if workspace.resolve().is_relative_to(Path(__file__).resolve().parent.parent):
                raise ValueError("temporary workspace must be outside the checkout")
            environment = workspace / "venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            installed_wheel = workspace / wheel.name
            installed_wheel.write_bytes(wheel_bytes)
            smoke = workspace / "installed_smoke.py"
            shutil.copyfile(Path(__file__).with_name("installed_smoke.py"), smoke)
            with (directory / "install.log").open("w", encoding="utf-8") as log:
                subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "install",
                                "--no-index", "--no-deps", "--disable-pip-version-check", str(installed_wheel)],
                               cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=120)
            report["checks"].append({"name": "non_editable_wheel_install", "passed": True})
            dependency_check = {"name": "installed_dependencies_satisfied", "passed": False}
            report["checks"].append(dependency_check)
            with (directory / "dependencies.log").open("w", encoding="utf-8") as log:
                subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "check"],
                               cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=30)
            dependency_check["passed"] = True
            with (directory / "smoke.log").open("w", encoding="utf-8") as log:
                run_smoke([str(python), "-I", str(smoke), str(directory)], cwd=workspace, env=env,
                          stdout=log, stderr=subprocess.STDOUT, timeout=90)
            observation = json.loads((directory / "installed.json").read_text(encoding="utf-8"))
            report["installed"] = observation
            report["overall"] = "PASS" if observation["overall"] == "PASS" else "FAIL"
    except Exception as exc:
        # Logs contain only this synthetic check; arbitrary exception text is not copied into the report.
        report["error_type"] = type(exc).__name__
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return directory, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel file or directory containing exactly one wheel")
    parser.add_argument("--output", type=Path, default=Path("outputs/distribution"))
    args = parser.parse_args(argv)
    directory, report = verify(args.wheel, args.output)
    print(json.dumps({"result": report["overall"], "report": str(directory / "report.json")}))
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
