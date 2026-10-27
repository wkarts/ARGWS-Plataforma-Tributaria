# Operação e distribuição da plataforma fiscal

## Componentes

`api`, `worker` e `scheduler` compartilham a mesma imagem FastAPI. `migrate` executa Alembic uma vez por alteração da imagem; `bootstrap` cria ou confirma o administrador inicial e o bucket S3 de modo idempotente. A interface Vue é servida por Nginx em `web`, que encaminha `/api/` à API. PostgreSQL, Redis, RabbitMQ e RustFS ficam na rede interna, sem portas públicas. Apenas `web` escuta em `127.0.0.1:48080` na produção (`48081` em develop); configure HTTPS no proxy reverso do host.

RustFS 1.0.1 é o serviço S3 compatível usado no primeiro deploy. A imagem comunitária antiga do MinIO não é utilizada: a última edição OSS tem advisory de segurança e não está disponível no registro público. A API recebe somente `S3_*`, de forma que um serviço S3 externo pode ser configurado e homologado depois. O RustFS deste Compose é **single-node**; o volume persiste, mas não oferece redundância entre máquinas.

| Destino | Develop | Produção |
|---|---|---|
| Docker | `deploy/docker/develop` | `deploy/docker/production` |
| Dockge | `deploy/dockge/develop` | `deploy/dockge/production` |
| Portainer | `deploy/portainer/develop` | `deploy/portainer/production` |
| CloudPanel | `deploy/cloudpanel/develop` | `deploy/cloudpanel/production` |

Cada diretório é autossuficiente: `compose.yaml` e `.env.example`. As configurações de serviço estão no próprio Compose com interpolação `${VAR}`; no Portainer, forneça as mesmas variáveis pela interface de Stack ou por `.env` disponível ao projeto. `scripts/generate_deploys.py` é o gerador; `scripts/validate_deploys.py --docker` compara os dez diretórios e executa `docker compose config --quiet` em cada um. Não edite uma cópia isoladamente.

## Imagens e canais

`VERSION` na raiz é a versão estável canônica; `python3 scripts/version.py check` verifica `pyproject.toml`, `package.json` e `package-lock.json`. Um bump explícito usa `python3 scripts/version.py set X.Y.Z` no branch de trabalho. Cada merge na `develop` dispara o CI e, se os artefatos passarem na validação, cria uma **pré-release imutável por commit** (`vX.Y.Z-develop.<sha12>`), com pacote completo de todos os dez modelos de implantação, coletor Windows/Linux e deployer Windows. Somente o commit atual da `develop` promove a tag móvel de imagens `:develop`. A antiga release `pre-release` permanece histórica, sem receber atualizações. Main publica `vX.Y.Z`, sem sufixos; as tags `:X.Y.Z`, `:vX.Y.Z`, `:sha-<12>`, `:main`, `:stable` e `:latest` apontam ao build aprovado. A release contém `IMAGES.txt` e `BASE-IMAGES.txt` com digests completos, pacote de deploy, executáveis Windows GUI/CLI e `SHA256SUMS.txt`. Uma release estável existente não tem seus assets substituídos pelo workflow.

Estes modelos são a implantação legada baseada em imagens GHCR. O namespace em `IMAGE_OWNER` é escolhido explicitamente pelo operador; imagens obtidas do registro de origem conservam sua proveniência e o nome do pacote no próprio registro. Para a instalação self com imagens construídas na infraestrutura do cliente e SQLite, consulte `deploy/self/`; ela não precisa de um namespace GHCR.

O fluxo de main constrói primeiro só `:sha-<12>`, conclui CI e os executáveis Windows, cria um draft e confere cada asset e seu checksum. Depois promove `:X.Y.Z` e `:vX.Y.Z` por digest e publica o draft. Um retry completa somente arquivos ausentes cujo hash coincide com o manifest remoto; um asset divergente interrompe a operação. As tags móveis `main/stable/latest` só avançam se o commit publicado ainda for o HEAD de main. Se uma execução falhar entre promoção e publicação, o draft completo permanece para retomada, sem apagar tags canônicas.

As sete imagens-base (`python`, `node`, `nginx`, `postgres`, `redis`, `rabbitmq`, `objectstore`) constam em `.github/base-images.json`. A automação não reetiqueta uma base publicada; para atualizar o runtime, incremente `rN`, ajuste as referências dependentes e valide a compatibilidade dos dados. Imagens de API e web são publicadas para `linux/amd64` e `linux/arm64`, com SBOM e proveniência. Os pacotes Python ainda usam faixas de dependências no `pyproject.toml`, logo rebuilds de código-fonte não são bit a bit reproduzíveis; a release registra `API-DEPENDENCIES.txt` extraído da imagem publicada e os digests exatos.

