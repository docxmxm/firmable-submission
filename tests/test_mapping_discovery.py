"""Scripted discovery integration, not model quality or real-source coverage."""

import copy
import csv
import json
import tempfile
import unittest
from unittest.mock import patch

from firmable_runtime.agent import onboard_source
from firmable_runtime.candidate_assembly import (
    _discovery_coverage_regressions,
    generate_candidate,
)
from firmable_runtime.common import Failure
from firmable_runtime.engine import compile_mappings, eval_ast
from firmable_runtime.execution import pointer_get
from firmable_runtime.runtime import Runtime
from test_agent import ScriptedProvider, candidate_response, control
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


class CoverageFixture(EnumFixture):
    """A documented category has no proposal or validation example."""

    def __init__(self, root, field="category_81", target="entity.entity_type"):
        super().__init__(root, field)
        self.target = target
        if target == "entity.status":
            self.entries = {"k7": "active", "m4": "deregistered", "z8": "suspended"}
            meaning = "Status of the company. k7 = Active; m4 = De-registered; z8 = Suspended."
        else:
            self.entries = {
                "k7": "company",
                "m4": "incorporated_association",
                "z8": "company",
            }
            meaning = "Legal form of the business. k7 = Company; m4 = Incorporated Association; z8 = Private company; p9 = Individual or sole trader."
        for i, row in enumerate(self.rows):
            row[field] = ("k7", "m4")[i % 2]
        with self.native.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)
        (self.evidence / "definitions.md").write_text(
            field + ": " + meaning + "\nABN: Australian Business Number.\n"
            "row_note: Internal row reference.\n"
        )
        self.request["required_fields"] = ["entity.abn", target]

    def config(self, bad=False):
        result = super().config(bad=True)
        mapping = result["field_mappings"][1]
        mapping.update(
            canonical_field=self.target,
            semantic_evidence=self.form_field,
            transformation=json.dumps(self.entries, sort_keys=True),
        )
        result["execution"]["transforms"][1].update(values=self.entries, default=None)
        return result


class CoverageProvider(ScriptedProvider):
    """Script model stages from the manifest, rather than deciding source truth."""

    def __init__(self, fixture, revised_entries, *, reverse=False):
        super().__init__(fixture)
        self.revised_entries = revised_entries
        self.reverse = reverse
        self.decisions = 0

    def generate(self, request, call_id):
        purpose, payload = request["purpose"], request["payload"]
        if purpose == "agent_decision":
            self.requests.append(copy.deepcopy(request))
            self.decisions += 1
            if self.decisions == 1:
                value = control("probe", candidate_id="first", backend="ast")
            elif (
                payload.get("feedback", {}).get("reason_code")
                == "discovery_coverage_regression"
            ):
                value = control("probe", candidate_id="repaired", backend="ast")
            else:
                value = control(
                    "select", candidate_id="repaired" if self.decisions > 2 else "first"
                )
            return {"config": value, "usage": {}, "cost_usd": 0}
        if purpose == "candidate_manifest":
            self.requests.append(copy.deepcopy(request))
            value = candidate_response(request, self.fixture.config())
            if payload.get("discovery_review"):
                value["field_mappings"][1]["transformation"] = json.dumps(
                    self.revised_entries, sort_keys=True
                )
                if self.reverse:
                    value["field_mappings"].reverse()
            return {"config": value, "usage": {}, "cost_usd": 0}
        if purpose == "candidate_rules":
            self.requests.append(copy.deepcopy(request))
            expressions = []
            for i, item in enumerate(payload["manifest"]["field_mappings"]):
                ast = (
                    {
                        "op": "parse_typed_id",
                        "kind": "abn",
                        "input": {"op": "field", "name": "ABN"},
                    }
                    if item["canonical_field"] == "entity.abn"
                    else {
                        "op": "enum_lookup",
                        "input": {"op": "field", "name": self.fixture.form_field},
                        "values": json.loads(item["transformation"]),
                        "default": None,
                    }
                )
                expressions.append({"slot": "mapping:" + str(i), "ast": ast})
            return {"config": {"expressions": expressions}, "usage": {}, "cost_usd": 0}
        return super().generate(request, call_id)


