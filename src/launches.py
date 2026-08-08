"""
Launch schedule and space events, from the Launch Library 2 API (thespacedevs.com).

Covers a window around today: past launches come back with their outcome
(success / failure), upcoming ones with their status and T-0. The same window
is queried on /events/ — EVAs, press conferences, dockings, celestial events —
and those are appended to the section as a sub-block.

The section is rendered from the API payload as-is, without going through the
LLM: times, statuses and outcomes are facts, and a model paraphrasing them is
a model that can get a T-0 wrong. Watched operators (config/competitors.yaml)
get a detailed card, everyone else a one-liner.

Two API calls per run. The anonymous tier allows roughly fifteen an hour, so
there is ample headroom, and both calls fail soft: a missing launch section
must never cost the reader the rest of the digest.
"""

import json
import logging
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

from models import Item, Section

log = logging.getLogger(__name__)

API_ROOT = "https://ll.thespacedevs.com/2.3.0/launches/"
EVENTS_ROOT = "https://ll.thespacedevs.com/2.3.0/events/"
COMPETITORS_PATH = Path(__file__).parent.parent / "config" / "competitors.yaml"

PARIS = ZoneInfo("Europe/Paris")
USER_AGENT = "des-nouvelles-des-etoiles/1.0"

SECTION_TITLE = "Lancements"
EVENTS_TITLE = "Événements"
EVENTS_HEADING = "Événements de la fenêtre"

# LL2 status abbreviations, mapped to what we show.
_STATUS_LABEL = {
    "Go": "confirmé",
    "TBC": "à confirmer",
    "TBD": "date non figée",
    "Hold": "suspendu",
    "In Flight": "en vol",
    "Success": "succès",
    "Failure": "échec",
    "Partial Failure": "échec partiel",
    "On Hold": "suspendu",
}

# Drives the colour of the status pill in the renderer.
_STATUS_TONE = {
    "Success": "ok",
    "Failure": "fail",
    "Partial Failure": "fail",
    "Go": "go",
    "TBC": "warn",
    "TBD": "warn",
    "Hold": "warn",
    "On Hold": "warn",
    "In Flight": "live",
}

_EVENT_TYPE_LABEL = {
    "Press Event": "conférence de presse",
    "EVA": "sortie extravéhiculaire",
    "Spacewalk": "sortie extravéhiculaire",
    "Celestial Event": "événement céleste",
    "Docking": "amarrage",
    "Undocking": "désamarrage",
    "Berthing": "amarrage",
    "Unberthing": "désamarrage",
    "Landing": "atterrissage",
    "Spacecraft Event": "opération de vaisseau",
    "Static Fire": "essai statique",
    "Test": "essai",
    "Launch Hazard Area": "zone d'exclusion",
    "Tour": "visite",
    "Media Event": "événement média",
}


@dataclass
class Launch:
    """One launch, flattened from the API payload."""

    name: str
    net: Optional[datetime]
    net_precision: str
    status: str
    provider: str
    rocket: str
    mission: str
    mission_type: str
    orbit: str
    pad: str
    location: str
    url: str = ""
    failreason: str = ""
    # Drives the colour of the status pill; derived from the API's abbreviation.
    tone: str = ""
    logo_url: str = ""
    provider_abbrev: str = ""
    # Performance figures, from the launcher configuration.
    reusable: Optional[bool] = None
    leo_capacity: Optional[float] = None
    gto_capacity: Optional[float] = None
    launch_mass: Optional[float] = None
    thrust: Optional[float] = None
    length: Optional[float] = None
    diameter: Optional[float] = None
    launch_cost: Optional[int] = None
    maiden_flight: str = ""
    fastest_turnaround: str = ""
    # Track record of the launcher configuration.
    total_launches: Optional[int] = None
    successes: Optional[int] = None
    failures: Optional[int] = None
    consecutive_successes: Optional[int] = None
    # Conditions on the day.
    probability: Optional[int] = None
    weather_concerns: str = ""
    # Latest human-written note from the LL2 contributors.
    update_comment: str = ""
    update_author: str = ""
    update_date: Optional[datetime] = None
    # Reference pages.
    rocket_wiki: str = ""
    provider_wiki: str = ""
    watched: bool = False
    region: str = ""

    @property
    def is_past(self) -> bool:
        return bool(self.net and self.net < datetime.now(timezone.utc))


