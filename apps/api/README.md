# API de integração CBS (FastAPI)

Este serviço registra consultas assíncronas da Apuração Assistida da **CBS**, guarda o arquivo original em armazenamento S3 compatível e disponibiliza projeções de conferência. `fixture` é uma simulação local. A integração real permanece desligada até configuração e homologação autenticada da credencial Receita Integra.

## Execução

Requer Python 3.12 ou superior, PostgreSQL, RabbitMQ, Redis e S3 compatível. Com os serviços disponíveis:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Preencha secrets e endereços em .env; não versione o arquivo.
alembic upgrade head
python -m app.bootstrap
uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log
# Em processos separados:
python -m app.worker
python -m app.scheduler
pytest -q
```

Gere `SECRET_KEY` com pelo menos 32 caracteres aleatórios. Configure `BOOTSTRAP_ADMIN_EMAIL` e senha de ao menos 12 caracteres para `python -m app.bootstrap`. A operação cria um administrador uma vez, imprime o identificador da organização e garante a existência do bucket S3. A senha do bootstrap **não é reaplicada** ao usuário existente. Após o primeiro acesso, use `/api/v1/auth/change-password`, rotacione ou retire o segredo inicial do deployment e guarde o novo acesso de modo seguro. O endpoint invalida tokens antigos. No deployment, a migration e o bootstrap são tarefas separadas; a API e o worker não fazem DDL no startup.

As variáveis de conexão seguem `.env.example`: `POSTGRES_*`, `REDIS_*`, `RABBITMQ_*`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION` e `S3_USE_SSL`. O bucket deve ser privado; configure backup, versionamento e, se exigido por política de retenção, Object Lock no provedor. O serviço não oferece endpoint de exclusão de arquivo bruto.

## Contrato REST

Autenticação `Authorization: Bearer <access_token>` em `/api/v1`, exceto o login e o callback dedicado. O JWT expira em 1 hora. `admin` cria empresas e consultas; demais usuários apenas consultam dados da organização. O bootstrap cria somente o administrador; gestão de usuários, MFA e concessão de múltiplos perfis ainda não são oferecidas.

| Método e rota | Função |
| --- | --- |
| `POST /api/v1/auth/login` | `{ "username": "email", "password": "..." }` → `{access_token,token_type}` |
| `POST /api/v1/auth/change-password` | `{current_password,new_password}`; revoga JWTs anteriores |
| `GET /api/v1/auth/me` | Identidade, papel e organização |
| `GET /api/v1/capabilities` | Ambientes e recursos habilitados; IBS permanece desligado |
| `GET /api/v1/companies` | Empresas da organização |
| `POST /api/v1/companies` | `{name,cnpj}`; verifica DV módulo 11 numérico/alfanumérico |
| `POST /api/v1/queries` | `{company_id,resource,environment}`, com header obrigatório `Idempotency-Key` |
| `GET /api/v1/queries?company_id=&cursor=&limit=` | Histórico paginado de consultas |
| `GET /api/v1/queries/{id}` | Estado, tíquete, SHA-256 e próximo polling |
| `POST /api/v1/queries/{id}/resolve` | Conciliação de abertura incerta, com ação `attach_ticket` ou `mark_failed` e nota auditada |
| `GET /api/v1/queries/{id}/raw` | Arquivo original privado, inclusive quando sua normalização falhar |
| `GET /api/v1/records?company_id=&resource=&query_id=&cursor=&limit=` | Snapshots projetados, em ordem decrescente |
| `GET /api/v1/coverage?company_id=&environment=` | Última conclusão por recurso, alerta aos 7 dias e lacuna após 8 dias |
| `HEAD/POST /api/v1/webhooks/cbs/{query_id}/{secret}` | Callback restrito por token único, fora das operações administrativas |
| `GET /health/live`, `GET /health/ready` | Processo e conectividade PostgreSQL/Redis |

`POST /queries` retorna HTTP 202 e estado `queued`. Repetir a mesma chave e o mesmo corpo retorna o mesmo recurso; reusar a chave com corpo diferente retorna 409. `resource` aceita `debitos`, `creditos`, `pagamentos`, `recolhimentos`; `environment` aceita `fixture`, `restricted`, `production`. Campos arbitrários de período não são aceitos: a API da Receita controla a janela incremental. Os estados possíveis são `queued`, `opening`, `awaiting`, `processing`, `completed`, `failed`, `uncertain`.

Exemplo:

```bash
curl -X POST https://seu-dominio/api/v1/queries \
  -H 'Authorization: Bearer <TOKEN_INTERNO>' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 2b8a6fd7-32d0-4d8e-a698-6d20e844769a' \
  -d '{"company_id":"<UUID>","resource":"creditos","environment":"fixture"}'
```

Valores monetários são enviados ao cliente como **strings decimais** para não passar por ponto flutuante. Em créditos, `amount` corresponde ao `cbs.apurado` informado; `credit_to_appropriate`, `credit_appropriated` e `credit_used` vêm de campos distintos da resposta oficial. O sistema não cria direito a crédito por cálculo próprio. Em pagamentos e recolhimentos, cada composição oficial gera uma linha com `period`, `amount` (total), `principal`, `penalty`, `interest`, DARF/tipo/chaveDFE e data de arrecadação na proveniência. Isso **não** baixa uma parcela comercial nem vincula automaticamente crédito a uma compra.

