"""
Orchestrator — runs the full pipeline:
  1. Fetch new articles from the RSS feed groups
  2. Synthesize the news sections via the Mistral API
  3. Assemble the digest and send it by email

Usage:
  python src/main.py              # full run
  python src/main.py --dry-run    # synthesize but do not send email,
                                  # and do not mark articles as seen
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

CUSTOM_FEEDS_TITLE = "Blogs & personnalités suivis"


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        log.error(
            "settings.yaml not found. Copy config/settings.yaml.example "
            "to config/settings.yaml and fill in your credentials."
        )
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _model_for(mistral_cfg: dict, usage: str) -> str:
    """Model to use for a given usage, falling back to the global default.

    Lets the cheap model handle bulk news while a stronger one can be wired
    to the analysis-heavy sections later, without touching the code.
    """
    default = mistral_cfg.get("model", "mistral-small-latest")
    return mistral_cfg.get("models", {}).get(usage, default)


def _custom_feeds_section(articles: list[dict]):
    """Builds the custom-feeds section verbatim — no LLM involved.

    These feeds are hand-picked voices: their own wording is the value, so
    entries are shown as published rather than summarized.
    """
    from models import Item, Section

    items = [
        Item(
            title=a["title"],
            url=a["url"],
            source=a["feed_name"],
            date=(a.get("published") or "")[:10],
            summary=a["summary"][:400],
        )
        for a in articles
    ]
    sources = sorted({a["feed_name"] for a in articles})
    return Section(
        key="custom_feeds",
        title=CUSTOM_FEEDS_TITLE,
        items=items,
        subtitle=" · ".join(sources),
    )


def run(dry_run: bool = False) -> None:
    settings = load_settings()
    digest_cfg = settings.get("digest", {})
    mistral_cfg = settings.get("mistral", {})
    mark_seen = not dry_run

    # Validated before fetching: a real run marks articles as seen as it goes,
    # so bailing out afterwards would silently burn them.
    if not mistral_cfg.get("api_key"):
        log.error("No mistral.api_key in settings.yaml — see config/settings.yaml.example")
        sys.exit(1)

    # --- 1. Fetch ---
    from fetcher import fetch_new_articles

    articles = fetch_new_articles(
        lookback_days=digest_cfg.get("lookback_days", 2),
        max_total=digest_cfg.get("max_articles", 40),
        group="feeds",
        mark_seen=mark_seen,
    )

    custom_cfg = digest_cfg.get("custom_feeds", {})
    custom_articles = fetch_new_articles(
        lookback_days=custom_cfg.get("lookback_days", 7),
        max_total=custom_cfg.get("max_items", 15),
        group="custom_feeds",
        mark_seen=mark_seen,
    )

    log.info(
        "%d new articles, %d new custom-feed entries",
        len(articles), len(custom_articles),
    )

    if not articles and not custom_articles:
        log.info("Nothing new — skipping digest")
        return

    # --- 2. Synthesize the news sections ---
    from models import Digest
    from renderer import render_error, render_html, render_plain
    from synthesizer import synthesize

    date_label = date.today().strftime("%A %d %B %Y")

    synthesis = synthesize(
        articles=articles,
        model=_model_for(mistral_cfg, "news"),
        api_key=mistral_cfg["api_key"],
        date_label=date_label,
    )

    # --- 3. Assemble ---
    if synthesis.raw_error:
        html_body = render_error(synthesis.raw_error, date_label)
        plain_body = synthesis.raw_error
    else:
        sections = list(synthesis.sections)
        if custom_articles:
            sections.append(_custom_feeds_section(custom_articles))

        digest = Digest(
            date_label=date_label,
            highlights=synthesis.highlights,
            sections=sections,
            article_count=len(articles) + len(custom_articles),
        )
        html_body = render_html(digest)
        plain_body = render_plain(digest)

    # --- 4. Send ---
    if dry_run:
        log.info("Dry-run mode — email not sent, articles not marked as seen")
        output_path = Path(__file__).parent.parent / "data" / "last_digest.html"
        output_path.write_text(html_body, encoding="utf-8")
        log.info("Digest written to %s", output_path)
        # Windows consoles default to cp1252 and choke on characters that
        # routinely show up in feeds (ʻokina, typographic dashes, …).
        sys.stdout.reconfigure(errors="replace")
        print("\n--- PLAIN TEXT PREVIEW ---\n")
        print(plain_body)
        return

    from mailer import send_digest

    email_cfg = settings.get("email", {})
    send_digest(
        html_body=html_body,
        plain_body=plain_body,
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
