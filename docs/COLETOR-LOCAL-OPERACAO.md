# Coletor local de XML — instalação e operação

O coletor roda no ambiente do cliente, lê XMLs individuais dos diretórios autorizados e envia os arquivos por HTTPS ao endpoint de ingestão da plataforma. Opcionalmente, pode consultar a **distribuição NF-e por NSU** com um certificado instalado no cliente. Ele mantém uma fila SQLite local para retomar envios após falhas e não modifica nem remove os XMLs de origem. Cada instalação é vinculada a uma empresa por `company_id`; use um diretório de estado distinto por empresa. A API precisa estar implantada e fornecer um token de escopo adequado antes do primeiro envio.

## Obter e verificar o binário

Quando publicados pelo fluxo de CI, os assets previstos são `fiscal-xml-collector-linux-amd64` e `fiscal-xml-collector-windows-amd64.exe`, além de `SHA256SUMS.txt`. Baixe o binário da arquitetura correspondente junto com o manifesto **da mesma release** e compare o SHA-256 antes de executá-lo. A pré-release contínua `pre-release` corresponde a `develop`; releases `vX.Y.Z` correspondem a `main` e não devem ser sobrescritas. Não misture um binário de uma release com manifesto ou scripts de outra.

No Linux, verifique com `sha256sum -c SHA256SUMS.txt` na pasta que contém os assets da release. No Windows, compare o valor de `(Get-FileHash -Algorithm SHA256 .\fiscal-xml-collector-windows-amd64.exe).Hash.ToLowerInvariant()` com a linha correspondente do manifesto. O CI valida o executável sem contato com a plataforma, inclusive cabeçalho de arquitetura, `--version` e `self-test --offline`; isso não substitui um teste de envio autenticado no ambiente do cliente.

## Configuração

O exemplo Linux abaixo pressupõe uma conta de serviço com acesso de **leitura** a `/dados/fiscais/xml`, acesso de escrita exclusivo a `/var/lib/fiscal-xml-collector` e TLS válido no servidor. Substitua domínio e UUID pelos valores reais. O comando `configure` grava JSON sem token:

```bash
install -d -m 0755 /opt/fiscal-xml-collector
install -m 0755 fiscal-xml-collector-linux-amd64 /opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64
/opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64 configure \
  --config /etc/fiscal-xml-collector/config.json \
  --server-url https://fiscal.exemplo.com.br \
  --company-id 00000000-0000-4000-8000-000000000001 \
  --root /dados/fiscais/xml \
  --state-dir /var/lib/fiscal-xml-collector \
  --token-file /etc/fiscal-xml-collector/agent-token
```

`--root` pode ser repetido. Por padrão a varredura é recursiva, processa somente arquivos individuais `.xml` estáveis, **não abre ZIP**, e aceita apenas XMLs que contenham o elemento XMLDSig `Signature`. A presença desse elemento **não comprova a validade criptográfica da assinatura**; esta versão também não faz validação criptográfica ou consulta de autorização na plataforma. `--flat` desliga a recursão; `--include-unsigned` amplia a coleta de pastas e deve ser escolhido conscientemente. Arquivos alterados durante a leitura são reavaliados. Não aponte `state_dir` para uma raiz de XML ou compartilhamento com outros coletores. O limite por XML é 10 MiB, alinhado ao endpoint de ingestão.

Guarde o token de ingestão em arquivo protegido e aplique `chmod 600` no Linux. A unit systemd de exemplo usa `LoadCredential`: o arquivo original `/etc/fiscal-xml-collector/agent-token` pode ser lido pelo systemd e é entregue ao processo numa cópia temporária. Crie a conta `fiscal-collector`, permita a ela ler o JSON (`chown root:fiscal-collector /etc/fiscal-xml-collector/config.json && chmod 640 /etc/fiscal-xml-collector/config.json`) e os diretórios de XML, e mantenha o original do token legível apenas por root. O token também pode vir de `FISCAL_COLLECTOR_TOKEN` ou de outra variável indicada em `--token-env`; cuidado com exposição em gerenciadores de processos, scripts e backups. `FISCAL_COLLECTOR_TOKEN_FILE` pode apontar para um arquivo montado como segredo. A configuração não deve conter a credencial em texto. No Windows, restrinja a ACL do arquivo de token, da configuração e do diretório de estado à conta de serviço e aos administradores.

