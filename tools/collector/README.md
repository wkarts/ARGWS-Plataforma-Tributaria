# Coletor local de XML fiscal

Esta distribuição foi projetada para a infraestrutura do cliente. A aplicação
self e o coletor podem operar no mesmo ambiente do cliente: cada empresa
cadastrada possui UUID, token de ingestão, configuração e fila SQLite próprios.
O serviço multiperfil lê XMLs individuais sem alterar os originais e envia os
bytes por HTTPS à API configurada pela empresa. O catálogo da instalação é um
SQLite separado dos estados de coleta de cada empresa.

## Pacotes para instalar

| Sistema | Arquivo da release | Componentes |
| --- | --- | --- |
| Windows x64 | `fiscal-xml-collector-setup-windows-x64.exe` | Instalador gráfico, GUI de administração, worker de serviço e WinSW integrado. |
| Linux x64 | `fiscal-xml-collector-gui-linux-x64.tar.gz` | GUI executável, worker, unit e scripts systemd multiperfil. |

Baixe o pacote e o `SHA256SUMS.txt` da **mesma release** e confira o SHA-256
antes de instalar. Os workers de console são componentes internos dos pacotes,
usados pelo serviço e pela GUI para diagnóstico; não são o download principal.
Leia os guias [Windows](windows/README-installer.md) e
[Linux](systemd/README-self.md) para instalação, atualização e remoção.

Na GUI, informe para cada empresa: nome, CNPJ, UUID da empresa na aplicação
local, URL HTTPS da API, token individual e uma ou mais pastas com XML. A GUI
recebe `--home` para escolher a pasta de dados durante diagnóstico. A
instalação do serviço restringe o estado ao diretório dedicado padrão de cada
sistema para proteger as ACLs. CNPJ e credenciais da SEFAZ
são separados por perfil. Depois de salvar, use **Validar**, faça uma coleta
controlada com **Coletar agora**, confira **Status** e instale o serviço.
O serviço usa a configuração validada no próximo ciclo após mudanças.

O token não é gravado em JSON nem SQLite. A GUI não o exibe ao reabrir o
perfil; deixe o campo vazio para preservá-lo ou informe um novo valor para
rotacionar. O diretório de dados contém segredos e deve ser acessível apenas
à conta de serviço e à administração local. Faça backup consistente do
catálogo, diretórios de cada empresa, fila SQLite e spool da SEFAZ, quando
habilitado. Em Windows o padrão é `%ProgramData%\FiscalXmlCollector`; em Linux,
`/var/lib/fiscal-xml-collector`.

## Integração com a API

Um administrador cadastra cada empresa na aplicação self e cria um token de
agente exclusivo com `POST /api/v1/agents` usando as credenciais autorizadas.
O token é exibido uma só vez. Para cada documento, o coletor envia
`POST /api/v1/documents/ingest?company_id=UUID` com Bearer do agente,
`Content-Type: application/xml`, `X-Content-SHA256` e `X-Idempotency-Key`
baseados no SHA-256 dos bytes originais. Um estado local por empresa evita
misturar filas e cursores. O servidor confirma o documento antes que a fila
local marque a entrega; erros transitórios permanecem para retry.

## Fontes e limites atuais

- Pastas locais: arquivos `.xml` individuais e estáveis, com varredura opcional
  de subpastas; o coletor não expande ZIP automaticamente. A opção padrão exige
  elemento XMLDSig `Signature`, mas a presença dele **não comprova** validade
  criptográfica, autorização ou situação fiscal. Ative a coleta de XML sem
  essa marcação apenas quando fizer sentido para a origem.
- SEFAZ: consulta opcional de **Distribuição DF-e de NF-e por `distNSU`** com
  certificado A1 em PEM/chave separada, retidos localmente. O CNPJ deve estar
  autorizado. Resumos e esquemas desconhecidos ficam em spool/quarentena;
  resumo não se converte em nota completa. O NSU avança após guarda local
  durável, independentemente do envio à API. Intervalos oficiais e espaço
  disponível limitam a consulta; o padrão de spool é 2 GiB.
- NFC-e e outros documentos assinados podem entrar pelas pastas locais se
  aceitos pela API. Este coletor ainda não consulta automaticamente CT-e,
  MDF-e, NFS-e nem realiza manifestação do destinatário.
- O XML individual tem limite de 10 MiB. O serviço não assina XML nem executa
  apuração assistida oficial por conta própria. A análise fiscal na aplicação
  depende dos dados efetivamente disponíveis e da homologação das integrações.

## Diagnóstico para operadores técnicos

Os comandos internos do worker são `--home CAMINHO check|once|status|serve` e
`self-test --offline`; `check` e `status` não acessam a rede. `once` pode
consultar a SEFAZ e enviar documentos. Em Windows use o worker instalado na
pasta do programa com privilégios suficientes para ler os dados protegidos;
em Linux a conta `fiscal-collector` deve ter acesso de leitura às raízes de
XML. O legado `collector.py --config CONFIG` continua no código para quem já
opera uma única empresa, mas não é distribuído como o pacote GUI multiperfil.
