"""Desktop hooks for TradingAgents' original Sign in with ChatGPT flow.

Only account status and the authorize URL cross the desktop bridge. Tokens stay
under the original auth module's ownership. A private, short-lived mailbox lets
another bridge process deliver a pasted loopback callback to ``sign_in``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tradingagents.llm_clients import chatgpt_auth

RUNTIME = Path(__file__).resolve().parents[1] / "work/runtime/subscription"
_ID = re.compile(r"[0-9a-f]{32}\Z")
Emit = Callable[[str, dict], None]


class SubscriptionCancelled(RuntimeError):
    """The desktop cancelled its own in-flight sign-in."""


def status() -> dict:
    """Return a public account label without exposing/refreshing credentials."""
    email = chatgpt_auth.signed_in_as()
    return {"provider": "chatgpt", "signed_in": email is not None, "email": email}


def logout() -> dict:
    """Revoke and forget through upstream, including its offline fallback."""
    result = chatgpt_auth.sign_out()
    return {**status(), "result": result}


def list_models() -> dict:
    """Ask the plan for its own available models using the upstream client."""
    return {"models": [{"label": label, "id": model}
                       for label, model in chatgpt_auth.list_models()]}


def _private_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _attempt(attempt_id: str, runtime: Path) -> Path:
    if not isinstance(attempt_id, str) or not _ID.fullmatch(attempt_id):
        raise ValueError("Invalid ChatGPT sign-in attempt.")
    path = runtime / attempt_id
    if not path.is_dir() or path.is_symlink():
        raise ValueError("This ChatGPT sign-in is no longer active. Start signing in again.")
    return path


def submit_redirect(attempt_id: str, url: str, *, runtime: Path = RUNTIME) -> dict:
    """Deliver a matching pasted callback; never echo its code or full URL."""
    attempt = _attempt(attempt_id, runtime)
    if not isinstance(url, str) or len(url) > 16_384:
        raise ValueError("Paste the full browser callback address.")
    try:
        expected = json.loads((attempt / "pending.json").read_text(encoding="utf-8"))
        parsed = urlparse(url.strip())
        redirect = urlparse(expected["redirect_uri"])
        params = parse_qs(parsed.query)
        valid = (time.time() < expected["expires_at"]
                 and parsed.scheme == redirect.scheme == "http"
                 and parsed.hostname == redirect.hostname == "127.0.0.1"
                 and parsed.port == redirect.port
                 and parsed.path == redirect.path == chatgpt_auth.CALLBACK_PATH
                 and parsed.username is None and parsed.password is None
                 and not parsed.fragment
                 and params.get("state") == [expected["state"]]
                 and bool(params.get("code") or params.get("error")))
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise ValueError("That address does not match the active ChatGPT sign-in. Paste the full callback address.")
    _private_json(attempt / "redirect.json", {"url": url.strip()})
    return {"accepted": True, "attempt_id": attempt_id}


def login(emit: Emit, *, timeout: float = 600, runtime: Path = RUNTIME,
          port: int = chatgpt_auth.DEFAULT_PORT) -> dict:
    """Run upstream login with native loopback and desktop paste fallback.

    Emitted events are ``subscription_pending``, ``subscription_url``,
    ``subscription_message`` and ``subscription_status``. The parent may open the
    authorize URL after a user click. Paste replies call ``submit_redirect`` from
    a separate process. The reader and mailbox are cleaned when login finishes.
    """
    if not 0 < timeout <= 3600:
        raise ValueError("ChatGPT sign-in timeout must be between 0 and 3600 seconds.")
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    attempt_id = secrets.token_hex(16)
    attempt = runtime / attempt_id
    attempt.mkdir(mode=0o700)
    done = threading.Event()
    emit("subscription_pending", {"attempt_id": attempt_id})

    def say(message: str) -> None:
        if message.startswith(chatgpt_auth.AUTHORIZE_URL + "?"):
            query = parse_qs(urlparse(message).query)
            _private_json(attempt / "pending.json", {
                "state": query["state"][0], "redirect_uri": query["redirect_uri"][0],
                "expires_at": time.time() + timeout,
            })
            emit("subscription_url", {"attempt_id": attempt_id, "url": message})
        else:
            # Upstream's say() only prints human instructions; the callback URL
            # (which carries the one-time code) is never passed to say().
            emit("subscription_message", {"message": message})

    def read_line() -> str:
        while not done.wait(0.1):
            path = attempt / "redirect.json"
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            # The code is a transient mailbox value, never durable auth storage.
            path.unlink(missing_ok=True)
            return json.loads(text)["url"]
        raise EOFError

    def cancelled(_signum, _frame):
        raise SubscriptionCancelled("ChatGPT sign-in was cancelled.")

    previous_handler = None
    use_signal = threading.current_thread() is threading.main_thread()
    if use_signal:
        previous_handler = signal.signal(signal.SIGTERM, cancelled)
    try:
        email = chatgpt_auth.sign_in(say=say, read_line=read_line, timeout=timeout, port=port)
        result = {"provider": "chatgpt", "signed_in": True, "email": email}
        emit("subscription_status", result)
        return result
    finally:
        done.set()
        if use_signal:
            signal.signal(signal.SIGTERM, previous_handler)
        for child in attempt.iterdir():
            if child.is_file() and not child.is_symlink():
                child.unlink(missing_ok=True)
        attempt.rmdir()