class MappingDiscoveryTests(unittest.TestCase):
    def test_discovery_cannot_lose_a_documented_unsampled_category(self):
        for field, target in (
            ("category_81", "entity.entity_type"),
            ("segment_42", "entity.entity_type"),
            ("state_73", "entity.status"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                fixture = CoverageFixture(root, field, target)
                before = copy.deepcopy(fixture.rows)
                reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
                provider = CoverageProvider(fixture, reduced, reverse=True)
                result = onboard_source(fixture.request, provider)
                config = result["mapping_config"]
                item = next(
                    m
                    for m in config["field_mappings"]
                    if m["canonical_field"] == target
                )
                self.assertEqual(
                    eval_ast(
                        pointer_get(config, item["execution_pointer"]), {field: "z8"}
                    ),
                    fixture.entries["z8"],
                )
                feedback = next(
                    r["payload"]["feedback"]
                    for r in provider.requests
                    if r["purpose"] == "agent_decision"
                    and (r["payload"].get("feedback") or {}).get("reason_code")
                    == "discovery_coverage_regression"
                )
                self.assertEqual(feedback["regressions"][0]["source_field"], field)
                self.assertEqual(feedback["regressions"][0]["raw_label"], "z8")
                self.assertEqual(feedback["regressions"][0]["canonical_field"], target)
                self.assertFalse(any(r[field] == "z8" for r in fixture.rows))
                self.assertEqual(fixture.rows, before)
                self.assertEqual(result["candidate_status"], "validated")
                self.assertIsNone(result["approval"])

    def test_discovery_can_remove_a_source_unsupported_old_conversion(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            supported = copy.deepcopy(fixture.entries)
            fixture.entries["p9"] = "sole_trader"
            provider = CoverageProvider(fixture, supported)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(provider.decisions, 2)
            self.assertIsNone(result["approval"])

    def test_discovery_can_expand_and_reorder_existing_targets(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            expanded = copy.deepcopy(fixture.entries)
            fixture.entries.pop("z8")
            provider = CoverageProvider(fixture, expanded, reverse=True)
            result = onboard_source(fixture.request, provider)
            item = next(
                m
                for m in result["mapping_config"]["field_mappings"]
                if m["canonical_field"] == fixture.target
            )
            self.assertEqual(
                eval_ast(
                    pointer_get(result["mapping_config"], item["execution_pointer"]),
                    {fixture.form_field: "z8"},
                ),
                "company",
            )
            self.assertEqual(provider.decisions, 2)

    def test_discovery_receives_original_mapping_details_without_omit_rationale(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            provider = CoverageProvider(fixture, fixture.entries)
            onboard_source(fixture.request, provider)
            review = next(
                r["payload"]["discovery_review"]
                for r in provider.requests
                if r["payload"].get("discovery_review")
            )
            original = next(
                m
                for m in review["prior_mappings"]
                if m["canonical_field"] == fixture.target
            )
            self.assertEqual(json.loads(original["transformation"]), fixture.entries)
            self.assertNotIn("unmapped_fields", review)

    def test_another_candidate_id_cannot_silently_repeat_the_reduction(self):
        class RepeatReduction(CoverageProvider):
            def generate(self, request, call_id):
                if request["purpose"] == "agent_decision" and self.decisions >= 2:
                    self.requests.append(copy.deepcopy(request))
                    return {
                        "config": control(
                            "stop", reason="No further supported repair."
                        ),
                        "usage": {},
                        "cost_usd": 0,
                    }
                response = super().generate(request, call_id)
                if (
                    request["purpose"] == "candidate_manifest"
                    and request["payload"]["candidate_id"] == "repaired"
                ):
                    response["config"]["field_mappings"][1]["transformation"] = (
                        json.dumps(self.revised_entries)
                    )
                return response

        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
            provider = RepeatReduction(fixture, reduced)
            result = onboard_source(fixture.request, provider)
            failures = [
                r["payload"]["feedback"]
                for r in provider.requests
                if r["purpose"] == "agent_decision"
                and (r["payload"].get("feedback") or {}).get("reason_code")
                == "discovery_coverage_regression"
            ]
            self.assertEqual(
                {f["candidate_id"] for f in failures}, {"first", "repaired"}
            )
            self.assertEqual(result["candidate_status"], "unavailable")
            self.assertIsNone(result["approval"])

    def test_unexplained_code_is_not_preserved_just_because_it_was_proposed(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            path = fixture.evidence / "definitions.md"
            path.write_text(path.read_text().replace("z8 = Private company; ", ""))
            reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
            provider = CoverageProvider(fixture, reduced)
            result = onboard_source(fixture.request, provider)
            self.assertEqual(result["candidate_status"], "validated")
            self.assertEqual(provider.decisions, 2)

    def test_equivalent_expression_is_allowed_without_matching_ast_text(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            fixture.entries = {"company": "company"}
            reference = fixture.config()
            reference["source_schema"] = {
                "fields": [{"source_field": f} for f in fixture.rows[0]]
            }
            revised = copy.deepcopy(reference)
            revised["execution"]["transforms"][1] = {
                "op": "trim",
                "input": {"op": "field", "name": fixture.form_field},
            }
            self.assertEqual(
                _discovery_coverage_regressions(
                    reference,
                    revised,
                    (fixture.evidence / "definitions.md").read_text(),
                ),
                ([], 0),
            )

    def test_unknown_reference_generation_is_never_resent(self):
        class Interrupted(CoverageProvider):
            def generate(self, request, call_id):
                if request["payload"].get("discovery_reference"):
                    self.requests.append(copy.deepcopy(request))
                    raise TimeoutError("Reference rules may have completed remotely")
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
            provider = Interrupted(fixture, reduced)
            for _ in range(2):
                with self.assertRaises(Failure) as caught:
                    onboard_source(fixture.request, provider)
                self.assertEqual(caught.exception.kind, "model_outcome_unknown")
            self.assertEqual(
                sum(
                    bool(r["payload"].get("discovery_reference"))
                    for r in provider.requests
                ),
                1,
            )

    def test_unchanged_conditional_is_not_a_coverage_regression(self):
        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            reference = fixture.config()
            reference["source_schema"] = {
                "fields": [{"source_field": f} for f in fixture.rows[0]]
            }
            mapping = reference["field_mappings"][1]
            mapping["source_fields"].append("row_note")
            reference["execution"]["transforms"][1] = {
                "op": "when",
                "condition": {
                    "op": "equals",
                    "input": {"op": "field", "name": "row_note"},
                    "value": "current",
                },
                "then": reference["execution"]["transforms"][1],
                "else": {"op": "omit", "reason": "Outside the current condition."},
            }
            self.assertEqual(
                _discovery_coverage_regressions(
                    reference,
                    copy.deepcopy(reference),
                    (fixture.evidence / "definitions.md").read_text(),
                ),
                ([], 0),
            )

    def test_completed_reference_response_is_reused_after_crash(self):
        for saved_first in (False, True):
            with (
                self.subTest(saved_first=saved_first),
                tempfile.TemporaryDirectory() as root,
            ):
                fixture = CoverageFixture(root)
                reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
                provider = CoverageProvider(fixture, reduced)
                original = Runtime.artifact

                def interrupt(runtime, name, value):
                    if (
                        name == "candidate-discovery-reference-first"
                        and not saved_first
                    ):
                        raise RuntimeError("After completed response, before artifact")
                    ref = original(runtime, name, value)
                    if name == "candidate-discovery-reference-first":
                        raise RuntimeError("After reference artifact")
                    return ref

                with (
                    patch.object(Runtime, "artifact", interrupt),
                    self.assertRaises(RuntimeError),
                ):
                    onboard_source(fixture.request, provider)
                result = onboard_source(fixture.request, provider)
                self.assertEqual(result["candidate_status"], "validated")
                self.assertEqual(
                    sum(
                        bool(r["payload"].get("discovery_reference"))
                        for r in provider.requests
                    ),
                    1,
                )

    def test_empty_revision_cannot_erase_the_discovery_feedback(self):
        class EmptyRevision(CoverageProvider):
            def generate(self, request, call_id):
                if (
                    request["purpose"] == "candidate_manifest"
                    and request["payload"]["candidate_id"] == "empty"
                ):
                    self.requests.append(copy.deepcopy(request))
                    return {
                        "config": {
                            "field_mappings": [],
                            "time_mappings": [],
                            "unmapped_fields": [
                                {"source_field": f, "reason": "Abandoned."}
                                for f in self.fixture.rows[0]
                            ],
                        },
                        "usage": {},
                        "cost_usd": 0,
                    }
                return super().generate(request, call_id)

        with tempfile.TemporaryDirectory() as root:
            fixture = CoverageFixture(root)
            reduced = {k: v for k, v in fixture.entries.items() if k != "z8"}
            provider = EmptyRevision(fixture, reduced)
            onboard_source(fixture.request, provider)
            runtime = Runtime(fixture.request, provider)
            context = runtime.run.load_artifact("candidate-context-repaired")["context"]
            with self.assertRaises(Failure) as caught:
                generate_candidate(runtime, "empty", "ast", 6, context)
            self.assertEqual(
                caught.exception.details["reason_code"], "discovery_coverage_regression"
            )

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
