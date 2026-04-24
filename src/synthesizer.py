"""
Synthesizes fetched articles into a daily digest using the Mistral API.

Returns a DigestResult with:
  - html_body: full HTML for the email
  - plain_body: plain-text fallback
  - new_dated_memories: list of new dated entries to save
  - new_permanent_memories: list of new permanent facts to save
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

from mistralai.client import Mistral

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Tu es un assistant expert en actualité spatiale, destiné à un opérateur de fusée professionnel. \
Tu reçois chaque matin une liste d'articles récents issus de flux RSS \
ainsi qu'une mémoire persistante des événements et contextes importants. \
Ta mission : produire un digest quotidien en français, clair, structuré \
et informatif, destiné à être envoyé par email.

Profil du lecteur : opérateur de fusée. Ses priorités, dans l'ordre :
1. Lanceurs et fusées — tout ce qui concerne les lancements, les véhicules de lancement, \
les moteurs, les infrastructures de lancement, les succès et échecs de tirs, \
les nouveaux contrats de lancement, les calendriers de vol.
2. Exploration spatiale — missions habitées et robotiques, sondes, rovers, \
stations spatiales, projets Luna/Mars/au-delà.
3. Nouvelles technologies spatiales — propulsion avancée, matériaux, \
systèmes embarqués, innovations liées au spatial.

Les autres sujets (défense générale, médias, tech grand public) ne doivent apparaître \
qu'en fin de digest dans une section "Autres actualités", uniquement s'ils ont \
un lien indirect pertinent avec le secteur spatial. Sinon, les ignorer.

Règles de rédaction :
- Rédige en français, même pour les articles anglophones.
- Structure les sections dans l'ordre de priorité ci-dessus.
- Les points marquants doivent refléter cette hiérarchie : prioriser les news lanceurs.
- Si un article concerne un événement à venir (lancement, annonce), note-le dans les mémoires datées.
- Si un article apporte un fait de contexte durable (nouveau lanceur confirmé, \
  résultat de tir, contrat majeur), note-le en mémoire permanente.
- Ne répète pas les faits déjà présents dans la mémoire.
- Sois synthétique et précis : une info = une phrase claire + source.
- Réponds UNIQUEMENT avec du JSON valide, sans texte avant ni après, sans balises markdown."""

ARTICLE_TEMPLATE = "- [{title}] ({feed}) : {summary}"

JSON_SCHEMA = """\
{
  "points_marquants": ["phrase 1", "phrase 2"],
  "sections": [
    {
      "titre": "Titre de section",
      "articles": [
        {"titre": "...", "url": "...", "source": "...", "resume": "..."}
      ]
    }
  ],
  "nouvelles_memoires_datees": ["- YYYY-MM-DD | Titre | Description"],
  "nouvelles_memoires_permanentes": ["- Fait de fond court"]
}"""


@dataclass
class DigestResult:
    html_body: str
    plain_body: str
    new_dated_memories: list[str] = field(default_factory=list)
    new_permanent_memories: list[str] = field(default_factory=list)


def _format_articles(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        summary = a["summary"][:250] if a["summary"] else "(pas de résumé)"
        lines.append(
            ARTICLE_TEMPLATE.format(
                title=a["title"],
                feed=a["feed_name"],
                summary=summary,
            )
        )
    return "\n".join(lines)


def _extract_json(raw: str) -> dict:
    """Extracts JSON from model output, tolerating markdown fences and leading text."""
    # Remove markdown fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } block
    start = cleaned.find("{")
    if start != -1:
        # Walk backwards from the end to find matching closing brace
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"Could not parse JSON from model output:\n{raw[:600]}")


