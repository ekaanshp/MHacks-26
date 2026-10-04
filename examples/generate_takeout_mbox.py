"""Generate a synthetic Google Takeout mailbox for a fictional person.

The file mirrors Takeout's "All mail Including Spam and Trash.mbox": a `From <id>@xxx <UTC date>`
separator, X-GM-THRID and X-Gmail-Labels first, Gmail's delivery and authentication headers, CRLF
line endings, multipart MIME, threads, attachments, Sent/Spam/Trash/Chat mail and inbox categories.
Everything is fictional and seeded, so the same date produces the same file. The answer key of
real accounts is written next to it.

    python examples/generate_takeout_mbox.py --person margaret-ellis --count 5000
    python examples/generate_takeout_mbox.py --person walter-kowalski --count 4000
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
from datetime import UTC, date, datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

PEOPLE = {
    "margaret-ellis": {
        "name": "Margaret Ellis", "email": "margaret.ellis@example.com", "seed": 1954, "city": "Ann Arbor", "hospital": "Michigan Medicine",
        "family": [("Sarah Ellis", "sarah.ellis@example.com"), ("Daniel Ellis", "daniel.ellis@example.com")],
        "friends": [("Joan Petrakis", "joan.petrakis@example.net"), ("Ruth Okonkwo", "ruth.okonkwo@example.org"), ("Bill Henderson", "bhenderson@example.net"),
                    ("Carol Nguyen", "carol.nguyen@example.com"), ("Pastor Mike Reyes", "office@stmarks.example.org")],
        "utility": ("DTE Energy", "customerservice@dteenergy.com", "DTE"),
        "pension": ("MPSERS (Michigan Office of Retirement Services)", "ors@michigan.gov", "Michigan ORS", "MPSERS retirement allowance", 1928.44, "miAccount"),
        "skip": set(),
    },
    "walter-kowalski": {
        "name": "Walter Kowalski", "email": "walter.kowalski@example.com", "seed": 1949, "city": "Evanston", "hospital": "Northwestern Medicine",
        "family": [("Anna Kowalski", "anna.kowalski@example.com"), ("Peter Kowalski", "peter.kowalski@example.net")],
        "friends": [("Stan Nowak", "stan.nowak@example.net"), ("Gloria Reyes", "gloria.reyes@example.org"), ("Frank Delaney", "fdelaney@example.com"),
                    ("Irene Chu", "irene.chu@example.org"), ("Father Tom Walsh", "office@stnicholas.example.org")],
        "utility": ("ComEd", "noreply@comed.com", "ComEd"),
        "pension": ("Illinois TRS (Teachers' Retirement System)", "member.services@trsil.org", "Illinois TRS", "TRS monthly annuity", 2310.75, "the TRS member portal"),
        # Walter never had these; the answer key leaves them out too.
        "skip": {"audible", "coinbase", "peacock", "delta", "nyt", "hulu_cancelled"},
    },
}
OWNER_NAME = OWNER = FIRST = CITY = HOSPITAL = ""
FAMILY: list = []
FRIENDS: list = []
UTILITY: tuple = ()
PENSION: tuple = ()
SKIP: set = set()


def configure(slug: str) -> dict:
    """Point every template at one fictional person."""
    global OWNER_NAME, OWNER, FIRST, CITY, HOSPITAL, FAMILY, FRIENDS, UTILITY, PENSION, SKIP
    person = PEOPLE[slug]
    OWNER_NAME, OWNER, CITY, HOSPITAL = person["name"], person["email"], person["city"], person["hospital"]
    FIRST = OWNER_NAME.split()[0]
    FAMILY, FRIENDS, UTILITY, PENSION, SKIP = person["family"], person["friends"], person["utility"], person["pension"], person["skip"]
    return person
HOME_TZ = ZoneInfo("America/Detroit")
HERE = Path(__file__).parent
CRLF = "\r\n"
SMTP = policy.SMTP.clone(max_line_length=78)



class Box:
    def __init__(self, today: date, rng: random.Random):
        self.today = today
        self.rng = rng
        self.items: list[tuple[datetime, str]] = []
        self.next_id = 1_745_000_000_000_000_000
        self.threads: dict[str, int] = {}
        self.truth: dict[str, dict] = {}

    def when(self, day: date, hour: int | None = None, tz: ZoneInfo | timezone = HOME_TZ) -> datetime:
        hour = self.rng.randint(6, 22) if hour is None else hour
        return datetime(day.year, day.month, day.day, hour, self.rng.randint(0, 59), self.rng.randint(0, 59), tzinfo=tz)

    def gid(self, sent: datetime) -> int:
        # Gmail ids grow with time; derive from the timestamp plus a counter so order is stable.
        self.next_id += 1
        return int(sent.timestamp() * 1000) * 1_000_000 + self.next_id % 1_000_000

    def add(self, sender: tuple[str, str], subject: str, sent: datetime, *, text: str | None = None,
            html: str | None = None, labels: str = "Inbox,Opened", to: tuple[str, str] | None = None,
            thread: str | None = None, attachments: list[tuple[str, str, bytes]] = (), account: str | None = None,
            extra: dict[str, str] | None = None, reply_to: str | None = None) -> str:
        if account in SKIP:
            return ""
        to = to or (OWNER_NAME, OWNER)
        msg = EmailMessage(policy=SMTP)
        if text is not None:
            msg.set_content(text, cte="quoted-printable")
            if html is not None:
                msg.add_alternative(html, subtype="html", cte="quoted-printable")
        elif html is not None:
            msg.set_content(html, subtype="html", cte="base64" if self.rng.random() < 0.5 else "quoted-printable")
        for filename, mime, data in attachments:
            maintype, subtype = mime.split("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
        body = msg.as_string(policy=SMTP)
        mime_headers, _, payload = body.partition(CRLF + CRLF)

        own_id = self.gid(sent)
        if thread:
            thrid = self.threads.setdefault(thread, own_id)
        else:
            thrid = own_id
        domain = sender[1].split("@")[1]
        utc = sent.astimezone(UTC)
        hop = utc - timedelta(seconds=self.rng.randint(1, 9))
        mid_local = hashlib.sha1(f"{own_id}".encode()).hexdigest()
        if domain.endswith(("example.com", "example.net", "example.org")):
            message_id = f"<CA{base64.urlsafe_b64encode(hashlib.sha256(mid_local.encode()).digest()).decode()[:40]}@mail.gmail.com>"
        else:
            message_id = f"<{mid_local[:24]}.{self.rng.randint(1000, 99999)}@{domain}>"
        ip = f"{self.rng.randint(23, 209)}.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"
        mta = f"mta{self.rng.randint(1, 40)}"
        fake_sig = base64.b64encode(b"".join(hashlib.sha512(f"{message_id}{n}".encode()).digest() for n in range(4))).decode()

        headers = [
            ("X-GM-THRID", str(thrid)),
            ("X-Gmail-Labels", labels),
            ("Delivered-To", OWNER),
            ("Received", f"by 2002:a05:7300:{self.rng.randint(4096, 65535):x}:b0:{self.rng.randint(256, 4095):x} with SMTP id {mid_local[:12]};{CRLF}        {format_datetime(hop)}"),
            ("X-Google-Smtp-Source", base64.b64encode(hashlib.md5(message_id.encode()).digest() * 3).decode()[:62]),
            ("X-Received", f"by 2002:a17:90b:{self.rng.randint(4096, 65535):x} with SMTP id {mid_local[12:24]};{CRLF}        {format_datetime(hop)}"),
            ("ARC-Seal", f"i=1; a=rsa-sha256; t={int(utc.timestamp())}; cv=none;{CRLF}        d=google.com; s=arc-20240605;{CRLF}        b={fake_sig[:64]}"),
            ("ARC-Message-Signature", f"i=1; a=rsa-sha256; c=relaxed/relaxed; d=google.com; s=arc-20240605;{CRLF}        h=to:subject:message-id:date:from:mime-version:dkim-signature;{CRLF}        bh={fake_sig[64:108]}=;{CRLF}        b={fake_sig[108:172]}"),
            ("ARC-Authentication-Results", f"i=1; mx.google.com;{CRLF}       dkim=pass header.i=@{domain};{CRLF}       spf=pass (google.com: domain of bounce@{domain} designates {ip} as permitted sender) smtp.mailfrom=bounce@{domain}"),
            ("Return-Path", f"<bounce-{mid_local[:10]}@{domain}>"),
            ("Received", f"from {mta}.{domain} ({mta}.{domain}. [{ip}]){CRLF}        by mx.google.com with ESMTPS id {mid_local[24:36]}{CRLF}        for <{OWNER}>{CRLF}        (version=TLS1_3 cipher=TLS_AES_256_GCM_SHA384 bits=256/256);{CRLF}        {format_datetime(hop)}"),
            ("Received-SPF", f"pass (google.com: domain of bounce@{domain} designates {ip} as permitted sender) client-ip={ip};"),
            ("Authentication-Results", f"mx.google.com;{CRLF}       dkim=pass header.i=@{domain} header.s=s1 header.b={fake_sig[172:180]};{CRLF}       spf=pass smtp.mailfrom=bounce@{domain};{CRLF}       dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from={domain}"),
            ("DKIM-Signature", f"v=1; a=rsa-sha256; c=relaxed/relaxed; d={domain}; s=s1; t={int(utc.timestamp())};{CRLF}        h=from:to:subject:date:message-id;{CRLF}        bh={fake_sig[180:224]}=;{CRLF}        b={fake_sig[224:300]}"),
        ]
        if sender[1] == OWNER:
            # Messages the owner sent have no inbound delivery trail.
            headers = headers[:2]
        headers += [
            ("From", formataddr(sender) if sender[0] else sender[1]),
            ("Date", format_datetime(sent)),
            ("Message-ID", message_id),
            ("Subject", subject),
            ("To", formataddr(to)),
        ]
        if reply_to:
            headers.append(("Reply-To", reply_to))
        for key, value in (extra or {}).items():
            headers.append((key, value))

        lines = []
        for key, value in headers:
            if key == "Subject" or key == "From":
                # Let the email package fold and RFC 2047 encode non-ASCII values.
                probe = EmailMessage(policy=SMTP)
                probe[key] = value
                lines.append(probe.as_string(policy=SMTP).split(CRLF + CRLF)[0])
            else:
                lines.append(f"{key}: {value}")
        raw = CRLF.join(lines) + CRLF + mime_headers + CRLF + CRLF + payload
        raw = CRLF.join(">" + line if line.startswith("From ") else line for line in raw.split(CRLF))
        separator = f"From {own_id}@xxx {utc.strftime('%a %b %d %H:%M:%S +0000 %Y')}"
        self.items.append((utc, separator + CRLF + raw.rstrip(CRLF) + CRLF + CRLF))
        if account:
            self.truth[account].setdefault("message_ids", []).append(message_id)
        return message_id

    def account(self, key: str, **fields) -> None:
        if key not in SKIP:
            self.truth[key] = fields


def months_back(day: date, count: int) -> list[date]:
    out = []
    year, month = day.year, day.month
    for _ in range(count):
        out.append(date(year, month, min(day.day, 28)))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out[::-1]


def receipt_html(brand: str, color: str, rows: list[tuple[str, str]], footer: str) -> str:
    cells = "".join(f'<tr><td style="padding:6px 0;color:#555">{k}</td><td style="padding:6px 0;text-align:right"><b>{v}</b></td></tr>' for k, v in rows)
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{{font-family:Helvetica,Arial,sans-serif}}'
            f'.btn{{background:{color};color:#fff;padding:10px 18px;border-radius:4px;text-decoration:none}}</style></head>'
            f'<body style="margin:0;background:#f4f4f4"><table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">'
            f'<table width="600" style="background:#fff;padding:24px"><tr><td><h1 style="color:{color};font-size:28px">{brand}</h1>'
            f'<table width="100%">{cells}</table><p><a class="btn" href="https://www.example.com/account">Manage account</a></p>'
            f'<p style="font-size:11px;color:#999">{footer}</p></td></tr></table></td></tr></table>'
            f'<img src="https://www.example.com/open/{random.randint(10**8, 10**9)}.gif" width="1" height="1" alt=""></body></html>')


def pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def build(today: date, count: int, seed: int) -> Box:
    rng = random.Random(seed)
    random.seed(seed)
    box = Box(today, rng)
    start = today - timedelta(days=3 * 365)
    last = today - timedelta(days=22)  # The owner's last day of activity; they died 21 days ago.
    death = today - timedelta(days=21)

    def label(category: str = "Updates", read: bool = True, important: bool = False) -> str:
        parts = ["Important"] if important else []
        parts += ["Inbox" if rng.random() < 0.7 else "Archived", f"Category {category}", "Opened" if read else "Unread"]
        return ",".join(parts)

    def unread_after_death(day: date, category: str = "Updates", important: bool = False) -> str:
        return label(category, read=day <= last, important=important)

    # ---- Monthly subscriptions and utilities --------------------------------------------------
    monthly = [
        ("netflix", "Netflix", "info@account.netflix.com", 15.49, "Standard plan", "#e50914", "subscription"),
        ("spotify", "Spotify", "no-reply@spotify.com", 11.99, "Premium Individual", "#1db954", "subscription"),
        ("planet_fitness", "Planet Fitness", "members@planetfitness.com", 24.99, "Black Card membership", "#5c2d91", "subscription"),
        ("nyt", "The New York Times", "nytimes@email.newyorktimes.com", 17.00, "All Access subscription", "#000000", "subscription"),
        ("audible", "Audible", "donotreply@audible.com", 14.95, "Audible Premium Plus", "#f7991c", "subscription"),
        ("xfinity", "Xfinity", "online.communications@alerts.comcast.net", 74.99, "Xfinity Internet 300 Mbps", "#6138f5", "utility"),
        ("att", "AT&T", "att-services@emaildl.att-mail.com", 65.00, "Unlimited Starter, 1 line", "#00a8e0", "utility"),
    ]
    for key, brand, address, amount, plan, color, category in monthly:
        box.account(key, institution=brand, category=category, amount=amount, frequency="monthly", sender=address)
        for day in months_back(today - timedelta(days=rng.randint(3, 12)), 36):
            if day < start:
                continue
            nxt = (day + timedelta(days=31)).replace(day=day.day)
            if key == "xfinity" or key == "att":
                subject = f"Your {brand} bill is ready" if key == "xfinity" else "Your AT&T wireless bill is ready to view"
                text = f"Hi {FIRST},\n\nYour bill for account ending in {rng.randint(1000, 9999)} is ready.\n\nAmount due: ${amount:.2f}\nDue date: {nxt:%B %d, %Y}\nAutoPay is on. We'll charge your Visa ending in 4417 on the due date.\n\nView your bill: https://www.example.com/bill\n"
                html = receipt_html(brand, color, [("Plan", plan), ("Amount due", f"${amount:.2f}"), ("Due date", f"{nxt:%b %d, %Y}"), ("AutoPay", "On")], f"{brand}, PO Box 1000. To stop receiving these emails, update your preferences.")
            else:
                subject = {"netflix": "Your Netflix payment receipt", "spotify": "Your Spotify Premium receipt",
                           "planet_fitness": "Your monthly club fees receipt", "nyt": "Your New York Times subscription receipt",
                           "audible": "Your Audible membership receipt"}[key]
                text = f"Hi {FIRST},\n\nThanks for being a member. We charged ${amount:.2f} to your Visa ending in 4417 for your {plan}.\n\nBilling date: {day:%B %d, %Y}\nNext billing date: {nxt:%B %d, %Y}\n\nQuestions? Visit the Help Center.\n"
                html = receipt_html(brand, color, [("Plan", plan), ("Amount", f"${amount:.2f}"), ("Payment method", "Visa •••• 4417"), ("Next billing date", f"{nxt:%b %d, %Y}")], f"This account email was sent to {OWNER}. {brand} Inc.")
            box.add((brand, address), subject, box.when(day), text=text, html=html, labels=unread_after_death(day), account=key)

    # Spotify price change notice and Netflix marketing from the real sender (not new accounts).
    box.add(("Spotify", "no-reply@spotify.com"), "An update on your Premium price", box.when(today - timedelta(days=200)),
            text=f"Hi {FIRST},\n\nStarting next month your Premium Individual price will change from $10.99 to $11.99/month.\n", html=None, labels=label(), account="spotify")
    for offset in range(0, 700, 45):
        box.add(("Netflix", "info@mailer.netflix.com"), rng.choice(["New on Netflix this week", "Top 10 in the U.S. today", "Your next watch is here"]),
                box.when(today - timedelta(days=offset + 2)), html=receipt_html("Netflix", "#e50914", [("Trending", "Three new series")], "Unsubscribe from Netflix promotional emails."), labels=label("Promotions", rng.random() < 0.4))

    # DTE Energy: amount varies by season.
    box.account("utility", institution=UTILITY[0], category="utility", amount=None, frequency="monthly", sender=UTILITY[1])
    for day in months_back(today - timedelta(days=8), 36):
        if day < start:
            continue
        due = round(62 + 48 * abs(6 - day.month) / 6 + rng.uniform(-6, 6), 2)
        box.add((UTILITY[0], UTILITY[1]), f"Your {UTILITY[2]} bill is ready", box.when(day),
                text=f"{OWNER_NAME}\nAccount 9100 {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}\n\nAmount due: ${due:.2f}\nPayment will be drafted by AutoPay on {day + timedelta(days=21):%m/%d/%Y}.\nUsage this period: {rng.randint(410, 980)} kWh electric, {rng.randint(8, 120)} CCF gas.\n",
                html=receipt_html(UTILITY[2], "#0067b1", [("Amount due", f"${due:.2f}"), ("AutoPay date", f"{day + timedelta(days=21):%m/%d/%Y}")], f"{UTILITY[0]} customer care."), labels=unread_after_death(day), account="utility")

    # Apple bundle: three subscriptions in one HTML-only receipt.
    for key, name, price in (("icloud", "iCloud+", 2.99), ("calm", "Calm", 14.99), ("paramount", "Paramount+", 7.99)):
        box.account(key, institution=name, category="subscription", amount=price, frequency="monthly", sender="no_reply@email.apple.com")
    for day in months_back(today - timedelta(days=6), 30):
        if day < start:
            continue
        rows = [("iCloud+ with 200 GB (Monthly)", "$2.99"), ("Calm: Sleep & Meditation (Monthly)", "$14.99"), ("Paramount+ Essential (Monthly)", "$7.99"), ("TOTAL", "$25.97")]
        message_id = box.add(("Apple", "no_reply@email.apple.com"), "Your receipt from Apple.", box.when(day, tz=ZoneInfo("America/Los_Angeles")),
                             html=receipt_html("Receipt", "#000", [("Apple Account", OWNER), ("Order ID", f"M{rng.randint(10**9, 10**10)}")] + rows, "Apple Inc., One Apple Park Way, Cupertino, CA."),
                             labels=unread_after_death(day))
        for key in ("icloud", "calm", "paramount"):
            box.truth[key].setdefault("message_ids", []).append(message_id)

    # ---- Annual memberships ------------------------------------------------------------------
    for key, brand, address, amount, plan, renew in (("prime", "Amazon Prime", "prime@amazon.com", 139.00, "Prime membership", today + timedelta(days=3)),
                                                    ("dropbox", "Dropbox", "no-reply@dropbox.com", 119.88, "Dropbox Plus (annual)", today + timedelta(days=24))):
        box.account(key, institution=brand, category="subscription", amount=amount, frequency="annual", sender=address, next_date=renew.isoformat())
        for years in (3, 2, 1):
            paid = renew - timedelta(days=365 * years)
            if paid >= start:
                box.add((brand, address), f"Your {plan} receipt", box.when(paid), text=f"Hello {FIRST},\n\nThank you for renewing your {plan}. You were charged ${amount:.2f}.\nYour membership renews on {paid + timedelta(days=365):%B %d, %Y}.\n", html=None, labels=label(), account=key)
        box.add((brand, address), f"Your {plan} will renew on {renew:%B %d}", box.when(today - timedelta(days=2)),
                text=f"Hello {FIRST},\n\nYour {plan} will automatically renew on {renew:%B %d, %Y} for ${amount:.2f}. No action is needed to keep your benefits.\n", html=None, labels=label(read=False), account=key)

    # Amazon one-off orders: purchases, not accounts to close beyond Prime.
    items = ["Reading glasses 3-pack", "Bird seed, 20 lb", "Large-print crossword book", "Vitamin D3 gummies", "Garden kneeler", "Kindle Paperwhite case", "Hummingbird feeder"]
    for _ in range(120):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        order = f"112-{rng.randint(10**6, 10**7 - 1)}-{rng.randint(10**6, 10**7 - 1)}"
        price = rng.choice([8.99, 12.49, 17.95, 24.99, 31.20, 6.79])
        item = rng.choice(items)
        box.add(("Amazon.com", "auto-confirm@amazon.com"), f'Your Amazon.com order of "{item}"', box.when(day),
                html=receipt_html("amazon", "#ff9900", [("Order #", order), ("Item", item), ("Order total", f"${price:.2f}")], "This email was sent from a notification-only address."), labels=label())
        box.add(("Amazon.com", "shipment-tracking@amazon.com"), f'Shipped: "{item}"', box.when(day + timedelta(days=1)),
                html=receipt_html("amazon", "#ff9900", [("Order #", order), ("Arriving", f"{day + timedelta(days=3):%A, %B %d}")], "Track your package in Your Orders."), labels=label())

    # ---- Financial accounts -------------------------------------------------------------------
    box.account("chase_checking", institution="Chase Checking", category="bank", amount=8452.31, frequency="balance", sender="no.reply.alerts@chase.com")
    box.account("chase_card", institution="Chase Freedom credit card", category="debt", amount=2314.87, frequency="balance", sender="no.reply.alerts@chase.com")
    for day in months_back(today - timedelta(days=10), 36):
        if day < start:
            continue
        box.add(("Chase", "no.reply.alerts@chase.com"), "Your statement is ready for your account ending in 5521", box.when(day),
                text=f"Your Chase Total Checking statement for the account ending in 5521 is now available.\nEnding balance: ${8452.31 + rng.uniform(-900, 900) if day < today - timedelta(days=40) else 8452.31:,.2f}\nSign in at chase.com to view it.\n", html=None, labels=label(), account="chase_checking")
        box.add(("Chase", "no.reply.alerts@chase.com"), "Your credit card statement is available", box.when(day + timedelta(days=3)),
                text=f"Chase Freedom ending in 8821\nNew balance: ${2314.87 if day > today - timedelta(days=40) else rng.uniform(300, 2200):,.2f}\nMinimum payment due: $40.00\nPayment due date: {day + timedelta(days=28):%m/%d/%Y}\n", html=None, labels=label(important=True), account="chase_card")
    for _ in range(90):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        box.add(("Chase", "no.reply.alerts@chase.com"), "You made a debit card purchase", box.when(day),
                text=f"A ${rng.uniform(4, 180):.2f} debit card transaction with {rng.choice(['KROGER #612', 'MEIJER #108', 'CVS/PHARMACY', 'SHELL OIL', 'BUSCH S'])} was made on {day:%b %d, %Y}.\n", labels=label(), account="chase_checking")
    for offset in range(30, 1000, 75):
        box.add(("Chase", "chase@marketing.chase.com"), f"{FIRST}, you're pre-approved for Chase Sapphire Preferred", box.when(today - timedelta(days=offset)),
                html=receipt_html("Chase", "#117aca", [("Offer", "Earn 60,000 bonus points")], "You are receiving this email because you are a Chase customer. Unsubscribe from promotional email."), labels=label("Promotions", False))

    for key, brand, address, amount, product in (("fidelity", "Fidelity Investments", "fidelity.investments@mail.fidelity.com", 67240.18, "Traditional IRA ending 3302"),
                                                 ("vanguard", "Vanguard", "vanguard@eonline.e-vanguard.com", 31220.09, "Brokerage account ending 7710")):
        box.account(key, institution=brand, category="investment", amount=amount, frequency="balance", sender=address)
        for day in months_back(today - timedelta(days=12), 36)[::3]:
            if day >= start:
                value = amount if day > today - timedelta(days=100) else amount * rng.uniform(0.82, 0.98)
                box.add((brand, address), "Your quarterly statement is ready", box.when(day),
                        text=f"Dear {OWNER_NAME},\n\nYour quarterly statement for your {product} is available online.\nTotal account value: ${value:,.2f}\n", html=None, labels=label(), account=key)

    box.account("coinbase", institution="Coinbase", category="crypto", amount=2486.20, frequency="balance", sender="no-reply@coinbase.com")
    for day in months_back(today - timedelta(days=20), 24)[::2]:
        if day >= start:
            box.add(("Coinbase", "no-reply@coinbase.com"), "Your monthly Coinbase statement", box.when(day),
                    text=f"Hi {FIRST},\n\nYour portfolio balance on {day:%B %d, %Y}: ${2486.20 * rng.uniform(0.6, 1.1):,.2f}\nHoldings: Bitcoin, Ethereum.\n", html=None, labels=label(), account="coinbase")
    box.account("paypal", institution="PayPal", category="payment_app", amount=183.42, frequency="balance", sender="service@paypal.com")
    for _ in range(40):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        box.add(("PayPal", "service@paypal.com"), rng.choice(["Receipt for your payment to St. Mark's Church", "You've got money", "Your PayPal balance summary"]), box.when(day),
                text=f"Hello {OWNER_NAME},\n\nTransaction ID: {rng.randint(10**15, 10**16)}\nAmount: ${rng.uniform(10, 120):.2f} USD\nPayPal balance: $183.42\n", html=None, labels=label(), account="paypal")

    box.account("metlife", institution="MetLife", category="insurance", amount=50000.00, frequency="balance", sender="service@metlife.com")
    for years in (2, 1, 0):
        day = date(today.year - years, 3, 14)
        if start <= day <= last:
            box.add(("MetLife", "service@metlife.com"), "Your annual life insurance policy statement", box.when(day),
                    text=f"Dear {OWNER_NAME},\n\nYour annual statement for policy 55-883-120 is attached. Please keep it with your important papers.\n",
                    attachments=[(f"MetLife_Annual_Statement_{day.year}.pdf", "application/pdf", pdf(f"MetLife policy 55-883-120 Face amount $50,000.00 Insured {OWNER_NAME} {day.year}"))],
                    labels=label(important=True), account="metlife")
    box.add(("MetLife", "service@metlife.com"), "Your premium payment was received", box.when(today - timedelta(days=60)),
            text="We received your quarterly premium payment of $68.40 for policy 55-883-120. Thank you.\n", labels=label(), account="metlife")

    # ---- Income and government ------------------------------------------------------------
    box.account("ssa", institution="Social Security", category="government", amount=1684.60, frequency="monthly", sender="no-reply@ssa.gov")
    for year in (today.year - 2, today.year - 1, today.year):
        day = date(year, 1, 6) if year < today.year else date(year, 1, 6)
        if start <= day <= last:
            box.add(("Social Security Administration", "no-reply@ssa.gov"), "You have a new message in my Social Security", box.when(day),
                    text=f"Your {year} cost-of-living adjustment notice is available. Your new monthly benefit amount is ${1684.60 - (today.year - year) * 41:,.2f}.\nSign in to my Social Security to read it.\n", labels=label(important=True), account="ssa")
    box.account("pension", institution=PENSION[0], category="pension", amount=PENSION[4], frequency="monthly", sender=PENSION[1])
    for day in months_back(today - timedelta(days=4), 36)[::6]:
        if start <= day <= last:
            box.add((PENSION[2], PENSION[1]), f"Your pension payment advice is available in {PENSION[5]}", box.when(day),
                    text=f"Your {PENSION[3]} has been deposited. Net payment: ${PENSION[4]:,.2f}.\nLog in to {PENSION[5]} for details.\n", labels=label(), account="pension")

    # ---- Trials, cancelled services, loyalty, digital legacy ------------------------------
    trial_end = today + timedelta(days=5)
    box.account("peacock", institution="Peacock", category="subscription", amount=7.99, frequency="monthly", sender="team@peacocktv.com", next_date=trial_end.isoformat())
    box.add(("Peacock", "team@peacocktv.com"), "Welcome to your Peacock Premium free trial", box.when(today - timedelta(days=25)),
            text=f"Hi {FIRST},\n\nYour 30-day free trial has started. After {trial_end:%B %d, %Y} you'll be charged $7.99/month unless you cancel.\n", labels=label(), account="peacock")
    box.add(("Peacock", "team@peacocktv.com"), f"Reminder: your free trial ends {trial_end:%B %d}", box.when(today - timedelta(days=2)),
            text=f"Your Peacock Premium trial ends on {trial_end:%B %d, %Y}. Your card will be charged $7.99 per month after that.\n", labels=label(read=False), account="peacock")
    box.account("hulu_cancelled", institution="Hulu (cancelled 2024)", category="subscription", amount=7.99, frequency="monthly", sender="hulu@hulumail.com", active=False)
    for month in range(1, 7):
        box.add(("Hulu", "hulu@hulumail.com"), "Your Hulu receipt", box.when(date(2024, month, 14)), text="Thanks for watching. You were charged $7.99 for Hulu (With Ads).\n", labels=label(), account="hulu_cancelled")
    box.add(("Hulu", "hulu@hulumail.com"), "Your Hulu subscription has been canceled", box.when(date(2024, 7, 2)), text="We're sorry to see you go. Your subscription has been canceled and you won't be charged again.\n", labels=label(), account="hulu_cancelled")
    box.account("delta", institution="Delta SkyMiles", category="digital_legacy", amount=None, frequency="none", sender="skymiles@delta.com")
    for day in months_back(today, 36)[::4]:
        if start <= day <= last:
            box.add(("Delta SkyMiles", "skymiles@delta.com"), "Your SkyMiles statement", box.when(day), html=receipt_html("Delta", "#003366", [("SkyMiles number", "9 2041 7731"), ("Miles balance", f"{rng.randint(41000, 43000):,}")], "Delta Air Lines. Manage email preferences."), labels=label(), account="delta")
    box.account("facebook", institution="Facebook", category="digital_legacy", amount=None, frequency="none", sender="notification@facebookmail.com")
    for _ in range(260):
        day = start + timedelta(days=rng.randint(0, (today - start).days - 1))
        who = rng.choice([FRIENDS[0][0], FAMILY[0][0], f"{CITY} Garden Club", FRIENDS[1][0], "the neighborhood group"])
        box.add(("Facebook", "notification@facebookmail.com"), rng.choice([f"{who} shared a photo", f"{who} commented on your post", "You have 3 new notifications", f"{who} posted in {CITY} Garden Club"]),
                box.when(day), html=receipt_html("facebook", "#1877f2", [("New activity", who)], f"This message was sent to {OWNER}. Unsubscribe."), labels=label("Social", day <= last and rng.random() < 0.5), account="facebook")
    box.account("google", institution="Google Account", category="digital_legacy", amount=None, frequency="none", sender="no-reply@accounts.google.com")
    for offset in (900, 600, 320, 150, 40):
        box.add(("Google", "no-reply@accounts.google.com"), "Security alert", box.when(today - timedelta(days=offset)), text=f"A new sign-in on iPad\n{OWNER}\nWe noticed a new sign-in to your Google Account. If this was you, you don't need to do anything.\n", labels=label(important=True), account="google")

    # ---- Decoys that look like accounts but are not -------------------------------------------
    decoys = [
        (("Netflix Support", "support@netflix-billing-secure.example"), "Your account is on hold: update payment", "We were unable to validate your billing information. Click here within 24 hours to avoid suspension: http://netflix-billing-secure.example/login"),
        (("Geek Squad Billing", "invoices@geeksquad-renewal.example"), "Invoice #GS-88213: $399.99 auto-renewal", "Your Geek Squad protection has been renewed for $399.99. If you did not authorize this, call 1-888-000-0000 immediately."),
        (("USPS", "tracking@usps-redelivery.example"), "Package delivery failed", "Your package could not be delivered. Pay a $1.99 redelivery fee here."),
    ]
    for _ in range(36):
        sender, subject, text = rng.choice(decoys)
        day = start + timedelta(days=rng.randint(0, (today - start).days - 1))
        box.add(sender, subject, box.when(day), text=text, labels=rng.choice(["Spam,Unread", "Spam,Opened", "Inbox,Category Updates,Unread"]))
    for _ in range(140):
        day = start + timedelta(days=rng.randint(0, (today - start).days - 1))
        box.add((rng.choice(["Prize Center", "Dr. Ade", "Rx Discounts", "Crypto Gains"]), f"{rng.choice(['win', 'claim', 'deals', 'notice'])}{rng.randint(10, 999)}@{rng.choice(['bulk-mail', 'winner-notice', 'cheap-meds', 'fastprofit'])}.example"),
                rng.choice(["Congratulations! You've been selected", "URGENT: Unclaimed funds in your name", "Lowest prices on prescriptions", "Turn $250 into $10,000"]),
                box.when(day), text="Reply with your full name, address and bank details to claim.", labels=rng.choice(["Spam,Unread", "Spam,Opened"]))

    # ---- Personal mail, threads and the owner's replies ----------------------------------------
    topics = [("Sunday dinner", "Are you still coming Sunday? I'm making the pot roast."), ("Doctor appointment Thursday", "Can you drive me to Dr. Patel on Thursday at 10?"),
              ("Photos from the lake", "Here are the pictures from the lake weekend. The kids loved it."), ("Book club - next pick", "Next month we're reading The Women by Kristin Hannah."),
              ("Tomato plants", "My tomatoes are finally coming in. Want some?"), ("Happy birthday!", "Happy birthday! Hope you have a lovely day."),
              ("Church potluck sign-up", "We still need two desserts for the fall potluck."), ("Recipe you asked for", "Here's Mom's lemon bar recipe, as promised.")]
    for index in range(260):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        person = rng.choice(FAMILY + FRIENDS)
        topic, opener = rng.choice(topics)
        thread = f"thread-{index}"
        attachments = [(f"IMG_{rng.randint(1000, 9999):04d}.jpg", "image/jpeg", b"\xff\xd8\xff\xe0" + bytes(rng.randrange(256) for _ in range(600)) + b"\xff\xd9")] if topic.startswith("Photos") else []
        first = box.when(day)
        box.add(person, topic, first, text=f"Hi {FIRST},\n\n{opener}\n\nLove,\n{person[0].split()[0]}\n", labels=label("Personal", important=True), thread=thread, attachments=attachments)
        if rng.random() < 0.7:
            reply_time = first + timedelta(hours=rng.randint(1, 30))
            quoted = f"On {format_datetime(first)}, {person[0]} <{person[1]}> wrote:\n> {opener}\n"
            box.add((OWNER_NAME, OWNER), f"Re: {topic}", reply_time, text=f"{rng.choice(['Sounds wonderful!', 'Yes, thank you dear.', 'I will be there.', 'Lovely, thank you!'])}\n\nFrom my iPad\n\n{quoted}",
                    labels="Sent", to=person, thread=thread, extra={"In-Reply-To": "<prior@mail.gmail.com>"})
    for _ in range(40):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        box.add((OWNER_NAME, OWNER), rng.choice([f"Chat with {FAMILY[0][0]}", f"Chat with {FRIENDS[0][0]}"]), box.when(day), text=f"{FIRST}: did you get my message?\n{FAMILY[0][0].split()[0]}: yes! call you tonight", labels="Chat", to=FAMILY[0])
    # A relative forwards a statement that went to the wrong address.
    box.add(FAMILY[0], "Fwd: Your quarterly statement is ready", box.when(today - timedelta(days=60)),
            text="Mom, this came to me by mistake.\n\n---------- Forwarded message ---------\nFrom: Fidelity Investments <fidelity.investments@mail.fidelity.com>\nSubject: Your quarterly statement is ready\n\nTotal account value: $67,240.18\n",
            labels=label("Personal", important=True), account="fidelity")
    # Condolence mail arrives after the death.
    for offset in range(1, 20, 2):
        person = rng.choice(FRIENDS)
        box.add(person, "Thinking of you all", box.when(death + timedelta(days=offset)), text=f"I'm so sorry for your loss. {FIRST} was a wonderful friend.", labels="Inbox,Category Personal,Unread", to=(OWNER_NAME, OWNER))
    for _ in range(25):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        box.add((HOSPITAL, "noreply@mychart.example"), "Appointment reminder", box.when(day),
                text=f"Reminder: you have an appointment with Dr. Patel on {day + timedelta(days=3):%A, %B %d} at 10:00 AM. Reply C to confirm.", labels=label())
    for _ in range(12):
        day = start + timedelta(days=rng.randint(0, (last - start).days))
        box.add(("Google Calendar", "calendar-notification@google.com"), f"Invitation: Book club @ {day:%a %b %d, %Y} 2pm - 4pm (EDT)", box.when(day),
                text="Joan Petrakis has invited you to an event.\nBook club\nWhen: 2pm - 4pm (Eastern Time)\nWhere: Joan's house", html=None,
                attachments=[("invite.ics", "text/calendar", f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:Book club\r\nDTSTART:{day:%Y%m%d}T180000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n".encode())], labels=label())

    # ---- Bulk promotions and newsletters fill the rest -------------------------------------
    if len(box.items) > count:
        raise SystemExit(f"--count must be at least {len(box.items)} to hold every account email.")
    promos = [("Kohl's", "kohls@s.kohls.com", ["Your Kohl's Cash expires soon!", "Extra 30% off with your Kohl's Card"]),
              ("Target", "target@em.target.com", ["New fall arrivals are here", "Deals of the week"]),
              ("Old Navy", "oldnavy@email.oldnavy.com", ["50% off EVERYTHING ends tonight", "Your weekend just got better"]),
              ("Wayfair", "shop@e.wayfair.com", ["Up to 70% off outdoor furniture", "Way Day starts now"]),
              ("Bed Bath & Beyond", "bedbath@emailbbb.com", ["20% off one item", "Fresh linens for fall"]),
              ("Harry & David", "harryanddavid@email.harryanddavid.com", ["Royal Riviera pears are back", "Gifts they'll love"]),
              ("Groupon", "noreply@r.groupon.com", [f"{CITY}: up to 60% off spa days", "Today's top deals near you"]),
              ("AARP Rewards", "aarp@email.aarp.org", [f"{FIRST}, earn points this week", "Your member discounts"]),
              ("Société Générale", "news@email.societegenerale.example", ["Découvrez nos offres d'épargne", "Votre lettre d'information"])]
    letters = [("AARP The Magazine", "aarpmagazine@email.aarp.org", ["This week: 10 ways to sleep better", "Your AARP Bulletin"]),
               (f"{CITY} Public Library", "events@library.example", ["Library events this month", "New large-print arrivals"]),
               (f"{CITY} Daily", "newsletters@localnews.example", [f"{CITY} morning headlines", "Weekend things to do"]),
               ("St. Mark's Lutheran", "office@stmarks.example.org", ["This Sunday at St. Mark's", "Prayer list update"]),
               (f"{CITY} Garden Club", "news@gardenclub.example", ["October meeting: bulbs for spring", "Garden club news"]),
               ("Nextdoor", "no-reply@rs.email.nextdoor.com", ["Lost cat on Packard St", "Trending in Burns Park"]),
               ("Taste of Home", "newsletters@tasteofhome.example", ["5-star apple desserts", "Your weekly dinner plan"])]
    while len(box.items) < count:
        day = start + timedelta(days=rng.randint(0, (today - start).days - 1))
        name, address, subjects = rng.choice(promos if rng.random() < 0.62 else letters)
        category = "Promotions" if (name, address, subjects) in promos else "Updates"
        subject = rng.choice(subjects)
        html = receipt_html(name, "#333", [("This week", subject)], "You are receiving this because you subscribed. <a href=\"https://example.com/unsub\">Unsubscribe</a> | View in browser")
        box.add((name, address), subject, box.when(day), html=html, labels=label(category, day <= last and rng.random() < 0.35),
                extra={"List-Unsubscribe": f"<mailto:unsub-{rng.randint(10**6, 10**7)}@{address.split('@')[1]}>, <https://{address.split('@')[1]}/u>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    return box


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--person", choices=sorted(PEOPLE), default="margaret-ellis")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--today", type=date.fromisoformat, default=datetime.now(HOME_TZ).date())
    parser.add_argument("--output", type=Path, help="Defaults to examples/<person>/All mail Including Spam and Trash.mbox")
    args = parser.parse_args()
    person = configure(args.person)
    output = args.output or HERE / args.person / "All mail Including Spam and Trash.mbox"
    output.parent.mkdir(parents=True, exist_ok=True)
    box = build(args.today, args.count, person["seed"])
    # Takeout lists newest messages first.
    box.items.sort(key=lambda item: item[0], reverse=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        for _, raw in box.items:
            handle.write(raw)
    truth = {"name": OWNER_NAME, "owner": OWNER, "today": args.today.isoformat(), "date_of_death": (args.today - timedelta(days=21)).isoformat(),
             "messages": len(box.items), "accounts": [{"key": key, **{k: v for k, v in value.items() if k != "message_ids"}, "evidence_messages": len(set(value.get("message_ids", [])))} for key, value in box.truth.items()]}
    output.with_name("takeout-truth.json").write_text(json.dumps(truth, indent=2, ensure_ascii=False) + "\n")
    print(f"{OWNER_NAME}: {len(box.items)} messages ({output.stat().st_size / 1e6:.1f} MB) in {output}")
    print(f"Answer key: {len(box.truth)} accounts in {output.with_name('takeout-truth.json')}")


if __name__ == "__main__":
    main()
