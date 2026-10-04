"""Generate Margaret's reproducible synthetic inbox, answer key, and bank CSV.

The answer key is separate from extraction inputs. Real Takeout imports must
never populate truth.json, and all generated addresses use example domains.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import os
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from secure_storage import ensure_private_directory, write_json, write_text

BASELINE_ACCOUNTS = [
    {"institution": "Netflix", "category": "subscription", "action": "cancel", "amount": 15.49, "frequency": "monthly", "sender": "info@account.netflix.com"},
    {"institution": "Spotify", "category": "subscription", "action": "cancel", "amount": 11.99, "frequency": "monthly", "sender": "no-reply@spotify.com"},
    {"institution": "Amazon Prime", "category": "subscription", "action": "cancel", "amount": 139.00, "frequency": "annual", "sender": "prime@amazon.com"},
    {"institution": "Planet Fitness", "category": "subscription", "action": "cancel", "amount": 24.99, "frequency": "monthly", "sender": "members@planetfitness.com"},
    {"institution": "iCloud+", "category": "subscription", "action": "cancel", "amount": 2.99, "frequency": "monthly", "sender": "no_reply@email.apple.com"},
    {"institution": "The New York Times", "category": "subscription", "action": "cancel", "amount": 17.00, "frequency": "monthly", "sender": "nytimes@email.newyorktimes.com"},
    {"institution": "Audible", "category": "subscription", "action": "cancel", "amount": 14.95, "frequency": "monthly", "sender": "members@audible.com"},
    {"institution": "Dropbox", "category": "subscription", "action": "cancel", "amount": 119.88, "frequency": "annual", "sender": "no-reply@dropbox.com"},
    {"institution": "DTE Energy", "category": "utility", "action": "transfer", "amount": 86.40, "frequency": "monthly", "sender": "customerservice@dteenergy.com"},
    {"institution": "Xfinity", "category": "utility", "action": "cancel", "amount": 74.99, "frequency": "monthly", "sender": "online.communications@alerts.comcast.net"},
    {"institution": "AT&T", "category": "utility", "action": "cancel", "amount": 65.00, "frequency": "monthly", "sender": "att-services@emaildl.att-mail.com"},
    {"institution": "Chase Checking", "category": "bank", "action": "transfer", "amount": 8452.31, "frequency": "balance", "sender": "no.reply.alerts@chase.com"},
    {"institution": "Chase Credit Card", "category": "debt", "action": "notify", "amount": 2314.87, "frequency": "balance", "sender": "no.reply.alerts@chase.com"},
    {"institution": "Fidelity", "category": "investment", "action": "transfer", "amount": 67240.18, "frequency": "balance", "sender": "fidelity.investments@mail.fidelity.com"},
    {"institution": "Vanguard", "category": "investment", "action": "transfer", "amount": 31220.09, "frequency": "balance", "sender": "vanguard@vanguard.com"},
    {"institution": "MPSERS", "category": "pension", "action": "notify", "amount": 1928.44, "frequency": "monthly", "sender": "ors@michigan.gov"},
    {"institution": "Social Security", "category": "government", "action": "notify", "amount": 1684.60, "frequency": "monthly", "sender": "no-reply@ssa.gov"},
    {"institution": "MetLife", "category": "insurance", "action": "claim", "amount": 50000.00, "frequency": "balance", "sender": "service@metlife.com"},
    {"institution": "Coinbase", "category": "crypto", "action": "transfer", "amount": 2486.20, "frequency": "balance", "sender": "no-reply@coinbase.com"},
    {"institution": "PayPal", "category": "payment_app", "action": "transfer", "amount": 183.42, "frequency": "balance", "sender": "service@paypal.com"},
    {"institution": "Facebook", "category": "digital_legacy", "action": "memorialize", "amount": None, "frequency": "none", "sender": "notification@facebookmail.com"},
    {"institution": "Gmail", "category": "digital_legacy", "action": "memorialize", "amount": None, "frequency": "none", "sender": "no-reply@accounts.google.com"},
]

ADDED_ACCOUNTS = [
    {"institution": "Calm", "category": "subscription", "action": "cancel", "amount": 14.99, "frequency": "monthly", "sender": "no_reply@email.apple.com"},
    {"institution": "Paramount+", "category": "subscription", "action": "cancel", "amount": 7.99, "frequency": "monthly", "sender": "no_reply@email.apple.com"},
    {"institution": "Hulu", "category": "subscription", "action": "cancel", "amount": 7.99, "frequency": "monthly", "sender": "hulu@hulumail.com"},
    {"institution": "Delta SkyMiles", "category": "digital_legacy", "action": "notify", "amount": None, "frequency": "none", "sender": "skymiles@delta.com"},
    {"institution": "Peacock", "category": "subscription", "action": "cancel", "amount": 7.99, "frequency": "monthly", "sender": "team@peacocktv.com"},
    {"institution": "Ypsi Yoga Collective", "category": "subscription", "action": "cancel", "amount": 45.00, "frequency": "monthly", "sender": None},
]

_BANK_DESCRIPTORS = {
    "Netflix": "NETFLIX.COM 866-579-7172 CA",
    "Spotify": "SPOTIFY USA 877-778-1161 NY",
    "Amazon Prime": "AMZN PRIME MEMBERSHIP WA",
    "Planet Fitness": "PLANET FIT CLUB FEES",
    "iCloud+": "APPLE.COM/BILL ICLOUD CA",
    "Calm": "APPLE.COM/BILL CALM CA",
    "Paramount+": "APPLE.COM/BILL PARAMOUNT CA",
    "The New York Times": "NYTIMES DIGITAL SUB NY",
    "Audible": "AUDIBLE MEMBERSHIP NJ",
    "Dropbox": "DROPBOX SUBSCRIPTION CA",
    "DTE Energy": "DTE ENERGY AUTOPAY MI",
    "Xfinity": "COMCAST XFINITY BILL MI",
    "AT&T": "ATT PAYMENT 800-331-0500 TX",
    "Ypsi Yoga Collective": "SQ *YPSI YOGA COLLECTIVE MI",
}


def _today() -> date:
    from datetime import datetime

    return datetime.now(ZoneInfo("America/New_York")).date()


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _standard_body(account: dict[str, Any], event_date: date, next_charge: date | None = None) -> str:
    amount = f"${account['amount']:,.2f}" if account["amount"] is not None else "Not stated"
    lines = [
        "Dear Margaret,",
        f"Here is the latest information for your existing {account['institution']} account.",
        "",
        f"Institution: {account['institution']}",
        f"Account type: {account['category']}",
        f"Amount: {amount}",
        f"Billing: {account['frequency']}",
        f"Requested action after a death: {account['action']}",
    ]
    if next_charge is not None:
        lines.append(f"Next charge: {next_charge.isoformat()}")
    if account["category"] in {"subscription", "utility"}:
        lines.append(f"Payment received: {event_date.isoformat()}. This receipt confirms the charge shown above.")
    elif account["institution"] == "MetLife":
        lines.extend([
            "Your individual whole life insurance policy remains in force.",
            "Death benefit: $50,000.00. This is the insured amount, not the monthly premium.",
            "Monthly premium: $29.00. Daniel Ellis is the named beneficiary.",
            "Please contact our claims team with a certified death certificate to begin a claim.",
        ])
    elif account["category"] == "debt":
        lines.append("This is the outstanding credit card balance owed, not a deposit account.")
    elif account["category"] == "digital_legacy":
        lines.append("This confirms you have an existing account. No paid subscription or balance is shown.")
    elif account["category"] in {"government", "pension"}:
        lines.append("This is your monthly benefit deposit. Your executor should notify us following a death.")
    else:
        lines.append("This is a current statement of account value, not a recurring subscription charge.")
    lines.extend(["", "Keep this message for your records."])
    return "\n".join(lines)


def generate(today: date | str | None = None) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Return three independent fixture objects without writing files."""
    if isinstance(today, str):
        today = date.fromisoformat(today)
    today = today or _today()
    death = today - timedelta(days=21)
    last_receipt = today - timedelta(days=6)
    rng = random.Random(2026)
    emails: list[dict[str, str]] = []

    def add(sender: str, subject: str, sent: date, body: str) -> None:
        emails.append({"from": sender, "subject": subject, "date": sent.isoformat(), "body": body})

    for account in BASELINE_ACCOUNTS:
        if account["institution"] == "iCloud+":
            continue  # All three subscriptions appear as line items below.
        if account["frequency"] == "monthly" and account["category"] in {"subscription", "utility"}:
            for offset in range(18, -1, -1):
                sent = _shift_months(last_receipt, -offset)
                add(account["sender"], f"Your {account['institution']} payment receipt", sent, _standard_body(account, sent, _shift_months(sent, 1)))
        elif account["frequency"] == "annual":
            upcoming = today + timedelta(days=3 if account["institution"] == "Amazon Prime" else 24)
            for offset in range(3, 0, -1):
                sent = upcoming - timedelta(days=365 * offset)
                add(account["sender"], f"Your {account['institution']} annual membership receipt", sent, _standard_body(account, sent, sent + timedelta(days=365)))
            renewal_body = _standard_body(account, upcoming - timedelta(days=365), upcoming)
            renewal_body = "\n".join(line for line in renewal_body.splitlines() if not line.startswith("Payment received:"))
            renewal_body += "\nThis is an advance renewal reminder. No charge has been collected for the upcoming year."
            add(account["sender"], f"Your {account['institution']} membership will renew", today - timedelta(days=2), renewal_body)
        else:
            for offset in (8, 5, 2, 0):
                sent = _shift_months(today - timedelta(days=4), -offset)
                add(account["sender"], f"Your {account['institution']} account statement", sent, _standard_body(account, sent))

    # Three distinct accounts from one sender, with no stand-alone iCloud email.
    for offset in range(18, -1, -1):
        sent = _shift_months(last_receipt, -offset)
        body = "\n".join([
            "Dear Margaret,", "Your receipt from Apple", "",
            "Subscriptions purchased through your Apple account:",
            "iCloud+ — $2.99 monthly", "Calm — $14.99 monthly", "Paramount+ — $7.99 monthly",
            "Total charged: $25.97", f"Payment received: {sent.isoformat()}",
            f"Next charge: {_shift_months(sent, 1).isoformat()}",
            "Each item is an active individual subscription that can be cancelled separately.",
        ])
        add("no_reply@email.apple.com", "Your Apple receipt: iCloud+, Calm, Paramount+", sent, body)

    hulu = next(account for account in ADDED_ACCOUNTS if account["institution"] == "Hulu")
    for month in range(1, 7):
        sent = date(2024, month, 14)
        add(hulu["sender"], "Your Hulu monthly receipt", sent, _standard_body(hulu, sent, _shift_months(sent, 1)))

    delta = next(account for account in ADDED_ACCOUNTS if account["institution"] == "Delta SkyMiles")
    for sent, subject in [(date(2019, 7, 8), "Welcome to Delta SkyMiles"),
                          (_shift_months(today, -14), "Your miles statement"),
                          (_shift_months(today, -2), "Your miles statement")]:
        add(delta["sender"], subject, sent, _standard_body(delta, sent) + "\nYour rewards miles remain available. No dollar value is stated.")

    peacock = next(account for account in ADDED_ACCOUNTS if account["institution"] == "Peacock")
    trial_end = today + timedelta(days=5)
    trial_body = "\n".join([
        "Dear Margaret,", f"Your free trial of Peacock ends on {trial_end.isoformat()}.", "",
        "Institution: Peacock", "Account type: subscription", "Amount: $7.99", "Billing: monthly",
        f"Next charge: {trial_end.isoformat()}", "Requested action after a death: cancel",
        "No payment has been collected. Unless cancelled, the free trial becomes a paid monthly subscription.",
    ])
    add(peacock["sender"], f"Your Peacock free trial ends on {trial_end.isoformat()}", today - timedelta(days=8), trial_body)

    noisy_triggers = [
        ("rewards@kohls.example", "Your Kohl's account has rewards", "This is an advertisement sent to a public mailing list. It does not confirm an account, membership, payment, or balance. Shop this weekend for rewards!"),
        ("office@church.example", "Statement from your church giving", "Community newsletter: this is a fundraising update for the congregation. No personal payment, membership, or financial account is identified."),
        ("security@training.example", "Your account is suspended", "Security-awareness exercise: this is a fake phishing example in our neighborhood newsletter. No real account exists. Ignore the sample payment link."),
        ("offers@coupons.example", "Insurance, pension, and bank statement tips", "General educational newsletter about account safety. No named customer, policy, balance, or existing service is confirmed."),
    ]
    for index in range(40):
        sender, subject, body = noisy_triggers[index % len(noisy_triggers)]
        add(sender, subject, today - timedelta(days=rng.randrange(1, 730)), body)

    noise_subjects = ["Ann Arbor garden club news", "A recipe for Sunday dinner", "Library events this month", "Photos from the family picnic", "Michigan walking trails", "Your neighborhood newsletter", "Local art fair reminder", "A note from Sarah"]
    while len(emails) < 790:
        index = len(emails)
        sender = f"newsletter{index % 35}@community.example"
        subject = noise_subjects[index % len(noise_subjects)]
        body = "Hello Margaret, here is this week's community news. There are no purchases or financial services in this message. We hope to see you at the next gathering."
        add(sender, subject, today - timedelta(days=rng.randrange(1, 1700)), body)
    if len(emails) != 790:
        raise ValueError("Fixture construction exceeded the target of 790 messages.")
    emails.sort(key=lambda item: (item["date"], item["from"], item["subject"], item["body"]))
    for index, email in enumerate(emails):
        email["id"] = f"msg_{index:04d}"

    rows: list[dict[str, Any]] = []
    bank_start = date(2025, 1, 1)

    def bank_row(sent: date, description: str, amount: float) -> None:
        if bank_start <= sent <= death:
            rows.append({"date": sent.isoformat(), "description": description, "amount": round(amount, 2)})

    for account in BASELINE_ACCOUNTS + ADDED_ACCOUNTS:
        institution = account["institution"]
        if institution not in _BANK_DESCRIPTORS:
            continue
        if account["frequency"] == "monthly":
            sent = last_receipt
            while sent >= bank_start:
                bank_row(sent, _BANK_DESCRIPTORS[institution], -account["amount"])
                sent = _shift_months(sent, -1)
        elif account["frequency"] == "annual":
            upcoming = today + timedelta(days=3 if institution == "Amazon Prime" else 24)
            for offset in range(1, 4):
                bank_row(upcoming - timedelta(days=365 * offset), _BANK_DESCRIPTORS[institution], -account["amount"])

    sent = last_receipt
    while sent >= bank_start:
        bank_row(sent, "METLIFE PREM", -29.00)
        bank_row(sent, "SSA TREAS 310 XXSOC SEC", 1684.60)
        bank_row(sent, "MPSERS PENSION BENEFIT", 1928.44)
        sent = _shift_months(sent, -1)

    # One-off purchases have unique merchant descriptors and irregular amounts;
    # they must never satisfy the recurrence rules merely by sharing a city.
    one_off_merchants = ["ARBOR GROCER", "YPSI FUEL", "COMMUNITY PHARMACY", "LOCAL BOOKSHOP", "HARDWARE MARKET"]
    interval = max((death - bank_start).days, 1)
    for index in range(150):
        sent = bank_start + timedelta(days=rng.randrange(interval + 1))
        merchant = f"{one_off_merchants[index % len(one_off_merchants)]} {chr(65 + index // 26)}{chr(65 + index % 26)} ANN ARBOR MI"
        bank_row(sent, f"POS DEBIT {merchant}", -rng.uniform(6, 180))
    rows.sort(key=lambda item: (item["date"], item["description"], item["amount"]))
    for index, row in enumerate(rows):
        row["id"] = f"bank_{index:03d}"

    truth_accounts = []
    baseline_institutions = {account["institution"] for account in BASELINE_ACCOUNTS}
    for account in BASELINE_ACCOUNTS + ADDED_ACCOUNTS:
        truth_accounts.append({
            "institution": account["institution"], "category": account["category"], "action": account["action"],
            "amount": account["amount"], "frequency": account["frequency"],
            "baseline": account["institution"] in baseline_institutions,
            "sources": ["bank"] if account["sender"] is None else (["email", "bank"] if (account["institution"] in _BANK_DESCRIPTORS and account["frequency"] == "monthly") or account["institution"] in {"MetLife", "MPSERS", "Social Security"} else ["email"]),
            "active": account["institution"] != "Hulu",
        })
    inbox = {
        "persona": {
            "name": "Margaret Ellis", "email": "margaret.ellis@example.com", "age": 74,
            "city": "Ann Arbor, Michigan", "date_of_death": death.isoformat(),
            "bio": "Retired Michigan public school teacher, gardener, and mother of Daniel and Sarah. Daniel is her executor.",
        },
        "today": today.isoformat(), "synthetic": True, "emails": emails,
    }
    truth = {"today": today.isoformat(), "baseline_accounts": 22, "expected_accounts": len(truth_accounts), "accounts": truth_accounts}
    return inbox, truth, rows


def generate_inbox(today: date | str | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    """Write all fixtures and return the inbox for in-process startup."""
    inbox, truth, rows = generate(today)
    directory = Path(output_dir or os.environ.get("LASTLY_DATA_DIR", str(Path(__file__).parent / "data")))
    ensure_private_directory(directory)
    for name, value in (("inbox.json", inbox), ("truth.json", truth)):
        path = directory / name
        write_json(path, value)
    with io.StringIO(newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "date", "description", "amount"])
        writer.writeheader()
        writer.writerows(rows)
        write_text(directory / "bank.csv", handle.getvalue())
    return inbox


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the entirely synthetic Margaret Ellis demo estate.")
    parser.add_argument("--today", type=date.fromisoformat, help="Use YYYY-MM-DD for a repeatable demo date; defaults to today in New York.")
    parser.add_argument("--output-dir", type=Path, help="Write fixtures in this directory instead of data/.")
    args = parser.parse_args()
    inbox = generate_inbox(args.today, args.output_dir)
    print(f"Generated {len(inbox['emails'])} synthetic messages, 22 baseline accounts and 28 total expected accounts.")
    print(f"Demo today: {inbox['today']}; Margaret died: {inbox['persona']['date_of_death']}.")


if __name__ == "__main__":
    main()
