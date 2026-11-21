"""Application acceptance reports must reflect actual execution and fixed criteria."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_core import AgentBuilder, HarnessLimits, NodeCallPolicy, NodeResult
from examples.acceptance_scenarios import build_scenario, case_token

from examples.workflow_acceptance import FIXTURES, effect_count, execute_case, run_suite, scripted_http


class WorkflowAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.fixtures = json.loads(FIXTURES.read_bytes())

    def only(self, case_id):
        fixtures = deepcopy(self.fixtures)
        fixtures["cases"] = [case for case in fixtures["cases"] if case["id"] == case_id]
        return fixtures

    def test_offline_http_suite_repeats_all_cases_with_persisted_evidence(self):
        directory, report = run_suite(self.output, repeat=2)
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(report["model_quality"], "NOT_EVALUATED_SCRIPTED")
        self.assertEqual(report["real_model_status"], "NOT_RUN_OFFLINE_MODE")
        self.assertEqual(len(report["cases"]), 28)
        self.assertEqual(set(row["status"] for row in report["stability"].values()), {"CONSISTENT"})
        self.assertEqual(report["input_sha256"], hashlib.sha256((directory / "fixtures.json").read_bytes()).hexdigest())
        for row in report["cases"]:
            self.assertTrue(row["passed"], row)
            self.assertTrue((directory / row["directory"] / row["evidence"]["run_result"]).is_file())
            self.assertTrue((directory / row["directory"] / row["evidence"]["audit"]).is_file())
            self.assertEqual(row["usage"]["status"], "NOT_REPORTED")
            self.assertIsNone(row["usage"]["totals"])
        restart = [row for row in report["cases"] if row["case_id"] == "triage-support-restart"]
        for row in restart:
            self.assertEqual(row["calls"]["model_adapter"], 1)
            self.assertEqual(row["effects"], 1)
            self.assertEqual([step["status"] for step in row["transitions"]], ["WAITING_APPROVAL", "COMPLETED"])
        self.assertEqual(sum(report["scripted_http_requests"].values()),
                         sum(row["calls"]["model_adapter"] for row in report["cases"]))

    def test_structurally_valid_wrong_classification_fails_task_not_framework(self):
        fixtures = self.only("triage-ops-approve")
        fixtures["cases"][0]["scripted_reply"]["queue"] = "support"
        _, report = run_suite(self.output, repeat=1, fixtures=fixtures)
        row = report["cases"][0]
        self.assertEqual(row["status"], "COMPLETED")
        self.assertTrue(row["framework"]["passed"])
        self.assertFalse(row["task"]["passed"])
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(row["artifact"]["queue"], "support")

    def test_model_prompt_does_not_expose_expected_labels_or_scripted_answers(self):
        fixture = self.only("review-ready")
        case = fixture["cases"][0]
        captured = []

        class Model:
            def complete(self, messages):
                captured.append(messages)
                return {"content": json.dumps(case["scripted_reply"])}

        row = execute_case(case, fixture, self.output / "opaque-case", Model(), "offline")
        self.assertTrue(row["passed"])
        user = json.loads(captured[0][-1]["content"])
        self.assertRegex(user["case_id"], r"^[0-9a-f]{64}$")
        self.assertNotIn(case["id"], json.dumps(captured))
        self.assertNotIn("expected", user)
        self.assertNotIn("scripted_reply", user)

    def test_invented_evidence_fails_before_lookup_and_assignment(self):
        fixtures = self.only("triage-ops-approve")
        fixtures["cases"][0]["scripted_reply"]["evidence"] = ["invented citation"]
        _, report = run_suite(self.output, repeat=1, fixtures=fixtures)
        row = report["cases"][0]
        self.assertEqual(row["reason_code"], "INVALID_TRIAGE_RESULT")
        self.assertEqual(row["calls"]["tools"], {})
        self.assertEqual(row["effects"], 0)
        self.assertTrue(row["framework"]["passed"])
        self.assertFalse(row["task"]["passed"])

    def test_unconfigured_real_model_is_not_run_or_reported_as_pass(self):
        with patch("examples.workflow_acceptance.scripted_http") as stub:
            directory, report = run_suite(self.output, mode="real", environ={})
        stub.assert_not_called()
        self.assertEqual(report["overall"], "NOT_RUN")
        self.assertEqual(report["real_model_status"], "MODEL_NOT_CONFIGURED")
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["model_quality"], "NOT_EVALUATED")
        self.assertEqual(json.loads((directory / "report.json").read_text(encoding="utf-8")), report)

    def test_real_mode_http_wiring_uses_config_and_does_not_record_credentials(self):
        # This is a local transport test, not a real-model quality evaluation.
        fixtures = self.only("review-ready")
        secret = "unit-test-secret-do-not-record"
        with scripted_http(fixtures) as (adapter, requests):
            env = {"AGENT_MODEL_BASE_URL": adapter.base_url, "AGENT_MODEL_API_KEY": secret,
                   "AGENT_MODEL_NAME": "unit-test-local-http"}
            directory, report = run_suite(self.output, mode="real", repeat=1, environ=env, fixtures=fixtures)
        self.assertEqual(report["overall"], "PASS")
        self.assertEqual(report["real_model_status"], "EXECUTED")
        self.assertEqual(requests["review-ready"], 1)
        self.assertEqual(report["model"]["configured_name"], "unit-test-local-http")
        self.assertNotIn(secret, (directory / "report.json").read_text(encoding="utf-8"))
        self.assertNotIn("api_key", json.dumps(report))

    def test_unexpected_errors_cannot_leak_exception_text_or_pass(self):
        secret = "private-provider-error-body"
        with patch("examples.workflow_acceptance.build_scenario", side_effect=RuntimeError(secret)):
            directory, report = run_suite(self.output, repeat=1, fixtures=self.only("review-ready"))
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["cases"][0]["error_type"], "RuntimeError")
        self.assertNotIn(secret, (directory / "report.json").read_text(encoding="utf-8"))

    def test_pre_model_blocks_cannot_be_reported_as_real_model_acceptance(self):
        fixtures = self.only("review-empty")
        with scripted_http(fixtures) as (adapter, requests):
            env = {"AGENT_MODEL_BASE_URL": adapter.base_url, "AGENT_MODEL_API_KEY": "test-only",
                   "AGENT_MODEL_NAME": "unit-test-local-http"}
            _, report = run_suite(self.output, mode="real", repeat=1, environ=env, fixtures=fixtures)
        self.assertTrue(report["cases"][0]["passed"])
        self.assertEqual(report["overall"], "NOT_RUN")
        self.assertEqual(report["real_model_status"], "NO_MODEL_CALLS")
        self.assertEqual(report["model_observations"], {"adapter_calls": 0, "completed_replies": 0})
        self.assertEqual(requests, {})

    def test_real_mode_execution_errors_remain_failures_before_any_model_call(self):
        fixtures = self.only("review-ready")
        with scripted_http(fixtures) as (adapter, _):
            env = {"AGENT_MODEL_BASE_URL": adapter.base_url, "AGENT_MODEL_API_KEY": "test-only",
                   "AGENT_MODEL_NAME": "unit-test-http"}
            with patch("examples.workflow_acceptance.build_scenario", side_effect=RuntimeError("internal error")):
                _, report = run_suite(self.output, mode="real", repeat=1, environ=env, fixtures=fixtures)
        self.assertEqual(report["cases"][0]["status"], "ERROR")
        self.assertEqual(report["overall"], "FAIL")

    def test_post_model_exception_preserves_observed_calls_and_evidence(self):
        fixtures = self.only("review-ready")

        def fail_after_execution(*args, **kwargs):
            execute_case(*args, **kwargs)
            raise RuntimeError("private error body")

        with scripted_http(fixtures) as (adapter, requests):
            env = {"AGENT_MODEL_BASE_URL": adapter.base_url, "AGENT_MODEL_API_KEY": "test-only",
                   "AGENT_MODEL_NAME": "unit-test-http"}
            with patch("examples.workflow_acceptance.execute_case", new=fail_after_execution):
                directory, report = run_suite(self.output, mode="real", repeat=1, environ=env, fixtures=fixtures)
        self.assertEqual(requests["review-ready"], 1)
        self.assertEqual(report["overall"], "FAIL")
        self.assertEqual(report["model_observations"], {"adapter_calls": 1, "completed_replies": 1})
        row = report["cases"][0]
        self.assertTrue((directory / row["directory"] / row["evidence"]["run_result"]).is_file())
        self.assertNotIn("private error body", json.dumps(report))

    def test_real_mode_cannot_pass_a_fabricated_assignment_without_side_effect(self):
        fixtures = self.only("triage-ops-approve")

        def build_without_commit(case, fixtures, directory, model, counters):
            _, runtime, audit, decisions = build_scenario(case, fixtures, directory, model, counters)

            def broken_workflow(ctx):
                ctx.calls.model([{"role": "user", "content": json.dumps({"case_id": case_token(case["id"])})}])
                return NodeResult.success({"classification": {"queue": "ops"},
                                           "directory": {"owner": "ops-team"}, "assignment": {"assigned": True}})

            agent = (AgentBuilder("lost-write", model=model, audit=audit, run_limits=HarnessLimits(1, 2))
                     .add_node("broken", broken_workflow, calls=NodeCallPolicy())
                     .set_entry("broken").build(runtime=runtime))
            return agent, runtime, audit, decisions

        with scripted_http(fixtures) as (adapter, _):
            env = {"AGENT_MODEL_BASE_URL": adapter.base_url, "AGENT_MODEL_API_KEY": "test-only",
                   "AGENT_MODEL_NAME": "unit-test-http"}
            with patch("examples.workflow_acceptance.build_scenario", new=build_without_commit):
                _, report = run_suite(self.output, mode="real", repeat=1, environ=env, fixtures=fixtures)
        row = report["cases"][0]
        self.assertTrue(row["framework"]["passed"])
        self.assertEqual(row["effects"], 0)
        self.assertFalse(row["task"]["passed"])
        self.assertEqual(report["overall"], "FAIL")

    def test_assignment_ledger_content_is_checked_in_addition_to_count(self):
        fixtures = self.only("triage-ops-approve")

        def corrupt_ledger(directory):
            path = directory / "assignments.jsonl"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                record["owner"] = "wrong-owner"
                path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            return effect_count(directory)

        with patch("examples.workflow_acceptance.effect_count", new=corrupt_ledger):
            _, report = run_suite(self.output, repeat=1, fixtures=fixtures)
        self.assertEqual(report["cases"][0]["effects"], 1)
        self.assertFalse(report["cases"][0]["framework"]["passed"])
        self.assertEqual(report["overall"], "FAIL")

    def test_unreadable_audit_after_error_reports_unknown_reply_count(self):
        fixtures = self.only("review-ready")

        for corruption in ("invalid-json", "[]", None):
            with self.subTest(corruption=corruption):
                def corrupt_after_call(*args, **kwargs):
                    row = execute_case(*args, **kwargs)
                    audit = args[2] / row["evidence"]["audit"]
                    if corruption is None:
                        audit.unlink()
                    else:
                        audit.write_text(corruption, encoding="utf-8")
                    raise RuntimeError("reporting failure")

                with patch("examples.workflow_acceptance.execute_case", new=corrupt_after_call):
                    _, report = run_suite(self.output, repeat=1, fixtures=fixtures)
                self.assertEqual(report["schema_version"], 2)
                self.assertEqual(report["overall"], "FAIL")
                self.assertEqual(report["model_observations"]["adapter_calls"], 1)
                self.assertIsNone(report["model_observations"]["completed_replies"])
                self.assertEqual(report["cases"][0]["usage"]["status"], "EVIDENCE_UNAVAILABLE")

    def test_unsafe_or_duplicate_case_ids_are_rejected_before_writing(self):
        fixtures = self.only("review-ready")
        fixtures["cases"][0]["id"] = "../escape"
        with self.assertRaises(ValueError):
            run_suite(self.output, fixtures=fixtures)
        fixtures = self.only("review-ready")
        fixtures["cases"].append(deepcopy(fixtures["cases"][0]))
        with self.assertRaises(ValueError):
            run_suite(self.output, fixtures=fixtures)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_real_cli_exits_two_when_model_configuration_is_absent(self):
        env = dict(os.environ)
        for key in ("AGENT_MODEL_BASE_URL", "AGENT_MODEL_API_KEY", "AGENT_MODEL_NAME"):
            env.pop(key, None)
        process = subprocess.run([sys.executable, "-m", "examples.workflow_acceptance", "--mode", "real",
                                  "--output", str(self.output)], capture_output=True, text=True, env=env,
                                 cwd=Path(__file__).resolve().parent.parent, timeout=15)
        self.assertEqual(process.returncode, 2, process.stderr)
        summary = json.loads(process.stdout)
        self.assertEqual(summary["result"], "NOT_RUN")
        self.assertEqual(summary["cases"], 0)


if __name__ == "__main__":
    unittest.main()
