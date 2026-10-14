"""Single command entry point; commands mirror the public workflow lifecycle."""

import argparse
import json
from pathlib import Path

from .common import Failure, strict_json
from .workflow import (
    acquire_source_evidence,
    approve_source,
    build_outputs,
    extract_source,
    infer_source,
    inspect_source,
    review_source,
    withdraw_outputs,
)

PROJECT = Path(__file__).resolve().parents[1]


def parser():
    root = argparse.ArgumentParser(
        description="Infer a mapping, review it, then run the company data pipeline."
    )
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect", help="Read native structures without a model call"
    )
    inspect.add_argument("--source-id", required=True)
    inspect.add_argument("--evidence-root", type=Path, required=True)
    infer = commands.add_parser("infer", help="Run the bounded source agent")
    infer.add_argument("--source-id", required=True)
    infer.add_argument("--evidence-root", type=Path, required=True)
    infer.add_argument("--run-dir", type=Path, required=True)
    infer.add_argument("--metadata", type=Path)
    infer.add_argument(
        "--require-field",
        action="append",
        default=[],
        dest="required_fields",
        help="Required canonical field; repeat to freeze the task's acceptance scope",
    )
    infer.add_argument(
        "--ontology", type=Path, default=PROJECT / "schema/firmable_ontology.yaml"
    )
    infer.add_argument(
        "--max-budget-usd",
        type=float,
        help="ccvibe CLI budget per model call, not a whole-run cap",
    )
    _provider_arguments(infer)
    acquire = commands.add_parser(
        "acquire",
        help="Read/follow hash-bound original source documents before inference",
    )
    acquire.add_argument("--source-id", required=True)
    acquire.add_argument("--evidence-root", type=Path, required=True)
    acquire.add_argument("--run-dir", type=Path, required=True)
    acquire.add_argument("--capture-dir", type=Path, required=True)
    acquire.add_argument(
        "--seeds",
        type=Path,
        required=True,
        help="JSON array of original url/path/sha256/media_type entries",
    )
    acquire.add_argument("--metadata", type=Path)
    acquire.add_argument(
        "--ontology", type=Path, default=PROJECT / "schema/firmable_ontology.yaml"
    )
    acquire.add_argument(
        "--require-field", action="append", default=[], dest="required_fields"
    )
    acquire.add_argument("--max-steps", type=int, default=12)
    acquire.add_argument(
        "--max-budget-usd", type=float, help="ccvibe CLI budget per model call"
    )
    _provider_arguments(acquire)
    for name in ("review", "approve", "extract"):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", type=Path, required=True)
        if name == "approve":
            command.add_argument("--fingerprint", required=True)
            command.add_argument("--reviewer", required=True)
            command.add_argument("--notes", default="")
        if name == "extract":
            command.add_argument("--output", type=Path)
            command.add_argument(
                "--max-budget-usd", type=float, help="ccvibe CLI budget per model call"
            )
            _provider_arguments(command)
    for name in ("build", "withdraw"):
        command = commands.add_parser(name)
        command.add_argument("--observations", type=Path, nargs="+", required=True)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--threshold", type=float, default=0.99)
        if name == "withdraw":
            command.add_argument("--source-id", required=True)
        else:
            command.add_argument("--profile-limit", type=int, default=50)
    commands.add_parser("check", help="Run offline regression checks; no model calls")
    return root


def _provider_arguments(command):
    command.add_argument(
        "--provider", choices=("ccvibe", "openrouter"), default="ccvibe"
    )
    command.add_argument(
        "--model",
        help="OpenRouter model ID (default: anthropic/claude-haiku-5.5)",
    )


def _provider(args):
    if args.provider == "openrouter":
        from .openrouter import OpenRouterProvider

        if args.max_budget_usd is not None:
            raise Failure("validation_error", "--max-budget-usd requires ccvibe")
        return OpenRouterProvider(
            model_id=args.model or "anthropic/claude-haiku-5.5", stream=True
        )
    from .provider import CcVibeProvider

    if args.model is not None:
        raise Failure("validation_error", "--model requires --provider openrouter")
    return CcVibeProvider(max_budget_usd=args.max_budget_usd)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_source(args.evidence_root, args.source_id)
        elif args.command in {"infer", "acquire"}:
            metadata = strict_json(args.metadata.read_bytes()) if args.metadata else {}
            request = {
                "source_id": args.source_id,
                "evidence_root": str(args.evidence_root),
                "state_dir": str(args.run_dir),
                "ontology_path": str(args.ontology),
                "catalogue_metadata": metadata,
                "required_fields": args.required_fields,
            }
            if args.command == "acquire":
                request["source_acquisition_max_steps"] = args.max_steps
                result = acquire_source_evidence(
                    request,
                    _provider(args),
                    args.capture_dir,
                    strict_json(args.seeds.read_bytes()),
                )
            else:
                result = infer_source(request, _provider(args))
        elif args.command == "review":
            result = review_source(args.run_dir)
        elif args.command == "approve":
            result = approve_source(
                args.run_dir, args.fingerprint, args.reviewer, args.notes
            )
        elif args.command == "extract":
            result = extract_source(
                args.run_dir,
                args.output,
                _provider(args),
            )
            result = {
                key: value for key, value in result.items() if key != "observations"
            }
        elif args.command == "build":
            result = build_outputs(
                args.observations, args.output_dir, args.threshold, args.profile_limit
            )
        elif args.command == "withdraw":
            result = withdraw_outputs(
                args.observations, args.source_id, args.output_dir, args.threshold
            )
            result = {key: value for key, value in result.items() if key != "profiles"}
        else:
            import unittest

            suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"))
            checked = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if checked.wasSuccessful() else 1
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Failure as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "kind": exc.kind,
                    "message": str(exc),
                    "details": exc.details,
                },
                ensure_ascii=False,
            )
        )
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps(
                {"status": "failed", "kind": "invalid_input", "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 1
