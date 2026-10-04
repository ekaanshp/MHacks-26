# Connect the accounts later

The app runs locally without any sponsor account. Start with the README's offline instructions. `.env` stores credentials locally and is ignored by Git. These adapters are implemented; their live operation needs accounts and a deliberate verification run. No phone call, Agentverse registration, account creation, or upload occurs simply by importing the code or running the offline demo.

## Anthropic

Set `ANTHROPIC_API_KEY`, set `ANTHROPIC_MODEL` to a model available to that account, and set `LASTLY_OFFLINE=false`. Run `python pipeline.py` to extract accounts using Anthropic. Review the printed recall and evidence before using that estate in a pitch. `python pipeline.py --mock` explicitly uses local rules; a successful offline recall score is not an LLM accuracy measurement.

The Q&A endpoint and letters reuse the same adapter. Every letter must be reviewed before sending. Real imported inbox data stays local by default. Sending it to a provider requires `ALLOW_PRIVATE_CLOUD=true`; configure an access token before sharing an instance. Account extraction, letter drafting, and calls remain separate requests.

## Check everything at once

```bash
python manage.py integrations
```

Prints `OK`, `FAIL` or `OFF` for Neon, ElevenLabs and Fetch.ai with the next step for anything missing. It exits non-zero if a configured integration is broken, and it never places a call.

## Neon Postgres / Family Hub

