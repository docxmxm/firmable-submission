"""A runnable but historically wrong candidate must be revised by the planner."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import _feedback, onboard_source
from firmable_runtime.common import Failure
from firmable_runtime.schema import check_response
from test_agent import NativeFixture, ScriptedProvider, control
from test_config_audit import scripted_branches


class NameRoleFixture(NativeFixture):
    def __init__(self, root):
        super().__init__(root, field="a17")
        self.definition = "a17: Registered legal name of the company on this row."
        self.rule = "b54: Y means a17 is current; null means a17 is historical and c99 holds the current registered legal name."
        (self.evidence / "definitions.md").write_text(
            self.definition
            + "\n"
            + self.rule
            + "\nc99: Current legal name of the company.\nLicence: CC BY 4.0\n"
        )
        self.rows = [
            {
                "a17": f"Current {i} Pty Ltd" if i % 2 else f"Former {i} Pty Ltd",
                "b54": "Y" if i % 2 else "",
                "c99": "" if i % 2 else f"Current {i} Pty Ltd",
            }
            for i in range(8)
        ]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["a17", "b54", "c99"])
            writer.writeheader()
            writer.writerows(self.rows)

    def config(self, bad=False):
        config = super().config()
        config["unmapped_fields"] = []
        if bad:
            config["unmapped_fields"] = [
                {
                    "source_field": name,
                    "reason": "Ignored qualifier in deliberately wrong fixture.",
                }
                for name in ("b54", "c99")
            ]
        else:
            config["field_mappings"][0]["source_fields"] = ["a17", "b54", "c99"]
            config["execution"]["transforms"][0] = {
                "op": "when",
                "condition": {
                    "op": "equals",
                    "input": {"op": "field", "name": "b54"},
                    "value": "Y",
                },
                "then": {"op": "field", "name": "a17"},
                "else": {"op": "field", "name": "c99"},
            }
        return config


class NameRoleProvider(ScriptedProvider):
    def generate(self, request, call_id):
        if request["purpose"] != "name_role_review":
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        executable = request["payload"]["name_mappings"][0]["executable"]
        response = {
            "reviews": [
                {
                    "mapping_index": 0,
                    "verdict": "supported"
                    if executable["op"] == "when"
                    else "contradicted",
                    "evidence_quotes": [
                        {"kind": "documentation", "quote": self.fixture.rule}
                    ],
                    "reason": "Current name selection must follow the original documented marker.",
                    "repair_hint": "Use b54 and c99 to preserve the current name on historical rows.",
                }
            ]
        }
        response = scripted_branches(response, request["payload"])
        check_response(response, request["output_schema"])
        return {
            "config": response,
            "cost_usd": 0,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


class AgentNameReviewTests(unittest.TestCase):
    def test_valid_execution_with_wrong_name_role_is_revised_then_selected(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NameRoleFixture(root)
            provider = NameRoleProvider(fixture, ["bad", "good", "select"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "complete")
            decisions = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ]
            rejected = decisions[1]["payload"]["feedback"]
            self.assertGreater(
                rejected["output_summary"]["informative_field_value_count"], 0
            )
            self.assertGreater(rejected["error_count"], 0)
            self.assertIn("Use b54 and c99", rejected["errors"][0]["repair_hint"])
            self.assertTrue(
                all(
                    r["entity"]["legal_name"].startswith("Current ")
                    for r in result["observations"]
                )
            )
            self.assertIn("complete source definitions", decisions[0]["system"])
            self.assertFalse(
                result["validation_results"]["name_role_review"]["human_gold"]
            )
            reviews = [
                r for r in provider.requests if r["purpose"] == "name_role_review"
            ]
            self.assertIn("dispute_review", reviews[1]["payload"])
            self.assertTrue(
                all("prior_candidate_review" not in r["payload"] for r in reviews)
            )
            count = len(provider.requests)
            self.assertEqual(onboard_source(fixture.request, provider), result)
            self.assertEqual(len(provider.requests), count)
            self.assertTrue(
                (
                    Path(fixture.request["state_dir"]) / "artifacts/name-audit-bad.json"
                ).exists()
            )

    def test_name_review_runs_when_an_unrelated_field_fails(self):
        class OtherFieldFailureFixture(NameRoleFixture):
            def config(self, bad=False):
                config = super().config(bad)
                if bad:
                    config["field_mappings"].append(
                        {
                            "source_field": "c99",
                            "source_fields": ["c99"],
                            "canonical_field": "entity.abn",
                            "confidence": 0.9,
                            "transformation": "Deliberately unsupported identifier fixture.",
                            "semantic_evidence": "c99: Current legal name of the company.",
                            "execution_pointer": "/execution/transforms/1",
                            "validation_pass": False,
                        }
                    )
                    config["execution"]["transforms"].append(
                        {"op": "field", "name": "c99"}
                    )
                    config["unmapped_fields"] = [
                        item
                        for item in config["unmapped_fields"]
                        if item["source_field"] != "c99"
                    ]
                return config

        with tempfile.TemporaryDirectory() as root:
            fixture = OtherFieldFailureFixture(root)
            provider = NameRoleProvider(fixture, ["bad", "good", "select"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["status"], "complete")
            decisions = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ]
            rejected = decisions[1]["payload"]["feedback"]
            self.assertEqual(
                rejected["output_summary"]["informative_field_value_count"], 0
            )
            self.assertEqual(rejected["name_role_review"]["status"], "rejected")
            self.assertIn("Use b54 and c99", rejected["errors"][0]["repair_hint"])
            self.assertGreater(rejected["error_count"], 1)

    def test_bounded_feedback_does_not_hide_name_repair_behind_other_failures(self):
        report = {
            "executed_records": 0,
            "errors": [
                {"reason_code": "unrelated_error", "message": str(index)}
                for index in range(10)
            ]
            + [
                {
                    "reason_code": "name_role_contradicted",
                    "rule": "independent_name_role_review",
                    "repair_hint": "Use the documented current name on both flag branches.",
                }
            ],
        }
        feedback = _feedback(report, [], {"execution": {"kind": "ast"}})
        self.assertEqual(feedback["error_count"], 11)
        self.assertEqual(len(feedback["errors"]), 8)
        self.assertEqual(feedback["errors"][0]["reason_code"], "name_role_contradicted")
        self.assertIn("both flag branches", feedback["errors"][0]["repair_hint"])

    def test_unknown_name_review_stops_and_never_resends_on_resume(self):
        class InterruptedReview(NameRoleProvider):
            def generate(self, request, call_id):
                if request["purpose"] == "name_role_review":
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Review may have been processed")
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = NameRoleFixture(root)
            provider = InterruptedReview(fixture, ["good", "select"])
            for _ in range(2):
                with self.assertRaises(Failure) as error:
                    onboard_source(fixture.request, provider)
                self.assertEqual(error.exception.kind, "model_outcome_unknown")
            self.assertEqual(len(provider.requests), 6)

    def test_prior_review_binding_survives_sorted_checkpoint_and_unknown_outcome(self):
        class ReverseCandidateProvider(NameRoleProvider):
            def __init__(self, fixture):
                super().__init__(fixture)
                self.candidate_ids = iter(("z-first", "a-second", "m-third"))

            def generate(self, request, call_id):
                if request["purpose"] == "agent_decision":
                    self.requests.append(copy.deepcopy(request))
                    ident = next(self.candidate_ids)
                    config = self.fixture.config()
                    # Change reviewed behavior structurally. Mapper-only confidence
                    # changes correctly reuse the identical independent request.
                    for _ in range({"z-first": 0, "a-second": 1, "m-third": 2}[ident]):
                        expression = config["execution"]["transforms"][0]
                        expression["then"] = {"op": "trim", "input": expression["then"]}
                    self.candidate_configs[ident] = config
                    return {
                        "config": control("probe", candidate_id=ident, backend="ast"),
                        "cost_usd": 0,
                        "usage": {},
                    }
                if request["purpose"] == "name_role_review":
                    calls = [
                        item
                        for item in self.requests
                        if item["purpose"] == "name_role_review"
                    ]
                    if len(calls) >= 2:
                        self.requests.append(copy.deepcopy(request))
                        raise TimeoutError(
                            "Unknown review must not reintroduce prior opinions"
                        )
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = NameRoleFixture(root)
            provider = ReverseCandidateProvider(fixture)
            for _ in range(2):
                with self.assertRaises(Failure) as error:
                    onboard_source(fixture.request, provider)
                self.assertEqual(error.exception.kind, "model_outcome_unknown")
            run = Path(fixture.request["state_dir"])
            checkpoint = json.loads((run / "checkpoint.internal.json").read_text())
            self.assertEqual(
                list(checkpoint["agent"]["candidates"]), ["a-second", "z-first"]
            )
            binding = json.loads(
                (run / "artifacts/name-review-context-m-third.json").read_text()
            )
            self.assertEqual(binding["prior_review_artifact"], "name-audit-a-second")
            reviews = [
                item
                for item in provider.requests
                if item["purpose"] == "name_role_review"
            ]
            decisions = [
                item
                for item in provider.requests
                if item["purpose"] == "agent_decision"
            ]
            self.assertEqual(len(reviews), 3)
            self.assertEqual(len(decisions), 3)
            self.assertTrue(
                all("prior_candidate_review" not in r["payload"] for r in reviews)
            )


if __name__ == "__main__":
    unittest.main()
