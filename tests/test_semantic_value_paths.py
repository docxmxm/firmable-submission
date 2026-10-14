"""Invented value paths test evidence ownership and composition, without model calls."""

import copy
import json
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings, extract_records


def field(name):
    return {"op": "field", "name": name}


def omitted():
    return {"op": "omit", "reason": "No supported value on this branch."}


def conditional(name, then, otherwise=None):
    return {
        "op": "when",
        "condition": {"op": "is_present", "input": field(name)},
        "then": then,
        "else": omitted() if otherwise is None else otherwise,
    }


def lookup(name, values):
    return {
        "op": "enum_lookup",
        "input": field(name),
        "values": values,
        "default": None,
    }


def joined(*names):
    return {
        "op": "join_address",
        "inputs": [field(name) for name in names],
        "require_all": True,
        "on_missing": "omit",
        "separator": ", ",
    }


def mapping(target, primary, fields, ast):
    return {
        "canonical_field": target,
        "source_field": primary,
        "source_fields": fields,
        "confidence": 0.9,
        "semantic_evidence": primary,
        "ast": ast,
    }


class _SemanticValuePathCase(unittest.TestCase):
    def compile(self, candidate, docs=""):
        return compile_mappings([candidate], candidate["source_fields"], docs)

    def failure(self, candidate, docs=""):
        with self.assertRaises(Failure) as raised:
            self.compile(candidate, docs)
        return raised.exception.details["findings"]

    def outputs(self, candidate, rows, docs=""):
        original = copy.deepcopy((candidate, rows))
        records = [
            {"source_id": "invented", "source_record_id": str(i), "raw_record": raw}
            for i, raw in enumerate(rows)
        ]
        observations, report = extract_records(records, self.compile(candidate, docs))
        self.assertEqual(report["errors"], [])
        self.assertEqual([row["raw_record"] for row in observations], rows)
        self.assertEqual((candidate, rows), original)
        return [row["values"] for row in observations]


