# Avaliação tecnológica dos três pacotes de referência

**Data da análise:** 14/10/2026. **Escopo:** inspeção dos arquivos anexados `DealsHUB-1.0.0.zip`, `ARGWS-Scout-0.8.1.zip` e `ARGWS-Connect-API-1.5.1.zip`. Esta avaliação descreve capacidades presentes no código e nos arquivos de distribuição; não comprova que os serviços externos desses produtos estejam operacionais.

## Decisão para a nova plataforma

Construir um produto **independente**, com API e trabalhadores em **Python/FastAPI**, banco **PostgreSQL**, **RabbitMQ/Celery** para execução assíncrona, **Redis** para cache e coordenação, **contrato S3 genérico** para artefatos (RustFS local no Compose ou serviço S3 externo) e **Vue 3/TypeScript** para o painel. Estes são **componentes da arquitetura alvo**; a presença neste documento não atesta implementação nem homologação na versão inicial. Os três pacotes servem de referência para contratos e processos de entrega. Nenhum deles contém um conector FastAPI homologado para Apuração Assistida da CBS ou para IBS.

**Meta de entrega:** distribuir stacks separadas para `develop` e `production` em Docker Compose, Dockge, CloudPanel e Portainer Standalone. Uma GUI Windows escrita em **Python stdlib/Tkinter**, empacotada por **PyInstaller**, deverá usar o mesmo núcleo de geração e validação que a CLI e ser publicada junto à release após o smoke test do executável. O servidor de destino consumirá imagens GHCR e não precisará instalar Python, Node ou executar scripts auxiliares do deployer. A existência dos workflows/arquivos fonte ainda requer execução de CI e inspeção dos artefatos.

## Matriz observada

As três colunas centrais registram **capacidades dos pacotes anexados**. A última coluna registra **destino proposto para a nova plataforma, pendente de implementação/validação**, e não um inventário da versão atual.

| Capacidade | DealsHUB 1.0.0 | Scout 0.8.1 | Connect API 1.5.1 | Arquitetura alvo da plataforma fiscal |
| --- | --- | --- | --- | --- |
| API principal | PHP próprio, controllers/services/repositories; `app/Services/Import/ImportEngine.php` | Node/TypeScript/Fastify, Prisma; `apps/api/package.json` | Node/TypeScript/Express, Prisma; `package.json`, `src/api/` | **Novo código FastAPI** com adaptadores tributários separados; nenhum backend é transplantado. |
| Painel | Views PHP | Vue 3/TS, Manager separado | Vue 3/Vite/Pinia, Manager separado | Vue 3/TS, padrão claro, administração, jobs, trilha, conciliação e status por estabelecimento. |
| Trabalho assíncrono | Fila local limitada de importação; processos PHP/Python | Dispatcher, scheduler, workers HTTP/browser/webhook e outbox | Eventos RabbitMQ/NATS/Kafka/SQS e operações por perfil | Celery/RabbitMQ com tarefas curtas, idempotentes e retry controlado; PostgreSQL mantém o estado. |
| Persistência | SQLite local ou MySQL | PostgreSQL/Prisma, Redis, Garage S3 | PostgreSQL/MySQL/Prisma, Redis, MinIO/S3 | PostgreSQL/SQLAlchemy/Alembic, Redis e interface S3 genérica; RustFS local no Compose ou S3 externo. Não importar schema comercial ou de mensagens. |
| Credenciais e tenants | Configuração privada do importador; revisão humana | Organizações, RBAC, MFA TOTP do OWNER, tokens por instância e auditoria | Instâncias, autenticação por API key e Manager | Escopo obrigatório por organização, empresa/CNPJ base, estabelecimento, tributo e ambiente; segredos cifrados e RBAC/MFA. |
| Aquisição | Adapters de fontes, bruto → normalizado → revisão; distingue incerteza | Conectores HTTP/browser, artefatos, outbox, jobs e webhooks com retry | SDKs de canais/eventos, OpenAPI/AsyncAPI e docs Scalar | Adaptador oficial CBS, ingestão fiscal ERP e conciliação; IBS em módulo separado, somente após contrato oficial validado. |
| Deployment | Compose develop/production, GUI Windows com backend Go incorporado no EXE Python/PyInstaller | Compose, Dockge, CloudPanel, Portainer; CLI+GUI Python/PyInstaller | Seis flavors de template; CLI+GUI Rust/Win32 com núcleo comum | Templates fiscal específicos; Python CLI+Tkinter/PyInstaller; apenas `compose.yaml` e `.env` na instalação. |
| Publicação | Uma pré-release móvel de develop, estáveis SemVer em main, imagens e checksums; `.github/workflows/ghcr-publish.yml` | `develop` e `stable/latest`, SemVer e deployer após builds; `.github/workflows/{ghcr-publish-application,release,deployer-release}.yml` | Builds nativos amd64/arm64 por digest, tags SemVer e `:develop`; `.github/workflows/auto-version-release.yml` | Uma pré-release móvel ligada a develop; releases imutáveis `vX.Y.Z` da main somente após CI, imagens e GUI. |
| Limpeza | Política por execução programada e migração de prefixos antigos | Sem retenção automática de artefatos declarada na versão auditada | Retenção com inventário, dry-run, pins canônicos e limites de tempo/quantidade | Limpeza limitada a cache, digests temporários e imagens descartáveis; preservar todos os digests de releases estáveis e canônicas declaradas. |
| Documentação/observabilidade | Relatórios de importação e diagnóstico por fonte | Health/readiness, histórico de jobs/auditoria | OpenAPI/AsyncAPI gerados, Scalar, diagnósticos/Prometheus | OpenAPI derivado do FastAPI, contratos externos versionados, health/readiness e métricas sem segredo fiscal. |

