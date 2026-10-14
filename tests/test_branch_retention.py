"""Local repair and value retention on invented, renamed native inputs."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure, digest
from firmable_runtime.branch_repair import _output_branch, repair_output_branches
from firmable_runtime.engine import eval_ast
from firmable_runtime.schema import check_response
from test_agent import NativeFixture, SubsetProvider
from test_candidate_repair import EnumFixture


class BranchFixture(NativeFixture):
    def __init__(self, root, labels=("label_a", "label_b", "mode")):
        super().__init__(root, field=labels[0])
        self.first, self.second, self.flag = labels
        self.definition = f"{self.first}: Registered legal name of the company."
        self.docs = (
            self.definition
            + "\n"
            + f"{self.second}: Historical display text, not a current legal name.\n"
            + f"{self.flag}: 'live' identifies current records; other records have no documented current name.\n"
            + "ABN: Australian Business Number.\n"
        )
        self.rows = [
            {
                self.first: f"Business {i} Ltd",
                self.second: f"Former {i}",
                self.flag: "live" if i % 2 else "past",
                "ABN": "51824753556",
            }
            for i in range(8)
        ]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        (self.evidence / "definitions.md").write_text(self.docs)
        self.ontology.write_text("entity:\n  abn: string\n  legal_name: string\n")
        self.request["required_fields"] = ["entity.abn", "entity.legal_name"]

    def config(self, bad=False):
        fields = ["ABN", self.first, self.second, self.flag]
        specs = [
            (
                "ABN",
                ["ABN"],
                "entity.abn",
                {
                    "op": "parse_typed_id",
                    "kind": "abn",
                    "require_label": False,
                    "input": {"op": "field", "name": "ABN"},
                },
            )
        ]
        if bad:
            specs.append(
                (
                    self.first,
                    fields[1:],
                    "entity.legal_name",
                    {
                        "op": "when",
                        "condition": {
                            "op": "equals",
                            "input": {"op": "field", "name": self.flag},
                            "value": "live",
                        },
                        "then": {"op": "field", "name": self.first},
                        "else": {"op": "field", "name": self.second},
                    },
                )
            )
        return {
            "field_mappings": [
                dict(
                    source_field=field,
                    source_fields=deps,
                    canonical_field=target,
                    confidence=0.9,
                    semantic_evidence=self.definition
                    if target.endswith("name")
                    else "ABN",
                    transformation="Use original role and condition.",
                    execution_pointer=f"/execution/transforms/{i}",
                    validation_pass=False,
                )
                for i, (field, deps, target, _) in enumerate(specs)
            ],
            "unmapped_fields": []
            if bad
            else [
                {"source_field": f, "reason": "Removed with disputed name."}
                for f in fields[1:]
            ],
            "execution": {"kind": "ast", "transforms": [s[3] for s in specs]},
        }


class BranchProvider(SubsetProvider):
    def generate(self, request, call_id):
        if request["purpose"] != "name_role_review":
            return super().generate(request, call_id)
        self.requests.append(copy.deepcopy(request))
        reviews = []
        for item in request["payload"]["name_mappings"]:
            index = item["mapping_index"]
            branches = []
            for branch in item["host_branch_evidence"]["branches"]:
                fields = branch["possible_value_fields"]
                disputed = self.fixture.second in fields
                branches.append(
                    {
                        "branch_id": branch["branch_id"],
                        "documented_condition": self.fixture.docs,
                        "expected_value_fields": fields,
                        "verdict": "uncertain" if disputed else "supported",
                        "evidence_quotes": [
                            {"kind": "documentation", "quote": self.fixture.docs}
                        ],
                        "reason": "Historical display text is not a current name."
                        if disputed
                        else "Original current role or explicit abstention.",
                        "current_value_requirement": "available"
                        if fields == [self.fixture.first]
                        else "not_documented",
                        "current_value_fields": fields
                        if fields == [self.fixture.first]
                        else [],
                        "coverage_mapping_indices": [index]
                        if fields == [self.fixture.first]
                        else [],
                    }
                )
            reviews.append(
                {
                    "mapping_index": index,
                    "verdict": "uncertain"
                    if any(b["verdict"] != "supported" for b in branches)
                    else "supported",
                    "evidence_quotes": [
                        {"kind": "documentation", "quote": self.fixture.docs}
                    ],
                    "reason": "Evaluate each current or historical branch separately.",
                    "repair_hint": "Retain the current branch and abstain on the historical branch.",
                    "branch_reviews": branches,
                }
            )
        response = {"reviews": reviews}
        check_response(response, request["output_schema"])
        return {"config": response, "cost_usd": 0, "usage": {}}


class BranchRetentionTests(unittest.TestCase):
    def captured(self, root):
        fixture = BranchFixture(root)
        provider = BranchProvider(fixture, ["bad", "stop", "stop", "stop"])
        onboard_source(fixture.request, provider)
        artifacts = Path(fixture.request["state_dir"]) / "artifacts"
        config = json.loads((artifacts / "candidate-bad.json").read_text())
        audit = json.loads((artifacts / "name-audit-bad.json").read_text())
        probe = json.loads((artifacts / "probe-bad.json").read_text())
        docs = next(r for r in provider.requests if r["purpose"] == "name_role_review")[
            "payload"
        ]["source_documentation"]
        return config, audit, probe["report"]["errors"] + audit["errors"], docs

    def test_stale_bindings_invalid_quotes_and_branch_claims_cannot_authorize_cuts(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            config, original, errors, docs = self.captured(root)
            for mode in (
                "config_hash",
                "documentation_hash",
                "quote",
                "duplicate",
                "wrong_pointer",
                "no_supported_branch",
            ):
                with self.subTest(mode=mode):
                    audit = copy.deepcopy(original)
                    review = audit["reviews"][0]
                    if mode == "config_hash":
                        audit["candidate_config_hash"] = "stale"
                    elif mode == "documentation_hash":
                        audit["documentation_hash"] = "stale"
                    elif mode == "quote":
                        review["branch_reviews"][0]["evidence_quotes"][0]["quote"] = (
                            "invented evidence"
                        )
                    elif mode == "duplicate":
                        review["branch_reviews"].append(
                            copy.deepcopy(review["branch_reviews"][0])
                        )
                    elif mode == "wrong_pointer":
                        review["branch_reviews"][1]["branch_id"] = "/condition"
                    else:
                        review["branch_reviews"][0]["verdict"] = "uncertain"
                    review["evidence_verified"] = review["branch_analysis_verified"] = (
                        True
                    )
                    repaired, remaining, changes = repair_output_branches(
                        config, errors, docs, audit
                    )
                    self.assertEqual(repaired, config)
                    self.assertEqual(remaining, errors)
                    self.assertEqual(changes, [])

    def test_current_value_obligation_cannot_be_removed_even_when_reviewer_is_negative(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            config, original, errors, docs = self.captured(root)
            for requirement in ("available", "uncertain"):
                audit = copy.deepcopy(original)
                branch = audit["reviews"][0]["branch_reviews"][1]
                branch["current_value_requirement"] = requirement
                branch["current_value_fields"] = ["label_b"]
                branch["coverage_mapping_indices"] = [1]
                repaired, remaining, changes = repair_output_branches(
                    config, errors, docs, audit
                )
                self.assertEqual((repaired, remaining, changes), (config, errors, []))

    def test_output_path_rejects_predicates_and_value_fallback_wrappers(self):
        leaf = {"op": "field", "name": "anything"}
        tree = {
            "op": "when",
            "condition": leaf,
            "then": copy.deepcopy(leaf),
            "else": {"op": "when", "condition": leaf, "then": leaf, "else": leaf},
        }
        self.assertEqual(_output_branch(tree, "/else/then"), leaf)
        for tree, path in (
            (tree, "/condition"),
            (tree, "/then/input"),
            ({"op": "trim", "input": tree}, "/input/else"),
            ({"op": "coalesce", "inputs": [tree, leaf]}, "/inputs/0/else"),
        ):
            self.assertIsNone(_output_branch(tree, path))

    def test_primary_binding_and_shared_transform_are_not_silently_reassigned(self):
        with tempfile.TemporaryDirectory() as root:
            original, audit, errors, docs = self.captured(root)
            for mode in ("primary", "shared"):
                config = copy.deepcopy(original)
                if mode == "primary":
                    config["field_mappings"][1]["source_field"] = "label_b"
                else:
                    config["field_mappings"][0]["execution_pointer"] = (
                        "/execution/transforms/1"
                    )
                bound = {**audit, "candidate_config_hash": digest(config)}
                repaired, remaining, changes = repair_output_branches(
                    config, errors, docs, bound
                )
                self.assertEqual((repaired, remaining, changes), (config, errors, []))

    def test_unknown_fresh_review_is_never_resent(self):
        class Interrupted(BranchProvider):
            def generate(self, request, call_id):
                if request["purpose"] == "name_role_review" and any(
                    m["executable"].get("else", {}).get("op") == "omit"
                    for m in request["payload"]["name_mappings"]
                ):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Possibly completed fresh local-repair review")
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = BranchFixture(root)
            provider = Interrupted(fixture, ["bad", "stop"])
            counts = []
            for _ in range(2):
                with self.assertRaises(Failure) as raised:
                    onboard_source(fixture.request, provider)
                self.assertEqual(raised.exception.kind, "model_outcome_unknown")
                counts.append(len(provider.requests))
            self.assertEqual(counts[0], counts[1])

    def test_local_name_repair_preserves_condition_and_requires_fresh_review(self):
        for labels in [("label_a", "label_b", "mode"), ("x91", "z42", "switch_8")]:
            with self.subTest(labels=labels), tempfile.TemporaryDirectory() as root:
                fixture = BranchFixture(root, labels)
                provider = BranchProvider(
                    fixture, ["bad", "good", "select", "select_subset"]
                )
                result = onboard_source(fixture.request, provider)
                artifacts = Path(fixture.request["state_dir"]) / "artifacts"
                self.assertEqual(
                    result["task_status"],
                    "complete",
                    "Supported current branch was lost with the disputed branch",
                )
                plan = json.loads(
                    (artifacts / "host-subset-repair-plan.json").read_text()
                )
                repaired = json.loads(
                    (artifacts / f"candidate-{plan['candidate_id']}.json").read_text()
                )
                original = json.loads((artifacts / "candidate-bad.json").read_text())
                self.assertEqual(result["task_status"], "complete")
                self.assertEqual(plan["excluded_mapping_indices"], [])
                self.assertEqual(
                    plan["removed_output_branches"][0]["branch_id"], "/else"
                )
                before, after = (
                    original["execution"]["transforms"][1],
                    repaired["execution"]["transforms"][1],
                )
                self.assertEqual(before["condition"], after["condition"])
                self.assertEqual(before["then"], after["then"])
                self.assertEqual(after["else"]["op"], "omit")
                for raw in fixture.rows:
                    self.assertEqual(
                        eval_ast(after, raw),
                        raw[fixture.first] if raw[fixture.flag] == "live" else None,
                    )
                self.assertEqual(digest(original), plan["source_config_hash"])
                audit = json.loads(
                    (artifacts / f"name-audit-{plan['candidate_id']}.json").read_text()
                )
                self.assertEqual(audit["status"], "supported")
                self.assertEqual(audit["candidate_config_hash"], digest(repaired))
                self.assertIsNone(result["approval"])
                calls = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)

    def test_same_target_loss_still_probes_supported_enum_alternative(self):
        class PartialEnum(EnumFixture):
            def config(self, bad=False):
                config = super().config(bad=True)
                if not bad:
                    config["execution"]["transforms"][1]["values"] = {
                        "Company": "company"
                    }
                return config

        with tempfile.TemporaryDirectory() as root:
            fixture = PartialEnum(root, field="classification_7")
            provider = SubsetProvider(
                fixture, ["bad", "good", "select", "select_subset"]
            )
            result = onboard_source(fixture.request, provider)
            artifacts = Path(fixture.request["state_dir"]) / "artifacts"
            self.assertTrue(
                (artifacts / "host-subset-repair-plan.json").exists(),
                "Keeping the target must not hide lost supported enum values",
            )
            plan = json.loads((artifacts / "host-subset-repair-plan.json").read_text())
            self.assertEqual(plan["requested_action"]["candidate_id"], "good")
            self.assertIn(
                "incorporated_association",
                {
                    r.get("entity", {}).get("entity_type")
                    for r in result["observations"]
                },
            )


if __name__ == "__main__":
    unittest.main()
