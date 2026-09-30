-- user_risk_metrics: one row per user with the three core signals
-- (frequency, average balance, failed payments) plus a 0-100 risk score.
--
-- "Today" is the latest transaction in the data, not now(), so results stay
-- reproducible no matter when the query is run.
--
-- Score weights (must match risk.py):
--   40 pts  failure rate        (25%+ failures = full points)
--   25 pts  low average balance (0 pts at >= 20,000; full points at 0)
--   20 pts  30-day frequency    (>= 12 txns in last 30 days = full points)
--   15 pts  night-time share    (>= 30% of txns between 00:00-05:59 = full points)

CREATE OR REPLACE VIEW user_risk_metrics AS
WITH ref AS (
    SELECT MAX(txn_timestamp) AS ref_ts FROM transactions
),
agg AS (
    SELECT
        t.user_id,
        COUNT(*)                                                        AS total_txns,
        COUNT(*) FILTER (WHERE t.txn_timestamp > r.ref_ts - INTERVAL '30 days')
                                                                        AS txns_30d,
        ROUND(AVG(t.balance_after), 2)                                  AS avg_balance,
        COUNT(*) FILTER (WHERE t.status = 'failed')                     AS failed_txns,
        COUNT(*) FILTER (WHERE t.status = 'failed'
                           AND t.txn_timestamp > r.ref_ts - INTERVAL '30 days')
                                                                        AS failed_30d,
        ROUND(AVG((t.status = 'failed')::int), 4)                       AS failure_rate,
        ROUND(AVG((EXTRACT(HOUR FROM t.txn_timestamp) < 6)::int), 4)    AS night_share
    FROM transactions t
    CROSS JOIN ref r
    GROUP BY t.user_id
),
scored AS (
    SELECT
        a.*,
        ROUND(
              40 * LEAST(a.failure_rate / 0.25, 1)
            + 25 * LEAST(GREATEST((20000 - a.avg_balance) / 20000, 0), 1)
            + 20 * LEAST(a.txns_30d / 12.0, 1)
            + 15 * LEAST(a.night_share / 0.30, 1)
        , 1) AS risk_score
    FROM agg a
)
SELECT
    u.user_id,
    u.full_name,
    u.country,
    u.account_tier,
    s.total_txns,
    s.txns_30d,
    s.avg_balance,
    s.failed_txns,
    s.failed_30d,
    s.failure_rate,
    s.night_share,
    s.risk_score,
    CASE WHEN s.risk_score >= 55 THEN 'High'
         WHEN s.risk_score >= 30 THEN 'Medium'
         ELSE 'Low' END AS risk_tier
FROM scored s
JOIN users u USING (user_id);
