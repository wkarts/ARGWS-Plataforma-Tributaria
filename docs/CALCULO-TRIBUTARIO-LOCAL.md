# Cálculo tributário por operação — motor normativo local

## Finalidade

A plataforma calcula os tributos por operação mediante o componente de cálculo tributário **instalado na própria infraestrutura do cliente**. Nenhum percentual, classificação fiscal, redução, benefício ou valor de tributo é estimado pelo backend. Falha de conectividade, versão não identificada ou ausência do serviço **impedem o cálculo**. Isso não impede a operação dos módulos de XML, conciliação e consultas CBS.

O cálculo e a memória retornados pelo componente local **não constituem a apuração assistida da competência**. A apuração de débitos/créditos e a comprovação de extinção devem utilizar seus próprios registros e retornos governamentais. Em particular, CBS/IBS destacados no documento de aquisição não são automaticamente créditos apropriados nem utilizados.

## Obter e executar a Calculadora

Consulte a documentação mantida pela administração tributária:

- https://piloto-cbs.tributos.gov.br/servico/calculadora-consumo/calculadora/calculadora-offline/calculadora-offline-instrucoes
- https://piloto-cbs.tributos.gov.br/servico/calculadora-consumo/calculadora/documentacao

O componente oficial offline pode ser disponibilizado via WSL, contêiner ou Java (JAR), conforme o pacote e a versão publicada. Documentação local de suas APIs costuma ser exposta no Swagger. Em versões publicadas em 2026, as portas padrão dos serviços são **8080 (regime regular)** e **8082 (Simples Nacional)** para a imagem offline; a versão JAR do Simples Nacional pode usar **3000**. Não se deve supor que um novo pacote manterá os contratos sem verificação.

Em uma instalação com a API ARGWS e o motor na **mesma rede confiável**, configure no arquivo de ambiente da plataforma:

```dotenv
TAX_CALCULATOR_REGULAR_URL=http://calculator-regular:8080/api/calculadora/regime-geral
TAX_CALCULATOR_REGULAR_VERSION_URL=http://calculator-regular:8080/api/calculadora/dados-abertos/versao
TAX_CALCULATOR_SIMPLES_URL=
TAX_CALCULATOR_SIMPLES_VERSION_URL=
```

O hostname `calculator-regular` é ilustrativo do **nome do serviço local de sua própria instalação**; somente funciona se estiver configurado no DNS/Compose interno. Se o motor estiver na máquina host, verifique a acessibilidade do endereço a partir do contêiner da API. O endereço `localhost` dentro de um contêiner não representa o host Windows/Linux. A aplicação aceita exclusivamente endpoints HTTP locais/nomes de serviço controlados, definidos no **servidor**, e não URLs fornecidas pelo navegador.

**Simples Nacional:** preencha as duas URLs apenas após conferir a rota de cálculo e a rota de versão na documentação Swagger da distribuição instalada. Nenhuma rota não verificada é presumida. Os dados JSON exigidos pelo motor para o regime vigente são validados pelo próprio componente; a interface aceita o contrato completo sem alterar suas regras.

## API ARGWS

- `GET /api/v1/tax-calculator/status` — parâmetros configurados, sem alegar conectividade.
- `POST /api/v1/tax-calculations` — JSON `{"company_id":"UUID","regime":"regular|simples","payload":{...}}`. O `payload` contém a operação para o motor local. Cabeçalho opcional `Idempotency-Key` (8 a 128 caracteres).
- `GET /api/v1/tax-calculations?company_id=UUID` — histórico paginado da empresa.
- `GET /api/v1/tax-calculations/{id}` — requisição original, versão do motor, retorno integral, memória de cálculo, hashes e situação técnica.

Toda rota requer autenticação. A empresa deve pertencer à organização autenticada. Respostas possuem `Cache-Control: no-store`. Requisições e retornos são preservados em `tax_calculations` junto com SHA-256 e versões declaradas pelo motor. A ação de criação também gera trilha em `audit_events`. Não há escrita em tabelas de créditos apropriados, débitos extintos, guias ou declarações.

### Regime regular

O formulário permite informar data/hora, UF, município IBGE, NCM/NBS, CST, cClassTrib, base de cálculo, quantidade e unidade, inclusive diversos itens. Para campos específicos da operação existe modo JSON completo, conforme o Swagger do motor local. A validação definitiva de enquadramento e fundamentos legais cabe ao serviço normativo instalado. A ARGWS exibe exclusivamente os valores retornados por ele e guarda o JSON integral.

### Simples Nacional

A interface usa o contrato JSON completo da versão instalada do motor específico. Não utiliza percentuais presumidos de DAS, anexo ou redução; não calcula uma opção tributária usando alíquotas arbitrárias. A seleção entre Simples e regime regular e os critérios de elegibilidade permanecem sujeitos às normas e à parametrização documentada do contribuinte.

## Pré-requisitos para uso real

1. Baixar um pacote oficial de versão conhecida, verificar sua procedência e instalar o(s) serviço(s) na rede da plataforma.
2. Conferir os endpoints e payloads da versão na documentação local.
3. Habilitar as URLs via configuração privada da API e confirmar que o endpoint de versão retorna `versaoApp` e `versaoDb` (ou adaptar e testar formalmente um novo contrato publicado).
4. Testar uma operação real com documentos, parametrização de CST/cClassTrib/NCM/NBS, município e data de fato gerador e confrontar com a memória emitida pelo motor.
5. Validar casos tributários especiais, devoluções, regras de crédito e opção do Simples Nacional com homologação fiscal independente.
6. Fazer backup e testar restauração do banco, XMLs e resultados dos cálculos antes de ativar em produção.

Não é feito download automático do componente, não se monta um motor oficial a partir de tabelas manuais e não se cria resultado fictício de contingência. Atualizações futuras devem registrar versão de código, base normativa e diferenças de contrato antes de habilitar cada pacote.
