# Gestão Fiscal — espaço de trabalho fiscal assistido

Plataforma em **FastAPI** e Vue para reunir evidências fiscais por empresa: XMLs fornecidos pelo cliente, observações da CBS, trilha de origem e comparação por documento e competência. A [instalação self](docs/SELF-HOSTED.md) fica na infraestrutura do cliente, com SQLite e arquivos locais; o coletor gráfico roda nos computadores que possuem os XMLs e os encaminha à URL HTTPS dessa instalação. Uma instalação pode cadastrar várias empresas, cada qual com identidade e credencial de ingestão próprias. **A prévia não constitui declaração nem homologação da apuração de CBS/IBS.** O modo de consultas CBS existente usa **fixture sintética** por padrão; chamadas autenticadas à Receita dependem de credencial, webhook público, disponibilidade do recurso e teste oficial.

A [arquitetura](docs/ARQUITETURA.md), o [plano de homologação](docs/ROADMAP-E-HOMOLOGACAO.md) e os [contratos oficiais](docs/CONTRATOS-OFICIAIS-2026.md) registram as escolhas e os limites de cada conector.

## O que já existe e o que depende de habilitação

| Frente | Capacidade implementada | Limite atual |
| --- | --- | --- |
| CBS | Abertura assíncrona, ticket, webhook e recuperação por situação, download e preservação do JSON bruto com SHA-256; painel de consultas e registros. Agendamento conservador opcional de débitos/créditos. | Fixture é o padrão. O provedor Receita exige flags, credenciais, CNPJ base autorizado e homologação específica. Uma consulta incremental não comprova cobertura integral de uma competência. |
| XML do cliente | Credencial de ingestão vinculada à empresa, envio do XML original por HTTPS, deduplicação por SHA-256, objeto privado e listagem/download. Extrai metadados de NF-e/NFC-e, CT-e, MDF-e e alguns eventos quando disponíveis. | Presença de `ds:Signature` e protocolo declarado são **informações do XML**, sem validação criptográfica, do schema ou confirmação on-line da autorização; outras famílias podem permanecer sem dados estruturados. Limite de 10 MiB por arquivo. |
| Coletor local | Interface para múltiplas empresas, URL HTTPS, token, CNPJ, pastas, certificado SEFAZ e controle do serviço. Catálogo SQLite e estado/token separados por UUID; o original não é modificado. | A release precisa compilar e testar o instalador gráfico Windows com WinSW embutido e o pacote GUI Linux em runners nativos; modo de pastas não requer certificado fiscal. |
| Distribuição NF-e opcional | No mesmo processo local, adaptador **opt-in** de `NFeDistribuicaoDFe` em `distNSU`, SOAP 1.2/mTLS com PEM, checkpoint por CNPJ/ambiente e spool durável do retorno SOAP, gzip e XML. Arquivos integrais compatíveis com marcador XMLDSig são encaminhados ao fluxo de ingestão. | Não há teste autenticado/homologação com SEFAZ. Resumos e eventos ficam classificados separadamente; resumo **não se transforma em NF-e integral**. Presença de XMLDSig não valida assinatura nem autorização. Só um consumidor deve coordenar o NSU daquela identidade. |
| Conferência | Comparação somente leitura entre observações CBS e XMLs da mesma empresa/competência, mostrando correspondência de valor, divergência, ausência local, ambiguidade e estado desconhecido. | Sem cobertura oficial comprovada, ausência em consulta incremental fica **desconhecida**. Correspondência de valor não comprova autenticidade, direito ao crédito nem extinção do tributo. Limite interativo de 5.000 registros e 5.000 XMLs por competência. |
| Prévia de apuração assistida | Totais **declarados** em XML por CBS/IBS, modelo e entrada/saída, com críticas por chave, ID e hash e observações CBS concluídas separadas de fixture. | Cobertura, autenticidade, autorização e direitos creditórios não são comprovados; o endpoint não calcula saldo a pagar nem faz declaração. |
| Perfil self | API, web e SQLite/arquivos locais no host do cliente, sem banco, broker, cache nem S3 externos; marca administrável por instalação. | Perfil self inicial não liga o worker de CBS/IBS; acesso HTTPS externo e backup/restauração precisam ser preparados no host do cliente. |
| Controles manuais | Candidato a crédito presumido de estoque em rascunho com referência de evidência; parcela comercial com referência documental e data informada de pagamento. | Não há cálculo do crédito de transição, apropriação/utilização automática ou baixa de débito fiscal por pagamento comercial. |
| IBS e outros DF-e | Contratos e fronteiras próprios documentados em [contratos oficiais](docs/CONTRATOS-OFICIAIS-2026.md). | A API IBS não está ligada ao sistema; CT-e, MDF-e e NFS-e oficiais requerem conectores próprios. |

