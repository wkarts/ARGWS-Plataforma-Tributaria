import json
from decimal import Decimal

import pytest

from app.normalizer import InvalidReceipt, normalize
from app.schemas import CompanyIn


def test_credit_states_are_distinct_and_exact():
    raw = b'''{"tiqueteSolicitacao":"t-1","ni":"12345678","apuracao":[{"pa":"10/2026","creditos":[{"origem":1,"chave":"CHAVE","cbs":{"apurado":1234567890123456.78,"apropriacao":{"aApropriar":10.02,"apropriado":8.01,"utilizacao":{"utilizado":3.04}}}}]}]}'''
    data = json.loads(raw, parse_float=str)
    rows = normalize("creditos", data, ticket="t-1", cnpj_base="12345678", digest="f" * 64)
    assert len(rows) == 1
    row = rows[0]
    assert row["amount"] == Decimal("1234567890123456.78")
    assert row["credit_to_appropriate"] == Decimal("10.02")
    assert row["credit_appropriated"] == Decimal("8.01")
    assert row["credit_used"] == Decimal("3.04")
    assert row["credit_category"] == "government_reported"
    assert row["raw"]["cbs"]["apurado"] == "1234567890123456.78"
    assert row["provenance"]["receipt_sha256"] == "f" * 64


@pytest.mark.parametrize("resource", ["pagamentos", "recolhimentos"])
def test_payment_composition_periods_are_not_commercial_installments(resource):
    data = {"tiqueteSolicitacao": "t", "ni": "12345678", "apuracao": [{
        "dataArrecadacao": "2026-11-03T10:00:00Z", "pagamentos": [{
            "numeroDARF": "", "tipo": 2, "composicao": [
                {"sequencial": 1, "pa": "09/2026", "chaveDFE": "NF1", "principal": "1.01", "multa": "0.02", "juros": "0.03", "total": "1.06"},
                {"sequencial": 2, "pa": "10/2026", "chaveDFE": "NF2", "principal": "2.01", "multa": "0", "juros": "0", "total": "2.01"},
            ]}] }]}
    rows = normalize(resource, data, ticket="t", cnpj_base="12345678", digest="a" * 64)
    assert [r["period"] for r in rows] == ["09/2026", "10/2026"]
    assert [r["amount"] for r in rows] == [Decimal("1.06"), Decimal("2.01")]
    assert rows[0]["principal"] == Decimal("1.01")
    assert rows[0]["penalty"] == Decimal("0.02")
    assert rows[0]["interest"] == Decimal("0.03")
    assert rows[0]["provenance"]["document_key"] == "NF1"
    assert rows[0]["provenance"]["collection_at"] == "2026-11-03T10:00:00Z"
    assert rows[0]["source_key"] != rows[1]["source_key"]
    assert all(r["credit_category"] is None for r in rows)
    assert all(r["credit_used"] is None for r in rows)


def test_absent_array_is_not_silent_empty_result():
    data = {"tiqueteSolicitacao": "t", "ni": "12345678", "apuracao": [{"pa": "10/2026"}]}
    with pytest.raises(InvalidReceipt, match="ausente"):
        normalize("creditos", data, ticket="t", cnpj_base="12345678", digest="x")


def test_receipt_from_wrong_contributor_or_ticket_is_rejected():
    data = {"tiqueteSolicitacao": "another", "ni": "87654321", "apuracao": []}
    with pytest.raises(InvalidReceipt):
        normalize("debitos", data, ticket="t", cnpj_base="12345678", digest="x")


def test_numeric_and_alphanumeric_cnpj_check_digits():
    assert CompanyIn(name="A empresa", cnpj="11.222.333/0001-81").cnpj == "11222333000181"
    assert CompanyIn(name="A empresa", cnpj="12ABC34501DE35").cnpj == "12ABC34501DE35"
    with pytest.raises(ValueError):
        CompanyIn(name="A empresa", cnpj="12ABC34501DE34")
