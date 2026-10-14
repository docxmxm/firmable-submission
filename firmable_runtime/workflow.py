"""Public lifecycle: infer → review → approve → extract → link/profile.

Approval binds the immutable config, source bytes, ontology and runtime. The
planner's development validation is separate from formal approved extraction.
"""

import copy
import fcntl
import shutil
from contextlib import contextmanager
from pathlib import Path

from .acceptance import evaluate_acceptance
from .common import (
    Failure,
    atomic_json,
    byte_hash,
    canonical,
    digest,
    strict_json,
    utc_now,
)
from .execution import (
    compile_config,
    discover,
    internal_observations,
    run_engine,
    table_schema,
    wire_profiles,
)
from .identity import build_profile_view, link_records, withdraw_view
from .native import input_manifest, public_structure
from .runtime import Runtime, runtime_fingerprint, source_output_summary
from .store import DurableRun


@contextmanager
def _run_lock(run_dir):
    root = Path(run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Failure(
                "run_busy", "Another process owns this run directory"
            ) from exc
        try:
            yield root
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _json(path):
    try:
        return strict_json(Path(path).read_bytes())
    except OSError as exc:
        raise Failure(
            "missing_artifact", "Required file is missing", path=str(path)
        ) from exc


def inspect_source(evidence_root, source_id):
    tables, paths, manifest, docs = discover(evidence_root, source_id)
    return {
        "source_id": source_id,
        "tables": public_structure(tables),
        "data_files": paths,
        "evidence_hash": digest(manifest),
        "documentation": docs,
    }


def acquire_source_evidence(request, provider, capture_root, seed_sources):
    """Prepare a separately frozen original-document supplement before infer."""
    from .acquisition import acquire_source_evidence as acquire

    return acquire(request, provider, capture_root, seed_sources)


def _acquire_for_inference(request, provider):
    """Let the reading agent choose source links, then freeze its original excerpts."""
    from .source_document_tools import document_pages, source_url_candidates

    root, evidence = Path(request["state_dir"]), Path(request["evidence_root"])
    manifest = input_manifest(evidence)
    links = source_url_candidates(
        request.get("catalogue_metadata", {}),
        pages=document_pages(evidence, manifest)["pages"],
    )
    if not links:
        return request, None
    navigation = root / "source-links.json"
    navigation_value = {"links": links}
    if navigation.exists() and _json(navigation) != navigation_value:
        raise Failure("checkpoint_mismatch", "Original navigation sources changed")
    atomic_json(navigation, navigation_value)
    acquired = acquire_source_evidence(
        {**request, "state_dir": str(root / "acquisition")},
        provider,
        root / "capture",
        [
            {
                "kind": "navigation",
                "url": None,
                "path": str(navigation),
                "sha256": byte_hash(navigation.read_bytes()),
                "media_type": "application/json",
            }
        ],
    )
    effective = copy.deepcopy(request)
    effective["source_acquisition_policy"] = {
        "mode": "observed_links_before_inference",
        "original_evidence_root": str(evidence),
        "original_evidence_hash": digest(manifest),
        "navigation_sha256": byte_hash(navigation.read_bytes()),
        "result_path": str(root / "acquisition/acquisition.json"),
        "result_sha256": byte_hash(
            (root / "acquisition/acquisition.json").read_bytes()
        ),
        "capture_manifest_sha256": acquired["capture_manifest_sha256"],
        "export_sha256": acquired["export_sha256"],
        "excerpt_count": acquired["excerpt_count"],
        "status": acquired["status"],
        "max_steps": request.get("source_acquisition_max_steps", 12),
    }
    if acquired["excerpt_count"]:
        # Only exact source excerpts join the input. Model reasons and navigation
        # records stay in the journal; original native files retain their paths.
        supplement = "acquired-" + acquired["export_sha256"][:16] + ".md"
        if any(item["path"] == supplement for item in manifest):
            raise Failure("validation_error", "Source supplement path is occupied")
        expected = sorted(
            manifest + [{"path": supplement, "sha256": acquired["export_sha256"]}],
            key=lambda item: item["path"],
        )
        snapshot = root / "evidence"
        if not snapshot.exists():
            pending = root / "evidence.pending"
            pending.mkdir(exist_ok=True)
            for item in manifest:
                target = pending / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(evidence / item["path"], target)
            shutil.copyfile(acquired["export_path"], pending / supplement)
            if input_manifest(pending) != expected:
                raise Failure("checkpoint_mismatch", "Evidence changed during snapshot")
            pending.replace(snapshot)
        if input_manifest(snapshot) != expected:
            raise Failure("checkpoint_mismatch", "Frozen source supplement changed")
        effective["evidence_root"] = str(snapshot)
    return effective, acquired


def infer_source(request, provider=None):
    from .agent import onboard_source
    from .provider import CcVibeProvider

    request = copy.deepcopy(request)
    for key in ("state_dir", "evidence_root", "ontology_path"):
        request[key] = str(Path(request[key]).resolve())
    root, evidence = Path(request["state_dir"]), Path(request["evidence_root"])
    if root.is_relative_to(evidence) or root.is_relative_to(Path(__file__).parent):
        raise Failure(
            "validation_error",
            "Run output must be separate from source evidence and code",
        )
    request.setdefault("execution_scope", "onboarding_development")
    if request["execution_scope"] != "onboarding_development":
        raise Failure("validation_error", "Inference requires development scope")
    if type(request.get("source_acquisition_enabled", True)) is not bool:
        raise Failure("validation_error", "source_acquisition_enabled must be boolean")
    with _run_lock(root):
        original = root / "input-request.json"
        if original.exists() and _json(original) != request:
            raise Failure("checkpoint_mismatch", "Saved input request changed")
        atomic_json(original, request)
        provider = provider or CcVibeProvider()
        acquired = None
        if request.get("source_acquisition_enabled", True):
            request, acquired = _acquire_for_inference(request, provider)
        else:
            request["source_acquisition_policy"] = {"mode": "disabled_local_evidence_only"}
        saved = root / "request.json"
        if saved.exists() and _json(saved) != request:
            raise Failure("checkpoint_mismatch", "Saved inference request changed")
        atomic_json(saved, request)
        result = onboard_source(request, provider)
        if acquired is not None:
            result = {**result, "source_acquisition": acquired}
        atomic_json(root / "onboard.json", result)
        return result


def _review_state(run_dir):
    root = Path(run_dir).resolve()
    request = _json(root / "request.json")
    checkpoint = _json(root / "checkpoint.internal.json")
    identity = checkpoint["identity"]
    run = DurableRun(root, identity, root / "checkpoint.internal.json")
    state = run.state.get("agent", {})
    if not state.get("result_artifact"):
        raise Failure("approval_required", "Only a validated candidate can be reviewed")
    result = run.load_artifact(state["result_artifact"])
    if result.get("candidate_status") != "validated":
        raise Failure("approval_required", "Only a validated candidate can be reviewed")
    config = run.load_artifact("final-config")
    if (
        config != result.get("mapping_config")
        or result.get("runtime_identity") != identity
    ):
        raise Failure("checkpoint_mismatch", "Final config or result identity changed")
    tables, paths, manifest, docs = discover(
        request["evidence_root"], request["source_id"]
    )
    current = {
        "source_id": request["source_id"],
        "evidence_hash": digest(manifest),
        "metadata_hash": digest(request.get("catalogue_metadata", {})),
        "ontology_hash": byte_hash(Path(request["ontology_path"]).read_bytes()),
        "runtime_hash": runtime_fingerprint(),
        "model": identity["model"],
        "policy": {
            k: request.get(k)
            for k in (
                "dev_record_limit",
                "proposal_record_limit",
                "declared_record_ids",
                "execution_scope",
                "required_fields",
                "source_acquisition_policy",
            )
        },
    }
    if current != identity or str(root) != request["state_dir"]:
        raise Failure(
            "checkpoint_mismatch",
            "Source, request, ontology or runtime changed; infer again in a new run",
        )
    acquisition = request.get("source_acquisition_policy") or {}
    if acquisition.get("mode") == "observed_links_before_inference":
        from .source_acquisition import SourceAcquisition

        receipt = root / "acquisition/acquisition.json"
        if byte_hash(receipt.read_bytes()) != acquisition["result_sha256"]:
            raise Failure("checkpoint_mismatch", "Source acquisition receipt changed")
        acquired = _json(receipt)
        capture = SourceAcquisition(root / "capture")
        if (
            byte_hash((capture.root / "manifest.json").read_bytes())
            != acquisition["capture_manifest_sha256"]
            or byte_hash(Path(acquired["export_path"]).read_bytes())
            != acquisition["export_sha256"]
        ):
            raise Failure("checkpoint_mismatch", "Acquired source evidence changed")
    selected = run.state["selected_tables"]
    if config["source_schema"] != table_schema(tables, selected, paths):
        raise Failure("checkpoint_mismatch", "Frozen native reader selection changed")
    name_review_name = result.get("name_review_artifact")
    name_review_binding = None
    if name_review_name is not None:
        candidate = state.get("candidates", {}).get(state.get("selected"), {})
        if name_review_name != candidate.get("name_review_artifact"):
            raise Failure("checkpoint_mismatch", "Selected name review changed")
        name_review = run.load_artifact(name_review_name)
        compile_config(config, docs, name_review=name_review)
        name_review_binding = run.state["artifacts"][name_review_name]
    binding = {
        "runtime_identity": identity,
        "config_hash": digest(config),
        "result_sha256": run.state["artifacts"][state["result_artifact"]]["sha256"],
        "validation_artifacts": {
            k: v["sha256"]
            for k, v in run.state["artifacts"].items()
            if k.startswith(
                ("validation-", "comparison-validation-", "selection-comparison-")
            )
        },
        "name_review_artifact": name_review_binding,
    }
    return root, request, run, result, config, tables, docs, digest(binding)


def review_source(run_dir):
    root, request, run, result, config, tables, docs, fingerprint = _review_state(
        run_dir
    )
    return {
        "source_id": request["source_id"],
        "review_fingerprint": fingerprint,
        "mapping_config": config,
        "status": result["status"],
        "candidate_status": result["candidate_status"],
        "task_status": result["task_status"],
        "task_requirements": result["task_requirements"],
        "task_acceptance": result["task_acceptance"],
        "unresolved_requirements": result["unresolved_requirements"],
        "unresolved_requirement_history": result["unresolved_requirement_history"],
        "candidate_selection": result.get("candidate_selection"),
        "table_scope": result.get("table_scope"),
        "gap": result.get("gap"),
        "proposal_records": run.load_artifact("proposal-records"),
        "validation_results": result["validation_results"],
        "name_review": run.load_artifact(result["name_review_artifact"])
        if result.get("name_review_artifact")
        else None,
        "usage": result["usage"],
        "source_acquisition": request.get("source_acquisition_policy"),
        "approval": _json(root / "approval.json")
        if (root / "approval.json").exists()
        else None,
        "limitation": "Model/development checks do not establish human accuracy or unseen-source success.",
    }


def approve_source(run_dir, fingerprint, reviewer, notes="", synthetic=False):
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise Failure("validation_error", "Reviewer identity is required")
    with _run_lock(run_dir):
        root, request, run, result, config, tables, docs, expected = _review_state(
            run_dir
        )
        if fingerprint != expected:
            raise Failure(
                "approval_mismatch",
                "Approval must quote the fingerprint of the reviewed candidate",
            )
        approval = {
            "review_fingerprint": expected,
            "reviewer": reviewer.strip(),
            "notes": notes,
            "synthetic": synthetic,
            "task_status": result["task_status"],
            "task_acceptance": result["task_acceptance"],
            "unresolved_requirements": result["unresolved_requirements"],
            "candidate_selection": result.get("candidate_selection"),
            "table_scope": result.get("table_scope"),
            "approved_at": utc_now(),
        }
        atomic_json(root / "approval.json", approval)
        return approval


def extract_source(run_dir, output=None, provider=None, allow_synthetic=False):
    from .provider import CcVibeProvider

    with _run_lock(run_dir):
        root, request, run, result, config, tables, docs, fingerprint = _review_state(
            run_dir
        )
        approval = _json(root / "approval.json")
        if (
            approval.get("review_fingerprint") != fingerprint
            or not approval.get("reviewer")
            or (approval.get("synthetic") is not False and not allow_synthetic)
        ):
            raise Failure(
                "approval_required",
                "A matching human approval is required before extraction",
            )
        destination = Path(output).resolve() if output else root / "observations.jsonl"
        if destination.is_relative_to(
            Path(request["evidence_root"]).resolve()
        ) or destination.is_relative_to(Path(__file__).parent):
            raise Failure(
                "validation_error",
                "Extraction output cannot overwrite source evidence or code",
            )
        if (
            destination.is_relative_to(root)
            and destination != root / "observations.jsonl"
        ):
            raise Failure(
                "validation_error",
                "Inside a run, use the standard observations.jsonl output",
            )
        if destination.exists() and destination.is_dir():
            raise Failure("validation_error", "Extraction output must be a file")
        extraction_request = {
            **request,
            "state_dir": str(root / "extraction"),
            "execution_scope": "approved_extraction",
        }
        runtime = Runtime(extraction_request, provider or CcVibeProvider())
        if "extraction-result" in runtime.run.state["artifacts"]:
            extracted = runtime.run.load_artifact("extraction-result")
        else:
            rows = [
                row
                for index in run.state["selected_tables"]
                for row in tables[index]["records"]
            ]
            observations, report = run_engine(
                config,
                rows,
                docs,
                request.get("catalogue_metadata", {}),
                runtime.run.state["generated_at"],
                runtime=runtime,
                name_review=run.load_artifact(result["name_review_artifact"])
                if result.get("name_review_artifact")
                else None,
            )
            acceptance = evaluate_acceptance(
                result["task_requirements"],
                config,
                observations,
                report,
                scope="full_extraction",
                prior_acceptance=result["task_acceptance"],
            )
            execution_status = "complete" if not report["errors"] else "partial"
            extracted = {
                "source_id": request["source_id"],
                "config_hash": digest(config),
                "approval": approval,
                "observations": observations,
                "report": report,
                "output_summary": source_output_summary(
                    observations, len(config["field_mappings"]), len(rows)
                ),
                "usage": runtime.usage(),
                "execution_status": execution_status,
                "status": "partial"
                if execution_status == "partial"
                or acceptance["status"] == "partial"
                or result.get("candidate_selection", {}).get("unresolved", False)
                else "complete",
                "task_status": acceptance["status"],
                "task_requirements": result["task_requirements"],
                "task_acceptance": acceptance,
                "unresolved_requirements": acceptance["unresolved_fields"],
                "candidate_selection": result.get("candidate_selection"),
                "table_scope": result.get("table_scope"),
                "gap": result.get("gap"),
            }
            runtime.artifact("extraction-result", extracted)
            runtime.persist("extracted")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            b"".join(canonical(row) + b"\n" for row in extracted["observations"])
        )
        atomic_json(
            root / "extraction.json",
            {k: v for k, v in extracted.items() if k != "observations"},
        )
        return extracted


