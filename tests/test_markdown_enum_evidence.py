"""Markdown line breaks preserve code boundaries, not permission to infer labels."""

import unittest

from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings
from firmable_runtime.semantic_support import check_support


def mapping(values):
    return {
        "source_field": "Lifecycle",
        "source_fields": ["Lifecycle"],
        "canonical_field": "entity.status",
        "confidence": 0.9,
        "semantic_evidence": "Status of company",
        "ast": {
            "op": "enum_lookup",
            "input": {"op": "field", "name": "Lifecycle"},
            "values": values,
            "default": None,
        },
    }


def documentation(parameters, data_type="Text", extra=""):
    return (
        "| Field | Description | Data Type | Data Parameters |\n"
        "| --- | --- | --- | --- |\n"
        f"| Lifecycle | Status of company | {data_type} | {parameters} |\n" + extra
    )


class MarkdownEnumEvidenceTests(unittest.TestCase):
    def test_br_forms_preserve_supported_relations_and_original_quote(self):
        for boundary in ("<br>", "<br/>", "<br />", "<BR>"):
            with self.subTest(boundary=boundary):
                parameters = boundary.join(
                    ["Allowed values:", "LIVE - Active", "GONE - De-registered"]
                )
                docs = documentation(parameters)
                candidate = mapping({"LIVE": "active", "GONE": "deregistered"})
                self.assertEqual(
                    len(compile_mappings([candidate], ["Lifecycle"], docs)), 1
                )
                support = check_support(candidate, docs, ["Lifecycle"])
                self.assertTrue(support["supported"])
                self.assertEqual(support["contexts"][0]["enum_parameters"], parameters)
                self.assertIn(parameters, support["contexts"][0]["text"])

    def test_wrapped_and_conflicting_qualifiers_cannot_be_narrowed(self):
        for parameters, values in (
            ("LQ - In liquidation<br>or receivership", {"LQ": "in_liquidation"}),
            ("LIVE - Active<br/>or suspended", {"LIVE": "active"}),
            ("LIVE - Active<br />LIVE - Suspended", {"LIVE": "active"}),
            (
                "REG - Registered<br>EXT - External administration (in receivership/liquidation)",
                {"REG": "active"},
            ),
            (
                "REG - Registered<br>EXT - External administration (in receivership/liquidation)",
                {"EXT": "in_liquidation"},
            ),
            ("No definition available<br>MISSING", {"MISSING": "active"}),
        ):
            with self.subTest(parameters=parameters), self.assertRaises(Failure):
                compile_mappings(
                    [mapping(values)], ["Lifecycle"], documentation(parameters)
                )

    def test_other_fields_and_type_cells_cannot_supply_enum_meaning(self):
        docs = documentation(
            "Allowed values:<br>LIVE",
            extra="| Licence lifecycle | Status of licence | Text | LIVE - Active |\n",
        )
        with self.assertRaises(Failure):
            compile_mappings(
                [mapping({"LIVE": "active"})],
                ["Lifecycle", "Licence lifecycle"],
                docs,
                [{"source_field": "Licence lifecycle", "reason": "Different subject."}],
            )
        with self.assertRaises(Failure):
            compile_mappings(
                [mapping({"LIVE": "active"})],
                ["Lifecycle"],
                documentation("Unspecified", data_type="Text<br>LIVE - Active"),
            )

    def test_html_boundaries_do_not_normalize_fabricated_evidence(self):
        candidate = mapping({"LIVE": "active"})
        candidate["semantic_evidence"] = "Status of company; LIVE - Active"
        with self.assertRaises(Failure):
            compile_mappings(
                [candidate],
                ["Lifecycle"],
                documentation("Allowed values:<br>LIVE - Active"),
            )


if __name__ == "__main__":
    unittest.main()
