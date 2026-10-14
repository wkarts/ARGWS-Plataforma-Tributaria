# Coletor local de XML fiscal

Processo pequeno, sem dependências de runtime fora da biblioteca padrão Python. Lê XMLs fiscais em diretórios locais configurados e envia os **bytes originais** por HTTPS à plataforma. Pode ser executado como binário empacotado ou com `python3 tools/collector/collector.py`. Os originais não são alterados nem excluídos.

## Contrato com a API

1. Cadastre a empresa na plataforma e obtenha seu UUID (`company_id`).
2. Um administrador cria um token exclusivo da empresa com `POST /api/v1/agents`, enviando `{"company_id":"UUID","name":"Coletor unidade 1"}` com seu Bearer administrativo. **A resposta exibe o token `rta_...` uma única vez.** Guarde-o como segredo. Revogue-o com `DELETE /api/v1/agents/{id}`.
3. O coletor envia `POST /api/v1/documents/ingest?company_id=UUID`, `Content-Type: application/xml`, `Authorization: Bearer rta_...`, `X-Content-SHA256: <sha256>` e `X-Idempotency-Key: <sha256>`. O corpo é o XML original, sem envelope, reserialização nem conversão de encoding.
4. Só marca o hash como entregue após `200`/`201` com JSON válido e hash correspondente, quando devolvido. `duplicate: true` confirma uma repetição aceita pelo servidor. O banco local vincula estado a um único `company_id`; não reutilize o mesmo `state_dir` com outra empresa.

O padrão para pastas locais exige a presença do elemento `ds:Signature` XMLDSig; isso **não verifica criptograficamente** certificado, cadeia, autorização ou autenticidade. A API executa suas próprias verificações e pode rejeitar XML de outra empresa. Para incluir XMLs locais sem essa marcação, configure `--include-unsigned` de forma consciente. A busca direta na SEFAZ é **opcional** e limitada nesta versão à Distribuição DF-e de NF-e por `distNSU`; o coletor não assina documentos, não manifesta destinatário e não consulta CT-e/MDF-e automaticamente.

## Configuração e CLI

Exemplo Linux (os caminhos Windows também são absolutos, como `C:\\Dados\\NFe`):

```bash
python3 tools/collector/collector.py configure \
  --config ./config.json \
  --server-url https://fiscal.exemplo.com.br \
  --company-id 8813d319-2376-44c3-b147-f1e1a7061518 \
  --root /dados/nfe --root /dados/cte \
  --state-dir /var/lib/fiscal-xml-collector

export FISCAL_COLLECTOR_TOKEN='rta_token_recebido_uma_vez'
python3 tools/collector/collector.py check --config ./config.json
python3 tools/collector/collector.py once --config ./config.json
python3 tools/collector/collector.py status --config ./config.json
python3 tools/collector/collector.py serve --config ./config.json
```

`check` valida configuração, diretórios e disponibilidade do token **sem fazer chamada à rede**; `check --no-token` permite verificar apenas diretórios e configuração sob a conta do serviço. `check --expect-state-dir PATH` exige o estado em local autorizado pelo supervisor. `self-test --offline` testa parser e armazenamento mínimo sem configuração nem rede. `once` faz uma rodada, imprime contagens JSON e retorna 1 se houve falha de envio; `serve` repete até SIGTERM/SIGINT. `status` mostra totais, últimas falhas locais e próximo retry, sem imprimir token ou conteúdo XML. `--version` lê o `VERSION` da distribuição; os binários incluem esse arquivo.

O arquivo JSON gerado não contém credencial. Em serviço, use um arquivo de token separado, com permissões `0600` no Linux, ou `FISCAL_COLLECTOR_TOKEN_FILE=/caminho/token`. Variável `FISCAL_COLLECTOR_TOKEN` é alternativa para operação interativa. O arquivo do token pode ser substituído durante a operação; `serve` o relê em cada ciclo. Nunca coloque token em argumento de linha de comando, log ou JSON.

