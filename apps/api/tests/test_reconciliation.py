from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.reconciliation import assisted_preview, reconcile
from app.models import FiscalDocument, Record


KEY = "35261012345678000199550010000000011000000017"
OTHER = "35261012345678000199550010000000021000000018"
SCOPE = {"organization_id": "org-a", "company_id": "company-a"}


def document(*, key=KEY, amount="12.34", direction="outgoing", period="2026-10",
             id="xml-1", digest="hash-1", **changes):
    return {**SCOPE, "id": id, "sha256": digest, "document_key": key, "period": period,
            "cbs_amount": Decimal(amount) if amount is not None else None,
            "direction": direction, **changes}


def record(*, key=KEY, amount="12.34", resource="debitos", period="202610",
           id="record-1", query="query-1", **changes):
    return {**SCOPE, "id": id, "query_id": query, "resource": resource,
            "source_key": f"{key}:composite:{id}", "period": period,
            "amount": Decimal(amount) if amount is not None else None,
            "raw": {"chave": key, "cbs": {"apurado": amount}},
            "provenance": {"receipt_sha256": query}, **changes}


def run(records, documents, **kwargs):
    return reconcile(records, documents, organization_id="org-a", company_id="company-a", **kwargs)


def test_exact_match_uses_document_key_period_and_decimal():
    result = run([record(amount="12.34")], [document(amount="12.34")])
    assert result["summary"] == {"total": 1, "match": 1, "mismatch": 0,
                                  "missing": 0, "ambiguous": 0, "unknown": 0}
    assert result["items"][0]["reason"] == "amounts_equal"
    assert result["items"][0]["source_amount"] == "12.34"


def test_receita_period_mm_yyyy_matches_xml_and_ui_yyyy_mm():
    result = run([record(period="10/2026")], [document(period="2026-10")], period="2026-10")
    assert result["summary"]["match"] == 1
    assert result["items"][0]["period"] == "2026-10"


def test_real_sqlalchemy_entities_are_accepted_without_a_database_session():
    official = Record(**record(period="10/2026"))
    local = FiscalDocument(**document(period="2026-10"))
    result = run([official], [local])
    assert result["summary"]["match"] == 1


def test_mismatch_is_reported_without_a_balance_or_rounding():
    entry = run([record(amount="12.3401")], [document(amount="12.34")])["items"][0]
    assert (entry["status"], entry["official_amount"], entry["reason"]) == (
        "mismatch", "12.3401", "cbs_amount_differs")


def test_missing_local_and_unverified_absence_in_incremental_receipt():
    result = run([record()], [document(key=OTHER, id="xml-2")])
    assert {(i["document_key"], i["status"], i["reason"]) for i in result["items"]} == {
        (KEY, "missing", "local_document_absent"),
        (OTHER, "unknown", "official_coverage_unverified"),
    }
    covered = run([], [document()], covered_periods=[("debitos", "202610")])
    assert covered["items"][0]["status"] == "missing"
    assert covered["items"][0]["reason"] == "official_document_absent_in_covered_period"


def test_multi_branch_cnpj_base_does_not_claim_missing_local_xml():
    result = run([record()], [], company_scope_complete=False)
    assert result["items"][0]["status"] == "unknown"
    assert result["items"][0]["reason"] == "local_company_scope_incomplete"


def test_same_snapshot_repeated_by_another_query_is_not_summed():
    first = record(id="r-1", query="q-1")
    second = record(id="r-2", query="q-2")
    result = run([first, second], [document()])
    assert result["summary"]["match"] == 1
    assert result["items"][0]["record_count"] == 1
    assert result["items"][0]["official_amount"] == "12.34"


def test_two_real_rows_in_one_query_or_changed_snapshots_are_ambiguous():
    duplicate = [record(id="r1"), record(id="r2")]
    same_query = run(duplicate, [document()])["items"][0]
    assert same_query["status"] == "ambiguous"
    assert same_query["record_count"] == 2
    assert same_query["official_amount"] is None
    changed = run([record(query="q1"), record(amount="99.99", query="q2")], [document()])
    assert changed["items"][0]["reason"] == "multiple_official_observations"


def test_distinct_xml_for_same_key_does_not_choose_one_silently():
    items = run([record()], [document(id="a", digest="sha-a"),
                             document(id="b", digest="sha-b")])["items"]
    assert len(items) == 1 and items[0]["status"] == "ambiguous"
    assert items[0]["document_ids"] == ["a", "b"]


def test_official_credit_states_stay_independent_and_are_never_posted():
    credit = record(resource="creditos", amount="10.00", credit_category="government_reported",
                    credit_to_appropriate=Decimal("6.00"),
                    credit_appropriated=Decimal("3.00"), credit_used=Decimal("1.00"))
    result = run([credit], [document(amount="10.00", direction="incoming")])
    item = result["items"][0]
    assert item["status"] == "match"
    assert (item["credit_to_appropriate"], item["credit_appropriated"], item["credit_used"]) == (
        "6.00", "3.00", "1.00")
    assert "balance" not in item and "tax_extinguished" not in item


