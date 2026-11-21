"""Run fixed application acceptance cases through a local HTTP stub or a real model.

python -m examples.workflow_acceptance --mode offline --repeat 2
python -m examples.workflow_acceptance --mode real --repeat 2
"""

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import threading
from time import monotonic
from uuid import uuid4

from agent_core import FileCheckpointStore, FileHarnessAuditStore, RunConflictError, RunStatus
from agent_core.models import OpenAICompatibleModel, model_from_environment
from .acceptance_scenarios import artifact, build_scenario, case_token


FIXTURES = Path(__file__).with_name("acceptance_cases.json")


class CountingModel:
    """Use the non-streaming HTTP adapter and count actual adapter invocations."""

    def __init__(self, adapter, counters):
        self.adapter, self.counters = adapter, counters

    def complete(self, messages):
        self.counters["model"] = self.counters.get("model", 0) + 1
        return self.adapter.complete(messages)


@contextmanager
def scripted_http(fixtures):
    """Explicitly scripted replies, with no access to expected assertions."""
    cases = {case_token(case["id"]): case for case in fixtures["cases"]}
    requests = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000 or self.path != "/v1/chat/completions":
                    raise ValueError("invalid stub request")
                payload = json.loads(self.rfile.read(size))
                user = json.loads(payload["messages"][-1]["content"])
                case = cases[user["case_id"]]
            except (ValueError, KeyError, TypeError, IndexError):
                self.send_error(400)
                return
            requests[case["id"]] += 1
            status = case.get("http_status", 200)
            body = ({"choices": [{"message": {"content": json.dumps(case.get("scripted_reply", {}))}}]}
                    if status == 200 else {"error": {"code": "SCRIPTED_UNAVAILABLE"}})
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield OpenAICompatibleModel(f"http://127.0.0.1:{server.server_port}/v1",
                                    "offline-not-a-secret", "scripted-http", timeout_seconds=5), requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def validate_fixtures(fixtures):
    if fixtures.get("version") != "m10-v1" or not isinstance(fixtures.get("cases"), list):
        raise ValueError("unsupported acceptance fixtures")
    ids = [case.get("id") for case in fixtures["cases"]]
    if any(not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None for value in ids):
        raise ValueError("invalid case id")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case ids")
    for case in fixtures["cases"]:
        if case.get("scenario") not in ("doc_review", "task_triage") or "expected" not in case:
            raise ValueError("invalid acceptance case")
        if case.get("action") not in (None, "approve", "approve_restart", "reject"):
            raise ValueError("invalid approval action")


def assignment_records(directory):
    path = directory / "assignments.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if (not isinstance(record, dict) or set(record) != {"task_id", "queue", "owner"}
                or any(not isinstance(value, str) or not value.strip() for value in record.values())):
            raise ValueError("invalid assignment evidence")
    return records


def effect_count(directory):
    return len(assignment_records(directory))


def usage_report(events):
    completed = [event for event in events if event.event_type == "model.completed"]
    reported = [event.summary.get("usage", {}) for event in completed if event.summary.get("usage")]
    totals = {}
    for usage in reported:
        for key, value in usage.items():
            totals[key] = totals.get(key, 0) + value
    return {"status": "REPORTED" if reported else "NOT_REPORTED", "totals": totals or None,
            "reported_calls": len(reported), "completed_calls": len(completed)}


