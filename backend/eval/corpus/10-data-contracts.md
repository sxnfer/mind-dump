# Data quality checks and data contracts

A data contract is an agreement between the team that produces a dataset and the teams that use it. It names the columns, their types, which ones may be empty, what each value means and how fresh the data must be. Changing the contract requires telling the consumers first, the same way a public API is versioned.

Quality checks are the contract's enforcement. Typical checks count rows against yesterday's count, look for unexpected nulls in required columns, test that keys are unique, and confirm that values fall inside known ranges. A check that fails should stop the pipeline before bad data reaches a dashboard, not raise a warning nobody reads.

Freshness is the check teams forget most often. A pipeline can run green for a week while its source has silently stopped updating, producing perfect copies of stale data. A freshness check compares the newest timestamp in the table with the current time and fails when the gap passes the agreed limit, for example six hours.

Short codes make failed checks easier to act on. A useful habit is to tag every rule with a code, so that alert DQ-117 always means duplicate invoice numbers in the billing export, and the runbook entry for DQ-117 tells the on-call engineer which upstream owner to call.

Contracts work best when they are checked in the producer's pipeline, before data is published, rather than in every consumer separately. One check in one place means one team owns the failure and fixes it at the source.

A schema change is breaking when an existing consumer can fail because of it: a column removed or renamed, a type narrowed, or a nullable column made required in a way that changes meaning. Adding a new optional column is usually safe.
