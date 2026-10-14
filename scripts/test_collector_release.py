"""Distribution checks cover the actual GUI installer and Linux bundle."""
import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from verify_collector_assets import ARTIFACTS, LINUX_FILES, verify


def linux_archive(*, include_all: bool = True, add_link: bool = False) -> bytes:
    elf = bytearray(512)
    elf[:6] = b"\x7fELF\x02\x01"
    elf[18:20] = b"\x3e\x00"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name in sorted(LINUX_FILES if include_all else LINUX_FILES - {"fiscal-xml-collector/README.md"}):
            content = bytes(elf) if name.endswith(("-linux-x64",)) else b"fixture"
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if add_link:
            link = tarfile.TarInfo("fiscal-xml-collector/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
    return output.getvalue()


def windows_setup() -> bytes:
    pe = bytearray(512)
    pe[:2] = b"MZ"
    pe[0x3C:0x40] = (0x80).to_bytes(4, "little")
    pe[0x80:0x86] = b"PE\0\0\x4c\x01"  # Inno bootstrap may be 32 bit, with an x64 payload.
    return bytes(pe)


class CollectorReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def artifact(self, platform: str, payload: bytes) -> Path:
        name = ARTIFACTS[platform]
        (self.directory / name).write_bytes(payload)
        (self.directory / "SHA256SUMS.txt").write_text(
            f"{hashlib.sha256(payload).hexdigest()}  {name}\n", encoding="ascii"
        )
        return self.directory / name

    def test_valid_gui_packages(self):
        linux = linux_archive()
        self.artifact("linux", linux)
        self.assertEqual(verify(self.directory, "linux"), hashlib.sha256(linux).hexdigest())
        for entry in self.directory.iterdir():
            entry.unlink()
        windows = windows_setup()
        self.artifact("windows", windows)
        self.assertEqual(verify(self.directory, "windows"), hashlib.sha256(windows).hexdigest())

    def test_rejects_modified_and_extra_assets(self):
        bundle = linux_archive()
        binary = self.artifact("linux", bundle)
        binary.write_bytes(bundle + b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(self.directory, "linux")
        self.artifact("linux", bundle)
        (self.directory / "loose-cli.exe").write_bytes(b"MZ")
        with self.assertRaisesRegex(ValueError, "inesperado"):
            verify(self.directory, "linux")

    def test_rejects_missing_gui_and_links_inside_linux_bundle(self):
        self.artifact("linux", linux_archive(include_all=False))
        with self.assertRaisesRegex(ValueError, "incompleto"):
            verify(self.directory, "linux")
        self.artifact("linux", linux_archive(add_link=True))
        with self.assertRaisesRegex(ValueError, "link"):
            verify(self.directory, "linux")


if __name__ == "__main__":
    unittest.main()
