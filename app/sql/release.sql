-- Hand a job back (e.g. on shutdown) so it doesn't wait for the lease to expire.
UPDATE transactions
SET locked_until = NULL
WHERE transaction_id = $1 AND status = 'PROCESSING';
