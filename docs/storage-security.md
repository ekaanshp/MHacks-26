# Private storage and database transport

Runtime inbox, estate, Family Hub, bank CSV, and phone call records use the shared
`secure_storage` module. On POSIX systems it creates directories with mode `0700`
and files with mode `0600`, independent of the process umask. Writes are staged in
the destination directory, flushed and synced before atomic publication. An
exclusive import refuses concurrent overwrites. Directory descriptors and
`O_NOFOLLOW` prevent symlink redirection; non-regular files, hard links, and files
owned by another user are rejected. This implementation fails closed on platforms
that cannot provide those protections; it does not substitute Windows mode bits
for a private ACL.

## Encryption configuration

Set `LASTLY_DATA_KEY` to a URL-safe base64 encoding of **32 random bytes**. Generate
a key once and keep it in a secret manager or a private, ignored local environment
file. Do not commit it, include it in screenshots, or store it alongside exported
data or backups. For local setup, generate a key with:

```bash
python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

When configured, all shared storage writes use AES-256-GCM with an independently
generated 12-byte nonce and an authentication tag. The version and destination
filename are authenticated associated data. Modified data, the wrong key, and a
file substituted under a different filename fail authentication. No decryption
failure falls back to plaintext. The JSON envelope exposes the encryption format
and encoded ciphertext length; it contains no account or email fields. The
construction follows the [cryptography authenticated encryption API](https://cryptography.io/en/stable/hazmat/primitives/aead/).

Synthetic demo fixtures can run without a key. In that mode, files are private
through filesystem permissions but **are not encrypted**. Private imports require
the key before their output is written. Plaintext reads remain available for
synthetic fixtures and explicit migration of existing local data. Adding a key
does not automatically encrypt existing files until they are rewritten through
`secure_storage.write_text` or `write_json`. Migrate runtime files before sharing
or deploying a private dataset; use the same filename so authenticated associated
data remains valid. Keep encrypted backups together with a separately secured key
backup. Losing the key makes encrypted data unrecoverable; rotating it requires
reading with the old key and writing with the new one.

Encryption protects stored data when the key is kept separate. The running
process necessarily decrypts data in memory. It does not protect a compromised
application account, privileged host access, originals of imported mail, or
earlier plaintext backups. Original Takeout archives and exported/downloaded
letters need the user's own secure storage and retention policy.

## Calls and replay protection

Phone calls reserve an idempotency key in the encrypted local call record before
the provider is contacted. A key is bound to a SHA-256 fingerprint of its request;
changing the request under the same key is rejected. A completed retry returns
the existing receipt. Pending or uncertain provider outcomes remain reserved,
including across restarts, and never expire automatically. A proven failure
before any call is placed may retry its original request. Reservations have a
fixed cap and cannot evict unresolved calls to make room for another call. The
same request under a new key is rejected while an equivalent call is unresolved
and for two minutes after it completes. New reservations also consume a durable
daily budget, defaulting to ten per UTC day. `LASTLY_CALL_DAILY_LIMIT` can explicitly
set a value from 1 through 100; uncertain outcomes continue to count because the
provider may have placed a call. Proven non-calls do not consume that budget. The
filesystem lock coordinates writers across processes sharing this local data
directory. Deployments across hosts need shared durable idempotency storage; the
local record is not a distributed database.

## Neon and PostgreSQL

Remote database connections force `sslmode=verify-full`, `sslrootcert` set to the
Mozilla CA bundle shipped by `certifi`, and TLS 1.2 or later. URL query parameters
cannot disable this verification. This authenticates the server certificate and
hostname; encryption without hostname verification is insufficient. The bundle is
used instead of `sslrootcert=system` because the `psycopg[binary]` wheel's libpq
cannot locate the macOS trust store, which made every verified Neon connection fail.
See the [PostgreSQL connection documentation](https://www.postgresql.org/docs/18/libpq-connect.html)
for the `verify-full` and `sslrootcert` behavior.

`LASTLY_ALLOW_INSECURE_DB=true` is an explicit local development exception. It is
accepted only when every configured host and address is loopback or an explicit
local Unix socket. Missing hosts and remote `hostaddr` overrides are rejected.
Leave this unset for Neon. Use a restricted database role and separate credentials
per environment. SQL values use bound parameters. Cloud upload of private estate
data still requires the app's explicit private cloud permission.
