"""
Create the schema, bulk-load the CSVs and create the risk view in PostgreSQL.

Usage:
    export DATABASE_URL="postgresql://user:password@localhost:5432/wallet_risk"
    python load_to_postgres.py
"""
import os
import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).parent
DATABASE_URL = os.environ.get("DATABASE_URL")


def copy_csv(cur, table: str, csv_path: Path, columns: str) -> None:
    with open(csv_path, "r", encoding="utf-8") as f:
        cur.copy_expert(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, HEADER true)", f)


def main() -> None:
    if not DATABASE_URL:
        sys.exit("Set DATABASE_URL first, e.g. postgresql://user:pass@localhost:5432/wallet_risk")

    with psycopg2.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "sql" / "01_schema.sql").read_text())
        copy_csv(cur, "users", ROOT / "data" / "users.csv",
                 "user_id, full_name, country, signup_date, account_tier, segment")
        copy_csv(cur, "transactions", ROOT / "data" / "transactions.csv",
                 "txn_id, user_id, txn_timestamp, txn_type, amount, status, failure_reason, balance_after, channel")
        cur.execute((ROOT / "sql" / "02_risk_view.sql").read_text())

        cur.execute("SELECT COUNT(*) FROM users")
        n_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM transactions")
        n_txns = cur.fetchone()[0]
    print(f"Loaded {n_users:,} users and {n_txns:,} transactions. View user_risk_metrics created.")


if __name__ == "__main__":
    main()