def test_special_credit_category_is_not_treated_as_normal_purchase_credit():
    special = record(resource="creditos", credit_category="presumido_estoque")
    item = run([special], [document(direction="incoming")])["items"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == "credit_category_requires_independent_review"


def test_payments_and_commercial_installments_do_not_release_credits():
    payment = record(resource="pagamentos", amount="10.00", raw={"payment": {},
                     "composition": {"chaveDFE": KEY}}, provenance={"document_key": KEY})
    installment = SimpleNamespace(document_ref=KEY, paid_at="2026-10-01", amount=Decimal("10"))
    # The installment is intentionally not an input to the fiscal projection.
    assert installment.paid_at
    item = next(item for item in run([payment], [document(amount="10.00")])["items"]
                if item["resource"] == "pagamentos")
    assert item["status"] == "unknown"
    assert item["reason"] == "payment_requires_fiscal_extinction_evidence"
    assert item["credit_appropriated"] is None


def test_missing_direction_amount_key_and_period_remain_unknown():
    result = run([record(amount="12.34")], [document(direction="unknown")])
    assert result["items"][0]["reason"] == "document_direction_unknown"
    result = run([record(amount=None)], [document()])
    assert result["items"][0]["reason"] == "cbs_amount_unavailable"
    result = run([], [document(key=None)])
    assert result["items"][0]["reason"] == "local_document_or_period_unavailable"
    result = run([record(period="??")], [])
    assert result["items"][0]["status"] == "unknown"


def test_scope_and_competence_are_required_for_a_match():
    other_org = record(organization_id="org-b")
    other_company = document(company_id="company-b")
    result = run([other_org], [other_company])
    assert result == {"summary": {"total": 0, "match": 0, "mismatch": 0,
                                  "missing": 0, "ambiguous": 0, "unknown": 0}, "items": []}
    result = run([record(period="2026-09")], [document(period="2026-10")], period="2026-10")
    assert result["items"][0]["status"] == "unknown"
    with pytest.raises(ValueError, match="Competência inválida"):
        run([], [], period="2026/10")


def test_float_never_masquerades_as_exact_fiscal_decimal():
    bad = record()
    bad["amount"] = 0.1
    result = run([bad], [document(amount="0.10")])
    assert result["items"][0]["status"] == "unknown"
    assert result["items"][0]["official_amount"] is None


def test_assisted_preview_separates_cbs_ibs_models_and_directions_without_net_balance():
    docs = [
        document(id="nfe", model="55", kind="nfe", amount="1.25", ibs_amount=Decimal("0.15"),
                 signature_present=True, authorization_status="reported_authorized"),
        document(id="nfce", key=OTHER, digest="nfce-hash", model="65", kind="nfce",
                 amount="2.10", ibs_amount=Decimal("0.20"), signature_present=True,
                 authorization_status="reported_authorized"),
        document(id="cte", key="3" * 44, digest="cte-hash", model="57", kind="cte",
                 direction="incoming", amount="0.90", ibs_amount=Decimal("0.08"),
                 signature_present=True, authorization_status="reported_authorized"),
    ]
    result = assisted_preview([], docs, organization_id="org-a", company_id="company-a",
                              period="2026-10")
    assert result["status"] == "preliminary_unvalidated"
    assert result["coverage"]["official_cbs_complete"] is False
    assert result["coverage"]["signature_cryptographically_verified"] is False
    assert {(x["tax"], x["direction"], x["model"]): x["amount"] for x in result["totals"]} == {
        ("CBS", "outgoing", "55"): "1.25", ("CBS", "outgoing", "65"): "2.10",
        ("CBS", "incoming", "57"): "0.90", ("IBS", "outgoing", "55"): "0.15",
        ("IBS", "outgoing", "65"): "0.20", ("IBS", "incoming", "57"): "0.08",
    }
    assert "payable" not in result and "credit" not in result
    assert all(item["document_ids"] for item in result["items"])


def test_assisted_preview_excludes_ambiguous_event_unsigned_and_protocol_only_claims():
    docs = [
        document(id="duplicate-a", digest="sha-a", model="55", kind="nfe", signature_present=True,
                 authorization_status="reported_authorized"),
        document(id="duplicate-b", digest="sha-b", model="55", kind="nfe", signature_present=True,
                 authorization_status="reported_authorized"),
        document(id="event", key=OTHER, kind="nfe", model=None, digest="event-hash"),
        document(id="unsigned", key="3" * 44, digest="unsigned-hash", model="55", kind="nfe",
                 signature_present=False, authorization_status="reported_authorized"),
        document(id="no-protocol", key="4" * 44, digest="no-protocol-hash", model="55", kind="nfe",
                 signature_present=True, authorization_status="not_provided"),
    ]
    result = assisted_preview([], docs, organization_id="org-a", company_id="company-a",
                              period="2026-10")
    assert result["totals"] == []
    codes = {issue["code"] for issue in result["issues"]}
    assert {"multiple_local_documents", "unsupported_model_or_event", "signature_element_absent",
            "authorization_protocol_not_reported"}.issubset(codes)


def test_reconciliation_accepts_alphanumeric_key_as_identifier_only():
    key = KEY[:8] + "A" + KEY[9:]
    result = run([record(key=key)], [document(key=key)])
    assert result["summary"]["match"] == 1
