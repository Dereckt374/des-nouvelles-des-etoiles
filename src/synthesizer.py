"""
Turns fetched articles into the news sections of the digest, using the Mistral API.

This module only produces content — rendering lives in renderer.py.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from mistralai.client import Mistral

from models import Item, Section

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Tu es un assistant expert en actualité spatiale, destiné à un opérateur de fusée professionnel. \
Tu reçois chaque matin une liste d'articles récents issus de flux RSS. \
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
- Sois synthétique et précis : une info = une phrase claire + source.
- Pour le champ "url" de chaque article, recopie EXACTEMENT l'URL fournie entre parenthèses dans la liste — ne la modifie pas, ne la reconstruis pas.
- Réponds UNIQUEMENT avec du JSON valide, sans texte avant ni après, sans balises markdown."""

ARTICLE_TEMPLATE = "- [{title}]({url}) ({feed}, {date}) : {summary}"

JSON_SCHEMA = """\
{
  "points_marquants": ["phrase 1", "phrase 2"],
  "sections": [
    {
      "titre": "Titre de section",
      "articles": [
        {"titre": "...", "url": "...", "source": "...", "date": "YYYY-MM-DD", "resume": "..."}
      ]
    }
  ]
}"""


@dataclass
class Synthesis:
    """Result of the LLM pass.

    `raw_error` is set instead of the other fields when the model answered
    but its output could not be parsed — the caller then ships the raw text
    rather than silently dropping the digest.
    """

    highlights: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    raw_error: Optional[str] = None


def _format_articles(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        summary = a["summary"][:250] if a["summary"] else "(pas de résumé)"
        pub = a.get("published", "")
        date_str = pub[:10] if pub else "date inconnue"
        lines.append(
            ARTICLE_TEMPLATE.format(
                title=a["title"],
                url=a.get("url", ""),
                feed=a["feed_name"],
                date=date_str,
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


def _to_sections(data: dict) -> list[Section]:
    """Maps the model's JSON onto Section objects, all under the `news` key."""
    sections = []
    for raw_section in data.get("sections", []):
        items = [
            Item(
                title=a.get("titre", ""),
                url=a.get("url", ""),
                source=a.get("source", ""),
                date=a.get("date", ""),
                summary=a.get("resume", ""),
            )
            for a in raw_section.get("articles", [])
        ]
        if items:
            sections.append(
                Section(key="news", title=raw_section.get("titre", "Actualités"), items=items)
            )
    return sections


def synthesize(
    articles: list[dict],
    model: str,
    api_key: str,
    date_label: str,
    **_kwargs,
) -> Synthesis:
    if not articles:
        log.warning("No articles to synthesize")
        return Synthesis()

    client = Mistral(api_key=api_key)
    articles_text = _format_articles(articles)

    user_message = f"""\
DATE: {date_label}
ARTICLES ({len(articles)}):
{articles_text}

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
        return Synthesis(raw_error=raw)

    return Synthesis(
        highlights=data.get("points_marquants", []),
        sections=_to_sections(data),
    )