class EnumValuePathTests(_SemanticValuePathCase):
    def test_predicate_evidence_cannot_authorize_unrelated_enum_values(self):
        for target, primary, definition, values in (
            (
                "entity.entity_type",
                "Legal form",
                "Legal form of the business.",
                {"Company": "company"},
            ),
            (
                "entity.status",
                "Company status",
                "Status of the company.",
                {"Active": "active"},
            ),
        ):
            with self.subTest(target=target):
                other = "Unrelated category"
                docs = (
                    f"{primary}: {definition}\n"
                    f"{other}: An internal reporting category, not the {definition.lower()}"
                )
                candidate = mapping(
                    target,
                    primary,
                    [primary, other],
                    conditional(primary, lookup(other, values)),
                )
                self.failure(candidate, docs)

    def test_supported_primary_cannot_hide_an_unsupported_fallback(self):
        primary, other = "Legal form", "Unrelated category"
        docs = (
            "Legal form: Legal form of the business.\n"
            "Unrelated category: An internal reporting category, not the legal form of the business."
        )
        for ast in (
            conditional(
                primary,
                lookup(primary, {"Company": "company"}),
                lookup(other, {"Company": "company"}),
            ),
            {
                "op": "enum_lookup",
                "input": conditional(primary, field(primary), field(other)),
                "values": {"Company": "company"},
                "default": None,
            },
        ):
            with self.subTest(ast=ast):
                findings = self.failure(
                    mapping("entity.entity_type", primary, [primary, other], ast), docs
                )
                self.assertIn(
                    "enum_value_evidence_missing_or_ambiguous", json.dumps(findings)
                )
                self.assertIn(other, json.dumps(findings))

    def test_enum_codes_use_each_value_fields_own_definition(self):
        docs = (
            "| Field | Description | Data Type | Data Parameters |\n"
            "| --- | --- | --- | --- |\n"
            "| Form A | Legal form of the business | Text | C - Company |\n"
            "| Form B | Legal form of the business | Text | C - Partnership |\n"
        )
        candidate = mapping(
            "entity.entity_type",
            "Form A",
            ["Form A", "Form B"],
            conditional(
                "Form A",
                lookup("Form A", {"C": "company"}),
                lookup("Form B", {"C": "company"}),
            ),
        )
        findings = self.failure(candidate, docs)
        self.assertIn("/else/values/C", json.dumps(findings))
        candidate["ast"]["else"]["values"]["C"] = "partnership"
        self.assertEqual(
            self.outputs(
                candidate,
                [
                    {"Form A": "C", "Form B": "C"},
                    {"Form A": "", "Form B": "C"},
                ],
                docs,
            ),
            [
                {"entity.entity_type": "company"},
                {"entity.entity_type": "partnership"},
            ],
        )

    def test_enum_above_a_conditional_cannot_borrow_primary_code_meaning(self):
        docs = (
            "| Field | Description | Data Type | Data Parameters |\n"
            "| --- | --- | --- | --- |\n"
            "| Form A | Legal form of the business | Text | C - Company |\n"
            "| Form B | Legal form of the business | Text | C - Partnership |\n"
        )
        candidate = mapping(
            "entity.entity_type",
            "Form A",
            ["Form A", "Form B"],
            {
                "op": "enum_lookup",
                "input": conditional("Form A", field("Form A"), field("Form B")),
                "values": {"C": "company"},
                "default": None,
            },
        )
        self.failure(candidate, docs)

    def test_enum_value_normalization_preserves_valid_branches_and_omission(self):
        primary, flag = "Legal form", "Selector"
        normalized = {
            "op": "collapse_ws",
            "input": {"op": "trim", "input": field(primary)},
        }
        ast = conditional(
            flag,
            {
                "op": "enum_lookup",
                "input": normalized,
                "values": {"Company": "company"},
                "default": None,
            },
        )
        candidate = mapping("entity.entity_type", primary, [primary, flag], ast)
        self.assertEqual(
            self.outputs(
                candidate,
                [
                    {primary: "  Company  ", flag: "yes"},
                    {primary: "Company", flag: ""},
                    {primary: "Unexplained", flag: "yes"},
                    {primary: None, flag: "yes"},
                ],
            ),
            [{"entity.entity_type": "company"}, {}, {}, {}],
        )

    def test_predicate_enum_conversions_are_still_validated(self):
        primary = "Legal form"
        ast = conditional(primary, lookup(primary, {"Company": "company"}))
        ast["condition"] = {
            "op": "equals",
            "input": lookup(primary, {"Individual or sole trader": "sole_trader"}),
            "value": "sole_trader",
        }
        candidate = mapping("entity.entity_type", primary, [primary], ast)
        findings = self.failure(candidate)
        self.assertIn(
            "/condition/input/values/Individual or sole trader", json.dumps(findings)
        )


class GovernmentAuthorityTests(_SemanticValuePathCase):
    def test_authority_legal_forms_preserve_supported_values_and_abstention(self):
        for primary in ("Legal form", "category_72"):
            docs = primary + ": Legal form of the business."
            labels = [
                "Government authority",
                "Territory government authority",
                "National government authority",
                "Local government authority",
            ]
            candidate = mapping(
                "entity.entity_type",
                primary,
                [primary],
                lookup(primary, dict.fromkeys(labels, "government")),
            )
            self.assertEqual(
                self.outputs(
                    candidate,
                    [{primary: v} for v in labels + ["", "Unexplained"]],
                    docs,
                ),
                [{"entity.entity_type": "government"}] * len(labels) + [{}, {}],
            )

    def test_documented_authority_codes_use_only_their_own_definition(self):
        primary = "category_72"
        candidate = mapping(
            "entity.entity_type",
            primary,
            [primary],
            lookup(primary, {"q8": "government"}),
        )
        docs = primary + ": Legal form of the business. q8 = Central government authority."
        self.assertEqual(
            self.outputs(candidate, [{primary: "q8"}, {primary: "r9"}], docs),
            [{"entity.entity_type": "government"}, {}],
        )
        self.failure(candidate, primary + ": Legal form of the business. Codes undefined.")

    def test_government_ownership_roles_and_ambiguous_categories_remain_rejected(self):
        primary = "Legal form"
        for label in (
            "Government owned corporation",
            "Company under government authority",
            "Former government authority",
            "Unverified government authority",
            "Government authority or private company",
            "Government authority contractor",
            "Government authority applicant",
            "Government authority (Company)",
        ):
            with self.subTest(label=label):
                candidate = mapping(
                    "entity.entity_type",
                    primary,
                    [primary],
                    lookup(primary, {label: "government"}),
                )
                findings = self.failure(candidate)
                self.assertEqual(findings[0]["code"], "semantic_rule")
                self.assertEqual(
                    findings[0]["semantic_support"]["unsupported_conversion"][
                        "raw_label"
                    ],
                    label,
                )


