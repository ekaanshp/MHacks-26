from __future__ import annotations

from email.message import EmailMessage

from import_gmail import fetch_messages, gmail_query


def raw(sender):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "Receipt"
    msg["Date"] = "Fri, 02 Oct 2026 10:00:00 -0400"
    msg.set_content("Your plan renews monthly.")
    return msg.as_bytes()


class FakeGmail:
    def __init__(self):
        self.calls = []

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Inbox) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"']

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7 9"]
        sender = {b"7": "noreply@tm.openai.com", b"9": "billing@anthropic.com"}[args[0]]
        return "OK", [(b"7 (BODY[] {1}", raw(sender)), b")"]


def test_gmail_search_is_server_side_read_only_and_never_marks_read():
    client = FakeGmail()
    messages = fetch_messages(client, ("openai.com", "anthropic.com"), "2021/10/04")
    assert [m["From"] for m in messages] == ["noreply@tm.openai.com", "billing@anthropic.com"]
    assert client.calls[0] == ("select", '"[Gmail]/All Mail"', True)
    assert client.calls[1] == ("SEARCH", "X-GM-RAW", '"(from:openai.com OR from:anthropic.com) after:2021/10/04"')
    assert all(call[2] == "(BODY.PEEK[])" for call in client.calls if call[0] == "FETCH")


def test_gmail_query_lists_every_domain():
    assert gmail_query(("a.com",), "2020/01/01") == "(from:a.com) after:2020/01/01"
