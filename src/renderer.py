"""
Renders a Digest into the email bodies.

All styles are inline and the layout is table-based, for maximum
email-client compatibility.
"""

from html import escape

from models import Digest, Item, Section, order_sections

# Palette
_C_BG       = "#f4f6fb"
_C_CARD     = "#ffffff"
_C_HEADER   = "#0b1f3a"
_C_ACCENT   = "#d94f3d"
_C_SECTION  = "#1a3a5c"
_C_TEXT     = "#2c2c2c"
_C_MUTED    = "#6b7280"
_C_BORDER   = "#e2e8f0"
_C_HLBG     = "#fff7ed"
_C_HLBORDER = "#f59e0b"
_C_DETAIL   = "#4b5563"
# Backdrop of the logo tile. It only shows through for the rare operator whose
# logo is transparent; a mid grey keeps both pale and dark artwork legible.
_C_TILE     = "#6b7280"

# Status pill colours, keyed by Item.tone. Entries without a tone keep the
# plain muted label used by news and feed entries.
_TONES = {
    "ok":   ("#e7f6ec", "#1c7a3e"),
    "fail": ("#fdeaea", "#b3261e"),
    "warn": ("#fff4e0", "#a05a0f"),
    "go":   ("#e8f0fe", "#1a4fa0"),
    "live": ("#ffe8ef", "#b3124f"),
}

_LOGO_PX = 44
_LOGO_CELL_PX = 56


def _article_count_label(n: int) -> str:
    """"1 nouvel article" / "6 nouveaux articles"."""
    return f"{n} nouvel article" if n == 1 else f"{n} nouveaux articles"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def render_html(digest: Digest) -> str:
    if not digest.sections and not digest.highlights:
        return render_empty()

    points_html = _points_block(digest.highlights)
    sections_html = "\n".join(
        _section_block(s) for s in order_sections(digest.sections) if s.items
    )
    count = digest.article_count
    count_html = (
        f'<span style="margin-left:14px;font-size:12px;color:#7fa8d0;'
        f'font-family:Arial,sans-serif;">·&nbsp;'
        f'{_article_count_label(count).replace(" ", "&nbsp;")}</span>'
        if count
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_C_BG};font-family:Georgia,serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:{_C_BG};padding:32px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;">

  <!-- HEADER -->
  <tr>
    <td style="background:{_C_HEADER};border-radius:12px 12px 0 0;padding:32px 40px 24px;">
      <p style="margin:0 0 4px;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#7fa8d0;">
        Veille quotidienne
      </p>
      <h1 style="margin:0;font-size:26px;font-weight:bold;color:#ffffff;line-height:1.2;">
        Des nouvelles des étoiles
      </h1>
      <p style="margin:10px 0 0;font-size:14px;color:#94b4cc;">
        {digest.date_label}{count_html}
      </p>
    </td>
  </tr>

  <!-- BODY CARD -->
  <tr>
    <td style="background:{_C_CARD};padding:32px 40px;border-left:1px solid {_C_BORDER};border-right:1px solid {_C_BORDER};">
      {points_html}
      {sections_html}
    </td>
  </tr>

  <!-- FOOTER -->
  <tr>
    <td style="background:#e8edf5;border-radius:0 0 12px 12px;padding:16px 40px;border:1px solid {_C_BORDER};border-top:none;">
      <p style="margin:0;font-size:11px;color:{_C_MUTED};text-align:center;">
        Digest généré automatiquement · Des nouvelles des étoiles
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>

</body>
</html>"""


def _points_block(points: list[str]) -> str:
    if not points:
        return ""
    items = "".join(
        f'<tr><td style="padding:5px 0 5px 12px;border-left:3px solid {_C_HLBORDER};'
        f'font-size:14px;color:{_C_TEXT};line-height:1.5;">{escape(p)}</td></tr>'
        for p in points
    )
    return f"""
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:{_C_HLBG};border-radius:8px;padding:20px 24px;margin-bottom:28px;">
  <tr>
    <td>
      <p style="margin:0 0 12px;font-size:11px;letter-spacing:2px;text-transform:uppercase;
                color:{_C_ACCENT};font-family:Arial,sans-serif;font-weight:bold;">
        Points marquants
      </p>
      <table width="100%" cellpadding="0" cellspacing="4">{items}</table>
    </td>
  </tr>