def execute_case(case, fixtures, directory, adapter, mode, *, counters=None):
    if counters is None:
        counters = {"model": 0, "tools": Counter()}
    counters["run_id"] = uuid4().hex
    model = CountingModel(adapter, counters)
    agent, runtime, audit, decisions = build_scenario(case, fixtures, directory, model, counters)
    started = monotonic()
    first = agent.run(case["input"], run_id=counters["run_id"])
    result = first.run
    transitions = [{"status": result.status.value, "reason_code": result.reason_code}]
    checks = {"no_effect_before_approval": effect_count(directory) == 0}
    action = case.get("action")
    if result.status is RunStatus.WAITING_APPROVAL and action:
        request_id = result.pending_approval["request_id"]
        if action == "reject":
            result = runtime.reject_approval(result.run_id, request_id, decisions)
            try:
                runtime.resume(agent.graph, FileCheckpointStore(directory / "checkpoints").load(result.run_id))
            except RunConflictError as exc:
                checks["rejected_resume_refused"] = exc.reason_code == "APPROVAL_REJECTED"
            else:
                checks["rejected_resume_refused"] = False
        else:
            before_model = counters["model"]
            before_tools = dict(counters["tools"])
            decisions.decide(result, request_id, "APPROVED")
            checks["approval_alone_has_no_effect"] = effect_count(directory) == 0
            if action == "approve_restart":
                agent, runtime, audit, decisions = build_scenario(case, fixtures, directory,
                                                                CountingModel(adapter, counters), counters)
            checkpoint = FileCheckpointStore(directory / "checkpoints").load(result.run_id)
            result = runtime.resume_approved(agent.graph, checkpoint, decisions=decisions)
            checks["model_not_repeated_on_resume"] = counters["model"] == before_model
            checks["reads_not_repeated_on_resume"] = all(
                counters["tools"][name] == count for name, count in before_tools.items())
        transitions.append({"status": result.status.value, "reason_code": result.reason_code})
    persisted = runtime.stored_result(result.run_id)
    events = audit.events(result.run_id)
    claims = [event for event in events if event.event_type == "run_budget.claimed"]
    reserved = Counter(event.summary["kind"] for event in claims)
    effects = effect_count(directory)
    records = assignment_records(directory)
    assignment = result.state.get("assignment", {})
    calls = dict(counters["tools"])
    decision = decisions.load(result.run_id)
    checks.update({
        "persisted_result_matches": persisted is not None and persisted.status == result.status
        and persisted.reason_code == result.reason_code and persisted.state == result.state,
        "model_budget_respected": reserved["model"] <= 1,
        "tool_budget_respected": reserved["tool"] <= case.get("max_tool_calls", 2),
        "model_attempts_match_adapter_calls": reserved["model"] == counters["model"],
        "tool_attempts_bound_invocations": sum(calls.values()) <= reserved["tool"],
        "effect_matches_assignment_invocations": effects == calls.get("assign_task", 0),
        "assignment_records_match_result": all(
            record == {key: assignment.get(key) for key in ("task_id", "queue", "owner")}
            and record["task_id"] == case["input"].get("task_id")
            and assignment.get("assigned") is True for record in records),
        "effect_requires_approved_wait": effects == 0 or (
            first.status is RunStatus.WAITING_APPROVAL and decision is not None and decision.decision == "APPROVED"),
    })
    expected = case["expected"]
    observed = artifact(case["scenario"], result.state)
    task_checks = {"status": result.status.value == expected["status"],
                   "reason_code": result.reason_code == expected["reason_code"],
                   "effects": effects == expected["effects"],
                   "artifact": all(observed.get(key) == value for key, value in expected.get("artifact", {}).items())}
    exact_counts = {"adapter_calls": counters["model"] == expected["adapter_calls"],
                    "tool_calls": calls == expected["tool_calls"], "effects": effects == expected["effects"]}
    return {
        "case_id": case["id"], "scenario": case["scenario"], "run_id": result.run_id,
        "status": result.status.value, "reason_code": result.reason_code,
        "duration_ms": round((monotonic() - started) * 1000, 2),
        "artifact": observed, "transitions": transitions,
        "framework": {"passed": all(checks.values()), "checks": checks},
        "task": {"passed": all(task_checks.values()), "checks": task_checks},
        "expected_path": {"passed": all(exact_counts.values()), "checks": exact_counts},
        "passed": all(checks.values()) and all(task_checks.values()) and
        (mode == "real" or all(exact_counts.values())),
        "calls": {"model_adapter": counters["model"], "tools": calls, "reserved_attempts": dict(reserved)},
        "effects": effects, "usage": usage_report(events),
        "config": {"max_model_calls": 1, "max_tool_calls": case.get("max_tool_calls", 2),
                   "capabilities": case.get("capabilities", ["documents.read"] if case["scenario"] == "doc_review"
                                            else ["queue.read", "queue.assign"]), "retries": 0, "streaming": False},
        "evidence": {"run_result": f"runs/{result.run_id}.json",
                     "audit": f"audit/{result.run_id}.harness.json"},
    }


