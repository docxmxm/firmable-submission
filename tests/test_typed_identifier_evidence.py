"""Invented label-only identifiers retain exact field, owner and raw checks."""

import copy
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings, extract_records


def mapping(name="Reference", kind="abn", quote=None, require_label=True):
    return {
        "source_field": name,
        "source_fields": [name],
        "canonical_field": "entity." + kind,
        "confidence": 0.8,
        "semantic_evidence": name if quote is None else quote,
        "ast": {
            "op": "parse_typed_id",
            "input": {"op": "field", "name": name},
            "kind": kind,
            "require_label": require_label,
        },
    }


class TypedIdentifierEvidenceTests(unittest.TestCase):
    def compile(self, value, docs="", inventory=None):
        inventory = inventory or value["source_fields"]
        return compile_mappings(
            [value],
            inventory,
            docs,
            unmapped=[
                {"source_field": name, "reason": "Unrelated fixture field"}
                for name in inventory
                if name not in value["source_fields"]
            ],
        )

    def failure(self, value, docs="", inventory=None):
        with self.assertRaises(Failure) as raised:
            self.compile(value, docs, inventory)
        return raised.exception.details["findings"]

    def test_header_and_same_field_quotes_are_equivalent_after_renaming(self):
        for name in ("Reference", "Entry token", "Nested/Code"):
            definition = "ABN or ACN number"
            for docs, whole in (
                (f"{name}: {definition}.", f"{name}: {definition}."),
                (
                    f"| Field | Description |\n| --- | --- |\n| {name} | {definition} |",
                    f"| {name} | {definition} |",
                ),
            ):
                for kind, raw, expected in (
                    ("abn", "ABN 51 824 753 556", "51824753556"),
                    ("acn", "ACN: 004-085-616", "004085616"),
                ):
                    for quote in (name, definition, whole):
                        with self.subTest(name=name, kind=kind, quote=quote):
                            value = mapping(name, kind, quote)
                            records = [
                                {
                                    "source_id": "invented",
                                    "source_record_id": "1",
                                    "raw_record": {name: raw},
                                }
                            ]
                            before = copy.deepcopy((value, records))
                            compiled = self.compile(value, docs)
                            output, report = extract_records(records, compiled)
                            self.assertEqual(report["errors"], [])
                            self.assertEqual(
                                output[0]["values"], {"entity." + kind: expected}
                            )
                            self.assertEqual(
                                output[0]["raw_record"], records[0]["raw_record"]
                            )
                            self.assertEqual((value, records), before)
                            support = compiled[0]["semantic_support"]
                            self.assertEqual(
                                support["basis"],
                                "matching_whole_cell_label_and_checksum",
                            )
                            if quote != name:
                                self.assertEqual(
                                    support["evidence_binding"],
                                    "exact_same_field_identifier_definition",
                                )
                                self.assertTrue(support["contexts"])

    def test_single_type_and_wrapped_original_definition_quotes(self):
        for docs, quote in (
            (
                "Reference: Australian Business Number (ABN).",
                "Australian Business Number (ABN)",
            ),
            ("Reference: ABN or\n  ACN number.", "ABN or\n  ACN number"),
        ):
            with self.subTest(docs=docs):
                self.assertEqual(len(self.compile(mapping(quote=quote), docs)), 1)

    def test_source_level_quote_remains_unbound_with_actionable_diagnostic(self):
        value = mapping(quote="ABN or ACN number")
        findings = self.failure(value, "The source publishes an ABN or ACN number.")
        detail = findings[0]["identifier_evidence"]
        self.assertEqual(
            detail["reason_code"], "labelled_identifier_evidence_not_bound"
        )
        self.assertEqual(detail["supplied_quote"], "ABN or ACN number")
        self.assertEqual(detail["exact_primary_header"], "Reference")
        self.assertEqual(detail["input_field"], "Reference")
        self.assertTrue(detail["require_label"])
        self.assertIn(
            "exact_primary_native_header_with_require_label_true",
            detail["accepted_evidence_forms"],
        )
        repaired = copy.deepcopy(value)
        repaired["semantic_evidence"] = "Reference"
        self.assertEqual(len(self.compile(repaired)), 1)

    def test_neighbor_quote_cannot_borrow_the_primary_definition(self):
        docs = "Reference: ABN or ACN number; Other: an administrative token."
        for quote in (
            "Other",
            "an administrative token",
            "Other: an administrative token.",
        ):
            with self.subTest(quote=quote):
                self.failure(mapping(quote=quote), docs, ["Reference", "Other"])

    def test_another_fields_identifier_definition_cannot_bind_primary(self):
        docs = "Reference: an administrative token.\nOther: ABN or ACN number."
        self.failure(mapping(quote="ABN or ACN number"), docs, ["Reference", "Other"])

    def test_fabricated_and_rewritten_quotes_remain_rejected(self):
        docs = "Reference: Australian Business Number (ABN)."
        for quote in ("An ABN", "Australian business number (ABN)"):
            with self.subTest(quote=quote):
                self.assertEqual(
                    self.failure(mapping(quote=quote), docs)[0]["code"], "exact_quote"
                )

    def test_wrong_type_uncertain_or_composite_definition_is_not_evidence(self):
        for definition in (
            "ACN number",
            "ABN and ACN number",
            "ABN or ACN or NZBN number",
            "possibly an ABN",
            "not an ABN",
        ):
            with self.subTest(definition=definition):
                self.failure(mapping(quote=definition), f"Reference: {definition}.")

    def test_foreign_owner_and_mixed_business_roles_remain_rejected(self):
        for definition in ("ABN of the director", "ABN of the supplier's director"):
            with self.subTest(definition=definition):
                findings = self.failure(
                    mapping(quote=definition), f"Reference: {definition}."
                )
                self.assertTrue(
                    any(
                        f.get("semantic_support", {}).get("status") == "foreign_subject"
                        for f in findings
                    )
                )
        identifier = mapping(quote="ABN of the supplier")
        website = {
            "source_field": "Buyer website",
            "source_fields": ["Buyer website"],
            "canonical_field": "entity.website",
            "confidence": 0.8,
            "semantic_evidence": "Buyer website",
            "ast": {"op": "field", "name": "Buyer website"},
        }
        with self.assertRaises(Failure) as raised:
            compile_mappings(
                [identifier, website],
                ["Reference", "Buyer website"],
                "Reference: ABN of the supplier.",
            )
        self.assertIn(
            "mixed_roles", {f["code"] for f in raised.exception.details["findings"]}
        )

    def test_quote_cannot_change_actual_input_field_or_canonical_type(self):
        value = mapping(quote="ABN or ACN number")
        value["source_fields"].append("Other")
        value["ast"]["input"]["name"] = "Other"
        value["ast"] = {
            "op": "when",
            "condition": {
                "op": "is_present",
                "input": {"op": "field", "name": "Reference"},
            },
            "then": value["ast"],
            "else": {"op": "omit", "reason": "No reference"},
        }
        findings = self.failure(value, "Reference: ABN or ACN number.")
        self.assertEqual(
            findings[0]["identifier_evidence"]["reason_code"],
            "identifier_target_or_primary_mismatch",
        )
        value = mapping(quote="ABN or ACN number")
        value["canonical_field"] = "entity.acn"
        findings = self.failure(value, "Reference: ABN or ACN number.")
        self.assertEqual(
            findings[0]["identifier_evidence"]["reason_code"],
            "identifier_target_or_primary_mismatch",
        )

    def test_bare_mixed_identifier_does_not_gain_type_support(self):
        value = mapping(quote="ABN or ACN number", require_label=False)
        self.failure(value, "Reference: ABN or ACN number.")

    def test_label_checksum_and_whole_cell_requirements_still_execute(self):
        value = mapping(quote="ABN or ACN number")
        compiled = self.compile(value, "Reference: ABN or ACN number.")
        raw_values = [
            "ABN 51 824 753 556",
            "51824753556",
            51824753556,
            "ACN 51824753556",
            "ABN 51824753557",
            "prefix ABN 51824753556",
            "ABN 51824753556 suffix",
            "ABN 51824753556 ACN 004085616",
            "",
            None,
        ]
        records = [
            {
                "source_id": "invented",
                "source_record_id": str(i),
                "raw_record": {"Reference": raw},
            }
            for i, raw in enumerate(raw_values)
        ]
        output, report = extract_records(records, compiled)
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            [row["values"] for row in output],
            [{"entity.abn": "51824753556"}] + [{}] * (len(records) - 1),
        )
        self.assertEqual(
            [row["raw_record"] for row in output],
            [row["raw_record"] for row in records],
        )

    def test_name_group_collector_clears_stale_values_on_invalid_input(self):
        scope = {"entity.legal_name": ["Previous"]}
        with self.assertRaises(Failure):
            compile_mappings(None, [], name_group_scope=scope)
        self.assertEqual(scope, {})

    def test_parser_in_another_branch_or_predicate_cannot_validate_a_raw_path(self):
        for name in ("Reference", "Entry token"):
            for kind in ("abn", "acn"):
                for placement in ("then", "else", "predicate", "nested"):
                    with self.subTest(name=name, kind=kind, placement=placement):
                        value = mapping(name, kind)
                        parser = value["ast"]
                        raw = {"op": "field", "name": name}
                        condition = {"op": "is_present", "input": copy.deepcopy(raw)}
                        value["ast"] = {
                            "op": "when",
                            "condition": condition,
                            "then": copy.deepcopy(parser),
                            "else": copy.deepcopy(raw),
                        }
                        if placement == "then":
                            value["ast"]["then"], value["ast"]["else"] = raw, parser
                        elif placement == "predicate":
                            condition["input"] = parser
                            value["ast"]["then"] = copy.deepcopy(raw)
                        elif placement == "nested":
                            value["ast"] = {"op": "trim", "input": value["ast"]}
                        findings = self.failure(value)
                        supports = [f.get("semantic_support", {}) for f in findings]
                        self.assertTrue(
                            any(
                                s.get("reason_code")
                                == "identifier_value_path_unchecked"
                                for s in supports
                            )
                        )

    def test_all_checked_value_branches_and_transparent_wrappers_remain_supported(self):
        for kind, label, digits in (
            ("abn", "ABN", "51824753556"),
            ("acn", "ACN", "004085616"),
        ):
            for operation in ("typed", "mixed", "validate"):
                for wrapper in (None, "trim", "collapse_ws", "strip_id_separators"):
                    with self.subTest(kind=kind, operation=operation, wrapper=wrapper):
                        value = mapping(kind=kind)
                        parser = value["ast"]
                        if operation == "mixed":
                            parser.pop("require_label")
                            parser["op"] = "parse_mixed_au_id"
                        elif operation == "validate":
                            parser = {
                                "op": "validate_" + kind,
                                "input": {
                                    "op": "strip_id_separators",
                                    "input": {"op": "field", "name": "Reference"},
                                },
                            }
                        value["ast"] = {
                            "op": "when",
                            "condition": {
                                "op": "is_present",
                                "input": {"op": "field", "name": "Reference"},
                            },
                            "then": parser,
                            "else": {"op": "omit", "reason": "No identifier"},
                        }
                        if wrapper:
                            value["ast"] = {"op": wrapper, "input": value["ast"]}
                        compiled = self.compile(value, "Reference: ABN or ACN number.")
                        records = [
                            {
                                "source_id": "invented",
                                "source_record_id": str(i),
                                "raw_record": {"Reference": raw},
                            }
                            for i, raw in enumerate(
                                (
                                    digits
                                    if operation == "validate"
                                    else label + " " + digits,
                                    "",
                                    "invalid",
                                )
                            )
                        ]
                        output, report = extract_records(records, compiled)
                        self.assertEqual(report["errors"], [])
                        self.assertEqual(
                            [row["values"] for row in output],
                            [{"entity." + kind: digits}, {}, {}],
                        )

    def test_post_parser_transform_cannot_replace_the_checked_identifier(self):
        for wrapper in (
            {
                "op": "enum_lookup",
                "values": {"51824753556": "53004085616"},
                "default": None,
            },
            {"op": "parse_date"},
        ):
            with self.subTest(wrapper=wrapper):
                value = mapping()
                value["ast"] = {**wrapper, "input": value["ast"]}
                findings = self.failure(value)
                self.assertTrue(
                    any(
                        f.get("semantic_support", {}).get("reason_code")
                        == "identifier_value_path_unchecked"
                        for f in findings
                    )
                )

    def test_opposite_checksum_in_another_branch_cannot_validate_target(self):
        for kind, opposite in (("abn", "acn"), ("acn", "abn")):
            value = mapping(kind=kind)
            value["ast"] = {
                "op": "when",
                "condition": {
                    "op": "is_present",
                    "input": {"op": "field", "name": "Reference"},
                },
                "then": {
                    "op": "validate_" + opposite,
                    "input": {"op": "field", "name": "Reference"},
                },
                "else": value["ast"],
            }
            self.failure(value)

    def test_outer_checksum_cannot_bypass_a_typed_value_paths_label(self):
        for kind in ("abn", "acn"):
            for placement in ("branch", "predicate"):
                with self.subTest(kind=kind, placement=placement):
                    value = mapping(kind=kind)
                    typed = value["ast"]
                    raw = {"op": "field", "name": "Reference"}
                    value["ast"] = {
                        "op": "validate_" + kind,
                        "input": {
                            "op": "when",
                            "condition": {
                                "op": "is_present",
                                "input": typed if placement == "predicate" else raw,
                            },
                            "then": copy.deepcopy(raw),
                            "else": copy.deepcopy(typed),
                        },
                    }
                    findings = self.failure(value)
                    support = next(
                        f["semantic_support"]
                        for f in findings
                        if f.get("semantic_support", {}).get("reason_code")
                        == "identifier_value_path_unchecked"
                    )
                    self.assertTrue(support["requires_typed_value_path"])
                    self.assertIn("/input/then", support["unchecked_ast_paths"])

    def test_outer_checksum_preserves_a_fully_typed_conditional(self):
        value = mapping()
        value["ast"] = {
            "op": "validate_abn",
            "input": {
                "op": "when",
                "condition": {
                    "op": "is_present",
                    "input": {"op": "field", "name": "Reference"},
                },
                "then": value["ast"],
                "else": {"op": "omit", "reason": "No identifier"},
            },
        }
        compiled = self.compile(value)
        records = [
            {
                "source_id": "invented",
                "source_record_id": str(i),
                "raw_record": {"Reference": raw},
            }
            for i, raw in enumerate(("ABN 51824753556", "51824753556", ""))
        ]
        output, report = extract_records(records, compiled)
        self.assertEqual(report["errors"], [])
        self.assertEqual(
            [row["values"] for row in output], [{"entity.abn": "51824753556"}, {}, {}]
        )


if __name__ == "__main__":
    unittest.main()