</table>"""


def _section_block(section: Section) -> str:
    items_html = "".join(_item_row(i) for i in section.items)
    subtitle_html = (
        f'<p style="margin:6px 0 0;font-size:11px;color:{_C_MUTED};'
        f'font-family:Arial,sans-serif;font-style:italic;">{escape(section.subtitle)}</p>'
        if section.subtitle
        else ""
    )
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
  <tr>
    <td style="padding-bottom:10px;border-bottom:2px solid {_C_SECTION};">
      <h2 style="margin:0;font-size:15px;font-weight:bold;color:{_C_SECTION};
                 font-family:Arial,sans-serif;text-transform:uppercase;letter-spacing:1px;">
        {escape(section.title)}
      </h2>
      {subtitle_html}
    </td>
  </tr>
  <tr><td style="padding-top:14px;">{items_html}</td></tr>
</table>"""


def _meta_html(item: Item) -> str:
    """The status label next to a title — a coloured pill when toned."""
    if item.source and item.tone in _TONES:
        bg, fg = _TONES[item.tone]
        pill = (
            f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'background:{bg};color:{fg};font-size:10px;font-family:Arial,sans-serif;'
            f'font-weight:bold;letter-spacing:0.5px;text-transform:uppercase;'
            f'white-space:nowrap;">{escape(item.source)}</span>'
        )
        date = (
            f'<span style="font-size:11px;color:{_C_MUTED};font-family:Arial,sans-serif;'
            f'margin-left:6px;">{escape(item.date)}</span>'
            if item.date
            else ""
        )
        return f'<span style="margin-left:8px;">{pill}{date}</span>'

    parts = [f"— {item.source}"] if item.source else []
    if item.date:
        parts.append(item.date)
    if not parts:
        return ""
    return (
        f'<span style="font-size:11px;color:{_C_MUTED};font-family:Arial,sans-serif;'
        f'margin-left:6px;">{escape(" · ".join(parts))}</span>'
    )


def _details_html(details: list[str]) -> str:
    """Factual lines under a launch card, one per row."""
    if not details:
        return ""
    rows = "".join(
        f'<div style="margin:3px 0 0;font-size:12px;color:{_C_DETAIL};'
        f'line-height:1.55;">{escape(d)}</div>'
        for d in details
    )
    return f'<div style="margin-top:5px;">{rows}</div>'


def _links_html(links: list[tuple[str, str]]) -> str:
    """Reference pages — Wikipedia and the like — as a compact link row."""
    if not links:
        return ""
    anchors = " · ".join(
        f'<a href="{escape(url, quote=True)}" style="color:{_C_SECTION};'
        f'text-decoration:underline;">{escape(label)}</a>'
        for label, url in links
        if url
    )
    if not anchors:
        return ""
    return (
        f'<div style="margin-top:7px;font-size:11px;font-family:Arial,sans-serif;'
        f'color:{_C_MUTED};">{anchors}</div>'
    )


def _logo_cell(item: Item) -> str:
    """Square operator logo pinned left of the entry.

    Only the width is pinned: the artwork is square in practice, and a
    fallback of another ratio then scales down instead of overflowing. The
    alt text carries the operator abbreviation, so a client that blocks
    remote images still names who is flying.
    """
    return f"""
    <td valign="top" width="{_LOGO_CELL_PX}"
        style="width:{_LOGO_CELL_PX}px;padding-right:12px;">
      <img src="{escape(item.image_url, quote=True)}" width="{_LOGO_PX}"
           alt="{escape(item.image_alt, quote=True)}"
           style="display:block;width:{_LOGO_PX}px;max-width:{_LOGO_PX}px;height:auto;
                  border-radius:6px;background:{_C_TILE};">
    </td>"""


