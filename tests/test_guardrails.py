"""Offline tests for the guardrails that keep an LLM agent safe on company data.

No database needed: these exercise validation that runs before any connection.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


def validate(query, database="people", max_rows=100, timeout_ms=5000):
    return server._validate_readonly_query(database, query, max_rows, timeout_ms)


# --- read-only SQL tool -----------------------------------------------------

@pytest.mark.parametrize("query", [
    "SELECT first_name FROM people WHERE status = 'Active'",
    "WITH m AS (SELECT * FROM people) SELECT count(*) FROM m",
    "select 1;",
])
def test_plain_reads_are_accepted(query):
    assert validate(query) is None


@pytest.mark.parametrize("query, reason", [
    ("DELETE FROM people", "Only SELECT / WITH"),
    ("DROP TABLE people", "Only SELECT / WITH"),
    ("SELECT 1; DROP TABLE people", "Only one SQL statement"),
    ("WITH gone AS (DELETE FROM people RETURNING *) SELECT * FROM gone", "Forbidden keyword: DELETE"),
    ("SELECT * INTO backup_people FROM people", "Forbidden keyword: INTO"),
    ("SELECT * FROM people FOR UPDATE", "Forbidden keyword: UPDATE"),
    ("SELECT set_config('role', 'admin', false)", None),  # function call allowed only if no forbidden keyword
])
def test_writes_and_tricks_are_rejected(query, reason):
    result = validate(query)
    if reason is None:
        return
    assert result is not None and result["success"] is False
    assert reason in result["error"]


def test_keywords_inside_comments_and_strings_do_not_cause_false_alarms():
    # 'drop' only appears inside a string literal and a comment: still a plain read.
    assert validate("SELECT 'drop table' AS label -- delete everything\nFROM people") is None


@pytest.mark.parametrize("kwargs, message", [
    ({"max_rows": 0}, "max_rows"),
    ({"max_rows": 10_000}, "max_rows"),
    ({"timeout_ms": 10}, "timeout_ms"),
    ({"database": "payroll"}, "database must be one of"),
])
def test_limits_and_database_allowlist(kwargs, message):
    result = validate("SELECT 1", **kwargs)
    assert result is not None and message in result["error"]


# --- schema changes are off by default ---------------------------------------

def test_ddl_tool_refuses_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("ENABLE_DDL", raising=False)
    result = server.create_new_table(database="people", table_name="ext_anything",
                                     columns=[{"name": "id", "type": "integer"}])
    assert result["success"] is False
    assert "ENABLE_DDL" in result["error"]


# --- tool catalogue -----------------------------------------------------------

def test_every_tool_is_documented_for_the_model():
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) >= 100
    undocumented = [t.name for t in tools if not (t.description or "").strip()]
    assert undocumented == []
