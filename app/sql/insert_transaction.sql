-- Idempotent insert: a duplicate transaction_id inserts nothing and returns no row.
INSERT INTO transactions
    (transaction_id, source_account, destination_account, amount, currency)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (transaction_id) DO NOTHING
RETURNING transaction_id;
