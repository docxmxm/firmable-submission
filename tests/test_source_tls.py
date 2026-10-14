"""Verified source transport without network calls or source-specific fixtures."""

import io
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError
from urllib.request import HTTPSHandler

import certifi

from firmable_runtime.common import byte_hash
from firmable_runtime.source_acquisition import (
    SourceAcquisition,
    _fetch_http,
    prepare_capture,
)


class SourceTlsTests(unittest.TestCase):
    def test_transport_uses_public_bundle_with_hostname_and_chain_verification(self):
        body = io.BytesIO(b"original document")
        response = MagicMock()
        response.url = "https://publisher.example/definition"
        response.status = 200
        response.headers = {"Content-Type": "text/plain"}
        response.read1.side_effect = body.read1
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with (
            patch(
                "firmable_runtime.source_acquisition.ssl.create_default_context",
                wraps=ssl.create_default_context,
            ) as create_context,
            patch(
                "firmable_runtime.source_acquisition.build_opener",
                return_value=opener,
            ) as build,
        ):
            result = _fetch_http(response.url, 100)
        self.assertEqual(result["body"], b"original document")
        create_context.assert_called_once_with(cafile=certifi.where())
        handler = next(h for h in build.call_args.args if isinstance(h, HTTPSHandler))
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handler._context.check_hostname)
        self.assertGreater(handler._context.cert_store_stats()["x509_ca"], 0)
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_certificate_failure_is_saved_and_never_retried_or_downgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "original.html"
            source.write_text('<a href="/definition">Definitions</a>')
            prepare_capture(
                root / "capture",
                [
                    {
                        "url": "https://publisher.example/index",
                        "path": source,
                        "sha256": byte_hash(source.read_bytes()),
                        "media_type": "text/html",
                    }
                ],
            )
            capture = SourceAcquisition(root / "capture")
            reference = capture.inventory()["references"][0]["reference_id"]
            opener = MagicMock()
            opener.open.side_effect = URLError(
                ssl.SSLCertVerificationError("certificate verify failed")
            )
            with patch(
                "firmable_runtime.source_acquisition.build_opener",
                return_value=opener,
            ) as build:
                with self.assertRaisesRegex(ValueError, "certificate verify failed"):
                    capture.fetch(reference)
                resumed = SourceAcquisition(root / "capture")
                with self.assertRaisesRegex(ValueError, "recorded fetch attempt"):
                    resumed.fetch(reference)
            opener.open.assert_called_once()
            build.assert_called_once()
            handler = next(
                h for h in build.call_args.args if isinstance(h, HTTPSHandler)
            )
            self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(handler._context.check_hostname)
            self.assertEqual(len(resumed.manifest["assets"]), 1)
            self.assertEqual(resumed.manifest["attempts"][0]["status"], "failed")
            self.assertEqual(resumed.manifest["attempts"][0]["error_type"], "URLError")

    def test_missing_bundle_fails_before_network_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "firmable_runtime.source_acquisition.certifi.where",
                    return_value=str(Path(tmp) / "missing.pem"),
                ),
                patch("firmable_runtime.source_acquisition.build_opener") as build,
            ):
                with self.assertRaises(FileNotFoundError):
                    _fetch_http("https://publisher.example/definition", 100)
            build.assert_not_called()
