# Instalação própria e coleta nos clientes

Esta instalação fica sob controle do escritório ou cliente que a executa. Uma instalação mantém sua própria identidade visual, usuários, empresas e evidências; cada empresa recebe um UUID e uma credencial de ingestão exclusivos. A instalação de demonstração não transmite XML ou credenciais para uma infraestrutura do fabricante. O operador é responsável pelo domínio, TLS, cópias de segurança e acesso dos usuários.

## Dois componentes com papéis distintos

| Componente | Local | Dados |
| --- | --- | --- |
| Plataforma self | Infraestrutura do escritório/cliente; `deploy/self/compose.yaml` com API, interface e SQLite/arquivos locais | Usuários, CNPJs cadastrados, documentos recebidos, observações e identidade visual da instalação |
| Coletor | Computador Windows/Linux com acesso aos diretórios XML do contribuinte; instalador gráfico Windows e interface Linux | Catálogo local SQLite, estado e spool isolados por empresa; tokens e chave PEM em arquivos protegidos |

O coletor se comunica **apenas com a URL HTTPS da plataforma configurada na instalação**, com token de escopo da empresa. Instalar o coletor não instala a API nem cria acesso à Receita; cada módulo tem configuração e permissões próprias. Para mais de um CNPJ, cadastre as empresas na plataforma, gere um token para cada uma e associe cada perfil no coletor ao UUID e à pasta corretos. Não reutilize token ou estado entre empresas.

## Instalar a plataforma self

No host que receberá os dados, prepare Docker Engine/Compose v2, espaço persistente e um domínio com HTTPS. No Windows, o servidor Docker requer Docker Desktop/WSL2; o instalador Windows do coletor funciona separadamente.

```bash
python3 deploy/self/init_secrets.py --project escritorio-fiscal --email admin@exemplo.com.br
docker compose --env-file deploy/self/.env -f deploy/self/compose.yaml up -d --build
```

O inicializador solicita uma senha administrativa forte sem exibi-la, cria `deploy/self/.env` e segredos privados com acesso restrito, sem conta ou senha padrão no código. A UI escuta em `127.0.0.1:48080` por padrão. O volume `fiscal_data` mantém o SQLite e os objetos originais sob `/data`. Um proxy HTTPS da própria instalação deve expor a UI e a rota de ingestão aos computadores coletores, com autenticação e controles de rede adequados. Depois do primeiro login, esvazie o segredo de bootstrap; mantenha o arquivo vazio para próximos `up` do Compose:

```bash
python3 deploy/self/init_secrets.py --erase-bootstrap-password
```

Não execute `docker compose down -v` para atualizar ou parar a plataforma. Pare escritas antes de copiar o volume, faça backup conjunto do SQLite e dos objetos, e ensaie a restauração. Guarde `.env`, chave de assinatura e backup em local protegido; a perda da chave invalida sessões existentes. Em uma atualização, preserve esses arquivos e o volume, aplique migrations e confira a prontidão antes de liberar coletores.

## Fluxo de operação

1. Entre na interface, cadastre a empresa com CNPJ e crie sua credencial de ingestão. O token é apresentado uma única vez.
2. Instale o pacote gráfico do coletor no computador que possui os XMLs. Configure URL HTTPS, UUID da empresa, CNPJ, token, diretórios de leitura, serviço e, opcionalmente, certificado/chave PEM para a distribuição NF-e.
3. Execute a verificação local na interface antes de iniciar o serviço. Ela valida configuração e permissões sem consultar a rede fiscal.
4. Na plataforma, confira documentos recebidos, hashes, competência, direção e divergências na prévia de apuração. Um XML sem chave, protocolo ou assinatura verificável exige análise humana; a presença da tag XMLDSig não prova autenticidade.

O conector SEFAZ implementado é **somente NF-e `distNSU`** com certificado A1 PEM, condicionado à autorização do ator consultado. NFC-e e XMLs de emissão/recepção podem entrar pelas pastas autorizadas; este conector não garante descoberta universal de NFC-e, CT-e, MDF-e ou NFS-e nas fontes oficiais. Resumos, eventos e XML integral têm classificações diferentes. Coordene o NSU com outros softwares do mesmo contribuinte.

Na troca de certificado, o coletor conserva o par atual e o anterior para recuperação. Pares antigos sem referência só são removidos após 24 horas e uma nova gravação do perfil; mantenha backup protegido e revise periodicamente o diretório privado `certificates`.

## Receita e apuração assistida

A prévia compara evidências locais com observações CBS efetivamente concluídas e identificadas pela origem. Ela não transmite declaração, não calcula crédito presumido por inferência e não valida assinatura, schema ou autorização online. A API IBS tem habilitação e contrato separados. Veja [a matriz de contratos](CONTRATOS-OFICIAIS-2026.md) e [a prévia de apuração](APURACAO-ASSISTIDA.md).

