# Security and deployment

Lastly now has explicit security boundaries for its **single-family, single-backend-process** architecture. These controls are tested, but this is not a certification or a guarantee that the application has no vulnerabilities.

## Credentials and private data

The unconfigured offline sample is available only through loopback connections and approved loopback hostnames. Real imports require both `LASTLY_ACCESS_TOKEN` and `LASTLY_DATA_KEY`; startup refuses unsafe private or production configuration. Live Anthropic and ElevenLabs configuration also requires an access code. The family code must be a random secret of at least 32 characters, not a memorable password.

Generate separate values locally using Python:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
python -c 'import secrets; print(secrets.token_urlsafe(32))'
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

Use the first value for `LASTLY_ACCESS_TOKEN`, the second for `LASTLY_AGENT_TOKEN` if using Fetch.ai, and the third for `LASTLY_DATA_KEY`. Keep credentials in a secret manager or an owner-only `.env` (`chmod 600 .env`); never put them in Git, URLs, screenshots or application logs. Give a separately deployed agent only its agent credential, not the family code or encryption key.

The browser exchanges the code for a random opaque session cookie. It is `HttpOnly`, `SameSite=Strict`, and `Secure` with the `__Host-` prefix over HTTPS. The family code is never saved in localStorage/sessionStorage. Session identifiers are hashed in server memory, expire after 30 minutes of inactivity or 8 hours total, rotate on login, and revoke on **Lock estate**. Changing the access code or connected owner's identity invalidates sessions. Restarting the backend revokes all sessions. CSRF tokens stay in browser memory and are checked on every cookie-authenticated mutation. Other open tabs can recover the token through the authenticated session endpoint.

An access code grants access to the entire current family estate. This is not personal identity, role-based authorization or multi-family isolation; activity labels are not verified individual identities. Before a public launch, integrate a trusted identity provider with MFA, per-family authorization and a durable session/rate-limit service. Do not run multiple backend workers with the current in-memory sessions.

## Files and database

Runtime inboxes, statements, estate caches, family edits and call records use atomic owner-only files (`0600`) in owner-only data directories (`0700`). Symlinks, hard links, nonregular files and files owned by another user are refused. With `LASTLY_DATA_KEY`, files use AES-256-GCM with a fresh 96-bit nonce and authenticated format/filename; corruption, tampering and wrong keys fail closed. Startup migrates existing runtime files to encrypted storage when a key is configured. The Takeout CLI requires encryption before importing private mail. Synthetic fixtures without a key stay plaintext.

Keep the encryption key separately from data backups. Preserve a secure recovery copy: losing the key makes encrypted files unreadable. Do not change the key while the server is running. Rotate it by stopping the backend, decrypting under the old key and rewriting each runtime file under the new key using the secure storage helpers, verifying the result, then restarting. Existing source mbox files and copies made outside Lastly are not encrypted by this application. Full-disk encryption and encrypted backups still matter.

`manage.py export` produces an encrypted private backup and refuses to overwrite files. Use `manage.py persona` to update an encrypted inbox's name/date and `manage.py import-bank` to copy a statement into encrypted storage; do not edit encryption envelopes by hand. See [storage details](docs/storage-security.md).

Remote PostgreSQL connections enforce `sslmode=verify-full` against the Mozilla CA bundle (`certifi`), checking the server certificate and hostname. A developer can explicitly permit an insecure **loopback-only** database with `LASTLY_ALLOW_INSECURE_DB=true`; it never permits plaintext remote connections. SQL values are parameterized. Neon stores decrypted estate JSON in its managed database, so the local encryption key does not provide end-to-end encryption of cloud records. Use a restricted application database role, keep its credentials private, and configure backups/access controls in Neon when that account is created.

## HTTP and browser protections

