"""Bounded, original-source capture before inference; never execute source code.

The caller supplies hash-bound seed files. Navigation seeds carry original link
bindings, not semantic evidence. The model follows their observed links, then
same-origin references in captured originals; source code is never executed.
"""

from __future__ import annotations

import copy
import mimetypes
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import atomic_json, byte_hash, digest, safe_path, strict_json, utc_now


DEFAULT_LIMITS = {
    "max_fetches": 12,
    "max_asset_bytes": 4_000_000,
    "max_total_bytes": 16_000_000,
    "max_depth": 3,
    "max_assets": 30,
    "max_references_per_asset": 2000,
    "max_read_chars": 16000,
}
TEXT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/javascript",
    "text/javascript",
    "application/json",
    "application/ld+json",
    "text/plain",
}
LITERAL_ASSET = re.compile(
    r"(?P<quote>['\"])(?P<value>(?:\.{0,2}/|https://|[A-Za-z0-9_~-])[^'\"\s<>\\]{1,2000}"
    r"\.(?:m?js|json|html?|css)(?:\?[^'\"\s<>\\]*)?)(?P=quote)"
)


def _url(value, *, allow_http=False):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError("Source URL must be a bounded HTTPS URL")
    value = urldefrag(value)[0]
    parts = urlsplit(value)
    if (
        parts.scheme not in ({"http", "https"} if allow_http else {"https"})
        or not parts.hostname
        or parts.username
        or parts.password
        or any(ord(c) < 33 for c in value)
    ):
        raise ValueError(
            "Source URL must use an allowed HTTP(S) scheme without credentials"
        )
    _ = parts.port
    return value


def _origin(value):
    parts = urlsplit(_url(value, allow_http=True))
    return parts.hostname.lower(), parts.port or (
        443 if parts.scheme == "https" else 80
    )


def _same_origin_or_upgrade(before, after):
    """Allow an observed HTTP endpoint to upgrade, never downgrade or change host."""
    source = urlsplit(_url(before, allow_http=True))
    target = urlsplit(_url(after, allow_http=True))
    if source.scheme == target.scheme:
        return _origin(before) == _origin(after)
    return (
        source.scheme == "http"
        and target.scheme == "https"
        and _origin(before) == (target.hostname.lower(), 80)
        and _origin(after) == (source.hostname.lower(), 443)
    )


def _media(value, url):
    value = value or mimetypes.guess_type(urlsplit(url).path)[0] or "text/plain"
    return value.split(";", 1)[0].strip().lower()


