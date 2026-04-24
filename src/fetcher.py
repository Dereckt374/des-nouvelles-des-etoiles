"""
Fetches new articles from RSS/Atom feeds, skipping already-seen entries.
Persistence is handled via SQLite (data/articles.db).
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import feedparser
import yaml

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "articles.db"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "feeds.yaml"


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_url    TEXT NOT NULL,
            guid        TEXT NOT NULL,
            title       TEXT,
            url         TEXT,
            published   TEXT,
            fetched_at  TEXT NOT NULL,
            UNIQUE(feed_url, guid)
        )
    """)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    _init_db(conn)
    return conn


def _entry_guid(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def _entry_published(entry) -> Optional[datetime]:
    for field in ("published_parsed", "updated_parsed"):
        val = entry.get(field)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def load_feeds() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("feeds", [])


def fetch_new_articles(lookback_days: int = 2, max_total: int = 40) -> list[dict]:
    """
    Returns a list of new articles (not previously seen) published within
    lookback_days. Each article is a dict with keys:
        feed_name, feed_url, guid, title, url, published, summary
    """
    feeds = load_feeds()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    conn = _get_conn()
    results: list[dict] = []

    for feed_cfg in feeds:
        feed_url = feed_cfg["url"]
        feed_name = feed_cfg.get("name", feed_url)
        try:
            parsed = feedparser.parse(feed_url, request_headers={"User-Agent": "des-nouvelles-des-etoiles/1.0"})
            if parsed.bozo and not parsed.entries:
                log.warning("Feed parse error for %s: %s", feed_name, parsed.bozo_exception)
                continue

            for entry in parsed.entries:
                guid = _entry_guid(entry)
                if not guid:
                    continue

                pub = _entry_published(entry)
                if pub and pub < cutoff:
                    continue

                # Check if already seen
                row = conn.execute(
                    "SELECT id FROM seen_articles WHERE feed_url=? AND guid=?",
                    (feed_url, guid),
                ).fetchone()
                if row:
                    continue

                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                summary = (
                    entry.get("summary")
                    or entry.get("description")
                    or entry.get("content", [{}])[0].get("value", "")
                ).strip()

                # Strip basic HTML from summary
                import re
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()[:1000]

                results.append({
                    "feed_name": feed_name,
                    "feed_url": feed_url,
                    "guid": guid,
                    "title": title,
                    "url": url,
                    "published": pub.isoformat() if pub else None,
                    "summary": summary,
                })

                # Mark as seen immediately
                conn.execute(
                    """INSERT OR IGNORE INTO seen_articles
                       (feed_url, guid, title, url, published, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (feed_url, guid, title, url,
                     pub.isoformat() if pub else None,
                     datetime.now(timezone.utc).isoformat()),
                )

        except Exception as e:
            log.error("Failed to fetch feed %s: %s", feed_name, e)

    conn.commit()
    conn.close()

    log.info("Fetched %d new articles across %d feeds", len(results), len(feeds))

    # Cap at max_total, most recent first
    results.sort(key=lambda a: a["published"] or "", reverse=True)
    return results[:max_total]
