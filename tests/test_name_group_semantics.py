"""Equivalent name expressions share evidence scope, never field meaning."""

import copy
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings, extract_records


def field(name):
    return {"op": "field", "name": name}


def trimmed(name):
    return {"op": "trim", "input": field(name)}


def omitted():
    return {"op": "omit", "reason": "The other branch supplies the current value."}


def fixture(base="Displayed label", current="Replacement label", flag="Selector"):
    inventory = [base, current, flag]
    docs = (
        "| Field | Description | Type | Parameters |\n"
        "| --- | --- | --- | --- |\n"
        f"| {base} | Company name as it appears on the register | Text | |\n"
        f"| {current} | Current company name | Text | |\n"
        f"| {flag} | Whether the displayed label is current | Text | |\n"
    )
    condition = {"op": "equals", "input": field(flag), "value": "current"}
    mappings = []
    for primary, then, otherwise in (
        (base, trimmed(base), omitted()),
        (current, omitted(), trimmed(current)),
    ):
        mappings.append(
            {
                "canonical_field": "entity.legal_name",
                "source_field": primary,
                "source_fields": [primary, flag],
                "semantic_evidence": primary,
                "confidence": 0.9,
                "ast": {
                    "op": "when",
                    "condition": copy.deepcopy(condition),
                    "then": then,
                    "else": otherwise,
                },
            }
        )
    merged = copy.deepcopy(mappings[0])
    merged["source_fields"] = inventory[:]
    merged["ast"]["else"] = trimmed(current)
    raw = [
        {base: " Now Ltd ", current: None, flag: "current"},
        {base: " Old Ltd ", current: " New Ltd ", flag: None},
        {base: " Historical Ltd ", current: "", flag: None},
    ]
    records = [
        {"source_id": "invented", "source_record_id": str(i), "raw_record": value}
        for i, value in enumerate(raw)
    ]
    return inventory, docs, mappings, merged, records


