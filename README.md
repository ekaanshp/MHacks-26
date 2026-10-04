# Lastly

Lastly discovers a loved one's accounts from inbox messages and a bank statement, shows the proof, and helps the family track the next steps. Built from `Lastly_Build_Plan.pdf` with Python/FastAPI and a native HTML/CSS/JavaScript dashboard.

The complete local app works before any provider accounts are created. Anthropic, Neon, ElevenLabs/Twilio, and Fetch.ai adapters are ready for credentials. Real outbound calls and Agentverse registration require their later account setup; the offline app never pretends to place a call.

## Run locally

Use Python 3.11 or newer on Linux/macOS (Windows: use WSL). Private storage requires POSIX filesystem protections. Run these commands inside `MHacks-26`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
python generate_inbox.py
python pipeline.py --mock
uvicorn server:app --reload --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000**. Continue through the plan-ahead screen, then select **Read Margaret's inbox**. A fresh checkout also generates and analyzes the synthetic dataset automatically on server startup if none exists.

Use **http://localhost:8000/?demo=1** to skip the plan-ahead screen and use fixed analysis animation timing. Cached analysis, local letters, and local questions keep this mode independent of live LLM calls. The phone call remains an explicit, live action once configured.

The fixture uses the generation date as today. Margaret's death is 21 days earlier, Prime renews in three days, and Peacock's free trial ends five days later. Re-generate before the presentation to keep these dates current. Family assignments and statuses survive analysis reruns.

`data/demo/` contains a generated, synthetic snapshot of the inbox, statement, truth and analyzed estate that can be reviewed and shared with the code. `data/` runtime files stay ignored. To inspect the supplied snapshot directly, set `LASTLY_DATA_DIR=data/demo` before starting the app.

## What's implemented

- Exactly 790 synthetic emails, 22 reconstructed baseline accounts and six additional accounts, bank CSV, and a separate ground-truth file. The missing starter's original 22-account fixture was not available; this reconstruction covers every described feature.
- Evidence-backed offline discovery and configurable Anthropic extraction, multiple accounts from one sender, Apple receipt line items, recurring bank detection, income classification, and reconciliation without merging Chase checking and credit card.
- All endpoints from the plan, cached analysis, per-account status and assignment, activity feed, and source fingerprints to prevent stale evidence after an import.
- Neon JSONB schema and durable JSON fallback. Edits made during an outage are queued and replayed on reconnection, preserving fields changed by other family members.
- Responsive dashboard, onboarding, urgent renewals, $50,000 policy discovery, both proof sources, coverage checklist, reviewed letter drafts, evidence-backed questions, and accessible account drawers.
- Explicit ElevenLabs calls with dynamic variables, AI disclosure, transcript polling, reference numbers, and completion only after an unconditional company confirmation.
- **Talk to them** opens a live microphone conversation with Lastly; you play the company representative. Choose your microphone, watch its input level, mute/unmute, or type replies. **Let agents handle it** exchanges account requests and responses through the two Fetch.ai processes, including subscriptions such as Paramount. The company process is a demonstration stand-in; its messages and references are generated from each request and synthetic account progress updates when it confirms completion.
- Optional Fetch.ai Chat Protocol/mailbox agent, five-slide pitch, Devpost draft, role-play script, and demo/submission checklist.
- **Connect or upload email**: a guided flow for Gmail (Google Takeout), iCloud Mail (Apple Mail export) and Yahoo Mail (via Apple Mail or Thunderbird), with progress, a review step (owner, date range, message count, skipped mail) before analysis, and delete controls. Live sync is labelled as not available yet. Test mailboxes: `python examples/generate_takeout_mbox.py --person margaret-ellis|walter-kowalski`.
- **Family workspace**: a review queue for thin findings, reviewed corrections (name, amount, category, billing) that keep the discovered original, dismissing false positives, adding missing accounts, notes, help requests and handoffs, follow-ups with due dates, and per-institution guides with a document checklist, a verified contact route and a downloadable document packet.
- **What should we do next?** orders overdue promises, renewals, requested documents, help requests and the largest charges and claims. Also: how each bill is paid (funding map), a weekly family digest and a downloadable executor report.
- **Outcomes for every action**: calls, voice conversations, Fetch.ai agents and claims record completed / request opened / documents required / declined / unclear on the server, so progress survives reloads and devices. Conditional, future and corrected replies never count as confirmation. Agent requests time out with a reason and can be retried with their history kept.

After updating conversation code, restart the web server and both agent processes, then refresh the page. Run `python manage.py elevenlabs-setup` to apply the caller prompt and longer response window to the configured ElevenLabs agent. Fetch.ai subscription conversations reuse `CLAIMS_AGENT_ADDRESS` and `CLAIMS_AGENT_ENDPOINT`; separate company agent settings are optional.

The Lastly agent always keeps its Agentverse Mailbox (so ASI:One can reach it). `LASTLY_AGENT_ENDPOINT` only tells the local demo company agent where to deliver replies on the same laptop.

See [the account setup guide](docs/integrations.md), [demo script](docs/demo.md), [role-play](docs/roleplay.md), [pitch deck](docs/pitch.html), [Devpost draft](docs/devpost-draft.md), and [acceptance checklist](docs/acceptance.md).

## Demo families

The start screen asks for the deceased person's full name (exact spelling and capital letters), your first name and your relationship. Each family has its own estate, accounts, activity and Neon records; all data is synthetic.

