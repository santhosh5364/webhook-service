SELECT transaction_id, source_account, destination_account, amount,
       currency, status, created_at, processed_at
FROM transactions
WHERE transaction_id = $1;
