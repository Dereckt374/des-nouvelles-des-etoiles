"""
Fetches new articles from RSS/Atom feeds, skipping already-seen entries.
Persistence is handled via SQLite (data/articles.db).

Feeds are declared in config/feeds.yaml under *groups* (`feeds`, `custom_feeds`, …).
Each group is fetched independently and feeds its own section of the digest.
Deduplication is global: the seen-table is keyed on (feed_url, guid), so the
same entry is never reported twice even if a URL appears in two groups.

Fetching never writes: articles are recorded only by `mark_seen()`, which the
orchestrator calls once the digest has actually been delivered. Anything that
fails in between (LLM outage, SMTP error) therefore leaves the articles pending
for the next run instead of losing them.
"""

import html
import re
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

USER_AGENT = "des-nouvelles-des-etoiles/1.0"


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


def _clean_summary(entry) -> str:
    """Reduces a feed entry to plain text.

    The result is plain text, not HTML: entities are decoded here and the
    renderer escapes again on output. Storing decoded text keeps the
    plain-text email readable ("l'ORI" rather than "l&#8217;ORI").
    """
    summary = (
        entry.get("summary")
        or entry.get("description")
        or entry.get("content", [{}])[0].get("value", "")
    ).strip()
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = html.unescape(summary)
    # WordPress feeds truncate the body and append "… Continue reading <title>"
    summary = re.sub(r"\s*(?:…|\.\.\.)?\s*Continue reading\s+.*$", "…", summary, flags=re.I | re.S)
    return re.sub(r"\s+", " ", summary).strip()[:1000]


# ---------------------------------------------------------------------------
# Per-source post-processing
#
# Some feeds need source-specific handling that does not belong in the generic
# RSS path. A feed declares `kind: <name>` in feeds.yaml and the matching
# transform below is applied to each of its articles; returning None drops the
# article. Dropped articles are never marked as seen, so they are re-evaluated
# on the next run — cheap, and it means a change of rule takes effect at once.
# ---------------------------------------------------------------------------

# https://<instance>/<handle>/status/<id>#m  ->  https://x.com/<handle>/status/<id>
_NITTER_STATUS = re.compile(r"^https?://[^/]+/([^/]+)/status/(\d+)")

# Nitter prefixes retweets with "RT by @x:" and replies with "R to @x:".
_NITTER_ECHO = ("RT by ", "R to ")


def _nitter_article(article: dict) -> Optional[dict]:
    """Keeps an account's own posts, and points links back at x.com.

    Retweets and replies are dropped: what matters for competitive tracking is
    what the company says itself. Links are rewritten because the relay is
    expected to disappear one day, whereas x.com URLs stay valid — and the
    tweet id embedded in them is stable either way.
    """
    if article["title"].startswith(_NITTER_ECHO):
        return None

    match = _NITTER_STATUS.match(article["url"] or "")
    if match:
        article["url"] = f"https://x.com/{match.group(1)}/status/{match.group(2)}"
    return article


_TRANSFORMS = {"nitter": _nitter_article}

# Sources whose entry stands for an *ongoing thread* rather than a one-off
# publication. A forum feed reuses the topic URL as the guid however much the
# discussion grows, so keying on it alone would announce a thread once and then
# suppress it for good — exactly the wrong behaviour for a discussion you follow
# precisely to see it move. Folding the last-activity timestamp into the key
# makes each new burst of posts a new entry. The feed's description carries the
# thread's most recent message, so the re-report brings fresh content, not a
# repeat of the opening post.
_ACTIVITY_KEYED = {"forum"}


def load_feeds(group: str = "feeds") -> list[dict]:
    """Returns the feed definitions declared under `group` in feeds.yaml."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get(group) or []


def mark_seen(articles: list[dict]) -> None:
    """Records articles as reported, so they never show up in a later digest.

    Deliberately separate from fetching: call it only once the digest has been
    delivered. Marking during collection means a crash between the fetch and
    the send silently swallows the day's articles for good.
    """
    if not articles:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    conn.executemany(
        """INSERT OR IGNORE INTO seen_articles
           (feed_url, guid, title, url, published, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (a["feed_url"], a["guid"], a["title"], a["url"], a["published"], now)
            for a in articles
        ],
    )
    conn.commit()
    conn.close()
    log.info("Marked %d articles as seen", len(articles))


def fetch_new_articles(
    lookback_days: int = 2,
    max_total: int = 40,
    group: str = "feeds",
) -> list[dict]:
    """
    Returns a list of new articles (not previously seen) published within
    lookback_days. Each article is a dict with keys:
        feed_name, feed_url, guid, title, url, published, summary

    Read-only — see `mark_seen()`.
    """
    feeds = load_feeds(group)
    if not feeds:
        log.info("No feeds declared in group '%s'", group)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    conn = _get_conn()
    results: list[dict] = []

    for feed_cfg in feeds:
        feed_url = feed_cfg["url"]
        feed_name = feed_cfg.get("name", feed_url)
        kind = feed_cfg.get("kind") or ""
        transform = _TRANSFORMS.get(kind)
        try:
            parsed = feedparser.parse(feed_url, request_headers={"User-Agent": USER_AGENT})
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

                if kind in _ACTIVITY_KEYED and pub:
                    guid = f"{guid}#{pub.isoformat()}"

                # Check if already seen
                row = conn.execute(
                    "SELECT id FROM seen_articles WHERE feed_url=? AND guid=?",
                    (feed_url, guid),
                ).fetchone()
                if row:
                    continue

                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                published = pub.isoformat() if pub else None

                article = {
                    "feed_name": feed_name,
                    "feed_url": feed_url,
                    "guid": guid,
                    "title": title,
                    "url": url,
                    "published": published,
                    "summary": _clean_summary(entry),
                }
                if transform:
                    article = transform(article)
                    if article is None:
                        continue

                results.append(article)

        except Exception as e:
            log.error("Failed to fetch feed %s: %s", feed_name, e)

    conn.close()

    log.info(
        "Group '%s': fetched %d new articles across %d feeds",
        group, len(results), len(feeds),
    )

    # Cap at max_total, most recent first
    results.sort(key=lambda a: a["published"] or "", reverse=True)
    return results[:max_total]
