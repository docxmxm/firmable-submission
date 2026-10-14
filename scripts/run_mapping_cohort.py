"""Bounded native-source cohort evaluation; separate frozen evidence and run output."""

import argparse

import copy
import getpass
import json
import math
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = None
REQUESTS = None
sys.path.insert(0, str(PROJECT))

from firmable_runtime.common import Failure, atomic_json, byte_hash, strict_json, utc_now
from firmable_runtime.engine import ENGINE_VERSION
from firmable_runtime.native import input_manifest
from firmable_runtime.openrouter import OpenRouterProvider
from firmable_runtime.runtime import runtime_fingerprint
from firmable_runtime.workflow import (
    approve_source,
    extract_source,
    infer_source,
    inspect_source,
    review_source,
)

LIMITS = {
    "global_calls": 500,
    "per_source_calls": 80,
    "new_send_seconds": 1500,
    "observed_cost_guard_usd": 5.0,
    "per_inflight_reservation_usd": 0.25,
}
PROVIDER = {
    "model_id": "anthropic/claude-haiku-5.5",
    "reasoning_effort": "low",
    "stream": False,
    "timeout": 180,
    "max_output_tokens": 16000,
    "provider_allowlist": ["Anthropic"],
}


def git(*arguments):
    return subprocess.run(
        ["git", *arguments], cwd=PROJECT, check=True, capture_output=True
    ).stdout


def freeze_inputs(requests):
    files = {
        str(path.resolve()): byte_hash(path.read_bytes())
        for path in [
            *sorted((PROJECT / "firmable_runtime").glob("*.py")),
            PROJECT / "pyproject.toml",
            PROJECT / "uv.lock",
            Path(__file__),
            REQUESTS,
            *(Path(request["ontology_path"]) for request in requests),
        ]
    }
    evidence = {}
    for request in requests:
        root = Path(request["evidence_root"])
        evidence[str(root)] = input_manifest(root)
        for item in evidence[str(root)]:
            files[str(root / item["path"])] = item["sha256"]
    return {
        "runtime_fingerprint": runtime_fingerprint(),
        "files_sha256": files,
        "evidence_manifests": evidence,
        "git": {
            "head": git("rev-parse", "HEAD").decode().strip(),
            "status": git("status", "--short").decode(),
            "unstaged_diff_sha256": byte_hash(git("diff", "--binary")),
            "staged_diff_sha256": byte_hash(git("diff", "--cached", "--binary")),
        },
    }


def integrity(frozen):
    changes = []
    for name, expected in frozen["files_sha256"].items():
        try:
            actual = byte_hash(Path(name).read_bytes())
        except OSError:
            actual = None
        if actual != expected:
            changes.append({"path": name, "kind": "file_changed_or_missing"})
    for name, expected in frozen["evidence_manifests"].items():
        try:
            actual = input_manifest(name)
        except (OSError, Failure):
            actual = None
        if actual != expected:
            changes.append({"path": name, "kind": "evidence_inventory_changed"})
    if runtime_fingerprint() != frozen["runtime_fingerprint"]:
        changes.append({"kind": "runtime_changed"})
    return {"unchanged": not changes, "changes": changes}


def ast_only(config):
    """Recognize only the existing deterministic AST contract, including legacy kind."""
    execution = config.get("execution", {})
    mappings = config.get("field_mappings", [])
    return (
        isinstance(execution, dict)
        and execution.get("kind", "ast") == "ast"
        and isinstance(execution.get("transforms"), list)
        and "task" not in execution
        and bool(mappings)
        and all(
            re.fullmatch(r"/execution/transforms/[0-9]+", item.get("execution_pointer", ""))
            for item in mappings
        )
    )


