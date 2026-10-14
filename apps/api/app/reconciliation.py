"""Conservative, read-only comparison of locally supplied DFe and CBS receipts.

This projection does not calculate the taxpayer's assessment or recognize a
credit. In particular, a commercial installment is never evidence of fiscal
debt extinction. Callers must scope the database query as well; this module
checks the scope again before considering an object.

Suggested API use::

    records = (await session.scalars(select(Record).where(
        Record.organization_id == user.organization_id,
        Record.company_id == company_id))).all()
    documents = (await session.scalars(select(FiscalDocument).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company_id))).all()
    return reconcile(records, documents, organization_id=user.organization_id,
                     company_id=company_id, period=period)

An absent official item cannot be asserted merely because an incremental CBS
response omits it. Pass covered_periods only after explicitly establishing
complete coverage for a resource and period.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any


_KEY = re.compile(r"[0-9]{44}\Z")
_PERIOD = re.compile(r"(20\d\d)-?(0[1-9]|1[0-2])\Z")
_PERIOD_RFB = re.compile(r"(0[1-9]|1[0-2])/(20\d\d)\Z")
_CREDIT_FIELDS = ("credit_to_appropriate", "credit_appropriated", "credit_used")


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _period(value: Any) -> str | None:
    candidate = str(value).strip() if value is not None else ""
    found = _PERIOD.fullmatch(candidate)
    if found:
        return f"{found[1]}-{found[2]}"
    rfb = _PERIOD_RFB.fullmatch(candidate)
    return f"{rfb[2]}-{rfb[1]}" if rfb else None


def _key(value: Any) -> str | None:
    candidate = str(value).strip() if value is not None else ""
    return candidate if _KEY.fullmatch(candidate) else None


def _money(value: Any) -> Decimal | None:
    # Never turn binary floats into supposedly exact fiscal amounts.
    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and abs(amount) < Decimal("1e16") else None


def _shown(value: Any) -> str | None:
    amount = _money(value)
    return str(amount) if amount is not None else None


def _record_key(record: Any) -> str | None:
    raw = _field(record, "raw") or {}
    provenance = _field(record, "provenance") or {}
    if not isinstance(raw, Mapping):
        raw = {}
    if not isinstance(provenance, Mapping):
        provenance = {}
    # Payment compositions refer to the DFe in provenance, while debits and
    # credits have their own top-level ``chave``. Do not parse source_key: it
    # contains positional and content hashes and is not a foreign key.
    composition = raw.get("composition") or {}
    if not isinstance(composition, Mapping):
        composition = {}
    for candidate in (provenance.get("document_key"), raw.get("chave"),
                      raw.get("chaveDFE"), composition.get("chaveDFE")):
        valid = _key(candidate)
        if valid:
            return valid
    return None


def _scope(item: Any, organization_id: str, company_id: str) -> bool:
    return (_field(item, "organization_id") == organization_id
            and _field(item, "company_id") == company_id)


def _signature(record: Any) -> str:
    # A repeated record in a later query is a new observation, not a new
    # amount. Fields describing provenance or the query are intentionally out.
    relevant = {"resource": _field(record, "resource"),
                "period": _period(_field(record, "period")),
                "key": _record_key(record),
                "amount": _shown(_field(record, "amount")),
                "credit_category": _field(record, "credit_category"),
                "raw": _field(record, "raw")}
    relevant.update({name: _shown(_field(record, name)) for name in _CREDIT_FIELDS})
    return json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)


def _observations(records: list[Any]) -> list[Any]:
    """Collapse repeated identical observations without collapsing real lines.

    Two identical lines in ONE query may be distinct items, so the maximum
    multiplicity in any query is retained. A second query returning the same
    lines does not double them. Changed observations remain separate and are
    flagged ambiguous during matching.
    """
    buckets: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for index, record in enumerate(records):
        query_id = _field(record, "query_id")
        # Plain structures without a query id represent independent samples.
        observation = str(query_id) if query_id is not None else f"__row_{index}"
        buckets[_signature(record)][observation].append(record)
    retained = []
    for samples in buckets.values():
        exemplar_query = max(samples, key=lambda query_id: len(samples[query_id]))
        retained.extend(samples[exemplar_query])
    return retained


def _documents(documents: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for document in documents:
        period, key = _period(_field(document, "period")), _key(_field(document, "document_key"))
        # Invalid and keyless files are retained in a separate unknown list by
        # reconcile(), rather than matched on a guessed identifier.
        if not period or not key:
            continue
        digest = _field(document, "sha256") or _field(document, "id")
        identity = (period, key, str(digest))
        if identity not in seen:
            groups[(period, key)].append(document)
            seen.add(identity)
    return groups


def _item(*, period: str | None, key: str | None, resource: str,
          status: str, reason: str, documents: list[Any] = (), records: list[Any] = ()) -> dict:
    first_record = records[0] if len(records) == 1 else None
    first_doc = documents[0] if len(documents) == 1 else None
    # Amount fields are deliberately not summed. Ambiguous records have no
    # single authoritative amount to present to an operator.
    return {
        "document_key": key, "period": period, "resource": resource,
        "status": status, "reason": reason,
        "source_amount": _shown(_field(first_doc, "cbs_amount")) if first_doc else None,
        "official_amount": _shown(_field(first_record, "amount")) if first_record else None,
        **{name: _shown(_field(first_record, name)) if first_record else None
           for name in _CREDIT_FIELDS},
        "credit_category": _field(first_record, "credit_category") if first_record else None,
        "source_authorization_status": (_field(first_doc, "authorization_status")
                                        if first_doc else None),
        "source_signature_present": (_field(first_doc, "signature_present")
                                     if first_doc else None),
        "document_ids": sorted({str(_field(doc, "id")) for doc in documents if _field(doc, "id")}),
        "record_ids": sorted({str(_field(rec, "id")) for rec in records if _field(rec, "id")}),
        "record_count": len(records),
    }


def _classify(resource: str, records: list[Any], documents: list[Any],
              company_scope_complete: bool) -> tuple[str, str]:
    if not documents:
        if not company_scope_complete:
            return "unknown", "local_company_scope_incomplete"
        return "missing", "local_document_absent"
    if len(documents) > 1:
        return "ambiguous", "multiple_local_documents"
    if len(records) > 1:
        return "ambiguous", "multiple_official_observations"
    record, document = records[0], documents[0]
    if resource not in {"debitos", "creditos"}:
        return "unknown", "payment_requires_fiscal_extinction_evidence"
    if resource == "creditos" and _field(record, "credit_category") not in (None, "government_reported"):
        return "unknown", "credit_category_requires_independent_review"
    direction = _field(document, "direction")
    if direction not in {"incoming", "outgoing"}:
        return "unknown", "document_direction_unknown"
    if (resource == "creditos" and direction != "incoming") or (resource == "debitos" and direction != "outgoing"):
        return "unknown", "document_direction_requires_review"
    amount_doc, amount_official = _money(_field(document, "cbs_amount")), _money(_field(record, "amount"))
    if amount_doc is None or amount_official is None:
        return "unknown", "cbs_amount_unavailable"
    return (("match", "amounts_equal") if amount_doc == amount_official
            else ("mismatch", "cbs_amount_differs"))


def reconcile(records: Iterable[Any], documents: Iterable[Any], *,
              organization_id: str, company_id: str, period: str | None = None,
              covered_periods: Iterable[tuple[str, str]] = (),
              company_scope_complete: bool = True) -> dict:
    """Compare official CBS observations with local DFe; never post a tax balance.

    The caller may explicitly provide complete ``(resource, YYYY-MM)``
    coverage to classify a local-only document as missing from official data.
    Without that proof, absence in an incremental response is ``unknown``.
    The five statuses are match, mismatch, missing, ambiguous and unknown.
    Objects, SQLAlchemy entities, and dictionaries are accepted.
    """
    requested_period = _period(period) if period is not None else None
    if period is not None and requested_period is None:
        raise ValueError("Competência inválida; use YYYY-MM")
    scoped_records = [row for row in records if _scope(row, organization_id, company_id)
                      and (requested_period is None or _period(_field(row, "period")) == requested_period)]
    scoped_docs = [doc for doc in documents if _scope(doc, organization_id, company_id)
                   and (requested_period is None or _period(_field(doc, "period")) == requested_period)]
    doc_groups = _documents(scoped_docs)
    unique_records = _observations(scoped_records)
    official: dict[tuple[str | None, str | None, str, str], list[Any]] = defaultdict(list)
    for record in unique_records:
        known_key = _record_key(record)
        # Do not coalesce unrelated official rows merely because both lack a
        # recognizable DFe key. Keep them independently inspectable.
        unresolved = "" if known_key else str(_field(record, "source_key") or _field(record, "id") or _signature(record))
        official[(_period(_field(record, "period")), known_key,
                  str(_field(record, "resource") or "unknown"), unresolved)].append(record)

    items = []
    represented: set[tuple[str, str, str]] = set()
    for (official_period, key, resource, _unresolved), rows in official.items():
        local = doc_groups.get((official_period, key), []) if official_period and key else []
        if official_period and key:
            represented.add((official_period, key, resource))
        if not official_period or not key:
            status, reason = "unknown", "official_document_or_period_unavailable"
        else:
            status, reason = _classify(resource, rows, local, company_scope_complete)
        items.append(_item(period=official_period, key=key, resource=resource,
                           status=status, reason=reason, documents=local, records=rows))

    normalized_coverage = {(str(resource), parsed) for resource, raw_period in covered_periods
                           if (parsed := _period(raw_period)) is not None}
    for (local_period, key), local in doc_groups.items():
        directions = {_field(doc, "direction") for doc in local}
        expected = ("debitos" if directions == {"outgoing"} else
                    "creditos" if directions == {"incoming"} else "unknown")
        if (local_period, key, expected) in represented:
            continue
        if expected == "unknown" and any(
                previous_period == local_period and previous_key == key
                for previous_period, previous_key, _resource in represented):
            continue
        if len(local) > 1:
            status, reason = "ambiguous", "multiple_local_documents"
        elif expected == "unknown":
            status, reason = "unknown", "document_direction_unknown"
        elif (expected, local_period) in normalized_coverage:
            status, reason = "missing", "official_document_absent_in_covered_period"
        else:
            status, reason = "unknown", "official_coverage_unverified"
        items.append(_item(period=local_period, key=key, resource=expected,
                           status=status, reason=reason, documents=local))

    # Keyless local XML remains visible for manual identification. It cannot
    # be matched on value, filename or CNPJ alone.
    for doc in scoped_docs:
        if _period(_field(doc, "period")) and _key(_field(doc, "document_key")):
            continue
        items.append(_item(period=_period(_field(doc, "period")),
                           key=_key(_field(doc, "document_key")), resource="unknown",
                           status="unknown", reason="local_document_or_period_unavailable",
                           documents=[doc]))

    items.sort(key=lambda row: (row["period"] or "", row["document_key"] or "",
                                row["resource"], row["reason"], row["document_ids"], row["record_ids"]))
    counts = Counter(item["status"] for item in items)
    return {"summary": {"total": len(items), **{name: counts[name]
                                             for name in ("match", "mismatch", "missing", "ambiguous", "unknown")}},
            "items": items}
