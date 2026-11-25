# Keeping history: slowly changing dimensions

A slowly changing dimension is a table whose rows describe things that change now and then, such as a customer's address or a product's price. The question is what to do with the old value when a new one arrives, and the answer decides whether you can ever report on the past correctly.

Type 1 simply overwrites the old value. It is easy and keeps the table small, but every old report recomputed today shows the new address, as if the customer had always lived there. Type 2 keeps the history: instead of updating the row, it closes it by setting a valid_to timestamp and inserts a new row with a valid_from timestamp.

With type 2, the question "what was the price on 1 June?" becomes a filter: the row where valid_from is on or before that day and valid_to is empty or later. A partial unique index on the key where valid_to is null makes the database itself refuse two current rows for the same customer.

Change data capture is how history tables are usually fed. Instead of comparing full snapshots, a CDC tool reads the source database's write-ahead log and turns each committed insert, update and delete into an event, in commit order, so the history table receives every change without querying the source again.

Type 2 tables grow without limit, so teams sometimes add a surrogate key, a plain number for each version of a row, and keep the business key, such as the customer number, in a separate column. Facts then point at the surrogate key and therefore at the exact version that was true when the fact happened.

Late-arriving changes are the classic trap. If a change dated last week arrives today, the rows after it must be adjusted, which is why history tables should be rebuildable from the raw change events rather than patched by hand.
