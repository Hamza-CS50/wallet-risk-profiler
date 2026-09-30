"""
Generate a mock digital-wallet dataset: 500 users, exactly 10,000 transactions.

Each user is assigned a behavioural segment (normal / heavy / risky / dormant),
which drives how often they transact, how much, how often payments fail, and
what balance they hold. That gives the SQL filters and risk dashboard real
signal to find instead of uniform noise.

Outputs:  data/users.csv, data/transactions.csv
Run:      python generate_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_USERS = 500
N_TXNS = 10_000
END_DATE = pd.Timestamp("2026-09-30 23:59:59")
WINDOW_DAYS = 180
OUT = Path(__file__).parent / "data"

rng = np.random.default_rng(SEED)

# --- Segment definitions ---------------------------------------------------
# share: fraction of users | activity: relative txn volume weight
# fail_p: base probability a payment fails for non-balance reasons
# amt_mu/amt_sigma: lognormal params for txn amount (PKR)
# start_bal: (low, high) starting balance | night_p: chance a txn happens 0-5am
SEGMENTS = {
    "normal":  dict(share=0.60, activity=1.0, fail_p=0.03, amt_mu=7.0, amt_sigma=0.9, start_bal=(15_000, 120_000), night_p=0.05),
    "heavy":   dict(share=0.15, activity=3.0, fail_p=0.04, amt_mu=7.4, amt_sigma=1.0, start_bal=(60_000, 300_000), night_p=0.10),
    "risky":   dict(share=0.15, activity=1.8, fail_p=0.12, amt_mu=7.6, amt_sigma=1.2, start_bal=(3_000, 25_000),    night_p=0.35),
    "dormant": dict(share=0.10, activity=0.25, fail_p=0.05, amt_mu=6.8, amt_sigma=0.8, start_bal=(5_000, 40_000),  night_p=0.03),
}
TXN_TYPES = ["top_up", "merchant_payment", "p2p_transfer", "bill_payment", "withdrawal"]
TYPE_P = [0.22, 0.34, 0.20, 0.14, 0.10]
FAIL_REASONS = ["network_error", "card_declined", "limit_exceeded", "suspected_fraud", "invalid_recipient"]
FAIL_REASON_P = {
    "normal":  [0.45, 0.25, 0.15, 0.03, 0.12],
    "heavy":   [0.40, 0.20, 0.25, 0.03, 0.12],
    "risky":   [0.15, 0.30, 0.15, 0.30, 0.10],
    "dormant": [0.50, 0.25, 0.10, 0.05, 0.10],
}
CHANNELS = ["mobile_app", "web", "qr_code", "agent"]
COUNTRIES = ["PK", "PK", "PK", "PK", "AE", "GB", "SA"]
FIRST = ["Ali", "Ayesha", "Bilal", "Fatima", "Hassan", "Zainab", "Omar", "Sana", "Usman", "Hira",
         "Ahmed", "Maryam", "Danish", "Noor", "Saad", "Iqra", "Faizan", "Areeba", "Talha", "Mahnoor"]
LAST = ["Khan", "Ahmed", "Malik", "Sheikh", "Qureshi", "Siddiqui", "Raza", "Baig", "Hussain", "Farooqui"]


def build_users() -> pd.DataFrame:
    names = list(SEGMENTS)
    shares = [SEGMENTS[s]["share"] for s in names]
    segment = rng.choice(names, size=N_USERS, p=shares)
    signup_offset = rng.integers(200, 1500, size=N_USERS)
    return pd.DataFrame({
        "user_id": np.arange(1, N_USERS + 1),
        "full_name": [f"{rng.choice(FIRST)} {rng.choice(LAST)}" for _ in range(N_USERS)],
        "country": rng.choice(COUNTRIES, size=N_USERS),
        "signup_date": (END_DATE.normalize() - pd.to_timedelta(signup_offset, unit="D")).date,
        "account_tier": rng.choice(["basic", "standard", "premium"], size=N_USERS, p=[0.5, 0.35, 0.15]),
        "segment": segment,  # generator-only label; handy to sanity check the risk model
    })


def allocate_txn_counts(users: pd.DataFrame) -> np.ndarray:
    """Split exactly N_TXNS across users, weighted by segment activity."""
    weights = users["segment"].map(lambda s: SEGMENTS[s]["activity"]).to_numpy()
    weights = weights * rng.gamma(shape=2.0, scale=0.5, size=N_USERS)  # per-user variation
    counts = np.floor(weights / weights.sum() * N_TXNS).astype(int)
    counts = np.maximum(counts, 1)
    diff = N_TXNS - counts.sum()
    while diff != 0:  # fix rounding so the total is exactly N_TXNS
        idx = rng.choice(N_USERS, size=abs(diff), replace=True, p=weights / weights.sum())
        for i in idx:
            if diff > 0:
                counts[i] += 1
            elif counts[i] > 1:
                counts[i] -= 1
        diff = N_TXNS - counts.sum()
    return counts


def sample_timestamps(n: int, night_p: float) -> pd.DatetimeIndex:
    days = rng.integers(0, WINDOW_DAYS, size=n)
    is_night = rng.random(n) < night_p
    hours = np.where(is_night, rng.integers(0, 6, size=n), rng.choice(np.arange(7, 24), size=n))
    secs = rng.integers(0, 3600, size=n)
    start = END_DATE.normalize() - pd.Timedelta(days=WINDOW_DAYS - 1)
    ts = start + pd.to_timedelta(days, unit="D") + pd.to_timedelta(hours, unit="h") + pd.to_timedelta(secs, unit="s")
    return pd.DatetimeIndex(np.sort(ts.values))


def simulate_user(user_id: int, segment: str, n: int) -> list[dict]:
    cfg = SEGMENTS[segment]
    balance = float(rng.uniform(*cfg["start_bal"]))
    timestamps = sample_timestamps(n, cfg["night_p"])
    types = rng.choice(TXN_TYPES, size=n, p=TYPE_P)
    amounts = np.round(rng.lognormal(cfg["amt_mu"], cfg["amt_sigma"], size=n), 2)
    rows = []
    for ts, ttype, amt in zip(timestamps, types, amounts):
        status, reason = "success", None
        if ttype == "top_up":
            # top-ups are sized to refill the wallet; they can still fail (card_declined etc.)
            amt = round(float(amt) * 1.6, 2)
            if rng.random() < cfg["fail_p"]:
                status = "failed"
        else:
            if amt > balance:
                status, reason = "failed", "insufficient_funds"
            elif rng.random() < cfg["fail_p"]:
                status = "failed"
        if status == "failed" and reason is None:
            reason = rng.choice(FAIL_REASONS, p=FAIL_REASON_P[segment])
        if status == "success":
            balance += amt if ttype == "top_up" else -amt
        rows.append(dict(
            user_id=user_id,
            txn_timestamp=ts,
            txn_type=ttype,
            amount=float(amt),
            status=status,
            failure_reason=reason,
            balance_after=round(balance, 2),
            channel=rng.choice(CHANNELS),
        ))
    return rows


def main() -> None:
    OUT.mkdir(exist_ok=True)
    users = build_users()
    counts = allocate_txn_counts(users)

    rows: list[dict] = []
    for (uid, seg), n in zip(users[["user_id", "segment"]].itertuples(index=False), counts):
        rows.extend(simulate_user(int(uid), seg, int(n)))

    txns = pd.DataFrame(rows).sort_values("txn_timestamp").reset_index(drop=True)
    txns.insert(0, "txn_id", np.arange(1, len(txns) + 1))
    assert len(txns) == N_TXNS, len(txns)

    users.to_csv(OUT / "users.csv", index=False)
    txns.to_csv(OUT / "transactions.csv", index=False)

    print(f"users: {len(users):,} | transactions: {len(txns):,}")
    print(f"overall failure rate: {(txns.status == 'failed').mean():.1%}")
    print(txns.merge(users[["user_id", "segment"]], on="user_id")
              .groupby("segment")
              .agg(txns=("txn_id", "count"),
                   fail_rate=("status", lambda s: (s == "failed").mean()),
                   avg_balance=("balance_after", "mean"))
              .round(3))


if __name__ == "__main__":
    main()