O processador CBS local inicia junto do Compose, mas fica inativo enquanto `PROVIDER_MODE=disabled`. A integração está **desabilitada por padrão** e só deve ser ativada com credencial autorizada do estabelecimento matriz, acesso ao recurso escolhido, teste em produção restrita e uma URL HTTPS pública da própria instalação. A Receita testa `urlRetorno` com `HEAD` antes de aceitar a abertura. O proxy deve encaminhar `/api/v1/webhooks/cbs/` à API, preservar o caminho completo e não registrar o segredo na URL. `127.0.0.1`, URL privada ou somente rede interna não atende ao callback. A publicação do webhook requer controle de abuso e disponibilidade durante o processamento. O retorno é conferido pelo endpoint autenticado de situação; o corpo do webhook não é usado como fonte de verdade para o link de download.

Para habilitar **somente produção restrita** após confirmar autorização e alcance externo, obtenha `organization_id` em `/api/v1/auth/me`, configure as raízes CNPJ autorizadas e grave o Client Secret sem inseri-lo no histórico do shell:

```bash
python3 deploy/self/init_secrets.py --set-cbs-client-secret
```

Adicione ao `deploy/self/.env` os valores `PROVIDER_MODE=receita`, `CBS_TOKEN_URL` (HTTPS da API de autenticação efetivamente concedida), `CBS_CLIENT_ID`, `CBS_CLIENT_SECRET_FILE=/run/customer-secrets/cbs-client-secret`, `CBS_WEBHOOK_BASE_URL=https://seu-dominio.example`, `CBS_CREDENTIAL_ORGANIZATION_ID=<organization_id>`, `CBS_ALLOWED_BASES=<raiz_de_8_digitos>` e `CBS_RESTRICTED_ENABLED=true`. Mantenha `CBS_PRODUCTION_ENABLED=false`, `CBS_PAYMENTS_ENABLED=false` e `CBS_AUTO_SYNC_ENABLED=false` até homologar cada operação e ambiente. Depois, inicie com `docker compose --env-file deploy/self/.env -f deploy/self/compose.yaml up -d --build --wait`. A UI passa a permitir consultas manuais de débitos/créditos apenas para a organização e raízes configuradas. A sincronização automática é outra opção explícita e não prova cobertura histórica: há janela incremental de oito dias e a primeira consulta tem escopo limitado. Ao girar somente o segredo, execute explicitamente `docker compose --env-file deploy/self/.env -f deploy/self/compose.yaml restart api cbs-worker`; `up -d` isolado pode manter os processos antigos em memória.

O worker é único por volume SQLite, guarda o orçamento de emissão OAuth no banco e mantém o bearer token somente em memória. Ele conserva a abertura remota indeterminada para revisão manual e não a repete automaticamente. Monitore a saúde de `cbs-worker`, consultas pendentes, lacunas e o backup conjunto de SQLite/arquivos. Se a credencial for revogada, volte `PROVIDER_MODE=disabled` e reinicie; consultas inacabadas permanecem registradas para reconciliação. Esta entrega valida apenas testes simulados; **não houve chamada autenticada nem homologação da Receita**. Há uma credencial matriz por instalação; cada raiz em `CBS_ALLOWED_BASES` precisa autorização comprovada para essa credencial. Clientes que exigem credenciais próprias precisam isolamento adicional antes de ativar consultas deles. Se várias empresas cadastradas compartilham a mesma raiz CNPJ, esta versão bloqueia a abertura CBS e a agenda automática para essa raiz, pois um recibo por raiz não pode ser atribuído com segurança a uma única filial. Os XMLs e a prévia local dessas empresas continuam disponíveis.

## Identidade visual e distribuição

A marca é da **instalação**, independente das empresas cadastradas. O administrador configura nome, cores e assets na plataforma. Para binários já compilados, nome do produto/instalador e metadados de assinatura são neutros; personalizar também esses campos requer compilar e assinar um pacote específico da marca. Imagens buscadas diretamente no GHCR público preservam o nome de seu proprietário no URL e em atestações. Para distribuição sem essa referência, construa as imagens localmente nesta infraestrutura ou publique em um registro sob controle do cliente. Não apresente o nome de um repositório upstream nem o canal de desenvolvimento em telas, relatórios ou arquivos entregues como marca do cliente.

## Próximos gates

| Capacidade | Evidência necessária antes de anunciar operação real |
| --- | --- |
| Windows | Instalação gráfica, elevação, serviço WinSW embutido, atualização que preserve SQLite/segredos, envio HTTPS e desinstalação em host Windows de destino |
| Linux | GUI sob desktop compatível, serviço systemd, permissões de leitura/escrita e retenção do estado após upgrade |
| Self | Migração SQLite, concorrência de ingestão, isolamento de empresas, backup/restauração do volume, TLS e monitoramento no host escolhido |
| Receita/SEFAZ | Credencial e papel autorizados, teste autenticado por fonte/ambiente, limites, retorno bruto/hash, tratamento de falhas e revisão fiscal |
| White label | Primeira carga, cache/manifesto, APIs, PDFs, serviço, instalador, binários e metadados examinados para cada pacote gerado |
