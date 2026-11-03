-- Lease one pending transaction. SKIP LOCKED keeps workers/instances from taking the same row.
UPDATE transactions
SET locked_until = now() + make_interval(secs => $1),
    attempts = attempts + 1
WHERE transaction_id = (
    SELECT transaction_id FROM transactions
    WHERE status = 'PROCESSING'
      AND (locked_until IS NULL OR locked_until < now())
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING transaction_id, attempts;
