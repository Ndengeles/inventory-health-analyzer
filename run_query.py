"""
run_query.py — run a .sql file against inventory.db and print the result.

Usage:
    python run_query.py sql/01_service_level.sql

Uses Python's built-in sqlite3 module, so there's nothing to install.
"""

import sqlite3
import sys
from pathlib import Path

DB = "inventory.db"


def main():
    # 1. Which .sql file did the user ask for?
    if len(sys.argv) < 2:
        print("Usage: python run_query.py <path-to-.sql-file>")
        sys.exit(1)

    sql_path = Path(sys.argv[1])
    if not sql_path.exists():
        print(f"File not found: {sql_path}")
        sys.exit(1)

    sql = sql_path.read_text(encoding="utf-8")

    # 2. Connect to the database and run the query.
    conn = sqlite3.connect(DB)
    cursor = conn.execute(sql)

    # 3. Print column headers, then every row.
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    print(" | ".join(columns))
    print("-" * 40)
    for row in rows:
        print(" | ".join(str(value) for value in row))

    conn.close()


if __name__ == "__main__":
    main()
