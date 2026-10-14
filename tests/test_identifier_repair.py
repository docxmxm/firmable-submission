"""Identifier repair feedback and bounded planning over invented native records."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings
from test_agent import NativeFixture, ScriptedProvider, control


class IdentifierFixture(NativeFixture):
    def __init__(self, root, field="Australian Business Number", definition=""):
        super().__init__(root)
        self.field = field
        self.rows = [{field: "51824753556", "row_note": str(i)} for i in range(8)]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=[field, "row_note"])
            writer.writeheader()
            writer.writerows(self.rows)
        (self.evidence / "definitions.md").write_text(
            definition + "\nrow_note: Internal row reference.\nLicence: CC BY 4.0\n"
        )
        self.ontology.write_text("entity:\n  abn: string\n  legal_name: string\n")
        self.request["required_fields"] = ["entity.abn", "entity.legal_name"]

    def config(self, mode="good"):
        if mode == "empty":
            return {
                "field_mappings": [],
                "unmapped_fields": [
                    {"source_field": field, "reason": "Planner abandoned this field."}
                    for field in (self.field, "row_note")
                ],
                "execution": {"kind": "ast", "transforms": []},
            }
        return {
            "field_mappings": [
                {
                    "source_field": self.field,
                    "source_fields": [self.field],
                    "canonical_field": "entity.abn",
                    "confidence": 0.9,
                    "transformation": "Parse the source-declared identifier and check its checksum.",
                    "semantic_evidence": self.field,
                    "execution_pointer": "/execution/transforms/0",
                    "validation_pass": False,
                }
            ],
            "unmapped_fields": [
                {"source_field": "row_note", "reason": "Internal reference."}
            ],
            "execution": {
                "kind": "ast",
                "transforms": [
                    {
                        "op": "parse_mixed_au_id"
                        if mode == "bad"
                        else "parse_typed_id",
                        "input": {"op": "field", "name": self.field},
                        "kind": "abn",
                        **({} if mode == "bad" else {"require_label": False}),
                    }
                ],
            },
        }


def provider_for(fixture, actions):
    provider = ScriptedProvider(fixture, actions)
    provider.candidate_configs = {
        mode: fixture.config(mode) for mode in ("bad", "empty", "good")
    }
    return provider


def probe(mode):
    return control("probe", candidate_id=mode, backend="ast")


class IdentifierRepairTests(unittest.TestCase):
    def test_mixed_failure_exposes_only_same_field_supported_direct_repair(self):
        for field, kind in (
            ("Australian Business Number", "abn"),
            ("Australian Company Number", "acn"),
        ):
            with self.subTest(field=field):
                mapping = {
                    "source_field": field,
                    "source_fields": [field],
                    "canonical_field": "entity." + kind,
                    "confidence": 0.9,
                    "semantic_evidence": field,
                    "ast": {
                        "op": "parse_mixed_au_id",
                        "kind": kind,
                        "input": {"op": "field", "name": field},
                    },
                }
                original = copy.deepcopy(mapping)
                with self.assertRaises(Failure) as caught:
                    compile_mappings([mapping], [field])
                options = caught.exception.details["findings"][0][
                    "identifier_evidence"
                ]["repair_options"]
                self.assertEqual(len(options), 1)
                option = options[0]
                self.assertEqual(
                    option["expression"],
                    {
                        "op": "parse_typed_id",
                        "kind": kind,
                        "require_label": False,
                        "input": {"op": "field", "name": field},
                    },
                )
                self.assertTrue(option["semantic_support"]["supported"])
                self.assertEqual(option["semantic_evidence"], field)
                self.assertEqual(mapping, original)

    def test_ambiguous_or_contradicted_types_do_not_get_direct_repair(self):
        for field, docs in (
            ("Reference", ""),
            ("ABN", "ABN: Australian Company Number."),
        ):
            with self.subTest(field=field):
                mapping = {
                    "source_field": field,
                    "source_fields": [field],
                    "canonical_field": "entity.abn",
                    "confidence": 0.9,
                    "semantic_evidence": field,
                    "ast": {
                        "op": "parse_mixed_au_id",
                        "kind": "abn",
                        "input": {"op": "field", "name": field},
                    },
                }
                with self.assertRaises(Failure) as caught:
                    compile_mappings([mapping], [field], docs)
                self.assertFalse(
                    any(
                        f.get("identifier_evidence", {}).get("repair_options")
                        for f in caught.exception.details["findings"]
                    )
                )

    def test_empty_revision_gets_one_repair_reminder_then_normal_probe_and_select(self):
        for field, definition in (
            ("Australian Business Number", ""),
            ("business_key", "business_key: Australian Business Number."),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture = IdentifierFixture(root, field, definition)
                provider = provider_for(
                    fixture,
                    [
                        probe("bad"),
                        control("inspect_field_evidence", field=field),
                        control("inspect_field_evidence", field="row_note"),
                        probe("empty"),
                        control("stop", reason="Lookup quota exhausted."),
                        probe("good"),
                        control("select", candidate_id="good"),
                    ],
                )
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["candidate_status"], "validated")
                self.assertEqual(result["task_status"], "partial")
                self.assertIsNone(result["approval"])
                decisions = [
                    r["payload"]
                    for r in provider.requests
                    if r["purpose"] == "agent_decision"
                ]
                reminder = next(
                    d
                    for d in decisions
                    if (d.get("feedback") or {}).get("error")
                    == "identifier_repair_not_tried"
                )
                self.assertEqual(
                    reminder["tool_availability"]["inspect_field_evidence"][
                        "remaining_new_lookups"
                    ],
                    0,
                )
                self.assertEqual(
                    reminder["identifier_repair_options"][0]["source_candidate_id"],
                    "bad",
                )
                self.assertEqual(
                    reminder["identifier_repair_options"][0]["source_field"], field
                )
                final = json.loads(
                    (
                        Path(fixture.request["state_dir"])
                        / "artifacts/final-config.json"
                    ).read_text()
                )
                self.assertEqual(len(final["field_mappings"]), 1)
                self.assertEqual(
                    final["execution"]["transforms"][0]["op"], "parse_typed_id"
                )
                calls = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), calls)

    def test_reminder_is_bounded_and_never_rewrites_or_selects_a_mapping(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = IdentifierFixture(root)
            provider = provider_for(
                fixture,
                [
                    probe("bad"),
                    probe("empty"),
                    control("stop", reason="Stop first."),
                    control("stop", reason="Still stop."),
                ],
            )
            result = onboard_source(fixture.request, provider)
            self.assertIsNone(result["mapping_config"])
            decisions = [
                r for r in provider.requests if r["purpose"] == "agent_decision"
            ]
            self.assertEqual(len(decisions), 4)
            self.assertEqual(
                sum(
                    (d["payload"].get("feedback") or {}).get("error")
                    == "identifier_repair_not_tried"
                    for d in decisions
                ),
                1,
            )
            checkpoint = json.loads(
                (
                    Path(fixture.request["state_dir"]) / "checkpoint.internal.json"
                ).read_text()
            )
            self.assertEqual(set(checkpoint["agent"]["candidates"]), {"bad", "empty"})

    def test_unsupported_field_can_stop_without_a_repair_reminder(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = IdentifierFixture(root, "Reference")
            provider = provider_for(
                fixture,
                [probe("bad"), control("stop", reason="Type is not documented.")],
            )
            result = onboard_source(fixture.request, provider)
            self.assertIsNone(result["mapping_config"])
            self.assertEqual(
                len([r for r in provider.requests if r["purpose"] == "agent_decision"]),
                2,
            )

    def test_invalid_expression_pointer_remains_repair_feedback(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = IdentifierFixture(root)
            provider = provider_for(
                fixture,
                [probe("bad"), probe("good"), control("select", candidate_id="good")],
            )
            # The normal assembly binds slots itself; inject only this malformed
            # config at its existing generation boundary to exercise recovery.
            from unittest.mock import patch
            from firmable_runtime.agent import generate_candidate

            def malformed_once(runtime, ident, backend, turn, context):
                config = generate_candidate(runtime, ident, backend, turn, context)
                if ident == "bad":
                    config["field_mappings"][0]["execution_pointer"] = (
                        "/execution/transforms/99"
                    )
                return config

            with patch(
                "firmable_runtime.agent.generate_candidate", side_effect=malformed_once
            ):
                result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")

    def test_attempted_direct_parser_with_no_valid_values_does_not_force_another_try(
        self,
    ):
        for explicit_default in (True, False):
            with (
                self.subTest(explicit_default=explicit_default),
                tempfile.TemporaryDirectory() as root,
            ):
                fixture = IdentifierFixture(root)
                with fixture.native.open("w", newline="") as stream:
                    writer = csv.DictWriter(
                        stream, fieldnames=[fixture.field, "row_note"]
                    )
                    writer.writeheader()
                    writer.writerows(
                        {fixture.field: "not-an-identifier", "row_note": str(i)}
                        for i in range(8)
                    )
                provider = provider_for(
                    fixture,
                    [
                        probe("bad"),
                        probe("good"),
                        control("stop", reason="No valid values."),
                    ],
                )
                if not explicit_default:
                    provider.candidate_configs["good"]["execution"]["transforms"][
                        0
                    ].pop("require_label")
                result = onboard_source(fixture.request, provider)
                self.assertIsNone(result["mapping_config"])
                self.assertEqual(
                    len(
                        [
                            r
                            for r in provider.requests
                            if r["purpose"] == "agent_decision"
                        ]
                    ),
                    3,
                )


if __name__ == "__main__":
    unittest.main()
