"""Do not invent version tags, choose drafts or rely on external services in CI."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import release_catalog as catalog


def release(tag: str, *, draft: bool = False, prerelease: bool = False,
            completed: bool = True, date: str = "2026-10-27T10:00:00Z") -> dict:
    return {
        "tag_name": tag, "draft": draft, "prerelease": prerelease,
        "published_at": date,
        "assets": ([{"name": "IMAGES.txt"}, {"name": "SHA256SUMS.txt"}]
                   if completed else [{"name": "IMAGES.txt"}]),
    }


class ReleaseCatalogTests(unittest.TestCase):
    def test_stable_latest_semver_ignores_drafts_previews_incomplete_and_alias(self):
        rows = [
            release("v0.3.9"),
            release("v0.3.10"),
            release("v0.5.0", draft=True),
            release("v0.4.0", completed=False),
            release("v0.3.11-develop.012345abcdef", prerelease=True),
            release("pre-release", prerelease=True),
            release("v0.2.0"),
        ]
        result = catalog.published_versions(rows, "production")
        self.assertEqual(result["latest"], "v0.3.10")
        self.assertEqual(result["versions"], ["v0.3.10", "v0.3.9", "v0.2.0"])
        self.assertTrue(all(value.startswith("v") for value in result["versions"]))

    def test_develop_lists_channel_fixed_previews_and_stable_versions(self):
        rows = [
            release("v0.3.0-develop.123456789abc", prerelease=True,
                    date="2026-10-24T10:00:00Z"),
            release("v0.3.0-develop.012345abcdef", prerelease=True,
                    date="2026-10-26T10:00:00Z"),
            release("v0.3.0"),
            release("v0.2.0-develop.111111111111", prerelease=True, draft=True),
        ]
        result = catalog.published_versions(rows, "develop")
        self.assertEqual(result["latest"], "develop")
        self.assertEqual(result["versions"], [
            "develop", "sha-012345abcdef", "sha-123456789abc", "v0.3.0"
        ])

    def test_github_http_response_is_queried_without_exposing_credentials(self):
        response = [release("v0.3.1"), release("v0.3.0")]
        with patch.object(catalog, "urlopen",
                          return_value=io.BytesIO(json.dumps(response).encode())) as request:
            result = catalog.load_catalog("wkarts", "production")
        self.assertEqual(result["owner"], "wkarts")
        self.assertEqual(result["source"], "github_releases")
        self.assertEqual(result["latest"], "v0.3.1")
        url = request.call_args.args[0].full_url
        self.assertTrue(url.startswith(
            "https://api.github.com/repos/wkarts/ARGWS-Plataforma-Tributaria/releases?"
        ))
        self.assertNotIn("token", url.lower())

    def test_catalog_paginates_until_last_page(self):
        first = [release(f"v0.1.{index}") for index in range(100)]
        last = [release("v0.2.0")]
        responses = [io.BytesIO(json.dumps(item).encode()) for item in (first, last)]
        with patch.object(catalog, "urlopen", side_effect=responses) as call:
            result = catalog.load_catalog("wkarts", "production")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["count"], 101)
        self.assertEqual(result["latest"], "v0.2.0")

    def test_network_failures_are_reported_and_cannot_select_latest(self):
        with patch.object(catalog, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(catalog.ReleaseCatalogError, "Não foi possível"):
                catalog.load_catalog("wkarts", "production")
        error = HTTPError("https://api.github.com", 403, "rate limited", {}, None)
        with patch.object(catalog, "urlopen", side_effect=error):
            with self.assertRaisesRegex(catalog.ReleaseCatalogError, "HTTP 403"):
                catalog.load_catalog("wkarts", "production")

    def test_rejects_untrusted_owner_and_invalid_channel(self):
        with patch.object(catalog, "urlopen") as request:
            for bad in ("../admin", "https://host", "a@b", "CAPS", ""):
                with self.subTest(owner=bad), self.assertRaises(catalog.ReleaseCatalogError):
                    catalog.load_catalog(bad, "production")
            request.assert_not_called()
        with self.assertRaises(catalog.ReleaseCatalogError):
            catalog.published_versions([], "unknown")


if __name__ == "__main__":
    unittest.main()