@dataclass
class Event:
    """One space event — EVA, press conference, docking, eclipse…"""

    name: str
    date: Optional[datetime]
    date_precision: str
    type: str
    description: str = ""
    location: str = ""
    url: str = ""
    duration: str = ""
    image_url: str = ""
    webcast_live: bool = False
    agencies: list[str] = field(default_factory=list)
    related_launches: list[str] = field(default_factory=list)
    watched: bool = False


# ---------------------------------------------------------------------------
# Watch list
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase, strip accents — so 'Avio S.p.A' matches 'avio'."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


@dataclass
class WatchList:
    """Matches a launch against the operators declared in competitors.yaml."""

    # (needle, region) pairs, pre-normalized
    needles: list[tuple[str, str]] = field(default_factory=list)
    routine: list[str] = field(default_factory=list)

    def match(self, provider: str, rocket: str) -> Optional[str]:
        """Returns the region of the matching entry, or None."""
        haystack = f"{_normalize(provider)} | {_normalize(rocket)}"
        for needle, region in self.needles:
            if needle in haystack:
                return region
        return None

    def is_routine(self, mission: str, name: str) -> bool:
        blob = _normalize(f"{mission} {name}")
        return any(_normalize(r) in blob for r in self.routine)


@lru_cache(maxsize=None)
def load_watchlist(path: Path = COMPETITORS_PATH) -> WatchList:
    if not path.exists():
        log.warning("No competitors.yaml at %s — no launch will be highlighted", path)
        return WatchList()

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    needles: list[tuple[str, str]] = []
    for entry in data.get("competitors") or []:
        region = entry.get("region", "")
        terms = [entry.get("name", "")]
        terms += entry.get("aliases") or []
        terms += entry.get("vehicles") or []
        for term in terms:
            if term:
                needles.append((_normalize(term), region))

    # Longest first: "Rocket Factory Augsburg" must win over "Rocket".
    needles.sort(key=lambda n: len(n[0]), reverse=True)

    return WatchList(needles=needles, routine=data.get("routine_missions") or [])


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _get(url: str, timeout: int) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _thumbnail(image: dict) -> str:
    """Square 256×256 thumbnail of an LL2 image, falling back to the original."""
    image = image or {}
    return image.get("thumbnail_url") or image.get("image_url") or ""


def _provider_logo(lsp: dict) -> str:
    """Best square logo for an operator.

    `social_logo` is the one to use: it is square for every operator checked
    and usually ships its own opaque backdrop, so a white wordmark stays
    readable on the white card. Its thumbnail is a clean downscale — unlike
    `logo.thumbnail_url`, which centre-crops a wide wordmark down to two
    unreadable letters. Plain `logo.image_url` is the last resort, kept at
    full aspect ratio and constrained by the renderer.
    """
    social = lsp.get("social_logo") or {}
    logo = lsp.get("logo") or {}
    return (
        social.get("thumbnail_url")
        or social.get("image_url")
        or logo.get("image_url")
        or ""
    )


def _latest_update(raw: dict) -> dict:
    """Most recent contributor note, by creation date."""
    updates = raw.get("updates") or []
    if not updates:
        return {}
    return max(updates, key=lambda u: u.get("created_on") or "")


