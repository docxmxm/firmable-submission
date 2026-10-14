"""The public workflow enforces review before processing complete native inputs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.common import Failure
from firmable_runtime.workflow import (
    approve_source,
    build_outputs,
    extract_source,
    infer_source,
    inspect_source,
    review_source,
    withdraw_outputs,
)
from test_agent import NativeFixture, ScriptedProvider


class NoCallsProvider:
    model_version = ScriptedProvider.model_version
    calls = 0

    def generate(self, request, call_id):
        self.calls += 1
        raise AssertionError("This operation must not dispatch a model request")


def approve_fixture(fixture):
    infer_source(fixture.request, ScriptedProvider(fixture))
    review = review_source(fixture.request["state_dir"])
    return approve_source(
        fixture.request["state_dir"],
        review["review_fingerprint"],
        "offline-test",
        synthetic=True,
    )


class WorkflowTests(unittest.TestCase):
    def test_known_candidate_loss_stays_partial_through_approval_and_full_extraction(
        self,
    ):
        from test_candidate_selection import TradeoffFixture, SelectionProvider, select

        with tempfile.TemporaryDirectory() as root:
            fixture = TradeoffFixture(root)
            provider = SelectionProvider(
                fixture, ["wide", "narrow", select("narrow"), select("narrow")]
            )
            result = infer_source(fixture.request, provider)
            review = review_source(fixture.request["state_dir"])
            self.assertEqual(
                review["candidate_selection"], result["candidate_selection"]
            )
            approval = approve_source(
                fixture.request["state_dir"],
                review["review_fingerprint"],
                "offline-test",
                synthetic=True,
            )
            self.assertTrue(approval["candidate_selection"]["unresolved"])
            extracted = extract_source(
                fixture.request["state_dir"],
                provider=NoCallsProvider(),
                allow_synthetic=True,
            )
            self.assertEqual(extracted["execution_status"], "complete")
            self.assertEqual(extracted["task_status"], "complete")
            self.assertEqual(extracted["status"], "partial")
            self.assertTrue(extracted["candidate_selection"]["unresolved"])

    def test_review_approval_then_full_extraction_with_no_additional_model_calls(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            run_dir = fixture.request["state_dir"]
            discovered = inspect_source(fixture.evidence, fixture.request["source_id"])
            self.assertEqual(discovered["data_files"], [fixture.native.name])
            self.assertTrue(discovered["tables"])
            infer_source(fixture.request, ScriptedProvider(fixture))
            provider = NoCallsProvider()
            with self.assertRaises(Failure):
                extract_source(run_dir, provider=provider)
            review = review_source(run_dir)
            with self.assertRaises(Failure) as error:
                approve_source(
                    run_dir, "stale-fingerprint", "offline-test", synthetic=True
                )
            self.assertEqual(error.exception.kind, "approval_mismatch")
            approval = approve_source(
                run_dir, review["review_fingerprint"], "offline-test", synthetic=True
            )
            self.assertTrue(approval["synthetic"])
            with self.assertRaises(Failure) as error:
                extract_source(run_dir, provider=provider)
            self.assertEqual(error.exception.kind, "approval_required")
            result = extract_source(run_dir, provider=provider, allow_synthetic=True)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["execution_status"], "complete")
            self.assertEqual(result["task_status"], "not_configured")
            self.assertEqual(result["report"]["errors"], [])
            self.assertEqual(len(result["observations"]), len(fixture.rows))
            self.assertEqual(
                {
                    json.dumps(row["raw_record"], sort_keys=True)
                    for row in result["observations"]
                },
                {json.dumps(row, sort_keys=True) for row in fixture.rows},
            )
            self.assertEqual(result["usage"]["call_count"], 0)
            self.assertEqual(result["usage"]["total_cost_usd"], 0)
            self.assertEqual(
                len((Path(run_dir) / "observations.jsonl").read_text().splitlines()), 8
            )
            self.assertEqual(
                extract_source(run_dir, provider=provider, allow_synthetic=True), result
            )
            self.assertEqual(provider.calls, 0)

    def test_source_ontology_and_frozen_config_changes_invalidate_approval(self):
        for changed in ("data", "documentation", "ontology", "config", "requirements"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as root:
                fixture = NativeFixture(root)
                approve_fixture(fixture)
                run_dir = Path(fixture.request["state_dir"])
                path = {
                    "data": fixture.native,
                    "documentation": fixture.evidence / "definitions.md",
                    "ontology": fixture.ontology,
                    "config": run_dir / "artifacts" / "final-config.json",
                    "requirements": run_dir / "request.json",
                }[changed]
                if changed == "requirements":
                    request = json.loads(path.read_text())
                    request["required_fields"] = ["entity.trading_name"]
                    path.write_text(json.dumps(request))
                else:
                    path.write_bytes(path.read_bytes() + b"\n")
                provider = NoCallsProvider()
                with self.assertRaises(Failure) as error:
                    extract_source(run_dir, provider=provider, allow_synthetic=True)
                self.assertIn(
                    error.exception.kind, {"checkpoint_mismatch", "checkpoint_failure"}
                )
                self.assertEqual(provider.calls, 0)
                self.assertFalse((run_dir / "observations.jsonl").exists())

    def test_validated_partial_candidate_retains_safe_values_and_unresolved_requirements(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            fixture.request["required_fields"] = [
                "entity.legal_name",
                "entity.trading_name",
            ]
            result = infer_source(fixture.request, ScriptedProvider(fixture))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["candidate_status"], "validated")
            run_dir = fixture.request["state_dir"]
            review = review_source(run_dir)
            self.assertEqual(review["task_status"], "partial")
            self.assertEqual(review["unresolved_requirements"], ["entity.trading_name"])
            approval = approve_source(
                run_dir, review["review_fingerprint"], "offline-test", synthetic=True
            )
            self.assertEqual(approval["task_status"], "partial")
            self.assertEqual(approval["task_acceptance"], review["task_acceptance"])
            provider = NoCallsProvider()
            extracted = extract_source(run_dir, provider=provider, allow_synthetic=True)
            self.assertEqual(extracted["execution_status"], "complete")
            self.assertEqual(extracted["status"], "partial")
            self.assertEqual(extracted["task_status"], "partial")
            fields = {
                item["canonical_field"]: item
                for item in extracted["task_acceptance"]["fields"]
            }
            self.assertEqual(fields["entity.legal_name"]["status"], "complete")
            self.assertEqual(fields["entity.legal_name"]["accepted_value_count"], 8)
            self.assertEqual(fields["entity.trading_name"]["status"], "needs_evidence")
            self.assertEqual(fields["entity.trading_name"]["accepted_value_count"], 0)
            self.assertEqual(len(extracted["observations"]), 8)
            self.assertTrue(
                all(
                    row["entity"].get("legal_name") for row in extracted["observations"]
                )
            )
            self.assertTrue(
                all(
                    not row["entity"].get("trading_name")
                    for row in extracted["observations"]
                )
            )
            self.assertEqual(
                extract_source(run_dir, provider=provider, allow_synthetic=True),
                extracted,
            )
            self.assertEqual(provider.calls, 0)

    def test_required_field_cannot_be_removed_when_resuming(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            fixture.request["required_fields"] = [
                "entity.legal_name",
                "entity.trading_name",
            ]
            infer_source(fixture.request, ScriptedProvider(fixture))
            changed = copy.deepcopy(fixture.request)
            changed["required_fields"].remove("entity.trading_name")
            provider = NoCallsProvider()
            with self.assertRaises(Failure) as error:
                infer_source(changed, provider)
            self.assertEqual(error.exception.kind, "checkpoint_mismatch")
            self.assertEqual(provider.calls, 0)

    def test_partial_onboarding_cannot_be_approved(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            result = infer_source(
                fixture.request, ScriptedProvider(fixture, ["bad", "stop"])
            )
            self.assertEqual(result["status"], "partial")
            with self.assertRaises(Failure) as error:
                approve_source(
                    fixture.request["state_dir"], "any", "offline-test", synthetic=True
                )
            self.assertEqual(error.exception.kind, "approval_required")

    def test_extraction_rejects_output_inside_source_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            approve_fixture(fixture)
            before = fixture.native.read_bytes()
            with self.assertRaises(Failure) as error:
                extract_source(
                    fixture.request["state_dir"],
                    output=fixture.native,
                    provider=NoCallsProvider(),
                    allow_synthetic=True,
                )
            self.assertEqual(error.exception.kind, "validation_error")
            self.assertEqual(fixture.native.read_bytes(), before)

    def test_links_profiles_and_withdrawal_keep_actual_evidence_and_conflicts(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = NativeFixture(root)
            approve_fixture(fixture)
            extracted = extract_source(
                fixture.request["state_dir"],
                provider=NoCallsProvider(),
                allow_synthetic=True,
            )
            primary = copy.deepcopy(extracted["observations"][0])
            primary["source_id"] = "registry-a"
            primary["raw_record"]["ABN"] = primary["entity"]["abn"] = "51824753556"
            primary["confidence"]["source_reliability"] = 0.9
            primary["confidence"]["field_confidence"]["entity.abn"] = 0.99
            secondary = copy.deepcopy(primary)
            secondary["source_id"] = "registry-b"
            secondary["entity"]["legal_name"] = "Aster Holdings Pty Ltd"
            secondary["raw_record"][fixture.field] = secondary["entity"]["legal_name"]
            secondary["raw_record"]["company_locality"] = "Melbourne"
            secondary["address"] = {"locality": "Melbourne"}
            secondary["confidence"]["source_reliability"] = 0.8
            secondary["confidence"]["field_confidence"]["address.locality"] = 0.8
            orphan = copy.deepcopy(extracted["observations"][1])
            orphan["source_id"] = "unqualified-source"
            path = Path(root) / "observations.jsonl"
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in (primary, secondary, orphan))
            )
            output = Path(root) / "profiles"
            summary = build_outputs([path], output)
            self.assertEqual(
                (
                    summary["observation_count"],
                    summary["link_count"],
                    summary["unlinked_count"],
                    summary["profile_count"],
                ),
                (3, 1, 1, 1),
            )
            profile = json.loads((output / "profiles.json").read_text())[0]
            legal_name = profile["entity"]["legal_name"]
            self.assertEqual(legal_name["value"], primary["entity"]["legal_name"])
            self.assertEqual(legal_name["provenance"][0]["source_id"], "registry-a")
            self.assertEqual(legal_name["conflicts"][0]["source_id"], "registry-b")
            self.assertEqual(profile["address"]["locality"]["value"], "Melbourne")
            unlinked = json.loads((output / "unlinked.json").read_text())
            self.assertEqual(unlinked[0]["reason"], "no_qualified_identifier")
            withdrawal = withdraw_outputs(
                [path], "registry-b", Path(root) / "withdrawal"
            )
            self.assertEqual(withdrawal["profiles_affected"], 1)
            self.assertEqual(withdrawal["fields_lost"], 1)
            self.assertEqual(
                withdrawal["delta"][0]["fields_lost"], ["address.locality"]
            )
            self.assertEqual(withdrawal["profiles"][0]["source_count"], 1)
            self.assertTrue(withdrawal["identity_review_required"])


if __name__ == "__main__":
    unittest.main()
