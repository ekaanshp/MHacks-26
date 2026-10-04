from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import bank
import llm
import pipeline
from models import Estate


def by_name(estate, name):
    return next(account for account in estate["accounts"] if account["institution"] == name)


def test_full_recall_and_every_proof_resolves(estate_data, dataset):
    Estate.model_validate(estate_data)
    assert estate_data["analysis"]["recall"] == {"found": 28, "expected": 28, "missing": [], "unexpected": []}
    emails = json.loads((dataset / "inbox.json").read_text())["emails"]
    all_ids = {email["id"] for email in emails} | {row["id"] for row in bank.read_rows(dataset / "bank.csv")}
    assert all(set(account["evidence_ids"]) <= all_ids for account in estate_data["accounts"])
    assert estate_data["stats"]["emails"] == 790
    assert {account["institution"] for account in estate_data["accounts"] if account["category"] in {"bank", "debt"}} == {"Chase Checking", "Chase Credit Card"}


def test_policy_value_bank_only_and_inactive_totals(estate_data):
    metlife = by_name(estate_data, "MetLife")
    assert metlife["amount"] == 50000 and metlife["category"] == "insurance"
    assert set(metlife["sources"]) == {"email", "bank"}
    hulu = by_name(estate_data, "Hulu")
    assert hulu["active"] is False and hulu["urgent"] is False
    assert hulu["last_seen"] == "2024-06-14"
    yoga = by_name(estate_data, "Ypsi Yoga Collective")
    assert yoga["sources"] == ["bank"] and yoga["email_count"] == 0
    delta = by_name(estate_data, "Delta SkyMiles")
    assert delta["amount"] is None
    prime = by_name(estate_data, "Amazon Prime")
    assert prime["days_until"] == 3 and prime["urgent"]
    assert by_name(estate_data, "Peacock")["days_until"] == 5
    expected = sum((account["amount"] or 0) / (12 if account["frequency"] == "annual" else 1) for account in estate_data["accounts"] if account["bucket"] == "leaving" and account["active"] and account["frequency"] in {"monthly", "annual"})
    assert estate_data["totals"]["monthly_drain"] == round(expected, 2)
    assert estate_data["totals"]["charged_since_death"] == 336.78


def test_truth_is_never_used_to_extract_accounts(dataset):
    (dataset / "truth.json").unlink()
    result = pipeline.run(mock=True, data_dir=dataset)
    assert len(result["accounts"]) == 28
    assert result["analysis"]["recall"] is None


def test_payment_date_and_renewal_notice_not_email_delivery_date():
    email = {"id": "msg_1", "date": "2026-10-02", "subject": "Your receipt", "body": "Payment received: 2026-09-01. Amount: $139.00"}
    assert pipeline.charge_events(email, 139, "annual") == [{"date": "2026-09-01", "amount": 139}]
    email["body"] += " No new payment has been collected."
    assert pipeline.charge_events(email, 139, "annual") == []


def test_later_welcome_does_not_erase_cycle_or_revive_inactive_account():
    old = {"institution": "Example", "category": "subscription", "amount": 20, "frequency": "monthly", "next_date": None, "first_seen": "2025-01-01", "last_seen": "2025-06-01", "evidence_ids": ["msg_1"], "sources": ["email"], "_charges": [{"date": "2025-06-01", "amount": 20}]}
    new = {**old, "amount": None, "frequency": "none", "last_seen": "2026-10-03", "evidence_ids": ["msg_2"], "_charges": []}
    merged = pipeline.merge([old, new])
    result, _ = pipeline.summarize(merged, {"date_of_death": "2026-09-12"}, date(2026, 10, 3))
    assert result[0]["frequency"] == "monthly" and result[0]["amount"] == 20
    assert result[0]["active"] is False


def test_private_input_does_not_contact_anthropic(dataset, monkeypatch):
    inbox_path = dataset / "inbox.json"
    inbox = json.loads(inbox_path.read_text())
    inbox["synthetic"] = False
    inbox_path.write_text(json.dumps(inbox))
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: pytest.fail("Private evidence left the machine"))
    with pytest.raises(ValueError, match="Private imports stay local"):
        pipeline.run(mock=False, data_dir=dataset)


def test_live_extraction_drops_hallucinated_evidence_and_unproved_amount(monkeypatch):
    email = {"id": "msg_1", "from": "billing@example.com", "date": "2026-10-01", "subject": "Your monthly receipt", "body": "Example plan charged $5.00 monthly."}
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: {"accounts": [
        {"institution": "Imaginary", "category": "bank", "amount": 99999, "evidence_ids": ["msg_invented"]},
        {"institution": "Example", "category": "subscription", "frequency": "monthly", "amount": 99999, "evidence_ids": ["msg_1"]},
    ]})
    found, _, _ = pipeline.extract([email], use_llm=True)
    assert len(found) == 1 and found[0]["amount"] is None


@pytest.mark.parametrize("offset,expected", [(45, True), (46, False)])
def test_monthly_inactivity_boundary(offset, expected):
    today = date(2026, 10, 3)
    seen = (today - timedelta(days=offset)).isoformat()
    item = {"institution": "Example", "category": "subscription", "amount": 1, "frequency": "monthly", "first_seen": seen, "last_seen": seen, "evidence_ids": ["msg_1"], "sources": ["email"]}
    accounts, _ = pipeline.summarize([item], {"date_of_death": "2026-09-12"}, today)
    assert accounts[0]["active"] is expected


def test_latest_account_balance_wins_without_replacing_policy_with_bank_premium():
    old = {"institution": "Investment", "category": "investment", "amount": 100, "frequency": "balance", "first_seen": "2025-01-01", "last_seen": "2025-06-01", "evidence_ids": ["msg_1"], "sources": ["email"]}
    new = {**old, "amount": 200, "last_seen": "2026-10-03", "evidence_ids": ["msg_2"]}
    assert pipeline.merge([old, new])[0]["amount"] == 200