## Calculadora tributária em todos os destinos

Os perfis `self`, Docker, Dockge, Portainer e CloudPanel utilizam o mesmo módulo de cálculo por operação. A API **não inclui uma imagem tributária fictícia**: instale a Calculadora normativa em rede interna controlada, verifique sua versão e seus contratos HTTP antes de habilitar o regime. Configure no `.env` da instalação:

```dotenv
TAX_CALCULATOR_REGULAR_URL=
TAX_CALCULATOR_REGULAR_VERSION_URL=
TAX_CALCULATOR_SIMPLES_URL=
TAX_CALCULATOR_SIMPLES_VERSION_URL=
```

Cada regime exige a URL de cálculo e a URL de versão, com o mesmo host e porta. Se o motor estiver em contêiner na rede de aplicação, use os nomes de serviço reconhecidos `calculator-regular`, `calculator-simples` ou `tax-calculator`. Se estiver instalado na máquina anfitriã, `host.docker.internal` é resolvido no contêiner `api` pelo mapeamento `host-gateway`; a porta deve ser acessível somente da rede autorizada. O nome `localhost` dentro da API não corresponde ao host. As URLs são acessíveis apenas ao backend e não são expostas no navegador.

Deixar as quatro variáveis vazias desabilita o cálculo, sem interromper os demais módulos. O serviço fiscal local não é baixado ou iniciado automaticamente, e a configuração nunca cria alíquotas ou resultados hipotéticos. Consulte `docs/CALCULO-TRIBUTARIO-LOCAL.md` para a versão do contrato utilizada.

Os modelos oficiais são `deploy/compose.yaml`, `deploy/env.develop.example` e `deploy/env.production.example`. `scripts/generate_deploys.py` propaga alterações aos dez diretórios derivados. O `.env` privado de uma instalação não é alterado pelo gerador. Para acrescentar apenas os novos parâmetros opcionais sem substituir segredos, use `scripts/sync_deploy_env.py` com confirmação explícita. O merge atualiza artefatos de distribuição; **não realiza deploy remoto em servidores de clientes**.

## Desenvolvimento local sem GHCR

O override local constrói API e web com bases públicas. Requer Docker Engine com Compose, Python 3.13 e Node 22 apenas para desenvolvimento fora dos containers. Gere um `.env` de develop com o deployer e copie o arquivo privado para `deploy/.env`, ou preencha uma cópia de `deploy/env.develop.example` com segredos fortes e e-mail administrativo real. Nunca versione `.env`.

```bash
python3 tools/deployer/deployer.py generate --target docker --channel develop --owner fiscal-local \
  --output /tmp/reforma-local --admin-email admin@exemplo.com.br
cp /tmp/reforma-local/.env deploy/.env
python3 scripts/preflight.py --env-file deploy/.env --channel develop
docker compose --project-directory deploy --env-file deploy/.env \
  -f deploy/compose.yaml -f deploy/build.local.yaml build api web
docker compose --project-directory deploy --env-file deploy/.env \
  -f deploy/compose.yaml -f deploy/build.local.yaml up -d --wait
curl -fsS http://127.0.0.1:48081/healthz
```

O serviço `bootstrap` é one-shot e exige `BOOTSTRAP_ADMIN_EMAIL/PASSWORD` na primeira inicialização; não há login administrativo padrão. A base e o volume do desenvolvimento usam `COMPOSE_PROJECT_NAME` próprio. O modo `PROVIDER_MODE=fixture` é o default: nenhuma credencial governamental é necessária para testar a interface e os fluxos locais.

## Produção e homologação governamental

Gere a pasta do destino pelo deployer GUI Windows ou CLI, selecione production, informe o e-mail do administrador e configure o segredo inicial. A ferramenta coloca em `APP_TAG` uma versão estável (`X.Y.Z`) derivada de `VERSION`, também publicada como `vX.Y.Z`. Se preparar manualmente pelo `.env.example`, substitua `CHANGE_ME_TO_RELEASE_TAG`, complete os oito segredos, guarde o arquivo com acesso restrito e autentique o host no GHCR se o pacote for privado. Nunca use `:latest` como tag de produção.

```bash
DEPLOY_DIR=/srv/reforma-tributaria/production DEPLOY_CHANNEL=production bash scripts/deploy.sh
```

O script valida `.env` e Compose, faz backup PostgreSQL quando já existe container, executa pull, migration, bootstrap e sobe os serviços aguardando health checks. Não usa `down -v`, `volume prune` ou DDL ao iniciar a API. Verifique o health web, `docker compose ps`, logs sem segredos e tamanho das filas. Em Portainer/Dockge/CloudPanel, preserve o mesmo `COMPOSE_PROJECT_NAME` e os volumes nomeados em todas as atualizações; a troca do projeto cria volumes novos vazios.

