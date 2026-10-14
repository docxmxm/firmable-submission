"""Planner repair feedback uses only existing candidate/evidence artifacts."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import _repair_progress, _repair_signatures, onboard_source
from firmable_runtime.common import Failure, digest
from firmable_runtime.runtime import Runtime
from test_agent import NativeFixture, ScriptedProvider, control


class RepairProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = NativeFixture(self.temporary.name, field="label_27")

    def config(self):
        config = self.fixture.config()
        config["source_schema"] = {"fields": [{"source_field": self.fixture.field}]}
        config["execution"]["transforms"][0] = {
            "op": "when",
            "condition": {
                "op": "is_present",
                "input": {"op": "field", "name": self.fixture.field},
            },
            "then": {"op": "field", "name": self.fixture.field},
            "else": {"op": "omit", "reason": "No original value."},
        }
        return config

    def test_cosmetic_changes_are_not_execution_or_evidence_changes(self):
        original = self.config()
        changed = copy.deepcopy(original)
        changed["field_mappings"][0].update(
            confidence=0.7,
            transformation="Different explanation.",
            validation_pass=True,
        )
        changed["unmapped_fields"][0]["reason"] = "Reworded omission."
        changed["execution"]["transforms"][0]["else"]["reason"] = (
            "Different omission explanation."
        )
        self.assertNotEqual(digest(original), digest(changed))
        self.assertEqual(_repair_signatures(original), _repair_signatures(changed))
        self.assertEqual(
            original["execution"]["transforms"][0]["else"]["reason"],
            "No original value.",
        )

    def test_ast_binding_and_evidence_changes_are_distinct(self):
        original = self.config()
        before = _repair_signatures(original)
        changed = copy.deepcopy(original)
        changed["field_mappings"][0]["semantic_evidence"] += (
            " Additional original context."
        )
        after = _repair_signatures(changed)
        self.assertEqual(before["execution"], after["execution"])
        self.assertEqual(before["target_binding"], after["target_binding"])
        self.assertNotEqual(before["semantic_evidence"], after["semantic_evidence"])
        changed = copy.deepcopy(original)
        changed["execution"]["transforms"][0]["then"] = {
            "op": "trim",
            "input": {"op": "field", "name": self.fixture.field},
        }
        self.assertNotEqual(
            before["execution"], _repair_signatures(changed)["execution"]
        )
        for key, value in [
            ("canonical_field", "entity.trading_name"),
            ("source_field", "row_note"),
            ("source_fields", [self.fixture.field, "row_note"]),
        ]:
            with self.subTest(binding=key):
                changed = copy.deepcopy(original)
                changed["field_mappings"][0][key] = value
                after = _repair_signatures(changed)
                self.assertNotEqual(before["target_binding"], after["target_binding"])
                self.assertNotEqual(before["execution"], after["execution"])

    def test_actual_pointers_and_metadata_are_compared_without_rewriting_data(self):
        original = self.config()
        original["execution"]["transforms"].append(
            {
                "op": "enum_lookup",
                "input": {"op": "field", "name": "row_note"},
                "values": {"op": "omit", "reason": "unknown"},
                "default": "unknown",
            }
        )
        original["field_mappings"].append(
            {
                **original["field_mappings"][0],
                "source_field": "row_note",
                "source_fields": ["row_note"],
                "canonical_field": "entity.entity_type",
                "execution_pointer": "/execution/transforms/1",
            }
        )
        before = _repair_signatures(original)
        reordered = copy.deepcopy(original)
        reordered["execution"]["transforms"].reverse()
        for index, mapping in enumerate(reordered["field_mappings"]):
            mapping["execution_pointer"] = "/execution/transforms/" + str(1 - index)
        reordered["field_mappings"].reverse()
        self.assertEqual(before, _repair_signatures(reordered))
        changed = copy.deepcopy(original)
        changed["execution"]["transforms"][1]["values"]["reason"] = "company"
        self.assertNotEqual(
            before["execution"], _repair_signatures(changed)["execution"]
        )
        for key, value in [
            ("subject", "documented subject"),
            (
                "observed_time",
                {
                    "source_fields": ["row_note"],
                    "evidence_ref": "original definition",
                    "ast": {"op": "field", "name": "row_note"},
                },
            ),
        ]:
            changed = copy.deepcopy(original)
            changed["execution"][key] = value
            self.assertNotEqual(
                before["execution"], _repair_signatures(changed)["execution"]
            )

    def test_malformed_ast_is_unavailable_not_a_new_flow_failure(self):
        for change in ("pointer", "node", "backend"):
            with self.subTest(change=change):
                config = self.config()
                if change == "pointer":
                    config["field_mappings"][0]["execution_pointer"] = (
                        "/execution/transforms/99"
                    )
                elif change == "node":
                    config["execution"]["transforms"][0]["op"] = "not_a_dsl_operation"
                else:
                    config["execution"] = {
                        "kind": "llm_task",
                        "task": "Read the original record",
                        "subject": "documented subject",
                    }
                result = _repair_signatures(config)
                self.assertEqual(result["status"], "unavailable")
                self.assertNotIn("execution", result)

    def test_planner_sees_repetition_lookups_and_real_revision_without_new_calls_on_resume(
        self,
    ):
        fixture = self.fixture
        actions = [
            control("probe", candidate_id="first", backend="ast"),
            control("inspect_field_evidence", field=fixture.field),
            control("inspect_field_evidence", field="row_note"),
            control("probe", candidate_id="second", backend="ast"),
            control("inspect_field_evidence", field=fixture.field),
            "good",
            "select",
        ]
        provider = ScriptedProvider(fixture, actions)
        provider.candidate_configs["first"] = fixture.config(bad=True)
        provider.candidate_configs["second"] = fixture.config(bad=True)
        provider.candidate_configs["second"]["field_mappings"][0]["transformation"] = (
            "A reworded explanation with no execution change."
        )
        result = onboard_source(fixture.request, provider)
        self.assertEqual(result["status"], "complete")
        contexts = [
            request["payload"]
            for request in provider.requests
            if request["purpose"] == "agent_decision"
        ]
        self.assertEqual(contexts[0]["repair_progress"]["status"], "no_prior_candidate")
        repeated = contexts[4]["repair_progress"]
        self.assertEqual(
            repeated["changes"],
            {
                "execution_changed": False,
                "target_binding_changed": False,
                "semantic_evidence_changed": False,
            },
        )
        self.assertEqual(repeated["same_execution_candidate_ids"], ["first"])
        self.assertEqual(
            repeated["repeated_structured_diagnostic_candidate_ids"], ["first"]
        )
        self.assertEqual(
            repeated["new_lookup_receipts_between_candidates"],
            sorted([fixture.field, "row_note"]),
        )
        self.assertEqual(repeated["new_lookup_receipts_since_latest_candidate"], [])
        self.assertEqual(
            contexts[4]["tool_availability"]["inspect_field_evidence"][
                "remaining_new_lookups"
            ],
            0,
        )
        self.assertEqual(contexts[5]["repair_progress"], repeated)
        self.assertTrue(contexts[6]["repair_progress"]["changes"]["execution_changed"])
        self.assertEqual(
            contexts[6]["repair_progress"][
                "repeated_structured_diagnostic_candidate_ids"
            ],
            [],
        )
        calls = len(provider.requests)
        runtime = Runtime(fixture.request, provider)
        state = runtime.run.state["agent"]
        signatures = _repair_progress(
            runtime, state, contexts[-1]["field_evidence_memory"]
        )
        self.assertEqual(onboard_source(fixture.request, provider), result)
        self.assertEqual(len(provider.requests), calls)
        self.assertEqual(
            _repair_progress(
                Runtime(fixture.request, provider),
                state,
                contexts[-1]["field_evidence_memory"],
            ),
            signatures,
        )
        # Existing artifact integrity checks still apply to this read-only context.
        candidate = state["candidates"]["good"]
        candidate["config_hash"] = "changed"
        with self.assertRaises(Failure) as raised:
            _repair_progress(runtime, state, [])
        self.assertEqual(raised.exception.kind, "checkpoint_mismatch")

    def test_unknown_planner_outcome_keeps_saved_progress_and_is_not_resent(self):
        class TimeoutAfterProbe(ScriptedProvider):
            def generate(self, request, call_id):
                if (
                    request["purpose"] == "agent_decision"
                    and request["payload"]["decision_turn"] == 1
                ):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Outcome unknown after progress was sent.")
                return super().generate(request, call_id)

        provider = TimeoutAfterProbe(self.fixture, ["bad"])
        for attempt in range(2):
            with self.assertRaises(Failure) as raised:
                onboard_source(self.fixture.request, provider)
            self.assertEqual(raised.exception.kind, "model_outcome_unknown")
            if attempt == 0:
                calls = len(provider.requests)
                progress = copy.deepcopy(
                    provider.requests[-1]["payload"]["repair_progress"]
                )
            else:
                self.assertEqual(len(provider.requests), calls)
        checkpoint = json.loads(
            (
                Path(self.fixture.request["state_dir"]) / "checkpoint.internal.json"
            ).read_text()
        )
        request = checkpoint["artifacts"][checkpoint["calls"][-1]["request_artifact"]]
        saved = json.loads(
            (Path(self.fixture.request["state_dir"]) / request["path"]).read_text()
        )
        self.assertEqual(saved["payload"]["repair_progress"], progress)


if __name__ == "__main__":
    unittest.main()
