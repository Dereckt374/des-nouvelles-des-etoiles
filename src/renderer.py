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


def _item_row(item: Item) -> str:
    meta_parts = [f"— {item.source}"] if item.source else []
    if item.date:
        meta_parts.append(item.date)
    meta = escape(" · ".join(meta_parts))
    summary_html = (
        f'<p style="margin:4px 0 0;font-size:13px;color:{_C_TEXT};line-height:1.5;">'
        f'{escape(item.summary)}</p>'
        if item.summary
        else ""
    )
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
  <tr>
    <td style="padding-left:12px;border-left:3px solid {_C_BORDER};">
      <a href="{escape(item.url or '#', quote=True)}" style="font-size:14px;font-weight:bold;color:{_C_ACCENT};
                             text-decoration:none;line-height:1.4;">{escape(item.title)}</a>
      <span style="font-size:11px;color:{_C_MUTED};font-family:Arial,sans-serif;
                   margin-left:6px;">{meta}</span>
      {summary_html}
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
            date_str = f" · {item.date}" if item.date else ""
            lines.append(f"  [{item.source}]{date_str} {item.title}")
            if item.summary:
                lines.append(f"  {item.summary}")
            lines.append(f"  {item.url}")
            lines.append("")

    return "\n".join(lines)
