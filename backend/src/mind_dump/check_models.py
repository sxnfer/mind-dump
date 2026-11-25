"""Check the model keys (D8): one tiny embedding call and one tiny Claude call.

    python -m mind_dump.check_models

In Docker:  docker compose exec worker python -m mind_dump.check_models

Prints one line per provider: OK with the model name, or the exact reason it failed
(a missing key, a 401, an unknown model). The embeddings line names the provider it
checked (EMBEDDING_PROVIDER: openai by default, or voyage). Exits 0 only if both work.
Each call costs a fraction of a penny.
"""

import sys
from pathlib import Path

from mind_dump.config import Settings, load_settings
from mind_dump.describe import ClaudeDescriber, create_describer
from mind_dump.embeddings import DIMENSIONS, Embedder, create_embedder, no_embedder_reason
from mind_dump.errors import PermanentError, TemporaryError


def check_embeddings(embedder: Embedder | None, settings: Settings) -> tuple[bool, str]:
    if embedder is None:
        return False, f"{no_embedder_reason(settings)}: chunks are saved without vectors"
    try:
        vectors = embedder.embed_documents(["Mind Dump model check."])
    except (PermanentError, TemporaryError) as exc:
        return False, str(exc)
    return True, f"OK, {embedder.model}, {len(vectors[0])} dimensions (expected {DIMENSIONS})"


def check_claude(describer: ClaudeDescriber | None) -> tuple[bool, str]:
    if describer is None:
        return False, "ANTHROPIC_API_KEY is not set (images can't be described)"
    try:
        describer.check()
    except (PermanentError, TemporaryError) as exc:
        return False, str(exc)
    return True, f"OK, {describer.model}"


def run(
    embedder: Embedder | None,
    describer: ClaudeDescriber | None,
    settings: Settings | None = None,
) -> tuple[bool, list[str]]:
    """settings says which provider was meant when there is no embedder (default: the
    default provider)."""
    settings = settings or Settings(database_url="unused", migrations_dir=Path())
    emb_ok, emb_line = check_embeddings(embedder, settings)
    llm_ok, llm_line = check_claude(describer)
    label = getattr(embedder, "label", None) or settings.embedding.label
    lines = [f"embeddings ({label}): {emb_line}", f"LLM (Claude): {llm_line}"]
    return emb_ok and llm_ok, lines


def main() -> int:
    settings = load_settings()
    describer = create_describer(settings)
    ok, lines = run(
        create_embedder(settings),
        describer if isinstance(describer, ClaudeDescriber) else None,
        settings,
    )
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
