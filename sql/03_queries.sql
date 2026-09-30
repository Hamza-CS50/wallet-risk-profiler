-- =====================================================================
-- Filtering users by transaction frequency, average balance, failed payments
-- Run 01_schema.sql, load the data, and run 02_risk_view.sql first.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. TRANSACTION FREQUENCY: users with 10+ transactions in the last 30 days
--     ("last 30 days" is relative to the newest transaction in the data)
-- ---------------------------------------------------------------------
SELECT
    t.user_id,
    COUNT(*)                                   AS txns_30d,
    ROUND(COUNT(*) / 30.0 * 7, 1)              AS txns_per_week,
    ROUND(SUM(t.amount), 2)                    AS total_volume
FROM transactions t
WHERE t.txn_timestamp > (SELECT MAX(txn_timestamp) FROM transactions) - INTERVAL '30 days'
GROUP BY t.user_id
HAVING COUNT(*) >= 10
ORDER BY txns_30d DESC;


-- ---------------------------------------------------------------------
-- Q2. AVERAGE BALANCE: users whose average balance is below 20,000
--     and who have at least 10 transactions (so the average is meaningful)
-- ---------------------------------------------------------------------
SELECT
    user_id,
    COUNT(*)                        AS total_txns,
    ROUND(AVG(balance_after), 2)    AS avg_balance,
    MIN(balance_after)              AS min_balance
FROM transactions
GROUP BY user_id
HAVING AVG(balance_after) < 20000
   AND COUNT(*) >= 10
ORDER BY avg_balance ASC;


-- ---------------------------------------------------------------------
-- Q3. FAILED PAYMENTS: users with a failure rate above 15%
--     (again requiring a minimum sample size)
-- ---------------------------------------------------------------------
SELECT
    user_id,
    COUNT(*)                                           AS total_txns,
    COUNT(*) FILTER (WHERE status = 'failed')          AS failed_txns,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'failed') / COUNT(*), 1) AS failure_rate_pct
FROM transactions
GROUP BY user_id
HAVING COUNT(*) >= 10
   AND COUNT(*) FILTER (WHERE status = 'failed')::numeric / COUNT(*) > 0.15
ORDER BY failure_rate_pct DESC;


-- ---------------------------------------------------------------------
-- Q4. COMBINED WATCHLIST: all three signals at once
--     Active users (frequency) + thin balance + repeated failures
-- ---------------------------------------------------------------------
WITH ref AS (SELECT MAX(txn_timestamp) AS ref_ts FROM transactions),
per_user AS (
    SELECT
        t.user_id,
        COUNT(*)                                                      AS total_txns,
        COUNT(*) FILTER (WHERE t.txn_timestamp > r.ref_ts - INTERVAL '30 days') AS txns_30d,
        AVG(t.balance_after)                                          AS avg_balance,
        COUNT(*) FILTER (WHERE t.status = 'failed')                   AS failed_txns
    FROM transactions t CROSS JOIN ref r
    GROUP BY t.user_id
)
SELECT
    u.user_id,
    u.full_name,
    p.total_txns,
    p.txns_30d,
    ROUND(p.avg_balance, 2)                         AS avg_balance,
    p.failed_txns,
    ROUND(100.0 * p.failed_txns / p.total_txns, 1)  AS failure_rate_pct
FROM per_user p
JOIN users u USING (user_id)
WHERE p.txns_30d >= 3                                -- actively transacting
  AND p.avg_balance < 25000                          -- thin balance
  AND p.failed_txns::numeric / p.total_txns > 0.15   -- frequent failures
ORDER BY failure_rate_pct DESC, avg_balance ASC;


-- ---------------------------------------------------------------------
-- Q5. RELATIVE THRESHOLDS with window functions
--     Instead of hard-coded cut-offs, flag users in the worst decile of
--     failure rate AND the lowest quartile of balance.
-- ---------------------------------------------------------------------
WITH per_user AS (
    SELECT
        user_id,
        COUNT(*)                                        AS total_txns,
        AVG(balance_after)                              AS avg_balance,
        AVG((status = 'failed')::int)                   AS failure_rate
    FROM transactions
    GROUP BY user_id
    HAVING COUNT(*) >= 5
),
ranked AS (
    SELECT *,
           NTILE(10) OVER (ORDER BY failure_rate DESC)  AS fail_decile,   -- 1 = worst 10%
           NTILE(4)  OVER (ORDER BY avg_balance ASC)    AS balance_quartile -- 1 = lowest 25%
    FROM per_user
)
SELECT user_id, total_txns,
       ROUND(avg_balance, 2)        AS avg_balance,
       ROUND(failure_rate * 100, 1) AS failure_rate_pct
FROM ranked
WHERE fail_decile = 1 AND balance_quartile = 1
ORDER BY failure_rate DESC;


-- ---------------------------------------------------------------------
-- Q6. LONGEST RUN OF CONSECUTIVE FAILURES per user (gaps-and-islands)
--     Back-to-back failures are a stronger fraud/distress signal than
--     failures scattered over months.
-- ---------------------------------------------------------------------
WITH seq AS (
    SELECT
        user_id, txn_timestamp, status,
        ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY txn_timestamp)                    AS rn,
        ROW_NUMBER() OVER (PARTITION BY user_id, status ORDER BY txn_timestamp)            AS rn_status
    FROM transactions
),
streaks AS (
    SELECT user_id, COUNT(*) AS streak_len, MIN(txn_timestamp) AS streak_start
    FROM seq
    WHERE status = 'failed'
    GROUP BY user_id, (rn - rn_status)
)
SELECT user_id, MAX(streak_len) AS longest_failure_streak
FROM streaks
GROUP BY user_id
HAVING MAX(streak_len) >= 3
ORDER BY longest_failure_streak DESC, user_id
LIMIT 25;


-- ---------------------------------------------------------------------
-- Q7. WHY are the flagged users failing? Failure reasons for High-risk tier
-- ---------------------------------------------------------------------
SELECT
    t.failure_reason,
    COUNT(*)                                                  AS failures,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1)        AS pct_of_failures
FROM transactions t
JOIN user_risk_metrics m USING (user_id)
WHERE t.status = 'failed'
  AND m.risk_tier = 'High'
GROUP BY t.failure_reason
ORDER BY failures DESC;


-- ---------------------------------------------------------------------
-- Q8. RISK TIER SUMMARY from the view
-- ---------------------------------------------------------------------
SELECT
    risk_tier,
    COUNT(*)                        AS users,
    ROUND(AVG(risk_score), 1)       AS avg_score,
    ROUND(AVG(avg_balance), 0)      AS avg_balance,
    ROUND(AVG(failure_rate) * 100, 1) AS avg_failure_rate_pct
FROM user_risk_metrics
GROUP BY risk_tier
ORDER BY avg_score DESC;
