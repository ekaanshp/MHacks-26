"""Adversarial HTTP and call-safety regressions; no external service is contacted."""
from __future__ import annotations

import asyncio
import base64
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import calls
import llm
import pipeline
import secure_storage
import security
import server


def isolated_app():
    # Reuse the actual endpoints, while keeping sessions/rate buckets isolated.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.router.routes = list(server.app.router.routes)
    app.exception_handlers = dict(server.app.exception_handlers)
    app.add_middleware(security.SecurityMiddleware)
    return app


@pytest.fixture
def security_environment(dataset, monkeypatch):
    for name in (
        "LASTLY_ACCESS_TOKEN", "LASTLY_AGENT_TOKEN", "LASTLY_DATA_KEY",
        "LASTLY_PRODUCTION", "LASTLY_ALLOWED_HOSTS", "LASTLY_ALLOWED_ORIGINS",
        "LASTLY_ALLOWED_CALL_NUMBERS", "FETCH_ALLOWED_SENDERS",
        "LASTLY_CALL_DAILY_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "enabled", lambda: False)
    return dataset


@pytest.fixture
def http_client(security_environment, estate_data):
    with TestClient(isolated_app(), base_url="http://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        yield client


@pytest.fixture
def secured(http_client, monkeypatch):
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    return http_client, token


def login(client, token):
    response = client.post("/api/session", json={"access_code": token})
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"] is True
    return response.json()["csrf_token"]


def middleware(client):
    current = client.app.middleware_stack
    while current is not None:
        if isinstance(current, security.SecurityMiddleware):
            return current
        current = getattr(current, "app", None)
    raise AssertionError("The real security middleware is missing")


def change_owner(directory, email="another-family@example.com", *, private=False):
    path = directory / "inbox.json"
    inbox = secure_storage.read_json(path)
    inbox["persona"]["email"] = email
    inbox["synthetic"] = not private
    secure_storage.write_json(path, inbox)


@pytest.mark.parametrize("has_token,has_key", [(False, False), (True, False), (False, True)])
def test_private_data_requires_both_authentication_and_encryption(http_client, security_environment, monkeypatch, has_token, has_key):
    change_owner(security_environment, private=True)
    token = secrets.token_urlsafe(32)
    if has_token:
        monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    if has_key:
        monkeypatch.setenv("LASTLY_DATA_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    headers = {"Authorization": f"Bearer {token}"} if has_token else {}
    for method, path, body in (
        ("GET", "/api/estate", None), ("GET", "/api/email/msg_0000", None),
        ("GET", "/api/bank/bank_0000", None), ("GET", "/api/activity", None),
        ("POST", "/api/ask", {"question": "Life insurance?"}),
        ("PATCH", "/api/account/acct_00", {"status": "done"}),
        ("POST", "/api/analyze", None), ("GET", "/api/session", None),
    ):
        response = http_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 503
        assert "another-family@example.com" not in response.text


def test_valid_private_configuration_still_requires_a_credential(secured, security_environment):
    client, token = secured
    change_owner(security_environment, private=True)
    assert client.get("/api/estate").status_code == 401
    login(client, token)
    # Analyze the newly connected owner only after authentication.
    csrf = client.get("/api/session").json()["csrf_token"]
    assert client.post("/api/analyze?mock=1", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/estate").json()["persona"]["email"] == "another-family@example.com"


@pytest.mark.parametrize("unsafe", [None, [], "not an inbox", {"synthetic": True, "persona": None}])
def test_malformed_source_is_never_a_public_demo(http_client, security_environment, unsafe):
    secure_storage.write_json(security_environment / "inbox.json", unsafe)
    response = http_client.get("/api/estate")
    assert response.status_code == 503


def test_missing_source_is_never_a_public_demo(http_client, security_environment):
    # Point at a missing input without deleting an existing artifact.
    path = security_environment / "inbox.json"
    path.rename(security_environment / "disconnected-inbox.json")
    assert http_client.get("/api/estate").status_code == 503


@pytest.mark.parametrize("name,value", [
    ("LASTLY_ACCESS_TOKEN", "short"), ("LASTLY_ACCESS_TOKEN", "x" * 31),
    ("LASTLY_ACCESS_TOKEN", "x" * 32 + " "), ("LASTLY_ACCESS_TOKEN", "é" * 40),
    ("LASTLY_AGENT_TOKEN", "short"), ("LASTLY_DATA_KEY", "bad-key"),
])
def test_invalid_security_configuration_fails_closed(http_client, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert http_client.get("/api/estate").status_code == 503


def test_family_and_agent_credentials_must_differ(secured, monkeypatch):
    client, token = secured
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", token)
    assert client.get("/api/estate").status_code == 503


def test_private_startup_refuses_missing_configuration(security_environment):
    change_owner(security_environment, private=True)
    with pytest.raises(RuntimeError, match="startup refused"):
        with TestClient(server.app, base_url="http://localhost"):
            pytest.fail("Private startup succeeded without protection")


def test_session_cookie_is_opaque_http_only_and_not_the_access_code(secured):
    client, token = secured
    response = client.post("/api/session", json={"access_code": token})
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert token not in cookie and token not in response.text
    assert client.cookies.get("lastly_session") != token
    assert client.get("/api/estate").status_code == 200
    assert client.get("/api/session").json()["authenticated"] is True


def test_https_session_uses_host_prefix_and_secure_cookie(secured):
    _, token = secured
    with TestClient(isolated_app(), base_url="https://localhost", headers={"X-Requested-With": "Lastly"}) as client:
        response = client.post("/api/session", json={"access_code": token})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-lastly_session=") and "Secure" in cookie
        assert "Domain=" not in cookie
        assert client.get("/api/estate").status_code == 200


def test_cookie_mutations_require_the_current_csrf_secret(secured):
    client, token = secured
    csrf = login(client, token)
    assert client.post("/api/ask", json={"question": "Life insurance?"}).status_code == 403
    assert client.post("/api/ask", json={"question": "Life insurance?"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    response = client.post("/api/ask", json={"question": "Life insurance?"}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200


def test_session_rotation_invalidates_previous_cookie_and_csrf(secured):
    client, token = secured
    previous_csrf = login(client, token)
    previous_cookie = client.cookies.get("lastly_session")
    current_csrf = login(client, token)
    assert current_csrf != previous_csrf and client.cookies.get("lastly_session") != previous_cookie
    assert client.post("/api/ask", json={"question": "insurance"}, headers={"X-CSRF-Token": previous_csrf}).status_code == 403
    assert client.get("/api/estate", headers={"Cookie": f"lastly_session={previous_cookie}"}).status_code == 401


def test_tampered_cookie_cannot_open_the_estate(secured):
    client, token = secured
    login(client, token)
    cookie = client.cookies.get("lastly_session")
    tampered = ("a" if cookie[0] != "a" else "b") + cookie[1:]
    assert client.get("/api/estate", headers={"Cookie": f"lastly_session={tampered}"}).status_code == 401


@pytest.mark.parametrize("timestamp,age", [("last", security.SESSION_IDLE + 1), ("created", security.SESSION_LIFETIME + 1)])
def test_session_idle_and_absolute_expiration(secured, timestamp, age):
    client, token = secured
    login(client, token)
    record = next(iter(middleware(client).sessions.values()))
    record[timestamp] = time.monotonic() - age
    assert client.get("/api/estate").status_code == 401
    assert client.get("/api/session").json()["authenticated"] is False


def test_logout_requires_csrf_and_revokes_cookie(secured):
    client, token = secured
    csrf = login(client, token)
    cookie = client.cookies.get("lastly_session")
    assert client.delete("/api/session").status_code == 403
    response = client.delete("/api/session", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200 and response.json()["authenticated"] is False
    assert client.get("/api/estate").status_code == 401
    assert client.get("/api/estate", headers={"Cookie": f"lastly_session={cookie}"}).status_code == 401


def test_access_code_rotation_revokes_existing_sessions(secured, monkeypatch):
    client, token = secured
    login(client, token)
    new_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", new_token)
    assert client.get("/api/estate").status_code == 401
    assert client.get("/api/estate", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    login(client, new_token)
    assert client.get("/api/estate").status_code == 200


def test_importing_a_different_owner_revokes_existing_sessions(secured, security_environment):
    client, token = secured
    login(client, token)
    change_owner(security_environment)
    assert client.get("/api/estate").status_code == 401


def test_switching_data_directory_revokes_existing_sessions(secured, security_environment, monkeypatch, tmp_path):
    client, token = secured
    login(client, token)
    alternate = tmp_path / "other-family"
    secure_storage.write_json(alternate / "inbox.json", secure_storage.read_json(security_environment / "inbox.json"))
    monkeypatch.setenv("LASTLY_DATA_DIR", str(alternate))
    assert client.get("/api/estate").status_code == 401


def test_login_failures_are_throttled_without_echoing_secrets(secured):
    client, token = secured
    for _ in range(5):
        response = client.post("/api/session", json={"access_code": "bad-credential"})
        assert response.status_code == 401
        assert token not in response.text and "bad-credential" not in response.text
    response = client.post("/api/session", json={"access_code": token})
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


@pytest.mark.parametrize("host", ["attacker.example", "localhost/innocent?x=", "localhost#fragment", "localhost@attacker.example", "localhost ", ""])
def test_host_allowlist_and_malformed_host_prevent_bypass(http_client, host):
    response = http_client.get("/api/estate", headers={"Host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("header,value", [("Origin", "https://evil.example"), ("Origin", "null"), ("Sec-Fetch-Site", "cross-site")])
def test_cross_site_requests_are_rejected_even_with_valid_authentication(secured, header, value):
    client, token = secured
    response = client.post("/api/ask", json={"question": "insurance"}, headers={"Authorization": f"Bearer {token}", header: value})
    assert response.status_code == 403


def test_same_origin_request_is_allowed(secured):
    client, token = secured
    response = client.post("/api/ask", json={"question": "insurance"}, headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost"})
    assert response.status_code == 200


@pytest.mark.parametrize("name,value", [
    ("Host", "localhost"), ("Authorization", "Bearer invalid"),
    ("Cookie", "lastly_session=invalid"), ("Origin", "http://localhost"),
    ("Content-Length", "2"), ("X-CSRF-Token", "invalid"),
])
def test_duplicate_sensitive_headers_are_rejected(http_client, name, value):
    response = http_client.get("/api/estate", headers=[(name, value), (name, value)])
    assert response.status_code == 400


@pytest.mark.parametrize("authorization", ["Basic abc", "Bearer ", "Bearer invalid", "Bearer invalid,Bearer other", "bearer invalid"])
def test_invalid_or_ambiguous_authorization_is_rejected(secured, authorization):
    client, _ = secured
    assert client.get("/api/estate", headers={"Authorization": authorization}).status_code == 401


def test_remote_transport_requires_https_and_authentication(security_environment, estate_data, monkeypatch):
    for scheme in ("http", "https"):
        with TestClient(isolated_app(), base_url=f"{scheme}://localhost", client=("198.51.100.12", 49152)) as client:
            assert client.get("/api/estate").status_code == 403
    token = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_ACCESS_TOKEN", token)
    with TestClient(isolated_app(), base_url="http://localhost", client=("198.51.100.12", 49152)) as client:
        response = client.get("/api/estate", headers={"Authorization": f"Bearer {token}", "X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"})
        assert response.status_code == 403
    with TestClient(isolated_app(), base_url="https://localhost", client=("198.51.100.12", 49152)) as client:
        assert client.get("/api/estate", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_production_requires_explicit_https_configuration(secured, monkeypatch):
    client, token = secured
    monkeypatch.setenv("LASTLY_PRODUCTION", "true")
    assert client.get("/api/estate").status_code == 503
    monkeypatch.setenv("LASTLY_ALLOWED_HOSTS", "localhost")
    monkeypatch.setenv("LASTLY_ALLOWED_ORIGINS", "http://localhost")
    assert client.get("/api/estate").status_code == 503
    monkeypatch.setenv("LASTLY_ALLOWED_ORIGINS", "https://localhost")
    assert client.get("/api/estate", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    with TestClient(isolated_app(), base_url="https://localhost") as https_client:
        response = https_client.get("/api/estate", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_mutations_require_the_client_header(http_client):
    client = TestClient(isolated_app(), base_url="http://localhost")
    try:
        assert client.post("/api/ask", json={"question": "insurance"}).status_code == 403
        assert client.patch("/api/account/acct_00", json={"status": "done"}).status_code == 403
    finally:
        client.close()


def test_json_body_and_length_are_bounded(http_client):
    assert http_client.post("/api/ask", content="x" * (security.BODY_LIMIT + 1), headers={"Content-Type": "application/json"}).status_code == 413
    assert http_client.post("/api/ask", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert http_client.post("/api/ask", content="{}", headers={"Content-Length": "-1", "Content-Type": "application/json"}).status_code == 400
    assert http_client.post("/api/ask", content="{}", headers={"Content-Length": "2", "Transfer-Encoding": "chunked", "Content-Type": "application/json"}).status_code == 400


def test_streamed_body_without_content_length_is_bounded(security_environment):
    messages = iter([
        {"type": "http.request", "body": b"x" * (security.BODY_LIMIT - 1), "more_body": True},
        {"type": "http.request", "body": b"xxxx", "more_body": False},
    ])
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    async def never_called(scope, receive, send):
        pytest.fail("An oversized streamed body reached the application")

    scope = {"type": "http", "path": "/api/ask", "method": "POST", "scheme": "http", "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"localhost"), (b"x-requested-with", b"Lastly"), (b"content-type", b"application/json")]}
    asyncio.run(security.SecurityMiddleware(never_called)(scope, receive, send))
    assert next(item for item in sent if item["type"] == "http.response.start")["status"] == 413


def test_streaming_request_cannot_outlive_public_to_private_transition(security_environment):
    sent = []

    async def receive():
        # A Takeout import completes while this public request is waiting for bytes.
        change_owner(security_environment, private=True)
        return {"type": "http.request", "body": b'{"question":"insurance"}', "more_body": False}

    async def send(message):
        sent.append(message)

    async def private_endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'"private data"'})

    scope = {"type": "http", "path": "/api/ask", "method": "POST", "scheme": "http", "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"localhost"), (b"x-requested-with", b"Lastly"), (b"content-type", b"application/json")]}
    asyncio.run(security.SecurityMiddleware(private_endpoint)(scope, receive, send))
    assert next(item for item in sent if item["type"] == "http.response.start")["status"] == 503


def test_streaming_request_rechecks_the_owner_bound_to_its_session(secured, security_environment, monkeypatch):
    client, token = secured
    csrf = login(client, token)
    boundary = middleware(client)
    sent = []

    async def receive():
        change_owner(security_environment, private=True)
        return {"type": "http.request", "body": b'{"question":"insurance"}', "more_body": False}

    async def send(message):
        sent.append(message)

    async def private_endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'"another family"'})

    monkeypatch.setattr(boundary, "app", private_endpoint)
    cookie = f"lastly_session={client.cookies.get('lastly_session')}".encode()
    scope = {"type": "http", "path": "/api/ask", "method": "POST", "scheme": "http", "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"localhost"), (b"cookie", cookie), (b"x-csrf-token", csrf.encode()),
                         (b"x-requested-with", b"Lastly"), (b"content-type", b"application/json")]}
    asyncio.run(boundary(scope, receive, send))
    assert next(item for item in sent if item["type"] == "http.response.start")["status"] == 401


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/api/estate", "/api/email/msg_missing"])
def test_success_and_failure_responses_have_security_headers(http_client, path):
    response = http_client.get(path)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-eval" not in response.headers["content-security-policy"]
    assert len(response.headers["x-request-id"]) == 32


def test_validation_and_internal_failures_do_not_expose_secrets(secured, monkeypatch):
    client, token = secured
    client.headers["Authorization"] = f"Bearer {token}"
    response = client.post("/api/ask", json={"question": token, "unsupported": token})
    assert response.status_code == 422 and token not in response.text

    def secret_failure():
        raise RuntimeError(f"credential {token}")

    monkeypatch.setattr(server, "get_estate", secret_failure)
    response = client.get("/api/estate")
    assert response.status_code == 500 and token not in response.text


def test_expensive_operations_are_rate_limited(secured):
    client, token = secured
    client.headers["Authorization"] = f"Bearer {token}"
    for _ in range(4):
        assert client.post("/api/analyze").status_code == 200
    response = client.post("/api/analyze")
    assert response.status_code == 429 and response.headers["retry-after"] == "60"


@pytest.fixture
def agent_access(secured, monkeypatch):
    client, family = secured
    agent = secrets.token_urlsafe(32)
    monkeypatch.setenv("LASTLY_AGENT_TOKEN", agent)
    return client, family, agent


def test_agent_credential_only_has_explicit_read_and_question_permissions(agent_access):
    client, _, agent = agent_access
    headers = {"Authorization": f"Bearer {agent}", "X-Lastly-Agent-Mode": "synthetic", "X-Lastly-Agent-Sender": "signed-sender"}
    assert client.get("/api/estate", headers=headers).status_code == 200
    assert client.post("/api/ask", json={"question": "insurance"}, headers=headers).status_code == 200
    for method, path, body in (
        ("GET", "/api/email/msg_0000", None), ("GET", "/api/bank/bank_0000", None),
        ("GET", "/api/activity", None), ("GET", "/api/health", None),
        ("PATCH", "/api/account/acct_00", {"status": "done"}),
        ("POST", "/api/letter/acct_00", None), ("POST", "/api/analyze", None),
        ("POST", "/api/call/acct_00", {"to_number": "+17345551234"}),
        ("GET", "/api/call/conv_any", None), ("POST", "/api/session", {"access_code": "not-the-code"}),
        ("POST", "/api/estate", None), ("GET", "/api/ask", None),
    ):
        assert client.request(method, path, json=body, headers=headers).status_code == 403


def test_family_or_anonymous_clients_cannot_spoof_agent_identity(agent_access):
    client, family, _ = agent_access
    for credential in (None, family):
        headers = {"X-Lastly-Agent-Mode": "authorized_private", "X-Lastly-Agent-Sender": "allowed-sender"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        assert client.get("/api/estate", headers=headers).status_code == 403


def test_private_agent_requires_both_cloud_consent_and_sender_allowlist(agent_access, security_environment, monkeypatch):
    client, _, agent = agent_access
    change_owner(security_environment, private=True)
    pipeline.run(mock=True, data_dir=security_environment)
    headers = {"Authorization": f"Bearer {agent}", "X-Lastly-Agent-Mode": "authorized_private", "X-Lastly-Agent-Sender": "allowed-sender"}
    assert client.get("/api/estate", headers=headers).status_code == 403
    monkeypatch.setenv("FETCH_ALLOWED_SENDERS", "allowed-sender")
    assert client.get("/api/estate", headers=headers).status_code == 403
    monkeypatch.setenv("ALLOW_PRIVATE_CLOUD", "true")
    assert client.get("/api/estate", headers=headers).status_code == 200
    assert client.post("/api/ask", headers=headers, json={"question": "insurance"}).status_code == 200
    headers["X-Lastly-Agent-Sender"] = "unapproved-sender"
    assert client.get("/api/estate", headers=headers).status_code == 403
    headers["X-Lastly-Agent-Mode"] = "synthetic"
    assert client.get("/api/estate", headers=headers).status_code == 403


@pytest.fixture
def outbound(secured, estate_data, monkeypatch):
    client, token = secured
    client.headers["Authorization"] = f"Bearer {token}"
    monkeypatch.setenv("LASTLY_OFFLINE", "false")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-provider-key")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "test-provider-agent")
    monkeypatch.setenv("ELEVENLABS_PHONE_NUMBER_ID", "test-provider-phone")
    monkeypatch.setenv("LASTLY_ALLOWED_CALL_NUMBERS", "+17345551234,+17345551235,+17345551236")
    account = next(account for account in estate_data["accounts"] if account["institution"] == "Planet Fitness")
    requests = []

    def provider(method, path, settings, *, payload=None):
        requests.append((method, path, payload))
        return {"success": True, "conversation_id": f"conv_security_{len(requests)}", "callSid": f"CA_security_{len(requests)}"}

    monkeypatch.setattr(calls, "_request", provider)
    return client, f"/api/call/{account['id']}", requests


def test_call_rejects_unapproved_destination_before_provider_io(outbound):
    client, path, requests = outbound
    response = client.post(path, json={"to_number": "+19005551234"}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 403 and requests == []


def test_live_call_always_requires_family_authentication(outbound):
    client, path, requests = outbound
    del client.headers["Authorization"]
    assert client.post(path, json={"to_number": "+17345551234"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 401
    assert requests == []


@pytest.mark.parametrize("key", ["", "not-a-uuid", "x" * 1024])
def test_call_requires_a_valid_idempotency_key_before_provider_io(outbound, key):
    client, path, requests = outbound
    response = client.post(path, json={"to_number": "+17345551234"}, headers={"Idempotency-Key": key})
    assert response.status_code in {400, 409} and requests == []


def test_call_replay_returns_receipt_without_placing_another_call(outbound):
    client, path, requests = outbound
    headers = {"Idempotency-Key": str(uuid4())}
    first = client.post(path, json={"to_number": "+17345551234"}, headers=headers)
    replay = client.post(path, json={"to_number": "+17345551234"}, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() and len(requests) == 1


def test_call_key_cannot_be_rebound_to_another_destination(outbound):
    client, path, requests = outbound
    headers = {"Idempotency-Key": str(uuid4())}
    assert client.post(path, json={"to_number": "+17345551234"}, headers=headers).status_code == 200
    assert client.post(path, json={"to_number": "+17345551235"}, headers=headers).status_code == 409
    assert len(requests) == 1


def test_concurrent_replay_places_exactly_one_call(outbound, monkeypatch):
    client, path, requests = outbound
    entered, release = threading.Event(), threading.Event()
    provider = calls._request

    def slow_provider(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Concurrent call test never released its provider stub"
        return provider(*args, **kwargs)

    monkeypatch.setattr(calls, "_request", slow_provider)
    headers = {"Idempotency-Key": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.post, path, json={"to_number": "+17345551234"}, headers=headers)
        try:
            assert entered.wait(5), "The first call never reached its provider stub"
            second = executor.submit(client.post, path, json={"to_number": "+17345551234"}, headers=headers)
            assert second.result(timeout=5).status_code == 409
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert len(requests) == 1


def test_uncertain_provider_outcome_blocks_replay_durably(outbound, monkeypatch, security_environment):
    client, path, requests = outbound
    key = str(uuid4())

    def uncertain_provider(method, path, settings, *, payload=None):
        requests.append((method, path, payload))
        raise calls.IntegrationError("The provider timed out after receiving the call request.")

    monkeypatch.setattr(calls, "_request", uncertain_provider)
    headers = {"Idempotency-Key": key}
    assert client.post(path, json={"to_number": "+17345551234"}, headers=headers).status_code == 502
    records = secure_storage.read_json(security_environment / "runtime_calls.json")
    assert records[calls._RESERVATIONS][key]["state"] == "uncertain"
    assert client.post(path, json={"to_number": "+17345551234"}, headers=headers).status_code == 409
    assert client.post(path, json={"to_number": "+17345551234"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 409
    assert len(requests) == 1


def test_calls_have_a_shared_budget_even_when_idempotency_keys_change(outbound):
    client, path, requests = outbound
    for number in ("+17345551234", "+17345551235", "+17345551236"):
        assert client.post(path, json={"to_number": number}, headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    assert client.post(path, json={"to_number": "+17345551234"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 429
    assert len(requests) == 3


def test_calls_respect_a_durable_daily_budget(outbound, monkeypatch):
    client, path, requests = outbound
    monkeypatch.setenv("LASTLY_CALL_DAILY_LIMIT", "2")
    for number in ("+17345551234", "+17345551235"):
        assert client.post(path, json={"to_number": number}, headers={"Idempotency-Key": str(uuid4())}).status_code == 200
    assert client.post(path, json={"to_number": "+17345551236"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 429
    assert len(requests) == 2


def test_unicode_phone_digits_are_rejected_before_provider_io(outbound):
    client, path, requests = outbound
    assert client.post(path, json={"to_number": "+1٧٣٤٥٥٥١٢٣٤"}, headers={"Idempotency-Key": str(uuid4())}).status_code == 422
    assert requests == []


def test_malformed_model_citations_fall_back_to_evidence(secured, monkeypatch):
    client, token = secured
    client.headers["Authorization"] = f"Bearer {token}"
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *args, **kwargs: {"answer": "Injected answer", "evidence_ids": [{"unexpected": "object"}, ["nested"], None, 1]})
    response = client.post("/api/ask", json={"question": "Life insurance?"})
    assert response.status_code == 200
    assert "MetLife" in response.json()["answer"]
    assert all(isinstance(value, str) for value in response.json()["evidence_ids"])
