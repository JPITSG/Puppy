#!/usr/bin/env python3
"""Account/bucket provenance tests using invented metadata and no engine runs."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

ROOT = private_root("quota-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from puppy import quota
from puppy.drivers import claude, codex


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def codex_auth(user="user-a", account="workspace-a", nonce="one"):
    claims = {"iss": "https://auth.openai.com", "nonce": nonce,
              "https://api.openai.com/auth": {
                  "chatgpt_account_id": account, "chatgpt_user_id": user}}
    token = "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
            "tokens": {"account_id": account, "id_token": token,
                       "access_token": "private-access", "refresh_token": "private-refresh"}}


def claude_auth(user="person-a", org="organization-a"):
    return {"oauthAccount": {"accountUuid": user, "organizationUuid": org,
            "emailAddress": user + "@example.invalid", "organizationName": org}}


def init_reply(account):
    return json.dumps({"type": "control_response", "response": {
        "request_id": "init_1", "subtype": "success", "response": {"account": account}}})


def rate_event(used=0.4, bucket="seven_day", reset=None):
    return json.dumps({"type": "rate_limit_event", "rate_limit_info": {
        "rateLimitType": "seven_day_overage_included", "utilization": 0.99,
        "unifiedWindows": {bucket: {"utilization": used,
                                    "resetsAt": reset or time.time() + 3600}}}})


async def checks():
    now = time.time()
    with patch("puppy.state_stream.wake") as wake:
        monitor = quota.Monitor("provider", "bucket")
        monitor.observe("A", "A", quota.weekly_sample(42, now + 500, now))
        assert monitor.payload("A")["sample"]["used_percent"] == 42
        monitor.observe("A", "A", quota.weekly_sample(12, now + 500, now - 10))
        assert monitor.payload("A")["sample"]["used_percent"] == 42
        monitor.observe("B", "A", quota.weekly_sample(55, now + 500, now + 1))
        assert monitor.payload("B")["sample"] is None
        assert monitor.payload("A")["sample"] is None  # switching back cannot resurrect it
        monitor.observe("A", "A", quota.weekly_sample(42, now - 1, now))
        assert monitor.payload("A")["sample"] is None
        assert wake.called
    assert quota.weekly_sample(True, now) is None
    assert quota.weekly_sample(float("nan"), now) is None
    assert quota.weekly_sample(-1, now) is None
    assert quota.account_key("p", "user", None) is None
    assert quota.account_key("a", "b", "c") != quota.account_key("a", "bc")

    auth_path = ROOT / "codex" / "auth.json"
    write(auth_path, codex_auth())
    identity = codex._quota_identity()
    assert identity and len(identity) == 64
    write(auth_path, codex_auth(nonce="rotated-token"))
    assert codex._quota_identity() == identity
    write(auth_path, codex_auth(user="user-b"))
    assert codex._quota_identity() != identity
    write(auth_path, codex_auth(account="workspace-b"))
    assert codex._quota_identity() != identity
    mismatched = codex_auth()
    mismatched["tokens"]["account_id"] = "workspace-b"
    write(auth_path, mismatched)
    assert codex._quota_identity() is None
    for bad in ({}, {"auth_mode": "apikey"}, {"auth_mode": "chatgpt", "tokens": []},
                {"auth_mode": "chatgpt", "tokens": {"id_token": "not-a-token"}}):
        write(auth_path, bad)
        assert codex._quota_identity() is None
    write(auth_path, codex_auth())
    with patch.dict(os.environ, {"OPENAI_API_KEY": "private-api-key"}):
        assert codex._quota_identity() is None

    driver = codex.CodexDriver()
    context = driver.turn_context({"cwd": str(ROOT)}, True, "hello", "pin")
    limits = {"limitId": "codex", "secondary": {
        "windowDurationMins": 10080, "usedPercent": 42, "resetsAt": now + 3600}}
    with patch("puppy.state_stream.wake"):
        driver.parse_line(json.dumps({"method": "account/rateLimits/updated", "params": {
            "rateLimits": limits}}), context)
        monitor = driver._extra_status()["usage_monitor"]
        assert monitor["account"] == identity and monitor["sample"]["used_percent"] == 42
        assert all(secret not in json.dumps(monitor) for secret in
                   ("private-access", "private-refresh", "user-a", "workspace-a"))
        # Same duration, different bucket must not replace Codex's week.
        other = {**limits, "limitId": "other-model", "secondary": {
            **limits["secondary"], "usedPercent": 99}}
        driver._observe_quota(other, identity, now + 1)
        assert driver._extra_status()["usage_monitor"]["sample"]["used_percent"] == 42
        # A slow account read must not overwrite a live update which arrived meanwhile.
        driver._observe_quota(limits, identity, now - 20)
        assert driver._extra_status()["usage_monitor"]["sample"]["observed_at"] > now - 20
        write(auth_path, codex_auth(user="user-b"))
        driver._observe_quota(limits, context["quota_identity"])
        assert driver._extra_status()["usage_monitor"]["sample"] is None
        auth_path.unlink()
        assert driver._extra_status()["usage_monitor"]["account"] is None

    config_path = ROOT / "claude" / ".claude.json"
    write(config_path, claude_auth())
    write(ROOT / "claude" / ".credentials.json", {"secret": "never-read"})
    owner = claude._quota_account()
    assert owner["key"]
    driver = claude.ClaudeDriver()
    verdict = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
               "orgId": owner["org_id"], "email": owner["email"]}
    async def auth_probe(*args):
        return 0, json.dumps(verdict)
    with patch.object(driver, "_run_probe", auth_probe):
        await driver._auth_status()
    assert driver._extra_status()["usage_monitor"]["account"] == owner["key"]
    account = {"apiProvider": "firstParty", "subscriptionType": "max",
               "email": owner["email"], "organization": owner["organization"]}
    context = driver.turn_context({}, True, "hello", "pin")
    with patch("puppy.state_stream.wake"):
        driver.parse_line(init_reply(account), context)
        driver.parse_line(rate_event(), context)
        monitor = driver._extra_status()["usage_monitor"]
        assert monitor["sample"]["used_percent"] == 40
        assert monitor["account"] == owner["key"]
        assert "example.invalid" not in json.dumps(monitor)
        driver.parse_line(rate_event(0.99, "seven_day_overage_included"), context)
        assert driver._extra_status()["usage_monitor"]["sample"]["used_percent"] == 40
        for override in ({"apiProvider": "bedrock"}, {"apiKeySource": "apiKeyHelper"},
                         {"tokenSource": "CLAUDE_CODE_OAUTH_TOKEN"}, {"email": "different"}):
            other_context = driver.turn_context({}, True, "other", "pin")
            driver.parse_line(init_reply({**account, **override}), other_context)
            driver.parse_line(rate_event(0.99), other_context)
            assert driver._extra_status()["usage_monitor"]["sample"]["used_percent"] == 40
        write(config_path, claude_auth(org="organization-b"))
        driver.parse_line(rate_event(0.99), context)
        assert driver._extra_status()["usage_monitor"]["sample"] is None
        assert driver._extra_status()["usage_monitor"]["account"] is None
        # A reply for the old login cannot establish the new identity.
        driver.parse_line(init_reply(account), context)
        assert driver._quota_identity() is None
    with patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://example.invalid"}):
        assert not claude._quota_account()
    (ROOT / "claude" / ".credentials.json").unlink()
    assert not claude._quota_account()
    print("PASS: quota identities, bucket isolation, login races, freshness and privacy")


try:
    with patch.dict(os.environ, {"CODEX_HOME": str(ROOT / "codex"),
                                 "CLAUDE_CONFIG_DIR": str(ROOT / "claude")}):
        asyncio.run(checks())
finally:
    shutil.rmtree(ROOT)