- Exact host allowlists block DNS rebinding and malformed Host headers. Authorization uses the ASGI request path, independent of Host parsing. Duplicate security-sensitive headers and ambiguous framing are rejected.
- Origin and Fetch Metadata checks reject cross-site requests. Mutations require `X-Requested-With: Lastly`; cookie sessions also require their CSRF token. No permissive CORS policy is installed.
- The CSP permits local scripts/styles only, blocks inline scripts/styles, objects and framing. Additional headers prevent MIME sniffing, cross-origin resource access and referrer leakage; API responses and pages are not cached. HTTPS responses include HSTS.
- API bodies are limited to 64 KiB including chunked requests, with a 15-second read deadline. JSON mutations, strict request models, finite monetary values and ASCII E.164 phone numbers are validated. Validation errors do not echo submitted private values. Mailbox, message, MIME and bank-row processing also have limits.
- Login attempts are limited to 5/minute per connection address. Authenticated API traffic is limited to 180/minute; local public sample traffic 600/minute. Expensive operations have shared estate limits: analysis 4/minute, questions 24/minute, letters 12/minute and call submissions 3/minute. Limits and session counts are bounded in memory. Apply additional connection/IP limits at the deployment edge.
- Unexpected errors return a generic message with a request ID; security logs record status/type and request ID rather than request bodies, credentials or provider errors.

For hosted use set `LASTLY_PRODUCTION=true`, an exact `LASTLY_ALLOWED_HOSTS`, and `LASTLY_ALLOWED_ORIGINS=https://your-domain`. Both security secrets are mandatory. Remote/production API access over HTTP is rejected. Terminate TLS through a configured trusted proxy or run Uvicorn with TLS. If using a proxy, restrict forwarded-header trust to that proxy's exact addresses and prevent direct access to the backend. Never use unrestricted `--forwarded-allow-ips='*'`. Use one worker, disable reload and server headers, and apply the identity-provider boundary described above.

## External integrations

The Fetch.ai bridge has a separate random credential limited to estate reads, questions and relaying agent-to-agent insurance claims. It can fetch queued claim requests (name, date of death, institution, claimant) and report an insurer agent's answer; only the configured `CLAIMS_AGENT_ADDRESS` can open or reject a claim, and resolved claims cannot change. It cannot start claims, edit accounts, obtain raw email/bank proof, generate letters or place calls. Sender/mode headers are accepted only after this credential authenticates. Private access additionally requires `ALLOW_PRIVATE_CLOUD=true` and an exact `FETCH_ALLOWED_SENDERS` allowlist in both bridge and backend. The public agent can answer only about synthetic data.

Real mailbox content remains local unless `ALLOW_PRIVATE_CLOUD=true` explicitly authorizes sharing with configured providers. Q&A sends selected accounts and proof rather than the full estate/assignments to Anthropic. Email, questions, transcripts and provider responses are treated as untrusted data; rendered text is escaped, citations are filtered to actual evidence, and generated text never invokes tools or places calls. Prompt injection cannot be completely eliminated, so evidence and draft actions still require human review.

Outbound calls require an authenticated family session, an exact destination in `LASTLY_ALLOWED_CALL_NUMBERS`, and a UUID `Idempotency-Key`. A durable reservation is saved before the provider is contacted. Repeated completed requests return their receipt; conflicting keys/payloads are rejected. Pending or uncertain outcomes cannot be retried with a new key. Confirmed calls to the same account/number have a 120-second cooldown. The durable daily call budget defaults to 10/UTC day (`LASTLY_CALL_DAILY_LIMIT`, 1–100). An ambiguous timeout stays reserved: check ElevenLabs and resolve the record before considering another call. No browser call is retried automatically after reauthentication or a network failure.

Provider endpoints are fixed; HTTP clients verify TLS, refuse redirects and ignore inherited proxy variables. Provider accounts/keys and live delivery remain unconfigured until you set them up.

## Verification and reporting

`tests/test_security.py`, `tests/test_secure_storage.py`, integration tests and the browser smoke suite exercise authentication, CSRF, cross-site requests, malformed headers, bounded bodies, scoped agents, encrypted storage/tampering, verified database transport and call replay/budget controls. Requirements include explicit security minimums for Starlette and h11; use the tested `requirements.lock` for reproducible application dependencies. Re-run a dependency advisory scan and these tests when updating packages. An advisory scan cannot detect every vulnerability or substitute for an independent security review.

Report suspected security issues privately to the repository owner. Do not include real mailbox contents, credentials or encryption keys in public issues. Rotate exposed credentials, revoke sessions by restarting, and inspect provider call records before retrying uncertain operations.
