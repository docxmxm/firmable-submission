"""Bounded discovery on invented schemas; no real-source aliases or gold labels."""

import copy
import tempfile
import unittest

from firmable_runtime.agent import onboard_source
from firmable_runtime.common import Failure
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
                    ast["values"].pop("Individual/~or sole trader")
            self.candidate_configs[request["payload"]["candidate_id"]] = config
            return {
                "config": candidate_response(request, config),
                "usage": {},
                "cost_usd": 0.0,
            }
        return super().generate(request, call_id)


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
