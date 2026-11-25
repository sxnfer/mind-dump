# Backfills and partitioning

A backfill reruns a pipeline over past dates, either to load history for a new table or to repair data after a bug. Backfills are where weak pipelines break, because a job written for one day at a time is suddenly asked to handle two years, and every assumption about size and order is tested at once.

Partitioning makes backfills cheap. When a large table is split into partitions by date, a rerun of one day can drop or truncate that day's partition and load it again, instead of deleting millions of rows one by one. Queries that filter on the date also read only the partitions they need, which is called partition pruning.

Run backfills in small batches, oldest first, with a checkpoint after each batch. If the backfill stops halfway, the checkpoint tells you where to resume, and the batches already done are not repeated. Throttling the batches keeps the backfill from starving the daily jobs that share the same database.

Before a large backfill, estimate its cost. Multiply the time and the cloud bill of one representative day by the number of days, add a margin for the oldest data, which is often messier, and decide whether the result is worth it. A backfill of 730 days at four minutes a day takes about two days of machine time, before any retries.

Backfills must not overwrite corrections. If analysts fixed some historical rows by hand, a backfill that rebuilds those days from the raw data will silently undo the fixes, so the fixes belong in the pipeline's code or in a table of overrides, not in the output.

Announce a backfill before it starts. Dashboards built on the affected tables will show numbers changing for past dates, and readers who see last quarter's revenue move without warning lose trust in every number afterwards.