1. Sign up at neon.com (free plan), create a project named `lastly`, and copy its connection string into `.env` as `DATABASE_URL`. Paste it unchanged: the app always forces `sslmode=verify-full` with a verified CA bundle, so URL options cannot weaken TLS. See [storage transport](storage-security.md).
2. Run `python manage.py neon-init` to apply `schema.sql` (or paste the file into Neon's SQL editor). It reports how many estate revisions are stored.
3. Run `python pipeline.py --mock` to save the synthetic estate into Neon, then `python manage.py integrations` should show Neon `OK`.
4. Checkpoint A: point two laptops' `.env` at the same `DATABASE_URL`, run the server on both, assign Spotify to Sarah and mark Netflix Done on one; refresh the other and confirm both changes. Re-run the pipeline and confirm the edits survive.

**Reading the data in Neon:** open the SQL Editor and query the readable views instead of raw JSON:

```sql
SELECT institution, status, assigned_to, assigned_member_id, updated_at
FROM account_overview WHERE is_latest ORDER BY updated_at DESC;

SELECT created_at, actor, actor_member_id, action, institution
FROM activity_overview ORDER BY created_at DESC LIMIT 20;

SELECT id, name FROM family_members;
```

`family_members` gives every assignee a private `mem_…` id. Database triggers link `accounts.assigned_member_id` and `activity.actor_member_id` automatically; agents such as "Insurer agent" are not members. These ids are backend-only: the API and dashboard return names, never member ids. Member ids exist in Neon only, not in the local JSON fallback.

**Relatives and live sync.** `FAMILY_RELATIVES` (default `Daniel:son,Sarah:daughter`; the `FAMILY_EXECUTOR` is marked executor) lists the deceased person's relatives. On startup they are stored in Neon's `relatives` table, each linked to a private member id; `family_overview` shows each relative's assignments and updates. In the app, **Viewing as** chooses which relative is acting (also `?as=Sarah` in the URL); changes and activity are recorded under that name. It is an attribution label behind the family access code, not a separate login. Every open dashboard checks the shared estate every few seconds and updates itself when another relative changes something, with a notice such as "Sarah updated Netflix."

**Demo reset.** Start the presenting server with `LASTLY_RESET_ON_START=true` to begin from zero: all synthetic accounts open and unassigned, activity, claims and agent requests cleared. Only set it on the demo server; any server started with it resets the shared estate for everyone.

With no `DATABASE_URL`, the Family Hub uses the local JSON store. During a Neon outage the adapter serves its local mirror and queues family edits, replaying them when Neon is reachable again; the terminal logs that Neon is unavailable. Imported (non-synthetic) estates are never uploaded unless `ALLOW_PRIVATE_CLOUD=true`. Share the connection string privately, never in the repo.

## ElevenLabs in the browser (no phone number needed)

The free option. The agent talks through the laptop's microphone and speakers instead of a phone line.

1. Sign up at elevenlabs.io (choose **ElevenAgents**). Create an API key with **ElevenAgents: Write** (and optionally **Voices: Read**) and set `ELEVENLABS_API_KEY` in `.env`.
2. Run `python manage.py elevenlabs-setup` and save the printed `ELEVENLABS_AGENT_ID` in `.env`.
3. Set `LASTLY_OFFLINE=false` and a random `LASTLY_ACCESS_TOKEN` (`python -c 'import secrets; print(secrets.token_urlsafe(32))'`), then restart the server. `python manage.py integrations` should show ElevenLabs `OK`.
4. Unlock the dashboard with the access code, open Planet Fitness, click **Talk to them here**, then **Approve & start conversation**, and allow the microphone. Use Chrome or Safari.
5. The agent opens with its AI disclosure. A teammate plays the company into the laptop mic (see [the role-play](roleplay.md)). Click **End conversation**: the transcript is fetched from ElevenLabs, and Planet Fitness is marked Done only if the company clearly confirmed cancellation. The reference number is shown.

The API key never reaches the browser: the server requests a short-lived signed URL. The page allows only microphone access and a WebSocket to `wss://api.elevenlabs.io`. The SDK (`@elevenlabs/client` 1.26.0, MIT) and its audio worklets are self-hosted in `static/vendor/` to keep the strict script policy. Firefox may need a resampler the app does not ship; use Chrome or Safari for the demo.

## ElevenLabs + Twilio (optional real phone calls)

Uses [ElevenLabs outbound Twilio calls](https://elevenlabs.io/docs/api-reference/twilio/outbound-call) and [conversation details](https://elevenlabs.io/docs/api-reference/conversations/get).

1. Create the ElevenLabs and Twilio (trial) accounts. Get a Twilio number and, on a trial account, verify every teammate phone that will receive calls under **Verified Caller IDs**. Trial calls play a Twilio notice and require a key press before the agent speaks; rehearse that.
2. In `.env` set `ELEVENLABS_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, a random `LASTLY_ACCESS_TOKEN` (see [SECURITY.md](../SECURITY.md)), `FAMILY_EXECUTOR`, and the teammate numbers in `LASTLY_ALLOWED_CALL_NUMBERS` (E.164, comma-separated).
3. Create the agent and connect the number in one step:

   ```bash
   python manage.py elevenlabs-setup --twilio-number +1734XXXXXXX [--voice-id <calm voice id>]
   ```

   This creates (or, if `ELEVENLABS_AGENT_ID` is already set, updates) the agent with `AGENT_PROMPT`, `FIRST_MESSAGE`, the AI disclosure and all six dynamic variables (`person_name`, `date_of_death`, `institution`, `action`, `category`, `executor_name`), imports the Twilio number into ElevenLabs if needed, and assigns the agent to it. Copy the printed `ELEVENLABS_AGENT_ID` and `ELEVENLABS_PHONE_NUMBER_ID` into `.env`. Re-run it after editing the prompt in `calls.py`.
4. Set `LASTLY_OFFLINE=false`, restart the server, and run `python manage.py integrations` until ElevenLabs shows `OK`.
5. Open Planet Fitness in the dashboard, unlock with the access code, enter an approved number and place the call. Rehearse [the role-play](roleplay.md) and verify the AI disclosure, correct name/date, and that the agent repeats the reference number. Checkpoint A is three successful calls in a row from the dashboard button. The same account/number has a two-minute cooldown and calls are budgeted; never repeat an uncertain call before checking the ElevenLabs call log.

The dashboard polls the transcript while a call is in progress. A completed phone conversation does not establish completed cancellation: automatic completion requires a clear company statement that cancellation is complete, with no outstanding condition. The reference number is taken from company speech. Call metadata is saved locally in ignored `data/runtime_calls.json`. Optional Anthropic transcript summaries additionally require `ALLOW_PRIVATE_CLOUD=true`.

## Fetch.ai / Agentverse / ASI:One

The bridge implements the [Fetch.ai Chat Protocol](https://uagents.fetch.ai/docs/examples/asi-1). It forwards questions to the Lastly API and returns that API's evidence IDs; it runs no separate LLM and needs no ASI key.

1. Ask the sponsor what the current prize requires (Agentverse registration, Chat Protocol, ASI:One discoverability).
2. Install the agent packages into the same environment: `pip install -r requirements-agent.txt` (verified on Python 3.12 and 3.14).
3. In `.env` set `AGENT_SEED` to a long random phrase and keep it fixed (it determines the agent address), and a separate random `LASTLY_AGENT_TOKEN` (`python -c 'import secrets; print(secrets.token_urlsafe(32))'`). Keep `AGENT_MAILBOX=true`, `AGENT_PORT=8001`, `LASTLY_API_BASE_URL=http://127.0.0.1:8000`. Restart the server so it accepts the agent token.
4. With the server running, `python manage.py integrations` shows the agent address and whether the API is reachable. Then run `python fetch_agent.py`, open the Agent Inspector link it prints, and connect **Mailbox** to register on Agentverse.
5. On Agentverse, name the profile **Lastly Estate Assistant** with the description **Answers questions about estate accounts discovered from email and bank statements, with evidence citations. The public demo uses synthetic data.** Add example questions: “Did Margaret have life insurance?”, “Which accounts are still charging?”, “What renews in the next 14 days?”, “Which subscriptions were found only on a bank statement?”
6. Ask “Did Margaret have life insurance?” through Agentverse or ASI:One. The reply must mention MetLife and cite a `msg_` ID. Keep the server and agent running while judges test it.

### Agent-to-agent claims (simulated insurer)

`insurer_agent.py` is a second uAgent that stands in for an insurance company's claims agent. It is a simulation, not affiliated with any insurer, and files nothing real. In the MetLife drawer, **Open a claim via agent** queues a claim; Lastly's agent (which holds the agent identity, not the web server) sends a signed `LastlyEstateClaim` `ClaimRequest` to the insurer agent, which replies with a claim number and the documents the beneficiary must send. Lastly accepts the answer only from `CLAIMS_AGENT_ADDRESS`, marks the policy In progress, and logs it in the family activity feed.

Setup: set `CLAIMS_AGENT_SEED` (long random phrase) and the derived `CLAIMS_AGENT_ADDRESS`; `python manage.py integrations` shows both agent addresses. On one laptop, `CLAIMS_AGENT_ENDPOINT` and `LASTLY_AGENT_ENDPOINT` deliver messages directly between the two local agents (fast and independent of venue Wi-Fi); leave them empty to resolve through the Almanac. Run `python insurer_agent.py` alongside `fetch_agent.py` and the server.

An empty `FETCH_ALLOWED_SENDERS` permits the synthetic estate only. For private use, set a comma-separated allowlist of exact sender agent addresses and `ALLOW_PRIVATE_CLOUD=true`. The agent credential is limited to estate reads and questions; it cannot mutate family state, access raw proof or place calls. Remote API URLs must use HTTPS.

## External verification record

Record the date, account environment, and outcome of each check when accounts exist:

| Check | Expected evidence |
|---|---|
| Anthropic full pipeline | Printed LLM recall, estate output, manual checks of separate Chase accounts and Apple line items |
| Neon shared family state | A second client sees assignments and statuses; a fresh extraction preserves both |
| ElevenLabs placement | Real `conversation_id` and `callSid`; authorized recipient's phone rings |
| ElevenLabs completed cancellation | Company statement, reference number, and truthful remaining steps |
| Agentverse registration | Inspector/agent profile and successful Chat Protocol question with an email citation |

Live integrations remain unverified until these checks pass with the configured accounts.
