"""
Data access + risk scoring shared by the dashboard.

Two data sources, chosen automatically:
  * PostgreSQL  - if the DATABASE_URL environment variable is set. User metrics
                  come straight from the `user_risk_metrics` SQL view.
  * CSV files   - otherwise (data/*.csv). Metrics are computed in pandas with
                  the exact same formula as the SQL view.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"

# Score components: name -> max points. Must match sql/02_risk_view.sql.
WEIGHTS = {
    "Failed payments": 40,
    "Low balance": 25,
    "High frequency": 20,
    "Night-time activity": 15,
}
TIER_CUTOFFS = {"High": 55, "Medium": 30}  # score >= cutoff


def tier_for(score: float) -> str:
    if score >= TIER_CUTOFFS["High"]:
        return "High"
    if score >= TIER_CUTOFFS["Medium"]:
        return "Medium"
    return "Low"


def score_components(failure_rate: float, avg_balance: float, txns_30d: float, night_share: float) -> dict:
    """Points contributed by each signal (they sum to the 0-100 risk score)."""
    return {
        "Failed payments": WEIGHTS["Failed payments"] * min(failure_rate / 0.25, 1),
        "Low balance": WEIGHTS["Low balance"] * min(max((20_000 - avg_balance) / 20_000, 0), 1),
        "High frequency": WEIGHTS["High frequency"] * min(txns_30d / 12.0, 1),
        "Night-time activity": WEIGHTS["Night-time activity"] * min(night_share / 0.30, 1),
    }


def compute_metrics_pandas(users: pd.DataFrame, txns: pd.DataFrame) -> pd.DataFrame:
    """Pandas equivalent of the user_risk_metrics SQL view."""
    ref_ts = txns["txn_timestamp"].max()
    recent = txns["txn_timestamp"] > ref_ts - pd.Timedelta(days=30)
    failed = txns["status"] == "failed"
    t = txns.assign(
        is_failed=failed.astype(int),
        is_recent=recent.astype(int),
        is_recent_failed=(failed & recent).astype(int),
        is_night=(txns["txn_timestamp"].dt.hour < 6).astype(int),
    )
    g = t.groupby("user_id").agg(
        total_txns=("txn_id", "count"),
        txns_30d=("is_recent", "sum"),
        avg_balance=("balance_after", "mean"),
        failed_txns=("is_failed", "sum"),
        failed_30d=("is_recent_failed", "sum"),
        failure_rate=("is_failed", "mean"),
        night_share=("is_night", "mean"),
    )
    g["avg_balance"] = g["avg_balance"].round(2)
    g["failure_rate"] = g["failure_rate"].round(4)
    g["night_share"] = g["night_share"].round(4)
    g["risk_score"] = g.apply(
        lambda r: round(sum(score_components(r.failure_rate, r.avg_balance, r.txns_30d, r.night_share).values()), 1),
        axis=1,
    )
    g["risk_tier"] = g["risk_score"].map(tier_for)
    out = users[["user_id", "full_name", "country", "account_tier"]].merge(g.reset_index(), on="user_id")
    return out


def using_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (user_metrics, transactions)."""
    if using_postgres():
        from sqlalchemy import create_engine

        url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg2://", 1)
        engine = create_engine(url)
        metrics = pd.read_sql("SELECT * FROM user_risk_metrics", engine)
        txns = pd.read_sql("SELECT * FROM transactions ORDER BY txn_timestamp", engine,
                           parse_dates=["txn_timestamp"])
        for col in ("avg_balance", "failure_rate", "night_share", "risk_score"):
            metrics[col] = metrics[col].astype(float)
        for col in ("amount", "balance_after"):
            txns[col] = txns[col].astype(float)
        return metrics, txns

    users = pd.read_csv(DATA_DIR / "users.csv")
    txns = pd.read_csv(DATA_DIR / "transactions.csv", parse_dates=["txn_timestamp"])
    return compute_metrics_pandas(users, txns), txns


def percentile_of(series: pd.Series, value: float) -> float:
    """Percent of users with a value at or below `value`."""
    return float((series <= value).mean() * 100)