## Regras que serão aproveitadas

Estes são **critérios para as fases seguintes**, não funcionalidades concluídas no protótipo 0.1.0:

1. **Aquisição verificável:** preservar respostas brutas, checksum, fonte, horário, contrato de origem e resultado de normalização; a estratégia de DealsHUB de não promover um dado incerto a fato é especialmente importante na conciliação fiscal.
2. **Processamento durável:** implementar outbox transacional, jobs rastreáveis, idempotência, scheduler, limites de concorrência, RBAC, MFA e verificação de saúde inspirados no Scout; trocar código de coleta de páginas por consumidores de APIs oficiais.
3. **Documentação e releases:** implantar o vínculo entre código/OpenAPI/documentação, versão materializada no runtime, builds por digest, relatório de imagem, verificações antes da publicação, sincronização conservadora de `develop` e inventário protegido antes de apagar qualquer imagem, conforme referência Connect.
4. **Deployer GUI real:** aproveitar da Scout a separação do núcleo Python de geração do Tkinter, e da DealsHUB o smoke test do `.exe` Windows. A GUI deve permitir preparar, validar, revisar e abrir a pasta; operações remotas opcionais só depois de autorização explícita e testes próprios.
5. **Stacks reproduzíveis:** Compose e `.env` por ambiente, nomes de projeto distintos, volumes persistentes isolados, healthchecks, serviço de migração antes da API, portas de infraestrutura apenas em loopback/rede interna e geração de credenciais sem sobrescrever uma instalação existente.

## Discrepâncias que não devem ser copiadas

