"""
Orchestrator — runs the full pipeline:
  1. Fetch new articles from RSS feeds
  2. Load memory context + today's reminders
  3. Synthesize digest via Claude API
  4. Update memory with new entries
  5. Send digest by email

Usage:
  python src/main.py              # full run
  python src/main.py --dry-run    # synthesize but do not send email
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        log.error(
            "settings.yaml not found. Copy config/settings.yaml.example "
            "to config/settings.yaml and fill in your credentials."
        )
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(dry_run: bool = False) -> None:
    settings = load_settings()

    # --- 1. Fetch articles ---
    from fetcher import fetch_new_articles

    digest_cfg = settings.get("digest", {})
    articles = fetch_new_articles(
        lookback_days=digest_cfg.get("lookback_days", 2),
        max_total=digest_cfg.get("max_articles", 40),
    )
    log.info("%d new articles fetched", len(articles))

    if not articles:
        log.info("No new articles — skipping digest")
        return

    # --- 2. Load memory ---
    from memory import read_memory, get_todays_reminders

    memory_content = read_memory()
    reminders = get_todays_reminders()
    if reminders:
        log.info("Today's reminders: %s", reminders)

    # --- 3. Synthesize ---
    from synthesizer import synthesize

    ollama_cfg = settings.get("ollama", {})
    result = synthesize(
        articles=articles,
        memory_content=memory_content,
        reminders=reminders,
        model=ollama_cfg.get("model", "mistral"),
        ollama_url=ollama_cfg.get("base_url", "http://localhost:11434"),
    )

    # --- 4. Update memory ---
    from memory import apply_memory_update

    apply_memory_update(
        new_dated=result.new_dated_memories,
        new_permanent=result.new_permanent_memories,
    )

    # --- 5. Send email ---
    if dry_run:
        log.info("Dry-run mode — email not sent")
        output_path = Path(__file__).parent.parent / "data" / "last_digest.html"
        output_path.write_text(result.html_body, encoding="utf-8")
        log.info("Digest written to %s", output_path)
        print("\n--- PLAIN TEXT PREVIEW ---\n")
        print(result.plain_body)
        return

    from mailer import send_digest

    email_cfg = settings.get("email", {})
    send_digest(
        html_body=result.html_body,
        plain_body=result.plain_body,
        smtp_host=email_cfg["smtp_host"],
        smtp_port=email_cfg["smtp_port"],
        smtp_user=email_cfg["smtp_user"],
        smtp_password=email_cfg["smtp_password"],
        sender_address=email_cfg["sender_address"],
        sender_name=email_cfg.get("sender_name", "Des nouvelles des étoiles"),
        recipient=email_cfg["recipient"],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily space news digest")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate digest without sending the email",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)
