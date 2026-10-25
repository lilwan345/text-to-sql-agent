"""
Offline tests for the two read-only safety layers and the result formatting.
No API key needed:  python -m pytest tests/
"""

import os
import shutil
import sqlite3

import pytest

import agent

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHINOOK = os.path.join(ROOT, "Chinook.db")


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM Track",
        "  select Name from Artist limit 3  ",
        "SELECT 1;",  # one trailing semicolon is fine
        "WITH t AS (SELECT * FROM Track) SELECT COUNT(*) FROM t",
        # whole-word matching: a column named CreateDate is not CREATE
        "SELECT InvoiceDate AS CreateDate FROM Invoice LIMIT 1",
    ],
)
def test_read_only_check_allows_selects(query):
    agent.assert_read_only(query)  # does not raise


@pytest.mark.parametrize(
    "query, message",
    [
        ("SELECT 1; DROP TABLE Track", "single statement"),
        ("DELETE FROM Track", "Only SELECT"),
        ("UPDATE Track SET Name = 'x'", "Only SELECT"),
        ("PRAGMA table_info(Track)", "Only SELECT"),
        ("ATTACH DATABASE 'x.db' AS x", "Only SELECT"),
        ("", "Only SELECT"),
        ("WITH t AS (SELECT 1) DELETE FROM Track", "DELETE"),
    ],
)
def test_read_only_check_rejects_everything_else(query, message):
    with pytest.raises(ValueError, match=message):
        agent.assert_read_only(query)


def test_database_is_opened_read_only(tmp_path, monkeypatch):
    """Layer 2: even if a write slipped past the keyword check, SQLite refuses it."""
    db = tmp_path / "copy.db"
    shutil.copy(CHINOOK, db)
    monkeypatch.setattr(agent, "assert_read_only", lambda q: None)  # disable layer 1

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        agent.run_sql(str(db), "DELETE FROM Track")

    count = sqlite3.connect(db).execute("SELECT COUNT(*) FROM Track").fetchone()[0]
    assert count == 3503


def test_run_sql_formats_a_text_table():
    out = agent.run_sql(CHINOOK, "SELECT Name, GenreId FROM Genre ORDER BY GenreId LIMIT 2")
    assert out.splitlines() == ["Name | GenreId", "Rock | 1", "Jazz | 2"]


def test_run_sql_reports_empty_results():
    out = agent.run_sql(CHINOOK, "SELECT * FROM Customer WHERE Country = 'Antarctica'")
    assert "0 rows" in out


def test_run_sql_caps_large_results():
    out = agent.run_sql(CHINOOK, "SELECT TrackId FROM Track")
    lines = out.splitlines()
    assert len(lines) == 1 + agent.MAX_ROWS + 1  # header + capped rows + note
    assert f"first {agent.MAX_ROWS} of 3503 rows" in lines[-1]


def test_schema_lists_every_table():
    schema = agent.get_schema(CHINOOK)
    for table in ("Album", "Artist", "Customer", "Genre", "Invoice", "InvoiceLine", "Track"):
        assert f"CREATE TABLE [{table}]" in schema
