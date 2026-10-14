"""Run the fixed six-source cohort or one supplied unseen source, once."""

import argparse
import getpass
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from firmable_runtime.common import Failure, atomic_json, byte_hash, utc_now
from firmable_runtime.openrouter import OpenRouterProvider
from firmable_runtime.runtime import runtime_fingerprint
from firmable_runtime.workflow import (
    approve_source,
    build_outputs,
    extract_source,
    infer_source,
    review_source,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        type=Path,
        help="Single-source inference request; no source-specific runtime rules.",
    )
    parser.add_argument(
        "--run-dir", type=Path,
        help="New run directory; an existing directory is never overwritten.",
    )
    parser.add_argument(
        "--no-acquisition", action="store_true",
        help="Use only supplied local evidence; do not run external source reading.",
    )
    args = parser.parse_args()
    supplied = json.loads(args.request.read_text()) if args.request else None
    root = (
        args.run_dir.resolve() if args.run_dir is not None else PROJECT / (
            "runs/unseen-source" if supplied is not None else "runs/six-source"
        )
    )
    if root.exists():
        raise SystemExit(f"{root} already exists; do not overwrite or resend.")
    key = os.environ.get("OPENROUTER_API_KEY") or getpass.getpass(
        "OpenRouter API key (not saved): "
    )
    if not key.strip():
        raise SystemExit("Missing API key")
    original = json.loads((PROJECT / "evidence/six-source-protocol.json").read_text())
    started = time.monotonic()
    sources = (
        [
            {
                "source_id": supplied["source_id"],
                "required_fields": supplied.get("required_fields", []),
            }
        ]
        if supplied is not None
        else original["sources"]
    )
    workers = min(3, len(sources))
    tracked = (
        list(Path(supplied["evidence_root"]).rglob("*")) + [args.request]
        if supplied is not None
        else [
            *sorted((PROJECT / "data/assessment-inputs").rglob("*")),
            PROJECT / "evidence/six-source-frozen-labels.json",
            PROJECT / "evidence/six-source-frozen-field-dispositions.json",
        ]
    )
    tracked += [
        PROJECT / "schema/firmable_ontology.yaml",
    ]
    hashes = {
        str(path.resolve().relative_to(PROJECT)): byte_hash(path.read_bytes())
        for path in tracked
        if path.is_file()
    }
    protocol = {
        "started_at": utc_now(),
        "runtime_fingerprint": runtime_fingerprint(),
        "model": original["model"],
        "reasoning_effort": "low",
        "stream": False,
        "max_output_tokens": 16000,
        "provider_allowlist": ["Anthropic"],
        "repetitions_per_source": 1,
        "worker_concurrency": workers,
        "evaluation_scope": "unseen_source"
        if supplied is not None
        else "six_development_sources",
        "runner_sha256": byte_hash(Path(__file__).read_bytes()),
        "synthetic_approval_only": True,
        "human_gold": False,
        "inputs_and_labels_sha256": hashes,
        "sources": [
            {"source_id": s["source_id"], "required_fields": s["required_fields"]}
            for s in sources
        ],
        "limits": {
            "calls": 180,
            "per_source_calls": 60,
            "new_send_seconds": 1200,
            "observed_cost_guard_usd": 2.0,
            "per_call_reserve_usd": 0.25,
        },
        "cost_guard_limitation": "Receipt-based stop with reservation; not a provider-enforced billing limit.",
        "unknown_outcome_policy": "Stop new calls globally; never resend an unknown outcome.",
        "labels_policy": "Frozen labels are read only by the subsequent scoring step, never by the planner.",
        "source_acquisition": "Disabled: supplied local evidence only."
        if args.no_acquisition
        else "Agent-directed reading of observed source links before inference; the agent may finish without fetching. Original inputs stay unchanged; acquired excerpts are separately hash-bound.",
        "source_acquisition_enabled": not args.no_acquisition,
    }
    atomic_json(root / "protocol.json", protocol)
    lock = threading.Lock()
    receipts, reservations, calls = [], set(), {}
    stop_reason = None

    def gate(source_id):
        nonlocal stop_reason
        with lock:
            cost = sum(r.get("cost_usd") or 0 for r in receipts)
            if (
                time.monotonic() - started >= 1200
                or sum(calls.values()) >= 180
                or cost + 0.25 * (len(reservations) + 1) > 2.0
            ):
                stop_reason = stop_reason or "cohort_resource_limit"
            if stop_reason or calls.get(source_id, 0) >= 60:
                error = Failure("resource_limit", stop_reason or "source_call_limit")
                error.definitely_not_sent = True
                raise error
            calls[source_id] = calls.get(source_id, 0) + 1
            reservations.add(source_id)

    def receipt(source_id, value):
        nonlocal stop_reason
        with lock:
            reservations.discard(source_id)
            receipts.append({"source_id": source_id, **value})
            if value.get("request_started") and value.get("outcome") == "unknown":
                stop_reason = "model_outcome_unknown"
            atomic_json(root / "provider-receipts.json", receipts)

    def run(source):
        source_id = source["source_id"]
        source_root = root / source_id
        source_started = time.monotonic()
        source_input = PROJECT / "data/assessment-inputs" / source_id
        request = {
            "source_id": source_id,
            "state_dir": str(source_root),
            "evidence_root": str(source_input / "evidence"),
            "ontology_path": str(PROJECT / "schema/firmable_ontology.yaml"),
            "catalogue_metadata": (
                {}
                if supplied is not None
                else json.loads((source_input / "catalogue_metadata.json").read_text())
            ),
            "required_fields": source["required_fields"],
            "execution_scope": "onboarding_development",
        }
        if supplied is not None:
            request = {**supplied, "state_dir": str(source_root)}
        if args.no_acquisition:
            request["source_acquisition_enabled"] = False
        provider = OpenRouterProvider(
            api_key=key,
            model_id=protocol["model"],
            stream=False,
            receipt_callback=lambda value: receipt(source_id, value),
            gate=lambda: gate(source_id),
        )
        result = {"source_id": source_id, "status": "started", "started_at": utc_now()}
        print(json.dumps(result), flush=True)
        try:
            onboard = infer_source(request, provider)
            result.update(
                onboarding_status=onboard["status"],
                task_status=onboard["task_status"],
                candidate_status=onboard.get("candidate_status"),
            )
            if onboard.get("source_acquisition"):
                acquired = onboard["source_acquisition"]
                result["source_acquisition"] = {
                    key: acquired[key]
                    for key in ("status", "excerpt_count", "usage", "export_sha256")
                }
                result["source_acquisition"]["actions"] = [
                    a["action"]["action"] for a in acquired["actions"]
                ]
            if onboard.get("candidate_status") != "validated":
                result["status"] = "no_validated_candidate"
            else:
                review = review_source(source_root)
                atomic_json(source_root / "review.json", review)
                approve_source(
                    source_root,
                    review["review_fingerprint"],
                    "synthetic-source-benchmark",
                    notes="Automated evaluation only; not human approval.",
                    synthetic=True,
                )
                extracted = extract_source(
                    source_root, provider=provider, allow_synthetic=True
                )
                result.update(
                    status="extracted",
                    extraction_status=extracted["status"],
                    task_status=extracted["task_status"],
                    execution_status=extracted["execution_status"],
                    observations=len(extracted["observations"]),
                )
        except Exception as error:
            result.update(
                status="failed",
                error={
                    "kind": getattr(error, "kind", type(error).__name__),
                    "message": str(error).replace(key, "[REDACTED]")[:1000],
                },
            )
        result.update(
            completed_at=utc_now(), wall_seconds=time.monotonic() - source_started
        )
        atomic_json(source_root / "result.json", result)
        print(json.dumps(result), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(run, sources))
    paths = [root / s["source_id"] / "observations.jsonl" for s in sources]
    available = [p for p in paths if p.exists()]
    downstream = build_outputs(available, root / "downstream") if available else None
    sent = [r for r in receipts if r.get("request_started")]
    summary = {
        "completed_at": utc_now(),
        "wall_seconds": time.monotonic() - started,
        "results": results,
        "downstream": downstream,
        "stop_reason": stop_reason,
        "model_calls": len(sent),
        "reported_cost_usd": sum(r.get("cost_usd") or 0 for r in sent),
        "cost_complete": all(r.get("cost_usd") is not None for r in sent),
        "reported_tokens": sum(sum(r.get("usage", {}).values()) for r in sent),
        "runtime_unchanged": runtime_fingerprint() == protocol["runtime_fingerprint"],
        "runner_unchanged": byte_hash(Path(__file__).read_bytes())
        == protocol["runner_sha256"],
        "inputs_and_labels_unchanged": all(
            byte_hash((PROJECT / p).read_bytes()) == h for p, h in hashes.items()
        ),
        "human_approved_configs": 0,
        "human_link_reviews": 0,
        "cost_basis": "Provider receipts; not invoice",
    }
    atomic_json(root / "summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}), flush=True)


if __name__ == "__main__":
    main()