## Componentes

| Camada | Tecnologia | Papel nesta versão |
| --- | --- | --- |
| API e persistência | Python 3.13, FastAPI, SQLAlchemy, Alembic, SQLite no self; PostgreSQL no perfil legado | Organizações, usuários, empresas, agentes de ingestão, XMLs, observações, controles manuais e auditoria. |
| Execução CBS no perfil legado | Celery, RabbitMQ, Redis | Abertura assíncrona, consulta de situação, cache de OAuth e agenda opcional; desabilitada no self inicial. |
| Evidência | Arquivos privados no self; S3/RustFS no perfil legado | JSON/XML originais, SHA-256 e referências duráveis; exige backup e restauração testados. |
| Painel | Vue 3, TypeScript, Vite, PWA | Consultas, XMLs, agentes, conciliação por competência, registros e download com hash. |
| Coletor do cliente | Python stdlib, Tkinter, SQLite, systemd/WinSW, PyInstaller e instalador Windows | GUI configura várias empresas e controla o serviço; vigia pastas e, quando autorizado, consulta `distNSU` NF-e com certificado local. Token e chave PEM fora do JSON. |
| Distribuição | Docker Compose, GHCR, GitHub Actions | Develop/production, imagens base, SemVer, checksums, release e limpeza conservadora. |
| Instalação | Docker Compose local para self; Tkinter/PyInstaller para deploy legado | Plataforma self usa build local; utilitário gráfico legado prepara Docker, Dockge, CloudPanel e Portainer com worker interno. |

## Cálculo normativo por operação e central operacional

O painel inclui **Central Tributária** com indicadores/histórico por empresa e **Calculadora Tributária** integrada ao componente **oficial offline** instalado pelo próprio cliente. O backend não utiliza percentuais ou classificações presumidos: os resultados só são registrados após chamada real ao motor local e confirmação de sua versão normativa, com conservação do retorno original, hashes e segregação por organização/empresa. O módulo suporta a integração do regime regular e mantém contrato independente configurável para o Simples Nacional. Ausência do motor impede somente os cálculos, sem interferir na coleta e Apuração Assistida existente.

A configuração, o uso do Swagger oficial, o histórico das versões, os contratos HTTP e os requisitos de homologação estão em [Cálculo Tributário Local](docs/CALCULO-TRIBUTARIO-LOCAL.md). Cálculo de operação não equivale a apuração concluída nem comprova crédito aproveitável ou extinção de débito.

## Começar com a instalação self

Leia [instalação e operação self](docs/SELF-HOSTED.md) para gerar segredos, subir API+web com SQLite, configurar TLS, cadastrar empresas e instalar o coletor gráfico. O [manual da prévia](docs/APURACAO-ASSISTIDA.md) explica exatamente o que entra nas somas e o que permanece pendente de validação.

## Perfil legado com imagens locais

Requer Docker com Compose v2, Python 3.12+ apenas para gerar a instalação, acesso às imagens base públicas e portas locais disponíveis. Na raiz do projeto:

```bash
python3 tools/deployer/deployer.py generate --target docker --channel develop \
  --owner sua-organizacao \
  --output ./installation --admin-email admin@example.org
python3 tools/deployer/deployer.py validate --directory ./installation
docker compose --project-directory ./deploy --env-file ./installation/.env \
  -f deploy/compose.yaml -f deploy/build.local.yaml up -d --build --wait
```

O gerador cria segredos aleatórios em `installation/.env`, incluindo `BOOTSTRAP_ADMIN_PASSWORD`; guarde essa senha em local seguro antes de subir a stack. A UI ficará em `http://127.0.0.1:<WEB_PORT>` (porta padrão `48080`), salvo configuração de porta no `.env`. O bootstrap aplica migrations e cria o primeiro administrador explicitamente; não há conta padrão embutida. O modo `fixture` permite testar consultas sem chamadas governamentais. Por padrão, a abertura de cada consulta CBS é explícita; o scheduler acompanha tickets abertos. `CBS_AUTO_SYNC_ENABLED=true` ativa uma agenda separada **somente com o provedor Receita real e ambiente liberado**, respeitando até quatro aberturas registradas por CNPJ base/24 horas e uma pendência por base. Ela não estabelece cobertura histórica nem reabre automaticamente uma solicitação incerta.

Para parar sem remover dados:

