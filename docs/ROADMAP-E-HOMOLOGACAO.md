# Plano de entrega e homologação

**Marco de referência:** 14/10/2026. A plataforma reúne módulos locais de consulta CBS, ingresso de XML, coletor de pastas, conferência e controles manuais. Os critérios abaixo descrevem o que falta demonstrar antes de anunciar uma capacidade como pronta para operação real. A existência de rota, tela, workflow, WSDL ou publicação governamental não comprova acesso autenticado, disponibilidade para aquele contribuinte ou homologação da instalação.

## Critério de status único por capacidade

| Estado | Evidência necessária |
| --- | --- |
| `planejada` | Decisão de produto, fonte oficial identificada e contrato de aceitação escrito. |
| `implementada_localmente` | Código, migrations, testes automatizados e mocks/fixtures versionados em CI. |
| `publicada_para_homologacao` | Imagens GHCR e GUI/CLI testados, deploy reproduzível, observabilidade e documentação. |
| `homologada_em_ambiente_oficial` | Para serviço governamental: identidade/credencial autorizada, solicitação real, resposta/arquivo, checksum, erro/limite e revisão documentados; ticket e callback quando o contrato os prevê. Para coleta local: instalação e envio HTTPS reais em cliente autorizado, sem presumir homologação governamental. |
| `habilitada_em_producao` | Operação disponível, controles e limites observados, aprovação operacional, rollback e monitoramento. |

Manter status por **fonte × recurso × ambiente × versão do contrato × identidade autorizada**. O ingresso de XML fornecido pelo cliente e a distribuição NF-e por certificado não herdam o estado um do outro; CBS e IBS também não. Marcar pagamentos, recolhimentos e DARF como indisponíveis até confirmação real no ambiente correspondente.

## Estado operacional das frentes nesta entrega

| Frente | Resultado local verificável | Gate pendente para anúncio de produção |
| --- | --- | --- |
| Consulta CBS | Fixture, cliente opt-in Receita, ticket, callback/status, download protegido, JSON bruto e observações; agenda opcional desabilitada por padrão. | Autorização do contribuinte, ambiente/recurso disponível, execução autenticada, retorno real conferido e operação de janela incremental. |
| Ingresso de XML | Token em hash, escopo organização/empresa, arquivo original até 10 MiB em S3, SHA-256, deduplicação, extração limitada e download. | XMLDSig/cadeia e schema verificados, autorização confirmada na fonte, backup/restore, volume e retenção aprovados. Status de protocolo no XML é apenas declaração. |
| Coletor no cliente | Monitor de diretórios configurados, estado SQLite, retry, sem alteração dos originais; instalação systemd/WinSW descrita e CI para binários. | Build e smoke real em Linux/Windows de destino, distribuição com checksums, teste HTTPS autenticado e operação sob conta de serviço com acesso mínimo. |
| Comparação CBS/XML | Rota somente leitura por empresa/competência, valores `Decimal` e estados explícitos `match`, `mismatch`, `missing`, `ambiguous`, `unknown`. | Evidência de cobertura oficial e casos revisados; o endpoint atual informa `official_coverage_proven=false`, sem declaração automática. |
| Crédito de estoque/parcela comercial | Cadastros manuais separados, valor string decimal, trilha e rascunho; leitura por usuário da organização. | Regra legal e documentação do inventário, revisão fiscal e processo de apropriação específico; parcela paga não atesta extinção tributária. |
| Distribuição NF-e/SEFAZ | Adaptador local **opt-in** `distNSU` em SOAP 1.2/mTLS, spool de SOAP/docZip/XML e checkpoint NSU após guarda; classificação separada de resumo, evento, XML elegível e quarentena. | Certificado do ator autorizado, endpoint/WSDL e esquema correntes, política de manifestação, série NSU coordenada com outros softwares, teste real e limites de uso. Presença de XMLDSig não comprova assinatura válida. |
| IBS | MOC CGIBS v1.10 inventariado, com Etapa 1 em homologação indicada para 28/09/2026. | Rotas definitivas (o documento ainda usa “proposta”), habilitação, credencial, arquivos reais/partes, hash oficial especificado, revisão fiscal e produção oficialmente disponível. |

