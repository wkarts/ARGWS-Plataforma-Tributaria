#!/usr/bin/env python3
"""Interface gráfica para instalar e operar a stack no ambiente do cliente."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


CLI_EXE = "fiscal-deployer-worker-win-x64.exe"
TARGETS = ("docker", "dockge", "portainer", "cloudpanel")
CHANNELS = ("production", "develop")


def backend_command() -> list[str]:
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / CLI_EXE
        if candidate.is_file():
            return [str(candidate)]
        raise FileNotFoundError(f"Extraia {CLI_EXE} na mesma pasta desta interface.")
    candidate = Path(__file__).resolve().parent / "deployer.py"
    if candidate.is_file():
        return [sys.executable, str(candidate)]
    raise FileNotFoundError("deployer.py não encontrado.")


def installation_title() -> str:
    """O título é local à instalação; arquivos inválidos não mudam a interface."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    try:
        brand = json.loads((base / "branding.json").read_text(encoding="utf-8"))
        name = brand.get("name", "") if isinstance(brand, dict) else ""
        if isinstance(name, str) and 1 <= len(name.strip()) <= 80 and all(ord(char) >= 32 for char in name):
            return name.strip()
    except (OSError, UnicodeError, ValueError):
        pass
    return "Gestão Fiscal"


def run_backend(arguments: list[str]) -> tuple[int, str]:
    command = backend_command()
    result = subprocess.run([*command, *arguments], text=True, encoding="utf-8",
                            errors="replace", capture_output=True, timeout=360,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            check=False)
    return result.returncode, (result.stdout or result.stderr).strip()


class DeployerWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.brand = installation_title()
        self.title(f"{self.brand} • Implantação")
        self.geometry("850x720")
        self.minsize(730, 620)
        self.configure(background="#f5f7fa")
        self.vars = {
            "target": tk.StringVar(value="docker"),
            "channel": tk.StringVar(value="production"),
            "owner": tk.StringVar(),
            "admin_email": tk.StringVar(),
            "tag": tk.StringVar(),
            "bind": tk.StringVar(value="127.0.0.1"),
            "port": tk.StringVar(),
            "output": tk.StringVar(value=str(Path.home() / "Gestao-Fiscal" / "production")),
            "rollback_tag": tk.StringVar(),
            "force": tk.BooleanVar(value=False),
        }
        self._build()
        self.vars["channel"].trace_add("write", self._channel_changed)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=self.brand, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Gerencie modelos Docker Compose e versões publicadas no GHCR.",
                  foreground="#4b5563").pack(anchor="w", pady=(2, 14))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="x")
        setup = ttk.Frame(tabs, padding=16)
        operation = ttk.Frame(tabs, padding=16)
        tabs.add(setup, text="Preparar stack")
        tabs.add(operation, text="Operar e recuperar")
        self._combo(setup, "Plataforma", "target", TARGETS, 0)
        self._combo(setup, "Canal", "channel", CHANNELS, 1)
        self._entry(setup, "Owner GHCR", "owner", 2)
        self._entry(setup, "E-mail do administrador inicial", "admin_email", 3)
        self._entry(setup, "Tag (vazia = VERSION / develop)", "tag", 4)
        self._entry(setup, "Endereço local", "bind", 5)
        self._entry(setup, "Porta web (vazia = modelo)", "port", 6)
        self._entry(setup, "Pasta do deploy", "output", 7, browse=True)
        ttk.Checkbutton(setup, text="Atualizar compose.yaml mantendo integralmente o .env e criando backup",
                        variable=self.vars["force"]).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 6))
        ttk.Label(setup, text="Produção usa uma versão fixa. Os segredos novos são gerados localmente e não aparecem no painel.",
                  wraplength=680, foreground="#4b5563").grid(row=9, column=0, columnspan=3, sticky="w")

        self._entry(operation, "Tag de rollback (opcional se houver histórico)", "rollback_tag", 0)
        ttk.Label(operation, text="O deploy local exige Docker Compose neste computador. Exportar inclui o .env e seus segredos.",
                  wraplength=680, foreground="#4b5563").grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 6))
        ttk.Label(operation, text="Recuperar restaura o backup local mais recente de compose.yaml e .env; não altera containers.",
                  wraplength=680, foreground="#4b5563").grid(row=2, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=16)
        self.actions: list[ttk.Button] = []
        actions = (("Prévia", self.preview), ("Gerar", self.generate), ("Validar", self.validate),
                   ("Status", self.status), ("Deploy local", self.deploy),
                   ("Atualizar versão", self.upgrade), ("Rollback", self.rollback),
                   ("Recuperar", self.recover), ("Exportar ZIP", self.export))
        for index, (label, action) in enumerate(actions):
            button = ttk.Button(buttons, text=label, command=action)
            button.grid(row=index // 4, column=index % 4, sticky="ew", padx=3, pady=3)
            buttons.columnconfigure(index % 4, weight=1)
            self.actions.append(button)
        result = ttk.LabelFrame(outer, text="Resultado", padding=8)
        result.pack(fill="both", expand=True)
        self.output = tk.Text(result, wrap="word", state="disabled", relief="flat", font=("Consolas", 10))
        scroll = ttk.Scrollbar(result, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self._show("Selecione plataforma e canal. Use Prévia para conferir o modelo antes de gerar.")

    def _entry(self, parent: ttk.Frame, label: str, key: str, row: int, *, browse: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(parent, textvariable=self.vars[key]).grid(row=row, column=1, sticky="ew", pady=5)
        if browse:
            ttk.Button(parent, text="Escolher…", command=self.choose_output).grid(row=row, column=2, padx=(8, 0))
        parent.columnconfigure(1, weight=1)

    def _combo(self, parent: ttk.Frame, label: str, key: str, values: tuple[str, ...], row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(parent, textvariable=self.vars[key], values=values,
                     state="readonly").grid(row=row, column=1, sticky="ew", pady=5)
        parent.columnconfigure(1, weight=1)

    def _channel_changed(self, *_: object) -> None:
        folder = Path(self.vars["output"].get())
        if folder.name in CHANNELS:
            self.vars["output"].set(str(folder.with_name(self.vars["channel"].get())))

    def choose_output(self) -> None:
        selection = filedialog.askdirectory(initialdir=self.vars["output"].get() or str(Path.home()))
        if selection:
            self.vars["output"].set(selection)

    def _show(self, message: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", message)
        self.output.configure(state="disabled")

    def _run(self, args: list[str]) -> None:
        for action in self.actions:
            action.configure(state="disabled")
        self._show("Operação em andamento…")

        def worker() -> None:
            try:
                code, message = run_backend(args)
            except (OSError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                code, message = 1, str(exc)
            self.after(0, lambda: self._done(code, message))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, code: int, message: str) -> None:
        for action in self.actions:
            action.configure(state="normal")
        try:
            parsed = json.loads(message)
            displayed = json.dumps(parsed, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            displayed = message
        self._show(displayed or "A operação terminou sem resposta.")
        self.title(f"{self.brand} • Implantação" + (" ✓" if code == 0 else " • verifique o resultado"))

    def _template_args(self) -> list[str] | None:
        if not self.vars["owner"].get().strip():
            messagebox.showerror("Registro de imagens", "Informe o namespace GHCR das imagens desta instalação.")
            return None
        args = ["--target", self.vars["target"].get(), "--channel", self.vars["channel"].get(),
                "--owner", self.vars["owner"].get().strip(), "--bind", self.vars["bind"].get().strip()]
        tag = self.vars["tag"].get().strip()
        port = self.vars["port"].get().strip()
        if tag:
            args += ["--tag", tag]
        if port:
            if not port.isdecimal():
                messagebox.showerror("Porta inválida", "Informe uma porta numérica de 40000 a 49999.")
                return None
            args += ["--port", port]
        return args

    def _directory_args(self) -> list[str]:
        return ["--directory", self.vars["output"].get().strip()]

    def preview(self) -> None:
        args = self._template_args()
        if args is not None:
            self._run(["preview", *args])

    def generate(self) -> None:
        args = self._template_args()
        if args is None:
            return
        if self.vars["force"].get() and not messagebox.askyesno(
                "Atualizar configuração", "Atualizar o Compose e guardar backup? O .env e os segredos atuais serão preservados."):
            return
        self._run(["generate", *args, "--output", self.vars["output"].get().strip(),
                   "--admin-email", self.vars["admin_email"].get().strip(),
                   *(["--force"] if self.vars["force"].get() else [])])

    def validate(self) -> None:
        self._run(["validate", *self._directory_args()])

    def status(self) -> None:
        self._run(["status", *self._directory_args()])

    def deploy(self) -> None:
        if messagebox.askyesno("Confirmar deploy local", "Executar Docker Compose config, pull e up -d --wait nesta pasta?",
                               icon="warning"):
            self._run(["deploy", *self._directory_args(), "--yes"])

    def upgrade(self) -> None:
        tag = self.vars["tag"].get().strip()
        label = tag or "a versão estável de VERSION"
        if messagebox.askyesno("Confirmar atualização", f"Implantar {label}, preservar o .env e registrar a versão anterior?",
                               icon="warning"):
            self._run(["upgrade", *self._directory_args(), *(["--tag", tag] if tag else []), "--yes"])

    def rollback(self) -> None:
        tag = self.vars["rollback_tag"].get().strip()
        label = tag or "a versão anterior registrada"
        if messagebox.askyesno("Confirmar rollback", f"Implantar {label} e preservar backup do .env anterior?",
                               icon="warning"):
            self._run(["rollback", *self._directory_args(), *(["--tag", tag] if tag else []), "--yes"])

    def recover(self) -> None:
        if messagebox.askyesno("Confirmar recuperação", "Restaurar os arquivos do último backup? Containers não serão alterados.",
                               icon="warning"):
            self._run(["recover", *self._directory_args(), "--yes"])

    def export(self) -> None:
        destination = filedialog.asksaveasfilename(defaultextension=".zip", filetypes=[("ZIP", "*.zip")])
        if destination and messagebox.askyesno("Exportar segredos", "O ZIP incluirá .env e senhas. Salve em local privado?",
                                               icon="warning"):
            self._run(["export", *self._directory_args(), "--output", destination])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interface gráfica do Deployer")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    window = DeployerWindow()
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