```bash
docker compose --project-directory ./deploy --env-file ./installation/.env \
  -f deploy/compose.yaml -f deploy/build.local.yaml down
```

Não use `down -v` se quiser preservar PostgreSQL, RabbitMQ, Redis e objetos. O script `scripts/backup.sh` faz backup de PostgreSQL de um deploy em execução; **não inclui o bucket S3**. Faça e teste backup/restauração do banco e dos objetos antes de operação com dados reais.

## Deployer e destinos

O [manual do deployer](tools/deployer/README.md) descreve GUI, CLI, geração/validação, `deploy`, `status`, `rollback`, recuperação de configuração e pacote Windows. Os oito destinos são o produto cartesiano de `docker`, `dockge`, `cloudpanel`, `portainer` com `develop`, `production`. Cada diretório `deploy/<destino>/<canal>` contém `compose.yaml` e `.env.example`; o gerador grava a instalação em pasta escolhida, com `.env` privado e sem sobrescrever segredos existentes. A operação padrão do deployer faz pull de imagens `ghcr.io/<owner>/reforma-tributaria-{api,web}` e bases: essas imagens **ainda precisam ser publicadas em um repositório GitHub**. Para o desenvolvimento inicial, use o override `deploy/build.local.yaml` acima.

`develop` usa `APP_TAG=develop`; `production` fixa o valor de `VERSION` ou uma tag imutável explicitamente fornecida. O rollback de imagem não reverte schema nem dados. O deploy em CloudPanel/Dockge/Portainer é um Compose preparado para esses gerenciadores; o deployer local não realiza SSH nem instalação remota.

## API e integração

`/health/live`, `/health/ready`, `/docs` e `/openapi.json` são rotas FastAPI. A API de negócio usa `/api/v1`: login, troca de senha, capacidades, empresas, consultas, registros, XMLs, agentes de ingestão, conciliação e controles manuais. Cadastre um CNPJ e solicite uma consulta pelo painel no ambiente `fixture`. Um `Idempotency-Key` na criação impede duplicação da mesma solicitação; o worker guarda bytes e SHA-256 antes da projeção dos registros.

Para um provedor real, configure `PROVIDER_MODE=receita` e as variáveis `CBS_*` em uma instalação privada. O modo real fica desabilitado sem organização autorizada, CNPJ base permitido, credencial, endpoint HTTPS e flags de ambiente. Uma única configuração de credencial por instalação é um limite desta versão: não há cofre por tenant nem homologação com contribuinte real. O webhook do governo requer HTTPS público, mas a UI padrão escuta apenas em loopback; configure proxy e rede segundo o ambiente autorizado. Recursos de pagamento/recolhimento têm flag separada. A resposta governamental é preservada em bruto; o painel distingue dados ausentes de zero. `CBS_AUTO_SYNC_ENABLED` começa `false`, com `CBS_AUTO_SYNC_ENVIRONMENT=restricted` e `CBS_AUTO_SYNC_RESOURCES=debitos,creditos` apenas como valores de configuração. Consulte [limites e evidência necessários](docs/ROADMAP-E-HOMOLOGACAO.md) antes de qualquer uso decisório.

### Ingressar XMLs de um cliente

1. Cadastre a empresa e crie um agente no painel. O token `rta_...` aparece uma única vez e tem escopo de **ingestão naquela empresa**; guarde-o em arquivo secreto. É possível revogá-lo no painel/API.
2. Configure [o coletor local](tools/collector/README.md) com URL HTTPS, `company_id`, uma ou mais pastas e `state_dir` persistente. Os comandos `check`, `once`, `serve`, `status` e `self-test --offline` permitem validar e operar a instalação. Veja também [o roteiro operacional](docs/COLETOR-LOCAL-OPERACAO.md).
3. O coletor envia `POST /api/v1/documents/ingest?company_id=UUID` com XML bruto, Bearer, `Content-Type: application/xml` e `X-Content-SHA256`. A API compara o hash, associa a organização/empresa, armazena o original e só então apresenta seus metadados. `GET /api/v1/documents` lista, e `GET /api/v1/documents/{id}/raw` baixa o original para conferência.
4. Escolha a competência no painel para `GET /api/v1/reconciliation?company_id=UUID&period=AAAA-MM`. O resultado é **conferência de fontes**.

### Consultar distribuição NF-e no ambiente do cliente