class ExplicitLocationFormatTests(_SemanticValuePathCase):
    def component_outputs(self, rows, component):
        primary = "Location"
        candidate = mapping(
            "address." + component,
            primary,
            [primary],
            {
                "op": "parse_au_location",
                "input": field(primary),
                "component": component,
            },
        )
        return self.outputs(candidate, [{primary: v} for v in rows])

    def test_explicit_state_names_normalize_without_postcode_inference(self):
        states = {
            "New South Wales": "NSW",
            "Victoria": "VIC",
            "Queensland": "QLD",
            "Western Australia": "WA",
            "South Australia": "SA",
            "Tasmania": "TAS",
            "Australian Capital Territory": "ACT",
            "Northern Territory": "NT",
        }
        for separator in (", ", " "):
            rows = [separator.join(("Exampletown", name, "0820")) for name in states]
            self.assertEqual(
                self.component_outputs(rows, "state"),
                [{"address.state": code} for code in states.values()],
            )
            self.assertEqual(
                self.component_outputs(rows, "postcode"),
                [{"address.postcode": "0820"}] * len(rows),
            )

    def test_explicit_australian_country_suffix_preserves_components(self):
        rows = [
            " Exampletown, NT, 0820, AUSTRALIA ",
            "Exampletown, Northern Territory, 0820, AU",
            "Exampletown NT 0820 Australia",
            "Exampletown Northern Territory 0820 au",
        ]
        for component, value in (
            ("locality", "Exampletown"), ("state", "NT"), ("postcode", "0820"),
        ):
            self.assertEqual(
                self.component_outputs(rows, component),
                [{"address." + component: value}] * len(rows),
            )

    def test_conflicts_typos_incomplete_and_street_values_still_abstain(self):
        rows = [
            "Exampletown, NSW, 2000, NEW ZEALAND",
            "Exampletown, NSW, 2000, GERMANY",
            "Exampletown, NSW, 2000, AUATRALIA",
            "Exampletown, QLDQUEENSLAND, 4000",
            "Exampletown, 2000, AUSTRALIA",
            "Exampletown, NSW, 200, AUSTRALIA",
            "Exampletown, NSW, 2000, AUSTRALIA, extra",
            "Exampletown, NSW 2000, AUSTRALIA",
            "12 Example Street, NSW, 2000, AUSTRALIA",
            "Example Road, Victoria, 3000",
            "Exampletown\nNSW 2000 AUSTRALIA",
            "2000",
            "NOT DISCLOSED",
            "",
            None,
        ]
        for component in ("locality", "state", "postcode"):
            self.assertEqual(self.component_outputs(rows, component), [{}] * len(rows))


