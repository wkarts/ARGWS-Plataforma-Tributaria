"""Regression: a draft release must not be expected to create its Git tag.

Exercise the production shell publisher with isolated fake GitHub and OCI tools.
No network, release, registry or secret is used by these tests.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHA = "5a473020082d6a05359e025ffcf538aacdcaa7fc"
DIGEST = "sha256:" + "a" * 64


class PreviewTagTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        assets = self.root / "release-files"
        assets.mkdir()
        self.assets = assets
        refs = (
            "ghcr.io/wkarts/reforma-tributaria-api@" + DIGEST,
            "ghcr.io/wkarts/reforma-tributaria-web@" + DIGEST,
        )
        imagefile = assets / "IMAGES.txt"
        imagefile.write_text("\n".join(refs) + "\n", encoding="ascii")
        (assets / "SHA256SUMS.txt").write_text(
            hashlib.sha256(imagefile.read_bytes()).hexdigest() + "  IMAGES.txt\n",
            encoding="ascii",
        )
        self.fake_bin(binary / "docker", """
            #!/usr/bin/env bash
            if [[ "$1" == "buildx" && "$2" == "imagetools" && "$3" == "inspect" ]]; then
              echo "Name: $4"
              echo "Digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
              exit 0
            fi
            exit 99
        """)
        self.fake_bin(binary / "gh", r'''
            #!/usr/bin/env python3
            import json
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            state = Path(os.environ["GH_MOCK_STATE"])
            target = os.environ["GITHUB_SHA"]
            tag = "v" + os.environ["VERSION"] + "-develop." + target[:12]
            ref = state / "tag.txt"
            events = state / "events.txt"

            def event(value):
                with events.open("a", encoding="utf-8") as writer:
                    writer.write(value + "\n")

            if args[:1] == ["api"]:
                if args[1:3] == ["--method", "POST"] and args[3] == "repos/wkarts/ARGWS-Plataforma-Tributaria/git/refs":
                    fields = dict(args[i + 1].split("=", 1) for i in range(4, len(args) - 1) if args[i] == "-f")
                    if fields.get("ref") != "refs/tags/" + tag or fields.get("sha") != target:
                        sys.exit(91)
                    if ref.exists():
                        print("HTTP 422 already exists", file=sys.stderr)
                        sys.exit(1)
                    ref.write_text(target, encoding="ascii")
                    event("tag-created")
                    print("{}")
                    sys.exit(0)
                if args[1] == "repos/wkarts/ARGWS-Plataforma-Tributaria/git/ref/tags/" + tag:
                    if not ref.exists():
                        print("HTTP 404 Not Found", file=sys.stderr)
                        sys.exit(1)
                    print(json.dumps({"object": {"type": "commit", "sha": ref.read_text(encoding="ascii")}}))
                    sys.exit(0)
            if args[:2] == ["release", "view"]:
                print("release not found", file=sys.stderr)
                sys.exit(1)
            if args[:2] == ["release", "create"]:
                if "--verify-tag" not in args:
                    print("Missing --verify-tag", file=sys.stderr)
                    sys.exit(90)
                if not ref.exists() or ref.read_text(encoding="ascii") != target:
                    print("Draft created without validated tag", file=sys.stderr)
                    sys.exit(92)
                event("draft-create-attempt")
                # Stop at the first external publication call; none is performed.
                sys.exit(47)
            print("Unexpected gh command: " + repr(args), file=sys.stderr)
            sys.exit(94)
        ''')
        self.env = os.environ.copy() | {
            "PATH": str(binary) + os.pathsep + os.environ.get("PATH", ""),
            "GH_MOCK_STATE": str(self.root),
            "GH_TOKEN": "testing-only",
            "GITHUB_REPOSITORY": "wkarts/ARGWS-Plataforma-Tributaria",
            "GITHUB_SHA": SHA,
            "VERSION": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "CHANNEL": "develop",
        }

    @staticmethod
    def fake_bin(path: Path, value: str) -> None:
        path.write_text(textwrap.dedent(value).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    def invoke(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / "publish_release.sh"), str(self.assets)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_tag_is_created_before_draft_and_retries_are_idempotent(self) -> None:
        result = self.invoke()
        self.assertEqual(result.returncode, 47, result.stdout + result.stderr)
        self.assertEqual((self.root / "tag.txt").read_text(), SHA)
        self.assertEqual((self.root / "events.txt").read_text().splitlines(),
                         ["tag-created", "draft-create-attempt"])
        again = self.invoke()
        self.assertEqual(again.returncode, 47, again.stdout + again.stderr)
        self.assertEqual((self.root / "events.txt").read_text().splitlines(),
                         ["tag-created", "draft-create-attempt", "draft-create-attempt"])

    def test_tag_pointing_to_another_commit_is_never_overwritten(self) -> None:
        other = "b" * 40
        (self.root / "tag.txt").write_text(other, encoding="ascii")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Tag de prévia já pertence a outro commit.", result.stderr)
        self.assertEqual((self.root / "tag.txt").read_text(), other)
        self.assertFalse((self.root / "events.txt").exists())


if __name__ == "__main__":
    unittest.main()
