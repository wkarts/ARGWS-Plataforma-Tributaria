"""Falhas na validação devem impedir a promoção da release."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from verify_collector_assets import ARTIFACTS, verify


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

    def test_accepts_valid_architectures_and_digests(self):
        elf = bytearray(512)
        elf[:6] = b"\x7fELF\x02\x01"
        elf[18:20] = b"\x3e\x00"
        self.artifact("linux", bytes(elf))
        self.assertEqual(verify(self.directory, "linux"), hashlib.sha256(elf).hexdigest())
        for entry in self.directory.iterdir():
            entry.unlink()
        pe = bytearray(512)
        pe[:2] = b"MZ"
        pe[0x3C:0x40] = (0x80).to_bytes(4, "little")
        pe[0x80:0x86] = b"PE\0\0\x64\x86"
        self.artifact("windows", bytes(pe))
        self.assertEqual(verify(self.directory, "windows"), hashlib.sha256(pe).hexdigest())

    def test_rejects_modified_and_extra_assets(self):
        elf = bytearray(512)
        elf[:6] = b"\x7fELF\x02\x01"
        elf[18:20] = b"\x3e\x00"
        binary = self.artifact("linux", bytes(elf))
        binary.write_bytes(bytes(elf) + b"modified")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(self.directory, "linux")
        self.artifact("linux", bytes(elf))
        (self.directory / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "inesperado"):
            verify(self.directory, "linux")


if __name__ == "__main__":
    unittest.main()
