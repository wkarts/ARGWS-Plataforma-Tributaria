#!/usr/bin/env python3
"""Interface gráfica para gerar e operar a implantação no ambiente do cliente."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

CLI_EXE = "fiscal-deployer-worker-win-x64.exe"
TARGETS = ("docker", "dockge", "portainer", "cloudpanel")
CHANNELS = ("production", "develop")
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+\Z")


def backend_command() -> list[str]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve().parent / CLI_EXE
        if executable.is_file():
            return [str(executable)]
        raise FileNotFoundError(
            "O executável interno de implantação não está ao lado da interface. "
            "Extraia o ZIP completo, incluindo o fiscal-deployer-worker-win-x64.exe."
        )
    script = Path(__file__).resolve().parent / "deployer.py"
    if script.is_file():
        return [sys.executable, str(script)]
    raise FileNotFoundError("deployer.py não foi encontrado na instalação.")


def installation_title() -> str:
    base = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent)
    try:
        brand = json.loads((base / "branding.json").read_text(encoding="utf-8"))
        name = brand.get("name", "") if isinstance(brand, dict) else ""
        if (isinstance(name, str) and 1 <= len(name.strip()) <= 80
                and all(ord(char) >= 32 for char in name)):
            return name.strip()
    except (OSError, UnicodeError, ValueError):
        pass
    return "Gestão Fiscal"


def run_backend(arguments: list[str]) -> tuple[int, str]:
    command = backend_command()
    result = subprocess.run(
        [*command, *arguments], text=True, encoding="utf-8",
        errors="replace", capture_output=True, timeout=360,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )
    message = result.stdout if result.returncode == 0 else (result.stderr or result.stdout)
    return result.returncode, (message or "").strip()


def parsed_backend(message: str) -> dict:
    try:
        data = json.loads(message)
    except (ValueError, TypeError) as exc:
        raise ValueError(message or "O serviço de implantação não forneceu uma resposta.") from exc
    if not isinstance(data, dict):
        raise ValueError("A resposta do serviço de implantação não é um objeto JSON.")
    if isinstance(data.get("error"), str):
        raise ValueError(data["error"])
    return data


def suggested_folder(target: str, channel: str) -> str:
    return str(Path.home() / "Gestao-Fiscal" / target / channel)


def should_replace_port(value: str, previous_auto: int | None) -> bool:
    return not value.strip() or (previous_auto is not None and value.strip() == str(previous_auto))


class DeployerWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.brand = installation_title()
        self.title(f"{self.brand} • Implantação")
        self.geometry("880x745")
        self.minsize(760, 640)
        self.configure(background="#f5f7fa")
        self._default_folder = suggested_folder("docker", "production")
        self._default_port: int | None = None
        self._request_generation = 0
        self._catalog_generation = 0
        self._running = False
        self.vars = {
            "target": tk.StringVar(value="docker"),
            "channel": tk.StringVar(value="production"),
            "owner": tk.StringVar(value="Detectando no modelo…"),
            "admin_email": tk.StringVar(),
            "tag": tk.StringVar(value="latest"),
            "bind": tk.StringVar(value="127.0.0.1"),
            "port": tk.StringVar(),
            "output": tk.StringVar(value=self._default_folder),
            "rollback_tag": tk.StringVar(),
            "force": tk.BooleanVar(value=False),
            "catalog_status": tk.StringVar(value="Consultando versões publicadas…"),
        }
        self._build()
        self.vars["channel"].trace_add("write", self._configuration_changed)
        self.vars["target"].trace_add("write", self._configuration_changed)
        self.after(50, self._reload_choices)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=self.brand, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Gere compose.yaml e .env automaticamente; escolha a versão do GHCR.",
            foreground="#4b5563",
        ).pack(anchor="w", pady=(2, 14))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="x")
        setup = ttk.Frame(tabs, padding=14)
        operation = ttk.Frame(tabs, padding=14)
        tabs.add(setup, text="Preparar stack")
        tabs.add(operation, text="Operar e recuperar")
        self._combo(setup, "Plataforma", "target", TARGETS, 0, readonly=True)
        self._combo(setup, "Canal", "channel", CHANNELS, 1, readonly=True)
        ttk.Label(setup, text="Owner GHCR (automático)").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Label(
            setup, textvariable=self.vars["owner"], foreground="#254f66"
        ).grid(row=2, column=1, sticky="w", pady=5)

        self.admin_entry = self._entry(setup, "E-mail do administrador inicial", "admin_email", 3)
        ttk.Label(setup, text="Versão / Tag").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
        self.tag_combo = ttk.Combobox(
            setup, textvariable=self.vars["tag"], state="normal", values=("latest",)
        )
        self.tag_combo.grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(
            setup, text="Atualizar lista", command=self._load_catalog
        ).grid(row=4, column=2, padx=(8, 0))
        self._entry(setup, "Endereço local", "bind", 5)
        self.port_entry = self._entry(setup, "Porta web (automática)", "port", 6)
        self._entry(setup, "Pasta do deploy", "output", 7, browse=True)
        ttk.Label(
            setup, textvariable=self.vars["catalog_status"],
            foreground="#526577", wraplength=690,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(5, 7))
        ttk.Checkbutton(
            setup, text="Atualizar compose.yaml existente sem substituir o .env (com backup)",
            variable=self.vars["force"],
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(6, 7))
        ttk.Label(
            setup,
            text="latest seleciona a última release estável PUBLICADA e grava uma tag fixa "
                 "no .env. Para outra versão, selecione a lista ou digite uma tag válida. "
                 "O e-mail do administrador é o único dado obrigatório não inferido.",
            wraplength=720, foreground="#4b5563",
        ).grid(row=10, column=0, columnspan=3, sticky="w")

        self._entry(operation, "Tag de rollback (opcional)", "rollback_tag", 0)
        ttk.Label(
            operation, text="Deploy local requer Docker Compose neste computador. "
                            "Exportar inclui credenciais privadas.",
            wraplength=700, foreground="#4b5563",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 6))
        ttk.Label(
            operation, text="Recuperar restaura a última cópia de compose.yaml/.env sem alterar containers.",
            wraplength=700, foreground="#4b5563",
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=15)
        self.actions: list[ttk.Button] = []
        actions = (
            ("Prévia", self.preview), ("Gerar arquivos", self.generate),
            ("Validar", self.validate), ("Abrir pasta", self.open_folder),
            ("Status", self.status), ("Deploy local", self.deploy),
            ("Atualizar versão", self.upgrade), ("Rollback", self.rollback),
            ("Recuperar", self.recover), ("Exportar ZIP", self.export),
        )
        for index, (label, action) in enumerate(actions):
            button = ttk.Button(buttons, text=label, command=action)
            button.grid(row=index // 5, column=index % 5, sticky="ew", padx=3, pady=3)
            buttons.columnconfigure(index % 5, weight=1)
            self.actions.append(button)
        result = ttk.LabelFrame(outer, text="Resultado", padding=8)
        result.pack(fill="both", expand=True)
        self.output = tk.Text(
            result, wrap="word", state="disabled", relief="flat", font=("Consolas", 10)
        )
        scroll = ttk.Scrollbar(result, command=self.output.yview)
        self.output.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.output.pack(side="left", fill="both", expand=True)
        self._show("O proprietário GHCR e a porta serão detectados do modelo. "
                   "Informe o e-mail administrativo e clique em Gerar arquivos.")

    def _entry(
        self, parent: ttk.Frame, label: str, key: str, row: int, *, browse: bool = False
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        entry = ttk.Entry(parent, textvariable=self.vars[key])
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        if browse:
            ttk.Button(parent, text="Escolher…", command=self.choose_output).grid(
                row=row, column=2, padx=(8, 0)
            )
        parent.columnconfigure(1, weight=1)
        return entry

    def _combo(
        self, parent: ttk.Frame, label: str, key: str, values: tuple[str, ...],
        row: int, *, readonly: bool
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(
            parent, textvariable=self.vars[key], values=values,
            state="readonly" if readonly else "normal",
        ).grid(row=row, column=1, sticky="ew", pady=5)
        parent.columnconfigure(1, weight=1)

    def _configuration_changed(self, *_: object) -> None:
        target = self.vars["target"].get()
        channel = self.vars["channel"].get()
        folder = suggested_folder(target, channel)
        if self.vars["output"].get() == self._default_folder:
            self.vars["output"].set(folder)
        self._default_folder = folder
        self.vars["tag"].set("latest" if channel == "production" else "develop")
        self._reload_choices()

    def _reload_choices(self) -> None:
        self._request_generation += 1
        identity = self._request_generation
        target, channel = self.vars["target"].get(), self.vars["channel"].get()
        self.vars["owner"].set("Detectando no modelo…")

        def worker() -> None:
            try:
                code, message = run_backend(
                    ["defaults", "--target", target, "--channel", channel]
                )
                if code:
                    raise ValueError(parsed_backend(message).get("error") or message)
                result = parsed_backend(message)
                self.after(0, lambda: self._defaults_done(identity, result))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.after(0, lambda detail=str(exc): self._defaults_failed(identity, detail))

        threading.Thread(target=worker, daemon=True).start()
        self._load_catalog()

    def _defaults_done(self, identity: int, data: dict) -> None:
        if identity != self._request_generation:
            return
        self.vars["owner"].set(str(data["owner"]))
        port = int(data["web_port"])
        if should_replace_port(self.vars["port"].get(), self._default_port):
            self.vars["port"].set(str(port))
        self._default_port = port

    def _defaults_failed(self, identity: int, message: str) -> None:
        if identity != self._request_generation:
            return
        self.vars["owner"].set("Obtido ao gerar")
        self._show("Não foi possível ler os valores do modelo: " + message)

    def _load_catalog(self) -> None:
        self._catalog_generation += 1
        identity = self._catalog_generation
        target, channel = self.vars["target"].get(), self.vars["channel"].get()
        self.vars["catalog_status"].set("Consultando as versões publicadas…")
        self.tag_combo.configure(values=("latest",) if channel == "production" else ("develop",))

        def worker() -> None:
            try:
                code, message = run_backend(
                    ["versions", "--target", target, "--channel", channel]
                )
                if code:
                    raise ValueError(parsed_backend(message).get("error") or message)
                result = parsed_backend(message)
                self.after(0, lambda: self._catalog_done(identity, result))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.after(0, lambda detail=str(exc): self._catalog_failed(identity, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _catalog_done(self, identity: int, result: dict) -> None:
        if identity != self._catalog_generation:
            return
        channel = self.vars["channel"].get()
        self.vars["owner"].set(str(result["owner"]))
        choices = (["latest"] if channel == "production" else [])
        choices += list(result.get("versions", []))
        self.tag_combo.configure(values=tuple(dict.fromkeys(choices)))
        latest = result.get("latest")
        if channel == "production":
            text = ("latest resolve para " + latest + " (tag fixa no .env)"
                    if latest else "Nenhuma release estável completa foi encontrada.")
        else:
            text = "develop usa a imagem móvel; commits anteriores aparecem como sha-xxxxxxxxxxxx."
        self.vars["catalog_status"].set(
            str(result.get("count", 0)) + " versões disponíveis. " + text
        )

    def _catalog_failed(self, identity: int, message: str) -> None:
        if identity != self._catalog_generation:
            return
        self.vars["catalog_status"].set(
            "Sem catálogo online: " + message +
            " A seleção manual de uma tag fixa continua disponível."
        )

    def choose_output(self) -> None:
        selection = filedialog.askdirectory(
            initialdir=self.vars["output"].get() or str(Path.home())
        )
        if selection:
            self.vars["output"].set(selection)

    def open_folder(self) -> None:
        directory = Path(self.vars["output"].get()).expanduser()
        if not directory.is_dir():
            messagebox.showinfo(
                "Pasta do deploy", "A pasta ainda não existe. Use Gerar arquivos primeiro."
            )
            return
        if os.name == "nt":
            os.startfile(str(directory))  # type: ignore[attr-defined]
        else:
            try:
                subprocess.Popen(["xdg-open", str(directory)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                self._show("Arquivos de implantação: " + str(directory))

    def _show(self, message: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", message)
        self.output.configure(state="disabled")

    def _run(self, args: list[str]) -> None:
        if self._running:
            return
        self._running = True
        for button in self.actions:
            button.configure(state="disabled")
        command = args[0]
        self._show("Executando " + command + "…")

        def worker() -> None:
            try:
                code, message = run_backend(args)
            except (OSError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
                code, message = 1, str(exc)
            self.after(0, lambda: self._done(code, message, command))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, code: int, message: str, command: str) -> None:
        self._running = False
        for button in self.actions:
            button.configure(state="normal")
        try:
            data = parsed_backend(message)
            if code:
                raise ValueError(message or "A operação não foi concluída.")
            if command == "generate":
                folder = Path(str(data["directory"]))
                compose, env_file = folder / "compose.yaml", folder / ".env"
                if not compose.is_file() or not env_file.is_file():
                    raise ValueError("O gerador retornou sucesso sem produzir os dois arquivos.")
                self._show(
                    "Arquivos gerados e validados com sucesso:\n\n"
                    + str(compose) + "\n" + str(env_file)
                    + "\n\nTag fixada: " + str(data["tag"])
                    + "\nOwner GHCR: " + str(data["owner"])
                    + "\nPorta web: " + str(data["web_port"])
                    + ("\n.env anterior preservado com seus segredos."
                       if data.get("env_preserved") else
                       "\n.env novo com credenciais protegidas.")
                )
            else:
                self._show(json.dumps(data, ensure_ascii=False, indent=2))
        except ValueError as exc:
            self._show("Erro na operação:\n" + str(exc))
            self.title(f"{self.brand} • Implantação — erro")
            return
        self.title(f"{self.brand} • Implantação ✓")

    def _template_args(self) -> list[str] | None:
        """The CLI reads the owner from the model; no manual input is necessary."""
        target, channel = self.vars["target"].get(), self.vars["channel"].get()
        args = ["--target", target, "--channel", channel]
        bind = self.vars["bind"].get().strip()
        if bind:
            args += ["--bind", bind]
        tag = self.vars["tag"].get().strip()
        if tag:
            args += ["--tag", tag]
        port = self.vars["port"].get().strip()
        if port:
            if not port.isdecimal() or not 40000 <= int(port) <= 49999:
                messagebox.showerror(
                    "Porta inválida", "Informe uma porta entre 40000 e 49999."
                )
                self.port_entry.focus_set()
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
        email = self.vars["admin_email"].get().strip().lower()
        if not EMAIL.fullmatch(email):
            messagebox.showerror(
                "E-mail necessário", "Informe o e-mail do administrador inicial. "
                                      "As demais configurações são automáticas."
            )
            self.admin_entry.focus_set()
            return
        folder = self.vars["output"].get().strip()
        if not folder:
            messagebox.showerror("Pasta necessária", "Selecione uma pasta de destino.")
            return
        existing = Path(folder).expanduser() / "compose.yaml"
        force = bool(self.vars["force"].get())
        if existing.is_file() and not force:
            if not messagebox.askyesno(
                "Implantação existente",
                "Já há um compose.yaml nessa pasta. Atualizar apenas o modelo e "
                "preservar integralmente o .env com backup?", icon="warning"
            ):
                return
            force = True
            self.vars["force"].set(True)
        elif force and not messagebox.askyesno(
            "Confirmar atualização",
            "Atualizar compose.yaml e preservar o .env com backup?", icon="warning"
        ):
            return
        self._run([
            "generate", *args, "--output", folder, "--admin-email", email,
            *(["--force"] if force else []),
        ])

    def validate(self) -> None:
        self._run(["validate", *self._directory_args()])

    def status(self) -> None:
        self._run(["status", *self._directory_args()])

    def deploy(self) -> None:
        if messagebox.askyesno(
            "Confirmar deploy local", "Executar Docker Compose config, pull e up -d --wait?",
            icon="warning"
        ):
            self._run(["deploy", *self._directory_args(), "--yes"])

    def upgrade(self) -> None:
        tag = self.vars["tag"].get().strip()
        label = tag or "a versão embutida"
        if messagebox.askyesno(
            "Confirmar atualização",
            "Atualizar para " + label + "? A versão anterior ficará registrada.",
            icon="warning"
        ):
            self._run([
                "upgrade", *self._directory_args(), *(["--tag", tag] if tag else []),
                "--yes",
            ])

    def rollback(self) -> None:
        tag = self.vars["rollback_tag"].get().strip()
        label = tag or "a versão anterior registrada"
        if messagebox.askyesno(
            "Confirmar rollback", f"Implantar {label} e salvar backup do .env anterior?",
            icon="warning"
        ):
            self._run([
                "rollback", *self._directory_args(), *(["--tag", tag] if tag else []),
                "--yes",
            ])

    def recover(self) -> None:
        if messagebox.askyesno(
            "Confirmar recuperação",
            "Restaurar a configuração do último backup? Containers não serão alterados.",
            icon="warning"
        ):
            self._run(["recover", *self._directory_args(), "--yes"])

    def export(self) -> None:
        destination = filedialog.asksaveasfilename(
            defaultextension=".zip", filetypes=[("ZIP", "*.zip")]
        )
        if destination and messagebox.askyesno(
            "Exportar segredos", "O ZIP incluirá .env e credenciais. Salvar em local privado?",
            icon="warning"
        ):
            self._run(["export", *self._directory_args(), "--output", destination])


def smoke_test(window: DeployerWindow) -> None:
    """Smoke the real packaged worker and actual disk output, without internet."""
    window.withdraw()
    window.update_idletasks()
    code, message = run_backend([
        "defaults", "--target", "docker", "--channel", "production"
    ])
    if code:
        raise RuntimeError("O serviço interno não retornou os modelos: " + message)
    meta = parsed_backend(message)
    if not meta.get("owner") or not meta.get("embedded_version"):
        raise RuntimeError("Modelos internos incompletos")
    with tempfile.TemporaryDirectory(prefix="fiscal-gui-smoke ") as temporary:
        selected = [
            "generate", "--target", "docker", "--channel", "production",
            "--tag", "v" + str(meta["embedded_version"]),
            "--output", temporary, "--admin-email", "admin@example.invalid",
        ]
        code, message = run_backend(selected)
        if code:
            raise RuntimeError("A GUI não conseguiu gerar os arquivos: " + message)
        result = parsed_backend(message)
        if not (Path(temporary, "compose.yaml").is_file()
                and Path(temporary, ".env").is_file()):
            raise RuntimeError("Gerador não gravou Compose e .env")
        code, message = run_backend(["validate", "--directory", temporary])
        if code:
            raise RuntimeError("Arquivos gerados falharam validação: " + message)
        if result.get("tag") != "v" + str(meta["embedded_version"]):
            raise RuntimeError("Versão selecionada não foi preservada")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interface gráfica do Deployer")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    window = DeployerWindow()
    if args.smoke_test:
        try:
            smoke_test(window)
            print("GUI e serviço interno: geração/validação executadas com sucesso.")
        finally:
            window.destroy()
        return 0
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
