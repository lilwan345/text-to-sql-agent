"""
csv_to_db.py — turn a .csv file into a SQLite .db the agent can query.

You CANNOT just rename data.csv to data.db — a CSV is plain text, a .db is a
binary database. This script actually loads the rows in.

Usage:
    ./.venv/bin/python csv_to_db.py path/to/data.csv

That creates  data.db  next to your CSV, with one table named after the file
(e.g. sales.csv -> sales.db, table "sales"). Then point the agent at it by
passing it on the command line:  python agent.py data.db
"""

import csv
import os
import sqlite3
import sys


def csv_to_db(csv_path: str) -> str:
    # Derive names: /foo/sales.csv -> db "/foo/sales.db", table "sales".
    base = os.path.splitext(csv_path)[0]   # drop the ".csv"
    db_path = base + ".db"
    table = os.path.basename(base)         # just the filename, no folder

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)              # first row = column names
        rows = list(reader)               # everything else = data rows

    conn = sqlite3.connect(db_path)
    # Build  CREATE TABLE "sales" ("col1" TEXT, "col2" TEXT, ...)
    # We store every column as TEXT for simplicity. SQLite still lets you do
    # SUM()/AVG() on numbers stored as text, so queries work fine.
    cols = ", ".join(f'"{name}" TEXT' for name in header)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'CREATE TABLE "{table}" ({cols})')

    # Insert all rows in one go. The "?" placeholders are filled per row —
    # this is the safe way to insert data (no SQL injection).
    placeholders = ", ".join("?" for _ in header)
    conn.executemany(
        f'INSERT INTO "{table}" VALUES ({placeholders})', rows
    )
    conn.commit()
    conn.close()

    print(f"Created {db_path}")
    print(f"  table: {table}")
    print(f"  columns: {', '.join(header)}")
    print(f"  rows: {len(rows)}")
    print(f"\nNow run the agent on it:  python agent.py {os.path.basename(db_path)}")
    return db_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python csv_to_db.py path/to/data.csv")
        sys.exit(1)
    csv_to_db(sys.argv[1])
