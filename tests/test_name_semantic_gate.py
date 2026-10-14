"""Scripted name reviews cannot replace nonsemantic compiler checks or approval."""

import copy
import unittest

from firmable_runtime.common import Failure
from firmable_runtime.config_audit import audit_name_mappings
from firmable_runtime.execution import compile_config, run_engine
from test_config_audit import ReviewRuntime, ordinary_name_fixture, review


NAME_DOC = "x07: Appellation under which the company was incorporated."


def add_field(config, records, name, value=None):
    config["source_schema"]["fields"].append(
        {"source_field": name, "inferred_type": "string"}
    )
    for record in records:
        record["raw_record"][name] = value


class NameSemanticGateTests(unittest.TestCase):
    def audit(self, config, records, docs, verdict="supported", claims=None):
        if claims is None:
            claims = [
                review(
                    verdict,
                    [{"kind": "documentation", "quote": docs}],
                    index,
                )
                for index, mapping in enumerate(config["field_mappings"])
                if mapping["canonical_field"]
                in {"entity.legal_name", "entity.trading_name"}
            ]
        return audit_name_mappings(ReviewRuntime(claims, docs), config, records, [])

    def failure(self, config, docs, audit=None):
        with self.assertRaises(Failure) as raised:
            compile_config(config, docs, name_review=audit)
        return raised.exception

    def test_reviewed_synonyms_blank_qualification_and_sole_trader_execute(self):
        definitions = (
            "Appellation under which the company was incorporated.",
            "Registered legal name of the company; may be blank.",
            "Legal name of the individual sole trader.",
        )
        for header in ("x07", "Entry alpha"):
            for definition in definitions:
                with self.subTest(header=header, definition=definition):
                    config, records = ordinary_name_fixture(header, "entity.legal_name")
                    docs = header + ": " + definition
                    before = copy.deepcopy((config, records))
                    self.failure(config, docs)
                    audit = self.audit(config, records, docs)
                    self.assertEqual(audit["status"], "supported")
                    compiled = compile_config(config, docs, name_review=audit)
                    self.assertEqual(
                        compiled[0]["semantic_support"]["basis"],
                        "independent_evidence_bound_name_review",
                    )
                    observations, report = run_engine(
                        config,
                        records,
                        docs,
                        {"licence": "CC BY 4.0"},
                        "2026-01-01T00:00:00+00:00",
                        name_review=audit,
                    )
                    self.assertEqual(report["errors"], [])
                    self.assertEqual(
                        [
                            row.get("entity", {}).get("legal_name")
                            for row in observations
                        ],
                        ["Example Ltd", None, None],
                    )
                    self.assertEqual(
                        [row["raw_record"] for row in observations],
                        [row["raw_record"] for row in records],
                    )
                    self.assertEqual((config, records), before)
                    self.assertIs(audit["human_gold"], False)

    def test_uncertain_or_contradicted_review_never_opens_gate(self):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        for verdict in ("uncertain", "contradicted"):
            with self.subTest(verdict=verdict):
                audit = self.audit(config, records, NAME_DOC, verdict)
                self.assertEqual(audit["status"], "rejected")
                self.failure(config, NAME_DOC, audit)

    def test_supported_review_cannot_override_explicit_name_role_conflict(self):
        for target, definition in (
            (
                "entity.legal_name",
                "Display caption, not the legal name of the company.",
            ),
            ("entity.legal_name", "Registered trading name of the company."),
            ("entity.legal_name", "Registered business name of the company."),
            ("entity.trading_name", "Display caption, not a trading name."),
        ):
            with self.subTest(target=target, definition=definition):
                config, records = ordinary_name_fixture("x07", target)
                docs = "x07: " + definition
                audit = self.audit(config, records, docs)
                self.assertEqual(audit["status"], "supported")
                failure = self.failure(config, docs, audit)
                support = failure.details["findings"][0]["semantic_support"]
                self.assertTrue(support["name_role_conflict"])

    def test_unresolved_primary_cannot_hide_conflicting_alternate_name(self):
        for sampled in (True, False):
            with self.subTest(alternate_sampled=sampled):
                config, records = ordinary_name_fixture("x07", "entity.legal_name")
                if not sampled:
                    records = records[:1]
                add_field(config, records, "z19", "Display alias")
                config["field_mappings"][0]["source_fields"].append("z19")
                config["execution"]["transforms"][0] = {
                    "op": "when",
                    "condition": {
                        "op": "is_present",
                        "input": {"op": "field", "name": "x07"},
                    },
                    "then": {"op": "field", "name": "x07"},
                    "else": {"op": "field", "name": "z19"},
                }
                docs = (
                    NAME_DOC
                    + "\nz19: Display caption, not the legal name of the company."
                )
                audit = self.audit(config, records, docs)
                self.assertEqual(audit["status"], "supported")
                failure = self.failure(config, docs, audit)
                support = failure.details["findings"][0]["semantic_support"]
                self.assertFalse(support["name_role_conflict"])
                self.assertTrue(support["name_branch_support"][0]["name_role_conflict"])

    def test_supported_review_keeps_foreign_subject_hard_gate(self):
        for definition in (
            "Legal name of the director.",
            "Legal name of the supplier's director.",
            "Trading name of the venue.",
        ):
            with self.subTest(definition=definition):
                target = (
                    "entity.trading_name"
                    if definition.startswith("Trading")
                    else "entity.legal_name"
                )
                config, records = ordinary_name_fixture("x07", target)
                docs = "x07: " + definition
                audit = self.audit(config, records, docs)
                self.assertEqual(audit["status"], "supported")
                failure = self.failure(config, docs, audit)
                self.assertEqual(
                    failure.details["findings"][0]["semantic_support"]["status"],
                    "foreign_subject",
                )

    def test_config_documentation_and_mapping_order_bind_review(self):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        add_field(config, records, "z19", "Example trading")
        config["field_mappings"].append(
            {
                **config["field_mappings"][0],
                "source_field": "z19",
                "source_fields": ["z19"],
                "canonical_field": "entity.trading_name",
                "semantic_evidence": "z19",
                "execution_pointer": "/execution/transforms/1",
            }
        )
        config["execution"]["transforms"].append({"op": "field", "name": "z19"})
        docs = NAME_DOC + "\nz19: Trading name of the company."
        audit = self.audit(config, records, docs)
        self.assertEqual(len(compile_config(config, docs, name_review=audit)), 2)
        for change in ("ast", "target", "order", "documentation"):
            with self.subTest(change=change):
                changed, changed_docs = copy.deepcopy(config), docs
                if change == "ast":
                    changed["execution"]["transforms"][0] = {
                        "op": "field",
                        "name": "x07",
                    }
                elif change == "target":
                    changed["field_mappings"][0]["canonical_field"] = (
                        "entity.trading_name"
                    )
                elif change == "order":
                    changed["field_mappings"].reverse()
                else:
                    changed_docs += "\nx07: Superseded definition."
                failure = self.failure(changed, changed_docs, audit)
                self.assertIn(
                    "documentation_hash"
                    if change == "documentation"
                    else "candidate_config_hash",
                    failure.details["mismatched_fields"],
                )

    def test_revalidation_ignores_saved_flags_and_rechecks_quotes_and_branches(self):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        original = self.audit(config, records, NAME_DOC)
        for change in ("quote", "missing_branch", "wrong_value_field", "ghost_mapping"):
            with self.subTest(change=change):
                audit = copy.deepcopy(original)
                row = audit["reviews"][0]
                row["evidence_verified"] = row["branch_analysis_verified"] = True
                if change == "quote":
                    row["evidence_quotes"][0]["quote"] = "Invented legal definition."
                elif change == "missing_branch":
                    row["branch_reviews"] = []
                elif change == "wrong_value_field":
                    row["branch_reviews"][0]["expected_value_fields"] = ["nonexistent"]
                else:
                    row["branch_reviews"][0]["coverage_mapping_indices"] = [99]
                self.failure(config, NAME_DOC, audit)

    def test_exact_mapping_quote_and_declared_dependencies_stay_mandatory(self):
        for change in ("quote", "undeclared_value", "fake_dependency"):
            with self.subTest(change=change):
                config, records = ordinary_name_fixture("x07", "entity.legal_name")
                if change == "quote":
                    config["field_mappings"][0]["semantic_evidence"] = (
                        "Fabricated meaning."
                    )
                else:
                    add_field(config, records, "z19", "Example alias")
                    if change == "undeclared_value":
                        config["execution"]["transforms"][0] = {
                            "op": "field",
                            "name": "z19",
                        }
                        config["unmapped_fields"] = [
                            {"source_field": "z19", "reason": "Unused"}
                        ]
                    else:
                        config["field_mappings"][0]["source_fields"].append("z19")
                audit = self.audit(config, records, NAME_DOC)
                failure = self.failure(config, NAME_DOC, audit)
                if change == "quote":
                    self.assertEqual(audit["status"], "supported")
                    self.assertIn(
                        "exact_quote", [f["code"] for f in failure.details["findings"]]
                    )
                else:
                    self.assertEqual(audit["status"], "local_validation_failed")
                    direct = self.failure(config, NAME_DOC)
                    self.assertIn(
                        "dependency_mismatch",
                        [f["code"] for f in direct.details["findings"]],
                    )

    def test_revalidation_rejects_duplicate_indices_foreign_target_and_uncertain_peer(
        self,
    ):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        add_field(config, records, "z19", "Example alias")
        config["field_mappings"].append(
            {
                **config["field_mappings"][0],
                "source_field": "z19",
                "source_fields": ["z19"],
                "canonical_field": "entity.trading_name",
                "semantic_evidence": "z19",
                "execution_pointer": "/execution/transforms/1",
            }
        )
        config["execution"]["transforms"].append({"op": "field", "name": "z19"})
        docs = NAME_DOC + "\nz19: Trading name of the company."
        original = self.audit(config, records, docs)
        self.assertEqual(len(compile_config(config, docs, name_review=original)), 2)
        for change in ("duplicate_index", "other_target", "uncertain_peer"):
            with self.subTest(change=change):
                audit = copy.deepcopy(original)
                if change == "duplicate_index":
                    audit["reviews"][1] = copy.deepcopy(audit["reviews"][0])
                elif change == "other_target":
                    audit["reviews"][0]["branch_reviews"][0][
                        "coverage_mapping_indices"
                    ] = [1]
                else:
                    audit["reviews"][1]["verdict"] = "uncertain"
                self.failure(config, docs, audit)

    def test_runtime_blank_omission_does_not_need_an_invented_quote(self):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        config["execution"]["transforms"][0] = {
            "op": "when",
            "condition": {"op": "is_present", "input": {"op": "field", "name": "x07"}},
            "then": {"op": "trim", "input": {"op": "field", "name": "x07"}},
            "else": {"op": "omit", "reason": "Absent value"},
        }
        claim = review(quotes=[{"kind": "documentation", "quote": NAME_DOC}])
        claim["branch_reviews"] = [
            {
                "branch_id": branch,
                "documented_condition": "Runtime presence handling; value role is documented separately.",
                "expected_value_fields": fields,
                "current_value_requirement": "available"
                if fields
                else "not_documented",
                "current_value_fields": fields,
                "coverage_mapping_indices": [0] if fields else [],
                "verdict": "supported",
                "evidence_quotes": [{"kind": "documentation", "quote": NAME_DOC}]
                if fields
                else [],
                "reason": "Only nonempty values assert the documented role.",
            }
            for branch, fields in (("/then", ["x07"]), ("/else", []))
        ]
        audit = self.audit(config, records, NAME_DOC, claims=[claim])
        self.assertEqual(audit["status"], "supported")
        self.assertEqual(len(compile_config(config, NAME_DOC, name_review=audit)), 1)

    def test_reviewed_name_does_not_suppress_identifier_type_or_value_path_failure(
        self,
    ):
        for invalid in ("type", "value_path"):
            with self.subTest(invalid=invalid):
                config, records = ordinary_name_fixture("x07", "entity.legal_name")
                add_field(config, records, "Ref", "ABN 51 824 753 556")
                config["field_mappings"].append(
                    {
                        **config["field_mappings"][0],
                        "source_field": "Ref",
                        "source_fields": ["Ref"],
                        "canonical_field": "entity.abn",
                        "semantic_evidence": "Ref",
                        "execution_pointer": "/execution/transforms/1",
                    }
                )
                parser = {
                    "op": "parse_typed_id",
                    "input": {"op": "field", "name": "Ref"},
                    "kind": "acn" if invalid == "type" else "abn",
                    "require_label": True,
                }
                ast = (
                    parser
                    if invalid == "type"
                    else {
                        "op": "when",
                        "condition": {
                            "op": "is_present",
                            "input": {"op": "field", "name": "Ref"},
                        },
                        "then": {"op": "field", "name": "Ref"},
                        "else": parser,
                    }
                )
                config["execution"]["transforms"].append(ast)
                audit = self.audit(config, records, NAME_DOC)
                self.assertEqual(audit["status"], "supported")
                failure = self.failure(config, NAME_DOC, audit)
                self.assertEqual(
                    {f["mapping_index"] for f in failure.details["findings"]}, {1}
                )

    def test_name_review_does_not_turn_bad_checksum_into_an_identifier(self):
        config, records = ordinary_name_fixture("x07", "entity.legal_name")
        add_field(config, records, "Ref", "ABN 51 824 753 555")
        config["field_mappings"].append(
            {
                **config["field_mappings"][0],
                "source_field": "Ref",
                "source_fields": ["Ref"],
                "canonical_field": "entity.abn",
                "semantic_evidence": "Ref",
                "execution_pointer": "/execution/transforms/1",
            }
        )
        config["execution"]["transforms"].append(
            {
                "op": "parse_typed_id",
                "input": {"op": "field", "name": "Ref"},
                "kind": "abn",
                "require_label": True,
            }
        )
        audit = self.audit(config, records, NAME_DOC)
        observations, report = run_engine(
            config,
            records,
            NAME_DOC,
            {"licence": "CC BY 4.0"},
            "2026-01-01T00:00:00+00:00",
            name_review=audit,
        )
        self.assertEqual(report["errors"], [])
        self.assertTrue(all("abn" not in row.get("entity", {}) for row in observations))
        self.assertEqual(observations[0]["entity"]["legal_name"], "Example Ltd")


if __name__ == "__main__":
    unittest.main()
