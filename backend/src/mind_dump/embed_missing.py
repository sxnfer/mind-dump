"""Queue an `embed` job for every item whose chunks lack a vector from the configured model.

    python -m mind_dump.embed_missing            # queue the jobs
    python -m mind_dump.embed_missing --dry-run  # only count what is behind

In Docker:  docker compose exec worker python -m mind_dump.embed_missing

When to run it (D8):
- after setting the embedding key (OPENAI_API_KEY, or VOYAGE_API_KEY with
  EMBEDDING_PROVIDER=voyage): chunks saved without a key have no vectors;
- after changing EMBEDDING_MODEL or EMBEDDING_PROVIDER: vectors from two models can't
  be compared, so every chunk made by the old model is embedded again.

The command itself uses no network: it only queues jobs. An `embed` job fetches nothing
and re-chunks nothing when the text is unchanged; the worker embeds the rows in place,
so chunk ids stay the same. An item that already has a job waiting gets no second one:
that job embeds the missing rows anyway.
"""

import argparse
import sys

import psycopg
from psycopg.rows import dict_row

from mind_dump import jobs
from mind_dump.config import load_settings
from mind_dump.embeddings import no_embedder_reason

_BEHIND = """
SELECT item_id, count(*) AS chunks
FROM chunks
WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s
GROUP BY item_id
ORDER BY item_id
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mind_dump.embed_missing",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="count only; queue nothing")
    args = parser.parse_args(argv)

    settings = load_settings()
    model = settings.embedding_model
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn:
        behind = conn.execute(_BEHIND, (model,)).fetchall()
        total = sum(row["chunks"] for row in behind)
        print(f"{total} chunk(s) in {len(behind)} item(s) have no vector from {model}")
        if not behind or args.dry_run:
            return 0
        if settings.embedding_api_key is None:
            print(
                f"{no_embedder_reason(settings)}, so the worker can't embed them. "
                f"Set {settings.embedding.key_setting} in .env, restart the worker, "
                "and run this again."
            )
            return 1
        queued = 0
        for row in behind:
            if jobs.enqueue(conn, row["item_id"], "embed") is None:
                print(f"item {row['item_id']}: a job is already waiting; it will embed them")
            else:
                queued += 1
        print(f"queued {queued} embed job(s); the worker embeds them in place")
    return 0


if __name__ == "__main__":
    sys.exit(main())
