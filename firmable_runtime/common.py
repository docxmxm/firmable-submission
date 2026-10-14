"""Shared serialization primitives; hashes describe bytes or canonical JSON."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

# A byte ceiling for full documentation plus bounded original-source excerpts.
# Token windows and per-call dollar limits remain separate provider/run policies.
MAX_MODEL_INPUT_BYTES = 512 * 1024


class Failure(ValueError):
    def __init__(self, kind, message, **details):
        super().__init__(message)
        self.kind, self.details = kind, details


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def byte_hash(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Failure("parse_error", "Duplicate JSON key: " + key)
            result[key] = value
        return result

    def number(value):
        result = float(value)
        if not math.isfinite(result):
            raise Failure("parse_error", "Non-finite number")
        return result

    def constant(value):
        raise Failure("parse_error", "Non-finite constant: " + value)

    try:
        return json.loads(
            data, object_pairs_hook=pairs, parse_float=number, parse_constant=constant
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Failure("parse_error", str(exc)) from exc


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(root, relative):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Failure("validation_error", "Root must be a regular directory")
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or relative.startswith("/")
        or any(p in ("", ".", "..") for p in relative.split("/"))
    ):
        raise Failure("validation_error", "Invalid relative path")
    target = root
    for part in PurePosixPath(relative).parts:
        target /= part
        if target.is_symlink():
            raise Failure("validation_error", "Symlinks are unsupported")
    if not target.is_file() or not target.resolve().is_relative_to(root.resolve()):
        raise Failure("file_not_found", "File is outside root or missing: " + relative)
    return target


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return byte_hash(data)
