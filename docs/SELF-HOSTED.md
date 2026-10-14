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

A prévia compara evidências locais com observações CBS efetivamente concluídas e identificadas pela origem. Ela não transmite declaração, não calcula crédito presumido por inferência e não valida assinatura, schema ou autorização online. A instalação self inicial executa ingestão e prévia com SQLite; não inclui worker de sincronização CBS nem habilita API IBS em produção.

A API CBS, quando habilitada em uma implantação que possua worker, exige credenciais autorizadas da matriz e `urlRetorno` HTTPS alcançável publicamente para o callback. A URL local `127.0.0.1` do perfil self não satisfaz esse contrato. A API IBS tem habilitação e contrato separados. Cada integração só deve ser liberada após teste autenticado, análise da cobertura e aceite fiscal específico. Veja [a matriz de contratos](CONTRATOS-OFICIAIS-2026.md) e [a prévia de apuração](APURACAO-ASSISTIDA.md).

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
