# Prévia de apuração assistida local

`GET /api/v1/assisted-preview?company_id=<UUID>&period=AAAA-MM` exige usuário
autenticado da mesma organização. O resultado `preliminary_unvalidated` agrupa
por competência, modelo (NF-e 55, NFC-e 65, CT-e 57), entrada/saída e tributo
(CBS/IBS) os valores **declarados nos XMLs** recebidos. Todas as somas usam
`Decimal`; cada linha da conciliação oferece chave, hash SHA-256 e IDs de XML
e registro CBS para revisão.

Entram nas somas somente documentos com chave identificável, um único XML por
chave, direção conhecida, elemento `Signature` presente e protocolo que **declara**
autorização. A presença do elemento e do protocolo **não** valida a assinatura,
a cadeia de certificados nem confirma autorização online. Eventos, chaves com
XMLs conflitantes, modelos não mapeados, valores ausentes e provas insuficientes
geram críticas e não são estimados. CT-e em que a empresa aparece em outro papel
que não emitente/destinatário pode permanecer com direção desconhecida.

Registros da CBS só são comparados quando persistidos a partir de consulta
concluída, com hash do recibo e ambiente `restricted` ou `production`. Dados
`fixture`, pagamentos comerciais e crédito presumido de estoque não entram na
prévia. Um item ausente na resposta incremental da Receita não prova que inexiste;
`coverage.official_cbs_complete` e `coverage.local_xml_complete` ficam `false`.
Crédito **a apropriar**, **apropriado** e **utilizado** é mostrado em colunas
independentes, sem lançar crédito ou calcular saldo a pagar. O IBS é apenas
valor declarado em XML local: nenhuma API oficial do IBS é consultada.

As APIs assíncronas da CBS exigem credenciais válidas da matriz e webhook HTTPS
publicamente acessível para receber tíquete/arquivo; consultam débitos e créditos
em janela incremental limitada. Numa instalação self, o endereço e a entrada
HTTPS devem ser fornecidos pela infraestrutura do próprio cliente. Este endpoint
de prévia não aciona Receita nem SEFAZ e funciona apenas com material já
importado. Em 2026, o ambiente CBS é de simulação, sem pagamento efetivo.

Fontes oficiais: [regras de API CBS](https://docs.receitafederal.gov.br/apuracao-cbs/guiaapiassincrona.html),
[créditos CBS](https://docs.receitafederal.gov.br/apuracao-cbs/credito.html),
[manual da plataforma CBS](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/manuais/reforma-tributaria-do-consumo/manual-plataforma-cbs-21-maio-2026-07h40.pdf)
e [manual do IBS v1.10](https://cgibs.gov.br/upload/arquivos/202609/23171119-moc-api-apuracao-assistida-ibs-v1-10-23092026.pdf).
