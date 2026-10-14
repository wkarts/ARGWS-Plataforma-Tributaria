# Console web da Reforma Tributária

Frontend Vue 3 + TypeScript + Vite para a API FastAPI da plataforma. O modo claro é o padrão.

## Desenvolvimento

```bash
cd apps/web
npm ci
cp .env.example .env
npm run dev
```

O proxy de desenvolvimento encaminha `/api/*` para `http://localhost:8000` (ou `VITE_DEV_PROXY_TARGET`). A API deve estar acessível e inicializada. `npm test` executa verificações do contrato e do tratamento de valores fiscais; `npm run build` faz a checagem de tipos e gera `dist/`.

Em produção, sirva `dist/` pelo servidor web que encaminha `/api/*` à API. O arquivo público `config.js` permite mudar `apiBaseUrl` em tempo de deploy, sem reconstruir a imagem. A URL padrão é `/api/v1`; `VITE_API_BASE_URL` é usada no build caso `config.js` não defina a URL. Use HTTPS fora de `localhost`, sobretudo para PWA, autenticação e conferência SHA-256 no navegador.

## Contratos utilizados

- `POST /api/v1/auth/login` com `{username, password}`: `username` é um e-mail; `GET /auth/me` fornece `role`.
- `GET /capabilities` informa provedor, ambientes e recursos realmente habilitados. Ações de consulta e cadastro exigem papel `admin`.
- `GET/POST /companies` para seleção e cadastro autorizado de CNPJ.
- `POST /queries` com `{company_id, resource, environment}` e cabeçalho `Idempotency-Key`. A mesma chave é preservada na tentativa após falha de rede e trocada quando os parâmetros mudam.
- `GET /queries?company_id=...`, `GET /queries/{id}` e `GET /records?company_id=...&cursor=...` para estados e registros.
- `GET /coverage?company_id=...&environment=...` para a última conclusão por recurso/CNPJ base e alertas de sete e oito dias. O indicador não prova a completude histórica.
- `POST /queries/{id}/resolve` para que um administrador vincule um tíquete confirmado oficialmente ou encerre como falha, sempre com justificativa auditada. Apenas solicitações `uncertain` aceitam a ação.
- `GET /queries/{id}/raw` somente quando houver `raw_sha256`; o navegador confere o SHA-256 dos bytes antes de liberar o arquivo.

As consultas são incrementais; a interface não aceita um filtro de datas que o serviço oficial não fornece. Estados ativos são consultados a cada 12 segundos enquanto a aba estiver visível. Isso acompanha consultas já criadas, **não abre consultas periodicamente**. O estado `uncertain` exige investigação manual, sem reabertura automática. A cobertura informa quando a última consulta concluída se aproxima do limite de oito dias ou o ultrapassa, sem prometer backfill.

As colunas de crédito a apropriar, apropriado e utilizado são exibidas sem arredondamento implícito nem soma em ponto flutuante. Um campo ausente aparece como `—`, distinto de zero. O crédito presumido de estoque requer controle próprio e ainda não está implementado neste backend. Registros CBS comuns nunca são classificados como estoque presumido por inferência.

O access token de uma hora fica em `sessionStorage` até o encerramento da aba/sessão ou logout. A PWA armazena apenas o casco da interface e arquivos estáticos. O service worker exclui `/api/*` e `config.js`; respostas fiscais e credenciais não entram no cache offline. Offline, a interface pode abrir, mas não consulta a API nem exibe dados armazenados.
