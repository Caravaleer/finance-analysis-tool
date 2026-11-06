-- ============================================================================
-- Supabase / PostgreSQL Migrations for Personal Finance Analysis Tool
-- ============================================================================

-- 1. Create Performance Composite Indexes
-- Ensures lightning-fast pagination, date ordering, and user-scoped filtering.

CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions (user_id, date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user_cat ON transactions (user_id, category);
CREATE INDEX IF NOT EXISTS idx_transactions_user_type ON transactions (user_id, type);
CREATE INDEX IF NOT EXISTS idx_transactions_user_date_type ON transactions (user_id, date DESC, type);

-- 2. Optional: Postgres View for Monthly Aggregates (Per User)
-- Can be queried directly or used in Supabase Studio.

CREATE OR REPLACE VIEW view_monthly_user_summary AS
SELECT
    user_id,
    date_trunc('month', date) AS month,
    SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS total_income,
    SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS total_expense,
    SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END) AS net_savings,
    COUNT(*) AS transaction_count
FROM transactions
GROUP BY user_id, date_trunc('month', date);

-- 3. Optional: SQL Function for Fast Filtered Cumulative Balance
CREATE OR REPLACE FUNCTION get_user_running_balance(p_user_id INT)
RETURNS TABLE (
    id INT,
    tx_date TIMESTAMP,
    category VARCHAR,
    amount FLOAT,
    tx_type VARCHAR,
    running_balance FLOAT
) LANGUAGE sql STABLE AS $$
    SELECT
        id,
        date AS tx_date,
        category,
        amount,
        type AS tx_type,
        SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END) OVER (ORDER BY date ASC, id ASC) AS running_balance
    FROM transactions
    WHERE user_id = p_user_id
    ORDER BY date ASC, id ASC;
$$;
