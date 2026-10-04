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
    def fake(system, user, **kwargs):
        if system == pipeline.TRIAGE_SYSTEM:
            return {"senders": [{"sender": "billing@example.com", "has_account": True}]}
        return {"accounts": [
            {"institution": "Imaginary", "category": "bank", "amount": 99999, "evidence_ids": ["msg_invented"]},
            {"institution": "Example", "category": "subscription", "frequency": "monthly", "amount": 99999, "evidence_ids": ["msg_1"]},
        ]}
    monkeypatch.setattr(llm, "complete_json", fake)
    found, _, _ = pipeline.extract([email], use_llm=True)
    assert len(found) == 1 and found[0]["amount"] is None


def test_live_triage_replaces_keyword_filter(monkeypatch):
    emails = [
        {"id": "msg_1", "from": "DTE <bills@dte.example>", "date": "2026-09-18", "subject": "Your DTE bill is ready", "body": "Amount due: $86.40 by AutoPay."},
        {"id": "msg_2", "from": "alerts@bank.example", "date": "2026-09-22", "subject": "Votre relevé de compte", "body": "Solde : 1 204,50 €"},
        {"id": "msg_3", "from": "news@club.example", "date": "2026-09-20", "subject": "Garden club news", "body": "See you Sunday."},
        {"id": "msg_4", "from": "skipped@shop.example", "date": "2026-09-21", "subject": "Your receipt", "body": "You paid $9.99."},
    ]
    extracted = []

    def fake(system, user, **kwargs):
        if system == pipeline.TRIAGE_SYSTEM:
            assert kwargs["model"] == "claude-haiku-4-5" and "Amount due" in user
            # skipped@shop.example is left out and must fall back to the keyword rules.
            return {"senders": [{"sender": "bills@dte.example", "has_account": True},
                                {"sender": "alerts@bank.example", "has_account": True},
                                {"sender": "news@club.example", "has_account": False},
                                {"sender": "invented@example.com", "has_account": True}]}
        sender = json.loads(user)["sender"]
        extracted.append(sender)
        return {"accounts": {
            "bills@dte.example": [{"institution": "DTE Energy", "category": "utility", "frequency": "monthly", "amount": 86.40, "evidence_ids": ["msg_1"]}],
            "alerts@bank.example": [{"institution": "Société Générale", "category": "bank", "frequency": "balance", "amount": 1204.50, "evidence_ids": ["msg_2"]}],
            "skipped@shop.example": [{"institution": "Shop", "category": "subscription", "frequency": "monthly", "amount": 9.99, "evidence_ids": ["msg_4"]}],
        }[sender]}

    monkeypatch.setenv("ANTHROPIC_TRIAGE_MODEL", "claude-haiku-4-5")
    monkeypatch.setattr(llm, "complete_json", fake)
    found, candidates, senders = pipeline.extract(emails, use_llm=True)
    assert sorted(extracted) == ["alerts@bank.example", "bills@dte.example", "skipped@shop.example"]
    assert (candidates, senders) == (3, 3)
    amounts = {account["institution"]: account["amount"] for account in found}
    assert amounts == {"DTE Energy": 86.40, "Société Générale": 1204.50, "Shop": 9.99}


@pytest.mark.parametrize("text,value", [("$2,314.87", 2314.87), ("1 204,50 €", 1204.50), ("EUR 1.204,50", 1204.50), ("USD 15.49", 15.49), ("$50,000", 50000.0)])
def test_stated_amounts_accepts_any_currency_format(text, value):
    assert value in pipeline.stated_amounts(text)


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