class AddressValuePathTests(_SemanticValuePathCase):
    def test_address_compositions_preserve_values_missing_policy_and_raw(self):
        primary, locality = "Business address line 1", "Locality"
        join = joined(primary, locality)
        variants = (
            join,
            {"op": "trim", "input": join},
            {"op": "collapse_ws", "input": {"op": "trim", "input": join}},
            conditional(primary, join),
            {"op": "trim", "input": conditional(primary, join)},
        )
        for ast in variants:
            with self.subTest(ast=ast):
                candidate = mapping("address.full", primary, [primary, locality], ast)
                self.assertEqual(
                    self.outputs(
                        candidate,
                        [
                            {primary: " 12 Test Street ", locality: " Sydney "},
                            {primary: "", locality: "Sydney"},
                            {primary: "12 Test Street", locality: None},
                        ],
                    ),
                    [{"address.full": "12 Test Street, Sydney"}, {}, {}],
                )

    def test_address_branches_can_select_join_or_whole_source_address(self):
        primary, locality, fallback = (
            "Business address line 1",
            "Locality",
            "Company address",
        )
        candidate = mapping(
            "address.full",
            primary,
            [primary, locality, fallback],
            conditional(primary, joined(primary, locality), field(fallback)),
        )
        self.assertEqual(
            self.outputs(
                candidate,
                [
                    {
                        primary: "12 Test Street",
                        locality: "Sydney",
                        fallback: "Other address",
                    },
                    {
                        primary: "",
                        locality: "Sydney",
                        fallback: "Source-provided address",
                    },
                ],
            ),
            [
                {"address.full": "12 Test Street, Sydney"},
                {"address.full": "Source-provided address"},
            ],
        )

    def test_whole_address_conditional_preserves_each_fields_own_evidence(self):
        primary, alternate = "Company address", "Postal address"
        candidate = mapping(
            "address.full",
            primary,
            [primary, alternate],
            conditional(primary, field(primary), field(alternate)),
        )
        self.assertEqual(
            self.outputs(
                candidate,
                [
                    {primary: "", alternate: "PO Box 10"},
                ],
            ),
            [{"address.full": "PO Box 10"}],
        )
        self.failure(
            candidate, "Postal address: The postcode component of the company address."
        )

    def test_address_composition_cannot_hide_a_component_branch(self):
        primary, locality = "Business address line 1", "Locality"
        candidate = mapping(
            "address.full",
            primary,
            [primary, locality],
            conditional(primary, joined(primary, locality), field(locality)),
        )
        self.failure(candidate)

    def test_address_nested_omission_does_not_block_a_supported_sibling(self):
        primary, flag = "Company address", "Selector"
        candidate = mapping(
            "address.full",
            primary,
            [primary, flag],
            conditional(flag, conditional(flag, omitted(), omitted()), field(primary)),
        )
        self.assertEqual(
            self.outputs(
                candidate,
                [
                    {primary: "Source-provided address", flag: "skip"},
                    {primary: "Source-provided address", flag: ""},
                ],
            ),
            [{}, {"address.full": "Source-provided address"}],
        )

    def test_address_with_only_omission_branches_remains_unsupported(self):
        primary = "Company address"
        candidate = mapping(
            "address.full",
            primary,
            [primary],
            conditional(primary, omitted(), omitted()),
        )
        self.failure(candidate)

    def test_address_replacement_transform_remains_unsupported(self):
        primary = "Company address"
        candidate = mapping(
            "address.full",
            primary,
            [primary],
            {
                "op": "enum_lookup",
                "input": field(primary),
                "values": {"Old address": "Replacement address"},
                "default": None,
            },
        )
        self.failure(candidate)

    def test_geographic_only_joins_and_predicate_streets_remain_unsupported(self):
        locality, state = "Locality", "State"
        for primary, fields, ast in (
            (
                locality,
                [locality, state],
                {"op": "trim", "input": joined(locality, state)},
            ),
            (
                "Business address line 1",
                ["Business address line 1", locality, state],
                conditional("Business address line 1", joined(locality, state)),
            ),
        ):
            with self.subTest(ast=ast):
                self.failure(mapping("address.full", primary, fields, ast))

    def test_address_wrappers_preserve_foreign_subject_and_quote_rejection(self):
        primary, locality = "Business address line 1", "Venue locality"
        candidate = mapping(
            "address.full",
            primary,
            [primary, locality],
            conditional(primary, joined(primary, locality)),
        )
        findings = self.failure(candidate)
        self.assertIn("foreign_subject", json.dumps(findings))
        candidate = mapping(
            "address.full",
            primary,
            [primary, "Locality"],
            {"op": "trim", "input": joined(primary, "Locality")},
        )
        candidate["semantic_evidence"] = "Fabricated source quotation"
        findings = self.failure(candidate)
        self.assertIn("exact_quote", json.dumps(findings))


if __name__ == "__main__":
    unittest.main()
