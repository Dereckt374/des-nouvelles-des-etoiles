# Des nouvelles des étoiles

Digest quotidien d'actualité spatiale, généré par IA et envoyé par email chaque matin.

## Stack

- **Python 3.11+**
- **feedparser** — lecture des flux RSS/Atom
- **mistralai** — synthèse via l'API Mistral
- **smtplib** — envoi email (compatible Mailjet, Resend, etc.)
- **SQLite** — déduplication des articles vus

## Installation

```bash
git clone https://github.com/vous/des-nouvelles-des-etoiles.git
cd des-nouvelles-des-etoiles

python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

cp config/settings.yaml.example config/settings.yaml
# Éditer config/settings.yaml avec vos clés API et SMTP
```

## Utilisation

```bash
# Test sans envoi email (génère data/last_digest.html)
python src/main.py --dry-run

# Envoi réel
python src/main.py
```

En `--dry-run`, les articles ne sont **pas** marqués comme vus en base : on peut
relancer autant de fois qu'on veut sans priver le prochain digest réel de son contenu.

## Planification (VPS Linux)

```bash
chmod +x cron/digest.sh

# Ajouter dans crontab (crontab -e) :
0 10 * * * /chemin/vers/des_nouvelles_des_etoiles/cron/digest.sh >> /var/log/digest.log 2>&1
```

## Structure

```
config/
  feeds.yaml              # Flux RSS, groupés par usage
  settings.yaml           # Secrets (gitignored)
  settings.yaml.example   # Template de configuration

src/
  models.py               # Item / Section / Digest + ordre des sections
  fetcher.py              # Lecture RSS + déduplication SQLite
  synthesizer.py          # Appel Mistral API → sections d'actualité
  renderer.py             # Rendu HTML (email) et texte
  memory.py               # Mémoire persistante (data/memory.md) — inutilisé
  mailer.py               # Envoi SMTP
  main.py                 # Orchestrateur + --dry-run

data/
  articles.db             # SQLite — articles déjà vus (gitignored)
  memory.md               # Mémoire événementielle (éditable manuellement)

cron/
  digest.sh               # Script pour le cron VPS
```

## Sections du digest

Le mail est composé de sections rendues dans un **ordre fixe**, défini par
`SECTION_RANK` dans `src/models.py`. Chaque source alimente sa propre section :
peu importe qu'elle vienne du LLM ou non, elle produit des objets `Section`.

| Groupe `feeds.yaml` | Section | Traitement |
|---|---|---|
| `feeds` | Suivi d'actualité | synthèse et regroupement par le LLM |
| `custom_feeds` | Blogs & personnalités suivis | **aucun LLM** — billets affichés tels quels |

### Flux customs

`custom_feeds` regroupe les voix suivies personnellement (blogs d'experts,
chercheurs). Leur formulation d'origine est ce qui a de la valeur : les billets
sont affichés verbatim, dans leur propre section, sans résumé ni reformulation,
et sans remonter dans les autres sections.

Leur fenêtre de collecte est réglée séparément (`digest.custom_feeds` dans
`settings.yaml`), car ces auteurs publient moins souvent que les sites d'actualité.

## Mémoire persistante

Le fichier `data/memory.md` est mis à jour automatiquement après chaque digest.
Il contient :
- **Événements datés** : rappels automatiques (lancements, annonces)
- **Contexte permanent** : missions en cours, faits de fond durables

Vous pouvez l'éditer manuellement pour ajouter ou supprimer des entrées.

## Setup Ollama

```bash
# 1. Installer Ollama (Linux/macOS)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Télécharger un modèle — choix selon ta RAM :
ollama pull mistral          # 4 GB RAM  — meilleur français ★★★
ollama pull qwen2.5:7b       # 5 GB RAM  — excellent multilingue ★★★
ollama pull llama3.2:3b      # 2 GB RAM  — très léger, français correct ★★

# 3. Vérifier qu'Ollama tourne
ollama serve   # (en arrière-plan sur le VPS)
```

**Recommandation :** `mistral` est le meilleur choix pour du français technique. `llama3.2:3b` si ton VPS a peu de RAM.

```bash
# Test complet
pip install -r requirements.txt
cd src && python main.py --dry-run
```

Le reste du pipeline (fetcher, memory, mailer) est identique — 100% gratuit, 100% local.