Configure um diretório por instalação no Windows, por exemplo `C:\ProgramData\FiscalXmlCollector\config.json` e `C:\ProgramData\FiscalXmlCollector\state`. Use os mesmos comandos com o `.exe`, trocando os caminhos e barras conforme o PowerShell:

```powershell
.\fiscal-xml-collector-windows-amd64.exe configure `
  --config 'C:\ProgramData\FiscalXmlCollector\config.json' `
  --server-url 'https://fiscal.exemplo.com.br' `
  --company-id '00000000-0000-4000-8000-000000000001' `
  --root 'D:\Fiscal\XML' `
  --state-dir 'C:\ProgramData\FiscalXmlCollector\state' `
  --token-file 'C:\ProgramData\FiscalXmlCollector\token'
```

## Consulta opcional à distribuição NF-e

O modo de pastas funciona sem certificado. Para consultar diretamente a **NF-e Distribuição DF-e** a partir do ambiente do cliente, acrescente ao mesmo `configure` as opções `--sefaz-cnpj CNPJ`, `--sefaz-env homologation` (ou `production`), `--sefaz-cert-pem /caminho/cert.pem` e `--sefaz-key-pem /caminho/key.pem`. As quatro opções devem ser fornecidas juntas. `--root` pode ser omitido se esta instalação tratar apenas a distribuição NF-e. Opcionalmente, `--sefaz-max-spool-bytes` limita o espaço reservado ao spool, entre 256 MiB e 100 GiB; o padrão é 2 GiB e o coletor reserva espaço antes de cada consulta. O CNPJ é a identidade consultada no serviço de distribuição e deve estar autorizado para o certificado. No Windows, use caminhos absolutos e proteja os arquivos PEM por ACL. A configuração guarda **caminhos**, não o conteúdo da chave privada. Mantenha a chave no cliente, com acesso restrito à conta do serviço, e não a envie à plataforma.

Por exemplo, configure simultaneamente as raízes locais e a consulta NF-e em homologação no Linux. Substitua `SEU_CNPJ_DE_14_POSICOES` pelo CNPJ autorizado do contribuinte antes de executar:

```bash
/opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64 configure \
  --config /etc/fiscal-xml-collector/config.json \
  --server-url https://fiscal.exemplo.com.br \
  --company-id 00000000-0000-4000-8000-000000000001 \
  --root /dados/fiscais/xml \
  --state-dir /var/lib/fiscal-xml-collector \
  --token-file /etc/fiscal-xml-collector/agent-token \
  --sefaz-cnpj SEU_CNPJ_DE_14_POSICOES \
  --sefaz-env homologation \
  --sefaz-cert-pem /etc/fiscal-xml-collector/cert.pem \
  --sefaz-key-pem /etc/fiscal-xml-collector/key.pem
```

O coletor usa a consulta sequencial `distNSU`, mantém o último NSU e o próximo horário de consulta por CNPJ e ambiente no estado local e respeita a espera de pelo menos uma hora após retorno sem documentos ou fim da sequência; `cStat=656` também adia nova tentativa. Combine o uso do NSU com os outros programas do cliente que consultam o mesmo CNPJ: o checkpoint local não se sincroniza com eles. Ele guarda a resposta SOAP, o `docZip` e o XML descompactado em spool privado; o NSU só avança depois da gravação durável do lote. **O NSU atesta a guarda local, não a entrega à plataforma.** O envio à plataforma passa pela mesma fila de XML e token da empresa. Apenas `procNFe`/`procEventoNFe` de esquema compatível com elemento XMLDSig vão à fila automática. Resumos `resNFe`/`resEvento` ficam marcados como `summary_unprocessed`, e esquemas desconhecidos ou incompatíveis ficam em quarentena local; nenhum deles é transmitido automaticamente como documento completo. Monitore espaço disponível, pendências e quarentena e faça backup do estado e do spool. Não remova o spool ou reinicie o NSU para tentar recuperar dados.

Este conector cobre apenas **distribuição de NF-e por NSU**. Não manifesta o destinatário, não busca indiscriminadamente por chave, não consulta CT-e, MDF-e ou NFS-e, não valida criptograficamente a assinatura e não comprova autorização da NF-e. O retorno oficial depende do papel do CNPJ, da disponibilidade no serviço e, em certos casos, de manifestação. Os testes locais não constituem homologação com a SEFAZ; valide certificado, endpoint, papel, respostas reais, limites e recuperação em homologação antes de habilitar produção.