Para credenciais reais da CBS, configure `PROVIDER_MODE=receita`, `CBS_RESTRICTED_ENABLED=true` somente quando a credencial e o ambiente estiverem homologados, `CBS_ALLOWED_BASES`, `CBS_CREDENTIAL_ORGANIZATION_ID`, `CBS_TOKEN_URL`, `CBS_CLIENT_ID`, `CBS_CLIENT_SECRET` e `CBS_WEBHOOK_BASE_URL` HTTPS. Ative `CBS_PRODUCTION_ENABLED` e `CBS_PAYMENTS_ENABLED` separadamente quando houver disponibilidade oficial efetiva. O preflight exige parâmetros básicos do modo Receita, sem imprimir valores secretos. Nenhuma dessas chaves é enviada ao container web. O proxy desliga access log para `/api/v1/webhooks/cbs/` e o Uvicorn desliga request access log para não registrar o segredo do callback no path.

## Backup, verificação e restauração

`DEPLOY_DIR=/srv/reforma-tributaria/production bash scripts/backup.sh` gera `pg_dump` custom em `backups/` com checksum e permissões privadas, sem imprimir senha. Esse comando cobre apenas PostgreSQL. Para consistência entre metadados e objetos, pause `web`, `api`, `worker` e `scheduler` antes do backup completo, execute `backup.sh`, pare `objectstore` e arquive o volume S3 com uma imagem utilitária confiável e fixada. Exemplo a partir da pasta do deploy:

```bash
docker compose --env-file .env -f compose.yaml stop web api worker scheduler
DEPLOY_DIR="$PWD" bash /caminho/do/repo/scripts/backup.sh
docker compose --env-file .env -f compose.yaml stop objectstore
container="$(docker compose --env-file .env -f compose.yaml ps -aq objectstore)"
mkdir -p backups
docker run --rm --volumes-from "$container":ro -v "$PWD/backups:/backup" \
  alpine:3.20 tar -C /data -czf /backup/objectstore.tar.gz .
sha256sum backups/objectstore.tar.gz > backups/objectstore.tar.gz.sha256
docker compose --env-file .env -f compose.yaml up -d
```

Guarde dump, snapshot S3, `.env` protegido, `VERSION`, `APP_TAG`, digest das imagens e checksums juntos em destino externo. Teste a restauração em ambiente isolado. Para restaurar PostgreSQL, interrompa os serviços de aplicação, **confirme que é o banco de destino correto** e aplique o dump com:

```bash
docker compose --env-file .env -f compose.yaml stop web api worker scheduler
docker compose --env-file .env -f compose.yaml exec -T postgres sh -ec \
  'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-acl' \
  < backups/postgres-YYYYMMDDTHHMMSSZ.dump
```

Restaurar objetos exige um volume S3 **novo e vazio** do mesmo ponto no tempo: crie uma stack isolada, pare o serviço de objetos e extraia `objectstore.tar.gz` nesse volume. Não extraia sobre dados operacionais. Verifique integridade do arquivo (`sha256sum -c`), saúde `/health/ready` do RustFS dentro da rede e `head_bucket`/`head_object` via SDK boto3 com as credenciais da aplicação antes de liberar tráfego. Confirme também contagem de registros e amostras de objetos por tenant. A mudança de credenciais raiz do RustFS depois da inicialização requer planejamento: a chave pode participar da criptografia interna dos dados.

## Rollback e limpeza

Para voltar a uma imagem, verifique compatibilidade das migrations com o schema atual, execute backup, altere só `APP_TAG` e suba a versão anterior:

```bash
python3 scripts/rollback.py --directory /srv/reforma-tributaria/production --tag vX.Y.Z
DEPLOY_DIR=/srv/reforma-tributaria/production bash scripts/deploy.sh
```

O script preserva uma cópia privada anterior do `.env`. Ele **não reverte schema nem dados**; migração incompatível exige restauração coordenada do backup PostgreSQL e S3. Verifique o digest esperado em `IMAGES.txt` e saúde API/web após a reversão.

`retention-report.yml` roda semanalmente só com permissões de leitura. `retention-apply.yml` é exclusivamente manual, exige confirmação, limita a 20 exclusões e preserva todas as versões GHCR com tag e todas as imagens-base. Versões sem tag só podem ser excluídas se seus IDs forem explicitamente aprovados em `.github/retention-policy.json`; a lista nasce vazia porque um digest sem tag ainda pode estar em uso. Caches só são candidatos após 21 dias e chave prefixada `buildkit-`. `scripts/clean_local_cache.sh` opera dry-run por padrão e limita a exclusão a imagens dangling rotuladas deste repositório. Nenhum fluxo apaga volumes.
