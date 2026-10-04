"""Fail-closed HTTP boundaries for the single-family, single-process application.

Opaque sessions never put the access code in browser storage. Authorization uses
the ASGI path, independent of attacker-controlled Host or forwarding headers.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

import config
import secure_storage
from config import DATA_DIR, get_settings

LOG = logging.getLogger("lastly.security")
BODY_LIMIT = 64 * 1024
SESSION_IDLE = 30 * 60
SESSION_LIFETIME = 8 * 60 * 60
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; style-src-attr 'none'; "
    "img-src 'self' data:; connect-src 'self' wss://api.elevenlabs.io; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def strong_secret(value: str) -> bool:
    return len(value) >= 32 and len(value) <= 512 and value.isascii() and not any(char.isspace() for char in value)


def configuration_error() -> str | None:
    settings = get_settings()
    if not settings.offline and (settings.anthropic_api_key or settings.elevenlabs_api_key) and not settings.access_token:
        return "Live AI and voice integrations require LASTLY_ACCESS_TOKEN."
    if settings.access_token and not strong_secret(settings.access_token):
        return "LASTLY_ACCESS_TOKEN must be a random secret of at least 32 characters."
    if settings.agent_token and (not strong_secret(settings.agent_token) or settings.agent_token == settings.access_token):
        return "LASTLY_AGENT_TOKEN must be a separate random secret of at least 32 characters."
    if settings.production:
        if not settings.access_token or not os.getenv("LASTLY_DATA_KEY"):
            return "Production requires LASTLY_ACCESS_TOKEN and LASTLY_DATA_KEY."
        if not os.getenv("LASTLY_ALLOWED_HOSTS") or not os.getenv("LASTLY_ALLOWED_ORIGINS"):
            return "Production requires explicit LASTLY_ALLOWED_HOSTS and LASTLY_ALLOWED_ORIGINS."
        if any(not value.strip().startswith("https://") for value in os.getenv("LASTLY_ALLOWED_ORIGINS", "").split(",")):
            return "Production origins must use HTTPS."
    if os.getenv("LASTLY_DATA_KEY"):
        try:
            # Exercise key validation without exporting the key or touching disk.
            secure_storage.data_key()
        except (ValueError, RuntimeError):
            return "LASTLY_DATA_KEY must encode a valid 32-byte encryption key."
    return None


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.sessions: OrderedDict[str, dict] = OrderedDict()
        self.buckets: OrderedDict[tuple, tuple[float, int]] = OrderedDict()
        self.cached_input: tuple | None = None
        self.input_policy = (False, "")

    def policy(self) -> tuple[bool, str]:
        directory = Path(os.getenv("LASTLY_DATA_DIR", str(DATA_DIR)))
        path = directory / "inbox.json"
        try:
            stat = path.lstat()
            marker = (str(path), stat.st_mtime_ns, stat.st_size, fingerprint(os.getenv("LASTLY_DATA_KEY", "")))
            with self.lock:
                if marker != self.cached_input:
                    inbox = secure_storage.read_json(path)
                    if not isinstance(inbox, dict) or not isinstance(inbox.get("persona"), dict):
                        return True, "unavailable"
                    self.input_policy = (inbox.get("synthetic") is not True, str(inbox.get("persona", {}).get("email", "")))
                    self.cached_input = marker
                return self.input_policy
        except (OSError, ValueError, RuntimeError, TypeError):
            # A missing, unreadable or malformed input is never treated as a public demo.
            return True, "unavailable"

    def consume(self, key: tuple, limit: int, interval: int = 60) -> bool:
        now = time.monotonic()
        with self.lock:
            for old in list(self.buckets)[:128]:
                if now - self.buckets[old][0] > 86400:
                    del self.buckets[old]
            start, count = self.buckets.get(key, (now, 0))
            if now - start >= interval:
                start, count = now, 0
            if key not in self.buckets and len(self.buckets) >= 4096:
                return False
            self.buckets[key] = (start, count + 1)
            self.buckets.move_to_end(key)
            return count < limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = secrets.token_hex(16)
        is_secure = scope.get("scheme") == "https"
        cookie_name = "__Host-lastly_session" if is_secure else "lastly_session"
        path = scope.get("path", "")
        method = scope["method"]
        response_started = False
        pairs = scope.get("headers", [])
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in pairs}

        async def protected_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                defaults = {
                    "content-security-policy": CSP, "x-content-type-options": "nosniff",
                    "x-frame-options": "DENY", "referrer-policy": "no-referrer",
                    "permissions-policy": "camera=(), microphone=(self), geolocation=(), payment=()",
                    "cross-origin-opener-policy": "same-origin", "cross-origin-resource-policy": "same-origin",
                    "cache-control": "no-store", "x-request-id": request_id,
                }
                if is_secure:
                    defaults["strict-transport-security"] = "max-age=31536000"
                filtered = [(key, val) for key, val in message.get("headers", []) if key.decode().lower() not in defaults]
                message["headers"] = filtered + [(key.encode(), val.encode()) for key, val in defaults.items()]
            await send(message)

        async def reject(status: int, detail: str, **extra):
            if status in {401, 403, 413, 429}:
                LOG.info("request_rejected status=%s request_id=%s", status, request_id)
            await JSONResponse({"detail": detail, "request_id": request_id}, status_code=status, headers=extra)(scope, receive, protected_send)

        for name in (b"host", b"authorization", b"cookie", b"origin", b"content-length", b"x-csrf-token"):
            if sum(key.lower() == name for key, _ in pairs) > 1:
                return await reject(400, "Duplicate security-sensitive headers are not allowed.")
        host = headers.get("host", "")
        if not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\])(?::[0-9]{1,5})?", host):
            return await reject(400, "Invalid Host header.")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            return await reject(400, "Invalid Host header.")
        allowed_hosts = {value.strip().lower() for value in os.getenv("LASTLY_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if value.strip()}
        if hostname not in allowed_hosts:
            return await reject(400, "This host is not allowed.")
        if "transfer-encoding" in headers and "content-length" in headers:
            return await reject(400, "Ambiguous request framing.")
        if headers.get("sec-fetch-site", "").lower() == "cross-site":
            return await reject(403, "Cross-site requests are not allowed.")
        origin = headers.get("origin")
        allowed_origins = {value.strip().rstrip("/") for value in os.getenv("LASTLY_ALLOWED_ORIGINS", "").split(",") if value.strip()}
        if not allowed_origins:
            allowed_origins.add(f"{scope.get('scheme', 'http')}://{host}")
        if origin is not None and origin not in allowed_origins:
            return await reject(403, "This request origin is not allowed.")
        client = str((scope.get("client") or ("unknown",))[0])
        try:
            loopback = ipaddress.ip_address(client).is_loopback
        except ValueError:
            loopback = client == "testclient"  # Starlette's in-process test transport only.
        settings = get_settings()
        private, owner = self.policy()
        auth_required = private or settings.production or bool(settings.access_token)
        is_api = path == "/api" or path.startswith("/api/")

        def exposure_error() -> tuple[int, str] | None:
            if private and (not settings.access_token or not os.getenv("LASTLY_DATA_KEY")):
                return 503, "Private estates require an access code and an encryption key before they can be opened."
            if not loopback and not auth_required:
                return 403, "The public sample is available on this computer only."
            return None

        if is_api and (error := configuration_error()):
            return await reject(503, error)
        if is_api and (settings.production or not loopback) and not is_secure:
            return await reject(403, "HTTPS is required for remote or production estate access.")
        if is_api and (failure := exposure_error()):
            return await reject(*failure)
        if is_api and not self.consume(("requests", client), 180 if auth_required else 600):
            return await reject(429, "Too many requests. Try again shortly.", **{"Retry-After": "60"})
        if is_api and method in MUTATING and headers.get("x-requested-with") != "Lastly":
            return await reject(403, "Use the Lastly client request header for this operation.")
        # Buffer a bounded body, including chunked messages without Content-Length.
        if is_api:
            declared = headers.get("content-length", "0")
            if not declared.isdecimal() or len(declared) > 10:
                return await reject(400, "Invalid request length.")
            if int(declared) > BODY_LIMIT:
                return await reject(413, "The request body is too large.")
            chunks = []
            total = 0
            deadline = time.monotonic() + 15
            while True:
                try:
                    message = await asyncio.wait_for(receive(), timeout=max(0.001, deadline - time.monotonic()))
                except TimeoutError:
                    return await reject(408, "The request body did not arrive in time.")
                if message["type"] == "http.disconnect":
                    return
                body = message.get("body", b"")
                total += len(body)
                if total > BODY_LIMIT:
                    return await reject(413, "The request body is too large.")
                chunks.append(body)
                if not message.get("more_body", False):
                    break
            body = b"".join(chunks)
            # An import can finish while the body streams; authorize against the estate as it is now.
            if (latest := self.policy()) != (private, owner):
                private, owner = latest
                auth_required = private or settings.production or bool(settings.access_token)
                if failure := exposure_error():
                    return await reject(*failure)
            if body and method in MUTATING and headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                return await reject(415, "A JSON request body is required.")
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
        else:
            body = b""
            bounded_receive = receive
        context = fingerprint(settings.access_token + "\x00" + owner + "\x00" + str(Path(os.getenv("LASTLY_DATA_DIR", str(DATA_DIR)))))
        principal = None
        session = None
        cookie_hash = None
        auth = headers.get("authorization", "")
        if auth:
            if not auth.startswith("Bearer ") or not auth[7:] or "," in auth:
                return await reject(401, "A valid Bearer credential is required.")
            value = auth[7:]
            if settings.agent_token and secrets.compare_digest(fingerprint(value), fingerprint(settings.agent_token)):
                principal = "agent"
            elif settings.access_token and secrets.compare_digest(fingerprint(value), fingerprint(settings.access_token)):
                principal = "family"
            else:
                return await reject(401, "Enter the family access code to continue.")
        else:
            jar = SimpleCookie()
            try:
                jar.load(headers.get("cookie", ""))
            except Exception:
                return await reject(400, "Invalid session cookie.")
            if cookie_name in jar:
                cookie_hash = fingerprint(jar[cookie_name].value)
                with self.lock:
                    candidate = self.sessions.get(cookie_hash)
                    now = time.monotonic()
                    if candidate and candidate["context"] == context and now - candidate["last"] < SESSION_IDLE and now - candidate["created"] < SESSION_LIFETIME:
                        session = candidate
                        session["last"] = now
                        principal = "family"
                    elif candidate:
                        del self.sessions[cookie_hash]
        agent_headers = any(name.startswith("x-lastly-agent-") for name in headers)
        if agent_headers and principal != "agent":
            return await reject(403, "Agent identity requires the dedicated agent credential.")
        # The agent may read the estate, ask questions and relay insurer-agent claims; nothing else.
        agent_routes = {("/api/estate", "GET"), ("/api/ask", "POST"), ("/api/agent/claims", "GET"), ("/api/agent/tasks", "GET")}
        relay = method == "POST" and re.fullmatch(r"/api/agent/(?:claims|tasks)/[a-f0-9]{32}", path) is not None
        if principal == "agent" and (path, method) not in agent_routes and not relay:
            return await reject(403, "This agent credential has read, question and claim-relay access only.")
        if path == "/api/session":
            if method == "GET":
                response = {"authenticated": principal == "family" or not auth_required}
                if session:
                    response["csrf_token"] = session["csrf"]
                return await JSONResponse(response)(scope, bounded_receive, protected_send)
            if method == "POST":
                if not self.consume(("login", client), 5):
                    return await reject(429, "Too many login attempts. Try again in a minute.", **{"Retry-After": "60"})
                try:
                    payload = json.loads(body)
                    supplied = payload.get("access_code") if isinstance(payload, dict) and set(payload) == {"access_code"} else None
                except (ValueError, UnicodeDecodeError):
                    supplied = None
                if not settings.access_token or not isinstance(supplied, str) or len(supplied) > 512 or not secrets.compare_digest(fingerprint(supplied), fingerprint(settings.access_token)):
                    return await reject(401, "The access code was not accepted.")
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                now = time.monotonic()
                with self.lock:
                    for key, value in list(self.sessions.items()):
                        if now - value["last"] >= SESSION_IDLE or now - value["created"] >= SESSION_LIFETIME:
                            del self.sessions[key]
                    if len(self.sessions) >= 128:
                        return await reject(429, "Too many active sessions. Lock an existing session first.")
                    if cookie_hash:
                        self.sessions.pop(cookie_hash, None)
                    self.sessions[fingerprint(token)] = {"csrf": csrf, "created": now, "last": now, "context": context}
                response = JSONResponse({"authenticated": True, "csrf_token": csrf})
                response.set_cookie(cookie_name, token, max_age=SESSION_LIFETIME, path="/", secure=is_secure, httponly=True, samesite="strict")
                return await response(scope, bounded_receive, protected_send)
        if is_api and auth_required and principal is None:
            return await reject(401, "Enter the family access code to continue.")
        if session and method in MUTATING and not secrets.compare_digest(fingerprint(headers.get("x-csrf-token", "")), fingerprint(session["csrf"])):
            return await reject(403, "Your security token is missing or expired. Unlock the estate again.")
        if path == "/api/session" and method == "DELETE":
            if cookie_hash:
                with self.lock:
                    self.sessions.pop(cookie_hash, None)
            response = JSONResponse({"authenticated": False})
            response.delete_cookie(cookie_name, path="/", secure=is_secure, httponly=True, samesite="strict")
            return await response(scope, bounded_receive, protected_send)
        if is_api and method in MUTATING:
            operation = path.split("/")[2] if len(path.split("/")) > 2 else "unknown"
            limits = {"analyze": 4, "ask": 24, "letter": 12, "call": 3, "voice": 12, "agent-task": 12, "identify": 12}
            if operation in limits and not self.consume((operation, context), limits[operation]):
                return await reject(429, "This operation is temporarily rate limited.", **{"Retry-After": "60"})
        scope.setdefault("state", {}).update(principal=principal, request_id=request_id)
        # Which deceased person's estate this request is about; unknown values fall back to the root estate.
        estate_dir = None
        if is_api and (chosen := headers.get("x-lastly-estate", "").strip()):
            estate_dir = config.estates().get(chosen)
            if estate_dir is None:
                return await reject(404, "That estate was not found.")
        token = config.use_data_dir(estate_dir) if estate_dir is not None else None
        try:
            await self.app(scope, bounded_receive, protected_send)
        except Exception as exc:
            LOG.error("request_failed type=%s request_id=%s", type(exc).__name__, request_id)
            if response_started:
                raise RuntimeError("The response was interrupted.") from None
            await reject(500, "The request could not be completed. Your saved data remains available.")
        finally:
            if token is not None:
                config.reset_data_dir(token)
