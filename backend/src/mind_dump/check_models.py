"""Check the model keys (D8): one tiny embedding call and one tiny Claude call.

    python -m mind_dump.check_models

In Docker:  docker compose exec worker python -m mind_dump.check_models

Prints one line per provider: OK with the model name, or the exact reason it failed
(a missing key, a 401, an unknown model). Exits 0 only if both work. Each call costs a
fraction of a penny.
"""

import sys

from mind_dump.config import load_settings
from mind_dump.describe import ClaudeDescriber, create_describer
from mind_dump.embeddings import DIMENSIONS, Embedder, create_embedder
from mind_dump.errors import PermanentError, TemporaryError


def check_embeddings(embedder: Embedder | None) -> tuple[bool, str]:
    if embedder is None:
        return False, "VOYAGE_API_KEY is not set (chunks are saved without vectors)"
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


def run(embedder: Embedder | None, describer: ClaudeDescriber | None) -> tuple[bool, list[str]]:
    emb_ok, emb_line = check_embeddings(embedder)
    llm_ok, llm_line = check_claude(describer)
    lines = [f"embeddings (Voyage AI): {emb_line}", f"LLM (Claude): {llm_line}"]
    return emb_ok and llm_ok, lines


def main() -> int:
    settings = load_settings()
    describer = create_describer(settings)
    ok, lines = run(
        create_embedder(settings),
        describer if isinstance(describer, ClaudeDescriber) else None,
    )
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
