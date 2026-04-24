"""
Synthesizes fetched articles into a daily digest using a local Ollama model.

Returns a DigestResult with:
  - html_body: full HTML for the email
  - plain_body: plain-text fallback
  - new_dated_memories: list of new dated entries to save
  - new_permanent_memories: list of new permanent facts to save
"""

import json
import logging
import re
import requests
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger(__name__)

OLLAMA_DEFAULT_URL = "http://localhost:11434"

SYSTEM_PROMPT = """\
Tu es un assistant expert en actualité spatiale et technologique. \
Tu reçois chaque matin une liste d'articles récents issus de flux RSS \
ainsi qu'une mémoire persistante des événements et contextes importants. \
Ta mission : produire un digest quotidien en français, clair, structuré \
et informatif, destiné à être envoyé par email.

Règles :
- Rédige en français, même pour les articles anglophones.
- Groupe les articles par thème (exploration, science, industrie, défense, etc.).
- Identifie les 3-5 points les plus marquants de la journée.
- Si un article concerne un événement à venir (lancement, annonce), note-le dans les mémoires datées.
- Si un article apporte un fait de contexte durable (nouvelle mission confirmée, résultat scientifique majeur), note-le en mémoire permanente.
- Ne répète pas les faits déjà présents dans la mémoire.
- Sois synthétique : une info = une phrase claire + source.
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


def _call_ollama(prompt: str, model: str, base_url: str) -> str:
    """Calls Ollama /api/generate with JSON format enforced."""
    url = f"{base_url.rstrip('/')}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",        # Ollama native JSON mode
        "options": {
            "temperature": 0.3,  # Low temp for structured output
            "num_predict": 3000,
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()["response"]
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Ollama not reachable at {base_url}. "
            "Is Ollama running? Try: ollama serve"
        )


def _extract_json(raw: str) -> dict:
    """Extracts and parses JSON from model output, tolerating minor noise."""
    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting the first {...} block
    m = re.search(r"\{[\s\S]+\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from model output: {raw[:400]}")


def synthesize(
    articles: list[dict],
    memory_content: str,
    reminders: list[str],
    model: str,
    ollama_url: str = OLLAMA_DEFAULT_URL,
    **_kwargs,  # absorb unused keys (api_key, etc.) from settings
) -> DigestResult:
    if not articles:
        log.warning("No articles to synthesize")
        return DigestResult(
            html_body="<p>Aucun article récent trouvé aujourd'hui.</p>",
            plain_body="Aucun article récent trouvé aujourd'hui.",
        )

    today = date.today().strftime("%A %d %B %Y")
    articles_text = _format_articles(articles)

    reminders_block = ""
    if reminders:
        items = "\n".join(f"- {r}" for r in reminders)
        reminders_block = f"\n\nRAPPELS DU JOUR:\n{items}"

    # Single-shot prompt — smaller models work best with one clear prompt
    prompt = f"""{SYSTEM_PROMPT}

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

    log.info("Calling Ollama model '%s' at %s ...", model, ollama_url)
    raw = _call_ollama(prompt, model=model, base_url=ollama_url)
    log.info("Ollama response received (%d chars)", len(raw))

    try:
        data = _extract_json(raw)
    except ValueError as e:
        log.error("JSON parse failed: %s", e)
        return DigestResult(html_body=f"<pre>{raw}</pre>", plain_body=raw)

    return DigestResult(
        html_body=_render_html(data, today),
        plain_body=_render_plain(data, today),
        new_dated_memories=data.get("nouvelles_memoires_datees", []),
        new_permanent_memories=data.get("nouvelles_memoires_permanentes", []),
    )


def _render_html(data: dict, today: str) -> str:
    parts = [f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Georgia, serif; max-width: 680px; margin: 40px auto; color: #1a1a2e; line-height: 1.6; }}
  h1 {{ color: #0f3460; border-bottom: 2px solid #e94560; padding-bottom: 8px; }}
  h2 {{ color: #0f3460; margin-top: 32px; }}
  .highlights {{ background: #f0f4ff; border-left: 4px solid #e94560; padding: 16px 20px; border-radius: 4px; }}
  .highlights li {{ margin-bottom: 6px; }}
  .article {{ margin-bottom: 14px; }}
  .article a {{ color: #e94560; text-decoration: none; font-weight: bold; }}
  .article .source {{ font-size: 0.85em; color: #666; }}
  footer {{ margin-top: 40px; font-size: 0.8em; color: #999; border-top: 1px solid #eee; padding-top: 12px; }}
</style>
</head>
<body>
<h1>Des nouvelles des étoiles</h1>
<p><em>{today}</em></p>
"""]

    points = data.get("points_marquants", [])
    if points:
        items = "".join(f"<li>{p}</li>" for p in points)
        parts.append(f'<div class="highlights"><strong>Points marquants</strong><ul>{items}</ul></div>')

    for section in data.get("sections", []):
        parts.append(f'<h2>{section["titre"]}</h2>')
        for art in section.get("articles", []):
            parts.append(
                f'<div class="article">'
                f'<a href="{art.get("url", "#")}">{art["titre"]}</a> '
                f'<span class="source">({art["source"]})</span><br>'
                f'{art["resume"]}'
                f'</div>'
            )

    parts.append("<footer>Digest généré localement · Des nouvelles des étoiles</footer>")
    parts.append("</body></html>")
    return "\n".join(parts)


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
