"""One durable call journal and artifact store."""

import copy
import re
from pathlib import Path
from .common import (
    Failure,
    atomic_json,
    byte_hash,
    canonical,
    digest,
    safe_path,
    strict_json,
    utc_now,
)


class DurableRun:
    """Explicit resume; immutable artifacts; journal unknown means no retry."""

    def __init__(self, state_dir, identity, resume=None):
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.root / "checkpoint.internal.json"
        if resume is not None:
            try:
                saved = strict_json(Path(resume).read_bytes())
                expected_checksum = saved.get("state_checksum")
                if expected_checksum != digest(
                    {k: v for k, v in saved.items() if k != "state_checksum"}
                ):
                    raise Failure("checkpoint_failure", "Checkpoint checksum mismatch")
                if saved["identity"] != identity:
                    raise Failure(
                        "checkpoint_mismatch", "Input/runtime identity changed"
                    )
                if saved["state_root"] != str(self.root):
                    raise Failure(
                        "checkpoint_mismatch", "Resume state directory changed"
                    )
                for ref in saved["artifacts"].values():
                    if (
                        byte_hash(safe_path(self.root, ref["path"]).read_bytes())
                        != ref["sha256"]
                    ):
                        raise Failure("checkpoint_failure", "Artifact hash mismatch")
                self.state = saved
            except Failure:
                raise
            except (OSError, KeyError, TypeError) as exc:
                raise Failure(
                    "checkpoint_failure", "Checkpoint missing or damaged"
                ) from exc
        else:
            if self.checkpoint_path.exists():
                raise Failure(
                    "checkpoint_failure", "New run requires an empty state directory"
                )
            self.state = {
                "identity": copy.deepcopy(identity),
                "state_root": str(self.root),
                "last_completed_step": "new",
                "artifacts": {},
                "calls": [],
                "fault_ids": [],
            }
            self.save()

    def save(self):
        self.state["state_checksum"] = digest(
            {k: v for k, v in self.state.items() if k != "state_checksum"}
        )
        atomic_json(self.checkpoint_path, self.state)

    def artifact(self, name, value):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise Failure("checkpoint_failure", "Unsafe artifact name")
        path = "artifacts/" + name + ".json"
        target = self.root / path
        data = canonical(value) + b"\n"
        if target.exists() and target.read_bytes() != data:
            raise Failure(
                "checkpoint_failure",
                "Immutable artifact already exists with different content",
            )
        sha = atomic_json(target, value)
        self.state["artifacts"][name] = {"path": path, "sha256": sha}
        self.save()
        return self.state["artifacts"][name]

    def load_artifact(self, name):
        ref = self.state["artifacts"][name]
        raw = safe_path(self.root, ref["path"]).read_bytes()
        if byte_hash(raw) != ref["sha256"]:
            raise Failure("checkpoint_failure", "Artifact hash mismatch")
        return strict_json(raw)

    def complete(self, step):
        self.state["last_completed_step"] = step
        self.save()

    def prepare_call(self, stage, request):
        call_id = digest(
            {
                "run": str(self.root),
                "stage": stage,
                "request": request,
                "attempt": len(self.state["calls"]),
            }
        )[:24]
        self.artifact("request-" + call_id, request)
        entry = {
            "call_id": call_id,
            "stage": stage,
            "status": "prepared",
            "request_artifact": "request-" + call_id,
            "created_at": utc_now(),
        }
        self.state["calls"].append(entry)
        self.save()
        return call_id

    def call(self, call_id, provider):
        entry = next((c for c in self.state["calls"] if c["call_id"] == call_id), None)
        if entry is None:
            raise Failure("checkpoint_failure", "Unknown call_id")
        receipt_name = "provider-receipt-" + call_id
        if receipt_name in self.state["artifacts"] and not entry.get(
            "provider_receipt_artifact"
        ):
            entry["provider_receipt_artifact"] = receipt_name
            self.save()
        if entry["status"] == "completed":
            return self.load_artifact(entry["response_artifact"])
        # A crash can occur after response bytes/ref are durable but before the
        # completed journal transition. Those bytes are already a known result.
        durable_response = "response-" + call_id
        if durable_response in self.state["artifacts"]:
            response = self.load_artifact(durable_response)
            entry["status"], entry["response_artifact"] = "completed", durable_response
            entry["completed_at"] = utc_now()
            self.save()
            return response
        # Recover a complete failure saved before its journal transition. An
        # incomplete receipt alone never proves that another send is safe.
        failure_name = "model-failure-" + call_id
        if failure_name in self.state["artifacts"]:
            failure = self.load_artifact(failure_name)
            entry.update(failure)
            self.save()
        if entry["status"] == "known_failed":
            raise self._call_failure(entry)
        if entry["status"] in {"sent", "model_outcome_unknown"}:
            entry["status"] = "model_outcome_unknown"
            self.save()
            reconcile = getattr(provider, "reconcile", None)
            if reconcile is None:
                raise Failure(
                    "model_outcome_unknown",
                    "Provider cannot reconcile the same call_id; no resend",
                )
            response = reconcile(call_id)
            if response is None:
                raise Failure(
                    "model_outcome_unknown", "No reconciled result; no resend"
                )
        elif entry["status"] == "prepared":
            entry["status"] = "sent"
            entry["sent_at"] = utc_now()
            self.save()  # The irreversible send always follows durable sent state.
            try:
                response = provider.generate(
                    self.load_artifact(entry["request_artifact"]), call_id
                )
            except Exception as exc:
                receipt = getattr(exc, "provider_receipt", None)
                if isinstance(receipt, dict):
                    name = "provider-receipt-" + call_id
                    self.artifact(name, receipt)
                    entry["provider_receipt_artifact"] = name
                reported = (
                    receipt.get("cli_reported", {}) if isinstance(receipt, dict) else {}
                )
                reported = reported if isinstance(reported, dict) else {}
                cli_complete = (
                    isinstance(receipt, dict)
                    and receipt.get("phase") == "process_returned"
                    and receipt.get("stdout_complete") is True
                    and type(receipt.get("exit_code")) is int
                    and receipt["exit_code"] == 0
                    and reported.get("envelope_type") == "result"
                    and reported.get("envelope_subtype") == "success"
                    and reported.get("is_error") is False
                )
                native_complete = (
                    isinstance(receipt, dict)
                    and receipt.get("provider") == "openrouter"
                    and receipt.get("request_started") is True
                    and receipt.get("http_status") == 200
                    and receipt.get("response_complete") is True
                    and receipt.get("outcome") == "success"
                    and receipt.get("failure_kind") == "known_invalid_response"
                )
                known_invalid = (
                    isinstance(exc, Failure)
                    and getattr(exc, "known_invalid_response", False) is True
                    and (cli_complete or native_complete)
                )
                rejected = (
                    getattr(exc, "known_provider_rejection", False) is True
                    and isinstance(receipt, dict)
                    and receipt.get("outcome") == "rejected"
                    and receipt.get("response_complete") is True
                    and receipt.get("http_status")
                    in {400, 401, 402, 403, 404, 405, 422, 429}
                )
                failure = {
                    "status": (
                        "known_failed"
                        if known_invalid
                        or rejected
                        or getattr(exc, "definitely_not_sent", False)
                        else "model_outcome_unknown"
                    ),
                    "error_type": type(exc).__name__,
                }
                if known_invalid:
                    failure.update(known_invalid_response=True, completed_at=utc_now())
                    diagnostic = getattr(exc, "response_validation", None)
                    if isinstance(diagnostic, dict):
                        failure["response_validation"] = diagnostic
                self.artifact(failure_name, failure)
                entry.update(failure)
                self.save()
                raise self._call_failure(entry) from exc
        else:
            raise Failure(
                "model_call_failed",
                "Known failure requires an explicit bounded new attempt",
            )
        artifact_name = "response-" + call_id
        if isinstance(response, dict) and isinstance(
            response.get("provider_receipt"), dict
        ):
            name = "provider-receipt-" + call_id
            self.artifact(name, response["provider_receipt"])
            entry["provider_receipt_artifact"] = name
        self.artifact(artifact_name, response)
        entry["status"], entry["response_artifact"] = "completed", artifact_name
        entry["completed_at"] = utc_now()
        self.save()
        return response

    @staticmethod
    def _call_failure(entry):
        diagnostic = entry.get("response_validation", {})
        return Failure(
            entry["status"],
            diagnostic.get("message", "Model call failed; see durable journal"),
            call_id=entry["call_id"],
            known_invalid_response=entry.get("known_invalid_response", False),
            response_validation=diagnostic,
        )
