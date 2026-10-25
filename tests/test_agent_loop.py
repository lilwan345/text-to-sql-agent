"""
Offline tests for the agent loop, using a fake client that plays back scripted
Claude replies. This checks the plumbing (tool calls, error feedback, step cap,
self-check, retries) without spending API credits.
"""

from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import agent

ROOT = Path(__file__).resolve().parent.parent


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_call(query, id="call_1"):
    return SimpleNamespace(type="tool_use", id=id, input={"query": query})


def reply(*blocks, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClient:
    """Stands in for anthropic.Anthropic(); returns scripted replies in order."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        next_reply = self.replies.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply


@pytest.fixture(autouse=True)
def chinook(monkeypatch):
    """Point the agent at the bundled Chinook.db wherever pytest is run from."""
    monkeypatch.setattr(agent, "DB_PATH", str(ROOT / "Chinook.db"))


def test_runs_the_sql_and_returns_the_final_answer():
    client = FakeClient([
        reply(tool_call("SELECT COUNT(*) FROM Track"), stop="tool_use"),
        reply(text("There are 3503 tracks.")),
    ])
    history = []
    answer = agent.ask("How many tracks?", client, "system", history, verify=False)

    assert answer == "There are 3503 tracks."
    # user question -> tool call -> tool result -> final answer
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    result = history[2]["content"][0]
    assert result["tool_use_id"] == "call_1"
    assert result["is_error"] is False
    assert result["content"].splitlines() == ["COUNT(*)", "3503"]


def test_sql_errors_are_sent_back_so_claude_can_retry():
    client = FakeClient([
        reply(tool_call("SELECT Length FROM Track", id="bad"), stop="tool_use"),
        reply(tool_call("SELECT MAX(Milliseconds) FROM Track", id="good"), stop="tool_use"),
        reply(text("The longest track is 5286953 ms.")),
    ])
    history = []
    agent.ask("Longest track?", client, "system", history, verify=False)

    first, second = history[2]["content"][0], history[4]["content"][0]
    assert first["is_error"] is True and "no such column" in first["content"]
    assert second["is_error"] is False and "5286953" in second["content"]


def test_write_attempts_are_refused_and_reported():
    client = FakeClient([
        reply(tool_call("DELETE FROM Track"), stop="tool_use"),
        reply(text("I can only read the data.")),
    ])
    history = []
    agent.ask("Delete all tracks", client, "system", history, verify=False)

    result = history[2]["content"][0]
    assert result["is_error"] is True
    assert "Only SELECT" in result["content"]


def test_step_cap_stops_a_runaway_loop():
    client = FakeClient(
        [reply(tool_call("SELECT 1"), stop="tool_use")] * agent.MAX_STEPS
    )
    answer = agent.ask("Loop forever", client, "system", [], verify=False)
    assert answer.startswith("(stopped")
    assert len(client.calls) == agent.MAX_STEPS


def test_self_check_warning_is_appended_to_the_answer():
    client = FakeClient([
        reply(tool_call("SELECT Name FROM Track ORDER BY UnitPrice DESC LIMIT 1"), stop="tool_use"),
        reply(text("The longest track is X.")),
        reply(text("WARN: sorts by UnitPrice, but length is Milliseconds.")),
    ])
    answer = agent.ask("Longest track?", client, "system", [], verify=True)
    assert "Self-check: sorts by UnitPrice" in answer
    assert "SELECT Name FROM Track ORDER BY UnitPrice" in client.calls[-1]["messages"][0]["content"]


def test_no_self_check_when_no_sql_was_run():
    client = FakeClient([reply(text("Could you say which metric you mean?"))])
    answer = agent.ask("Who is the best employee?", client, "system", [], verify=True)
    assert answer == "Could you say which metric you mean?"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "verdict, shown",
    [
        ("OK - uses Milliseconds.", "✓ Self-check passed: uses Milliseconds."),
        ("WARN: ambiguous metric.", "⚠️  Self-check: ambiguous metric."),
        ("Looks fine", "Self-check: Looks fine"),
    ],
)
def test_format_verdict(verdict, shown):
    assert agent.format_verdict(verdict) == shown


def test_transient_errors_are_retried(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    client = FakeClient([
        anthropic.APIConnectionError(request=request),
        reply(text("ok")),
    ])
    assert agent.call_claude(client, model="m").content[0].text == "ok"
    assert len(client.calls) == 2


def test_real_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request)
    client = FakeClient([
        anthropic.AuthenticationError("bad key", response=response, body=None),
    ])
    with pytest.raises(anthropic.AuthenticationError):
        agent.call_claude(client, model="m")
    assert len(client.calls) == 1
