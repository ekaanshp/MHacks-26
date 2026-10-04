from __future__ import annotations

import mailbox
from datetime import date
from email.message import EmailMessage

from import_takeout import import_mailbox


def message(sender, *, labels="Inbox", day="Fri, 02 Oct 2026 10:00:00 -0400", body="Your receipt: $5.00 monthly", markup=False):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Your résumé receipt"
    msg["Date"] = day
    msg["X-Gmail-Labels"] = labels
    if markup:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg


def test_takeout_filters_mime_dates_labels_and_hidden_fields(tmp_path):
    path = tmp_path / "mail.mbox"
    box = mailbox.mbox(str(path))
    for msg in [
        message("owner@example.com"),
        message("receipts@example.com", labels="Inbox, Spam"),
        message("receipts@example.com", labels="Trash"),
        message("receipts@example.com", day="Thu, 01 Jan 2015 10:00:00 -0400"),
        message("Receipts <receipts@example.com>", body="A" * 3000),
        message("receipts@example.com", body="<html><style>hidden</style><p>Your bill &amp; receipt</p></html>", markup=True),
    ]:
        box.add(msg)
    box.flush()
    box.close()
    inbox = import_mailbox(path, own_address="owner@example.com", today=date(2026, 10, 3))
    assert inbox["import_stats"] == {"total": 6, "kept": 2, "invalid_dates": 0, "senders": 1, "spam_or_trash": 2, "sent": 1,
                                     "outside_range": 1, "first_date": "2026-10-02", "last_date": "2026-10-02"}
    assert inbox["synthetic"] is False and inbox["persona"]["name"] == ""
    assert inbox["persona"]["date_of_death"] == "2026-10-03"
    assert len(inbox["emails"][0]["body"]) == 2000
    assert inbox["emails"][0]["subject"] == "Your résumé receipt"
    assert inbox["emails"][1]["body"] == "Your bill & receipt"
    assert all(not key.startswith("_truth") for email in inbox["emails"] for key in email)


def test_takeout_keeps_only_allowed_sender_domains(tmp_path):
    path = tmp_path / "mail.mbox"
    box = mailbox.mbox(str(path))
    for sender in ["noreply@tm.openai.com", "billing@anthropic.com", "news@notanthropic.com", "friend@gmail.com", "x@openai.com.evil.io"]:
        box.add(message(sender))
    box.flush()
    box.close()
    inbox = import_mailbox(path, own_address="owner@example.com", today=date(2026, 10, 3), only_from=("openai.com", "@Anthropic.com "))
    assert sorted(email["from"] for email in inbox["emails"]) == ["billing@anthropic.com", "noreply@tm.openai.com"]
    assert inbox["import_stats"]["other_senders"] == 3