`records` é uma lista de **snapshots**. O mesmo documento pode aparecer novamente em outro tíquete como atualização; somar a lista gera duplicação. Para uma visão corrente ou conciliação com DFe, será necessária uma política versionada por chave/período/timestamp de atualização. O retorno bruto é preservado por tíquete com SHA-256, inclusive caso um leiaute novo impeça a projeção.

## Operação real e limites

Para habilitar o adapter oficial, configure `PROVIDER_MODE=receita`, `CBS_TOKEN_URL=https://api.receitafederal.gov.br/token`, `CBS_CLIENT_ID`, `CBS_CLIENT_SECRET`, `CBS_WEBHOOK_BASE_URL=https://...`, `CBS_CREDENTIAL_ORGANIZATION_ID` e `CBS_ALLOWED_BASES` (raízes CNPJ de oito caracteres explicitamente autorizadas). Habilite `CBS_RESTRICTED_ENABLED=true` após homologação restrita; `CBS_PRODUCTION_ENABLED` começa `false`. `CBS_PAYMENTS_ENABLED` começa `false`, pois o calendário divulgado em setembro de 2026 previa pagamentos e recolhimentos para novembro. O ambiente `fixture` só existe quando `PROVIDER_MODE=fixture`.

As credenciais oficiais são **de um único escopo por deployment**, limitado ao identificador de organização e às raízes CNPJ configuradas. Não há cofre nem credenciais individuais por organização/empresa nesta versão; para múltiplas organizações com credenciais reais distintas, implemente antes um vault e vinculação de credencial comprovada ao contribuinte. Não há chamada autenticada real validada nesta entrega.

O fluxo abre `POST /v2/{resource}/{cnpj_base}` com `urlRetorno`, aguarda tíquete e consulta `GET /v2/situacao/{tiquete}`. O callback aceita `HEAD` e, em `POST`, apenas antecipa uma consulta autenticada; ignora qualquer URL assinada que ele enviar. O worker aceita download apenas em HTTPS e em hosts configurados em `CBS_DOWNLOAD_HOSTS`, rejeita redirecionamentos, checa DNS público, prazo de expiração e limite de 100 MB. URLs assinadas, bearer tokens e segredos não entram no retorno da API, na auditoria ou em logs de aplicação. Configure o reverse proxy e Uvicorn sem access log da rota de callback, pois o segredo está no caminho. O token OAuth é guardado no Redis entre processos, com lock e limite conservador de duas emissões por hora.

Uma abertura sem confirmação pode ter sido aceita pela Receita: fica `uncertain`, **sem repetição automática do POST**. O operador consulta o ambiente oficial e registra tíquete confirmado por `resolve/attach_ticket`, ou fundamenta `mark_failed`. Já o `GET` de situação é repetível e também recupera webhook não recebido. A aplicação limita conservadoramente a quatro aberturas em 24 horas por organização, ambiente e CNPJ base, e mantém apenas uma consulta ativa por base/ambiente. O limite publicado é de quatro aberturas por dia para o endpoint; confirme a quota contratual da credencial ao homologar.

Débitos e créditos possuem janela incremental máxima publicada de oito dias (primeira consulta: desde o primeiro dia do mês corrente). A execução periódica aqui **processa consultas solicitadas**, mas **não agenda uma nova abertura diária automaticamente**. Monitore `GET /coverage`, inicie novas consultas antes de sete dias e trate `gap_over_eight_days` como lacuna potencial que exige procedimento oficial de recuperação; uma nova consulta não comprova backfill histórico. `first_query_limited=true` também não comprova cobertura anterior ao mês corrente.

As tabelas `stock_credit_claims` e `commercial_installments` estão segregadas da projeção governamental e entre si. Elas não têm endpoints de lançamento, cálculo de 9,25%, apropriação parcelada ou baixa automática nesta versão; não devem ser consideradas um módulo de apuração fiscal em operação. O IBS tem contrato e disponibilidade próprios e segue desabilitado. A migration inicial é reversível para um ambiente vazio; o `downgrade` apaga dados, portanto, em operação, reverta a aplicação sem apagar banco e restaure backup somente com plano de dados.

## Referências oficiais consultadas

- [Regras comuns das APIs assíncronas da CBS](https://docs.receitafederal.gov.br/apuracao-cbs/guiaapiassincrona.html)
- [Créditos CBS](https://docs.receitafederal.gov.br/apuracao-cbs/credito.html)
- [Débitos CBS](https://docs.receitafederal.gov.br/apuracao-cbs/debito.html)
- [Pagamentos CBS](https://docs.receitafederal.gov.br/apuracao-cbs/pagamentos.html)
- [Recolhimentos CBS](https://docs.receitafederal.gov.br/apuracao-cbs/recolhimentos.html)
- [Situação da solicitação](https://docs.receitafederal.gov.br/apuracao-cbs/situacao-solicitacao.html)
- [Manual do DV do CNPJ alfanumérico](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj/manual-dv-cnpj.pdf)
- [Manual Receita Integra v1.4](https://www.cgibs.gov.br/upload/arquivos/202609/05164748-08-manual-de-integracao-tecnica-receita-integra-v-1-4.pdf)
