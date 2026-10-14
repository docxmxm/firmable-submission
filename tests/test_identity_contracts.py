"""Canonical name roles and final decision consistency, independent of a source."""

import copy
import unittest

from firmable_runtime.identity import link_records


def record(source, values):
    return {
        "source_id": source,
        "source_record_id": "row-17",
        "raw_record": {
            "cell_" + str(i): value for i, value in enumerate(values.values())
        },
        "values": values,
    }


class NameRoleContracts(unittest.TestCase):
    def test_legal_and_trading_names_need_not_match(self):
        for target, identifier in (
            ("entity.abn", "61000000002"),
            ("entity.acn", "134567801"),
        ):
            for reversed_roles in (False, True):
                with self.subTest(target=target, reversed_roles=reversed_roles):
                    rows = [
                        record(
                            "alpha",
                            {
                                target: identifier,
                                "entity.legal_name": "Orion Manufacturing Pty Ltd",
                            },
                        ),
                        record(
                            "zeta",
                            {
                                target: identifier,
                                "entity.trading_name": "Blue Harbor Kitchen",
                            },
                        ),
                    ]
                    if reversed_roles:
                        rows.reverse()
                    before = copy.deepcopy(rows)
                    result = link_records(rows)
                    self.assertEqual(len(result["links"]), 1)
                    self.assertEqual(len(result["links"][0]["pairs"]), 1)
                    self.assertEqual(rows, before)

    def test_one_business_may_have_multiple_distinct_trading_names(self):
        rows = [
            record(
                "alpha",
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "Orion Manufacturing Pty Ltd",
                },
            ),
            record(
                "beta",
                {
                    "entity.abn": "61000000002",
                    "entity.trading_name": "Blue Harbor Kitchen",
                },
            ),
            record(
                "gamma",
                {
                    "entity.abn": "61000000002",
                    "entity.trading_name": "Forest Bicycle Repairs",
                },
            ),
        ]
        result = link_records(rows)
        self.assertEqual(len(result["links"]), 1)
        self.assertEqual(len(result["links"][0]["pairs"]), 3)

    def test_shared_trading_name_cannot_hide_a_legal_name_conflict(self):
        rows = [
            record(
                "alpha",
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "Orion Research Pty Ltd",
                    "entity.trading_name": "Shared Banner",
                },
            ),
            record(
                "beta",
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "Maple Ventures Pty Ltd",
                    "entity.trading_name": "Shared Banner",
                },
            ),
        ]
        result = link_records(rows)
        self.assertEqual(result["links"], [])
        self.assertEqual(
            result["candidate_decisions"][0]["reason"], "incompatible_names_need_review"
        )

    def test_legal_name_conflict_still_requires_review(self):
        rows = [
            record(
                "alpha",
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "Orion Research Pty Ltd",
                },
            ),
            record(
                "beta",
                {
                    "entity.abn": "61000000002",
                    "entity.legal_name": "Maple Ventures Pty Ltd",
                },
            ),
        ]
        self.assertEqual(link_records(rows)["links"], [])

    def test_name_roles_never_override_identifier_or_subject_conflicts(self):
        for conflict in ("identifier", "subject"):
            with self.subTest(conflict=conflict):
                rows = [
                    record(
                        "alpha",
                        {
                            "entity.abn": "61000000002",
                            "entity.legal_name": "Orion Research Pty Ltd",
                            "entity.entity_type": "company",
                        },
                    ),
                    record(
                        "beta",
                        {
                            "entity.abn": "61000000002",
                            "entity.trading_name": "Blue Harbor Kitchen",
                        },
                    ),
                ]
                if conflict == "identifier":
                    rows[0]["values"]["entity.acn"] = "000000000"
                    rows[1]["values"]["entity.acn"] = "000000019"
                    for row in rows:
                        row["raw_record"]["cell_42"] = row["values"]["entity.acn"]
                else:
                    rows[1]["values"]["entity.entity_type"] = "sole_trader"
                self.assertEqual(link_records(rows)["links"], [])

    def test_shared_brand_or_parent_identifier_is_not_an_identity_anchor(self):
        rows = [
            record(
                "alpha",
                {"entity.abn": "61000000002", "entity.trading_name": "Shared Banner"},
            ),
            record(
                "beta",
                {"entity.abn": "51111111113", "entity.trading_name": "Shared Banner"},
            ),
        ]
        rows[1]["raw_record"]["related_parent_identifier"] = "61000000002"
        self.assertEqual(link_records(rows)["links"], [])

    def test_names_alone_cannot_link_records(self):
        rows = [
            record(source, {"entity.trading_name": "Shared Banner"})
            for source in ("alpha", "beta")
        ]
        self.assertEqual(link_records(rows)["links"], [])


class DecisionConsistencyContracts(unittest.TestCase):
    def test_ambiguity_rejection_clears_the_policy_score(self):
        rows = [
            record("alpha", {"entity.abn": "61000000002"}),
            record("beta", {"entity.abn": "61000000002", "entity.acn": "000000000"}),
            record("gamma", {"entity.acn": "000000000"}),
        ]
        before = copy.deepcopy(rows)
        result = link_records(rows)
        self.assertEqual(result["links"], [])
        self.assertEqual(len(result["candidate_decisions"]), 2)
        for decision in result["candidate_decisions"]:
            self.assertFalse(decision["accepted"])
            self.assertEqual(decision["reason"], "ambiguous_multi_anchor_membership")
            self.assertEqual(decision["link_confidence"], 0.0)
        self.assertEqual(rows, before)