def _flatten(raw: dict, watchlist: WatchList) -> Launch:
    lsp = raw.get("launch_service_provider") or {}
    provider = lsp.get("name", "")
    config = ((raw.get("rocket") or {}).get("configuration") or {})
    rocket = config.get("full_name") or config.get("name") or ""
    mission = raw.get("mission") or {}
    pad = raw.get("pad") or {}
    status = raw.get("status") or {}
    abbrev = status.get("abbrev", "")
    update = _latest_update(raw)

    region = watchlist.match(provider, rocket)

    return Launch(
        name=raw.get("name", ""),
        net=_parse_dt(raw.get("net", "")),
        net_precision=(raw.get("net_precision") or {}).get("name", ""),
        status=_STATUS_LABEL.get(abbrev, abbrev),
        tone=_STATUS_TONE.get(abbrev, ""),
        provider=provider,
        rocket=rocket,
        mission=mission.get("name", ""),
        mission_type=mission.get("type", "") or "",
        orbit=(mission.get("orbit") or {}).get("name", "") or "",
        pad=pad.get("name", "") or "",
        location=(pad.get("location") or {}).get("name", "") or "",
        url=raw.get("url", "") or "",
        failreason=raw.get("failreason") or "",
        logo_url=_provider_logo(lsp),
        provider_abbrev=lsp.get("abbrev") or provider,
        reusable=config.get("reusable"),
        leo_capacity=config.get("leo_capacity"),
        gto_capacity=config.get("gto_capacity"),
        launch_mass=config.get("launch_mass"),
        thrust=config.get("to_thrust"),
        length=config.get("length"),
        diameter=config.get("diameter"),
        launch_cost=config.get("launch_cost"),
        maiden_flight=config.get("maiden_flight") or "",
        fastest_turnaround=config.get("fastest_turnaround") or "",
        total_launches=config.get("total_launch_count"),
        successes=config.get("successful_launches"),
        failures=config.get("failed_launches"),
        consecutive_successes=config.get("consecutive_successful_launches"),
        probability=raw.get("probability"),
        weather_concerns=raw.get("weather_concerns") or "",
        update_comment=update.get("comment") or "",
        update_author=update.get("created_by") or "",
        update_date=_parse_dt(update.get("created_on") or ""),
        rocket_wiki=config.get("wiki_url") or "",
        provider_wiki=lsp.get("wiki_url") or "",
        watched=region is not None,
        region=region or "",
    )


def _flatten_event(raw: dict, watchlist: WatchList) -> Event:
    type_name = (raw.get("type") or {}).get("name", "") or ""
    agencies = [a.get("name", "") for a in (raw.get("agencies") or []) if a.get("name")]
    launches = [l.get("name", "") for l in (raw.get("launches") or []) if l.get("name")]

    # An event counts as watched when a tracked operator is behind it, or when
    # it hangs off one of their launches.
    watched = any(
        watchlist.match(agency, "") is not None for agency in agencies
    ) or any(watchlist.match("", name) is not None for name in launches)

    info_urls = raw.get("info_urls") or []
    vid_urls = raw.get("vid_urls") or []
    url = ""
    if info_urls:
        url = info_urls[0].get("url", "") or ""
    if not url and vid_urls:
        url = vid_urls[0].get("url", "") or ""

    return Event(
        name=raw.get("name", ""),
        date=_parse_dt(raw.get("date", "")),
        date_precision=(raw.get("date_precision") or {}).get("name", "") or "",
        type=_EVENT_TYPE_LABEL.get(type_name, type_name.lower()),
        description=raw.get("description") or "",
        location=raw.get("location") or "",
        url=url,
        duration=raw.get("duration") or "",
        image_url=_thumbnail(raw.get("image")),
        webcast_live=bool(raw.get("webcast_live")),
        agencies=agencies,
        related_launches=launches,
        watched=watched,
    )


def _window(days_back: int, days_ahead: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        (now - timedelta(days=days_back)).strftime(fmt),
        (now + timedelta(days=days_ahead)).strftime(fmt),
    )


