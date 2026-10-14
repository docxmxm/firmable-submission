"""OpenRouter transport, complete-output contract and safe failure telemetry."""

import io
import json
import os
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch

from firmable_runtime.common import MAX_MODEL_INPUT_BYTES, Failure
from firmable_runtime.openrouter import OpenRouterProvider
from firmable_runtime.openrouter_wire import OpenRouterWireContract, WIRE_PROTOCOL


REQUEST = {
    "system": "Return the supported name.",
    "payload": {"name": "Aster Pty Ltd"},
    "output_schema": {
        "type": "object",
        "required": ["name"],
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}},
    },
}


class Response(io.BytesIO):
    status = 200


def envelope(content, usage=None):
    return Response(
        json.dumps(
            {
                "id": "gen-mock-1",
                "model": "anthropic/claude-haiku-5.5",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": usage,
            }
        ).encode()
    )


def stream(*events, done=True):
    raw = b": comment\n\n"
    for event in events:
        raw += b"data: " + json.dumps(event).encode() + b"\n\n"
    if done:
        raw += b"data: [DONE]\n\n"
    return Response(raw)


def delta(content, reason=None):
    return {
        "choices": [
            {"index": 0, "delta": {"content": content}, "finish_reason": reason}
        ]
    }


class OpenRouterTests(unittest.TestCase):
    def provider(self, response, **kwargs):
        provider = OpenRouterProvider(api_key="secret-test-key", **kwargs)
        provider._opener.open = Mock(return_value=response)
        return provider

    def test_direct_object_content_and_single_layer_wire_contract(self):
        provider = self.provider(envelope('{"name":"Aster Pty Ltd"}'), stream=False)
        result = provider.generate(REQUEST, "call-1")
        self.assertEqual(result["config"], {"name": "Aster Pty Ltd"})
        sent = provider._opener.open.call_args.args[0]
        wire = json.loads(sent.data)
        self.assertEqual(
            wire["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "firmable_response",
                    "strict": True,
                    "schema": REQUEST["output_schema"],
                },
            },
        )
        self.assertEqual(
            wire["provider"], {"require_parameters": True, "only": ["Anthropic"]}
        )
        self.assertEqual(wire["reasoning"], {"effort": "low"})
        self.assertEqual(wire["max_tokens"], 16000)
        self.assertIn("strict transport schema", wire["messages"][0]["content"])
        self.assertEqual(wire["messages"][1]["content"], '{"name":"Aster Pty Ltd"}')
        self.assertEqual(result["usage"], {})
        self.assertIsNone(result["cost_usd"])
        self.assertNotIn("cli_reported", result["provider_receipt"])
        self.assertTrue(result["provider_receipt"]["request_started"])
        self.assertEqual(result["provider_receipt"]["wire_protocol"], WIRE_PROTOCOL)
        self.assertEqual(result["provider_receipt"]["request_bytes"], len(sent.data))
        self.assertEqual(len(result["provider_receipt"]["wire_schema_sha256"]), 64)
        self.assertEqual(len(result["provider_receipt"]["host_schema_sha256"]), 64)

    def test_unsupported_schema_fails_locally_without_json_mode_fallback(self):
        for schema in (
            {"$ref": "https://example.invalid/external-schema"},
            {"type": "object", "unevaluatedProperties": False},
            {"type": "object", "additionalProperties": {"type": "string"}},
        ):
            with self.subTest(schema=schema):
                provider = self.provider(None)
                with self.assertRaises(Failure) as caught:
                    provider.generate(
                        {**REQUEST, "output_schema": schema}, "unsupported"
                    )
                self.assertEqual(caught.exception.kind, "unsupported_schema")
                self.assertTrue(caught.exception.definitely_not_sent)
                self.assertFalse(caught.exception.provider_receipt["request_started"])
                provider._opener.open.assert_not_called()

    def test_original_evidence_over_128_kib_within_shared_byte_cap_is_sent(self):
        evidence = "源" * 100000
        request = {**REQUEST, "payload": {"source_documentation": evidence}}
        provider = self.provider(envelope('{"name":"Aster Pty Ltd"}'), stream=False)
        result = provider.generate(request, "large-original-evidence")
        provider._opener.open.assert_called_once()
        sent = provider._opener.open.call_args.args[0].data
        self.assertGreater(len(sent), 128 * 1024)
        self.assertLess(len(sent), MAX_MODEL_INPUT_BYTES)
        self.assertEqual(MAX_MODEL_INPUT_BYTES, 512 * 1024)
        wire = json.loads(sent)
        self.assertEqual(json.loads(wire["messages"][1]["content"]), request["payload"])
        self.assertEqual(
            wire["response_format"]["json_schema"]["schema"], REQUEST["output_schema"]
        )
        self.assertEqual(result["config"], {"name": "Aster Pty Ltd"})
        receipt = result["provider_receipt"]
        self.assertEqual(receipt["request_bytes"], len(sent))
        self.assertEqual(receipt["request_byte_limit"], MAX_MODEL_INPUT_BYTES)
        self.assertTrue(receipt["request_started"])

    def test_over_shared_byte_cap_is_definitely_not_sent_with_local_receipt(self):
        callback, gate = Mock(), Mock()
        provider = self.provider(None, receipt_callback=callback, gate=gate)
        request = {
            **REQUEST,
            "payload": {
                "source_documentation": "源" * (MAX_MODEL_INPUT_BYTES // 3 + 1)
            },
        }
        with self.assertRaises(Failure) as caught:
            provider.generate(request, "oversize-original-evidence")
        error = caught.exception
        self.assertEqual(error.kind, "resource_limit")
        self.assertTrue(error.definitely_not_sent)
        provider._opener.open.assert_not_called()
        gate.assert_not_called()
        receipt = error.provider_receipt
        self.assertGreater(receipt["request_bytes"], MAX_MODEL_INPUT_BYTES)
        self.assertEqual(receipt["request_byte_limit"], MAX_MODEL_INPUT_BYTES)
        self.assertEqual(receipt["phase"], "prepared")
        self.assertFalse(receipt["request_started"])
        self.assertFalse(receipt["response_complete"])
        self.assertIsNone(receipt["http_status"])
        self.assertIsNone(receipt["cost_usd"])
        callback.assert_called_once_with(receipt)

    def test_provider_selection_is_explicit_and_changes_durable_identity(self):
        provider = self.provider(
            envelope('{"name":"Aster"}'), stream=False, provider_allowlist=["Google"]
        )
        provider.generate(REQUEST, "provider-selection")
        wire = json.loads(provider._opener.open.call_args.args[0].data)
        self.assertEqual(
            wire["provider"], {"require_parameters": True, "only": ["Google"]}
        )
        self.assertNotEqual(provider.model_version, OpenRouterProvider().model_version)

    def test_strict_ast_transport_decodes_before_original_contract_validation(self):
        from firmable_runtime.schema import ast_rules_schema
        from test_ast_schema import current_name_expression

        host = {
            "expressions": [{"slot": "mapping:0", "ast": current_name_expression()}],
        }
        request = {**REQUEST, "output_schema": ast_rules_schema(1)}
        encoded = OpenRouterWireContract(request["output_schema"]).encode(host)
        provider = self.provider(envelope(json.dumps(encoded)), stream=False)
        self.assertEqual(provider.generate(request, "flat-ast")["config"], host)
        wire = json.loads(provider._opener.open.call_args.args[0].data)
        self.assertIn("TRANSPORT OVERRIDE", wire["messages"][0]["content"])
        self.assertIn("EARLIER nodes", wire["messages"][0]["content"])
        arguments = encoded["expressions"][0]["ast"]["nodes"][-1]["arguments"]
        next(item for item in arguments if item["key"] == "then")["value"][
            "number_value"
        ] = 999
        provider = self.provider(envelope(json.dumps(encoded)), stream=False)
        with self.assertRaises(Failure) as caught:
            provider.generate(request, "flat-invalid")
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(caught.exception.response_validation["rule"], "ast_reference")

    def test_host_constraints_still_reject_wire_valid_output(self):
        schema = {
            **REQUEST["output_schema"],
            "properties": {"name": {"type": "string", "minLength": 3}},
        }
        provider = self.provider(envelope('{"name":"X"}'), stream=False)
        with self.assertRaises(Failure) as caught:
            provider.generate({**REQUEST, "output_schema": schema}, "host-constraint")
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(caught.exception.response_validation["rule"], "minLength")

    def test_stream_combines_chunks_before_json_validation_and_collects_usage(self):
        callback, gate = Mock(), Mock()
        provider = self.provider(
            stream(
                delta('{"name":"'),
                delta("Aster Pty "),
                delta('Ltd"}', "stop"),
                {
                    "id": "gen-stream-1",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 9,
                        "total_tokens": 29,
                        "cost": 0.001,
                        "prompt_tokens_details": {"cached_tokens": 4},
                        "completion_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            ),
            receipt_callback=callback,
            gate=gate,
        )
        result = provider.generate(REQUEST, "call-stream")
        self.assertEqual(result["config"]["name"], "Aster Pty Ltd")
        self.assertEqual(result["usage"], {"input_tokens": 20, "output_tokens": 9})
        self.assertEqual(result["cost_usd"], 0.001)
        self.assertIsNotNone(result["provider_receipt"]["ttft_seconds"])
        self.assertGreaterEqual(
            result["provider_receipt"]["outer_wall_seconds"],
            result["provider_receipt"]["ttft_seconds"],
        )
        self.assertTrue(result["provider_receipt"]["response_complete"])
        callback.assert_called_once()
        gate.assert_called_once_with()
        wire = json.loads(provider._opener.open.call_args.args[0].data)
        self.assertEqual(wire["stream_options"], {"include_usage": True})

    def test_extra_json_is_known_invalid_and_preserves_safe_diagnostic(self):
        callback = Mock()
        provider = self.provider(
            envelope(
                '{"name":"Aster"}{"other":"body-secret"}',
                {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.002},
            ),
            stream=False,
            receipt_callback=callback,
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-invalid")
        error = caught.exception
        self.assertEqual(error.kind, "known_failed")
        self.assertTrue(error.known_invalid_response)
        self.assertEqual(error.response_validation["phase"], "content_json")
        self.assertIn("Extra data", error.response_validation["message"])
        self.assertEqual(error.provider_receipt["outcome"], "success")
        self.assertTrue(error.provider_receipt["response_complete"])
        self.assertEqual(error.provider_receipt["cost_usd"], 0.002)
        self.assertNotIn("body-secret", json.dumps(error.provider_receipt))
        callback.assert_called_once()
        provider._opener.open.assert_called_once()

    def test_schema_failure_has_path_and_rule_and_no_model_body(self):
        provider = self.provider(stream(delta('{"name":123}', "stop")))
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-schema")
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(caught.exception.response_validation["path"], ["name"])
        self.assertEqual(caught.exception.response_validation["rule"], "type")
        provider._opener.open.assert_called_once()

    def test_code_fence_is_rejected_without_guessing(self):
        provider = self.provider(
            envelope('```json\n{"name":"Aster"}\n```'), stream=False
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-fence")
        self.assertTrue(caught.exception.known_invalid_response)

    def test_partial_stream_is_unknown_and_not_resent(self):
        for response in (stream(delta('{"name":"Aster'), done=False),):
            callback = Mock()
            provider = self.provider(response, receipt_callback=callback)
            with self.assertRaises(Failure) as caught:
                provider.generate(REQUEST, "call-partial")
            self.assertEqual(caught.exception.kind, "model_outcome_unknown")
            self.assertFalse(caught.exception.provider_receipt["response_complete"])
            self.assertFalse(getattr(caught.exception, "known_invalid_response", False))
            callback.assert_called_once()
            provider._opener.open.assert_called_once()

    def test_interrupted_stream_without_finish_reason_is_unknown(self):
        class Interrupted(Response):
            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"content":"{"}}]}\n'
                yield b"\n"
                raise TimeoutError("stream interrupted")

        provider = self.provider(Interrupted())
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-timeout")
        self.assertEqual(caught.exception.kind, "model_outcome_unknown")
        self.assertFalse(caught.exception.provider_receipt["response_complete"])
        provider._opener.open.assert_called_once()

    def test_malformed_stream_event_has_unknown_outcome(self):
        provider = self.provider(Response(b"data: {broken\n\n"))
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-bad-event")
        self.assertEqual(caught.exception.kind, "model_outcome_unknown")
        self.assertFalse(caught.exception.provider_receipt["response_complete"])

    def test_explicit_finish_reason_can_complete_stream_without_done(self):
        provider = self.provider(stream(delta('{"name":"Aster"}', "stop"), done=False))
        self.assertEqual(
            provider.generate(REQUEST, "call-finish")["config"], {"name": "Aster"}
        )

    def test_http_rejections_are_known_and_not_retried_or_redirected(self):
        for status in (400, 401, 402, 429):
            with self.subTest(status=status):
                callback = Mock()
                provider = self.provider(None, receipt_callback=callback)
                provider._opener.open.side_effect = HTTPError(
                    "https://openrouter.ai",
                    status,
                    "private-message",
                    {},
                    io.BytesIO(b"private-body secret-test-key"),
                )
                with self.assertRaises(Failure) as caught:
                    provider.generate(REQUEST, "call-reject")
                self.assertTrue(caught.exception.known_provider_rejection)
                self.assertEqual(
                    caught.exception.provider_receipt["http_status"], status
                )
                self.assertNotIn(
                    "secret-test-key", json.dumps(caught.exception.provider_receipt)
                )
                callback.assert_called_once()
                provider._opener.open.assert_called_once()
        provider = self.provider(None)
        provider._opener.open.side_effect = HTTPError(
            "https://openrouter.ai", 307, "redirect", {}, None
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-redirect")
        self.assertEqual(caught.exception.kind, "model_outcome_unknown")
        redirect = next(
            handler
            for handler in provider._opener.handlers
            if type(handler).__name__ == "_NoRedirect"
        )
        self.assertIsNone(
            redirect.redirect_request(
                None, None, 307, "redirect", {}, "https://other.invalid"
            )
        )

    def test_http_400_preserves_safe_error_summary(self):
        provider = self.provider(None)
        body = json.dumps(
            {
                "error": {
                    "code": 400,
                    "message": "Unsupported schema\nsecret-test-key Bearer other-private-key sk-or-v1-abcdef "
                    + "x" * 600,
                },
                "private": "omitted-body",
            }
        ).encode()
        provider._opener.open.side_effect = HTTPError(
            "https://openrouter.ai", 400, "error", {}, io.BytesIO(body)
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-reject-summary")
        summary = caught.exception.provider_receipt["provider_error"]
        self.assertEqual(summary["code"], 400)
        self.assertIn("Unsupported schema", summary["message"])
        self.assertLessEqual(len(summary["message"]), 500)
        for private in (
            "secret-test-key",
            "other-private-key",
            "sk-or-v1-abcdef",
            "omitted-body",
        ):
            self.assertNotIn(private, json.dumps(caught.exception.provider_receipt))
        self.assertTrue(caught.exception.known_provider_rejection)

    def test_upstream_schema_error_selects_only_bounded_redacted_fields(self):
        raw = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_schema",
                "message": "Schema rejected\nsecret-test-key Bearer other-private-key sk-ant-api03-private "
                + "x" * 600,
                "input": "private-upstream-input",
            },
            "request_id": "private-upstream-id",
        }
        for upstream in (raw, json.dumps(raw)):
            with self.subTest(encoded=isinstance(upstream, str)):
                provider = self.provider(None)
                body = json.dumps(
                    {
                        "error": {
                            "code": 400,
                            "message": "Provider returned error",
                            "metadata": {"raw": upstream},
                        }
                    }
                ).encode()
                provider._opener.open.side_effect = HTTPError(
                    "https://openrouter.ai", 400, "error", {}, io.BytesIO(body)
                )
                with self.assertRaises(Failure) as caught:
                    provider.generate(REQUEST, "upstream-error")
                receipt = caught.exception.provider_receipt
                summary = receipt["provider_error"]["upstream_error"]
                self.assertEqual(set(summary), {"type", "code", "message"})
                self.assertEqual(summary["type"], "invalid_request_error")
                self.assertIn("Schema rejected", summary["message"])
                self.assertLessEqual(len(summary["message"]), 500)
                for private in (
                    "secret-test-key",
                    "other-private-key",
                    "sk-ant-api03-private",
                    "private-upstream-input",
                    "private-upstream-id",
                ):
                    self.assertNotIn(private, json.dumps(receipt))
                self.assertTrue(caught.exception.known_provider_rejection)
                self.assertEqual(provider._opener.open.call_count, 1)

    def test_malformed_upstream_error_never_leaks_raw_envelope(self):
        for raw in (
            "not JSON private-envelope",
            ["private-envelope"],
            {"error": "private-envelope"},
        ):
            body = json.dumps(
                {"error": {"code": 400, "metadata": {"raw": raw}}}
            ).encode()
            error = HTTPError(
                "https://openrouter.ai", 400, "error", {}, io.BytesIO(body)
            )
            self.assertEqual(
                OpenRouterProvider._provider_error(error, "private-key"), {"code": 400}
            )

    def test_raising_callback_is_terminal_once_and_retains_receipt(self):
        callback = Mock(side_effect=RuntimeError("private callback detail"))
        provider = self.provider(
            envelope('{"name":"Aster"}'), stream=False, receipt_callback=callback
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-callback-error")
        callback.assert_called_once()
        provider._opener.open.assert_called_once()
        self.assertEqual(caught.exception.kind, "model_outcome_unknown")
        self.assertTrue(caught.exception.provider_receipt["response_complete"])
        self.assertEqual(
            caught.exception.provider_receipt["receipt_callback_error"], "RuntimeError"
        )
        self.assertNotIn(
            "private callback detail", json.dumps(caught.exception.provider_receipt)
        )

    def test_raising_callback_does_not_replace_original_validation_failure(self):
        callback = Mock(side_effect=RuntimeError("callback failed"))
        provider = self.provider(
            envelope('{"name":123}'), stream=False, receipt_callback=callback
        )
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-invalid-callback")
        callback.assert_called_once()
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(
            caught.exception.provider_receipt["receipt_callback_error"], "RuntimeError"
        )

    def test_ambiguous_http_error_never_retries(self):
        provider = self.provider(None)
        provider._opener.open.side_effect = URLError("private credential")
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-unavailable")
        self.assertEqual(caught.exception.kind, "model_outcome_unknown")
        self.assertNotIn("private credential", str(caught.exception))
        provider._opener.open.assert_called_once()

    def test_missing_key_is_definitely_not_sent_and_environment_key_is_private(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenRouterProvider()
            provider._opener.open = Mock()
            with self.assertRaises(Failure) as caught:
                provider.generate(REQUEST, "call-disabled")
            self.assertTrue(caught.exception.definitely_not_sent)
            provider._opener.open.assert_not_called()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-secret"}):
            provider = OpenRouterProvider(stream=False)
            provider._opener.open = Mock(return_value=envelope('{"name":"Aster"}'))
            result = provider.generate(REQUEST, "call-env")
            self.assertNotIn("env-secret", json.dumps(result))

    def test_malformed_complete_envelope_is_known_invalid(self):
        provider = self.provider(Response(b'{"broken"'), stream=False)
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-bad-envelope")
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(caught.exception.provider_receipt["outcome"], "success")
        self.assertEqual(caught.exception.response_validation["phase"], "envelope_json")

    def test_finish_length_is_known_invalid_even_if_json_parses(self):
        provider = self.provider(stream(delta('{"name":"Aster"}', "length")))
        with self.assertRaises(Failure) as caught:
            provider.generate(REQUEST, "call-length")
        self.assertTrue(caught.exception.known_invalid_response)
        self.assertEqual(caught.exception.response_validation["phase"], "completion")


if __name__ == "__main__":
    unittest.main()
