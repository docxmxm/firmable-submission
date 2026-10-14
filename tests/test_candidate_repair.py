"""Repair tests over newly constructed records, never source-specific mappings."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import (
    _compile_finding,
    _repair_bound_findings,
    onboard_source,
)
from firmable_runtime.common import Failure, digest
from firmable_runtime.engine import compile_mappings, eval_ast
from firmable_runtime.execution import pointer_get
from test_agent import NativeFixture, SubsetProvider


class EnumFixture(NativeFixture):
    def __init__(self, root, field="Legal Form", reverse=False):
        super().__init__(root)
        self.form_field, self.reverse = field, reverse
        forms = ["Company", "Incorporated Association", "Individual/~or sole trader"]
        self.rows = [
            {field: forms[i % 3], "ABN": "51824753556", "row_note": str(i)}
            for i in range(8)
        ]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        (self.evidence / "definitions.md").write_text(
            field + ": Legal form of the business.\nABN: Australian Business Number.\n"
        )
        self.ontology.write_text("entity:\n  abn: string\n  entity_type: enum\n")
        self.request["required_fields"] = ["entity.abn", "entity.entity_type"]

    def config(self, bad=False):
        pairs = [
            (
                "ABN",
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
            pairs.append(
                (
                    self.form_field,
                    "entity.entity_type",
                    {
                        "op": "enum_lookup",
                        "input": {"op": "field", "name": self.form_field},
                        "values": {
                            "Company": "company",
                            "Incorporated Association": "incorporated_association",
                            "Individual/~or sole trader": "sole_trader",
                        },
                        "default": "unknown",
                    },
                )
            )
        if self.reverse:
            pairs.reverse()
        return {
            "field_mappings": [
                {
                    "source_field": field,
                    "source_fields": [field],
                    "canonical_field": target,
                    "confidence": 0.9,
                    "transformation": "Validate the original identifier or normalize supported legal forms.",
                    "semantic_evidence": "ABN"
                    if target == "entity.abn"
                    else self.form_field + ": Legal form of the business.",
                    "execution_pointer": "/execution/transforms/" + str(i),
                    "validation_pass": False,
                }
                for i, (field, target, _) in enumerate(pairs)
            ],
            "unmapped_fields": [
                {"source_field": "row_note", "reason": "Source row reference."}
            ]
            + (
                []
                if bad
                else [
                    {
                        "source_field": self.form_field,
                        "reason": "Omitted after another enum entry failed.",
                    }
                ]
            ),
            "execution": {"kind": "ast", "transforms": [ast for _, _, ast in pairs]},
        }


class CandidateRepairTests(unittest.TestCase):
    def run_repair(self, root, actions, field="Legal Form", reverse=False):
        fixture = EnumFixture(root, field, reverse)
        provider = SubsetProvider(fixture, actions)
        result = onboard_source(fixture.request, provider)
        artifacts = Path(fixture.request["state_dir"]) / "artifacts"
        plan = json.loads((artifacts / "host-subset-repair-plan.json").read_text())
        repaired = json.loads(
            (artifacts / ("candidate-" + plan["candidate_id"] + ".json")).read_text()
        )
        return fixture, provider, result, plan, repaired

    def test_stop_repairs_only_bound_enum_entry_and_requires_normal_selection(self):
        for field, reverse in [("Legal Form", False), ("category_81", True)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture, provider, result, plan, repaired = self.run_repair(
                    root, ["bad", "stop", "select_subset"], field, reverse
                )
                self.assertEqual(plan["excluded_mapping_indices"], [])
                self.assertEqual(len(plan["removed_enum_entries"]), 1)
                self.assertIn(
                    "Individual~1~0or sole trader",
                    plan["removed_enum_entries"][0]["pointer"],
                )
                self.assertEqual(result["candidate_status"], "validated")
                self.assertEqual(result["task_status"], "complete")
                self.assertIsNone(result["approval"])
                self.assertTrue(result["approval_required"])
                ast = next(
                    ast
                    for ast in repaired["execution"]["transforms"]
                    if ast["op"] == "enum_lookup"
                )
                self.assertEqual(
                    ast["values"],
                    {
                        "Company": "company",
                        "Incorporated Association": "incorporated_association",
                    },
                )
                self.assertEqual(
                    eval_ast(ast, {field: "Individual/~or sole trader"}), "unknown"
                )
                description = next(
                    m["transformation"]
                    for m in repaired["field_mappings"]
                    if m["canonical_field"] == "entity.entity_type"
                )
                self.assertNotIn("Individual/~or sole trader", description)
                self.assertNotIn("sole_trader", description)
                self.assertIn("still requires validation", description)
                count = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), count)

    def test_selecting_lossy_revision_first_probes_repairable_targets(self):
        with tempfile.TemporaryDirectory() as root:
            _, provider, result, plan, _ = self.run_repair(
                root, ["bad", "good", "select", "select_subset"]
            )
            self.assertEqual(plan["trigger"], "selection_omits_repairable_targets")
            self.assertEqual(plan["requested_action"]["candidate_id"], "good")
            self.assertEqual(result["task_status"], "complete")
            last = [r for r in provider.requests if r["purpose"] == "agent_decision"][
                -1
            ]
            self.assertIn(
                "good", [c["candidate_id"] for c in last["payload"]["candidates"]]
            )
            self.assertTrue(
                any(
                    c["candidate_id"].startswith("host-subset-")
                    for c in last["payload"]["candidates"]
                )
            )

    def test_repair_does_not_select_itself_and_is_one_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, result, _, _ = self.run_repair(root, ["bad", "stop", "stop", "stop"])
            self.assertIsNone(result["mapping_config"])
            self.assertEqual(result["candidate_status"], "unavailable")

    def test_wrong_pointer_or_nonabstaining_default_cannot_preserve_group(self):
        with tempfile.TemporaryDirectory() as root:
            fixture, _, _, plan, _ = self.run_repair(root, ["bad", "stop", "stop", "stop"])
            original = fixture.config(bad=True)
            for mode in [
                "wrong_pointer",
                "wrong_value",
                "nonabstaining_default",
                "last_entry",
            ]:
                config, errors = (
                    copy.deepcopy(original),
                    copy.deepcopy(plan["blockers"]),
                )
                if mode == "wrong_pointer":
                    errors[0]["pointer"] = "/execution/transforms/0/values/Company"
                elif mode == "wrong_value":
                    errors[0]["semantic_support"]["unsupported_conversion"][
                        "canonical_value"
                    ] = "company"
                elif mode == "nonabstaining_default":
                    config["execution"]["transforms"][1]["default"] = "company"
                else:
                    config["execution"]["transforms"][1]["values"] = {
                        "Individual/~or sole trader": "sole_trader"
                    }
                before = digest(config)
                repaired, blocked, pruned = _repair_bound_findings(config, errors)
                self.assertEqual(blocked, [1], mode)
                self.assertEqual(pruned, [], mode)
                self.assertEqual(
                    [m["canonical_field"] for m in repaired["field_mappings"]],
                    ["entity.abn"],
                )
                self.assertEqual(digest(config), before)

    def test_pruned_descriptions_derive_from_final_rules_without_rewriting_evidence(
        self,
    ):
        for field, nested in (("Legal Form", False), ("category_81", True)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture = EnumFixture(root, field)
                config = fixture.config(bad=True)
                mapping = config["field_mappings"][1]
                mapping["transformation"] = (
                    'Original prose: "Individual/~or sole trader" emits sole_trader; '
                    "company and association are also emitted."
                )
                if nested:
                    config["execution"]["transforms"][1] = {
                        "op": "when",
                        "condition": {
                            "op": "is_present",
                            "input": {"op": "field", "name": field},
                        },
                        "then": config["execution"]["transforms"][1],
                        "else": {"op": "omit", "reason": "No category"},
                    }
                before = copy.deepcopy(config)
                docs = (fixture.evidence / "definitions.md").read_text()
                inventory = [field, "ABN", "row_note"]
                with self.assertRaises(Failure) as raised:
                    compile_mappings(
                        [
                            {**m, "ast": pointer_get(config, m["execution_pointer"])}
                            for m in config["field_mappings"]
                        ],
                        inventory,
                        docs,
                        config["unmapped_fields"],
                    )
                errors = [
                    _compile_finding(f, config, str(raised.exception))
                    for f in raised.exception.details["findings"]
                ]
                repaired, blocked, pruned = _repair_bound_findings(config, errors)
                self.assertEqual(blocked, [])
                self.assertEqual(len(pruned), 1)
                current = repaired["field_mappings"][1]
                description = current["transformation"]
                self.assertNotIn("Original prose", description)
                self.assertNotIn("sole_trader", description)
                rules = json.loads(
                    description.split(
                        "Retained output enum rules (mapping-relative pointers): ", 1
                    )[1]
                )
                ast = pointer_get(repaired, current["execution_pointer"])
                enum = ast["then"] if nested else ast
                self.assertEqual(
                    rules,
                    [
                        {
                            "ast_pointer": "/then" if nested else "/",
                            "values": enum["values"],
                            "default": enum["default"],
                        }
                    ],
                )
                self.assertEqual(
                    {k: v for k, v in current.items() if k != "transformation"},
                    {k: v for k, v in mapping.items() if k != "transformation"},
                )
                self.assertEqual(
                    repaired["field_mappings"][0], config["field_mappings"][0]
                )
                self.assertEqual(config, before)

    def test_shared_pruned_expression_refreshes_every_retained_description(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = EnumFixture(root)
            config = fixture.config(bad=True)
            config["field_mappings"].append(copy.deepcopy(config["field_mappings"][1]))
            for mapping in config["field_mappings"][1:]:
                mapping["transformation"] = "Old description says sole_trader."
            docs = (fixture.evidence / "definitions.md").read_text()
            with self.assertRaises(Failure) as raised:
                compile_mappings(
                    [
                        {**m, "ast": pointer_get(config, m["execution_pointer"])}
                        for m in config["field_mappings"]
                    ],
                    [fixture.form_field, "ABN", "row_note"],
                    docs,
                    config["unmapped_fields"],
                )
            errors = [
                _compile_finding(f, config, str(raised.exception))
                for f in raised.exception.details["findings"]
            ]
            self.assertEqual(len(errors), 2)
            repaired, blocked, pruned = _repair_bound_findings(config, errors)
            self.assertEqual(blocked, [])
            self.assertEqual(len(pruned), 1)
            descriptions = [m["transformation"] for m in repaired["field_mappings"][1:]]
            self.assertEqual(descriptions[0], descriptions[1])
            self.assertNotIn("sole_trader", descriptions[0])

    def test_nested_default_cannot_change_a_predicate_or_become_informative(self):
        """A locally abstaining default need not abstain at the mapping output."""
        with tempfile.TemporaryDirectory() as root:
            fixture = EnumFixture(root)
            form, alternate = fixture.form_field, "Alternative Type"
            raw_label = "Individual/~or sole trader"
            docs = (
                form + ": Legal form of the business; unknown means company.\n"
                # Give the fallback its own role definition without an ambiguous
                # mention of the separate native field named "Legal Form".
                "Alternative Type: Entity type of the business.\n"
                "ABN: Australian Business Number.\n"
            )
            for position in ("predicate", "outer_conversion"):
                with self.subTest(position=position):
                    config = fixture.config(bad=True)
                    nested = config["execution"]["transforms"][1]
                    mapping = config["field_mappings"][1]
                    mapping["semantic_evidence"] = form
                    if position == "predicate":
                        expression = {
                            "op": "when",
                            "condition": {
                                "op": "equals",
                                "input": nested,
                                "value": "sole_trader",
                            },
                            "then": {"op": "omit", "reason": "Unresolved category"},
                            "else": {
                                "op": "enum_lookup",
                                "input": {"op": "field", "name": alternate},
                                "values": {"Company": "company"},
                                "default": None,
                            },
                        }
                        mapping["source_fields"].append(alternate)
                        inner_path = "/condition/input"
                    else:
                        expression = {
                            "op": "enum_lookup",
                            "input": nested,
                            "values": {"company": "company", "unknown": "company"},
                            "default": None,
                        }
                        inner_path = "/input"
                    config["execution"]["transforms"][1] = expression
                    inventory = ["ABN", form, "row_note"] + (
                        [alternate] if position == "predicate" else []
                    )
                    values = [
                        {**m, "ast": pointer_get(config, m["execution_pointer"])}
                        for m in config["field_mappings"]
                    ]
                    with self.assertRaises(Failure) as raised:
                        compile_mappings(
                            values, inventory, docs, config["unmapped_fields"]
                        )
                    errors = [
                        _compile_finding(finding, config, str(raised.exception))
                        for finding in raised.exception.details["findings"]
                    ]
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(
                        errors[0]["pointer"],
                        "/execution/transforms/1"
                        + inner_path
                        + "/values/Individual~1~0or sole trader",
                    )
                    raw = {form: raw_label, alternate: "Company"}
                    self.assertIsNone(eval_ast(expression, raw))
                    unsafe = copy.deepcopy(config)
                    del pointer_get(unsafe, "/execution/transforms/1" + inner_path)[
                        "values"
                    ][raw_label]
                    self.assertEqual(
                        eval_ast(unsafe["execution"]["transforms"][1], raw), "company"
                    )
                    # Recompilation alone cannot detect this changed selection.
                    compile_mappings(
                        [
                            {**m, "ast": pointer_get(unsafe, m["execution_pointer"])}
                            for m in unsafe["field_mappings"]
                        ],
                        inventory,
                        docs,
                        unsafe["unmapped_fields"],
                    )
                    before = digest(config)
                    repaired, blocked, pruned = _repair_bound_findings(config, errors)
                    self.assertEqual(blocked, [1])
                    self.assertEqual(pruned, [])
                    self.assertEqual(
                        [m["canonical_field"] for m in repaired["field_mappings"]],
                        ["entity.abn"],
                    )
                    self.assertEqual(digest(config), before)


if __name__ == "__main__":
    unittest.main()
