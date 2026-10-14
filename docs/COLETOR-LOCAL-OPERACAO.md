# Coletor de XML na instalação self — operação

O cliente instala a aplicação self em sua infraestrutura e cadastra suas
empresas nela. O coletor local é outro componente instalado no ambiente do
cliente: a GUI configura URL HTTPS da API local, UUID, CNPJ, token de agente,
pastas de XML e a consulta opcional à Distribuição NF-e por empresa. Um
worker multiperfil executa sob supervisão do Windows Service Control Manager
ou do systemd. Não existe dependência de infraestrutura hospedada pelo
fornecedor para guardar os XMLs desta instalação.

## Instalação e validação de artefatos

| Ambiente | Pacote GUI para o operador | Serviço e dados |
| --- | --- | --- |
| Windows x64 | `fiscal-xml-collector-setup-windows-x64.exe` | Instala worker e WinSW juntos; serviço `FiscalXmlCollector`; dados em `%ProgramData%\FiscalXmlCollector`. |
| Linux x64 | `fiscal-xml-collector-gui-linux-x64.tar.gz` | Inclui worker e `self-install.sh`; unidade `fiscal-xml-self.service`; dados em `/var/lib/fiscal-xml-collector`. |

Compare o SHA-256 do pacote com `SHA256SUMS.txt` da mesma release. O CI verifica
os binários frozen e a GUI sem rede, instala/desinstala o setup Windows em um
runner e prova preservação do diretório de dados. Isso não substitui um teste
de serviço com fontes reais, token emitido pela instância do cliente e
certificado autorizado.

**Windows:** execute o instalador com permissão administrativa. Ele guarda
executáveis em `%ProgramFiles%\FiscalXmlCollector` e protege o catálogo,
configurações, SQLite, tokens, certificados e spool em
`%ProgramData%\FiscalXmlCollector` com ACL de SYSTEM/Administradores. A GUI
exige elevação para administrar esse diretório e o serviço. Primeiro cadastre
uma empresa ativa e valide; depois clique em **Instalar serviço**. O wrapper
WinSW v2.12.0 vem no instalador, com hash conferido e licença MIT incluída.
Uma atualização preserva os dados e só reinicia um serviço que já estava em
execução. Desinstalar preserva os dados para decisão de backup ou descarte.

**Linux:** extraia o tar.gz, abra a GUI em sessão gráfica com permissões
suficientes para `--home /var/lib/fiscal-xml-collector`, cadastre empresa e execute
`self-install.sh WORKER GUI HOME` como root. A instalação cria a conta
`fiscal-collector`, protege o estado, instala o worker em `/opt` e habilita
`fiscal-xml-self.service`. O script `self-uninstall.sh` remove apenas a unit;
preserva dados, segredos e binários. Revise permissões dos diretórios XML e
certificados para a conta de serviço. Consulte
`journalctl -u fiscal-xml-self.service` para erros de acesso e de coleta.
Os scripts de serviço recusam outro diretório de estado: no Windows apenas
`%ProgramData%\FiscalXmlCollector`; no Linux apenas
`/var/lib/fiscal-xml-collector`. Pastas de origem dos XMLs continuam
parametrizáveis por empresa.

## Cadastro de empresas e credenciais

Para cada empresa, obtenha na aplicação self o UUID, a URL HTTPS de sua API e
um token de agente com acesso apenas a ela. O token é exibido uma vez e fica
em arquivo próprio; ao editar o perfil, o campo vazio o preserva. Não grave
token na URL, em JSON, na linha de comando nem em registros de serviço.
Use CNPJ distinto por perfil para não duplicar consulta NSU. O catálogo
`catalog.sqlite3` mantém metadados; cada empresa fica sob
`companies/UUID/config.json`, `companies/UUID/token` e
`companies/UUID/state/state.sqlite3`. Uma instalação pode cadastrar várias
empresas, com filas e retries independentes.

Na GUI, preencha pastas absolutas de XML (uma por linha), marque varredura de
subpastas e ajuste intervalo. É possível operar apenas com SEFAZ, apenas com
pastas ou combinar ambas. A opção **Somente com elemento Signature** filtra
arquivos locais sem a marca XMLDSig; essa filtragem não valida assinatura
criptográfica, cadeia de certificado nem autorização fiscal. Os XMLs
originais permanecem onde o cliente os gravou. Pastas com ZIPs precisam de
extração autorizada anterior à coleta.

Se habilitar a Distribuição NF-e, selecione o CNPJ do perfil, ambiente e o
par certificado A1 PEM/chave PEM. A GUI valida o par e copia arquivos privados
para o perfil do cliente, sem enviá-los para a API. Guarde a senha/chave e
backup com controles de acesso adequados. O conector consulta apenas NF-e
por `distNSU`, pode receber resumos e eventos e respeita janelas de consulta;
resumos permanecem pendentes sem serem presumidos como nota completa.
O spool guarda SOAP integral, `docZip` e XML extraído antes de avançar NSU.
Limite padrão do spool: 2 GiB, com reserva de espaço por lote. NSU significa
guarda local, **não confirmação de entrega** à API. Coordene a consulta se
outro software do cliente também consome o NSU da mesma empresa.

## Aceite e manutenção

1. Cadastre uma empresa ativa, salve e execute **Validar** sem conexão de
   rede. Corrija diretórios, token e certificado até obter sucesso.
2. Use **Coletar agora** com XML real autorizado e confronte SHA-256 dos
   bytes preservados no servidor. Essa ação pode enviar dados e consultar
   a SEFAZ quando habilitada.
3. Instale o serviço, reinicie o host de teste e confira **Status**,
   duplicidades, tentativas pendentes e recuperação após falha de rede.
4. Faça backup consistente de catálogo, perfis e spool antes de atualizar.
   Pare o serviço ou use mecanismo de backup SQLite que inclua WAL. Preserve
   os tokens e PEM fora de backups públicos.
5. Monitore pendências, rejeições, espaço em disco, último NSU e o horário da
   próxima consulta. Nunca apague SQLite ou spool para forçar repetição.

O coletor limita cada XML a 10 MiB e não assina documentos. A consulta atual
à SEFAZ não cobre automaticamente NFC-e, CT-e, MDF-e nem NFS-e; esses XMLs
podem ser captados em diretórios locais conforme suporte da API. A apuração
assistida usa as fontes disponíveis na aplicação e demanda integração oficial
e homologação específicas antes de ser tratada como apuração definitiva.