O modo NF-e é **opcional** no mesmo coletor. `configure` aceita `--sefaz-cnpj`, `--sefaz-env homologation|production`, `--sefaz-cert-pem` e `--sefaz-key-pem` juntos; `--root` pode ser omitido quando há somente SEFAZ. O JSON grava **caminhos absolutos**, sem conteúdo da chave nem token. `check` valida PEM e permissões da chave sem rede. `once` ou `serve` fazem a consulta real ao serviço oficial quando configurados: confira primeiro CNPJ/ator, certificado, ambiente, credencial da plataforma e diretório persistente. O consumo governamental usa NSU por CNPJ/ambiente; coordene-o com outros softwares do contribuinte. Veja [a operação e as classes de retorno](tools/collector/README.md).

O adaptador preserva o SOAP original e cada `docZip`, faz extração limitada e guarda o checkpoint NSU **após** gravar o lote local. O spool começa com limite de 2 GiB e reserva espaço para o lote. `resNFe`/`resEvento` permanecem `summary_unprocessed`; schema desconhecido fica em quarentena. Somente XML integral/evento compatível com marcador XMLDSig segue como `ready` para o envio autenticado. É uma classificação local, **sem comprovação de autenticidade, autorização, completude nem envio confirmado**. Consulte `status` para diferenciar NSU gravado, arquivos em espera e upload aceito. Nenhum teste autenticado com a SEFAZ foi executado nesta instalação.

`POST/GET /api/v1/stock-credit-claims` e `POST/GET /api/v1/commercial-installments` registram fatos **manuais** separados do ledger CBS. Somente administrador registra; outras contas da organização podem consultar. Referências de evidência não comprovam o direito ao crédito ou a extinção tributária.

## Versão e esteiras

`VERSION` é a fonte canônica SemVer. `python3 scripts/version.py check` verifica a versão do backend, frontend e lockfile; `python3 scripts/version.py next patch|minor|major` sugere o próximo valor e `python3 scripts/version.py set X.Y.Z` sincroniza os arquivos. O fluxo proposto é `feature/*` → PR em `develop` → pré-release automática e imutável por commit (`vX.Y.Z-develop.<sha12>`), com imagens móveis `:develop`; uma promoção automática, por PR e CI verificados, leva as alterações à `main`, reserva o próximo SemVer estável e publica `vX.Y.Z` com imagens GHCR, GUI Windows e verificações de digest/checksum. Consulte [a automação de promoção](docs/PROMOCAO-DEVELOP-MAIN.md). A tag estável existente não é sobrescrita. As sete imagens base são declaradas em `.github/base-images.json`; a política de retenção preserva releases estáveis e bases, e a exclusão de GHCR não está habilitada por padrão. Não execute limpeza global de Docker/volumes.

Verificações locais:

```bash
python3 scripts/version.py check
python3 scripts/validate_deploys.py
python3 scripts/validate_release.py
python3 -m unittest discover -s tools/deployer -p 'test_*.py'
python3 -m unittest discover -s tools/collector/tests -p 'test_*.py'
python3 -m unittest discover -s tools/collector -p 'test_sefaz_nfe.py'
python3 tools/collector/collector.py self-test --offline
(cd apps/api && python3 -m venv .venv && .venv/bin/python -m pip install -e '.[test]' && .venv/bin/python -m pytest -q)
(cd apps/web && npm ci && npm test && npm run build)
```

Docker/Compose, GHCR, publicação GitHub, build real do `.exe` Windows e homologação Receita/SEFAZ são verificações de ambiente a executar depois de conectar o repositório e as credenciais. A [PR preparada](docs/PULL_REQUEST.md) resume as mudanças e os gates que faltam.

## Limites técnicos atuais

- O arquivo de retorno CBS e cada XML recebido têm limites de tamanho. XML é lido por partes na API, mas processado em memória até 10 MiB; ingestão em grande volume requer filas, streaming do parser, paginação operacional e métricas.
- O isolamento é aplicado por `organization_id` e empresa nos fluxos de documentos/conciliação; MFA, RLS, cofre de credenciais por tenant e outbox transacional continuam no plano de evolução. O scheduler CBS usa a base do CNPJ; outras filiais da mesma base não ganham cobertura de conciliação por empresa automaticamente.
- O S3 local facilita desenvolvimento, mas backup/restauração, compatibilidade e segurança operacional precisam ser testados no destino escolhido antes da produção.
- Há MOC oficial IBS v1.10, porém suas rotas ainda têm pontos propostos e produção futura indicada; não há adaptador IBS implantado. A disponibilidade anunciada da CBS não comprova acesso, contrato atual ou autorização neste ambiente. Consulte a [matriz de fontes oficiais](docs/CONTRATOS-OFICIAIS-2026.md).

Licença e política de distribuição devem ser definidas pelo titular antes de publicar o repositório.
