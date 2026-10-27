# Implantação com interface gráfica

Ferramenta local para preparar modelos Docker Compose na infraestrutura do cliente. O pacote Windows contém a interface gráfica e um executável de operação **interno**, usado pela interface. Extraia o ZIP inteiro e abra `fiscal-deployer-gui-win-x64.exe`. **O owner GHCR e a porta web são detectados automaticamente a partir do modelo**; produção consulta as releases publicadas e oferece a opção `latest`, todas as versões estáveis disponíveis e seleção manual de tag. O cliente não precisa conhecer o namespace ou o número da porta para gerar os arquivos. A marca pode ser configurada por instalação em um `branding.json` ao lado da interface, por exemplo `{"name":"Seu Escritório"}`. O padrão é "Gestão Fiscal". O executável interno não é um produto de linha de comando para o usuário final.

## Configuração

Na raiz do repositório, execute:

```bash
python tools/deployer/deployer.py defaults --target docker --channel production
python tools/deployer/deployer.py versions --target docker --channel production
python tools/deployer/deployer.py preview --target docker --channel production --tag latest
python tools/deployer/deployer.py generate --target docker --channel production --tag latest \
  --output ./installation --admin-email admin@example.org
python tools/deployer/deployer.py validate --directory ./installation
```

Alvos: `docker`, `dockge`, `portainer` e `cloudpanel`. Canais: `develop` e `production`. Os oito modelos autossuficientes estão em `deploy/<alvo>/<canal>`. O padrão automático da GUI para produção é `latest`: consulta as releases estáveis completas no GitHub e **resolve para uma tag fixa, como `v0.3.1`, antes de escrever o `.env`**. Nunca deixa `APP_TAG=latest` mutável no cliente. O seletor permite escolher todas as versões publicadas e digitar manualmente uma tag SemVer ou `sha-<12 hex>`. No canal develop, o padrão é `develop`, com possibilidade de selecionar commits de pré-release pelo SHA. A CLI mantém o comportamento histórico de usar a versão embarcada quando `--tag` for omitida; passe `--tag latest` para consultar automaticamente. Sem acesso ao GitHub, a GUI explica a indisponibilidade do catálogo e permite usar uma tag fixa conhecida; ela não inventa a versão mais recente. O namespace GHCR vem de `IMAGE_OWNER` no modelo; `--owner` é opcional para personalizações autorizadas. A porta vem de `WEB_PORT` (`48080` em produção e `48081` em develop); `--port` permite ajuste de 40000 a 49999 e `--bind` aceita somente loopback.

O botão **Gerar arquivos** cria `compose.yaml` e `.env` na pasta escolhida e confirma os dois caminhos na tela. O smoke-test do executável gráfico valida essa geração real e reabre a configuração, sem precisar da rede para o teste. Senhas e chaves aleatórias entram apenas no `.env`, com permissão `0600` em Unix. O único dado não inferido automaticamente é o e-mail inicial do administrador, que deve ser informado explicitamente: a senha inicial fica em `BOOTSTRAP_ADMIN_PASSWORD`. Guarde-a antes do primeiro deploy. O gerador não mostra senhas ou chaves no terminal ou na GUI.

Os modelos fornecidos incluem o serviço local `objectstore` (RustFS), compatível com S3. O deployer gera `S3_ACCESS_KEY` e `S3_SECRET_KEY`; os demais parâmetros `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION` e `S3_USE_SSL` vêm do modelo e são validados. O backend suporta S3 genérico, mas usar um serviço S3 externo exige um modelo Compose ou infraestrutura adaptada e validação própria. A GUI deste pacote prepara os modelos com RustFS local.

Se o destino já tiver `compose.yaml`, use `generate ... --force`. Essa opção substitui apenas o modelo Compose e preserva os bytes do `.env` existente, incluindo segredos, porta e tag. Um backup local fica em `.deployer/backups`. Para mudar a tag de um deploy existente, use `upgrade` ou `rollback` com uma tag fixa. `recover --directory ./installation --yes` restaura o backup mais recente de configuração e não altera containers; `--backup-id` seleciona outro. Arquivos são gravados via substituição atômica, com restauração automática em falhas de gravação.

## Operar localmente