def _observations(paths):
    result = []
    for path in paths:
        path = Path(path)
        if path.suffix == ".jsonl":
            result.extend(
                strict_json(line)
                for line in path.read_bytes().splitlines()
                if line.strip()
            )
        else:
            value = _json(path)
            result.extend(value["observations"] if isinstance(value, dict) else value)
    if not result:
        raise Failure("validation_empty", "No observations supplied")
    return internal_observations(result)


def build_outputs(paths, output_dir, threshold=0.99, profile_limit=50):
    observations = _observations(paths)
    linking = link_records(observations, threshold)
    profiles = build_profile_view(observations, linking)["profiles"]
    root = Path(output_dir)
    if type(profile_limit) is not int or profile_limit < 1:
        raise Failure("validation_error", "Profile limit must be a positive integer")
    atomic_json(root / "links.json", linking)
    atomic_json(root / "unlinked.json", linking["unlinked"])
    atomic_json(root / "profiles.json", wire_profiles(profiles[:profile_limit]))
    summary = {
        "observation_count": len(observations),
        "link_count": len(linking["links"]),
        "unlinked_count": len(linking["unlinked"]),
        "profile_population": len(profiles),
        "profile_count": min(profile_limit, len(profiles)),
        "threshold": threshold,
        "limitation": "Deterministic pipeline output; precision requires separate hand review.",
    }
    atomic_json(root / "summary.json", summary)
    return summary


def withdraw_outputs(paths, source_id, output_dir, threshold=0.99):
    observations = _observations(paths)
    if source_id not in {o["source_id"] for o in observations}:
        raise Failure(
            "validation_error", "Withdrawal source is not in the supplied observations"
        )
    linking = link_records(observations, threshold)
    profiles = build_profile_view(observations, linking)["profiles"]
    result = withdraw_view(profiles, observations, linking, source_id)
    atomic_json(Path(output_dir) / "withdrawal.json", result)
    return result