def test_later_batches_reuse_account_names_from_the_same_sender(monkeypatch):
    emails = [{"id": f"msg_{index}", "from": "alerts@bank.example", "date": f"2026-0{1 + index // 10}-{1 + index % 10:02d}", "subject": "Your statement", "body": "Ending balance: $100.00"} for index in range(30)]
    seen = []

    def fake(system, user, **kwargs):
        if system == pipeline.TRIAGE_SYSTEM:
            return {"senders": [{"sender": "alerts@bank.example", "has_account": True}]}
        request = json.loads(user)
        seen.append(request.get("known_accounts"))
        return {"accounts": [{"institution": "Example Checking", "category": "bank", "frequency": "balance", "amount": 100, "evidence_ids": [request["emails"][0]["id"]]}]}

    monkeypatch.setattr(llm, "complete_json", fake)
    found, _, _ = pipeline.extract(emails, use_llm=True)
    assert seen == [None, ["Example Checking"]]
    assert len(found) == 1 and found[0]["email_count"] == 2


@pytest.mark.parametrize("replies,expected", [
    (['{"ok": true}'], {"ok": True}),
    (['Here are the accounts:\n{"ok": true}\nLet me know.'], {"ok": True}),
    (['```json\n{"ok": true}\n```'], {"ok": True}),
    (["Sorry, I can't.", '{"ok": true}'], {"ok": True}),
])
def test_complete_json_tolerates_and_retries_unreadable_replies(monkeypatch, replies, expected):
    pending = list(replies)
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: pending.pop(0))
    assert llm.complete_json("system", "user") == expected


def test_complete_json_gives_up_after_two_unreadable_replies(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *args, **kwargs: "not json")
    with pytest.raises(llm.LLMError, match="invalid JSON"):
        llm.complete_json("system", "user")


def test_longer_name_for_the_same_account_merges_but_other_types_stay_apart():
    base = {"frequency": "balance", "first_seen": "2026-01-01", "last_seen": "2026-01-01", "sources": ["email"]}
    accounts = [
        {**base, "institution": "Fidelity Investments", "category": "investment", "amount": 100.0, "evidence_ids": ["msg_1"]},
        {**base, "institution": "Fidelity Investments Traditional IRA", "category": "investment", "amount": 100.0, "evidence_ids": ["msg_2"]},
        {**base, "institution": "Chase Total Checking", "category": "bank", "amount": 50.0, "evidence_ids": ["msg_3"]},
        {**base, "institution": "Chase Freedom", "category": "debt", "amount": 20.0, "evidence_ids": ["msg_4"]},
    ]
    merged = pipeline.merge(accounts)
    assert [account["institution"] for account in merged] == ["Fidelity Investments", "Chase Total Checking", "Chase Freedom"]
    assert merged[0]["evidence_ids"] == ["msg_1", "msg_2"]


def test_welcome_to_a_plan_is_named_after_the_sender_not_the_plan():
    def welcome(index, subject, sender):
        return {"id": f"msg_{index:04d}", "from": sender, "subject": subject, "date": "2026-06-28",
                "body": f"{subject}! Your subscription is active and renews monthly."}
    found = pipeline.offline_extract([welcome(1, "Welcome to the Pro plan", "no-reply@mail.anthropic.com"),
                                      welcome(2, "Welcome to ChatGPT Plus", "noreply@email.openai.com")])
    assert sorted(account["institution"] for account in found) == ["Anthropic", "ChatGPT Plus"]


def test_repeated_receipts_without_a_cycle_are_monthly():
    def receipt(index, day, amount="20.00"):
        return {"id": f"msg_{index:04d}", "from": "invoice@mail.anthropic.com", "subject": "Your receipt from Anthropic, PBC",
                "date": day, "body": f"Receipt from Anthropic, PBC ${amount} Paid"}
    found = pipeline.offline_extract([receipt(1, "2026-07-28"), receipt(2, "2026-08-28"), receipt(3, "2026-09-28")])
    assert [(account["frequency"], len(account["_charges"])) for account in found] == [("monthly", 3)]
    one = pipeline.offline_extract([receipt(4, "2026-09-28"), receipt(5, "2026-09-29", "5.00")])
    assert all(account["frequency"] == "none" for account in one)
