"""Scripted discovery integration, not model quality or real-source coverage."""

import copy
import csv
import tempfile
import unittest

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings, eval_ast
from firmable_runtime.execution import pointer_get
from test_agent import ScriptedProvider, candidate_response
from test_candidate_repair import EnumFixture


class DiscoveryProvider(ScriptedProvider):
    def generate(self, request, call_id):
        if request["purpose"] == "candidate_manifest" and request["payload"].get(
            "discovery_review"
        ):
            self.requests.append(copy.deepcopy(request))
            config = self.fixture.config(bad=True)
            for ast in config["execution"]["transforms"]:
                if ast["op"] == "enum_lookup":
                    ast["values"].pop("Individual/~or sole trader", None)
            self.candidate_configs[request["payload"]["candidate_id"]] = config
            return {
                "config": candidate_response(request, config),
                "usage": {},
                "cost_usd": 0.0,
            }
        return super().generate(request, call_id)


class DependencyEnumFixture(EnumFixture):
    """A coded field is initially used only to qualify an identifier."""

    def __init__(self, root, field="category_81", documented=True):
        super().__init__(root, field)
        self.codes = ("k7", "m4", "p9")
        for index, row in enumerate(self.rows):
            row[field] = self.codes[index % len(self.codes)]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        self.definition = field + ": Legal form of the business."
        meanings = (
            " k7 = Company; m4 = Incorporated Association; p9 = Individual or sole trader."
            if documented
            else " Codes are not defined in this document."
        )
        (self.evidence / "definitions.md").write_text(
            self.definition + meanings + "\n"
            "ABN: Australian Business Number. Use only when " + field + " is not p9.\n"
            "row_note: Internal row reference.\n"
        )
        self.request["required_fields"] = ["entity.abn"]

    def config(self, bad=False):
        config = super().config(bad=bad)
        identifier = config["execution"]["transforms"][0]
        config["execution"]["transforms"][0] = {
            "op": "when",
            "condition": {
                "op": "equals",
                "input": {"op": "field", "name": self.form_field},
                "value": "p9",
            },
            "then": {"op": "omit", "reason": "Identifier does not apply to this code."},
            "else": identifier,
        }
        config["field_mappings"][0]["source_fields"].append(self.form_field)
        config["field_mappings"][0]["transformation"] = (
            "Validate ABN only when " + self.form_field + " is not p9."
        )
        # Reproduce a dependency disappearing from the initial omission list.
        config["unmapped_fields"] = [
            item for item in config["unmapped_fields"]
            if item["source_field"] != self.form_field
        ]
        if bad:
            config["execution"]["transforms"][1]["values"] = {
                "k7": "company", "m4": "incorporated_association"
            }
        return config