## Fases e entregáveis verificáveis

| Fase | Entrega | Gate de aceitação |
| --- | --- | --- |
| 0. Contratos e baseline | Fontes oficiais, inventário das três referências, ADRs, limites por operação/ambiente e fixtures sintéticos sem dados reais | Revisão fiscal/técnica; nenhuma afirmação de endpoint disponível sem evidência. |
| 1. Plataforma independente | FastAPI, Alembic/PostgreSQL, Vue, organização/CNPJ, trilha, Celery/RabbitMQ, Redis, contrato S3 genérico com RustFS local no Compose ou S3 externo, health/readiness e OpenAPI | CI local e Compose sobem em ambiente limpo; `put/get/head`, checksum, migração e restore testados em cada opção aceita; tenant A não acessa tenant B; segredos não aparecem em bundle/log. MFA/RLS/cofre são gates de evolução. |
| 2. Entrega e deployer | Compose para Docker/Dockge/CloudPanel/Portainer, develop/production isolados; Tkinter GUI/CLI Python; workflows SemVer, GHCR, cache, coletor Linux/Windows e checksums | Smoke real dos executáveis em seus sistemas gera/valida stack e testa coletor offline; CI promove somente com imagens/digests/assets confirmados; update preserva `.env` e volumes; retenção em dry-run. |
| 3. Integração CBS: débito e crédito | Adaptador oficial, OAuth2, solicitação, ticket, webhook HEAD/POST, status, download seguro, arquivo bruto, normalização incremental | Contratos simulados e casos de falha aprovados; após autorização, execução real documentada separadamente em produção restrita/beta. |
| 4. XML, conferência e controles | Ingestão/autenticação por empresa, coletor de pastas, consulta/download de objetos brutos, correspondência CBS × XML por competência, estados de crédito observados e fatos financeiros manuais separados | Casos revisados com `Decimal`, reprocessamento sem duplicidade, assinatura/autorização e cobertura verificadas em etapas próprias; carga, exportação de evidência e regras fiscais aceitas antes de uso decisório. |
| 4b. Distribuição DF-e | Adaptador local NF-e `distNSU` com certificado/ator autorizado; CT-e/MDF-e/NFS-e apenas após contratos e credenciais próprios | Teste autenticado na fonte, `cStat` e tempos de espera, docZip e resumo/eventos, controle NSU com softwares coexistentes, autorização de acesso e origem/sha no arquivo recebido. |
| 5. Outros recursos CBS | Pagamentos, recolhimentos pelo adquirente, RAD/PCONT e recursos adicionais quando o ambiente e o contrato estiverem disponíveis | Gate individual por operação e ambiente, inclusive permissão e resposta real; sem reutilizar mocks como comprovante. |
| 6. IBS e evolução | Modelos e fixtures a partir do MOC v1.10, parser de extrato e partes; adaptador próprio após confirmação de rota final, credenciais e ambiente | Homologação CGIBS independente; `extrato_cc.hash` do MOC ainda carece de algoritmo definido, não tratá-lo como SHA-256 da nossa cópia. Conciliação CBS/IBS exibe origens separadas. |

Fases podem avançar em paralelo quando seus gates independentes estiverem satisfeitos. A fase de integração real depende de autorização e disponibilidade do ambiente governamental; o restante da plataforma não precisa ficar parado.

