from collections import Counter
from datetime import date, timedelta

from generate_inbox import generate, generate_inbox


def test_fixture_is_repeatable_and_truth_is_separate():
    first = generate("2026-10-03")
    assert first == generate("2026-10-03")
    inbox, truth, _ = first
    assert len(inbox["emails"]) == 790
    assert truth["baseline_accounts"] == 22
    assert truth["expected_accounts"] == 28
    assert sum(account["baseline"] for account in truth["accounts"]) == 22
    assert len({(account["institution"], account["category"]) for account in truth["accounts"]}) == 28
    assert len({message["id"] for message in inbox["emails"]}) == 790
    assert all(set(message) == {"id", "from", "subject", "date", "body"} for message in inbox["emails"])
    assert "truth" not in inbox and "_truth" not in str(inbox)


def test_feature_examples_are_visible_in_the_emails():
    inbox, truth, _ = generate("2026-10-03")
    emails = inbox["emails"]
    apple = [message for message in emails if message["from"] == "no_reply@email.apple.com"]
    assert apple
    assert all(all(name in message["body"] for name in ("iCloud+", "Calm", "Paramount+")) for message in apple)
    assert all("$25.97" in message["body"] for message in apple)
    chase = [message for message in emails if message["from"] == "no.reply.alerts@chase.com"]
    assert any("Institution: Chase Checking" in message["body"] for message in chase)
    assert any("Institution: Chase Credit Card" in message["body"] for message in chase)
    metlife = [message for message in emails if message["from"] == "service@metlife.com"]
    assert metlife and all("Death benefit: $50,000.00" in message["body"] for message in metlife)
    assert not any("Ypsi Yoga" in message["body"] for message in emails)
    hulu = [message for message in emails if message["from"] == "hulu@hulumail.com"]
    assert len(hulu) == 6 and max(message["date"] for message in hulu) == "2024-06-14"
    assert next(account for account in truth["accounts"] if account["institution"] == "Hulu")["active"] is False
    delta = [message for message in emails if message["from"] == "skymiles@delta.com"]
    assert len(delta) == 3 and any(message["date"].startswith("2019") for message in delta)
    assert all("Amount: Not stated" in message["body"] for message in delta)


def test_demo_dates_follow_today_and_bank_rows_end_at_death():
    today = date(2026, 10, 3)
    inbox, _, rows = generate(today)
    death = today - timedelta(days=21)
    assert inbox["persona"]["date_of_death"] == death.isoformat()
    prime = [message for message in inbox["emails"] if "membership will renew" in message["subject"] and "Amazon Prime" in message["subject"]]
    assert any(f"Next charge: {(today + timedelta(days=3)).isoformat()}" in message["body"] for message in prime)
    assert all("Payment received:" not in message["body"] and "No charge has been collected" in message["body"] for message in prime)
    peacock = [message for message in inbox["emails"] if message["from"] == "team@peacocktv.com"]
    assert f"Next charge: {(today + timedelta(days=5)).isoformat()}" in peacock[0]["body"]
    assert all(date(2025, 1, 1) <= date.fromisoformat(row["date"]) <= death for row in rows)
    assert not any("HULU" in row["description"] or "PEACOCK" in row["description"] for row in rows)
    assert len({row["id"] for row in rows}) == len(rows)
    counts = Counter(row["description"] for row in rows)
    assert counts["SQ *YPSI YOGA COLLECTIVE MI"] >= 3
    assert counts["SSA TREAS 310 XXSOC SEC"] >= 3
    assert counts["MPSERS PENSION BENEFIT"] >= 3
    purchases = [row for row in rows if row["description"].startswith("POS DEBIT")]
    assert len(purchases) == 150
    assert len({row["description"] for row in purchases}) == 150


def test_writes_portable_csv_and_json(tmp_path):
    import csv
    import json

    inbox = generate_inbox("2026-10-03", tmp_path)
    assert json.loads((tmp_path / "inbox.json").read_text()) == inbox
    assert json.loads((tmp_path / "truth.json").read_text())["expected_accounts"] == 28
    with (tmp_path / "bank.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["id", "date", "description", "amount"]
        rows = list(reader)
    assert any(float(row["amount"]) > 0 for row in rows)
    assert any(float(row["amount"]) < 0 for row in rows)