```bash
python tools/deployer/deployer.py status --directory ./installation
python tools/deployer/deployer.py deploy --directory ./installation --yes
python tools/deployer/deployer.py upgrade --directory ./installation --tag latest --yes
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
  --name fiscal-deployer-worker-win-x64 --distpath dist/cli --workpath build/cli `
  --add-data "deploy;deploy" --add-data "VERSION;." tools/deployer/deployer.py
python -m PyInstaller --clean --noconfirm --onefile --windowed `
  --name fiscal-deployer-gui-win-x64 --distpath dist/gui --workpath build/gui `
  tools/deployer/gui.py
```

Inclua os dois executáveis e este README no mesmo ZIP; publique apenas o pacote gráfico. Smoke interno: `version`, `list`, `defaults`, `generate --target docker --channel production --output <pasta> --admin-email admin@example.org`, `validate --directory <pasta>`. O smoke da GUI no Windows (`fiscal-deployer-gui-win-x64.exe --smoke-test`) usa o executável interno de verdade para **gerar e validar ambos os arquivos** em uma pasta temporária com espaços, sem internet. A compilação deve incluir o módulo Python `release_catalog.py` no worker, e o ZIP final sempre contém os dois executáveis.

## Escolher a integração CBS

Na GUI Windows, o campo **Integração CBS / PROVIDER_MODE** apresenta três opções:

- **disabled** — padrão seguro para produção. Desliga apenas consultas CBS oficiais; login, usuários, empresas e dados locais permanecem disponíveis.
- **receita** — permite consultas oficiais SOMENTE após credenciais e ambientes autorizados. São necessários `CBS_TOKEN_URL`, `CBS_CLIENT_ID`, `CBS_CLIENT_SECRET`, `CBS_WEBHOOK_BASE_URL` público em HTTPS, `CBS_CREDENTIAL_ORGANIZATION_ID`, `CBS_ALLOWED_BASES`, além de `CBS_PRODUCTION_ENABLED` ou `CBS_RESTRICTED_ENABLED`. Sem isso a plataforma inicia, informa a pendência e não envia consultas oficiais.
- **fixture** — dados de testes, **disponível exclusivamente em develop**. Não permite instalação de produção nem constitui apuração fiscal.

Uma instalação nova grava o modo escolhido no `.env` pela opção **Gerar arquivos**. Para alterar somente o modo de uma instalação existente, use **Aplicar modo CBS**, que preserva todas as senhas e cria um backup completo do `.env`; ou execute:

```bash
python tools/deployer/deployer.py provider-update --directory ./installation --mode disabled --yes
python tools/deployer/deployer.py provider-update --directory ./installation --mode receita --yes
```

Após alterar o `.env`, recrie os serviços da stack para atualizar suas variáveis de ambiente. **Não execute `down --volumes`**. Os parâmetros oficiais vazios não são preenchidos com valores inventados nem com respostas simuladas. O estado de integração incompleta é apresentado no painel da API.

## Integrações Receita / SMTP

A GUI possui abas **Receita / CBS** e **E-mail / SMTP**, com configuração real de OAuth oficial, CNPJ raiz autorizado, callback HTTPS, ambientes e sincronização, além de servidor SMTP, TLS/SSL, autenticação, remetente e URL pública. Campos de senha ficam mascarados e **nunca entram em argumentos de linha de comando**. A ferramenta transmite JSON por stdin ao worker local, grava somente no .env da instalação e cria backup ao atualizar.

Clique **Carregar configurações** para obter somente parâmetros não secretos; os campos de senha permanecem vazios, significando *preservar os segredos já salvos*. **Salvar integrações** atualiza o `.env` existente com backup e mantém bancos/volumes intactos. Ao gerar uma nova instalação, a interface envia os parâmetros preenchidos ao gerador, que verifica HTTPS e evita `fixture` na produção.

O CLI interno aceita `generate --stdin-json` e `configure --directory ./installation --stdin-json --yes`, recebendo JSON com nomes permitidos pela entrada padrão. Não informe senhas como argumentos. `integration-settings --directory ./installation` retorna somente parâmetros públicos e flags de presença, nunca senha. Produção usa `disabled` por padrão; `receita` precisa de credenciais/ambientes para efetivamente consultar; `fixture` continua exclusivo do desenvolvimento.
