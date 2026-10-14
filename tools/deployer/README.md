# Deployer GUI e CLI

Ferramenta local para preparar os modelos Docker Compose da plataforma. Runtime Python somente da biblioteca padrão; a distribuição Windows inclui dois executáveis portáteis, GUI e CLI. A GUI encontra o CLI na mesma pasta. O pacote precisa conter os dois arquivos juntos.

## Configuração

Na raiz do repositório, execute:

```bash
python tools/deployer/deployer.py preview --target docker --channel production
python tools/deployer/deployer.py generate --target docker --channel production \
  --output ./installation --admin-email admin@example.org
python tools/deployer/deployer.py validate --directory ./installation
```

Alvos: `docker`, `dockge`, `portainer` e `cloudpanel`. Canais: `develop` e `production`. Os oito modelos autossuficientes estão em `deploy/<alvo>/<canal>`. A versão vem exclusivamente de `VERSION`: produção fixa `APP_TAG` nesse SemVer; develop usa `develop`. É possível informar `--tag 0.1.0`, `--tag v0.1.0` ou `--tag sha-012345abcdef` para uma imagem fixa previamente publicada. `--owner` define o namespace GHCR; `--port` aceita 40000–49999 e `--bind` aceita somente loopback.

O gerador escreve `compose.yaml` e `.env`; senhas e chaves aleatórias entram apenas no `.env`, com permissão `0600` em Unix. Informe o e-mail inicial do administrador explicitamente: a senha inicial fica em `BOOTSTRAP_ADMIN_PASSWORD`. Guarde-a antes do primeiro deploy. O gerador não mostra senhas ou chaves no terminal ou na GUI.

Os modelos fornecidos incluem o serviço local `objectstore` (RustFS), compatível com S3. O deployer gera `S3_ACCESS_KEY` e `S3_SECRET_KEY`; os demais parâmetros `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION` e `S3_USE_SSL` vêm do modelo e são validados. O backend suporta S3 genérico, mas usar um serviço S3 externo exige um modelo Compose ou infraestrutura adaptada e validação própria. A GUI deste pacote prepara os modelos com RustFS local.

Se o destino já tiver `compose.yaml`, use `generate ... --force`. Essa opção substitui apenas o modelo Compose e preserva os bytes do `.env` existente, incluindo segredos, porta e tag. Um backup local fica em `.deployer/backups`. Para mudar a tag de um deploy existente, use `upgrade` ou `rollback` com uma tag fixa. `recover --directory ./installation --yes` restaura o backup mais recente de configuração e não altera containers; `--backup-id` seleciona outro. Arquivos são gravados via substituição atômica, com restauração automática em falhas de gravação.

## Operar localmente

```bash
python tools/deployer/deployer.py status --directory ./installation
python tools/deployer/deployer.py deploy --directory ./installation --yes
python tools/deployer/deployer.py upgrade --directory ./installation --tag 0.1.0 --yes
python tools/deployer/deployer.py rollback --directory ./installation --tag 0.1.0 --yes
python tools/deployer/deployer.py export --directory ./installation --output ./installation.zip
```

`deploy` executa `docker compose config --quiet`, `pull` e `up -d --wait --wait-timeout 180`; exige confirmação explícita `--yes` e só registra sucesso após a espera de saúde do Compose. O histórico de tags implantadas fica em `.deployer/state.json` sem credenciais. `status` lê essa informação e consulta `docker compose ps` quando Docker está acessível. `upgrade` usa a tag indicada ou a versão estável de `VERSION`; `rollback` aceita uma tag SemVer ou `sha-<12 hex>` informada, ou a versão anterior registrada. Ambos salvam o `.env` anterior e restauram o arquivo se o Compose falhar. Uma falha depois que os containers iniciaram pode exigir conferência manual do estado; restauração do `.env` não desfaz migrations ou outros efeitos no banco. Migrações de banco podem exigir restauração de backup de dados. A ferramenta não faz deploy remoto por SSH.

`export` contém `.env` e credenciais para transportar a stack: proteja esse ZIP como segredo. O Portainer pode receber o Compose e as variáveis do `.env` pelo stack; a operação local continua usando `docker compose --env-file .env`. Nenhum comando imprime valores dos segredos. Quando GHCR usar pacotes privados, configure a autenticação do Docker no host antes do pull.

## Pacote Windows

Requer Python 3.12 no ambiente de build, não na máquina que executará os `.exe`:

```powershell
python -m unittest discover -s tools/deployer -p "test_*.py" -v
python -m pip install -r tools/deployer/requirements-build.txt
python -m PyInstaller --clean --noconfirm --onefile --console `
  --name reforma-tributaria-deployer-win-x64 --distpath dist/cli --workpath build/cli `
  --add-data "deploy;deploy" --add-data "VERSION;." tools/deployer/deployer.py
python -m PyInstaller --clean --noconfirm --onefile --windowed `
  --name reforma-tributaria-deployer-gui-win-x64 --distpath dist/gui --workpath build/gui `
  tools/deployer/gui.py
```

Inclua os dois executáveis e este README no mesmo ZIP. Smoke do CLI: `version`, `list`, `generate --target docker --channel production --output <pasta> --admin-email admin@example.org`, `validate --directory <pasta>`. Smoke da GUI no Windows: `reforma-tributaria-deployer-gui-win-x64.exe --smoke-test`.