**Gate específico de armazenamento:** usar configuração `S3_*` e testar a mesma API de artefatos com RustFS local e o S3 externo escolhido: criação/acesso privado ao bucket, upload/download/head, SHA-256, erro e retry, isolamento por tenant, backup e restauração. Versionamento, retenção imutável e multipart entram somente se exigidos pela política e aprovados em testes próprios. A [matriz de compatibilidade RustFS](https://docs.rustfs.com/en/reference/s3-compatibility) cobre um subconjunto de comportamentos S3; a [documentação Docker/Compose](https://docs.rustfs.com/en/installation/container/docker) recomenda TLS, backup e arquitetura adequada antes de produção. **RustFS em produção não está homologado neste trabalho.** O [advisory MinIO GHSA-xh8f-g2qw-gcm7](https://github.com/minio/minio/security/advisories/GHSA-xh8f-g2qw-gcm7) inclui a última release OSS na faixa afetada no modo distribuído; não usar MinIO OSS como serviço ativo do Compose da nova plataforma.

## Plano de testes do protocolo CBS

A [documentação oficial de APIs assíncronas](https://docs.receitafederal.gov.br/apuracao-cbs/guiaapiassincrona.html) informa `HEAD` no webhook, `POST` com `urlRetorno`, ticket HTTP 201, notificação ou consulta de situação, processamento de até 240 minutos, URL assinada válida por 48 horas e até quatro aberturas por dia. A [API de créditos](https://docs.receitafederal.gov.br/apuracao-cbs/credito.html) informa consulta incremental com janela máxima de oito dias, ressalvada a primeira consulta. Casos mínimos:

1. `HEAD` público retorna rapidamente status permitido sem autenticar usuário do painel; `POST` requer ticket conhecido, body válido e escopo resolvido. Repetir o mesmo webhook dez vezes cria uma observação e nenhum job duplicado.
2. Simular webhook chegando antes da transação de persistência do ticket, depois de retry do broker, fora de ordem ou não chegando. O reconciliador por status recupera o ticket sem abrir consulta adicional indevida.
3. Simular OAuth expirado/401, indisponibilidade/429/5xx, timeout, erro oficial, processamento além do limite e concorrência de dois workers. Validar retry delimitado, quota persistida e estado final de revisão quando necessário.
4. Simular URL assinada prestes a expirar, expirada, redirecionamento inesperado, resolução para rede privada, arquivo truncado, JSON inválido, tamanho excessivo e armazenamento indisponível. Nunca logar URL/token; só concluir depois de preservar o arquivo e SHA-256.
5. Duas consultas incrementais contendo o mesmo crédito atualizado devem manter duas observações auditáveis e uma projeção corrente correta. Oito dias sem coleta devem gerar alerta de cobertura; não prometer backfill automático fora do contrato.
6. Valor do crédito esperado pelo ERP, a apropriar, apropriado e utilizado devem poder divergir legitimamente. Baixar parcela comercial não cria apropriação; crédito presumido de estoque não entra no ledger normal.
7. Importar documentos de dois tenants com mesma chave artificial de fixture e provar isolamento em SQL, API, fila, bucket, logs, auditoria e exportações.

Esses testes usam fixtures sintéticas e não gastam chamadas oficiais. O teste real terá registro explícito de ambiente, versão do contrato, CNPJ autorizado mascarado, data, recurso, ticket mascarado, HTTP/códigos de erro, checksum do arquivo, versão da imagem e resultado da conciliação; jamais anexar credenciais ou URL assinada à evidência.

## Plano de testes do XML e da distribuição NF-e

1. Enviar o mesmo XML duas vezes pela API e por reenvio do coletor; manter um objeto lógico por organização, empresa e SHA-256 e aceitar idempotência. Negar token revogado, de outra empresa e de outra organização; não exibir token após sua criação.
2. Rejeitar hash divergente, corpo vazio, tamanho acima de 10 MiB, DTD/entidades/XInclude, profundidade excessiva e XML de CNPJ não vinculado à empresa. Preservar exatamente os bytes válidos no objeto privado; comparar hash após download.
3. Comprovante de `ds:Signature` presente, protocolo `cStat=100/150` e chave coincidente são indicadores **relatados pelo documento**, não validação criptográfica nem autorização on-line. Ensaiar adulteração de assinatura, cadeia vencida, protocolo falso, schema incompatível e eventos cancelamento/inutilização antes de passar a status verificado.
4. Com o serviço instalado, escrever XML parcialmente, mudar arquivo durante leitura, manter symlink e incluir arquivo grande; provar que só o XML estável é enviado e que o original permanece inalterado. Reiniciar serviço durante queda HTTPS, confirmar checkpoint/retry e testar revogação/rotação do token. Testar `413` e falhas permanentes sem loop infinito.
5. Na distribuição NF-e, testar certificado e CNPJ legitimado, endpoint/WSDL/schema de homologação, múltiplos consumidores que compartilham `ultNSU`, respostas `137`, `138` e `656`, espera obrigatória, `docZip` compactado e somente resumo/evento. Preservar arquivo retornado e hash sem presumir autorização pelo mero sucesso da consulta. Avançar a produção somente após resposta real e aceite do ator.
6. Conferência: XML e observação CBS da mesma empresa/competência/chave com valor idêntico ou distinto; mesma chave com múltiplos XMLs; observação incremental repetida; filial diferente; registro sem documento local; XML sem item oficial e **sem prova de cobertura completa**. Neste último caso retornar `unknown`, jamais baixa fiscal ou crédito automático.

O coletor de pastas pode operar sem certificado fiscal porque lê somente arquivos já presentes no cliente. Para buscar DF-e na SEFAZ, a autorização do ator, o certificado, os cursores NSU e a frequência de consulta são requisitos adicionais. [Matriz e fontes primárias](CONTRATOS-OFICIAIS-2026.md).

## Matriz de disponibilidade a verificar

O [comunicado da Receita Federal de 14/09/2026](https://www.gov.br/receitafederal/pt-br/assuntos/noticias/2026/setembro/receita-federal-publica-nova-documentacao-tecnica-das-apis-de-apuracao-de-cbs) anunciou disponibilidade gradual para produção restrita e produção beta: débitos/créditos no início de outubro de 2026, pagamentos/recolhimentos no início de novembro, DARF RAD/PCONT ao final de novembro. O anúncio não confirma habilitação para nosso contribuinte nem operação no ambiente exato.

| Recurso | Cronograma anunciado pela RFB | Validação exigida antes da habilitação |
| --- | --- | --- |
| CBS débitos | Início de outubro/2026 | Contrato e endpoint da versão corrente, credencial, ambiente, ticket e arquivo real. |
| CBS créditos | Início de outubro/2026 | Itens acima, mais comparação de `aApropriar`, `apropriado` e `utilizado` com o JSON bruto. |
| CBS pagamentos | Início de novembro/2026 | Confirmar ambiente, payload e origem oficial sem inferir de Contas a Pagar. |
| CBS recolhimentos do adquirente | Início de novembro/2026 | Confirmar ambiente e vínculo com operação/valor sem assumir split universal. |
| CBS DARF RAD/PCONT | Final de novembro/2026 | Contrato e autorização própria; nenhuma emissão automática antes do aceite funcional. |
| IBS Etapa 1 | MOC CGIBS v1.10 publicado; Etapa 1 em homologação desde 28/09/2026 e produção futura indicada | Confirmar rota final, habilitação do contribuinte, credencial, arquivo/partes reais, esquema e aceite fiscal; nenhuma chamada IBS em produção nesta entrega. |

Para NF-e, a NT 2014.002 v1.40 mapeia o serviço `NFeDistribuicaoDFe`. Uma resposta de distribuição pode conter resumo, evento ou XML segundo o ator e suas manifestações. Confirmar no WSDL/XSD corrente e nas regras de consumo antes de ativar. CT-e, MDF-e e NFS-e nacional têm contratos e habilitações próprios; veja [inventário oficial](CONTRATOS-OFICIAIS-2026.md).

## Gates da esteira

**PR:** lint/type check Python e TypeScript, pytest de domínio/tenancy/protocolo/ingestão/conferência, `alembic upgrade head` em banco limpo e upgrade de snapshot, testes da GUI/CLI e coletor, `docker compose config --quiet` por alvo, scan de segredos, contratos OpenAPI e documentação de impacto. A falha de um gate impede publicar `:develop`.

**Develop:** construir imagens pelo mesmo commit, publicar `:develop` com digest e metadados SHA, executar smoke em ambiente efêmero, compilar GUI/CLI Windows e coletor Windows/Linux nos runners correspondentes, verificar cabeçalho da arquitetura, `--version`, `self-test --offline`, deployer generate/validate e `SHA256SUMS`, e publicar uma pré-release imutável por SHA após validar também o pacote de todos os destinos de implantação. A imagem móvel `:develop` e a release de compatibilidade `pre-release` só devem avançar no HEAD. Essa pré-release não cria SemVer estável nem prova instalação de serviço real.

**Main:** PR de promoção aprovada; versão `vX.Y.Z` calculada de forma inequívoca (major/minor/patch), imagens amd64/arm64 onde houver suporte validado, tag estável imutável, GHCR verificado pelo digest, GUI/CLI, coletor nas arquiteturas previstas, arquivo de deploy, checksums, manifesto e GitHub Release. A versão exibida no runtime, painel, manifesto e tag deve coincidir. Se algum asset falhar, não anunciar release completa.

**Retenção:** inventário completo e relatório dry-run antes de qualquer deleção; permitir apenas caches e imagens/digests temporários com prazo e orçamento de lote; nunca apagar Git tag, release, asset estável, imagem de SemVer estável/canônica, bases compartilhadas, dados Docker ou volumes. Se faltarem permissões, inventário, relação de manifest ou prova de publicação, preservar e registrar pendência. Remoção de prefixos legados deve ter plano de migração separado e prova de substituto, sem prune global.

## Homologação operacional

1. Obter do titular autorização, credenciais e vínculo matriz/CNPJ base para cada ambiente e escopo. Verificar a documentação oficial no dia da execução e registrar revisão/data; credenciais de produção restrita não são intercambiáveis com produção.
2. Preparar webhook HTTPS público com certificado válido, resposta `HEAD`, observabilidade e URL dedicada. Confirmar conectividade externa e política de egress do worker; manter o endpoint acessível durante o processamento.
3. Executar um recurso por vez dentro do limite oficial, armazenar ticket e acompanhar webhook/status. Baixar arquivo dentro da validade de 48 horas, validar SHA-256 e comparar campos com o schema oficial e a projeção local.
4. Exercitar cenários de recurso vazio, alteração incremental, erro de credencial, expiração/recuperação, atualização de um mesmo registro e baixa do worker durante o retorno. Fazer rollback da aplicação sem perder ticket ou artefato.
5. Assinar relatório de evidências técnico e fiscal com diferenças abertas. Só então habilitar recurso em produção para organizações autorizadas e acompanhar quota, cobertura, backlog, divergências e falhas.

## Pendências que impedem declaração de conclusão

- Disponibilidade efetiva, contratos atuais e credenciais para pagamentos, recolhimentos e DARF da CBS no ambiente escolhido.
- Contrato **final** das rotas e do hash do extrato IBS, disponibilidade de produção, habilitação e acesso; o MOC v1.10 publicado é uma base de modelagem, não conclusão operacional.
- Homologação autenticada com contribuinte autorizado; os pacotes anexados e o protótipo Laravel anterior não a fornecem.
- Build real e validação dos executáveis Windows/Linux, publicação GHCR/release e deploy remoto da **nova** plataforma; arquivos de workflow no repositório não substituem a evidência de execução.
- Distribuição NF-e com certificado do contribuinte em homologação, acesso efetivo e controle de NSU compartilhado; conectores CT-e/MDF-e/NFS-e próprios e validação criptográfica/schema dos XMLs antes de afirmar autenticidade/autorização.
- Medição de carga, backups/restauração e revisão fiscal de regras antes de uso decisório em produção.
- Validação do contrato S3, backup/restore e requisitos de segurança do backend escolhido; RustFS local no Compose não equivale por si só a compatibilidade ou prontidão de produção homologada.
