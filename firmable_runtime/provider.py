"""Explicit ccvibe CLI adapter; credentials and raw CLI envelopes stay private."""

import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from .common import MAX_MODEL_INPUT_BYTES, Failure, canonical, strict_json
from .schema import check_response


class CcVibeProvider:
    model_id = "claude-fable-5-1"
    model_version = "claude-fable-5-1:effort=max"
    real_model = True

    def __init__(self, *, timeout=600, max_budget_usd=None):
        if max_budget_usd is not None and (
            type(max_budget_usd) not in (int, float)
            or not math.isfinite(max_budget_usd)
            or max_budget_usd <= 0
        ):
            raise Failure(
                "validation_error", "Per-call budget must be a positive finite number"
            )
        self.timeout = timeout
        self.max_budget_usd = max_budget_usd
        self.scratch_dir = None

    def _environment(self):
        if os.environ.get("FIRMABLE_MVP_API") != "1":
            raise Failure("model_disabled", "Set FIRMABLE_MVP_API=1 for paid inference")
        profile = Path.home() / ".claude/mvp-api/settings.json"
        if not profile.is_file() or stat.S_IMODE(profile.stat().st_mode) != 0o600:
            raise Failure(
                "model_disabled", "Dedicated API profile must exist with mode 0600"
            )
        settings = strict_json(profile.read_bytes())
        configured = settings.get("env") if isinstance(settings, dict) else None
        if not isinstance(configured, dict) or not isinstance(
            configured.get("ANTHROPIC_BASE_URL"), str
        ):
            raise Failure(
                "model_disabled",
                "Dedicated profile must contain an env object and ccvibe URL",
            )
        url = urlsplit(configured["ANTHROPIC_BASE_URL"])
        if (
            url.scheme != "https"
            or not url.hostname
            or "ccvibe" not in url.hostname.lower().replace("-", "")
            or not any(
                configured.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
            )
        ):
            raise Failure(
                "model_disabled",
                "Dedicated profile must specify ccvibe and a credential",
            )
        env = {
            k: os.environ[k]
            for k in ("HOME", "PATH", "LANG", "LC_ALL")
            if k in os.environ
        }
        env.update(
            {k: str(v) for k, v in configured.items() if k.startswith("ANTHROPIC_")}
        )
        env.update(
            CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
            MAX_STRUCTURED_OUTPUT_RETRIES="2",
        )
        return env, url.hostname

    def generate(self, request, call_id):
        try:
            env, hostname = self._environment()
        except (Failure, OSError, ValueError) as cause:
            error = Failure(
                "model_disabled", "Explicit ccvibe configuration is unavailable"
            )
            error.definitely_not_sent = True
            raise error from cause
        wire_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["response_json"],
            "properties": {"response_json": {"type": "string"}},
        }
        system = (
            request["system"]
            + "\nReturn the complete response as JSON text inside response_json. INNER SCHEMA:\n"
            + json.dumps(request["output_schema"])
        )
        stdin = canonical(request["payload"])
        if (
            len(stdin) + len(system.encode()) + len(canonical(wire_schema))
            > MAX_MODEL_INPUT_BYTES
        ):
            error = Failure("resource_limit", "Model input exceeds 512 KiB")
            error.definitely_not_sent = True
            raise error
        command = [
            str(Path.home() / ".local/bin/claude"),
            "--safe-mode",
            "--print",
            "--model",
            self.model_id,
            "--effort",
            "max",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--disable-slash-commands",
            "--no-session-persistence",
            "--no-chrome",
            "--output-format",
            "json",
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(wire_schema),
        ]
        if self.max_budget_usd is not None:
            command += ["--max-budget-usd", str(self.max_budget_usd)]
        start = time.monotonic()
        receipt = {
            "call_id": call_id,
            "requested_model": self.model_version,
            "backend_identity": "unverified",
            "route_hostname": hostname,
            "process_started": False,
            "phase": "launch",
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
        }
        try:
            with tempfile.TemporaryDirectory(
                prefix="firmable-", dir=self.scratch_dir
            ) as directory:
                env.update(
                    CLAUDE_CONFIG_DIR=str(Path(directory) / "config"), TMPDIR=directory
                )
                try:
                    result = subprocess.run(
                        command,
                        input=stdin,
                        capture_output=True,
                        cwd=directory,
                        env=env,
                        timeout=self.timeout,
                    )
                except (FileNotFoundError, PermissionError) as cause:
                    error = Failure(
                        "model_disabled", "Claude CLI could not be launched"
                    )
                    error.definitely_not_sent = True
                    raise error from cause
                except subprocess.TimeoutExpired:
                    receipt["process_started"] = True
                    raise
                receipt["process_started"] = True
            receipt.update(
                phase="process_returned",
                exit_code=result.returncode,
                stdout_complete=True,
                stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
            )
            payload = strict_json(result.stdout)
            if not isinstance(payload, dict):
                raise Failure(
                    "model_outcome_unknown", "CLI returned a non-object envelope"
                )
            reported = {
                "envelope_type": payload.get("type"),
                "envelope_subtype": payload.get("subtype"),
                "is_error": payload.get("is_error"),
            }
            for name in ("total_cost_usd", "duration_ms", "num_turns"):
                value = payload.get(name)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    reported[name] = value
            usage = payload.get("usage")
            reported["usage"] = {
                k: v
                for k, v in (usage.items() if isinstance(usage, dict) else ())
                if k
                in {
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                }
                and type(v) is int
                and v >= 0
            }
            receipt["cli_reported"] = reported
            if (
                result.returncode
                or payload.get("type") != "result"
                or payload.get("subtype") != "success"
                or payload.get("is_error") is not False
            ):
                raise Failure(
                    "model_outcome_unknown", "CLI did not return explicit success"
                )
            try:
                value = check_response(
                    payload.get("structured_output"),
                    request["output_schema"],
                    "wrapped",
                )
            except (Failure, ValueError, TypeError) as cause:
                error = Failure(
                    "known_failed",
                    "Successful CLI output failed local schema validation",
                )
                error.known_invalid_response = True
                raise error from cause
            receipt["outer_wall_seconds"] = time.monotonic() - start
            return {
                "config": value,
                "provider_receipt": receipt,
                "usage": reported["usage"],
                "cost_usd": reported.get("total_cost_usd"),
                "cost_basis": "CLI estimate, not invoice",
            }
        except Exception as cause:
            error = (
                cause
                if isinstance(cause, Failure)
                else Failure(
                    "model_outcome_unknown", "CLI outcome unavailable; no resend"
                )
            )
            receipt["outer_wall_seconds"] = time.monotonic() - start
            error.provider_receipt = receipt
            raise error from (None if error is cause else cause)
