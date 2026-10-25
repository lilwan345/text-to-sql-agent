"""csv_to_db.py turns a CSV into a one-table SQLite file the agent can query."""

import sqlite3

import agent
import csv_to_db


def test_csv_becomes_a_queryable_table(tmp_path, capsys):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("region,quantity\nWest,3\nEast,1\nWest,2\n", encoding="utf-8")

    db_path = csv_to_db.csv_to_db(str(csv_path))

    assert db_path == str(tmp_path / "sales.db")
    rows = sqlite3.connect(db_path).execute(
        "SELECT region, SUM(quantity) FROM sales GROUP BY region ORDER BY region"
    ).fetchall()
    assert rows == [("East", 1), ("West", 5)]  # TEXT columns still sum as numbers
    assert "rows: 3" in capsys.readouterr().out


def test_converted_file_works_with_the_agent_tools(tmp_path, capsys):
    csv_path = tmp_path / "orders.csv"
    csv_path.write_text("id,total\n1,9.5\n2,10.5\n", encoding="utf-8")
    db_path = csv_to_db.csv_to_db(str(csv_path))

    assert 'CREATE TABLE "orders"' in agent.get_schema(db_path)
    assert agent.run_sql(db_path, "SELECT SUM(total) FROM orders").splitlines()[1] == "20.0"


def test_running_it_again_replaces_the_table(tmp_path, capsys):
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("a\n1\n", encoding="utf-8")
    csv_to_db.csv_to_db(str(csv_path))
    csv_path.write_text("a\n1\n2\n", encoding="utf-8")
    db_path = csv_to_db.csv_to_db(str(csv_path))

    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