def _fetch(root: str, params: dict, timeout: int, what: str) -> list[dict]:
    url = root + "?" + urllib.parse.urlencode(params)
    try:
        return _get(url, timeout).get("results", [])
    except urllib.error.HTTPError as e:
        # 429 is the usual one: the free tier allows a handful of calls per hour.
        log.error("Launch Library returned HTTP %s — skipping %s", e.code, what)
    except Exception as e:
        log.error("Could not reach Launch Library (%s) — skipping %s", e, what)
    return []


def fetch_launches(
    days_back: int = 1,
    days_ahead: int = 1,
    limit: int = 40,
    timeout: int = 30,
) -> list[Launch]:
    """Launches whose T-0 falls in [now - days_back, now + days_ahead]."""
    start, end = _window(days_back, days_ahead)
    results = _fetch(
        API_ROOT,
        {
            "net__gte": start,
            "net__lte": end,
            "ordering": "net",
            "limit": str(limit),
            "mode": "detailed",
        },
        timeout,
        "the launch section",
    )

    watchlist = load_watchlist()
    launches = [_flatten(raw, watchlist) for raw in results]
    log.info(
        "Launch window: %d launches (%d watched)",
        len(launches), sum(1 for l in launches if l.watched),
    )
    return launches


def fetch_events(
    days_back: int = 1,
    days_ahead: int = 1,
    limit: int = 10,
    timeout: int = 30,
) -> list[Event]:
    """Events dated within the same window as the launches.

    Events are sparse — a handful a week — so this list is often empty, and
    that is fine: the sub-block simply does not appear.
    """
    start, end = _window(days_back, days_ahead)
    results = _fetch(
        EVENTS_ROOT,
        {
            "date__gte": start,
            "date__lte": end,
            "ordering": "date",
            "limit": str(limit),
            "mode": "detailed",
        },
        timeout,
        "the events sub-block",
    )

    watchlist = load_watchlist()
    events = [_flatten_event(raw, watchlist) for raw in results]
    log.info("Event window: %d events", len(events))
    return events


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


_DAY_WORDS = {-1: "hier", 1: "demain"}

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>[\d.]+)S)?)?$"
)


# Thousands are grouped with a no-break space (U+00A0): correct French
# typography, and it stops the mail from splitting "22 800" across two lines.
def _num(value: float) -> str:
    """12345.6 -> '12 346' — French thousands separator."""
    return f"{value:,.0f}".replace(",", " ")


def _decimal(value: float) -> str:
    """3.65 -> '3,65', 70.0 -> '70'."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _duration_fr(value: str) -> str:
    """ISO 8601 duration -> '27 min', '1 h 05', '44 j'."""
    match = _DURATION_RE.match(value or "")
    if not match:
        return ""
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)

    if days:
        return f"{days} j" + (f" {hours} h" if hours else "")
    if hours:
        return f"{hours} h {minutes:02d}" if minutes else f"{hours} h"
    if minutes:
        return f"{minutes} min"
    return ""


def _date_fr(iso_date: str) -> str:
    """'2018-05-11' -> '11/05/2018'."""
    try:
        return datetime.strptime(iso_date[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return ""


def _clip(text: str, limit: int) -> str:
    """Trims to `limit` characters on a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def _when_at(moment: Optional[datetime], precision: str, today: datetime) -> str:
    """Day and time in Paris time, as one phrase.

    The API's precision field says how much of the timestamp to trust: a slot
    given to the hour is announced as approximate rather than as a false
    minute. Today's entries drop the day word — the mail is already dated.
    """
    if not moment:
        return "date inconnue"

    local = moment.astimezone(PARIS)
    delta = (local.date() - today.astimezone(PARIS).date()).days
    day = _DAY_WORDS.get(delta) or local.strftime("%d/%m")

    if precision in ("Minute", "Second", ""):
        time = local.strftime("%Hh%M")
    elif precision == "Hour":
        time = local.strftime("vers %Hh")
    else:
        return "aujourd'hui, heure non figée" if delta == 0 else f"{day}, heure non figée"

    return time if delta == 0 else f"{day} {time}"