class MappingDiscoveryTests(unittest.TestCase):
    def test_unknown_discovery_outcome_is_not_resent(self):
        class InterruptedProvider(DiscoveryProvider):
            def generate(self, request, call_id):
                if request["payload"].get("discovery_review"):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Discovery may have completed remotely")
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = EnumFixture(root)
            provider = InterruptedProvider(fixture, ["good", "select"])
            for _ in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
            self.assertEqual(
                sum(
                    bool(r["payload"].get("discovery_review"))
                    for r in provider.requests
                ),
                1,
            )
            self.assertEqual(provider.actions, ["select"])

    def test_additional_target_discovered_without_required_field_or_header_alias(self):
        for field in ("Legal Form", "category_81"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture = EnumFixture(root, field)
                fixture.request["required_fields"] = ["entity.abn"]
                provider = DiscoveryProvider(fixture, ["good", "select"])
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["candidate_status"], "validated")
                self.assertIn(
                    "entity.entity_type",
                    {
                        m["canonical_field"]
                        for m in result["mapping_config"]["field_mappings"]
                    },
                )
                self.assertTrue(
                    any(
                        row["entity"].get("entity_type") == "company"
                        for row in result["observations"]
                    )
                )
                review = [
                    r for r in provider.requests if r["payload"].get("discovery_review")
                ]
                self.assertEqual(len(review), 1)
                self.assertNotIn(
                    "reason", str(review[0]["payload"]["discovery_review"])
                )
                count = len(provider.requests)
                self.assertEqual(onboard_source(fixture.request, provider), result)
                self.assertEqual(len(provider.requests), count)

    def test_dependency_reconsidered_without_prior_omission_or_required_target(self):
        for field in ("category_81", "segment_42"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture = DependencyEnumFixture(root, field)
                provider = DiscoveryProvider(fixture, ["good", "select"])
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["candidate_status"], "validated")
                review = next(
                    request["payload"]["discovery_review"]
                    for request in provider.requests
                    if request["payload"].get("discovery_review")
                )
                self.assertEqual(review["dependency_only_native_fields"], [field])
                self.assertNotIn(field, review["unmapped_native_fields"])
                self.assertEqual(review["prior_target_groups"], [{
                    "canonical_field": "entity.abn",
                    "source_field": "ABN",
                    "source_fields": ["ABN", field],
                }])
                self.assertNotIn("reason", str(review))
                values = {
                    row["raw_record"][field]: row["entity"].get("entity_type")
                    for row in result["observations"]
                }
                expected = {
                    "k7": "company", "m4": "incorporated_association", "p9": "unknown"
                }
                self.assertTrue(values)
                self.assertEqual(values, {code: expected[code] for code in values})
                selected = result["mapping_config"]
                mapping = next(m for m in selected["field_mappings"]
                               if m["canonical_field"] == "entity.entity_type")
                expression = pointer_get(selected, mapping["execution_pointer"])
                for code, value in expected.items():
                    self.assertEqual(eval_ast(expression, {field: code}), value)
                self.assertIsNone(result["approval"])

    def test_dependency_opportunity_does_not_authorize_undefined_code_conversion(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = DependencyEnumFixture(root, documented=False)
            # A scripted abstention stays an abstention; the host proposes no enum.
            provider = ScriptedProvider(fixture, ["good", "select"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")
            self.assertNotIn(
                "entity.entity_type",
                {m["canonical_field"] for m in result["mapping_config"]["field_mappings"]},
            )
            # Even if a later model proposes the codes, source evidence is required.
            config = fixture.config(bad=True)
            with self.assertRaises(Failure) as raised:
                compile_mappings(
                    [{**m, "ast": pointer_get(config, m["execution_pointer"])}
                     for m in config["field_mappings"]],
                    list(fixture.rows[0]),
                    (fixture.evidence / "definitions.md").read_text(),
                    config["unmapped_fields"],
                )
            self.assertIn("unsupported_conversion", str(raised.exception.details))

    def test_documented_mixed_code_cannot_be_promoted_to_one_legal_form(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = DependencyEnumFixture(root)
            config = fixture.config(bad=True)
            config["execution"]["transforms"][1]["values"]["p9"] = "sole_trader"
            with self.assertRaises(Failure) as raised:
                compile_mappings(
                    [{**m, "ast": pointer_get(config, m["execution_pointer"])}
                     for m in config["field_mappings"]],
                    list(fixture.rows[0]),
                    (fixture.evidence / "definitions.md").read_text(),
                    config["unmapped_fields"],
                )
            self.assertIn("unsupported_conversion", str(raised.exception.details))
            self.assertIn("p9", str(raised.exception.details))

    def test_discovery_cannot_silently_drop_prior_target_groups(self):
        class DroppingProvider(DiscoveryProvider):
            def generate(self, request, call_id):
                value = super().generate(request, call_id)
                if request["payload"].get("discovery_review"):
                    value["config"]["field_mappings"] = [
                        m
                        for m in value["config"]["field_mappings"]
                        if m["canonical_field"] != "entity.abn"
                    ]
                return value

        with tempfile.TemporaryDirectory() as root:
            fixture = EnumFixture(root)
            provider = DroppingProvider(fixture, ["good", "stop"])
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertEqual(
                provider.requests[-1]["payload"]["feedback"]["reason_code"],
                "discovery_review_dropped_targets",
            )
            self.assertFalse(
                any(r["purpose"] == "candidate_rules" for r in provider.requests)
            )