class NameGroupSemanticsTests(unittest.TestCase):
    def compile_failure(self, mappings, inventory, docs, unmapped=()):
        with self.assertRaises(Failure) as raised:
            compile_mappings(mappings, inventory, docs, unmapped)
        return raised.exception.details["findings"]

    def test_split_merged_and_reordered_mappings_are_equivalent(self):
        for labels in (
            ("Displayed label", "Replacement label", "Selector"),
            ("Entry Alpha", "Entry Beta", "Switch"),
        ):
            with self.subTest(labels=labels):
                inventory, docs, split, merged, records = fixture(*labels)
                before = copy.deepcopy((split, merged, records))
                for candidate in (split, list(reversed(split)), [merged]):
                    compiled = compile_mappings(candidate, inventory, docs)
                    observations, report = extract_records(records, compiled)
                    self.assertEqual(report["errors"], [])
                    self.assertEqual(
                        [row["values"] for row in observations],
                        [
                            {"entity.legal_name": "Now Ltd"},
                            {"entity.legal_name": "New Ltd"},
                            {},
                        ],
                    )
                    self.assertEqual(
                        [row["raw_record"] for row in observations],
                        [row["raw_record"] for row in records],
                    )
                self.assertEqual((split, merged, records), before)

    def test_relation_retains_both_exact_field_definitions(self):
        inventory, docs, split, _, _ = fixture()
        compiled = compile_mappings(split, inventory, docs)
        relation = compiled[1]["semantic_support"]["contexts"][0][
            "register_name_relation"
        ]
        self.assertEqual(relation["register_name_field"], inventory[0])
        self.assertEqual(relation["current_name_field"], inventory[1])
        self.assertEqual(set(relation["field_definitions"]), set(inventory[:2]))
        for definition in relation["field_definitions"].values():
            self.assertIn(definition["text"], docs)

    def test_complete_local_relation_survives_another_same_target_anchor(self):
        inventory, docs, _, merged, records = fixture()
        inventory.append("Mirror")
        docs += "| Mirror | Company name as it appears on the register | Text | |\n"
        peer = {
            "canonical_field": "entity.legal_name",
            "source_field": "Mirror",
            "source_fields": ["Mirror"],
            "semantic_evidence": "Mirror",
            "confidence": 0.9,
            "ast": trimmed("Mirror"),
        }
        for record, value in zip(records, ("Now Ltd", "New Ltd", "")):
            record["raw_record"]["Mirror"] = value
        for mappings in ([merged, peer], [peer, merged]):
            with self.subTest(mappings=mappings):
                compiled = compile_mappings(mappings, inventory, docs)
                observations, report = extract_records(records, compiled)
                self.assertEqual(report["errors"], [])
                self.assertEqual(
                    [row["values"] for row in observations],
                    [
                        {"entity.legal_name": "Now Ltd"},
                        {"entity.legal_name": "New Ltd"},
                        {},
                    ],
                )

    def test_multiple_register_anchors_cannot_resolve_incomplete_local_relation(self):
        inventory, docs, split, _, _ = fixture()
        inventory.append("Mirror")
        docs += "| Mirror | Company name as it appears on the register | Text | |\n"
        peer = copy.deepcopy(split[0])
        peer["source_field"] = "Mirror"
        peer["source_fields"] = ["Mirror"]
        peer["semantic_evidence"] = "Mirror"
        peer["ast"] = trimmed("Mirror")
        findings = self.compile_failure([*split, peer], inventory, docs)
        self.assertTrue(
            any(
                item.get("mapping_index") == 1 and item["code"] == "semantic_rule"
                for item in findings
            )
        )

    def test_an_unmapped_base_cannot_lend_its_role(self):
        inventory, docs, split, _, _ = fixture()
        findings = self.compile_failure(
            split[1:],
            inventory,
            docs,
            [{"source_field": inventory[0], "reason": "Not mapped."}],
        )
        self.assertTrue(any(item["code"] == "semantic_rule" for item in findings))

    def test_cross_target_mappings_do_not_share_name_evidence(self):
        inventory, docs, split, _, _ = fixture()
        split[0]["canonical_field"] = "entity.trading_name"
        findings = self.compile_failure(split, inventory, docs)
        self.assertTrue(
            any(
                item.get("mapping_index") == 1 and item["code"] == "semantic_rule"
                for item in findings
            )
        )

    def test_generic_value_is_not_promoted_by_a_supported_name(self):
        inventory, docs, split, merged, _ = fixture()
        docs = docs.replace("Current company name", "Free-form display text")
        for candidate in (split, [merged]):
            with self.subTest(candidate=candidate):
                findings = self.compile_failure(candidate, inventory, docs)
                self.assertTrue(
                    any(item["code"] == "semantic_rule" for item in findings)
                )

    def test_foreign_subject_peer_cannot_supply_a_register_anchor(self):
        inventory, docs, split, _, _ = fixture(base="Director label")
        findings = self.compile_failure(split, inventory, docs)
        self.assertTrue(
            any(
                item.get("mapping_index") == 0
                and item.get("semantic_support", {}).get("status") == "foreign_subject"
                for item in findings
            )
        )
        self.assertTrue(
            any(
                item.get("mapping_index") == 1 and item["code"] == "semantic_rule"
                for item in findings
            )
        )

    def test_foreign_subject_current_value_remains_rejected(self):
        inventory, docs, split, merged, _ = fixture(current="Director label")
        for candidate in (split, [merged]):
            with self.subTest(candidate=candidate):
                findings = self.compile_failure(candidate, inventory, docs)
                self.assertTrue(
                    any(
                        item.get("semantic_support", {}).get("status")
                        == "foreign_subject"
                        for item in findings
                    )
                )

    def test_contradictory_definition_cannot_be_overruled_by_group(self):
        inventory, docs, split, merged, _ = fixture()
        docs += f"| {inventory[1]} | Not the registered company name | Text | |\n"
        for candidate in (split, [merged]):
            with self.subTest(candidate=candidate):
                findings = self.compile_failure(candidate, inventory, docs)
                self.assertTrue(
                    any(item["code"] == "semantic_rule" for item in findings)
                )

    def test_invalid_peer_dependencies_cannot_supply_evidence(self):
        inventory, docs, split, _, _ = fixture()
        split[0]["source_fields"].remove(inventory[0])
        findings = self.compile_failure(split, inventory, docs)
        self.assertTrue(
            any(item["code"] == "undeclared_dependency" for item in findings)
        )
        self.assertTrue(
            any(
                item.get("mapping_index") == 1 and item["code"] == "semantic_rule"
                for item in findings
            )
        )

    def test_predicate_only_field_cannot_supply_name_role(self):
        inventory, docs, split, _, _ = fixture()
        current = split[1]
        current["ast"]["condition"] = {"op": "is_present", "input": field(inventory[0])}
        current["source_fields"] = inventory[:2]
        findings = self.compile_failure(
            [current],
            inventory,
            docs,
            [{"source_field": inventory[2], "reason": "Not consumed."}],
        )
        self.assertTrue(any(item["code"] == "semantic_rule" for item in findings))

    def test_conflicting_same_record_values_are_still_execution_errors(self):
        inventory, docs, split, _, records = fixture()
        for mapping in split:
            mapping["ast"] = trimmed(mapping["source_field"])
            mapping["source_fields"] = [mapping["source_field"]]
        compiled = compile_mappings(
            split,
            inventory,
            docs,
            [{"source_field": inventory[2], "reason": "Not consumed."}],
        )
        _, report = extract_records(records, compiled)
        self.assertTrue(
            any(
                item["reason_code"] == "canonical_mapping_conflict"
                for item in report["errors"]
            )
        )


if __name__ == "__main__":
    unittest.main()