def synthesize(
    articles: list[dict],
    memory_content: str,
    reminders: list[str],
    model: str,
    api_key: str,
    **_kwargs,
) -> DigestResult:
    if not articles:
        log.warning("No articles to synthesize")
        return DigestResult(
            html_body=_render_empty(),
            plain_body="Aucun article récent trouvé aujourd'hui.",
        )

    client = Mistral(api_key=api_key)
    today = date.today().strftime("%A %d %B %Y")
    articles_text = _format_articles(articles)

    reminders_block = ""
    if reminders:
        items = "\n".join(f"- {r}" for r in reminders)
        reminders_block = f"\n\nRAPPELS DU JOUR:\n{items}"

    user_message = f"""\
MÉMOIRE PERSISTANTE:
{memory_content}

---
DATE: {today}
ARTICLES ({len(articles)}):
{articles_text}
{reminders_block}

---
Génère le digest en respectant EXACTEMENT ce schéma JSON:
{JSON_SCHEMA}
"""

    log.info("Calling Mistral API (model: %s, articles: %d) ...", model, len(articles))

    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=4000,
    )

    raw = response.choices[0].message.content.strip()
    log.info("Mistral response received (%d chars)", len(raw))


    try:
        data = _extract_json(raw)
    except ValueError as e:
        log.error("JSON parse failed: %s", e)
        # Send the raw text rather than silent failure
        return DigestResult(
            html_body=_render_error(raw, today),
            plain_body=raw,
        )

    return DigestResult(
        html_body=_render_html(data, today),
        plain_body=_render_plain(data, today),
        new_dated_memories=data.get("nouvelles_memoires_datees", []),
        new_permanent_memories=data.get("nouvelles_memoires_permanentes", []),
    )


# ---------------------------------------------------------------------------
# Email rendering — all styles are inline for maximum email client compat
# ---------------------------------------------------------------------------

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


def _td(content: str, style: str = "") -> str:
    return f'<td style="{style}">{content}</td>'


def _render_html(data: dict, today: str) -> str:
    sections_html = _sections_block(data.get("sections", []))
    points_html   = _points_block(data.get("points_marquants", []))

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
      <p style="margin:10px 0 0;font-size:14px;color:#94b4cc;">{today}</p>
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
        f'font-size:14px;color:{_C_TEXT};line-height:1.5;">{p}</td></tr>'
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


def _sections_block(sections: list[dict]) -> str:
    if not sections:
        return ""
    html_parts = []
    for section in sections:
        articles_html = "".join(_article_row(a) for a in section.get("articles", []))
        html_parts.append(f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
  <tr>
    <td style="padding-bottom:10px;border-bottom:2px solid {_C_SECTION};">
      <h2 style="margin:0;font-size:15px;font-weight:bold;color:{_C_SECTION};
                 font-family:Arial,sans-serif;text-transform:uppercase;letter-spacing:1px;">
        {section['titre']}
      </h2>
    </td>
  </tr>
  <tr><td style="padding-top:14px;">{articles_html}</td></tr>
</table>""")
    return "\n".join(html_parts)


def _article_row(art: dict) -> str:
    url    = art.get("url", "#")
    titre  = art.get("titre", "")
    source = art.get("source", "")
    resume = art.get("resume", "")
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;">
  <tr>
    <td style="padding-left:12px;border-left:3px solid {_C_BORDER};">
      <a href="{url}" style="font-size:14px;font-weight:bold;color:{_C_ACCENT};
                             text-decoration:none;line-height:1.4;">{titre}</a>
      <span style="font-size:11px;color:{_C_MUTED};font-family:Arial,sans-serif;
                   margin-left:6px;">— {source}</span>
      <p style="margin:4px 0 0;font-size:13px;color:{_C_TEXT};line-height:1.5;">{resume}</p>
    </td>
  </tr>
</table>"""


def _render_error(raw: str, today: str) -> str:
    """Fallback email when JSON parsing fails — shows raw output."""
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:620px;margin:40px auto;color:#333;">
  <h2 style="color:#c0392b;">Digest du {today} — erreur de rendu</h2>
  <p>Le modèle a répondu mais la structure JSON n'a pas pu être analysée.</p>
  <pre style="background:#f8f8f8;padding:16px;border-radius:6px;
              font-size:12px;overflow-x:auto;white-space:pre-wrap;">{raw}</pre>
</body></html>"""


def _render_empty() -> str:
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:Georgia,serif;max-width:620px;margin:40px auto;color:#555;text-align:center;">
  <h2>Des nouvelles des étoiles</h2>
  <p>Aucun nouvel article trouvé aujourd'hui.</p>
</body></html>"""


def _render_plain(data: dict, today: str) -> str:
    lines = [f"Des nouvelles des étoiles — {today}", "=" * 50, ""]

    points = data.get("points_marquants", [])
    if points:
        lines.append("POINTS MARQUANTS")
        for p in points:
            lines.append(f"  • {p}")
        lines.append("")

    for section in data.get("sections", []):
        lines.append(section["titre"].upper())
        lines.append("-" * len(section["titre"]))
        for art in section.get("articles", []):
            lines.append(f'  [{art["source"]}] {art["titre"]}')
            lines.append(f'  {art["resume"]}')
            lines.append(f'  {art.get("url", "")}')
            lines.append("")

    return "\n".join(lines)