def main():
    global ROOT, REQUESTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=500)
    parser.add_argument("--max-seconds", type=int, default=1500)
    parser.add_argument("--cost-guard", type=float, default=5.0)
    args = parser.parse_args()
    ROOT, REQUESTS = args.run_dir.resolve(), args.requests.resolve()
    LIMITS.update(global_calls=args.max_calls, new_send_seconds=args.max_seconds,
                  observed_cost_guard_usd=args.cost_guard)
    if (ROOT / "protocol.json").exists() or (ROOT / "execution").exists():
        raise SystemExit("Existing protocol/execution found; this runner never resumes or resends.")
    supplied = strict_json(REQUESTS.read_bytes())
    if not isinstance(supplied, list) or not 1 <= len(supplied) <= 12:
        raise SystemExit("requests.json must contain one to twelve inference request objects.")
    requests, ids = [], set()
    for value in supplied:
        if not isinstance(value, dict):
            raise SystemExit("Each request must be an object.")
        request = copy.deepcopy(value)
        source = request.get("source_id")
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}", source) or source in ids:
            raise SystemExit("Expected unique safe source IDs.")
        ids.add(source)
        request["state_dir"] = str(ROOT / "execution" / source)
        for name in ("evidence_root", "ontology_path"):
            request[name] = str(Path(request[name]).resolve())
        request.setdefault("execution_scope", "onboarding_development")
        request.setdefault("required_fields", [])
        request.setdefault("dev_record_limit", 32)
        request.setdefault("proposal_record_limit", 8)
        if request["execution_scope"] != "onboarding_development":
            raise SystemExit("Only onboarding_development is supported.")
        evidence = Path(request["evidence_root"])
        if ROOT.is_relative_to(evidence) or evidence.is_relative_to(ROOT / "execution"):
            raise SystemExit("Evidence and execution output must be separate.")
        requests.append(request)

    frozen = freeze_inputs(requests)
    inspections = {}
    for request in requests:
        source = request["source_id"]
        inspections[source] = inspect_source(request["evidence_root"], source)
    checked = integrity(frozen)
    if not checked["unchanged"]:
        raise SystemExit("Inputs changed during offline preflight; no key requested and no calls sent.")
    for source, inspection in inspections.items():
        atomic_json(ROOT / "inspection" / (source + ".json"), inspection)
    protocol = {
        "created_at": utc_now(),
        "engine_version": ENGINE_VERSION,
        "runtime_fingerprint": frozen["runtime_fingerprint"],
        "code_identity": frozen,
        "provider": PROVIDER,
        "limits": LIMITS,
        "requests": requests,
        "worker_concurrency": 2,
        "repetitions_per_source": 1,
        "scope": "One frozen cohort trajectory; source role is declared in the experiment protocol. No human-gold or statistical generalization claim.",
        "synthetic_approval_only": True,
        "full_extraction_policy": "Only validated AST configs; all other backends retain bounded development results without full extraction.",
        "cost_guard_limitation": "Observed provider costs plus $0.25 per inflight reservation; not a provider-enforced billing ceiling, and a call can exceed its reservation.",
        "unknown_outcome_policy": "Stop all new sends on unknown outcome or missing sent-call cost; never resume or resend.",
        "integrity_policy": "Recheck code, dependency locks, runner, requests, ontology and complete original evidence inventories before every send and at completion.",
        "human_gold": False,
    }
    # The frozen protocol exists before a credential is requested or any API send.
    atomic_json(ROOT / "protocol.json", protocol)
    key = getpass.getpass("OpenRouter API key (memory only; not saved): ").strip()
    if not key:
        raise SystemExit("Missing key; frozen preparation retained and no calls sent.")

    started, lock = time.monotonic(), threading.RLock()
    receipts, reservations, calls, results = [], set(), {}, []
    stop_reason = None

    def clean(value):
        if isinstance(value, str):
            return value.replace(key, "[REDACTED]")
        if isinstance(value, dict):
            return {clean(k): clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    def save(path, value):
        atomic_json(path, clean(value))

    def totals(selected):
        sent = [item for item in selected if item.get("request_started")]
        return {
            "model_calls": len(sent),
            "reported_cost_usd": sum(item["cost_usd"] for item in sent if isinstance(item.get("cost_usd"), (int, float))),
            "cost_complete": all(isinstance(item.get("cost_usd"), (int, float)) for item in sent),
            "input_tokens": sum(item.get("usage", {}).get("input_tokens", 0) for item in sent),
            "output_tokens": sum(item.get("usage", {}).get("output_tokens", 0) for item in sent),
        }

    def persist_summary(final=False):
        save(ROOT / "summary.json", {
            "status": "finished" if final else "running",
            "updated_at": utc_now(),
            "wall_seconds": time.monotonic() - started,
            "results": results,
            "stop_reason": stop_reason,
            "gate_admitted_calls": sum(calls.values()),
            "inflight_sources": sorted(reservations),
            **totals(receipts),
            "per_source": {
                source: totals([r for r in receipts if r["source_id"] == source])
                for source in sorted(ids)
            },
            "integrity_end": integrity(frozen) if final else None,
            "human_approved_configs": 0,
            "human_link_reviews": 0,
            "cost_basis": "OpenRouter reported receipts, not invoice",
            "limitation": "Execution and emitted-field coverage do not establish semantic accuracy; synthetic approvals are evaluation only.",
        })

    def gate(source):
        nonlocal stop_reason
        with lock:
            if not integrity(frozen)["unchanged"]:
                stop_reason = stop_reason or "frozen_input_or_code_drift"
            if time.monotonic() - started >= LIMITS["new_send_seconds"]:
                stop_reason = stop_reason or "new_send_time_limit"
            if sum(calls.values()) >= LIMITS["global_calls"]:
                stop_reason = stop_reason or "global_call_limit"
            reported = totals(receipts)["reported_cost_usd"]
            if reported + LIMITS["per_inflight_reservation_usd"] * (len(reservations) + 1) > LIMITS["observed_cost_guard_usd"]:
                stop_reason = stop_reason or "observed_cost_guard"
            reason = stop_reason
            if calls.get(source, 0) >= LIMITS["per_source_calls"]:
                reason = reason or "source_call_limit"
            if reason:
                error = Failure("resource_limit", reason)
                error.definitely_not_sent = True
                raise error
            calls[source] = calls.get(source, 0) + 1
            reservations.add(source)
            persist_summary()

    def receipt(source, value):
        nonlocal stop_reason
        with lock:
            reservations.discard(source)
            receipts.append({"source_id": source, **value})
            if value.get("request_started"):
                if value.get("outcome") == "unknown":
                    stop_reason = stop_reason or "model_outcome_unknown"
                cost = value.get("cost_usd")
                if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                    stop_reason = stop_reason or "sent_call_cost_missing"
            save(ROOT / "provider-receipts.json", receipts)
            persist_summary()

    def run(request):
        nonlocal stop_reason
        source, source_started = request["source_id"], time.monotonic()
        directory = Path(request["state_dir"])
        result = {"source_id": source, "status": "started", "started_at": utc_now()}
        save(directory / "result.json", result)
        print(json.dumps(result), flush=True)
        provider = OpenRouterProvider(
            api_key=key,
            **PROVIDER,
            receipt_callback=lambda value: receipt(source, value),
            gate=lambda: gate(source),
        )
        try:
            onboard = infer_source(request, provider)
            result.update({name: onboard.get(name) for name in (
                "candidate_status", "task_status", "unresolved_requirements", "gap"
            )})
            result["onboarding_status"] = onboard.get("status")
            result["development_usage"] = onboard.get("usage")
            if onboard.get("source_acquisition"):
                acquisition = onboard["source_acquisition"]
                result["source_acquisition"] = {
                    name: acquisition.get(name)
                    for name in ("status", "excerpt_count", "usage", "export_sha256")
                }
            if onboard.get("candidate_status") != "validated":
                result["status"] = "no_validated_candidate"
            else:
                review = review_source(directory)
                save(directory / "review.json", review)
                config = review["mapping_config"]
                result["backend"] = config.get("execution", {}).get("kind", "ast")
                result["mapping_count"] = len(config.get("field_mappings", []))
                if not ast_only(config):
                    result["status"] = "bounded_development_only"
                    result["full_extraction_skipped_reason"] = "Non-AST or unrecognized execution contract; no full-record LLM extraction authorized by this trial policy."
                else:
                    with lock:
                        if not integrity(frozen)["unchanged"]:
                            stop_reason = stop_reason or "frozen_input_or_code_drift"
                            raise Failure("checkpoint_mismatch", stop_reason)
                    approve_source(
                        directory, review["review_fingerprint"],
                        "synthetic-mapping-cohort",
                        notes="Automated exploratory evaluation only; not human approval or ingestion authorization.",
                        synthetic=True,
                    )
                    extracted = extract_source(directory, provider=provider, allow_synthetic=True)
                    result.update(
                        status="extracted",
                        extraction_status=extracted["status"],
                        execution_status=extracted["execution_status"],
                        task_status=extracted["task_status"],
                        unresolved_requirements=extracted["unresolved_requirements"],
                        observations=len(extracted["observations"]),
                        output_summary=extracted["output_summary"],
                        extraction_usage=extracted["usage"],
                    )
        except Exception as error:
            with lock:
                if getattr(error, "kind", None) == "model_outcome_unknown":
                    stop_reason = stop_reason or "model_outcome_unknown"
            result.update(status="failed", error={
                "kind": getattr(error, "kind", type(error).__name__),
                "message": clean(str(error))[:1000],
            })
        result.update(completed_at=utc_now(), wall_seconds=time.monotonic() - source_started)
        save(directory / "result.json", result)
        with lock:
            results.append(result)
            persist_summary()
        print(json.dumps(clean(result), ensure_ascii=False), flush=True)
        return result

    save(ROOT / "provider-receipts.json", [])
    persist_summary()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run, requests))
    with lock:
        persist_summary(final=True)
    print(json.dumps({"summary": str(ROOT / "summary.json"), **totals(receipts)}), flush=True)


if __name__ == "__main__":
    main()
