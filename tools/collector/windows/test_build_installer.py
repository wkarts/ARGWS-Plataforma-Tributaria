"""Validação dos parâmetros que entram em diretivas do instalador Windows."""

import tempfile
import unittest
from pathlib import Path

from build_installer import compiler_args, validate_label, validate_path, version_numbers, version_resource


class InstallerBrandTests(unittest.TestCase):
    def test_brand_values_are_safe_for_inno_and_windows_shortcuts(self):
        for value in ("Fiscal; [Run]", 'Marca"Injeção', "Marca\n[Code]", r"Marca\Subpasta", " Marcação"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_label(value, "Marca")
        self.assertEqual(validate_label("Contábil & Cia. (SP)", "Marca"), "Contábil & Cia. (SP)")

    def test_icon_must_be_ico_and_paths_do_not_inject_directives(self):
        with tempfile.TemporaryDirectory() as directory:
            icon = Path(directory) / "marca.ico"
            icon.write_bytes(b"\x00\x00\x01\x00" + b"0" * 12)
            self.assertEqual(validate_path(icon, icon=True), icon.resolve())
            icon.with_name("marca;maliciosa.ico").write_bytes(icon.read_bytes())
            with self.assertRaises(ValueError):
                validate_path(icon.with_name("marca;maliciosa.ico"), icon=True)
            icon.write_bytes(b"not ico")
            with self.assertRaises(ValueError):
                validate_path(icon, icon=True)

    def test_version_info_and_compiler_arguments_use_explicit_values(self):
        self.assertEqual(version_numbers("0.2.0"), (0, 2, 0, 0))
        with self.assertRaises(ValueError):
            version_numbers("0.2.0\n[Run]")
        info = version_resource("0.2.0", "Marca do Cliente", "Empresa Cliente")
        self.assertIn("'ProductName', 'Marca do Cliente'", info)
        self.assertIn("'CompanyName', 'Empresa Cliente'", info)
        args = compiler_args(Path("C:/Inno/ISCC.exe"), Path("C:/repo"), "0.2.0",
                             "Marca do Cliente", "Empresa Cliente", Path("C:/repo/marca.ico"))
        self.assertIn("/DAPP_DISPLAY_NAME=Marca do Cliente", args)
        self.assertIn(f"/DAPP_ICON_PATH={Path('C:/repo/marca.ico')}", args)


if __name__ == "__main__":
    unittest.main()