def failed_case_observation(case, directory, counters, error):
    """Retain observed calls and persisted evidence even if reporting fails."""
    run_id = counters.get("run_id")
    evidence = {}
    if run_id is not None:
        for name, relative in (("run_result", f"runs/{run_id}.json"),
                               ("audit", f"audit/{run_id}.harness.json")):
            if (directory / relative).is_file():
                evidence[name] = relative
    try:
        if (counters["model"] or sum(counters["tools"].values())) and "audit" not in evidence:
            raise ValueError("invocation evidence is missing")
        events = FileHarnessAuditStore(directory / "audit").load(run_id) if run_id is not None else ()
        usage = usage_report(events)
        reserved = dict(Counter(event.summary["kind"] for event in events
                                if event.event_type == "run_budget.claimed"))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        usage = {"status": "EVIDENCE_UNAVAILABLE", "totals": None,
                 "reported_calls": None, "completed_calls": None}
        reserved = None
    try:
        effects = effect_count(directory)
    except (ValueError, OSError):
        effects = None
    return {"case_id": case["id"], "scenario": case["scenario"], "run_id": run_id,
            "passed": False, "status": "ERROR", "reason_code": "ACCEPTANCE_EXECUTION_ERROR",
            "error_type": type(error).__name__, "artifact": {}, "effects": effects,
            "framework": {"passed": False, "checks": {"execution_completed": False}},
            "task": {"passed": False, "checks": {"execution_completed": False}},
            "calls": {"model_adapter": counters["model"], "tools": dict(counters["tools"]),
                      "reserved_attempts": reserved},
            "usage": usage, "evidence": evidence}