- O Connect chama `connect-transcription-service` no workflow/Compose, enquanto `RELEASE-AUTOMATION.md` e `.github/retention-policy.json` ainda mencionam `argws-connect-transcription-worker`; o allowlist de exclusão não cobre o pacote real. `RELEASE-MANIFEST.json` registra a versão do produto como 1.5.1, enquanto o arquivo distinto `.github/retention-policy.json` ainda registra `channels.stable/latest` como 1.1.3. Isto é divergência documental e de inventário; não permite inferir a tag efetiva no GHCR sem consultá-lo. A stack `deploy/canonical` fixa API/docs em 1.3.0, mas deixa a transcrição opcional com fallback `:latest`.
- Em Connect, `manager/src/views/FlowsView.vue` e `AutomationsView.vue` são áreas de futura evolução, sem editor funcional de fluxo. Não usá-las como prova de que o fluxo fiscal já existe.
- O gerador Rust Connect valida alguns trechos textuais de Compose, e grava `compose.yaml` antes do `.env`; a promessa de modo `0600` só se aplica ao Unix. A plataforma fiscal deve chamar `docker compose config --quiet` quando disponível, validar semanticamente o manifesto antes de gravar, escrever em arquivos temporários e usar troca/backup controlados; no Windows, orientar ou impor ACL restrita à pasta de segredos.
- A retenção Connect pode apagar imagens SemVer antigas após a janela de 48 horas, exceto pins canônicos e release mais recente. **Não aplicar essa regra às imagens estáveis desta plataforma.** Git tag ou GitHub Release preservados não garantem imagem recuperável.
- `manager/Dockerfile` Connect usa `npm install` em build de release. Preferir `npm ci`/lockfile ou `pnpm --frozen-lockfile` no painel fiscal para reprodutibilidade.
- O Scout informa explicitamente que domínios e bancos físicos exclusivos por organização ainda não existem na 0.8.1; seus espaços lógicos não representam isolamento físico. Definir a estratégia fiscal de isolamento com honestidade e testes de acesso cruzado.
- O MinIO do pacote Connect é **referência observada**, não componente escolhido para a nova stack. O [advisory oficial GHSA-xh8f-g2qw-gcm7](https://github.com/minio/minio/security/advisories/GHSA-xh8f-g2qw-gcm7) inclui a versão final do projeto OSS (`RELEASE.2025-09-07T16-13-09Z`) na faixa afetada por uma falha no endpoint internode de instalações distribuídas. Isso não demonstra exploração nem afeta o modo standalone descrito no aviso; fundamenta a retirada do MinIO OSS como opção ativa da nova plataforma. A escolha RustFS local se apoia nas [instruções oficiais de Docker/Compose](https://docs.rustfs.com/en/installation/container/docker), mas sua compatibilidade com os recursos usados pelo produto e sua operação em produção dependem dos testes do projeto.
- Scraping, browser worker, WhatsApp, IA de conteúdo, transcrição, Traccar, Kafka e NATS não são dependências de uma primeira integração governamental. Adicioná-los somente para um requisito fiscal comprovado.

## Alternativas avaliadas

| Decisão | Seleção inicial | Critério de revisão |
| --- | --- | --- |
| API | FastAPI + Pydantic + SQLAlchemy 2 + Alembic | Separar contratos HTTP, persistência e cálculo decimal; revisão se a operação exigir um serviço específico em outra linguagem. |
| Fila | RabbitMQ + Celery | Retry, isolamento e jobs duráveis; usar Redis para cache/locks e não como única fonte dos jobs. |
| Arquivos | Contrato S3 genérico com bucket privado e SHA-256; RustFS no Compose local ou S3 externo configurado por `S3_*` | Validar `put/get/head`, checksum, indisponibilidade e restauração em ambos os alvos; [a matriz oficial do RustFS](https://docs.rustfs.com/en/reference/s3-compatibility) documenta um subconjunto testado da API S3, não compatibilidade universal. Versionamento, Object Lock e política de retenção dependem de gate específico antes de uso; não há homologação operacional nesta fase. |
| Interface | Vue 3/TypeScript/Vite | Isolar painel do contrato público da API e usar tema claro inicialmente. |
| Deployer | Python stdlib/Tkinter + PyInstaller | Rust/Win32 é alternativa futura se tamanho, inicialização, distribuição ou proteção operacional exigirem; não bloquear a primeira entrega. |
| Banco e isolamento | PostgreSQL com `tenant_id`/RLS avaliada e constraints; escopo de CNPJ em cada consulta | Banco por tenant é opção de produto posterior; exigir testes de isolamento antes de adotá-la. |

## Evidência e limite da integração governamental

A Receita anunciou consultas incrementais de débitos/créditos da CBS para início de outubro de 2026, pagamentos/recolhimentos para início de novembro, e DARFs para o fim de novembro, em ambientes disponibilizados gradualmente. Isso é **calendário anunciado**, não prova de credenciais disponíveis, endpoint respondendo ou homologação nesta plataforma. [Comunicado RFB de 14/09/2026](https://www.gov.br/receitafederal/pt-br/assuntos/noticias/2026/setembro/receita-federal-publica-nova-documentacao-tecnica-das-apis-de-apuracao-de-cbs); [documentação oficial CBS](https://docs.receitafederal.gov.br/apuracao-cbs/).

O escopo IBS requer fonte oficial do CGIBS, contrato técnico, credenciais e ambiente próprios. Não reutilizar host, payload, disponibilidade ou autenticação da CBS por analogia. A homologação real está definida em [ROADMAP-E-HOMOLOGACAO.md](ROADMAP-E-HOMOLOGACAO.md).
