-- Only moves PROCESSING -> PROCESSED once, so a transaction can't be completed twice.
UPDATE transactions
SET status = 'PROCESSED', processed_at = now(), locked_until = NULL
WHERE transaction_id = $1 AND status = 'PROCESSING';