def _when(launch: Launch, today: datetime) -> str:
    return _when_at(launch.net, launch.net_precision, today)


def _performance(launch: Launch) -> str:
    """Vehicle capability: reusability, payload, thrust, size, price."""
    bits = []
    if launch.reusable is not None:
        bits.append("réutilisable" if launch.reusable else "consommable")
    if launch.leo_capacity:
        bits.append(f"{_num(launch.leo_capacity)} kg en LEO")
    if launch.gto_capacity:
        bits.append(f"{_num(launch.gto_capacity)} kg en GTO")
    if launch.thrust:
        bits.append(f"{_num(launch.thrust)} kN de poussée")
    if launch.length and launch.diameter:
        bits.append(f"{_decimal(launch.length)} × {_decimal(launch.diameter)} m")
    if launch.launch_mass:
        bits.append(f"{_num(launch.launch_mass)} t au décollage")
    if launch.launch_cost:
        bits.append(f"{_num(launch.launch_cost / 1e6)} M$ le tir")
    return " · ".join(bits)


def _reliability(launch: Launch) -> str:
    """Track record of this launcher configuration."""
    total = launch.total_launches
    if not total:
        return ""

    bits = [f"{total} tir{'s' if total > 1 else ''}"]
    if launch.successes is not None:
        rate = 100 * launch.successes / total
        bits.append(f"{rate:.1f}".replace(".", ",") + " % de succès")
    fails = launch.failures
    if fails:
        bits.append(f"{fails} échec{'s' if fails > 1 else ''}")
    elif fails == 0:
        bits.append("aucun échec")
    if launch.consecutive_successes:
        bits.append(f"{launch.consecutive_successes} succès d'affilée")
    if launch.maiden_flight:
        first = _date_fr(launch.maiden_flight)
        if first:
            bits.append(f"premier vol le {first}")
    turnaround = _duration_fr(launch.fastest_turnaround)
    if turnaround:
        bits.append(f"rotation record {turnaround}")
    return " · ".join(bits)


def _weather(launch: Launch) -> str:
    """Conditions on the pad. LL2 uses a negative probability for 'unknown'."""
    bits = []
    if launch.probability is not None and launch.probability >= 0:
        bits.append(f"{launch.probability} % de conditions favorables")
    if launch.weather_concerns:
        bits.append(f"contraintes : {launch.weather_concerns}")
    return " · ".join(bits)


def _last_update(launch: Launch, today: datetime) -> str:
    """The latest contributor note — often the freshest word on a slipping T-0."""
    if not launch.update_comment:
        return ""
    note = f"« {_clip(launch.update_comment, 180)} »"
    meta = []
    if launch.update_author:
        meta.append(launch.update_author)
    if launch.update_date:
        meta.append(_when_at(launch.update_date, "Minute", today))
    return f"{note} — {', '.join(meta)}" if meta else note


def _wiki_links(launch: Launch) -> list[tuple[str, str]]:
    links = []
    if launch.rocket_wiki:
        links.append((f"Wikipédia · {launch.rocket}", launch.rocket_wiki))
    if launch.provider_wiki:
        links.append((f"Wikipédia · {launch.provider}", launch.provider_wiki))
    return links


def _detailed_item(launch: Launch, today: datetime) -> Item:
    when = _when(launch, today)
    headline = f"{launch.rocket} · {launch.mission or 'charge utile non précisée'}"

    context = [launch.provider]
    if launch.orbit and launch.orbit != "Unknown":
        context.append(launch.orbit)
    where = " — ".join(p for p in (launch.pad, launch.location) if p)
    if where:
        context.append(where)

    details = []
    for label, value in (
        ("Performance", _performance(launch)),
        ("Fiabilité", _reliability(launch)),
        ("Météo", _weather(launch)),
        ("Dernier point", _last_update(launch, today)),
    ):
        if value:
            details.append(f"{label} : {value}")
    if launch.failreason:
        details.append(f"Cause de l'échec : {launch.failreason}")

    return Item(
        title=f"{when} — {headline}",
        url=launch.url,
        source=launch.status,
        tone=launch.tone,
        summary=" · ".join(context),
        details=details,
        links=_wiki_links(launch),
        image_url=launch.logo_url,
        image_alt=launch.provider_abbrev,
        accent=True,
    )