Campos adicionais editáveis no JSON: `recursive` (padrão `true`), `signed_only` (`true`), `stable_seconds` (`10`), `poll_seconds` (`30`), `timeout_seconds` (`30`), `max_file_bytes` (`10485760`, limite do endpoint), `batch_limit` (`100`), `retry_min_seconds` (`10`) e `retry_max_seconds` (`3600`). O programa só considera arquivos `.xml`, ignora symlinks e exige tamanho e `mtime` inalterados entre abertura e leitura. O servidor limita cada XML a 10 MiB; arquivos maiores aparecem em `ignored` até serem alterados. `state_dir` deve persistir entre reinicializações e ser protegido. O SQLite contém caminhos de arquivos e hashes; faça backup dele se precisar conservar o histórico local.

### Distribuição NF-e pela SEFAZ (opcional)

É possível habilitar o conector com certificado A1 em **PEM e chave separada**, sem compartilhar a chave privada com a plataforma. O CNPJ configurado deve corresponder à empresa cadastrada na API e estar autorizado a consultar a Distribuição DF-e. O módulo não importa PFX/P12 nem valida criptograficamente a assinatura recebida.

```bash
python3 tools/collector/collector.py configure \
  --config ./config-sefaz.json \
  --server-url https://fiscal.exemplo.com.br \
  --company-id 8813d319-2376-44c3-b147-f1e1a7061518 \
  --state-dir /var/lib/fiscal-xml-collector \
  --sefaz-cnpj 12345678000195 \
  --sefaz-env homologation \
  --sefaz-cert-pem /etc/fiscal-xml-collector/cert.pem \
  --sefaz-key-pem /etc/fiscal-xml-collector/key.pem
```

`--root` é opcional nessa modalidade; é possível combinar a coleta SEFAZ com várias pastas locais. `check` carrega o par PEM sem fazer chamada à rede; no Linux a chave privada exige `0600` ou mais restrita. O JSON contém somente caminhos de certificados, jamais material privado. O ambiente aceita `production` ou `homologation` e mantém NSU e janela de consulta por CNPJ/ambiente em SQLite.

Antes de avançar o NSU, cada retorno salva o **SOAP integral**, cada `docZip` gzip e cada XML descompactado em `state_dir/sefaz_nfe/CNPJ/ambiente`, com arquivo temporário, `fsync` e renomeação atômica; no Linux, sincroniza também o diretório. O checkpoint do NSU só é gravado após a guarda local desses itens. Se houver falha de disco, o NSU anterior é mantido; novo retorno do mesmo NSU encontra o arquivo pelo NSU e SHA. **O NSU representa recebimento local durável, não confirmação de upload na API.** Upload com falha continua na fila para retry.

Somente `procNFe`/`procEventoNFe` cujo schema confere com a raiz e contém `ds:Signature` entra no spool `ready` e na fila de envio. `resNFe`/`resEvento` permanecem como `summary_unprocessed`, mesmo que incluam `Signature`; schema desconhecido ou conflitante fica em `schema_quarantine`; outros XMLs sem assinatura ficam em `unsigned_unprocessed`. SOAP, XML e gzip são preservados para conferência, e nenhum resumo vira uma nota completa por presunção. `status` apresenta o último NSU, `cStat`, janela de consulta, classes de documentos e estados do upload dos itens `ready`.

O spool tem limite padrão de **2 GiB** (`--sefaz-max-spool-bytes` para alterar, mínimo 256 MiB). Antes de cada consulta reserva 128 MiB para o próximo lote e verifica o espaço livre; quando atinge o limite, informa `storage_full`/`disk_low` e não avança NSU. Dimensione armazenamento e backup para os SOAPs, gzip e XMLs conservados; não remova o spool enquanto houver itens pendentes. O retorno 137 e o fim do lote 138 respeitam intervalo de 61 minutos; 656 também impõe espera e preserva o NSU. Erros de transporte e persistência aplicam retry com backoff.

