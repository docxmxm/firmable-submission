"""Direct OpenRouter calls with complete-output validation and safe receipts."""

import copy
import hashlib
import json
import math
import os
import re
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import MAX_MODEL_INPUT_BYTES, Failure, canonical, digest, strict_json
from .openrouter_wire import OpenRouterWireContract, WIRE_PROTOCOL


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenRouterProvider:
    real_model = True

    def __init__(
        self,
        *,
        api_key=None,
        model_id="anthropic/claude-haiku-5.5",
        timeout=600,
        max_output_tokens=16000,
        reasoning_effort="low",
        stream=True,
        receipt_callback=None,
        gate=None,
        provider_allowlist=("Anthropic",),
    ):
        if (
            not isinstance(model_id, str)
            or not model_id.strip()
            or type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or type(max_output_tokens) is not int
            or max_output_tokens <= 0
            or type(stream) is not bool
            or reasoning_effort != "low"
            or not isinstance(provider_allowlist, (list, tuple))
            or not provider_allowlist
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 100
                for item in provider_allowlist
            )
        ):
            error = Failure("validation_error", "Invalid OpenRouter configuration")
            error.definitely_not_sent = True
            raise error
        self.api_key = api_key
        self.model_id = model_id
        self.provider_allowlist = list(provider_allowlist)
        self.model_version = (
            model_id
            + ":reasoning=low:wire="
            + WIRE_PROTOCOL
            + ":providers="
            + digest(self.provider_allowlist)
        )
        self.timeout = timeout
        self.max_tokens = max_output_tokens
        self.stream = stream
        self.receipt_callback = receipt_callback
        self.gate = gate
        self._opener = build_opener(_NoRedirect())

    def _notify(self, receipt):
        if self.receipt_callback is not None:
            self.receipt_callback(copy.deepcopy(receipt))

    @staticmethod
    def _metadata(value, receipt):
        for source, target in (
            ("id", "generation_id"),
            ("model", "model"),
            ("provider", "backend_provider"),
        ):
            item = value.get(source)
            if isinstance(item, str) and len(item) <= 200 and item.isprintable():
                receipt[target] = item
        usage = value.get("usage")
        if not isinstance(usage, dict):
            return
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            amount = usage.get(source)
            if type(amount) is int and amount >= 0:
                receipt["usage"][target] = amount
        cost = usage.get("cost")
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            receipt["cost_usd"] = cost
            receipt["cost_basis"] = "OpenRouter reported cost, not invoice"

    @staticmethod
    def _provider_error(error, api_key):
        try:
            raw = strict_json(error.read(8192))
        except Exception:
            return {}
        details = raw.get("error") if isinstance(raw, dict) else None
        if not isinstance(details, dict):
            return {}

        def safe_fields(source):
            summary = {}
            for name in ("code", "type", "message"):
                value = source.get(name)
                if type(value) is int:
                    summary[name] = value
                elif isinstance(value, str):
                    value = value.replace(api_key, "[REDACTED]")
                    value = re.sub(
                        r"(?i)\bBearer\s+[^\s\"\'<>]+", "Bearer [REDACTED]", value
                    )
                    value = re.sub(
                        r"sk-(?:or-v1|ant)-[A-Za-z0-9_-]+", "[REDACTED]", value
                    )
                    summary[name] = " ".join(value.split())[:500]
            return summary

        summary = safe_fields(details)
        metadata = details.get("metadata")
        upstream = metadata.get("raw") if isinstance(metadata, dict) else None
        if isinstance(upstream, str):
            try:
                upstream = strict_json(upstream.encode("utf-8"))
            except (Failure, ValueError, TypeError):
                upstream = None
        if isinstance(upstream, dict):
            upstream = upstream.get("error", upstream)
            if isinstance(upstream, dict):
                selected = safe_fields(upstream)
                if selected:
                    summary["upstream_error"] = selected
        return summary

    def _stream_content(self, response, receipt, started):
        parts, data = [], []
        completed = False

        def event():
            nonlocal completed
            if not data:
                return
            raw = b"\n".join(data)
            data.clear()
            if raw.strip() == b"[DONE]":
                receipt["response_complete"] = True
                completed = True
                return
            try:
                value = strict_json(raw)
            except (Failure, ValueError, TypeError) as cause:
                raise Failure(
                    "model_outcome_unknown", "Invalid OpenRouter stream event"
                ) from cause
            if not isinstance(value, dict):
                raise Failure(
                    "model_outcome_unknown", "Invalid OpenRouter stream event"
                )
            self._metadata(value, receipt)
            if value.get("error"):
                raise Failure(
                    "model_outcome_unknown", "OpenRouter reported a stream error"
                )
            choices = value.get("choices", [])
            if not isinstance(choices, list):
                raise Failure(
                    "model_outcome_unknown", "Invalid OpenRouter stream choices"
                )
            for choice in choices:
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {})
                content = delta.get("content") if isinstance(delta, dict) else None
                if content is not None and not isinstance(content, str):
                    raise Failure(
                        "model_outcome_unknown", "Invalid OpenRouter content chunk"
                    )
                if content:
                    if receipt["ttft_seconds"] is None:
                        receipt["ttft_seconds"] = time.monotonic() - started
                    parts.append(content)
                reason = choice.get("finish_reason")
                if isinstance(reason, str) and reason:
                    receipt["response_complete"] = True
                    receipt["finish_reason"] = reason

        try:
            for line in response:
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError("OpenRouter response deadline exceeded")
                line = line.rstrip(b"\r\n")
                if not line:
                    event()
                    if completed:
                        break
                elif line.startswith(b"data:"):
                    data.append(line[5:].lstrip(b" "))
            if data:
                event()
        except (OSError, TimeoutError):
            if not receipt["response_complete"]:
                raise
        if not receipt["response_complete"]:
            raise Failure(
                "model_outcome_unknown", "OpenRouter stream ended before completion"
            )
        return "".join(parts)

    def generate(self, request, call_id):
        started = time.monotonic()
        receipt = {
            "call_id": call_id,
            "provider": "openrouter",
            "requested_model": self.model_version,
            "phase": "prepared",
            "request_started": False,
            "outcome": "unknown",
            "response_complete": False,
            "http_status": None,
            "stream": self.stream,
            "ttft_seconds": None,
            "usage": {},
            "cost_usd": None,
            "cost_basis": None,
            "response_format": "json_schema",
            "strict": True,
            "require_parameters": True,
            "provider_allowlist": self.provider_allowlist,
            "wire_protocol": WIRE_PROTOCOL,
        }
        sent = False
        notified = False

        def terminal_receipt(*, suppress=False):
            nonlocal notified
            if not notified:
                notified = True
                try:
                    self._notify(receipt)
                except Exception as cause:
                    receipt["receipt_callback_error"] = type(cause).__name__
                    if not suppress:
                        raise Failure(
                            "model_outcome_unknown",
                            "OpenRouter receipt callback failed; no resend",
                        ) from cause

        try:
            key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
            if not isinstance(key, str) or not key.strip():
                raise Failure(
                    "model_disabled", "Set OPENROUTER_API_KEY for paid inference"
                )
            contract = OpenRouterWireContract(request["output_schema"])
            receipt.update(
                host_schema_sha256=digest(request["output_schema"]),
                wire_schema_sha256=digest(contract.schema),
                wire_schema_complexity=contract.complexity,
            )
            system = request["system"] + "\n" + contract.instructions
            wire = {
                "model": self.model_id,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": canonical(request["payload"]).decode("utf-8"),
                    },
                ],
                "max_tokens": self.max_tokens,
                "reasoning": {"effort": "low"},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "firmable_response",
                        "strict": True,
                        "schema": contract.schema,
                    },
                },
                "provider": {
                    "require_parameters": True,
                    "only": self.provider_allowlist,
                },
                "stream": self.stream,
            }
            if self.stream:
                wire["stream_options"] = {"include_usage": True}
            body = canonical(wire)
            receipt["request_bytes"] = len(body)
            receipt["request_byte_limit"] = MAX_MODEL_INPUT_BYTES
            if len(body) > MAX_MODEL_INPUT_BYTES:
                raise Failure("resource_limit", "Model input exceeds 512 KiB")
            receipt["request_sha256"] = hashlib.sha256(body).hexdigest()
            http_request = Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            if self.gate is not None:
                self.gate()
            receipt["phase"] = "sending"
            receipt["request_started"] = True
            sent = True
            try:
                with self._opener.open(http_request, timeout=self.timeout) as response:
                    receipt["http_status"] = response.status
                    if response.status != 200:
                        raise Failure(
                            "model_outcome_unknown", "Unexpected OpenRouter HTTP status"
                        )
                    receipt["phase"] = "receiving"
                    if self.stream:
                        content = self._stream_content(response, receipt, started)
                    else:
                        raw = response.read()
                        receipt.update(
                            response_complete=True,
                            outcome="success",
                            phase="response_complete",
                        )
                        try:
                            value = strict_json(raw)
                        except (Failure, ValueError, TypeError) as cause:
                            self._invalid(receipt, "envelope_json", cause)
                        if not isinstance(value, dict):
                            self._invalid(
                                receipt,
                                "envelope",
                                Failure("validation_error", "Expected object envelope"),
                            )
                        self._metadata(value, receipt)
                        choices = value.get("choices")
                        if (
                            not isinstance(choices, list)
                            or len(choices) != 1
                            or not isinstance(choices[0], dict)
                        ):
                            self._invalid(
                                receipt,
                                "envelope",
                                Failure(
                                    "validation_error", "Expected one response choice"
                                ),
                            )
                        choice = choices[0]
                        receipt["finish_reason"] = choice.get("finish_reason")
                        message = choice.get("message")
                        content = (
                            message.get("content")
                            if isinstance(message, dict)
                            else None
                        )
                        receipt["ttft_seconds"] = time.monotonic() - started
            except HTTPError as cause:
                receipt["http_status"] = cause.code
                try:
                    summary = self._provider_error(cause, key)
                finally:
                    cause.close()
                if summary:
                    receipt["provider_error"] = summary
                if cause.code in {400, 401, 402, 403, 404, 405, 422, 429}:
                    receipt.update(
                        outcome="rejected",
                        response_complete=True,
                        phase="provider_rejected",
                    )
                    error = Failure(
                        "known_failed",
                        "OpenRouter rejected request",
                        http_status=cause.code,
                    )
                    error.known_provider_rejection = True
                    raise error from cause
                raise Failure(
                    "model_outcome_unknown",
                    "OpenRouter HTTP outcome unavailable; no resend",
                ) from cause
            receipt.update(outcome="success", phase="response_complete")
            if receipt.get("finish_reason") not in (None, "stop"):
                self._invalid(
                    receipt,
                    "completion",
                    Failure("validation_error", "Response did not finish normally"),
                )
            if not isinstance(content, str):
                self._invalid(
                    receipt,
                    "content",
                    Failure("validation_error", "Expected JSON text content"),
                )
            try:
                value = strict_json(content.encode("utf-8"))
            except (Failure, ValueError, TypeError) as cause:
                self._invalid(receipt, "content_json", cause)
            try:
                value = contract.decode(value)
            except (Failure, ValueError, TypeError) as cause:
                self._invalid(receipt, "schema", cause)
            receipt["phase"] = "validated"
            receipt["outer_wall_seconds"] = time.monotonic() - started
            terminal_receipt()
            return {
                "config": value,
                "provider_receipt": receipt,
                "usage": receipt["usage"],
                "cost_usd": receipt["cost_usd"],
                "cost_basis": receipt["cost_basis"],
            }
        except Exception as cause:
            error = (
                cause
                if isinstance(cause, Failure)
                else Failure(
                    "model_outcome_unknown", "OpenRouter outcome unavailable; no resend"
                )
            )
            if not sent:
                error.definitely_not_sent = True
            receipt["outer_wall_seconds"] = time.monotonic() - started
            error.provider_receipt = receipt
            terminal_receipt(suppress=True)
            raise error from (None if error is cause else cause)

    @staticmethod
    def _invalid(receipt, phase, cause):
        # Diagnostics intentionally exclude model text and HTTP bodies.
        details = getattr(cause, "details", {})
        validation = {
            "phase": phase,
            "error_type": getattr(cause, "kind", type(cause).__name__),
            "message": "Complete model response failed " + phase + " validation",
            "path": details.get("path", []),
            "rule": details.get("rule"),
        }
        parse_error = getattr(cause, "__cause__", None)
        if isinstance(parse_error, json.JSONDecodeError):
            validation["message"] = (
                parse_error.msg
                + " at line "
                + str(parse_error.lineno)
                + " column "
                + str(parse_error.colno)
            )
        receipt["failure_kind"] = "known_invalid_response"
        receipt["response_validation"] = validation
        error = Failure("known_failed", validation["message"])
        error.known_invalid_response = True
        error.response_validation = validation
        raise error from cause
