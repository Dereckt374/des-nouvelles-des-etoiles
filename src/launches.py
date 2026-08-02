"""
Launch schedule, from the Launch Library 2 API (thespacedevs.com).

Covers a window around today: past launches come back with their outcome
(success / failure), upcoming ones with their status and T-0.

The section is rendered from the API payload as-is, without going through the
LLM: times, statuses and outcomes are facts, and a model paraphrasing them is
a model that can get a T-0 wrong. Watched operators (config/competitors.yaml)
get a detailed card, everyone else a one-liner.
"""

import json
import logging
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
COMPETITORS_PATH = Path(__file__).parent.parent / "config" / "competitors.yaml"

PARIS = ZoneInfo("Europe/Paris")
USER_AGENT = "des-nouvelles-des-etoiles/1.0"

SECTION_TITLE = "Lancements"

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
    reusable: Optional[bool] = None
    leo_capacity: Optional[float] = None
    launch_cost: Optional[int] = None
    total_launches: Optional[int] = None
    successes: Optional[int] = None
    failures: Optional[int] = None
    watched: bool = False
    region: str = ""

    @property
    def is_past(self) -> bool:
        return bool(self.net and self.net < datetime.now(timezone.utc))


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


def _flatten(raw: dict, watchlist: WatchList) -> Launch:
    provider = (raw.get("launch_service_provider") or {}).get("name", "")
    config = ((raw.get("rocket") or {}).get("configuration") or {})
    rocket = config.get("full_name") or config.get("name") or ""
    mission = raw.get("mission") or {}
    pad = raw.get("pad") or {}
    status = raw.get("status") or {}

    region = watchlist.match(provider, rocket)

    return Launch(
        name=raw.get("name", ""),
        net=_parse_dt(raw.get("net", "")),
        net_precision=(raw.get("net_precision") or {}).get("name", ""),
        status=_STATUS_LABEL.get(status.get("abbrev", ""), status.get("abbrev", "")),
        provider=provider,
        rocket=rocket,
        mission=mission.get("name", ""),
        mission_type=mission.get("type", "") or "",
        orbit=(mission.get("orbit") or {}).get("name", "") or "",
        pad=pad.get("name", "") or "",
        location=(pad.get("location") or {}).get("name", "") or "",
        url=raw.get("url", "") or "",
        failreason=raw.get("failreason") or "",
        reusable=config.get("reusable"),
        leo_capacity=config.get("leo_capacity"),
        launch_cost=config.get("launch_cost"),
        total_launches=config.get("total_launch_count"),
        successes=config.get("successful_launches"),
        failures=config.get("failed_launches"),
        watched=region is not None,
        region=region or "",
    )