def _heading_row(item: Item) -> str:
    """Sub-header separating two blocks inside one section."""
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:4px 0 12px;">
  <tr>
    <td style="padding-top:12px;border-top:1px solid {_C_BORDER};">
      <p style="margin:0;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;
                color:{_C_MUTED};font-family:Arial,sans-serif;font-weight:bold;">
        {escape(item.title)}
      </p>
    </td>
  </tr>
</table>"""


def _item_row(item: Item) -> str:
    if item.heading:
        return _heading_row(item)

    summary_html = (
        f'<p style="margin:4px 0 0;font-size:13px;color:{_C_TEXT};line-height:1.5;">'
        f'{escape(item.summary)}</p>'
        if item.summary
        else ""
    )
    marker = _C_HLBORDER if item.accent else _C_BORDER
    title_style = f"font-size:14px;font-weight:bold;color:{_C_ACCENT};line-height:1.4;"
    # Entries without a source link (a grouped routine line) stay plain text
    # rather than becoming a dead anchor.
    title_html = (
        f'<a href="{escape(item.url, quote=True)}" style="{title_style}'
        f'text-decoration:none;">{escape(item.title)}</a>'
        if item.url
        else f'<span style="{title_style}">{escape(item.title)}</span>'
    )
    content = f"""
      {title_html}
      {_meta_html(item)}
      {summary_html}
      {_details_html(item.details)}
      {_links_html(item.links)}"""

    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
  <tr>
    {_logo_cell(item) if item.image_url else ""}
    <td valign="top" style="padding-left:12px;border-left:3px solid {marker};">{content}
    </td>
  </tr>
</table>"""


def render_error(raw: str, date_label: str) -> str:
    """Fallback email when JSON parsing fails — shows raw model output."""
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:620px;margin:40px auto;color:#333;">
  <h2 style="color:#c0392b;">Digest du {date_label} — erreur de rendu</h2>
  <p>Le modèle a répondu mais la structure JSON n'a pas pu être analysée.</p>
  <pre style="background:#f8f8f8;padding:16px;border-radius:6px;
              font-size:12px;overflow-x:auto;white-space:pre-wrap;">{escape(raw)}</pre>
</body></html>"""


def render_empty() -> str:
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:620px;margin:40px auto;color:#555;text-align:center;">
  <h2>Des nouvelles des étoiles</h2>
  <p>Aucun nouvel article trouvé aujourd'hui.</p>
</body></html>"""


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def render_plain(digest: Digest) -> str:
    count = digest.article_count
    suffix = f" · {_article_count_label(count)}" if count else ""
    lines = [f"Des nouvelles des étoiles — {digest.date_label}{suffix}", "=" * 50, ""]

    if digest.highlights:
        lines.append("POINTS MARQUANTS")
        lines.extend(f"  • {p}" for p in digest.highlights)
        lines.append("")

    for section in order_sections(digest.sections):
        if not section.items:
            continue
        lines.append(section.title.upper())
        lines.append("-" * len(section.title))
        if section.subtitle:
            lines.append(f"({section.subtitle})")
        for item in section.items:
            if item.heading:
                lines.append(f"  {item.title.upper()}")
                lines.append("")
                continue
            date_str = f" · {item.date}" if item.date else ""
            prefix = f"[{item.source}]{date_str} " if item.source or date_str else ""
            lines.append(f"  {prefix}{item.title}")
            if item.summary:
                lines.append(f"  {item.summary}")
            for detail in item.details:
                lines.append(f"    {detail}")
            for label, url in item.links:
                if url:
                    lines.append(f"    {label} : {url}")
            if item.url:
                lines.append(f"  {item.url}")
            lines.append("")

    return "\n".join(lines)
