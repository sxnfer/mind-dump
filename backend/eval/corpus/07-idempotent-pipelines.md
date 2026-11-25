# Idempotent data pipelines

A pipeline step is idempotent when running it twice gives the same result as running it once. This matters because every real pipeline is run twice sooner or later: a scheduler retries after a timeout, an engineer reruns a failed day, or a worker dies after writing half its output. Without idempotency, each rerun risks duplicate rows or totals that are counted twice.

The simplest pattern is delete then insert inside one transaction. The step removes every row it owns for its unit of work, for example one day or one source file, and writes the new rows, then commits. Readers see either the old set or the new set, never a mix, and a crash before the commit leaves the old set untouched.

The other common pattern is the upsert, an insert that updates the existing row when a row with the same key is already there. In Postgres it is written INSERT ... ON CONFLICT DO UPDATE. Upserts need a stable business key, such as an order number, and they do not remove rows that disappeared from the source, so they suit append-heavy data better than full replacement.

Idempotency also needs deterministic inputs. A step that reads "the latest data" or calls now() gives a different answer on each run. Passing the logical date of the run as a parameter, instead of reading the clock, lets a rerun of 3 March process exactly what the first run of 3 March processed.

Side effects outside the database are the hard part. Sending an email, charging a card or calling a partner's API cannot be rolled back by a transaction, so such steps record an idempotency key and check it before acting, which turns a repeated call into a harmless no-op.

Testing idempotency is simple and often skipped. Run the step, take a snapshot of its output, run it again with the same inputs, and compare. Any difference, even in row order or a timestamp column, shows a step that will misbehave on its first real retry.
