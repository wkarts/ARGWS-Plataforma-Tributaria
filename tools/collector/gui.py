#!/usr/bin/env python3
"""Interface gráfica local para perfis, coleta e instalação do serviço."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import profiles


DEFAULT_BRAND = {"display_name": "Gestão Fiscal", "short_name": "Fiscal",
                 "primary_color": "#255896", "secondary_color": "#102743"}
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def branding(home: Path) -> dict[str, str]:
    """Marca opcional, com PNG exclusivamente dentro da instalação local."""
    result = dict(DEFAULT_BRAND)
    path = home / "branding.json"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16_384:
        return result
    try:
        supplied = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            return result
        for field in ("display_name", "short_name"):
            value = supplied.get(field)
            if isinstance(value, str) and 1 <= len(value.strip()) <= 80 and "\n" not in value:
                result[field] = value.strip()
        for field in ("primary_color", "secondary_color"):
            value = supplied.get(field)
            if isinstance(value, str) and COLOR.fullmatch(value):
                result[field] = value
        logo = supplied.get("logo_png")
        if isinstance(logo, str) and logo and not Path(logo).is_absolute():
            destination = (home / logo).resolve()
            if (destination.is_relative_to(home.resolve()) and destination.is_file()
                    and destination.stat().st_size <= 2 * 1024 * 1024):
                with destination.open("rb") as image:
                    if image.read(8) == b"\x89PNG\r\n\x1a\n":
                        result["logo_png"] = str(destination)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return result


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        filename = ("fiscal-xml-worker-windows-x64.exe" if os.name == "nt"
                    else "fiscal-xml-worker-linux-x64")
        candidate = Path(sys.executable).resolve().parent / filename
        if not candidate.is_file():
            raise FileNotFoundError(f"Worker não encontrado: {candidate.name}")
        return [str(candidate)]
    return [sys.executable, str(Path(__file__).with_name("worker.py"))]


def service_command(action: str, home: Path) -> list[str]:
    if action not in {"Install", "Start", "Stop", "Status"}:
        raise ValueError("Ação de serviço inválida")
    package_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    if os.name == "nt":
        script = package_dir / "manage-service.ps1"
        if not script.is_file() and not getattr(sys, "frozen", False):
            script = package_dir / "windows" / "manage-service.ps1"
        if not script.is_file():
            raise FileNotFoundError("Script de serviço Windows não encontrado na instalação")
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(script), "-Action", action, "-Home", str(home)]
    service = "fiscal-xml-self.service"
    if action == "Install":
        script = package_dir / "self-install.sh"
        if not script.is_file() and not getattr(sys, "frozen", False):
            script = package_dir / "systemd" / "self-install.sh"
        if not script.is_file():
            raise FileNotFoundError("Instalador do serviço Linux não encontrado")
        if not getattr(sys, "frozen", False):
            raise ValueError("Instale o serviço a partir do pacote binário Linux")
        command = ["bash", str(script), str(package_dir / "fiscal-xml-worker-linux-x64"),
                   str(Path(sys.executable).resolve()), str(home)]
        return command if os.geteuid() == 0 else ["pkexec", *command]
    verb = {"Start": "start", "Stop": "stop", "Status": "is-active"}[action]
    command = ["systemctl", verb, service]
    return command if action == "Status" or os.geteuid() == 0 else ["pkexec", *command]


def run_command(command: list[str], timeout: int = 120) -> tuple[int, str]:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, check=False,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


class CollectorWindow(tk.Tk):
    def __init__(self, home: Path):
        super().__init__()
        self.home = profiles.require_home(home)
        self.brand = branding(self.home)
        self.title(self.brand["display_name"] + " • Coleta fiscal")
        self.geometry("980x760")
        self.minsize(810, 630)
        self.configure(background="#f5f7fa")
        self.logo = None
        self.vars: dict[str, tk.StringVar | tk.BooleanVar] = {
            "name": tk.StringVar(), "cnpj": tk.StringVar(), "company_id": tk.StringVar(),
            "server_url": tk.StringVar(), "token": tk.StringVar(), "poll_seconds": tk.StringVar(value="30"),
            "stable_seconds": tk.StringVar(value="10"), "timeout_seconds": tk.StringVar(value="30"),
            "max_file_bytes": tk.StringVar(value=str(10 * 1024 * 1024)),
            "batch_limit": tk.StringVar(value="100"),
            "retry_min_seconds": tk.StringVar(value="10"), "retry_max_seconds": tk.StringVar(value="3600"),
            "sefaz_max_spool_bytes": tk.StringVar(value=str(2 * 1024 * 1024 * 1024)),
            "recursive": tk.BooleanVar(value=True), "signed_only": tk.BooleanVar(value=True),
            "enabled": tk.BooleanVar(value=True), "sefaz": tk.BooleanVar(value=False),
            "environment": tk.StringVar(value="production"), "cert_pem": tk.StringVar(),
            "key_pem": tk.StringVar(),
        }
        self._build()
        self.refresh()

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        if "logo_png" in self.brand:
            try:
                self.logo = tk.PhotoImage(file=self.brand["logo_png"])
                self.logo = self.logo.subsample(max(1, self.logo.width() // 64), max(1, self.logo.height() // 64))
                ttk.Label(header, image=self.logo).pack(side="left", padx=(0, 12))
            except tk.TclError:
                self.logo = None
        ttk.Label(header, text=self.brand["display_name"], font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(header, text="Configuração local de empresas e documentos fiscais",
                  foreground="#4b5563").pack(anchor="w")

        panes = ttk.PanedWindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=12)
        side = ttk.Frame(panes, padding=6)
        details = ttk.Frame(panes, padding=6)
        panes.add(side, weight=1)
        panes.add(details, weight=3)
        ttk.Label(side, text="Empresas cadastradas", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.tree = ttk.Treeview(side, columns=("cnpj", "state"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Nome")
        self.tree.heading("cnpj", text="CNPJ")
        self.tree.heading("state", text="Ativo")
        self.tree.column("#0", width=170)
        self.tree.column("cnpj", width=130)
        self.tree.column("state", width=55)
        self.tree.pack(fill="both", expand=True, pady=(6, 8))
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        ttk.Button(side, text="Novo perfil", command=self.new_profile).pack(fill="x")
        ttk.Button(side, text="Atualizar lista", command=self.refresh).pack(fill="x", pady=5)

        tabs = ttk.Notebook(details)
        tabs.pack(fill="both", expand=True)
        basic = ttk.Frame(tabs, padding=10)
        sefaz = ttk.Frame(tabs, padding=10)
        tuning = ttk.Frame(tabs, padding=10)
        tabs.add(basic, text="Empresa e API")
        tabs.add(sefaz, text="Distribuição NF-e")
        tabs.add(tuning, text="Coleta e tentativas")
        self._entry(basic, 0, "Nome", "name")
        self._entry(basic, 1, "CNPJ", "cnpj")
        self._entry(basic, 2, "UUID da empresa na aplicação", "company_id")
        self._entry(basic, 3, "URL base HTTPS da API", "server_url")
        self._entry(basic, 4, "Token de autenticação (vazio preserva)", "token", secret=True)
        self._entry(basic, 5, "Intervalo de coleta (segundos)", "poll_seconds")
        ttk.Label(basic, text="Pastas XML (uma por linha; caminhos absolutos)").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.roots = tk.Text(basic, height=5, wrap="none")
        self.roots.grid(row=7, column=0, columnspan=2, sticky="nsew", pady=4)
        ttk.Button(basic, text="Adicionar pasta…", command=self.add_root).grid(row=7, column=2, sticky="n")
        basic.columnconfigure(1, weight=1)
        basic.rowconfigure(7, weight=1)
        options = ttk.Frame(basic)
        options.grid(row=8, column=0, columnspan=3, sticky="w", pady=8)
        for key, label in (("enabled", "Ativo"), ("recursive", "Incluir subpastas"),
                           ("signed_only", "Somente com elemento Signature")):
            ttk.Checkbutton(options, text=label, variable=self.vars[key]).pack(side="left", padx=(0, 12))
        ttk.Label(basic, text="Os XMLs originais permanecem nas pastas. A presença de Signature não prova autenticidade.",
                  wraplength=540, foreground="#4b5563").grid(row=9, column=0, columnspan=3, sticky="w")

        ttk.Checkbutton(sefaz, text="Habilitar Distribuição NF-e por NSU com certificado A1 em PEM",
                        variable=self.vars["sefaz"]).grid(row=0, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(sefaz, text="Ambiente").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(sefaz, textvariable=self.vars["environment"],
                     values=("production", "homologation"), state="readonly").grid(row=1, column=1, sticky="ew")
        self._entry(sefaz, 2, "Certificado PEM", "cert_pem", browse_file=True)
        self._entry(sefaz, 3, "Chave PEM", "key_pem", browse_file=True)
        self._entry(sefaz, 4, "Limite do spool SEFAZ (bytes)", "sefaz_max_spool_bytes")
        ttk.Label(sefaz, text="A chave privada fica no cliente. A consulta atual cobre somente NF-e; resumos não viram notas completas.",
                  wraplength=530, foreground="#4b5563").grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        sefaz.columnconfigure(1, weight=1)

        for row, (label, key) in enumerate((
            ("Aguardar estabilidade do XML (segundos)", "stable_seconds"),
            ("Timeout HTTPS (segundos)", "timeout_seconds"),
            ("Limite por XML (bytes, até 10 MiB)", "max_file_bytes"),
            ("Arquivos por lote", "batch_limit"),
            ("Intervalo mínimo de nova tentativa (segundos)", "retry_min_seconds"),
            ("Intervalo máximo de nova tentativa (segundos)", "retry_max_seconds"),
        )):
            self._entry(tuning, row, label, key)
        ttk.Label(tuning, text="Ajustes são validados nos limites do coletor e mantidos ao editar uma empresa.",
                  foreground="#4b5563", wraplength=530).grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
        tuning.columnconfigure(1, weight=1)

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        for label, callback in (("Salvar perfil", self.save), ("Validar", self.validate),
                                ("Coletar agora", self.collect), ("Status", self.status),
                                ("Instalar serviço", lambda: self.service("Install")),
                                ("Iniciar serviço", lambda: self.service("Start")),
                                ("Parar serviço", lambda: self.service("Stop")),
                                ("Status serviço", lambda: self.service("Status"))):
            ttk.Button(actions, text=label, command=callback).pack(side="left", padx=2, pady=5)
        result = ttk.LabelFrame(outer, text="Diagnóstico", padding=5)
        result.pack(fill="both", expand=True)
        self.output = tk.Text(result, height=6, state="disabled", wrap="word", font=("Consolas", 9))
        self.output.pack(fill="both", expand=True)
        self.show("Cadastre uma empresa com o UUID e token emitidos pela aplicação local.")

    def _entry(self, parent: ttk.Frame, row: int, label: str, key: str,
               *, secret: bool = False, browse_file: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(parent, textvariable=self.vars[key], show="•" if secret else "").grid(
            row=row, column=1, sticky="ew", pady=5)
        if browse_file:
            ttk.Button(parent, text="Escolher…", command=lambda: self.choose_file(key)).grid(
                row=row, column=2, padx=5)

    def choose_file(self, key: str) -> None:
        selected = filedialog.askopenfilename()
        if selected:
            self.vars[key].set(selected)

    def add_root(self) -> None:
        selected = filedialog.askdirectory()
        if selected:
            current = self.roots.get("1.0", "end").strip()
            self.roots.insert("end", ("\n" if current else "") + selected)

    def show(self, message: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", message)
        self.output.configure(state="disabled")

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        try:
            for item in profiles.list_profiles(self.home):
                self.tree.insert("", "end", iid=item["company_id"], text=item["name"],
                                 values=(item["cnpj"], "Sim" if item["enabled"] else "Não"))
        except (OSError, ValueError) as exc:
            self.show(f"Catálogo indisponível: {type(exc).__name__}")

    def new_profile(self) -> None:
        defaults = {"poll_seconds": "30", "stable_seconds": "10", "timeout_seconds": "30",
                    "max_file_bytes": str(10 * 1024 * 1024), "batch_limit": "100",
                    "retry_min_seconds": "10", "retry_max_seconds": "3600",
                    "sefaz_max_spool_bytes": str(2 * 1024 * 1024 * 1024)}
        for key, var in self.vars.items():
            if isinstance(var, tk.BooleanVar):
                var.set(key != "sefaz")
            else:
                var.set("production" if key == "environment" else defaults.get(key, ""))
        self.roots.delete("1.0", "end")
        self.tree.selection_remove(*self.tree.selection())

    def _selected(self, _event: object) -> None:
        current = self.tree.selection()
        if not current:
            return
        item = next((row for row in profiles.list_profiles(self.home) if row["company_id"] == current[0]), None)
        if item is None:
            return
        try:
            config = profiles.load_config(self.home, item["company_id"])
        except (OSError, ValueError) as exc:
            self.show(f"Configuração indisponível: {type(exc).__name__}")
            return
        for key in ("name", "cnpj", "company_id"):
            self.vars[key].set(item[key])
        for key in ("server_url", "poll_seconds", "recursive", "signed_only",
                    "stable_seconds", "timeout_seconds", "max_file_bytes", "batch_limit",
                    "retry_min_seconds", "retry_max_seconds"):
            self.vars[key].set(config[key])
        self.vars["enabled"].set(item["enabled"])
        self.vars["token"].set("")  # Segredos nunca são exibidos de volta.
        self.roots.delete("1.0", "end")
        self.roots.insert("1.0", "\n".join(config["roots"]))
        sefaz = config["sefaz_nfe"]
        self.vars["sefaz"].set(bool(sefaz))
        self.vars["environment"].set(sefaz["environment"] if sefaz else "production")
        self.vars["sefaz_max_spool_bytes"].set(
            sefaz["max_spool_bytes"] if sefaz else 2 * 1024 * 1024 * 1024)
        for key in ("cert_pem", "key_pem"):
            self.vars[key].set(sefaz[key] if sefaz else "")

    def save(self) -> None:
        try:
            sefaz = ({"cnpj": self.vars["cnpj"].get().strip().upper(),
                      "environment": self.vars["environment"].get(),
                      "cert_pem": self.vars["cert_pem"].get().strip(),
                      "key_pem": self.vars["key_pem"].get().strip(),
                      "max_spool_bytes": int(self.vars["sefaz_max_spool_bytes"].get())}
                     if self.vars["sefaz"].get() else None)
            item = profiles.save_profile(self.home,
                company_id=self.vars["company_id"].get().strip(),
                cnpj=self.vars["cnpj"].get(), name=self.vars["name"].get(),
                server_url=self.vars["server_url"].get().strip(),
                roots=[line.strip() for line in self.roots.get("1.0", "end").splitlines() if line.strip()],
                token=self.vars["token"].get().strip() or None,
                enabled=bool(self.vars["enabled"].get()),
                recursive=bool(self.vars["recursive"].get()),
                signed_only=bool(self.vars["signed_only"].get()),
                poll_seconds=int(self.vars["poll_seconds"].get()), sefaz_nfe=sefaz,
                stable_seconds=int(self.vars["stable_seconds"].get()),
                timeout_seconds=int(self.vars["timeout_seconds"].get()),
                max_file_bytes=int(self.vars["max_file_bytes"].get()),
                batch_limit=int(self.vars["batch_limit"].get()),
                retry_min_seconds=int(self.vars["retry_min_seconds"].get()),
                retry_max_seconds=int(self.vars["retry_max_seconds"].get()))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Perfil inválido", str(exc))
            return
        self.vars["token"].set("")
        self.refresh()
        self.tree.selection_set(item["company_id"])
        self.show("Perfil salvo. Use Validar antes de iniciar o serviço.")

    def _run(self, command: list[str], timeout: int = 120) -> None:
        self.show("Executando operação local…")

        def execute() -> None:
            try:
                code, output = run_command(command, timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                code, output = 1, type(exc).__name__
            self.after(0, lambda: self.show(("Concluído" if code == 0 else "Falha") + ":\n" + output))

        threading.Thread(target=execute, daemon=True).start()

    def _worker(self, operation: str) -> None:
        selected = self.tree.selection()
        arguments = [*worker_command(), "--home", str(self.home), operation]
        if selected:
            arguments.extend(["--company-id", selected[0]])
        self._run(arguments, timeout=360 if operation == "once" else 120)

    def validate(self) -> None:
        try:
            self._worker("check")
        except OSError as exc:
            self.show(str(exc))

    def status(self) -> None:
        try:
            self._worker("status")
        except OSError as exc:
            self.show(str(exc))

    def collect(self) -> None:
        if messagebox.askyesno("Coletar documentos", "Consultar fontes habilitadas e enviar XMLs agora?",
                               icon="question"):
            try:
                self._worker("once")
            except OSError as exc:
                self.show(str(exc))

    def service(self, action: str) -> None:
        if action != "Status" and not messagebox.askyesno("Serviço local",
            f"{action} o serviço local de coleta neste computador?", icon="question"):
            return
        try:
            self._run(service_command(action, self.home))
        except (OSError, ValueError) as exc:
            self.show(str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interface gráfica de coleta fiscal")
    parser.add_argument("--home", type=Path, default=profiles.default_home())
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    home = profiles.require_home(args.home)
    window = CollectorWindow(home)
    if args.smoke_test:
        window.withdraw()
        window.update_idletasks()
        window.destroy()
        print("Interface inicializada.")
        return 0
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