## Validação inicial e rotina

Rode `--version`, `self-test --offline`, `check --config CAMINHO`, `once --config CAMINHO` e `status --config CAMINHO`, nessa ordem. `check` verifica caminhos/configuração, token e, se habilitado, a leitura e combinação do par PEM **sem abrir conexão de rede**. `once` faz uma varredura, pode transmitir XMLs e, no modo SEFAZ, pode consultar a distribuição oficial: execute somente com token, empresa, CNPJ, ambiente, certificado e URL conferidos. `status` informa totais locais, NSU/horário da próxima consulta, classificações e pendências, sem consultar o servidor. O comando `serve --config CAMINHO` mantém ciclos periódicos sob supervisão do sistema operacional.

No Linux, instale a unit `fiscal-xml-collector.service` da mesma release em `/etc/systemd/system/`, revise `User`, `Group`, `ExecStart`, `ReadWritePaths` e as permissões conforme o host, então execute `systemctl daemon-reload`, `systemctl enable --now fiscal-xml-collector` e `systemctl status fiscal-xml-collector`. A unit inclui `LoadCredential` para ler `/etc/fiscal-xml-collector/agent-token` e fixa o diretório de estado em `/var/lib/fiscal-xml-collector`; alinhe a configuração a esses caminhos antes de iniciar. Como alternativa, `install-fiscal-xml-collector-systemd.sh BINARIO CONFIG_JSON ARQUIVO_TOKEN [CERT_PEM KEY_PEM]` cria a conta e instala os arquivos e a unit. Quando passar o par PEM, faça `CONFIG_JSON` apontar para `/etc/fiscal-xml-collector/cert.pem` e `/etc/fiscal-xml-collector/key.pem`: o script copia o certificado para leitura da conta de serviço e a chave com permissão `0600` dessa conta. Prepare `CONFIG_JSON` numa pasta temporária **diferente** de `/etc/fiscal-xml-collector/config.json` e revise os caminhos antes de executar o script como root. `uninstall-fiscal-xml-collector-systemd.sh` desabilita o serviço e preserva dados e segredos para revisão manual.

No Windows, use `install-fiscal-xml-collector-winsw.ps1` da mesma release com o wrapper WinSW conferido e instalado no host; o `.exe` do coletor é um processo de console e não implementa por si a interface nativa do Service Control Manager. Se `sefaz_nfe` estiver configurado, o instalador copia o par PEM para seu diretório, reescreve os caminhos no JSON instalado e aplica ACL para SYSTEM e Administradores. Ele usa LocalSystem por padrão; para outra conta, ajuste a ACL e as permissões das pastas XML e do estado antes de iniciar. Veja os parâmetros e pré-requisitos no README do coletor antes de executar o instalador. O script `uninstall-fiscal-xml-collector-winsw.ps1` remove o serviço, preservando os dados.

A base `state.sqlite3` guarda estado, caminhos, hash e tentativas, e deve ser protegida e incluída no backup operacional junto com o spool da SEFAZ, se habilitado. Antes de restaurá-la ou trocar `company_id`, pare o serviço. Se perder o estado local, o servidor ainda precisa tratar reenvios por hash de forma idempotente; confirme isso em homologação. Monitore `pending_retries`, o último sucesso e o espaço livre do estado. A fila marca sucesso apenas após resposta de aceitação do servidor. Em caso de token revogado, indisponibilidade de rede ou rejeição do servidor, verifique as mensagens e corrija a causa antes de reiniciar; não apague o SQLite para forçar repetição.

## Limites e aceite

O coletor lê XMLs individuais presentes no ambiente do cliente e pode consultar **somente a distribuição NF-e por NSU** quando configurado com o certificado do cliente. Não valida a cadeia de assinatura dos documentos nem os assina. Para cada empresa, valide permissão de acesso, teste de XML assinado e não assinado, lote com duplicatas, alteração de arquivo durante coleta, perda/reconexão de rede, reinício do serviço e comparação entre o SHA-256 da origem e do artefato preservado no servidor. No modo SEFAZ, teste também o spool durável, restauração do NSU, intervalos entre consultas e tratamento de resumo/quarentena. Confirme escopo do token e segregação entre empresas antes de habilitar a rotina em produção.
