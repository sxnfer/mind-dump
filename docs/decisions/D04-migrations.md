# D4: Schema changes (migrations)

Status: Approved, 2026-11-17
Problem: the schema changes every milestone, and every copy (San's Mac, a stranger's Mac, Azure, CI) must reach the same schema without losing data.
Without a tool: SQL run by hand; copies drift apart; nobody knows which changes ran where.
Options: our own runner with numbered SQL files; dbmate; Alembic.
Chosen: our own runner (`mind_dump.migrate`) over `backend/migrations/NNN_name.sql`.
Why: about 50 lines, no new dependency, and it shows exactly how migration tools work: an advisory lock (one migrator at a time), files applied in order, each in its own transaction, recorded in `schema_migrations`. This is the version we keep, not a throwaway.
Cost: no automatic "down" (undo) migrations; we fix forward with a new file. We maintain the runner.
Rule: never edit a migration that has run. Add a new one.
We will switch if: we need rollbacks, or the runner grows past ~150 lines. Then dbmate.
