# Digital Wallet Risk Profiler

A small end-to-end data project: synthetic wallet data -> PostgreSQL analysis -> Streamlit risk dashboard.

| Step | What | File |
|---|---|---|
| 1 | Generate 500 users / exactly 10,000 transactions with pandas + NumPy | `generate_data.py` |
| 2 | PostgreSQL schema, bulk load, risk view | `sql/01_schema.sql`, `load_to_postgres.py`, `sql/02_risk_view.sql` |
| 3 | SQL queries filtering by frequency, average balance, failed payments | `sql/03_queries.sql` |
| 4 | Streamlit dashboard: per-user risk profile + watchlist filter | `app.py`, `risk.py` |

## Quick start

```bash
pip install -r requirements.txt
python generate_data.py          # writes data/users.csv and data/transactions.csv
streamlit run app.py             # works immediately, reads the CSVs
```

### With PostgreSQL (recommended; this is the full version)

```bash
createdb wallet_risk
export DATABASE_URL="postgresql://USER:PASSWORD@localhost:5432/wallet_risk"
python load_to_postgres.py                     # schema + data + user_risk_metrics view
psql "$DATABASE_URL" -f sql/03_queries.sql     # run the analysis queries
streamlit run app.py                           # now reads metrics from the SQL view
```

If `DATABASE_URL` is set, the dashboard pulls metrics from the `user_risk_metrics` view.
If not, it computes identical metrics in pandas (`risk.py`). I verified the two agree
(0 tier mismatches; differences are rounding only).

## The data

- **Users**: country, tier, signup date, plus a hidden `segment` label (normal / heavy / risky / dormant) used only by the generator.
- **Transactions**: timestamp (last 180 days), type, amount, status, failure reason, running `balance_after`, channel.
- Balances are simulated per user in time order. A payment larger than the balance fails with
  `insufficient_funds`, so failures and low balances are naturally linked, as in real data.
- Seeded (`SEED = 42`), so results are reproducible.

## The SQL (`sql/03_queries.sql`)

| Query | Purpose |
|---|---|
| Q1 | Frequency: 10+ transactions in the last 30 days |
| Q2 | Average balance below a threshold (with minimum sample size) |
| Q3 | Failure rate above 15% using `FILTER` aggregates |
| Q4 | Combined watchlist: active + thin balance + frequent failures (CTEs) |
| Q5 | Relative thresholds with `NTILE` window functions (worst failure decile AND lowest balance quartile) |
| Q6 | Longest run of consecutive failures (gaps-and-islands) |
| Q7 | Failure reasons among High-risk users |
| Q8 | Risk tier summary from the view |

"Last 30 days" is measured from the newest transaction in the data rather than `now()`, so results don't drift.

## Risk score (0-100, rule-based)

| Signal | Max points | Full points when |
|---|---|---|
| Failed payments | 40 | failure rate >= 25% |
| Low average balance | 25 | average balance near 0 (0 pts at >= 20,000) |
| 30-day frequency | 20 | >= 12 transactions in 30 days |
| Night-time share | 15 | >= 30% of transactions between 00:00 and 05:59 |

Tiers: **High** >= 55, **Medium** 30-54, **Low** < 30.

On the generated data, 59 of the 79 "risky" users land in High and only 7 of 421 other users do.
That check only shows the score recovers the patterns the generator planted. It is not evidence
that the score would work on real wallet data.

## Dashboard

- **User risk profile**: gauge, KPIs, score breakdown, comparison with the median user, balance timeline with failed payments marked, monthly activity, failure reasons, recent failures.
- **Watchlist filter**: sliders for frequency, balance and failure rate (interactive version of Q4) plus a scatter of all users.

## Ideas to extend

- Add a rolling 7-day failure rate and alert when it spikes.
- Replace the hand-tuned weights with logistic regression on labelled fraud/chargeback data.
- Add a scheduled job that refreshes a materialized version of the view.