| Deceased person | Relatives (executor first) |
|---|---|
| Margaret Ellis | Daniel (son), Sarah (daughter) |
| Harold Bennett | Linda (daughter), Michael (son) |
| Rosa Martinez | Elena (daughter), Carlos (husband) |
| James Okafor | Grace (wife), David (son) |
| Eleanor Whitfield | Thomas (son), Anne (granddaughter) |

The four extra people are generated in `data/estates/` on server start from the root inbox's demo date, so every laptop sharing `data/inbox.json` gets identical files (`LASTLY_EXTRA_ESTATES=false` disables them). In Neon, `people_overview` lists every deceased person with relatives and progress.

## Connect providers later

Credentials belong in `.env`, which Git ignores. [`.env.example`](.env.example) lists every setting. Restart the server after changing `.env`.

| Product | Settings | Next step |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `LASTLY_OFFLINE=false` | Run `python pipeline.py --live` and review live recall and proof |
| Neon | `DATABASE_URL` (paste Neon's string as-is) | `python manage.py neon-init`, then run the pipeline to persist the estate |
| ElevenLabs | API key, Twilio SID/token, family access code, approved destinations, `LASTLY_OFFLINE=false` | `python manage.py elevenlabs-setup --twilio-number +1...` creates the agent and connects the number; save the printed IDs |
| Fetch.ai | `AGENT_SEED`, dedicated `LASTLY_AGENT_TOKEN`, mailbox/port/API URL settings | `pip install -r requirements-agent.txt`, run `fetch_agent.py`, and connect Mailbox |

Run `python manage.py integrations` at any time to see which integrations are connected and what is missing.

`LASTLY_OFFLINE=true` disables Anthropic and phone calls. Neon remains independently controlled by `DATABASE_URL`. Registration does not happen until you explicitly run the optional agent. No accounts, real calls, uploads, publications, or Git commits were made during development.

## Import a real Gmail Takeout

Keep private data in a separate directory. Configure a random `LASTLY_ACCESS_TOKEN` and a `LASTLY_DATA_KEY` following [the security guide](SECURITY.md) before importing. The importer encrypts its output, reads `.mbox`, skips outgoing messages and Spam/Trash, decodes headers and MIME, truncates bodies to 2,000 characters, and keeps the last five years by default.

```bash
python import_takeout.py '/path/All mail Including Spam and Trash.mbox' \
  --email owner@example.com --years 5 --output /path/private-estate/inbox.json
```

Set `LASTLY_DATA_DIR=/path/private-estate` for the pipeline and server. The importer refuses to overwrite an existing inbox. It leaves the person's real name blank and sets the date of death to today as the plan specifies. Update encrypted metadata with `python manage.py persona --name 'Person Name' --date-of-death YYYY-MM-DD`. Import a bank CSV with `python manage.py import-bank --input /path/statement.csv`, using `id,date,description,amount`, unique `bank_` IDs and negative debits. Re-analyze after changes.

Real imports stay local by default, including when Neon is configured. `ALLOW_PRIVATE_CLOUD=true` is explicit consent to share data with configured providers. Offline rules are intentionally conservative for arbitrary inboxes; use a reviewed live analysis to measure AI discovery. Real mailbox data, runtime caches, credentials, and call records are ignored by Git. Never force-add private files.

This is a single-family Family Hub prototype, intended for one backend process. Private estates fail closed without authentication and encryption. Browser sessions use HttpOnly cookies and CSRF protection; the Fetch.ai bridge uses a separate credential restricted to reads and questions. Calls require approved destinations and durable idempotency. See [SECURITY.md](SECURITY.md) for configuration, threat boundaries, TLS, limits and the identity-provider requirements before a public launch. Everything remains uncommitted.

## Amounts and evidence

`monthly_drain` is the sum of active discovered monthly charges plus annual charges divided by 12; inactive accounts are excluded. A future trial conversion is included as a known upcoming recurring amount. `charged_since_death` sums supported charge events after death through the analysis date, deduplicating matching email/bank date-and-amount evidence. Renewal reminders do not count as payments. These are observed totals, not a refund or savings promise.

`assets_found` sums stated balances and policy benefits in the waiting bucket. It does not add recurring pension/income deposits. `debts_found` sums debt balances. `death_certificates` is a planning estimate of distinct institutions outside subscriptions/utilities; each institution's actual document requirements must be confirmed. Human task status does not change the amounts established by source records.

Buckets follow categories: subscriptions/utilities → leaving; banks/investments/insurance/crypto/payment apps → waiting; pensions/government → notify; debts → owed; digital legacy → legacy. Delta's points account has no fabricated dollar value. An account needs at least one valid evidence ID. Bank-only results require three regular charges within 5% of the median and monthly/annual date gaps; one-off purchases are excluded.

## Verify and rehearse

```bash
pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
node --check static/app.js
python -m playwright install chromium
python tests/browser_smoke.py
```

The tests cover discovery, evidence, family state, encrypted files, verified database TLS, authentication, CSRF, origin/host checks, rate/body limits, agent permissions, durable call replay protection and truthful outcomes. Browser verification covers 390, 1280 and 1440 pixels plus authenticated sessions and CSP enforcement; the smoke script starts isolated synthetic backends.

Local fixture recall is **28/28 with offline rules**, with 452 bank rows on October 3, 2026. This is a test fixture result, not measured live Anthropic accuracy. Live Neon sharing, ElevenLabs phone delivery, and Agentverse registration still require the accounts and verification described in the setup guide.

```bash
python manage.py export --output data/estate-backup.json
python manage.py reset-progress
```

Export refuses to overwrite existing backups. Reset only affects the synthetic demo. Record the final two-minute video with live call audio after voice setup, check sponsor requirements, and complete the Devpost submission manually. Nothing is committed yet.
