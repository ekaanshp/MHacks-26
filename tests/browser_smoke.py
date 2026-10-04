"""Repeatable browser checks against an isolated synthetic Lastly estate.

Run from the project root after installing development requirements and Chromium:
    python -m playwright install chromium
    python tests/browser_smoke.py

By default this starts its own offline server, using a temporary data directory,
and removes all state after the checks. No external provider call is made.
To capture screenshots: python tests/browser_smoke.py --screenshots /tmp/lastly-shots
An existing server is supported with --base-url, but its estate MUST be synthetic.
Account status and assignments are restored; activity logs on that server remain.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def read_json(url: str, access_code: str = "") -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {access_code}"} if access_code else {})
    with urlopen(request, timeout=3) as response:
        return json.load(response)


@contextmanager
def test_server(base_url: str | None, *, access_code: str = ""):
    if base_url:
        yield base_url.rstrip("/")
        return
    with tempfile.TemporaryDirectory(prefix="lastly-browser-") as temporary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        environment = dict(os.environ)
        environment.update({
            "LASTLY_DATA_DIR": str(Path(temporary) / "data"),
            "LASTLY_OFFLINE": "1",
            "LASTLY_ACCESS_TOKEN": access_code,
            "LASTLY_AGENT_TOKEN": "",
            "DATABASE_URL": "",
            "ANTHROPIC_API_KEY": "",
            "ELEVENLABS_API_KEY": "",
            "ELEVENLABS_AGENT_ID": "",
            "ELEVENLABS_PHONE_NUMBER_ID": "",
        })
        base_url = f"http://127.0.0.1:{port}"
        with (Path(temporary) / "server.log").open("w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=environment, stdout=log, stderr=log,
            )
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(f"The isolated test server exited.\n{log.read()[-2500:]}")
                    try:
                        if read_json(f"{base_url}/api/health", access_code).get("status") == "ok":
                            break
                    except (URLError, TimeoutError):
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError("The isolated test server did not become ready within 25 seconds.")
                yield base_url
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def save_screenshot(page, directory: Path | None, name: str, *, full_page: bool = True):
    if directory:
        page.screenshot(path=str(directory / f"{name}.png"), full_page=full_page)


def restore_account(page, base_url: str, account: dict):
    response = page.request.patch(
        f"{base_url}/api/account/{account['id']}",
        data={"status": account["status"], "assigned_to": account["assigned_to"]},
        headers={"X-Requested-With": "Lastly"},
    )
    assert response.ok, "Could not restore the synthetic account's original family edits."


def run_browser_checks(base_url: str, screenshots: Path | None, executable: str | None):
    from playwright.sync_api import sync_playwright

    estate = read_json(f"{base_url}/api/estate")
    if (estate.get("analysis") or {}).get("synthetic") is not True:
        raise RuntimeError("Browser checks require a synthetic test estate. No private estate was modified.")
    gym = next(account for account in estate["accounts"] if account["institution"] == "Planet Fitness")
    yoga = next(account for account in estate["accounts"] if account["sources"] == ["bank"])
    problems = []
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--no-sandbox"]}
        if executable:
            options["executable_path"] = executable
        browser = playwright.chromium.launch(**options)
        try:
            for width in (390, 1280, 1440):
                page = browser.new_page(viewport={"width": width, "height": 844 if width == 390 else 1000})
                page.on("pageerror", lambda error, width=width: problems.append(f"{width}px: {error}"))
                if width != 390:
                    # Analysis itself is exercised once; later responsive
                    # navigation reuses its synthetic result under rate limits.
                    page.route("**/api/analyze?demo=1", lambda route: route.fulfill(
                        status=200, content_type="application/json", body=json.dumps(estate),
                    ))
                # A rejected call is simulated locally: never dial a real number in a browser test.
                page.route("**/api/call/*", lambda route: route.fulfill(
                    status=503, content_type="application/json",
                    body=json.dumps({"detail": "Voice calling is not configured. Add ElevenLabs credentials to enable calls."}),
                ))
                try:
                    page.goto(f"{base_url}/?demo=1&estate=margaret-ellis&as=Daniel")
                    page.get_by_role("button", name="Read Margaret").click()
                    page.locator("#dashboard").wait_for(state="visible")
                    assert page.locator(".account-row").count() == len(estate["accounts"])
                    dimensions = page.evaluate("({width: innerWidth, scrollWidth: document.documentElement.scrollWidth})")
                    assert dimensions["scrollWidth"] <= dimensions["width"], f"Horizontal overflow at {width}px: {dimensions}"
                    save_screenshot(page, screenshots, f"dashboard-{width}")
                    save_screenshot(page, screenshots, f"dashboard-top-{width}", full_page=False)
                    page.locator(f'[data-account-id="{gym["id"]}"]').click()
                    page.locator(".evidence-card .evidence-body").wait_for()
                    page.locator("#account-assignment").select_option("Sarah")
                    page.get_by_text("Saved for your family.").wait_for()
                    page.locator("#account-status").select_option("in_progress")
                    page.get_by_text("Saved for your family.").wait_for()
                    page.get_by_role("button", name="Draft a letter").click()
                    page.locator("#letter-editor").wait_for()
                    assert "Margaret" in page.locator("#letter-editor").input_value()
                    page.get_by_role("button", name="Call them for me").click()
                    page.locator("#call-number").fill("+17345551234")
                    page.get_by_role("button", name="Approve & place call").click()
                    page.locator(".call-form .inline-error:not([hidden])").wait_for()
                    assert page.locator("#account-status").input_value() == "in_progress"
                    save_screenshot(page, screenshots, f"drawer-{width}", full_page=False)
                    page.keyboard.press("Escape")
                    page.locator("#drawer-shell").wait_for(state="hidden")
                    assert page.evaluate("document.activeElement.dataset.accountId") == gym["id"]
                    page.locator(f'[data-account-id="{yoga["id"]}"]').click()
                    page.locator(".bank-fact").first.wait_for()
                    assert "No emails. Found only on her bank statement." in page.locator("#drawer-content").inner_text()
                    page.keyboard.press("Escape")
                    page.locator("#ask-input").fill("Did Margaret have life insurance?")
                    page.get_by_role("button", name="Ask Lastly").click()
                    page.locator(".proof-link").first.wait_for()
                    assert "MetLife" in page.locator("#ask-result").inner_text()
                    page.locator(".proof-link").first.click()
                    page.locator("#drawer-title").wait_for()
                    page.keyboard.press("Escape")
                    print(f"PASS {width}px: account ledger, no overflow, email/bank proof, family edits, letter, call error, cited question, Escape/focus")
                finally:
                    restore_account(page, base_url, gym)
                    page.close()

            # Provider responses below exist only inside Playwright's request interception.
            for cancelled in (True, False):
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                page.on("pageerror", lambda error: problems.append(str(error)))
                page.route("**/api/analyze?demo=1", lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(estate),
                ))
                page.request.patch(f"{base_url}/api/account/{gym['id']}", data={"status": "open"}, headers={"X-Requested-With": "Lastly"})
                page.route(f"**/api/call/{gym['id']}", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"success": True, "conversation_id": "browser-test-call", "callSid": "test"}),
                ))
                polls = [0]

                def call_progress(route, _request=None, *, polls=polls, cancelled=cancelled):
                    polls[0] += 1
                    response = {"status": "in-progress", "transcript_summary": None, "summary": None} if polls[0] < 2 else {
                        "status": "done",
                        "transcript_summary": "Company confirmed cancellation." if cancelled else "Call finished. Cancellation remains pending.",
                        "summary": {
                            "cancelled": cancelled,
                            "reference_number": "PF-20931",
                            "next_steps": ["Retain the cancellation reference."] if cancelled else ["Email a death certificate before cancellation can be confirmed."],
                        },
                    }
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

                page.route("**/api/call/browser-test-call", call_progress)

                def estate_after_call(route, _request=None, *, polls=polls, cancelled=cancelled):
                    # The server records a finished call's outcome; the page re-reads the estate to show it.
                    response = route.fetch()
                    body = response.json()
                    if polls[0] >= 2 and cancelled:
                        for account in body["accounts"]:
                            if account["id"] == gym["id"]:
                                account["status"] = "done"
                    route.fulfill(response=response, body=json.dumps(body))

                page.route("**/api/estate", estate_after_call)
                try:
                    page.goto(f"{base_url}/?demo=1&estate=margaret-ellis&as=Daniel")
                    page.get_by_role("button", name="Read Margaret").click()
                    page.locator("#dashboard").wait_for(state="visible")
                    page.locator(f'[data-account-id="{gym["id"]}"]').click()
                    page.get_by_role("button", name="Call them for me").click()
                    page.locator("#call-number").fill("+17345551234")
                    page.get_by_role("button", name="Approve & place call").click()
                    page.get_by_text("Call in progress", exact=True).wait_for()
                    page.get_by_text("Reference: PF-20931", exact=True).wait_for()
                    assert page.locator("#account-status").input_value() == ("done" if cancelled else "open")
                    print(f"PASS call polling: cancelled={cancelled}, progress {'done' if cancelled else 'open'}")
                finally:
                    restore_account(page, base_url, gym)
                    page.close()

            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda error: problems.append(str(error)))
            try:
                # This fixture keeps the non-demo onboarding path offline even on a configured server.
                page.route("**/api/analyze", lambda route: route.fulfill(
                    status=200, content_type="application/json", body=json.dumps(estate),
                ))
                page.goto(f"{base_url}/?estate=margaret-ellis&as=Daniel")
                page.locator("#plan-screen").wait_for(state="visible")
                save_screenshot(page, screenshots, "plan-ahead-390")
                page.get_by_role("button", name="Continue to Margaret").click()
                page.get_by_role("button", name="Read Margaret").click()
                page.locator("#dashboard").wait_for(state="visible")
                page.route("**/api/email/*", lambda route: route.fulfill(
                    status=503, content_type="application/json", body=json.dumps({"detail": "Evidence unavailable. Please try again."}),
                ))
                page.get_by_role("button", name="See what we found").click()
                page.get_by_role("button", name="Try again", exact=True).wait_for()
                page.get_by_role("button", name="Close account details").focus()
                page.keyboard.press("Shift+Tab")
                assert page.evaluate("document.activeElement.id") != "drawer-close"
                page.keyboard.press("Tab")
                assert page.evaluate("document.activeElement.id") == "drawer-close"
                print("PASS plan-ahead, evidence error/retry, drawer focus trap")
            finally:
                page.close()
            assert not problems, f"Browser runtime errors: {problems}"
        finally:
            browser.close()


def run_security_browser_checks(base_url: str, access_code: str, executable: str | None):
    """Exercise the actual session boundary and call retry behavior in Chromium."""
    from playwright.sync_api import sync_playwright

    estate = read_json(f"{base_url}/api/estate", access_code)
    assert (estate.get("analysis") or {}).get("synthetic") is True
    gym = next(account for account in estate["accounts"] if account["institution"] == "Planet Fitness")
    proof_account = next(account for account in estate["accounts"] if account["id"] != gym["id"] and any(eid.startswith("msg_") for eid in account["evidence_ids"]))
    problems = []
    mutations = []
    with sync_playwright() as playwright:
        options = {"headless": True, "args": ["--no-sandbox"]}
        if executable:
            options["executable_path"] = executable
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        context.add_init_script("sessionStorage.setItem('lastly-family-access', 'obsolete-stored-bearer');")
        page = context.new_page()
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.on("request", lambda request: mutations.append((request.url, request.method, request.all_headers()))
                if "/api/" in request.url and request.method not in {"GET", "HEAD", "OPTIONS"} else None)
        try:
            response = page.goto(f"{base_url}/?demo=1&estate=margaret-ellis&as=Daniel")
            csp = response.headers.get("content-security-policy", "")
            assert "script-src 'self'" in csp and "style-src-attr 'none'" in csp
            page.locator("#access-dialog[open]").wait_for()
            assert page.locator("#access-code").get_attribute("type") == "password"
            page.locator("#access-code").fill("incorrect-family-code")
            page.get_by_role("button", name="Unlock estate", exact=True).click()
            page.locator("#access-error:not([hidden])").wait_for()
            assert page.locator("#access-code").input_value() == ""
            page.locator("#access-code").fill(access_code)
            page.get_by_role("button", name="Unlock estate", exact=True).click()
            page.locator("#access-dialog").wait_for(state="hidden")
            page.get_by_role("button", name="Read Margaret").click()
            page.locator("#dashboard").wait_for(state="visible")
            assert page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])") == "[[],[]]"
            cookies = context.cookies()
            session_cookie = next(cookie for cookie in cookies if "lastly_session" in cookie["name"])
            assert session_cookie["httpOnly"] is True and session_cookie["sameSite"] == "Strict"
            assert access_code not in session_cookie["value"]
            assert "lastly_session" not in page.evaluate("document.cookie")

            # An inline script and stylesheet inserted by an attacker are denied.
            page.evaluate("""() => {
                window.lastlyInlineExecuted = false;
                const script = document.createElement('script');
                script.textContent = 'window.lastlyInlineExecuted = true';
                document.body.append(script);
                const style = document.createElement('style');
                style.textContent = 'body { display: none !important; }';
                document.head.append(style);
            }""")
            assert page.evaluate("window.lastlyInlineExecuted") is False
            assert page.locator("#dashboard").is_visible()

            page.reload()
            page.wait_for_function("!document.querySelector('#lock-estate').hidden")
            assert not page.locator("#access-dialog").is_visible()
            page.get_by_role("button", name="Read Margaret").click()
            page.locator("#dashboard").wait_for(state="visible")
            page.locator(f'[data-account-id="{gym["id"]}"]').click()
            # Model another tab renewing the shared cookie while this tab still
            # has the previous CSRF token. Its first mutation must be rejected.
            renewed = context.request.post(f"{base_url}/api/session", data={"access_code": access_code},
                                           headers={"X-Requested-With": "Lastly"})
            assert renewed.ok
            page.locator("#account-status").select_option("in_progress")
            page.get_by_text("Your family session was refreshed. Approve this action again.", exact=False).wait_for()
            unchanged = read_json(f"{base_url}/api/estate", access_code)
            assert next(account for account in unchanged["accounts"] if account["id"] == gym["id"])["status"] == gym["status"]
            page.locator("#account-status").select_option("in_progress")
            page.get_by_text("Saved for your family.").wait_for()
            page.locator("#account-status").select_option(gym["status"])
            page.get_by_text("Saved for your family.").wait_for()
            page.keyboard.press("Escape")

            # Inbox HTML remains text even after private access is granted.
            malicious_text = '<img src="x" onerror="window.lastlyEvidenceExecuted=true">'
            page.route("**/api/email/*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"body": malicious_text, "subject": "Untrusted inbox HTML"}),
            ))
            page.locator(f'[data-account-id="{proof_account["id"]}"]').click()
            page.locator(".evidence-body").wait_for()
            assert page.locator(".evidence-body").inner_text() == malicious_text
            assert page.locator(".evidence-body img").count() == 0
            assert page.evaluate("window.lastlyEvidenceExecuted === undefined")
            page.keyboard.press("Escape")
            page.locator(f'[data-account-id="{gym["id"]}"]').click()

            call_keys = []
            def uncertain_call(route):
                call_keys.append(route.request.headers.get("idempotency-key"))
                if len(call_keys) == 1:
                    route.abort("failed")
                elif len(call_keys) == 2:
                    route.fulfill(status=400, content_type="application/json", body=json.dumps({"detail": "Confirmed no call was placed."}))
                else:
                    route.fulfill(status=200, content_type="application/json", body=json.dumps({"success": True, "conversation_id": None}))
            page.route(f"**/api/call/{gym['id']}", uncertain_call)
            page.get_by_role("button", name="Call them for me").click()
            page.locator("#call-number").fill("+17345551234")
            page.get_by_role("button", name="Approve & place call").click()
            page.get_by_text("The call’s outcome is unconfirmed.", exact=False).wait_for()
            page.wait_for_timeout(350)
            assert len(call_keys) == 1, "A call was automatically retried after a network failure."
            page.get_by_role("button", name="Approve & place call").click()
            page.get_by_text("Confirmed no call was placed.", exact=False).wait_for()
            page.get_by_role("button", name="Approve & place call").click()
            page.get_by_text("Call placed", exact=True).wait_for()
            assert call_keys[0] and call_keys[0] == call_keys[1] and call_keys[2] != call_keys[1]
            page.keyboard.press("Escape")
            print("PASS browser security: password login, HttpOnly session, CSRF after reload, strict CSP, inbox text, idempotent call retry")

            # Reauthentication must never silently replay an approved phone call.
            page.unroute(f"**/api/call/{gym['id']}")
            page.reload()
            page.get_by_role("button", name="Read Margaret").click()
            page.locator("#dashboard").wait_for(state="visible")
            page.locator(f'[data-account-id="{gym["id"]}"]').click()
            expired_keys = []
            def expired_call(route):
                expired_keys.append(route.request.headers.get("idempotency-key"))
                route.fulfill(status=401 if len(expired_keys) == 1 else 200, content_type="application/json",
                              body=json.dumps({"success": True, "conversation_id": None}) if len(expired_keys) > 1 else json.dumps({"detail": "Session expired. Approve the call again."}))
            page.route(f"**/api/call/{gym['id']}", expired_call)
            page.get_by_role("button", name="Call them for me").click()
            page.locator("#call-number").fill("+17345551234")
            page.get_by_role("button", name="Approve & place call").click()
            page.locator("#access-dialog[open]").wait_for()
            page.locator("#access-code").fill(access_code)
            page.get_by_role("button", name="Unlock estate", exact=True).click()
            page.get_by_text("Session expired. Approve the call again.", exact=False).wait_for()
            page.wait_for_timeout(350)
            assert len(expired_keys) == 1, "A call was automatically replayed after login."
            page.get_by_role("button", name="Approve & place call").click()
            page.get_by_text("Call placed", exact=True).wait_for()
            assert len(expired_keys) == 2 and expired_keys[0] != expired_keys[1]
            page.keyboard.press("Escape")
            print("PASS browser security: an expired call session requires renewed explicit approval")

            # Every browser mutation uses the same-origin marker; sessions keep
            # the access code confined to the authentication exchange.
            for url, method, headers in mutations:
                assert headers.get("x-requested-with") == "Lastly", (url, method)
                assert "authorization" not in headers, "A family bearer credential reached a browser API request."
                if not (url.endswith("/api/session") and method == "POST"):
                    assert headers.get("x-csrf-token"), (url, method)

            page.get_by_role("button", name="Lock estate", exact=True).click()
            page.locator("#access-dialog[open]").wait_for()
            assert not page.locator("#dashboard").is_visible()
            assert not [cookie for cookie in context.cookies() if "lastly_session" in cookie["name"]]
            assert page.request.get(f"{base_url}/api/session").json()["authenticated"] is False
            print("PASS browser security: explicit lock revokes the session and removes estate data")
            assert not problems, f"Browser security runtime errors: {problems}"
        finally:
            context.close()
            browser.close()


def run_conversation_browser_checks(base_url, executable):
    """SDK events and Fetch.ai status updates are isolated from external providers."""
    from playwright.sync_api import sync_playwright

    estate = read_json(f"{base_url}/api/estate")
    account = next(a for a in estate["accounts"] if "Paramount" in a["institution"])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox"])
        page = browser.new_page(reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""(() => {
            window.voiceTest = { sent: [], muted: [] };
            window.ElevenLabsClient = { Conversation: { startSession: async (options) => {
                window.voiceTest.options = options;
                const conversation = {
                    getId: () => 'voice-test', getInputVolume: () => 0.4,
                    setMicMuted: (muted) => window.voiceTest.muted.push(muted),
                    sendUserMessage: (text) => window.voiceTest.sent.push(text),
                    endSession: async () => options.onDisconnect(),
                };
                options.onConversationCreated(conversation);
                return conversation;
            } } };
        })();""")
        page.route("**/api/voice/**", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"signed_url": "wss://api.elevenlabs.io/test", "dynamic_variables": {"institution": account["institution"]}})))
        page.route("**/api/call/voice-test", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"status": "done", "summary": {"cancelled": False, "reference_number": "REF-VOICE", "next_steps": []}})))
        task_started = [False]

        def task_route(route):
            if route.request.method == "POST":
                task_started[0] = True
                result = {"id": "test-task", "status": "queued", "transcript": []}
            elif task_started[0]:
                result = {"id": "test-task", "status": "completed", "reference_number": "REF-PARAMOUNT", "transcript": [
                    {"speaker": "Lastly", "message": "Please cancel this subscription."},
                    {"speaker": account["institution"], "message": "The subscription has been cancelled."},
                ]}
            else:
                result = {"status": None}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

        page.route("**/api/agent-task/*", task_route)

        def estate_after_task(route):
            # The server records the company agent's answer; the page re-reads the estate to show it.
            response = route.fetch()
            body = response.json()
            if task_started[0]:
                for item in body["accounts"]:
                    if item["id"] == account["id"]:
                        item["status"] = "done"
            route.fulfill(response=response, body=json.dumps(body))

        page.route("**/api/estate", estate_after_task)
        try:
            page.goto(f"{base_url}/?demo=1&estate=margaret-ellis&as=Daniel")
            page.get_by_role("button", name="Read Margaret").click()
            page.locator("#dashboard").wait_for(state="visible")
            page.locator(f'[data-account-id="{account["id"]}"]').click()
            page.get_by_role("button", name="Talk to them").click()
            page.get_by_role("button", name="Approve & start conversation").click()
            page.get_by_text("Microphone on", exact=False).wait_for()
            assert page.evaluate("window.voiceTest.options.textOnly") is False
            assert page.evaluate("window.voiceTest.muted") == [False]
            page.evaluate("window.voiceTest.options.onMessage({source: 'user', message: 'I need a death certificate first.'})")
            assert "I need a death certificate first." in page.locator(".voice-transcript").inner_text()
            page.locator("#voice-reply").fill("The reference is REF-123.")
            page.get_by_role("button", name="Send reply").click()
            assert page.evaluate("window.voiceTest.sent") == ["The reference is REF-123."]
            page.get_by_role("button", name="Mute microphone", exact=True).click()
            page.get_by_text("Microphone muted", exact=True).wait_for()
            page.get_by_role("button", name="Unmute microphone", exact=True).click()
            assert page.evaluate("window.voiceTest.muted") == [False, True, False]
            page.get_by_role("button", name="End conversation", exact=True).click()
            page.get_by_text("Reference: REF-VOICE", exact=True).wait_for()
            assert page.locator("#account-status").input_value() != "done"
            page.get_by_role("button", name="Let agents handle it").click()
            page.get_by_text("Reference: REF-PARAMOUNT", exact=True).wait_for()
            for _ in range(50):
                if page.locator("#account-status").input_value() == "done":
                    break
                page.wait_for_timeout(200)
            assert page.locator("#account-status").input_value() == "done"
            assert "subscription has been cancelled" in page.locator(".voice-transcript").inner_text()
            assert not errors, errors
            print("PASS conversation modes: live mic, user transcript, typed replies, mute, Fetch.ai receipt and Done without reload")
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", help="Use an existing synthetic estate instead of an isolated offline server.")
    parser.add_argument("--screenshots", type=Path, help="Directory in which to save responsive screenshots.")
    parser.add_argument("--browser-executable", help="Use an already installed Chromium executable.")
    parser.add_argument("--security-only", action="store_true", help="Run only the isolated session and browser security checks.")
    args = parser.parse_args()
    if args.security_only and args.base_url:
        parser.error("--security-only uses its own isolated authenticated server.")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    try:
        if not args.security_only:
            with test_server(args.base_url) as base_url:
                run_browser_checks(base_url, args.screenshots, args.browser_executable)
                run_conversation_browser_checks(base_url, args.browser_executable)
        if not args.base_url:
            access_code = secrets.token_urlsafe(32)
            with test_server(None, access_code=access_code) as base_url:
                run_security_browser_checks(base_url, access_code, args.browser_executable)
    except ImportError:
        print("Install development dependencies, then run: python -m playwright install chromium", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Browser validation failed: {error}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print("All browser checks passed. Synthetic account edits were restored; isolated test data was removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
