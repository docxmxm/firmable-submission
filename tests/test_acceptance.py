"""Task acceptance cannot be weakened by a candidate or a later extraction."""

import unittest

from firmable_runtime.acceptance import (
    evaluate_acceptance,
    freeze_requirements,
    unresolved_history,
)
from firmable_runtime.common import Failure


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.contract = freeze_requirements({"required_fields": ["entity.legal_name"]})
        self.config = {"field_mappings": [{"canonical_field": "entity.legal_name"}]}
        self.rows = [
            {
                "source_id": "register",
                "source_record_id": "1",
                "entity": {"legal_name": "Aster"},
            },
            {"source_id": "register", "source_record_id": "2", "entity": {}},
        ]

    def test_no_default_quota_or_inferred_task_success(self):
        result = evaluate_acceptance(
            self.contract, self.config, self.rows, {}, scope="proposal"
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["fields"][0]["accepted_record_fraction"], 0.5)
        self.assertEqual(result["fields"][0]["accepted_value_count"], 1)
        empty = evaluate_acceptance(
            freeze_requirements({}), self.config, self.rows, {}, scope="proposal"
        )
        self.assertEqual(empty["status"], "not_configured")

    def test_history_keeps_probe_and_development_findings_with_bound_scopes(self):
        failed = evaluate_acceptance(
            self.contract, self.config, [], {}, scope="proposal"
        )
        passed = evaluate_acceptance(
            self.contract, self.config, self.rows, {}, scope="proposal"
        )
        development = evaluate_acceptance(
            self.contract,
            self.config,
            self.rows,
            {
                "errors": [
                    {
                        "canonical_field": "entity.legal_name",
                        "reason_code": "quote_not_found",
                        "validation_stage": "llm_review_evidence",
                    }
                ]
            },
            scope="development_validation",
        )
        legacy = {
            "decision_turn": 0,
            "config_hash": "old-config",
            "probe_ref": {"artifact_path": "probe-old"},
            "feedback": {"task_acceptance": failed},
        }
        current = {
            "decision_turn": 1,
            "config_hash": "new-config",
            "probe_ref": {"artifact_path": "probe-new"},
            "feedback": {"task_acceptance": passed},
            "validation_feedback": {"task_acceptance": development},
            "validation_ref": {"artifact_path": "validation-new"},
        }
        entries = unresolved_history({"legacy": legacy, "current": current})["entries"]
        self.assertEqual(
            [item["scope"] for item in entries], ["proposal", "development_validation"]
        )
        self.assertEqual(entries[0]["execution_ref"], legacy["probe_ref"])
        self.assertEqual(entries[1]["probe_ref"], current["probe_ref"])
        self.assertEqual(entries[1]["execution_ref"], current["validation_ref"])
        self.assertEqual(
            entries[1]["diagnostics"][0]["validation_stage"], "llm_review_evidence"
        )

    def test_unmapped_blank_and_unknown_never_satisfy_requirement(self):
        for config, value in (
            ({}, "Aster"),
            (self.config, " "),
            (self.config, "unknown"),
        ):
            with self.subTest(value=value):
                rows = [{**self.rows[0], "entity": {"legal_name": value}}]
                result = evaluate_acceptance(
                    self.contract, config, rows, {}, scope="proposal"
                )
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["fields"][0]["status"], "needs_evidence")

    def test_model_uncertainty_and_execution_failure_are_not_accepted(self):
        for reason, expected in (
            ("name_role_uncertain", "needs_evidence"),
            ("name_role_contradicted", "failed"),
        ):
            report = {
                "errors": [
                    {"canonical_field": "entity.legal_name", "reason_code": reason}
                ]
            }
            result = evaluate_acceptance(
                self.contract, self.config, self.rows, report, scope="proposal"
            )
            self.assertEqual(result["fields"][0]["status"], expected)

    def test_full_extraction_cannot_upgrade_prior_uncertainty(self):
        prior = evaluate_acceptance(
            self.contract,
            self.config,
            self.rows,
            {
                "errors": [
                    {
                        "canonical_field": "entity.legal_name",
                        "reason_code": "name_role_uncertain",
                    }
                ]
            },
            scope="development_validation",
        )
        result = evaluate_acceptance(
            self.contract,
            self.config,
            self.rows,
            {},
            scope="full_extraction",
            prior_acceptance=prior,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["fields"][0]["status"], "needs_evidence")
        self.assertEqual(result["fields"][0]["accepted_value_count"], 1)

    def test_field_compile_failure_leaves_other_mappings_unexecuted(self):
        contract = freeze_requirements(
            {"required_fields": ["entity.legal_name", "entity.trading_name"]}
        )
        config = {
            "field_mappings": [
                {"canonical_field": target} for target in contract["required_fields"]
            ]
        }
        report = {
            "execution_status": "not_executed",
            "errors": [
                {
                    "mapping_index": 0,
                    "canonical_field": "entity.legal_name",
                    "reason_code": "semantic_rule",
                    "semantic_support": {"supported": False},
                }
            ],
        }
        result = evaluate_acceptance(contract, config, [], report, scope="proposal")
        legal, trade = result["fields"]
        self.assertEqual(legal["status"], "failed")
        self.assertEqual(
            legal["diagnostics"][0]["semantic_support"], {"supported": False}
        )
        self.assertEqual(trade["status"], "needs_evidence")
        self.assertEqual(trade["reason_code"], "candidate_not_executed")
        self.assertEqual(trade["diagnostics"], [])
        self.assertEqual(trade["accepted_value_count"], 0)
        self.assertEqual(result["status"], "partial")

    def test_global_compile_block_is_not_a_field_semantic_failure(self):
        result = evaluate_acceptance(
            self.contract,
            self.config,
            [],
            {
                "execution_status": "not_executed",
                "errors": [
                    {"reason_code": "native_inventory", "error_scope": "candidate"}
                ],
            },
            scope="proposal",
        )
        self.assertEqual(result["fields"][0]["status"], "needs_evidence")
        self.assertEqual(result["fields"][0]["reason_code"], "candidate_not_executed")

    def test_prior_acceptance_binds_requirements_and_config(self):
        prior = evaluate_acceptance(
            self.contract, self.config, self.rows, {}, scope="proposal"
        )
        for contract, config in (
            (freeze_requirements({}), self.config),
            (self.contract, {}),
        ):
            with self.assertRaises(Failure) as error:
                evaluate_acceptance(
                    contract,
                    config,
                    self.rows,
                    {},
                    scope="full_extraction",
                    prior_acceptance=prior,
                )
            self.assertEqual(error.exception.kind, "checkpoint_mismatch")

    def test_caller_contract_is_validated_and_copied(self):
        request = {"required_fields": ["entity.legal_name"]}
        contract = freeze_requirements(request)
        request["required_fields"].clear()
        self.assertEqual(contract["required_fields"], ["entity.legal_name"])
        for value in (None, "entity.abn", ["fake"], ["entity.abn", "entity.abn"], [3]):
            with self.assertRaises(Failure):
                freeze_requirements({"required_fields": value})


if __name__ == "__main__":
    unittest.main()
