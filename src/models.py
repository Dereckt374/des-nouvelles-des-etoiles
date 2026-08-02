"""
Typed building blocks shared by the whole pipeline.

A digest is a list of Sections rendered in a *fixed, predictable order*.
Sections are produced by different upstreams — some by the LLM (news),
some directly by a collector without any LLM involved (custom feeds) —
but they all end up as the same Section object, so the renderer stays
agnostic about where the content came from.

Adding a new feature = producing one more Section and giving it a rank below.
"""

from dataclasses import dataclass, field


@dataclass
class Item:
    """A single entry displayed inside a section."""

    title: str
    url: str = ""
    source: str = ""
    date: str = ""
    summary: str = ""


@dataclass
class Section:
    """A titled block of the digest.

    `key` is stable and drives ordering; `title` is what the reader sees
    and may vary from one run to the next (LLM-written news headings).
    """

    key: str
    title: str
    items: list[Item] = field(default_factory=list)
    subtitle: str = ""


@dataclass
class Digest:
    """Everything needed to render the email."""

    date_label: str
    highlights: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    article_count: int = 0


# Display order of the digest. Lower rank = higher in the email.
# Gaps are intentional: upcoming features slot in without renumbering.
SECTION_RANK = {
    "competition": 10,   # suivi de concurrence          (à venir)
    "launches": 15,      # lancements du jour            (à venir)
    "wikipedia": 20,     # wikipédia du jour             (à venir)
    "news": 30,          # suivi d'actualité             (sections LLM)
    "custom_feeds": 40,  # RSS customs (personnalités)
    "history": 50,       # histoire, anecdotes, images   (à venir)
}

# Unknown keys land with the news sections rather than at the very end.
DEFAULT_RANK = SECTION_RANK["news"]


def order_sections(sections: list[Section]) -> list[Section]:
    """Sorts sections by rank, preserving the original order within a rank.

    The sort is stable, so the several free-titled `news` sections keep the
    sequence the LLM gave them (which already follows the priority order
    stated in the system prompt).
    """
    return sorted(sections, key=lambda s: SECTION_RANK.get(s.key, DEFAULT_RANK))
