"""A verified name review can resolve a header using its own explicit definition."""

import copy
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.config_audit import audit_name_mappings
from firmable_runtime.execution import compile_config
from firmable_runtime.semantic_support import subject_support
from test_config_audit import ReviewRuntime, ordinary_name_fixture, review


def audited(header, definition):
    config, records = ordinary_name_fixture(header, "entity.legal_name")
    docs = header + ": " + definition
    runtime = ReviewRuntime(
        [review(quotes=[{"kind": "documentation", "quote": docs}])], docs
    )
    audit = audit_name_mappings(runtime, config, records, [])
    return config, docs, audit


class ReviewedNameSubjectTests(unittest.TestCase):
    def test_header_conflict_requires_verified_review_and_same_field_entity_definition(
        self,
    ):
        for header, definition in (
            (
                "Person Name",
                "Current legal name of the sole trader who is the subject business in this register.",
            ),
            ("Venue Name", "Registered legal name of the company."),
            ("Contact Name", "Legal name of the business."),
        ):
            with self.subTest(header=header):
                config, docs, audit = audited(header, definition)
                self.assertEqual(audit["status"], "supported")
                with self.assertRaises(Failure):
                    compile_config(config, docs)
                compiled = compile_config(config, docs, name_review=audit)
                support = compiled[0]["semantic_support"]
                self.assertTrue(support.get("subject_support", support)["supported"])
                self.assertEqual(config["field_mappings"][0]["source_field"], header)

    def test_review_does_not_override_documented_foreign_or_ambiguous_owner(self):
        for definition in (
            "Legal name of the company's director.",
            "Legal name of the business contact.",
            "Legal name of the venue.",
            "Legal name of the company or individual.",
            "Display name, with no documented business subject.",
        ):
            with self.subTest(definition=definition):
                config, docs, audit = audited("Person Name", definition)
                with self.assertRaises(Failure):
                    compile_config(config, docs, name_review=audit)

    def test_neighboring_field_or_source_level_entity_words_cannot_resolve_header(self):
        for docs in (
            "All rows describe companies.\nPerson Name: Display name.",
            "Other Label: Registered legal name of the company.\nPerson Name: Display name.",
            "Person Name: Legal name of the company. Legal name of the director.",
        ):
            with self.subTest(docs=docs):
                config, records = ordinary_name_fixture(
                    "Person Name", "entity.legal_name"
                )
                runtime = ReviewRuntime(
                    [review(quotes=[{"kind": "documentation", "quote": docs}])], docs
                )
                audit = audit_name_mappings(runtime, config, records, [])
                with self.assertRaises(Failure):
                    compile_config(config, docs, name_review=audit)

    def test_resolution_does_not_waive_role_negation_or_mixed_business_roles(self):
        config, docs, audit = audited(
            "Person Name", "Name of the company, not the legal name."
        )
        with self.assertRaises(Failure):
            compile_config(config, docs, name_review=audit)
        mapping = {
            "canonical_field": "entity.legal_name",
            "source_field": "Person Name",
            "source_fields": ["Person Name", "Other Name", "Flag"],
            "ast": {
                "op": "when",
                "condition": {
                    "op": "is_present",
                    "input": {"op": "field", "name": "Flag"},
                },
                "then": {"op": "field", "name": "Person Name"},
                "else": {"op": "field", "name": "Other Name"},
            },
        }
        result = subject_support(
            mapping,
            "Person Name: Legal name of the supplier.\nOther Name: Legal name of the buyer.",
            mapping["source_fields"],
            reviewed_name=True,
        )
        self.assertFalse(result["supported"])
        self.assertEqual(result["status"], "mixed_roles")

    def test_name_authority_cannot_resolve_an_address_header(self):
        config, docs, _ = audited("Person Name", "Legal name of the company.")
        mapping = copy.deepcopy(config["field_mappings"][0])
        mapping.update(
            canonical_field="address.locality",
            ast={"op": "field", "name": "Person Name"},
        )
        result = subject_support(mapping, docs, ["Person Name"], reviewed_name=True)
        self.assertFalse(result["supported"])