class _HTMLIndex(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text, self.references, self.blocks = text, [], []
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.script = None
        self.bases = []

    def source_offset(self):
        line, column = self.getpos()
        return self.starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        start = self.source_offset()
        end = start + len(self.get_starttag_text())
        if tag == "base" and "href" in attrs:
            self.bases.append(
                {
                    "kind": "html_base",
                    "href": attrs["href"] or "",
                    "start": start,
                    "end": end,
                }
            )
        key = "src" if tag in {"script", "iframe", "source"} else "href"
        if tag in {"script", "iframe", "source", "a", "link"} and attrs.get(key):
            self.references.append((attrs[key], f"html_{tag}_{key}", start, end))
        if tag == "script" and not attrs.get("src"):
            kind = (
                "embedded_json" if "json" in attrs.get("type", "") else "inline_script"
            )
            self.script = (kind, end)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            kind, start = self.script
            self.blocks.append(
                {"kind": kind, "start": start, "end": self.source_offset()}
            )
            self.script = None

    def finish(self):
        self.close()
        if self.script is not None:
            kind, start = self.script
            self.blocks.append({"kind": kind, "start": start, "end": len(self.text)})


def _navigation_links(text):
    value = strict_json(text)
    if (
        not isinstance(value, dict)
        or set(value) != {"links"}
        or not isinstance(value["links"], list)
    ):
        raise ValueError("Navigation seed must contain only a links list")
    for link in value["links"]:
        if (
            not isinstance(link, dict)
            or set(link) != {"url", "source_ref"}
            or not isinstance(link["url"], str)
            or not isinstance(link["source_ref"], dict)
            or not link["source_ref"]
        ):
            raise ValueError(
                "Navigation links require a URL and original source binding"
            )
    return value["links"]


def _navigation_index(asset, text, limits):
    links = _navigation_links(text)
    references = []
    for index, link in enumerate(links[: limits["max_references_per_asset"]]):
        url, blocked = link["url"], None
        try:
            url = _url(url, allow_http=True)
        except ValueError:
            blocked = "unsupported_url"
        if asset["depth"] >= limits["max_depth"]:
            blocked = blocked or "depth_limit"
        reference = {
            "parent_asset_id": asset["asset_id"],
            "parent_sha256": asset["sha256"],
            "url": url,
            "kind": "navigation_link",
            "source_ref": copy.deepcopy(link["source_ref"]),
            "navigation_pointer": f"/links/{index}/url",
            # The JSON pointer identifies the value; the hash/offsets bind the
            # complete navigation envelope, whose text is not source evidence.
            "start": 0,
            "end": len(text),
            "source_text": text[:256],
            "source_text_sha256": byte_hash(text.encode("utf-8")),
            "source_text_truncated": len(text) > 256,
            "fetchable": blocked is None,
            "blocked_reason": blocked,
        }
        reference["reference_id"] = "ref-" + digest(reference)[:24]
        references.append(reference)
    omissions = (
        [{"kind": "reference_index_limit", "omitted": len(links) - len(references)}]
        if len(links) > len(references)
        else []
    )
    return references, [], omissions


def _index(asset, text, limits):
    if asset.get("kind", "original") == "navigation":
        return _navigation_index(asset, text, limits)
    refs, blocks, omissions = [], [], []
    base_url = asset["url"]
    if asset["media_type"] in {"text/html", "application/xhtml+xml"}:
        parser = _HTMLIndex(text)
        parser.feed(text)
        parser.finish()
        refs, blocks = parser.references, parser.blocks
        for base in parser.bases:
            try:
                candidate = urldefrag(urljoin(asset["url"], base["href"]))[0]
                parts = urlsplit(candidate)
                if not parts.scheme or (
                    parts.scheme in {"http", "https"} and not parts.hostname
                ):
                    raise ValueError("Invalid HTML base URL")
                _ = parts.port
                base_url = candidate
                blocks.append({**base, "resolved_url": base_url})
                break
            except (TypeError, ValueError):
                omissions.append({"kind": "invalid_html_base", "start": base["start"]})
    # A quoted path is only a reference candidate, not a claim it executes.
    refs += [
        (m.group("value"), "literal_asset_reference", m.start("value"), m.end("value"))
        for m in LITERAL_ASSET.finditer(text)
    ]
    result, seen, invalid = [], set(), 0
    for raw, kind, start, end in refs:
        if raw.startswith(("#", "data:", "javascript:", "mailto:", "tel:")):
            continue
        url = raw
        try:
            url = urldefrag(urljoin(base_url, raw))[0]
            _url(url, allow_http=urlsplit(asset["url"]).scheme == "http")
            blocked = (
                None if _same_origin_or_upgrade(asset["url"], url) else "cross_origin"
            )
        except ValueError:
            blocked = "unsupported_url"
            invalid += 1
        if (url, start) in seen:
            continue
        seen.add((url, start))
        if asset["depth"] >= limits["max_depth"]:
            blocked = blocked or "depth_limit"
        item = {
            "parent_asset_id": asset["asset_id"],
            "parent_sha256": asset["sha256"],
            "url": url,
            "kind": kind,
            "start": start,
            "end": end,
            "source_text": text[start : min(end, start + 256)],
            "source_text_sha256": byte_hash(text[start:end].encode("utf-8")),
            "source_text_truncated": end - start > 256,
            "fetchable": blocked is None,
            "blocked_reason": blocked,
        }
        item["reference_id"] = "ref-" + digest(item)[:24]
        if len(result) < limits["max_references_per_asset"]:
            result.append(item)
    if len(seen) > len(result):
        omissions.append(
            {"kind": "reference_index_limit", "omitted": len(seen) - len(result)}
        )
    if invalid:
        omissions.append({"kind": "unsupported_reference_url", "count": invalid})
    if len(blocks) > 64:
        omissions.append(
            {"kind": "embedded_block_index_limit", "omitted": len(blocks) - 64}
        )
        blocks = blocks[:64]
    for block in blocks:
        block["line"] = text.count("\n", 0, block["start"]) + 1
    return result, blocks, omissions


class _SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _same_origin_or_upgrade(req.full_url, newurl):
            raise ValueError("Cross-origin redirect is outside this source reference")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_http(url, max_bytes):
    request = Request(
        url,
        headers={
            "User-Agent": "Firmable-source-capture/1",
            "Accept-Encoding": "identity",
        },
    )
    deadline = time.monotonic() + 30
    with build_opener(_SameOriginRedirect()).open(request, timeout=15) as response:
        chunks, size = [], 0
        while size <= max_bytes:
            if time.monotonic() > deadline:
                raise TimeoutError("Source response exceeded the fetch time bound")
            chunk = response.read1(min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        body = b"".join(chunks)
        return {
            "url": response.url,
            "status": response.status,
            "media_type": response.headers.get("Content-Type", ""),
            "body": body,
        }


def prepare_capture(capture_root, sources, *, limits=None):
    """Copy bound seeds; originals use url/path/sha256, navigation has url=None.

    The returned manifest may also be prepared externally using this format.
    SourceAcquisition validates original hashes and reindexes references before
    network access. Do not put capture_root inside an already frozen evidence set.
    """
    root = Path(capture_root)
    if root.exists() and (root.is_symlink() or any(root.iterdir())):
        raise ValueError("Capture directory must be new or empty")
    chosen = {**DEFAULT_LIMITS, **(limits or {})}
    if set(chosen) != set(DEFAULT_LIMITS) or any(
        type(v) is not int or v < 1 for v in chosen.values()
    ):
        raise ValueError("Capture limits must be positive integers with known keys")
    if not sources or len(sources) > chosen["max_assets"]:
        raise ValueError("Seed count is outside capture limits")
    originals = []
    for source in sources:
        path = Path(source["path"])
        if path.is_symlink() or not path.is_file():
            raise ValueError("Seed must be a regular original file")
        if path.stat().st_size > chosen["max_asset_bytes"]:
            raise ValueError("Seed exceeds max_asset_bytes")
        data = path.read_bytes()
        if byte_hash(data) != source["sha256"]:
            raise ValueError("Seed hash mismatch")
        kind = source.get("kind", "original")
        if kind == "navigation":
            if (
                source.get("url") is not None
                or source.get("media_type") != "application/json"
            ):
                raise ValueError(
                    "Navigation seed requires url=None and application/json"
                )
            _navigation_links(data)
            url, media = None, "application/json"
        elif kind == "original":
            url = _url(source["url"])
            media = _media(source.get("media_type"), url)
        else:
            raise ValueError("Unknown capture seed kind")
        if media not in TEXT_TYPES:
            raise ValueError(
                "Only original HTML/JavaScript/JSON/plain text is supported"
            )
        originals.append((url, data, media, kind))
    if sum(len(item[1]) for item in originals) > chosen["max_total_bytes"]:
        raise ValueError("Seeds exceed max_total_bytes")
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir()
    manifest = {
        "version": 1,
        "created_utc": utc_now(),
        "limits": chosen,
        "assets": [],
        "references": [],
        "exposed_references": [],
        "attempts": [],
        "omissions": [
            {
                "kind": "static_capture_only",
                "detail": "Scripts are not executed; rendered DOM, runtime network requests and unreferenced assets are not captured.",
            }
        ],
    }
    atomic_json(root / "manifest.json", manifest)
    capture = SourceAcquisition(root)
    for url, data, media, kind in originals:
        capture._add(url, data, media, None, kind=kind)
    return capture.manifest


class SourceAcquisition:
    def __init__(self, capture_root, *, fetcher=None):
        self.root = Path(capture_root)
        self.manifest = strict_json(safe_path(self.root, "manifest.json").read_bytes())
        if self.manifest.get("version") != 1:
            raise ValueError("Unsupported capture manifest version")
        self.limits = self.manifest["limits"]
        if set(self.limits) != set(DEFAULT_LIMITS) or any(
            type(v) is not int or v < 1 for v in self.limits.values()
        ):
            raise ValueError("Invalid capture limits")
        self.fetcher = fetcher or _fetch_http
        for asset in self.manifest["assets"]:
            self._text(asset)

    def _save(self):
        atomic_json(self.root / "manifest.json", self.manifest)

    def _asset(self, asset_id):
        for asset in self.manifest["assets"]:
            if asset["asset_id"] == asset_id:
                return asset
        raise ValueError("Unknown captured asset")

    def _text(self, asset):
        data = safe_path(self.root, asset["path"]).read_bytes()
        if len(data) != asset["bytes"] or byte_hash(data) != asset["sha256"]:
            raise ValueError("Captured original hash mismatch")
        return data.decode("utf-8", errors="replace")

    def _add(self, url, data, media, reference, *, kind="original"):
        asset_id = "asset-" + digest({"url": url, "sha256": byte_hash(data)})[:24]
        if any(a["asset_id"] == asset_id for a in self.manifest["assets"]):
            return self._asset(asset_id)
        relative = f"assets/{asset_id}.source"
        path = self.root / relative
        with path.open("xb") as handle:
            handle.write(data)
        text = data.decode("utf-8", errors="replace")
        asset = {
            "asset_id": asset_id,
            "url": url,
            "kind": kind,
            "sha256": byte_hash(data),
            "media_type": media,
            "bytes": len(data),
            "path": relative,
            "captured_utc": utc_now(),
            "depth": 0
            if reference is None
            else self._asset(reference["parent_asset_id"])["depth"] + 1,
            "parent_reference_id": None
            if reference is None
            else reference["reference_id"],
            "parent_sha256": None if reference is None else reference["parent_sha256"],
            "text_encoding": "utf-8",
            "decode_replacement": text.encode("utf-8") != data,
            "total_chars": len(text),
        }
        refs, blocks, omissions = _index(asset, text, self.limits)
        asset["embedded_blocks"] = blocks
        asset["omissions"] = omissions
        if asset["decode_replacement"]:
            asset["omissions"].append(
                {
                    "kind": "utf8_decode_replacement",
                    "detail": "Raw bytes retained; text offsets refer to UTF-8 replacement decoding.",
                }
            )
        self.manifest["assets"].append(asset)
        self.manifest["references"].extend(refs)
        self._save()
        return asset

    def _expose(self, references):
        self.manifest["exposed_references"] = sorted(
            set(self.manifest["exposed_references"])
            | {r["reference_id"] for r in references}
        )
        self._save()

    def inventory(self, reference_offset=0):
        if type(reference_offset) is not int or reference_offset < 0:
            raise ValueError("Reference offset must be a nonnegative integer")
        references = self.manifest["references"][
            reference_offset : reference_offset + 32
        ]
        self._expose(references)
        return {
            "assets": self.manifest["assets"],
            "references": references,
            "reference_offset": reference_offset,
            "reference_total": len(self.manifest["references"]),
            "reference_truncated": reference_offset + len(references)
            < len(self.manifest["references"]),
            "limits": self.limits,
            "usage": {
                "fetches": len(self.manifest["attempts"]),
                "bytes": sum(a["bytes"] for a in self.manifest["assets"]),
                "assets": len(self.manifest["assets"]),
            },
            "omissions": self.manifest["omissions"],
        }

    def _excerpt(self, asset, text, start, end):
        references = [
            r
            for r in self.manifest["references"]
            if r["parent_asset_id"] == asset["asset_id"]
            and r["start"] >= start
            and r["end"] <= end
        ]
        return {
            "asset_id": asset["asset_id"],
            "url": asset["url"],
            "sha256": asset["sha256"],
            "start": start,
            "end": end,
            "start_line": text.count("\n", 0, start) + 1,
            "end_line": text.count("\n", 0, end) + 1,
            "text": text[start:end],
            "text_sha256": byte_hash(text[start:end].encode("utf-8")),
            "references": references[:32],
            "reference_total": len(references),
            "reference_truncated": len(references) > 32,
            "offset_unit": "Unicode characters in UTF-8 decoded original",
        }

    def read(self, asset_id, start=0, max_chars=12000):
        if (
            type(start) is not int
            or start < 0
            or type(max_chars) is not int
            or not 1 <= max_chars <= self.limits["max_read_chars"]
        ):
            raise ValueError("Read window is outside capture limits")
        asset = self._asset(asset_id)
        text = self._text(asset)
        if start > len(text):
            raise ValueError("Read starts beyond original text")
        result = self._excerpt(asset, text, start, min(start + max_chars, len(text)))
        self._expose(result["references"])
        result.update(
            total_chars=len(text),
            truncated=start > 0 or result["end"] < len(text),
            omissions=asset["omissions"],
        )
        return result

    def search(self, query, asset_id=None, max_matches=6):
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 256
            or type(max_matches) is not int
            or not 1 <= max_matches <= 8
        ):
            raise ValueError("Search needs a literal bounded query and 1-8 matches")
        assets = [self._asset(asset_id)] if asset_id else self.manifest["assets"]
        matches, omitted = [], 0
        window = min(1800, self.limits["max_read_chars"] // max_matches)
        if len(query) > window:
            raise ValueError("Query is longer than the bounded search excerpt")
        for asset in assets:
            text = self._text(asset)
            for match in re.finditer(re.escape(query), text, flags=re.IGNORECASE):
                if len(matches) >= max_matches:
                    omitted += 1
                    continue
                start = max(0, match.start() - window // 2)
                end = min(len(text), max(match.end(), start + window))
                matches.append(self._excerpt(asset, text, start, end))
        self._expose([ref for item in matches for ref in item["references"]])
        return {
            "query": query,
            "matches": matches,
            "truncated": bool(omitted),
            "omissions": [{"kind": "match_limit", "omitted": omitted}]
            if omitted
            else [],
        }

    def fetch(self, reference_id):
        if reference_id not in self.manifest["exposed_references"]:
            raise ValueError(
                "Only a reference exposed by inventory/search/read may be fetched"
            )
        reference = next(
            (
                r
                for r in self.manifest["references"]
                if r["reference_id"] == reference_id
            ),
            None,
        )
        if reference is None:
            raise ValueError("Unknown source reference")
        parent = self._asset(reference["parent_asset_id"])
        original_refs, _, _ = _index(parent, self._text(parent), self.limits)
        if reference not in original_refs or not reference["fetchable"]:
            raise ValueError("Reference is not bound to an allowed original parent")
        attempts = [
            a for a in self.manifest["attempts"] if a["url"] == reference["url"]
        ]
        for asset in self.manifest["assets"]:
            captured_reference = next(
                (
                    r
                    for r in self.manifest["references"]
                    if r["reference_id"] == asset["parent_reference_id"]
                ),
                None,
            )
            # _add durably saves original bytes before the attempt receipt is
            # marked completed. Recover that window without another HTTP call.
            if captured_reference and captured_reference["url"] == reference["url"]:
                captured_parent = self._asset(captured_reference["parent_asset_id"])
                bound_refs, _, _ = _index(
                    captured_parent, self._text(captured_parent), self.limits
                )
                if (
                    captured_reference not in bound_refs
                    or asset["parent_sha256"] != captured_parent["sha256"]
                ):
                    raise ValueError("Captured asset parent reference changed")
                captured_for_url = True
            else:
                captured_for_url = False
            if asset["url"] == reference["url"] or captured_for_url:
                self._text(asset)
                for attempt in attempts:
                    if attempt["status"] not in {"captured", "captured_recovered"}:
                        attempt.update(
                            status="captured_recovered",
                            asset_id=asset["asset_id"],
                            final_url=asset["url"],
                            sha256=asset["sha256"],
                            bytes=asset["bytes"],
                            recovered_utc=utc_now(),
                        )
                self._save()
                return {
                    "status": "reused",
                    "asset": asset,
                    "reference_id": reference_id,
                }
        if attempts:
            raise ValueError("This URL already has a recorded fetch attempt")
        remaining = self.limits["max_total_bytes"] - sum(
            a["bytes"] for a in self.manifest["assets"]
        )
        if (
            len(self.manifest["attempts"]) >= self.limits["max_fetches"]
            or len(self.manifest["assets"]) >= self.limits["max_assets"]
            or remaining < 1
        ):
            raise ValueError("Capture fetch/asset/byte budget exhausted")
        bound = min(remaining, self.limits["max_asset_bytes"])
        attempt = {
            "reference_id": reference_id,
            "url": reference["url"],
            "parent_sha256": parent["sha256"],
            "started_utc": utc_now(),
            "status": "started",
            "max_bytes": bound,
        }
        self.manifest["attempts"].append(attempt)
        self._save()
        try:
            response = self.fetcher(reference["url"], bound)
            url, data = (
                _url(
                    response["url"],
                    allow_http=urlsplit(reference["url"]).scheme == "http",
                ),
                response["body"],
            )
            media = _media(response.get("media_type"), url)
            if not _same_origin_or_upgrade(reference["url"], url):
                raise ValueError("Response redirected outside the referenced origin")
            if (
                response["status"] != 200
                or not isinstance(data, bytes)
                or len(data) > bound
                or media not in TEXT_TYPES
            ):
                raise ValueError(
                    "Response status, media type or size is outside capture limits"
                )
            asset = self._add(url, data, media, reference)
            attempt.update(
                status="captured",
                asset_id=asset["asset_id"],
                response_status=response["status"],
                final_url=url,
                sha256=asset["sha256"],
                bytes=len(data),
            )
        except Exception as exc:
            attempt.update(
                status="failed", error_type=type(exc).__name__, error=str(exc)[:500]
            )
            self.manifest["omissions"].append(
                {
                    "kind": "fetch_failed",
                    "reference_id": reference_id,
                    "error_type": type(exc).__name__,
                }
            )
            raise ValueError("Source fetch failed: " + str(exc)[:500]) from exc
        finally:
            attempt["completed_utc"] = utc_now()
            self._save()
        return {"status": "captured", "asset": asset, "reference_id": reference_id}
