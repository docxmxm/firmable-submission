"""Planner control constraints must survive the real provider wire adapter."""

import copy
import unittest

from jsonschema import Draft202012Validator

from firmable_runtime.agent import decision_schema, validate_decision
from firmable_runtime.common import Failure
from firmable_runtime.openrouter_wire import OpenRouterWireContract


USED = {
    "probe": {"candidate_id", "backend"},
    "select": {"candidate_id"},
    "inspect_field_evidence": {"field"},
    "stop": {"reason"},
}


def action(kind):
    values = {
        "candidate_id": "next_candidate",
        "backend": "ast",
        "field": "Native field",
        "reason": "Original evidence does not establish the required role.",
    }
    return {"action": kind, **{
        key: value if key in USED[kind] else ""
        for key, value in values.items()
    }}


class ControlContractTests(unittest.TestCase):
    def setUp(self):
        self.wire = OpenRouterWireContract(decision_schema())
        self.validator = Draft202012Validator(self.wire.schema)

    def provider_response(self, value):
        # Do not use encode for negative cases: encode validates host constraints
        # and could hide a missing constraint in the actual provider schema.
        return {"result": value} if self.wire.wrapped_root else value

    def assert_provider_rejects(self, value):
        self.assertTrue(list(self.validator.iter_errors(self.provider_response(value))))

    def test_all_control_actions_and_both_backends_roundtrip(self):
        values = [action(kind) for kind in USED]
        llm = action("probe")
        llm["backend"] = "llm_task"
        values.append(llm)
        for value in values:
            with self.subTest(value=value):
                validate_decision(value)
                self.assertFalse(list(self.validator.iter_errors(self.provider_response(value))))
                self.assertEqual(self.wire.decode(self.wire.encode(value)), value)

    def test_unused_control_fields_are_empty_in_provider_schema(self):
        for kind, used in USED.items():
            for key in {"candidate_id", "backend", "field", "reason"} - used:
                with self.subTest(action=kind, field=key):
                    value = action(kind)
                    value[key] = "ast" if key == "backend" else "explanation"
                    self.assert_provider_rejects(value)

    def test_probe_requires_a_supported_nonempty_backend_upstream(self):
        for backend in ("", "python", None):
            with self.subTest(backend=backend):
                value = action("probe")
                value["backend"] = backend
                self.assert_provider_rejects(value)

    def test_closed_control_objects_reject_missing_extra_or_unknown_action(self):
        for kind in USED:
            with self.subTest(action=kind):
                value = action(kind)
                for key in value:
                    missing = copy.deepcopy(value)
                    del missing[key]
                    self.assert_provider_rejects(missing)
                value["candidate_config"] = {}
                self.assert_provider_rejects(value)
        value = action("stop")
        value["action"] = "execute"
        self.assert_provider_rejects(value)

    def test_nonblank_used_values_and_candidate_identity_remain_host_checked(self):
        for kind, used in USED.items():
            for key in used:
                with self.subTest(action=kind, field=key):
                    value = action(kind)
                    value[key] = "   "
                    with self.assertRaises(Failure):
                        validate_decision(value)
        for name in ("../outside", "has space", "a" * 65):
            with self.subTest(candidate_id=name):
                value = action("probe")
                value["candidate_id"] = name
                with self.assertRaises(Failure):
                    validate_decision(value)


if __name__ == "__main__":
    unittest.main()
