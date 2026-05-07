"""
Manages the persistent memory file (data/memory.md).

The memory file has two sections:
  ## Événements datés
  Entries with a date — surfaced as reminders when the date matches today.

  ## Contexte permanent
  Background facts about ongoing missions, discoveries, etc.

The synthesizer reads this file as context and may propose updates.
This module applies those updates.
"""

import json
import re
import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

MEMORY_PATH = Path(__file__).parent.parent / "data" / "memory.md"
HIGHLIGHTS_PATH = Path(__file__).parent.parent / "data" / "highlights_history.json"

_HIGHLIGHTS_RETENTION_DAYS = 14

TEMPLATE = """\
# Mémoire — Des nouvelles des étoiles

## Événements datés
<!-- Format : - YYYY-MM-DD | Titre | Description courte -->

## Contexte permanent
<!-- Faits de fond : missions en cours, agences, contexte scientifique -->
"""


def _ensure_memory() -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not MEMORY_PATH.exists():
        MEMORY_PATH.write_text(TEMPLATE, encoding="utf-8")
        log.info("Created new memory file at %s", MEMORY_PATH)


def read_memory() -> str:
    _ensure_memory()
    return MEMORY_PATH.read_text(encoding="utf-8")


def get_todays_reminders() -> list[str]:
    """Returns dated entries whose date matches today."""
    _ensure_memory()
    content = MEMORY_PATH.read_text(encoding="utf-8")
    today = date.today().isoformat()
    reminders = []
    for line in content.splitlines():
        m = re.match(r"-\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+)", line)
        if m and m.group(1) == today:
            reminders.append(f"**{m.group(2)}** — {m.group(3)}")
    return reminders


def apply_memory_update(new_dated: list[str], new_permanent: list[str]) -> None:
    """
    Appends new entries proposed by the synthesizer to the memory file.
    new_dated: lines like "- 2026-05-12 | Titre | Description"
    new_permanent: lines like "- Description du fait de fond"
    """
    _ensure_memory()
    content = MEMORY_PATH.read_text(encoding="utf-8")

    if new_dated:
        block = "\n".join(new_dated)
        content = content.replace(
            "## Événements datés",
            f"## Événements datés\n{block}",
            1,
        )

    if new_permanent:
        block = "\n".join(new_permanent)
        content = content.replace(
            "## Contexte permanent",
            f"## Contexte permanent\n{block}",
            1,
        )

    MEMORY_PATH.write_text(content, encoding="utf-8")
    log.info(
        "Memory updated: +%d dated, +%d permanent entries",
        len(new_dated),
        len(new_permanent),
    )


def get_recent_highlights(days: int = 7) -> list[dict]:
    """Returns highlights from the last `days` days, oldest first."""
    if not HIGHLIGHTS_PATH.exists():
        return []
    try:
        history = json.loads(HIGHLIGHTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [entry for entry in history if entry.get("date", "") >= cutoff]


def save_highlights(points: list[str]) -> None:
    """Appends today's highlights and prunes entries older than retention window."""
    today = date.today().isoformat()
    if HIGHLIGHTS_PATH.exists():
        try:
            history = json.loads(HIGHLIGHTS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []
    else:
        history = []

    # Remove any existing entry for today (idempotent re-runs)
    history = [e for e in history if e.get("date") != today]
    history.append({"date": today, "highlights": points})

    # Prune old entries
    cutoff = (date.today() - timedelta(days=_HIGHLIGHTS_RETENTION_DAYS)).isoformat()
    history = [e for e in history if e.get("date", "") >= cutoff]

    HIGHLIGHTS_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Saved %d highlights for %s", len(points), today)