def source_revision():
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root,
                                         stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root,
                                             stderr=subprocess.DEVNULL, text=True).strip())
        return {"commit": commit, "working_tree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "working_tree_dirty": None}


def run_suite(output: Path, *, mode="offline", repeat=2, environ=None, fixtures=None):
    if mode not in ("offline", "real") or isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= 10:
        raise ValueError("mode must be offline/real and repeat must be 1..10")
    raw = FIXTURES.read_bytes() if fixtures is None else json.dumps(fixtures, ensure_ascii=False, sort_keys=True).encode("utf-8")
    fixtures = json.loads(raw)
    validate_fixtures(fixtures)
    directory = output.resolve() / f"{mode}-{uuid4().hex}"
    directory.mkdir(parents=True)
    (directory / "fixtures.json").write_bytes(raw)
    report = {"schema_version": 2, "created_at": datetime.now(timezone.utc).isoformat(),
              "mode": mode, "input_version": fixtures["version"], "input_sha256": hashlib.sha256(raw).hexdigest(),
              "source": source_revision(), "repeat": repeat, "cases": [],
              "model_quality": "NOT_EVALUATED_SCRIPTED" if mode == "offline" else "PENDING",
              "real_model_status": "NOT_RUN_OFFLINE_MODE" if mode == "offline" else "PENDING",
              "model_observations": {"adapter_calls": 0, "completed_replies": 0},
              "stability_scope": "terminal status, reason and expected artifact fields; excludes prose wording"}
    adapter = None
    if mode == "real":
        try:
            adapter = model_from_environment(environ)
        except (ValueError, TypeError):
            report.update(overall="NOT_RUN", real_model_status="INVALID_MODEL_CONFIGURATION")
        if adapter is None:
            report.setdefault("overall", "NOT_RUN")
            if report["real_model_status"] == "PENDING":
                report["real_model_status"] = "MODEL_NOT_CONFIGURED"
            report["model_quality"] = "NOT_EVALUATED"
            write_report(directory, report)
            return directory, report
    context = scripted_http(fixtures) if mode == "offline" else nullcontext((adapter, None))
    with context as (adapter, http_requests):
        report["model"] = {"kind": "scripted_http" if mode == "offline" else "external",
                           "configured_name": adapter.model, "generation_parameters": {},
                           "provider_defaults": True}
        for case in fixtures["cases"]:
            if mode not in case.get("modes", ["offline", "real"]):
                continue
            for repetition in range(1, repeat + 1):
                case_dir = directory / f"{case['id']}-{repetition}"
                counters = {"model": 0, "tools": Counter()}
                try:
                    observed = execute_case(case, fixtures, case_dir, adapter, mode, counters=counters)
                except Exception as exc:
                    # Do not put provider error bodies, keys or arbitrary exception text in a report.
                    observed = failed_case_observation(case, case_dir, counters, exc)
                observed["repetition"] = repetition
                observed["directory"] = case_dir.name
                report["cases"].append(observed)
        if http_requests is not None:
            report["scripted_http_requests"] = dict(http_requests)
    report["stability"] = {}
    for case in fixtures["cases"]:
        rows = [row for row in report["cases"] if row["case_id"] == case["id"]]
        if not rows:
            continue
        signatures = {json.dumps([row["status"], row["reason_code"],
                                 {key: row["artifact"].get(key) for key in case["expected"].get("artifact", {})}],
                                sort_keys=True) for row in rows}
        report["stability"][case["id"]] = {"observations": len(rows), "distinct_outcomes": len(signatures),
                                             "status": "CONSISTENT" if len(rows) > 1 and len(signatures) == 1
                                             else "VARIED" if len(signatures) > 1 else "NOT_MEASURED"}
    report["overall"] = "PASS" if all(row["passed"] for row in report["cases"]) and report["cases"] else "FAIL"
    completed = [row["usage"]["completed_calls"] for row in report["cases"]]
    report["model_observations"] = {
        "adapter_calls": sum(row.get("calls", {}).get("model_adapter", 0) for row in report["cases"]),
        "completed_replies": None if any(count is None for count in completed) else sum(completed),
    }
    if mode == "real":
        report["real_model_status"] = "EXECUTED" if report["model_observations"]["adapter_calls"] else "NO_MODEL_CALLS"
        report["model_quality"] = ("FIXED_CASE_RESULTS_ONLY" if report["model_observations"]["completed_replies"]
                                   else "NOT_EVALUATED_NO_MODEL_REPLY")
        if report["model_observations"]["completed_replies"] is None:
            report["model_quality"] = "NOT_EVALUATED_EVIDENCE_UNAVAILABLE"
        if any(row["status"] == "ERROR" for row in report["cases"]):
            report["model_quality"] = "INCOMPLETE_EXECUTION_ERRORS"
        if report["real_model_status"] == "NO_MODEL_CALLS" and report["overall"] == "PASS":
            report["overall"] = "NOT_RUN"
    write_report(directory, report)
    return directory, report


def write_report(directory, report):
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Workflow acceptance", "", f"Mode: {report['mode']}; result: {report['overall']}",
             f"Input: {report['input_version']} / {report['input_sha256']}",
             f"Model quality: {report['model_quality']}", "",
             f"Real model: {report['real_model_status']}", "",
             "| Case | Status | Reason | Framework | Task | Passed |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in report["cases"]:
        lines.append(f"| {row['case_id']} / {row['repetition']} | {row['status']} | {row['reason_code'] or '-'} | "
                     f"{row.get('framework', {}).get('passed', 'ERROR')} | "
                     f"{row.get('task', {}).get('passed', 'ERROR')} | {row['passed']} |")
    if report["overall"] == "NOT_RUN":
        lines += ["", f"Real model check not executed: {report['real_model_status']}."]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "real"), default="offline")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("outputs/acceptance"))
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 10:
        parser.error("repeat must be between 1 and 10")
    directory, report = run_suite(args.output, mode=args.mode, repeat=args.repeat)
    print(json.dumps({"result": report["overall"], "mode": args.mode, "cases": len(report["cases"]),
                      "report": str(directory / "report.json")}, ensure_ascii=False))
    return 0 if report["overall"] == "PASS" else 2 if report["overall"] == "NOT_RUN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
