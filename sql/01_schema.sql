-- Schema for the digital wallet risk project (PostgreSQL 13+)

DROP TABLE IF EXISTS transactions;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY,
    full_name     TEXT        NOT NULL,
    country       CHAR(2)     NOT NULL,
    signup_date   DATE        NOT NULL,
    account_tier  TEXT        NOT NULL CHECK (account_tier IN ('basic', 'standard', 'premium')),
    segment       TEXT        -- generator label only (for validating the risk model); not used in risk logic
);

CREATE TABLE transactions (
    txn_id         INTEGER PRIMARY KEY,
    user_id        INTEGER       NOT NULL REFERENCES users(user_id),
    txn_timestamp  TIMESTAMP     NOT NULL,
    txn_type       TEXT          NOT NULL,
    amount         NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    status         TEXT          NOT NULL CHECK (status IN ('success', 'failed')),
    failure_reason TEXT,
    balance_after  NUMERIC(14,2) NOT NULL,
    channel        TEXT          NOT NULL
);

CREATE INDEX idx_txn_user_ts ON transactions (user_id, txn_timestamp);
CREATE INDEX idx_txn_status  ON transactions (status);
