"""Desktop subscription delegates credentials and verifies callback delivery."""
import json
import stat
import threading
import time
from urllib.parse import urlencode

import pytest

from desktop import subscription


@pytest.mark.unit
def test_status_and_logout_only_use_public_upstream_functions(monkeypatch):
    email = ["investor@example.test"]
    monkeypatch.setattr(subscription.chatgpt_auth, "signed_in_as", lambda: email[0])
    calls = []

    def sign_out():
        calls.append("revoked")
        email[0] = None
        return "revoked"

    monkeypatch.setattr(subscription.chatgpt_auth, "sign_out", sign_out)
    assert subscription.status() == {
        "provider": "chatgpt", "signed_in": True, "email": "investor@example.test"}
    assert subscription.logout() == {
        "provider": "chatgpt", "signed_in": False, "email": None, "result": "revoked"}
    assert calls == ["revoked"]


@pytest.mark.unit
def test_models_are_the_models_the_signed_in_plan_returns(monkeypatch):
    monkeypatch.setattr(subscription.chatgpt_auth, "list_models", lambda: [("Plan model", "plan-id")])
    assert subscription.list_models() == {"models": [{"label": "Plan model", "id": "plan-id"}]}


@pytest.mark.unit
def test_pasted_callback_reaches_original_login_and_is_cleaned(tmp_path, monkeypatch):
    events = []
    pasted = []
    response = "http://127.0.0.1:1455/auth/callback?state=expected&code=single-use-code"

    def original_login(*, say, read_line, timeout, port):
        assert timeout == 20 and port == 1455
        say(subscription.chatgpt_auth.AUTHORIZE_URL + "?" + urlencode({
            "state": "expected", "redirect_uri": "http://127.0.0.1:1455/auth/callback"}))
        pasted.append(read_line())
        return "investor@example.test"

    monkeypatch.setattr(subscription.chatgpt_auth, "sign_in", original_login)

    def emit(kind, payload):
        events.append((kind, payload))
        if kind == "subscription_url":
            attempt = tmp_path / payload["attempt_id"]
            assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
            assert stat.S_IMODE((attempt / "pending.json").stat().st_mode) == 0o600
            result = subscription.submit_redirect(payload["attempt_id"], response, runtime=tmp_path)
            assert result["accepted"]
            assert "code" not in json.dumps(result)
            assert stat.S_IMODE((attempt / "redirect.json").stat().st_mode) == 0o600

    result = subscription.login(emit, timeout=20, runtime=tmp_path)
    assert result["signed_in"] and result["email"] == "investor@example.test"
    assert pasted == [response]
    assert not list(tmp_path.iterdir())
    assert "single-use-code" not in json.dumps(events)
    assert events[-1][0] == "subscription_status"


@pytest.mark.unit
def test_native_loopback_can_finish_without_a_pasted_callback(tmp_path, monkeypatch):
    calls = []

    def original_login(*, say, read_line, timeout, port):
        calls.append(port)
        # Upstream owns its HTTP listener; desktop does not need an additional
        # listener or token exchanger when that listener receives the answer.
        say("Continue with ChatGPT")
        return "investor@example.test"

    monkeypatch.setattr(subscription.chatgpt_auth, "sign_in", original_login)
    assert subscription.login(lambda *_: None, runtime=tmp_path, port=0)["signed_in"]
    assert calls == [0]
    assert not list(tmp_path.iterdir())


def _pending(tmp_path, *, expiry=None):
    attempt_id = "a" * 32
    attempt = tmp_path / attempt_id
    attempt.mkdir()
    (attempt / "pending.json").write_text(json.dumps({
        "redirect_uri": "http://127.0.0.1:1455/auth/callback", "state": "expected",
        "expires_at": expiry if expiry is not None else time.time() + 60}))
    return attempt_id, attempt


@pytest.mark.unit
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:1455/auth/callback?state=stale&code=secret",
    "http://127.0.0.1:1456/auth/callback?state=expected&code=secret",
    "http://localhost:1455/auth/callback?state=expected&code=secret",
    "https://127.0.0.1:1455/auth/callback?state=expected&code=secret",
    "http://example.test:1455/auth/callback?state=expected&code=secret",
    "http://127.0.0.1:1455/other?state=expected&code=secret",
    "http://user@127.0.0.1:1455/auth/callback?state=expected&code=secret",
    "http://127.0.0.1:1455/auth/callback?state=expected&code=secret#fragment",
    "http://127.0.0.1:1455/auth/callback?state=expected",
    "http://127.0.0.1:1455/auth/callback?state=expected&state=extra&code=secret",
    "http://127.0.0.1:bad/auth/callback?state=expected&code=secret",
])
def test_stale_or_unrelated_pastes_are_refused_without_echo(tmp_path, url):
    attempt_id, attempt = _pending(tmp_path)
    with pytest.raises(ValueError, match="does not match") as error:
        subscription.submit_redirect(attempt_id, url, runtime=tmp_path)
    assert "secret" not in str(error.value)
    assert not (attempt / "redirect.json").exists()


@pytest.mark.unit
def test_expired_or_traversal_attempt_is_refused(tmp_path):
    attempt_id, attempt = _pending(tmp_path, expiry=0)
    with pytest.raises(ValueError):
        subscription.submit_redirect(attempt_id,
            "http://127.0.0.1:1455/auth/callback?state=expected&code=secret", runtime=tmp_path)
    with pytest.raises(ValueError, match="Invalid"):
        subscription.submit_redirect("../elsewhere", "anything", runtime=tmp_path)
    assert not (attempt / "redirect.json").exists()


@pytest.mark.unit
def test_failed_or_cancelled_login_cleans_pending_state(tmp_path, monkeypatch):
    def fail(*, say, **kwargs):
        say(subscription.chatgpt_auth.AUTHORIZE_URL + "?" + urlencode({
            "state": "expected", "redirect_uri": "http://127.0.0.1:1455/auth/callback"}))
        raise subscription.SubscriptionCancelled("cancelled")
    monkeypatch.setattr(subscription.chatgpt_auth, "sign_in", fail)
    with pytest.raises(subscription.SubscriptionCancelled):
        subscription.login(lambda *_: None, runtime=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.unit
def test_reader_is_released_after_native_login_finishes(tmp_path, monkeypatch):
    finished = threading.Event()

    def original_login(*, read_line, **kwargs):
        def reader():
            with pytest.raises(EOFError):
                read_line()
            finished.set()
        threading.Thread(target=reader, daemon=True).start()
        return "investor@example.test"

    monkeypatch.setattr(subscription.chatgpt_auth, "sign_in", original_login)
    subscription.login(lambda *_: None, runtime=tmp_path)
    assert finished.wait(1)
