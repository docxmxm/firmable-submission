"""Subject review and profile abstention preserve evidence and useful links."""

import copy
import unittest

from firmable_runtime.identity import build_profile_view, link_records, withdraw_view


def observation(source, values, reliability):
    values = {"entity.abn": "61000000002", **values}
    return {
        "source_id": source,
        "source_record_id": "1",
        "raw_record": dict(values),
        "values": values,
        "field_confidence": {field: 0.9 for field in values},
        "source_reliability": reliability,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "observed_at_basis": "ingested_at_proxy",
    }


class SubjectReviewTests(unittest.TestCase):
    def test_losing_counterpart_type_does_not_clear_person_subject_risk(self):
        person = observation("alpha", {}, 0.5)
        person["raw_record"].update(
            {
                "License Holder Type Description": "Natural person",
                "License Holder Name": "Alex Example",
                "License No": "EXAMPLE-1",
            }
        )
        company = observation("beta", {"entity.entity_type": "company"}, 0.5)
        original_raw = copy.deepcopy(company["raw_record"])
        baseline = link_records([person, company])
        self.assertEqual(baseline["links"], [])
        self.assertEqual(
            baseline["candidate_decisions"][0]["reason"],
            "person_company_subject_conflict",
        )
        for value in (None, "unknown"):
            with self.subTest(counterpart_type=value):
                changed = copy.deepcopy(company)
                if value is None:
                    changed["values"].pop("entity.entity_type")
                else:
                    changed["values"]["entity.entity_type"] = value
                before = copy.deepcopy([person, changed])
                result = link_records([person, changed])
                decision = result["candidate_decisions"][0]
                self.assertEqual(result["links"], [])
                self.assertFalse(decision["accepted"])
                self.assertEqual(
                    decision["reason"], "person_unknown_subject_needs_review"
                )
                self.assertEqual(decision["subject_kind_evidence"]["right"], [])
                self.assertEqual(
                    decision["subject_kind_evidence"]["left"][0]["basis"],
                    "explicit_licence_holder_type",
                )
                for unlinked in result["unlinked"]:
                    self.assertIn(decision["reason"], unlinked["reasons"])
                self.assertEqual([person, changed], before)
                self.assertEqual(changed["raw_record"], original_raw)

    def test_abstract_fields_and_known_other_types_do_not_prove_person_ownership(self):
        # Subject evidence may already be canonical even when native names are opaque.
        for counterpart_type in (None, "unknown", "trust", "partnership", "other"):
            for person_source in ("a", "z"):
                with self.subTest(kind=counterpart_type, person_source=person_source):
                    person = observation(
                        person_source, {"entity.entity_type": "sole_trader"}, 0.5
                    )
                    peer_values = {"entity.acn": "000000000"}
                    if counterpart_type is not None:
                        peer_values["entity.entity_type"] = counterpart_type
                    peer = observation("m", peer_values, 0.5)
                    person["raw_record"] = {"X17": "61000000002", "X18": "P"}
                    peer["raw_record"] = {"Y17": "61000000002", "Y18": "000000000"}
                    result = link_records([person, peer])
                    self.assertEqual(result["links"], [])
                    decision = result["candidate_decisions"][0]
                    self.assertEqual(
                        decision["reason"], "person_unknown_subject_needs_review"
                    )
                    person_side = "left" if person_source == "a" else "right"
                    self.assertEqual(
                        decision["subject_kind_evidence"][person_side][0]["kind"],
                        "person",
                    )

    def test_matching_names_do_not_supply_missing_subject_ownership(self):
        rows = [
            observation(
                "a",
                {
                    "entity.entity_type": "sole_trader",
                    "entity.legal_name": "Alex Example",
                },
                0.5,
            ),
            observation("b", {"entity.legal_name": "Alex Example"}, 0.5),
        ]
        result = link_records(rows)
        self.assertEqual(result["links"], [])
        self.assertEqual(
            result["candidate_decisions"][0]["reason"],
            "person_unknown_subject_needs_review",
        )

    def test_compatible_people_and_no_person_risk_keep_existing_links(self):
        for kinds in (
            (None, None),
            ("unknown", "unknown"),
            ("company", None),
            (None, "company"),
            ("company", "company"),
            ("sole_trader", "sole_trader"),
        ):
            with self.subTest(kinds=kinds):
                rows = [
                    observation(
                        source,
                        {"entity.entity_type": kind} if kind is not None else {},
                        0.5,
                    )
                    for source, kind in zip(("a", "b"), kinds)
                ]
                result = link_records(rows)
                self.assertEqual(len(result["links"]), 1)
                self.assertTrue(result["candidate_decisions"][0]["accepted"])
                self.assertIn("not a calibrated", result["links"][0]["evidence"])

    def test_unrelated_contact_type_is_not_subject_evidence(self):
        rows = [observation("a", {}, 0.5), observation("b", {}, 0.5)]
        rows[0]["raw_record"].update(
            {
                "Contact Type": "Individual",
                "License Holder Name": "Example Operations",
                "License No": "EXAMPLE-1",
            }
        )
        self.assertEqual(len(link_records(rows)["links"]), 1)


class ProfileAbstentionTests(unittest.TestCase):
    def test_unknown_enums_are_omitted_without_mutating_observations(self):
        rows = [
            observation(
                "a",
                {
                    "entity.status": "unknown",
                    "entity.entity_type": "unknown",
                    "entity.legal_name": "unknown",
                    "address.state": "OTHER",
                },
                0.9,
            ),
            observation("b", {}, 0.7),
        ]
        original = copy.deepcopy(rows)
        profile = build_profile_view(rows, link_records(rows))["profiles"][0]
        self.assertNotIn("entity.status", profile["fields"])
        self.assertNotIn("entity.entity_type", profile["fields"])
        self.assertEqual(profile["fields"]["entity.legal_name"]["value"], "unknown")
        self.assertEqual(profile["fields"]["address.state"]["value"], "OTHER")
        self.assertEqual(profile["record_count"], 2)
        self.assertEqual(rows, original)

    def test_unknown_cannot_override_known_value_or_return_after_withdrawal(self):
        for field, known in (
            ("entity.status", "active"),
            ("entity.entity_type", "company"),
        ):
            with self.subTest(field=field):
                rows = [
                    observation("unknown-source", {field: "unknown"}, 0.99),
                    observation("known-source", {field: known}, 0.7),
                ]
                original = copy.deepcopy(rows)
                linking = link_records(rows)
                profiles = build_profile_view(rows, linking)["profiles"]
                selected = profiles[0]["fields"][field]
                self.assertEqual(selected["value"], known)
                self.assertEqual(selected["conflicts"], [])
                self.assertEqual(
                    [claim["source_id"] for claim in selected["provenance"]],
                    ["known-source"],
                )
                withdrawn = withdraw_view(profiles, rows, linking, "known-source")
                self.assertNotIn(field, withdrawn["profiles"][0]["fields"])
                self.assertEqual(withdrawn["delta"][0]["fields_lost"], [field])
                self.assertEqual(rows, original)
