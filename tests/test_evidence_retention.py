"""Reproduce evidence-reference loss and final-selection loss on invented schemas."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from firmable_runtime.agent import _retention_obligations, onboard_source
from firmable_runtime.branch_repair import repair_evidence_bindings
from firmable_runtime.common import Failure, digest
from firmable_runtime.runtime import Runtime
from test_candidate_selection import SelectionFixture, SelectionProvider, select


class BindingFixture(SelectionFixture):
    def __init__(self, root, renamed=False, kind="abn"):
        super().__init__(root, renamed)
        self.id_kind = kind
        # A native path is an exact field identity, not a source-specific alias.
        label = "ABN" if kind == "abn" else "ACN"
        long_label = "Australian Business Number" if kind == "abn" else "Australian Company Number"
        self.id_field = "record_82." + label if renamed else "payload." + long_label
        self.definitions[self.id_field] = self.id_field
        self.rows = [{self.id_field: "51824753556" if kind == "abn" else "123456780", self.address_field: f"{i + 1} Example Rd, Sydney NSW 2000"} for i in range(8)]
        self.request["required_fields"] = ["entity." + kind]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        if renamed:
            self.native.unlink()
            self.native = self.evidence / "native.json"
            self.native.write_text(json.dumps({"items": [{"record_82": {label: r[self.id_field]}, self.address_field: r[self.address_field]} for r in self.rows]}))
        (self.evidence / "definitions.md").write_text(self.definitions[self.address_field] + ("\nGlossary example: " + label + "\n" if renamed else ""))

    def named_config(self, name):
        config = super().named_config("wide")
        config["field_mappings"].pop(1)
        config["execution"]["transforms"].pop(1)
        config["field_mappings"][1]["execution_pointer"] = "/execution/transforms/1"
        config["unmapped_fields"] = []
        config["field_mappings"][0]["canonical_field"] = "entity." + self.id_kind
        config["execution"]["transforms"][0]["kind"] = self.id_kind
        config["field_mappings"][0]["semantic_evidence"] = self.id_field.split(".")[-1]
        if name == "narrow":
            config["field_mappings"].pop(0)
            config["execution"]["transforms"].pop(0)
            config["field_mappings"][0]["execution_pointer"] = "/execution/transforms/0"
            config["unmapped_fields"] = [{"source_field": self.id_field, "reason": "Abandoned after evidence binding failed."}]
        return config


class RetentionProvider(SelectionProvider):
    def generate(self, request, call_id):
        if request["purpose"] == "agent_decision" and self.actions and self.actions[0] == "select_repair":
            self.actions.pop(0)
            candidates = request["payload"]["candidates"]
            repair = next((c for c in candidates if c["candidate_id"].startswith("host-subset-")), None)
            self.actions.insert(0, select(repair["candidate_id"] if repair else "narrow"))
        return super().generate(request, call_id)


class EvidenceRetentionTests(unittest.TestCase):
    def test_exact_original_binding_is_repaired_before_final_field_deletion(self):
        for renamed, kind in ((False, "abn"), (True, "abn"), (False, "acn"), (True, "acn")):
            with self.subTest(renamed=renamed, kind=kind), tempfile.TemporaryDirectory() as root:
                fixture = BindingFixture(root, renamed, kind)
                provider = RetentionProvider(fixture, ["wide", "narrow", select("narrow"), "select_repair", "select_repair"])
                result = onboard_source(fixture.request, provider)
                self.assertEqual({m["canonical_field"] for m in result["mapping_config"]["field_mappings"]}, {"entity." + kind, "address.full"})
                artifacts = Path(fixture.request["state_dir"]) / "artifacts"
                plan = json.loads((artifacts / "host-subset-repair-plan.json").read_text())
                self.assertEqual(len(plan["rebound_evidence"]), 1)
                original = json.loads((artifacts / "candidate-wide.json").read_text())
                repaired = json.loads((artifacts / ("candidate-" + plan["candidate_id"] + ".json")).read_text())
                self.assertEqual(repaired["execution"], original["execution"])
                self.assertEqual(repaired["field_mappings"][0]["semantic_evidence"], fixture.id_field)
                self.assertIsNone(result["approval"])
                calls = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)

    def test_ambiguous_contradicted_and_wrong_input_bindings_remain_unrepaired(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = BindingFixture(root)
            provider = RetentionProvider(fixture, ["wide", "narrow", select("narrow"), "select_repair", "select_repair"])
            onboard_source(fixture.request, provider)
            artifacts = Path(fixture.request["state_dir"]) / "artifacts"
            original = json.loads((artifacts / "candidate-wide.json").read_text())
            for mode in ("ambiguous", "contradicted", "other_input", "other_target", "row_failure"):
                config = copy.deepcopy(original)
                mapping = config["field_mappings"][0]
                docs = (fixture.evidence / "definitions.md").read_text()
                if mode == "ambiguous":
                    old = mapping["source_field"]
                    mapping["source_field"] = "ref_92"
                    mapping["source_fields"] = ["ref_92"]
                    config["execution"]["transforms"][0]["input"]["name"] = "ref_92"
                    for f in config["source_schema"]["fields"]:
                        if f["source_field"] == old:
                            f["source_field"] = "ref_92"
                elif mode == "contradicted":
                    docs += "\n" + fixture.id_field + ": Australian Company Number."
                elif mode == "other_input":
                    config["execution"]["transforms"][0]["input"]["name"] = fixture.address_field
                elif mode == "other_target":
                    mapping["canonical_field"] = "entity.acn"
                error = {"reason_code": "row_validation" if mode == "row_failure" else "semantic_rule", "error_scope": "mapping", "mapping_index": 0, "source_fields": mapping["source_fields"], "canonical_field": mapping["canonical_field"]}
                with self.subTest(mode=mode):
                    repaired, remaining, changes = repair_evidence_bindings(config, [error], docs)
                    self.assertEqual((repaired, remaining, changes), (config, [error], []))

    def test_rule_or_time_changes_do_not_create_retention_obligations(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root, True)
            provider = SelectionProvider(fixture, ["wide", "narrow", select("narrow"), select("wide")])
            onboard_source(fixture.request, provider)
            runtime = Runtime(fixture.request, provider)
            state = runtime.run.state["agent"]
            proposal, validation = runtime.proposal_records(), runtime.run.load_artifact("validation-records")
            self.assertTrue(_retention_obligations(runtime, state, "narrow", proposal, validation))
            original = runtime.run.load_artifact
            for mode in ("rule", "time", "schema", "evidence"):
                def altered(name):
                    value = copy.deepcopy(original(name))
                    if name == state["candidates"]["wide"]["config_artifact"]:
                        if mode == "rule":
                            value["execution"]["transforms"][0]["require_label"] = True
                        elif mode == "time":
                            value["execution"]["observed_time"] = {"different": True}
                        elif mode == "schema":
                            value["source_schema"]["selected_table_ids"] = [91]
                        else:
                            value["field_mappings"][0]["semantic_evidence"] = "different definition"
                    return value
                changed = copy.deepcopy(state)
                changed["candidates"]["wide"]["config_hash"] = digest(altered(state["candidates"]["wide"]["config_artifact"]))
                with self.subTest(mode=mode), patch.object(runtime.run, "load_artifact", side_effect=altered):
                    self.assertEqual(_retention_obligations(runtime, changed, "narrow", proposal, validation), [])

    def test_repeated_refusal_is_bounded_and_never_auto_selects(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = SelectionProvider(fixture, ["wide", "narrow", select("narrow"), select("narrow"), select("narrow")])
            with patch("firmable_runtime.agent.MAX_DECISIONS", 5):
                result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertEqual(result["gap"]["kind"], "decision_limit")
            self.assertFalse((Path(fixture.request["state_dir"]) / "artifacts/final-config.json").exists())

    def test_unknown_after_retention_refusal_is_not_resent(self):
        class UnknownProvider(SelectionProvider):
            def generate(self, request, call_id):
                if request["purpose"] == "agent_decision" and (request["payload"].get("feedback") or {}).get("error") == "selection_discards_validated_extension":
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Unknown retention decision")
                return super().generate(request, call_id)
        with tempfile.TemporaryDirectory() as root:
            fixture = SelectionFixture(root)
            provider = UnknownProvider(fixture, ["wide", "narrow", select("narrow"), select("narrow")])
            calls = None
            for _ in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
                calls = len(provider.requests) if calls is None else calls
                self.assertEqual(len(provider.requests), calls)

    def test_repeated_lossy_selection_does_not_finalize_before_retained_choice(self):
        for renamed in (False, True):
            with self.subTest(renamed=renamed), tempfile.TemporaryDirectory() as root:
                fixture = SelectionFixture(root, renamed)
                provider = SelectionProvider(fixture, ["wide", "narrow", select("narrow"), select("narrow"), select("wide")])
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["candidate_selection"]["selected_candidate_id"], "wide")
                feedback = [r["payload"].get("feedback") for r in provider.requests if r["purpose"] == "agent_decision"]
                refusal = next(f for f in feedback if f and f.get("error") == "selection_discards_validated_extension")
                self.assertEqual(refusal["retention_obligations"][0]["candidate_id"], "wide")
                self.assertIsNone(result["approval"])


if __name__ == "__main__":
    unittest.main()
