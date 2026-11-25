"""Settings come from environment variables, the same way locally, in CI and in Azure.

Compose fills them from `.env` (git-ignored). Code never holds a secret.
"""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

# backend/migrations, when running from a checkout (local dev and tests).
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
# <repo>/data, when running from a checkout. The Docker image sets STORAGE_DIR=/data (a volume).
_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parents[3] / "data"

DEFAULT_EMBEDDING_MODEL = "voyage-4"  # D8
DEFAULT_LLM_MODEL = "claude-opus-5-5"  # D8


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens (D39). cache_write is the 5-minute cache write."""

    input: Decimal
    output: Decimal = Decimal(0)
    cache_write: Decimal = Decimal(0)
    cache_read: Decimal = Decimal(0)


def _price(input_: str, output: str = "0", cache_write: str = "0", cache_read: str = "0") -> Price:
    return Price(Decimal(input_), Decimal(output), Decimal(cache_write), Decimal(cache_read))


# D39: the price table. Claude's list prices from Anthropic's model table (claude-api
# skill, cached 2026-11-03): input, output, 5-minute cache write (1.25x input), cache
# read; where the table gives no cache price, the documented rules (write 1.25x input,
# read 0.1x input). claude-opus-5 and claude-opus-4-8 are here because a refusal fallback (D8) can
# serve an answer with them. Prices drift: MODEL_PRICES (JSON) overrides any entry.
DEFAULT_MODEL_PRICES: dict[str, Price] = {
    "claude-opus-5-5": _price("4", "20", "5", "0.20"),
    "claude-opus-5": _price("5", "25", "6.25", "0.50"),
    "claude-opus-4-8": _price("5", "25", "6.25", "0.50"),
    "claude-sonnet-5-5": _price("2", "10", "2.50", "0.10"),
    "claude-haiku-5-5": _price("0.10", "0.50", "0.125", "0.01"),
    "claude-fable-5-1": _price("10", "50", "12.50", "0.25"),
}
# NOT VERIFIED: a placeholder for Voyage AI's embedding price (US dollars per million
# tokens). Check it against Voyage AI's pricing page and set VOYAGE_PRICE_PER_MTOK.
DEFAULT_VOYAGE_PRICE_PER_MTOK = "0.20"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)  # holds the database password
    migrations_dir: Path
    pool_min_size: int = 1
    pool_max_size: int = 10
    storage_backend: str = "local"  # D5: "local" now, "azure" in P6
    storage_dir: Path = _DEFAULT_STORAGE_DIR
    # D8: the owner's model keys. Both are optional: without VOYAGE_API_KEY chunks are
    # saved with no vectors; without ANTHROPIC_API_KEY images can't be described.
    # repr=False: a logged or printed Settings never shows a key.
    voyage_api_key: str | None = field(default=None, repr=False)
    anthropic_api_key: str | None = field(default=None, repr=False)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    llm_model: str = DEFAULT_LLM_MODEL
    # M5: answers (D9). Effort is set explicitly: this model's default is medium, and
    # effort is the control for how much it thinks (and costs).
    llm_effort: str = "medium"
    agent_max_turns: int = 6  # model calls that may use tools, before "answer now"
    # D39: cost cap and rate limit.
    daily_cost_cap_usd: Decimal = Decimal("2.00")
    ask_rate_per_minute: int = 10
    model_prices: Mapping[str, Price] = field(default_factory=lambda: dict(DEFAULT_MODEL_PRICES))
    voyage_price_per_mtok: Decimal = Decimal(DEFAULT_VOYAGE_PRICE_PER_MTOK)


def _optional(name: str) -> str | None:
    """An unset or empty variable is None. Compose passes `${NAME:-}`, so empty is normal."""
    value = os.environ.get(name, "").strip()
    return value or None


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _effort(value: str | None) -> str:
    effort = value or "medium"
    if effort not in EFFORTS:
        raise RuntimeError(f"LLM_EFFORT must be one of {', '.join(EFFORTS)}, not {effort!r}")
    return effort


def _prices(value: str | None) -> dict[str, Price]:
    """The default table, with MODEL_PRICES (JSON) laid over it:
    {"claude-opus-5-5": {"input": 4, "output": 20, "cache_write": 5, "cache_read": 0.2}}"""
    prices = dict(DEFAULT_MODEL_PRICES)
    if not value:
        return prices
    try:
        for model, p in json.loads(value).items():
            prices[model] = Price(**{k: Decimal(str(v)) for k, v in p.items()})
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"MODEL_PRICES is not valid: {exc}") from None
    return prices


def load_settings() -> Settings:
    try:
        database_url = os.environ["DATABASE_URL"]
    except KeyError:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env, or export it, e.g. "
            "postgresql://mind_dump:PASSWORD@localhost:5432/mind_dump"
        ) from None
    return Settings(
        database_url=database_url,
        migrations_dir=Path(os.environ.get("MIGRATIONS_DIR", _DEFAULT_MIGRATIONS_DIR)),
        pool_min_size=int(os.environ.get("DB_POOL_MIN", "1")),
        pool_max_size=int(os.environ.get("DB_POOL_MAX", "10")),
        storage_backend=os.environ.get("STORAGE_BACKEND", "local"),
        storage_dir=Path(os.environ.get("STORAGE_DIR", _DEFAULT_STORAGE_DIR)),
        voyage_api_key=_optional("VOYAGE_API_KEY"),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY"),
        embedding_model=_optional("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
        llm_model=_optional("LLM_MODEL") or DEFAULT_LLM_MODEL,
        llm_effort=_effort(_optional("LLM_EFFORT")),
        agent_max_turns=int(_optional("AGENT_MAX_TURNS") or "6"),
        daily_cost_cap_usd=Decimal(_optional("DAILY_COST_CAP_USD") or "2.00"),
        ask_rate_per_minute=int(_optional("ASK_RATE_PER_MINUTE") or "10"),
        model_prices=_prices(_optional("MODEL_PRICES")),
        voyage_price_per_mtok=Decimal(
            _optional("VOYAGE_PRICE_PER_MTOK") or DEFAULT_VOYAGE_PRICE_PER_MTOK
        ),
    )