def fetch_launches(
    days_back: int = 1,
    days_ahead: int = 1,
    limit: int = 40,
    timeout: int = 30,
) -> list[Launch]:
    """Launches whose T-0 falls in [now - days_back, now + days_ahead].

    Network or API failures are swallowed: a missing launch section must never
    cost the reader the rest of the digest.
    """
    now = datetime.now(timezone.utc)
    params = {
        "net__gte": (now - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": (now + timedelta(days=days_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ordering": "net",
        "limit": str(limit),
        "mode": "detailed",
    }
    url = API_ROOT + "?" + urllib.parse.urlencode(params)

    try:
        payload = _get(url, timeout)
    except urllib.error.HTTPError as e:
        # 429 is the usual one: the free tier allows a handful of calls per hour.
        log.error("Launch Library returned HTTP %s — skipping launch section", e.code)
        return []
    except Exception as e:
        log.error("Could not reach Launch Library (%s) — skipping launch section", e)
        return []

    watchlist = load_watchlist()
    launches = [_flatten(raw, watchlist) for raw in payload.get("results", [])]
    log.info(
        "Launch window: %d launches (%d watched)",
        len(launches), sum(1 for l in launches if l.watched),
    )
    return launches


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


_DAY_WORDS = {-1: "hier", 1: "demain"}


def _when(launch: Launch, today: datetime) -> str:
    """Day and T-0 in Paris time, as one phrase.

    The API's `net_precision` says how much of the timestamp to trust: a slot
    given to the hour is announced as approximate rather than as a false
    minute. Today's launches drop the day word — the mail is already dated.
    """
    if not launch.net:
        return "date inconnue"

    local = launch.net.astimezone(PARIS)
    delta = (local.date() - today.astimezone(PARIS).date()).days
    day = _DAY_WORDS.get(delta) or local.strftime("%d/%m")

    precision = launch.net_precision
    if precision in ("Minute", "Second", ""):
        time = local.strftime("%Hh%M")
    elif precision == "Hour":
        time = local.strftime("vers %Hh")
    else:
        return "aujourd'hui, heure non figée" if delta == 0 else f"{day}, heure non figée"

    return time if delta == 0 else f"{day} {time}"


def _reliability(launch: Launch) -> str:
    total = launch.total_launches
    if not total:
        return ""
    shots = f"{total} tir{'s' if total > 1 else ''}"
    fails = launch.failures or 0
    if fails == 0:
        return f"{shots}, aucun échec"
    return f"{shots}, {fails} échec{'s' if fails > 1 else ''}"


def _tech_specs(launch: Launch) -> str:
    """The competitive-analysis line: reusability, capacity, cost, reliability."""
    bits = []
    if launch.reusable is not None:
        bits.append("réutilisable" if launch.reusable else "consommable")
    if launch.leo_capacity:
        bits.append(f"{launch.leo_capacity:,.0f} kg en LEO".replace(",", " "))
    if launch.launch_cost:
        bits.append(f"{launch.launch_cost / 1e6:,.0f} M$ le tir".replace(",", " "))
    reliability = _reliability(launch)
    if reliability:
        bits.append(reliability)
    return " · ".join(bits)


def _detailed_item(launch: Launch, today: datetime) -> Item:
    when = _when(launch, today)
    headline = f"{launch.rocket} · {launch.mission or 'charge utile non précisée'}"

    context = [launch.provider]
    if launch.orbit and launch.orbit != "Unknown":
        context.append(launch.orbit)
    where = " — ".join(p for p in (launch.pad, launch.location) if p)
    if where:
        context.append(where)

    lines = [" · ".join(context)]
    specs = _tech_specs(launch)
    if specs:
        lines.append(specs)
    if launch.failreason:
        lines.append(f"Cause de l'échec : {launch.failreason}")

    return Item(
        title=f"{when} — {headline}",
        url=launch.url,
        source=launch.status,
        summary=" | ".join(lines),
        accent=True,
    )


def _compact_item(launch: Launch, today: datetime) -> Item:
    when = _when(launch, today)
    tail = " · ".join(p for p in (launch.provider, launch.location) if p)
    return Item(
        title=f"{when} — {launch.rocket} · {launch.mission or '—'}",
        url=launch.url,
        source=launch.status,
        summary=tail,
    )


def _routine_item(launches: list[Launch], today: datetime, label: str) -> Item:
    times = ", ".join(_when(l, today) for l in launches if l.net)
    count = len(launches)
    return Item(
        title=f"{count} vol{'s' if count > 1 else ''} {label}",
        summary=f"Déploiement de constellation — {times}." if times else "Déploiement de constellation.",
    )


def build_section(launches: list[Launch]) -> Optional[Section]:
    """Turns the launch window into a digest section, or None if empty."""
    if not launches:
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

    watched = sum(1 for l in launches if l.watched)
    subtitle = f"{len(launches)} tirs sur la fenêtre · heures de Paris"
    if watched:
        subtitle += f" · {watched} concurrent{'s' if watched > 1 else ''} suivi{'s' if watched > 1 else ''}"

    return Section(key="launches", title=SECTION_TITLE, items=items, subtitle=subtitle)
