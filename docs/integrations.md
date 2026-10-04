# Connect the accounts later

The app runs locally without any sponsor account. Start with the README's offline instructions. `.env` stores credentials locally and is ignored by Git. These adapters are implemented; their live operation needs accounts and a deliberate verification run. No phone call, Agentverse registration, account creation, or upload occurs simply by importing the code or running the offline demo.

## Anthropic

Set `ANTHROPIC_API_KEY`, set `ANTHROPIC_MODEL` to a model available to that account, and set `LASTLY_OFFLINE=false`. Run `python pipeline.py` to extract accounts using Anthropic. Review the printed recall and evidence before using that estate in a pitch. `python pipeline.py --mock` explicitly uses local rules; a successful offline recall score is not an LLM accuracy measurement.

The Q&A endpoint and letters reuse the same adapter. Every letter must be reviewed before sending. Real imported inbox data stays local by default. Sending it to a provider requires `ALLOW_PRIVATE_CLOUD=true`; configure an access token before sharing an instance. Account extraction, letter drafting, and calls remain separate requests.

## Neon Postgres / Family Hub

1. Create a Neon project later and copy its Postgres connection string to `DATABASE_URL`. Certificate and hostname verification are enforced; use `sslmode=verify-full&sslrootcert=system`. See [storage transport](storage-security.md).
2. Install `requirements.txt`. The Postgres driver is included.
3. Initialize the schema using the supplied `schema.sql` or the application database initializer. If using the SQL editor, paste the whole schema there.
4. Start with synthetic data, run the pipeline, and open the app on two computers connected to the same backend or database. Assign Spotify to Sarah and mark Netflix Done on one; refresh the other and confirm both changes.
5. Re-run the pipeline, refresh, and confirm those edits survive. Store connection strings privately.

With no `DATABASE_URL`, the Family Hub uses the local JSON store. Sharing that store across separate computers requires the hosted backend or Neon; independent local stores are independent estates. During a configured Neon outage, the adapter uses its durable local mirror and records pending family edits for replay when connectivity returns. Other clients see those offline changes after the backend reconnects and synchronizes; the terminal reports that Neon is unavailable.

## ElevenLabs + Twilio

The implementation uses [ElevenLabs outbound Twilio calls](https://elevenlabs.io/docs/api-reference/integrations/twilio/outbound-call) and [conversation details](https://elevenlabs.io/docs/api-reference/conversations/get).

1. Create the ElevenLabs and Twilio accounts. Obtain a Twilio number and configure any trial recipient restrictions using the account's current console instructions.
2. In ElevenLabs, create a conversational agent. Copy `AGENT_PROMPT` and `FIRST_MESSAGE` from `calls.py` into its system prompt and first message. Choose a calm, clear voice. Keep the explicit AI disclosure.
3. Register these dynamic variables in the agent: `person_name`, `date_of_death`, `institution`, `action`, `category`, `executor_name`. Values come from the selected account and its estate; they are not hard-coded to Margaret.
4. Import the Twilio number into ElevenLabs, supply the Twilio credentials there, and assign the agent to that number. The app itself does not need Twilio secrets.
5. Set `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `FAMILY_EXECUTOR`, and `LASTLY_OFFLINE=false`. Configure a random family access code and the exact authorized recipients in `LASTLY_ALLOWED_CALL_NUMBERS` following [the security guide](../SECURITY.md). Restart after editing `.env`.
6. Open Planet Fitness in the dashboard, enter a teammate's authorized phone number in international E.164 format, and explicitly place the call. A successful placement requires real provider IDs; no offline response pretends a call occurred.
7. Rehearse [the role-play](roleplay.md). Verify the AI disclosure and correct name/date. Confirm successful calls before staging the demo; the same account/number has a two-minute cooldown and calls are budgeted. Never repeat an uncertain call before checking ElevenLabs.

The dashboard polls the transcript while a call is in progress. A completed phone conversation does not establish completed cancellation. Automatic completion requires a clear company statement that cancellation is complete, with no outstanding condition. A requirement to supply a death certificate first keeps the account open or in progress. The reference number is taken from company speech. Call metadata is saved locally in ignored `data/runtime_calls.json` and tied to its estate; keep that file if you need to resume polling after a restart. This local prototype is intended to run as one backend process.

The local transcript parser works without Anthropic. Optional Anthropic summary extraction additionally requires `ALLOW_PRIVATE_CLOUD=true`, so enabling voice calls does not silently authorize sending their transcripts to another provider.

## Fetch.ai / Agentverse / ASI:One

The bridge implements the [Fetch.ai Chat Protocol](https://uagents.fetch.ai/docs/examples/asi-1). It forwards questions to the existing Lastly API and returns that API's evidence IDs. It does not run a separate LLM and does not need an ASI model API key to answer through its own handler.

1. Ask the sponsor what the current prize requires. This code cannot establish event eligibility or register an account for you.
2. Use Python 3.12 for the optional agent environment. [The uAgents package guidance](https://pypi.org/project/uagents/) lists Python 3.10–3.13; this app requires 3.11 or newer. The pinned SDK pair is verified on Python 3.12 alongside all base requirements. If your main app runs Python 3.14, keep its environment and create a separate agent environment:

   ```bash
   python3.12 -m venv .venv-agent
   .venv-agent/bin/python -m pip install -r requirements.txt -r requirements-agent.txt
   ```

   On Windows, use `py -3.12 -m venv .venv-agent` and `.venv-agent\Scripts\python.exe` for the subsequent commands. Keep the agent environment outside version control.
3. Create a long random phrase for `AGENT_SEED` and keep it fixed and secret. Set a separate random `LASTLY_AGENT_TOKEN` in the backend and bridge environments; the bridge refuses to start without it. Give a deployed bridge only this credential, never the family access code. Use `AGENT_MAILBOX=true`, `AGENT_PORT=8001`, and `LASTLY_API_BASE_URL=http://127.0.0.1:8000` for local use.
4. Start the Lastly server, load the synthetic demo estate, then run `.venv-agent/bin/python fetch_agent.py` (or the Windows equivalent). The uAgents runtime prints the Agent Inspector URL. Open it and connect Mailbox in Agentverse; follow the account's registration flow.
5. Name the profile **Lastly Estate Assistant**. Description: **Answers questions about estate accounts discovered from email and bank statements, with evidence citations. The public demo uses synthetic data.**
6. Example questions: “Did Margaret have life insurance?”, “Which accounts are still charging?”, “What renews in the next 14 days?”, “Which subscriptions were found only on a bank statement?”
7. Verify the MetLife answer cites an actual `msg_` ID from the estate. Keep the server and agent running when judges test it.

An empty `FETCH_ALLOWED_SENDERS` permits the synthetic estate only. For private use, set a comma-separated allowlist of exact sender agent addresses and `ALLOW_PRIVATE_CLOUD=true`. Both bridge and backend enforce the restriction after authenticating the dedicated service token. Its permissions are limited to estate reads and questions; it cannot mutate family state, access raw proof or place calls. Remote API URLs must use HTTPS. Real family data should never be made available to a public demo agent.

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