def _compact_item(launch: Launch, today: datetime) -> Item:
    when = _when(launch, today)
    tail = " · ".join(p for p in (launch.provider, launch.location) if p)
    return Item(
        title=f"{when} — {launch.rocket} · {launch.mission or '—'}",
        url=launch.url,
        source=launch.status,
        tone=launch.tone,
        summary=tail,
        image_url=launch.logo_url,
        image_alt=launch.provider_abbrev,
    )


def _routine_item(launches: list[Launch], today: datetime, label: str) -> Item:
    times = ", ".join(_when(l, today) for l in launches if l.net)
    count = len(launches)
    # All the grouped flights share an operator, so the first logo speaks for
    # the batch.
    first = launches[0]
    return Item(
        title=f"{count} vol{'s' if count > 1 else ''} {label}",
        summary=f"Déploiement de constellation — {times}." if times else "Déploiement de constellation.",
        image_url=first.logo_url,
        image_alt=first.provider_abbrev,
    )


def _event_item(event: Event, today: datetime) -> Item:
    when = _when_at(event.date, event.date_precision, today)

    context = [p for p in (event.location,) if p]
    duration = _duration_fr(event.duration)
    if duration:
        context.append(f"durée {duration}")
    if event.agencies:
        context.append(" / ".join(event.agencies[:2]))

    details = []
    if event.description:
        details.append(_clip(event.description, 240))
    if event.related_launches:
        details.append(f"Rattaché au tir : {', '.join(event.related_launches)}")

    return Item(
        title=f"{when} — {event.name}",
        url=event.url,
        source="en direct" if event.webcast_live else event.type,
        tone="live" if event.webcast_live else "",
        summary=" · ".join(context),
        details=details,
        image_url=event.image_url,
        image_alt=event.type,
        accent=event.watched,
    )


def _subtitle(launches: list[Launch], events: list[Event]) -> str:
    parts = []
    if launches:
        count = len(launches)
        parts.append(f"{count} tir{'s' if count > 1 else ''} sur la fenêtre")
    if events:
        count = len(events)
        parts.append(f"{count} événement{'s' if count > 1 else ''}")
    parts.append("heures de Paris")

    watched = sum(1 for l in launches if l.watched)
    if watched:
        plural = "s" if watched > 1 else ""
        parts.append(f"{watched} concurrent{plural} suivi{plural}")
    return " · ".join(parts)


def build_section(
    launches: list[Launch],
    events: Optional[list[Event]] = None,
) -> Optional[Section]:
    """Turns the launch window into a digest section, or None if empty."""
    events = events or []
    if not launches and not events:
        return None

    watchlist = load_watchlist()
    today = datetime.now(timezone.utc)

    routine: dict[str, list[Launch]] = {}
    items: list[Item] = []

    for launch in launches:
        if not launch.watched and watchlist.is_routine(launch.mission, launch.name):
            # Grouped rather than dropped: the count still says something
            # about a competitor's cadence.
            label = launch.mission.split()[0] if launch.mission else "constellation"
            routine.setdefault(label, []).append(launch)
            continue
        items.append(
            _detailed_item(launch, today) if launch.watched
            else _compact_item(launch, today)
        )

    for label, group in routine.items():
        items.append(_routine_item(group, today, label))

    if events:
        # The heading only earns its place when it separates two blocks.
        if items:
            items.append(Item(title=EVENTS_HEADING, heading=True))
        items.extend(_event_item(e, today) for e in events)

    return Section(
        key="launches",
        title=SECTION_TITLE if launches else EVENTS_TITLE,
        items=items,
        subtitle=_subtitle(launches, events),
    )
