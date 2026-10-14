# Pull Request

**Pull Request:** [#1](https://github.com/wkarts/ARGWS-Plataforma-Tributaria/pull/1), `develop` → `main`.

**Título:** `release: primeira versão 0.1.0 da plataforma tributária`

**Versão proposta:** `0.1.0`; isso não representa release publicada.

**Descrição para a PR:** Entrega a primeira versão estável da plataforma, com API FastAPI/CBS, ingestão rastreável de XML fiscal, credenciais de coleta restritas à empresa, serviço local com checkpoint e envio HTTPS, adaptador opcional de distribuição NF-e `distNSU`, comparação assistida de documentos e observações CBS, controles financeiros manuais, agenda CBS opt-in e telas correspondentes. Acrescenta migration, testes, documentação de contratos oficiais e workflows que verificam os binários Linux/Windows, publicam uma pré-release contínua em `develop` e promovem a release `v0.1.0` em `main` somente após os gates. Os dados fiscais brutos permanecem preservados e o painel distingue confronto de valores de apuração, autorização oficial e direito ao crédito.

## Objetivo e escopo

O operador pode cadastrar empresa, registrar consulta CBS, receber XMLs que o cliente já possui, consultar os documentos preservados, confrontar por empresa e competência e registrar fatos manuais para revisão. O coletor instala-se no ambiente autorizado do cliente e pode ler pastas locais; a consulta de distribuição NF-e é uma opção separada que exige certificado, CNPJ e ambiente configurados. O projeto mantém fixture como padrão e não pressupõe acesso autorizado desta instalação à Receita, SEFAZ ou CGIBS.

| Frente | Implementação nesta branch | Limite explícito |
| --- | --- | --- |
| CBS | API assíncrona anterior com ticket, webhook/status, bruto e projeção; agenda Celery Beat opt-in para recursos permitidos, limite conservador de aberturas e estados incertos sem reabertura automática. | A agenda não comprova cobertura incremental; execução autenticada com contribuinte autorizado ainda pendente. |
| XML | Agentes por organização/empresa, token mostrado uma vez e armazenado em hash; upload bruto `application/xml` até 10 MiB com SHA-256, deduplicação, metadados e download do original. | Presença de `Signature` e protocolo declarado não são verificação criptográfica, validação de schema ou consulta de autorização. |
| Coletor no cliente | CLI `configure`, `check`, `once`, `serve`, `status` e autoteste offline; pastas autorizadas, arquivo estável, SQLite, retomada, retry, HTTPS; instalação como systemd e via WinSW. | XMLs do cliente permanecem intocados; Windows Service depende do wrapper WinSW separado e de binário Windows construído no runner. |
| SEFAZ NF-e | Modo opcional de distribuição sequencial `distNSU` com mTLS/PEM no cliente, espera após ausência/bloqueio, checkpoint por identidade e ambiente, spool e quarentena. | Somente NF-e; sem manifestação, assinatura XML verificada ou teste autenticado com SEFAZ. Não inclui CT-e, MDF-e e NFS-e. |
| Conferência | Endpoint somente leitura por empresa/competência, comparação por chave e valor decimal, categorias `match`, `mismatch`, `missing`, `ambiguous`, `unknown`; observações duplicadas não são somadas. | `match` indica igualdade de valor observável, não apuração homologada. `official_coverage_proven` permanece `false`; filial da mesma raiz CNPJ exige atenção ao escopo. |
| Controles manuais | Candidato a crédito de estoque em `draft` e parcela comercial, ambos registrados em tabelas próprias e auditados. | Sem cálculo/apropriação de crédito, sem baixa de tributo por pagamento comercial e sem lançamento oficial. |
| Interface e operação | Vue: documentos, agente, conciliação e controles manuais; templates Compose, configuração de agenda e pipeline de binários/checksums. | Telas, Compose estático e workflow local não atestam deploy, GHCR, executável Windows ou release remoto. |

## Arquivos e contratos

| Área | Caminhos principais | Contrato ou responsabilidade |
| --- | --- | --- |
| API/XML | `apps/api/app/documents.py`, `xml_parser.py`, `storage.py`, `models.py` | Credencial de agente, recepção original, extração limitada, objeto S3 privado e auditoria. |
| Migration | `apps/api/alembic/versions/20261014_0002_document_ingest.py` | Acrescenta `agent_credentials` e `fiscal_documents` com índices e unicidade de `(organization_id, company_id, sha256)`; preserva tabelas iniciais. |
| Conciliação e controles | `apps/api/app/reconciliation.py`, `reconciliation_api.py`, `finance_controls.py` | Comparação somente leitura e cadastros manuais separados do `Record` oficial. |
| Agenda | `apps/api/app/auto_sync.py`, `worker.py`, `config.py` | Criação opt-in de `Query` por organização/CNPJ base/ambiente; worker existente continua responsável pela consulta externa. |
| Agente | `tools/collector/collector.py`, `sefaz_nfe.py`, `systemd/`, `windows/` | Serviço configurável, estado local, distribuição NF-e opcional, instaladores e testes offline. |
| Frontend | `apps/web/src/App.vue`, `api.ts`, `types.ts`, `components/FiscalWorkspace.vue`, `components/FinanceControls.vue` | Listagem/download de XML, token recém-criado em memória, resultados e fatos manuais com rótulos conservadores. |
| Entrega | `deploy/`, `.github/workflows/collector-binaries.yml`, `.github/workflows/ghcr-publish.yml`, `scripts/verify_collector_assets.py`, `scripts/validate_release.py` | Templates, jobs Linux/Windows com autoteste e hash, transporte por draft interno e gates para publicação. |
| Documentação | `README.md`, `docs/ARQUITETURA.md`, `docs/COLETOR-LOCAL-OPERACAO.md`, `docs/CONTRATOS-OFICIAIS-2026.md`, `docs/ROADMAP-E-HOMOLOGACAO.md` | Uso, responsabilidades, fontes oficiais, limites e gates por integração. |

API nova: `POST/GET /api/v1/agents`, `DELETE /api/v1/agents/{id}`; `POST /api/v1/documents/ingest?company_id=...` com `Authorization: Bearer rta_...`, `Content-Type: application/xml` e `X-Content-SHA256`; `GET /api/v1/documents`, `GET /api/v1/documents/{id}/raw`; `GET /api/v1/reconciliation?company_id=...&period=AAAA-MM`; `POST/GET /api/v1/stock-credit-claims` e `POST/GET /api/v1/commercial-installments`. A API anterior de autenticação, empresas, consultas, registros e capacidades permanece. Cadastros de agentes e controles manuais exigem administrador da organização; listagens de documentos e conferência exigem usuário autenticado no escopo da organização.

## Banco, segurança e integridade

- Alembic `20261014_0002` acrescenta tabelas e índices sem migrar dados existentes. Aplicar `upgrade head` antes de subir o código; conferir backup e ensaiar em cópia de PostgreSQL. Reverter a imagem sem avaliar o schema não equivale a desfazer migration. `downgrade` remove as duas tabelas **e os dados novos**; somente após backup e decisão operacional.
- O token `rta_...` não é devolvido depois da criação nem escrito em claro no banco. O coletor guarda o segredo fora do JSON, lê de variável/arquivo protegido e associa o SQLite a uma empresa. Revogação interrompe novos uploads. Certificado e chave PEM do modo SEFAZ permanecem no cliente.
- A API verifica tamanho, tipo, digest e escopo; guarda bytes originais antes de projetar metadados. O parser bloqueia DTD/entidades/XInclude e limita a entrada. Hash local comprova integridade da cópia, não validade fiscal. `reported_authorized` só reflete dados/protocolo apresentados no XML.
- O envio HTTP do coletor não segue redirecionamento; classifica rejeições permanentes, limita backoff e mantém checkpoint. A distribuição NF-e exige spool/estado duráveis e coordenação do NSU com outros consumidores do mesmo CNPJ; resumo `resNFe` e esquema desconhecido não são enviados como XML completo.
- A consulta CBS real continua condicionada a `PROVIDER_MODE=receita`, flags, credencial, allowlist de organização/CNPJ e callback HTTPS. `CBS_AUTO_SYNC_ENABLED=false` por padrão; habilitar exige configuração real e observabilidade. Não reaproveitar token de coleta como credencial governamental.

## Compatibilidade e implantação

`VERSION=0.1.0` é a versão de código proposta; o consumidor da API antiga mantém seus caminhos. Instalação nova aplica as duas migrations. Em atualização, executar backup do PostgreSQL **e do bucket S3** e preservar `.env`/volumes; validar os templates após regeneração. O coletor é um componente fora da stack no cliente e requer permissões de leitura para as pastas XML, escrita no estado e saída HTTPS à plataforma. Com SEFAZ habilitada, requer também PEM, espaço de spool, acesso ao serviço de distribuição e política de backup do SQLite/spool. Usar uma instância coordenada por identidade/ambiente de consulta ou procedimento explícito para outros consumidores do mesmo NSU.

O workflow `collector-binaries.yml` especifica builds PyInstaller Linux AMD64 e Windows AMD64 em runners nativos, `--version`, `self-test --offline`, verificação ELF/PE e SHA-256. `ghcr-publish.yml` baixa binários verificados de um draft interno por commit, confere os checksums, promove imagens GHCR e publica releases. A instalação Linux/Windows no ambiente do cliente e a stack Docker/PostgreSQL/S3 precisam de ensaio próprio antes de produção. O binário não traz WinSW nem certificado do cliente.

## Testes e revisão

| Verificação | Comando / evidência esperada | Estado nesta descrição |
| --- | --- | --- |
| Backend | `cd apps/api && python -m pytest -q`; migration `alembic upgrade head`, `check`, `downgrade -1` e novo `upgrade head` em SQLite temporário. | **47 testes** e ciclo da migration passaram; PostgreSQL real ainda pendente. |
| Coletor e SEFAZ | `python -m unittest discover -s tools/collector -p 'test_*.py' -q`; `python -m unittest discover -s tools/collector/tests -p 'test_*.py' -q`; `python tools/collector/collector.py self-test --offline`. | **11 testes** do adapter, **15** do coletor e autoteste offline passaram. Sem chamada autenticada SEFAZ. |
| Frontend | `cd apps/web && npm test && npm run build`. | **9 testes** e build TypeScript/Vite passaram. |
| Versão, templates e release | `python scripts/version.py check`; `python scripts/validate_deploys.py`; `python scripts/validate_release.py`; suites de `scripts` e `tools/deployer`; `git diff --check`. | **0.1.0** sincronizada, dez diretórios de deploy validados, **5** testes de scripts e **11** do deployer passaram. |
| Binário Linux AMD64 | PyInstaller 6.16.0, `file`, `--version`, `self-test --offline` e `sha256sum -c SHA256SUMS.txt` fora do checkout. | ELF x86-64 conferido em versão anterior; build **0.1.0** aguardando validação no CI; instalação systemd em host do cliente não ensaiada. |
| Operação real | Compose/build/run, PostgreSQL, bucket, backup/restore, systemd, WinSW/Windows, GHCR e serviços oficiais com credencial autorizada. | Pendente. Nenhum teste unitário substitui esse gate. |

Revisão manual sugerida: emitir/revogar um agente e tentar envio cruzado de outra empresa; subir XML repetido e divergente, conferir bytes e SHA-256 no download; verificar XML sem assinatura/protocolo como alegação limitada; comparar competência com registros duplicados, sem cobertura e com duas filiais; desligar/reiniciar coletor e conferir retry e NSU/spool; operar CBS com fixture e só depois, em ambiente habilitado, executar cenários oficiais individualmente.

## Pendências e riscos de aceite

1. Executar CI remoto, construir e testar o `.exe` Windows e o ELF Linux em hosts de destino, provar integridade do artefato e instalação dos serviços. Rodar Docker Compose completo com PostgreSQL, broker, Redis e S3; testar upgrade/restore do banco **e** dos objetos.
2. Obter credenciais e autorização explícita de contribuinte para validar Receita e NF-e Distribuição em cada ambiente. Confirmar WSDL/schema/versão em uso, ator apto, certificado, NSU e limites antes de tráfego real; registrar resposta com origem e hash. A rotina NF-e `distNSU` é opt-in, não homologada. Não afirmar cobertura documental ou autorização a partir de XML local.
3. Evoluir apuração fiscal: prova de cobertura de consultas CBS, conciliação por estabelecimento quando o retorno vem por CNPJ base, regras versionadas para créditos/estoque/split payment, validação de assinatura/schema/protocolo e revisão por responsável fiscal. Hoje os resultados são assistivos e de somente leitura.
4. O CGIBS publicou o MOC IBS v1.10 com etapa em homologação e rotas ainda propostas; **não há conector IBS em produção nesta branch**. CT-e/MDF-e/NFS-e também demandam adaptadores, credenciais, casos e gates independentes. Consultar [contratos e datas](CONTRATOS-OFICIAIS-2026.md) antes de implementar ou habilitar.
5. Planejar outbox transacional, métricas/alertas de lacuna incremental, retenção/backup dos XMLs, testes de escala e controle mais granular de credenciais para produção multiempresa. Revisar segurança de acesso a objetos e telemetria sem conteúdo fiscal/segredos.

**Rollback:** interromper coletor/agenda e manter estado/spool, reverter a imagem/tag com os mecanismos existentes e restaurar dados/objetos do backup quando necessário. Migration é aditiva, mas um downgrade descarta documentos e agentes; não usá-lo como rollback automático. Revogar tokens comprometidos, substituir o arquivo de segredo protegido e registrar a ação.

**Promoção:** conferir os checks da PR #1 antes do merge em `main`, verificar a publicação de `pre-release` e os digests `:develop`, e então conferir `v0.1.0` e os assets estáveis.