Estados: `pending` aguarda envio/retry; `sent` confirmado; `ignored` grande demais, XML inválido ou sem assinatura; `rejected` erro HTTP permanente (400, 404, 413, 415 ou 422). Uma mudança de tamanho/mtime no arquivo gera nova avaliação. HTTP 401/403 aguarda pelo menos cinco minutos e interrompe o lote atual; 429 e indisponibilidade do servidor também interrompem o lote, evitando saturação. Respostas 3xx não são seguidas para não encaminhar o token a outro destino. O backoff exponencial tem jitter, respeita `Retry-After` numérico e sobrevive a reinício. O sistema remoto também deduplica por organização, empresa e SHA-256.

## Linux como serviço

Use um binário Linux realmente gerado e testado, o token e o JSON com `state_dir` igual a `/var/lib/fiscal-xml-collector`. Para SEFAZ, o JSON deve apontar para `/etc/fiscal-xml-collector/cert.pem` e `/etc/fiscal-xml-collector/key.pem`, e passe os dois PEM como quarto e quinto argumentos. Como root:

```bash
bash tools/collector/systemd/install.sh \
  ./fiscal-xml-collector-linux-amd64 ./config.json ./agent-token
# Se SEFAZ estiver habilitada:
# bash tools/collector/systemd/install.sh ./fiscal-xml-collector-linux-amd64 \
#   ./config-sefaz.json ./agent-token ./cert.pem ./key.pem
systemctl status fiscal-xml-collector.service
journalctl -u fiscal-xml-collector.service -n 100 --no-pager
```

O instalador cria a conta `fiscal-collector`, copia binário/configuração/token e os PEM opcionais em locais duráveis, restringe permissões e ativa a unidade systemd. A unidade usa `LoadCredential`, `StateDirectory` e escrita restrita. Conceda a essa conta leitura e travessia dos diretórios e arquivos XML do cliente; `check --no-token` no instalador valida as raízes e o par PEM. Para remover **apenas a unidade**, execute `bash tools/collector/systemd/uninstall.sh` como root. Isso preserva binário, configuração, token, estado SQLite e XMLs.

## Windows como serviço

O executável CLI isolado **não implementa sozinho o protocolo Windows Service**. Use [WinSW](https://github.com/winsw/winsw), um wrapper Windows Service distribuído separadamente, e um executável Windows do coletor realmente compilado. Em PowerShell elevado:

```powershell
.\tools\collector\windows\install-winsw.ps1 `
  -WinSWPath C:\Pacotes\WinSW.exe `
  -CollectorPath C:\Pacotes\fiscal-xml-collector-windows-amd64.exe `
  -ConfigPath C:\Pacotes\config.json `
  -TokenFile C:\Pacotes\agent-token
```

O script valida a configuração, copia os executáveis, o JSON, o segredo e os PEM opcionais a `Program Files\FiscalXmlCollector`, atualiza no JSON copiado os caminhos PEM, cria XML de serviço, aplica ACL somente para SYSTEM/Administradores e inicia WinSW. Por padrão o serviço usa LocalSystem; ele precisa de permissão para ler os XMLs. Para compartilhamentos de rede, configure uma conta de serviço própria e permissões de rede adequadas antes de iniciar. O estado deve apontar para um caminho persistente gravável pela conta de serviço, como `C:\ProgramData\FiscalXmlCollector\state`. Após revogar o token, atualize `agent-token` com ACL restrita e reinicie o serviço se necessário. Para remover a unidade SCM sem apagar dados:

```powershell
.\tools\collector\windows\uninstall-winsw.ps1
```

O script de instalação não baixa WinSW nem declara que um `.exe` existe antes do build. Confira a procedência, checksums e assinatura dos binários distribuídos.

## Testes locais

```bash
python3 -m unittest discover -s tools/collector/tests -v
python3 tools/collector/collector.py self-test --offline
```

Os testes simulam o envio sem rede, incluindo deduplicação, retry persistente, bytes originais, isolamento da empresa, token e contrato HTTP. Não substituem teste autenticado com os serviços oficiais nem prova de instalação em Windows/Linux real.
