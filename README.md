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

Un article n'est marqué « vu » en base **qu'une fois le mail réellement parti**.
Conséquences :

- `--dry-run` est rejouable à volonté sans priver le digest réel de son contenu ;
- une panne (API LLM, SMTP) ne consomme rien : les articles repassent au run suivant ;
- si le modèle renvoie du JSON invalide, le mail brut est envoyé mais les articles
  restent en attente, pour être correctement synthétisés le lendemain.

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
  competitors.yaml        # Acteurs suivis + missions routinières
  settings.yaml           # Secrets (gitignored)
  settings.yaml.example   # Template de configuration

src/
  models.py               # Item / Section / Digest + ordre des sections
  fetcher.py              # Lecture RSS + déduplication SQLite
  launches.py             # Launch Library 2 → section Lancements
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

| Source | Section | Traitement |
|---|---|---|
| Launch Library 2 | Lancements | **aucun LLM** — données factuelles de l'API |
| `feeds.yaml` → `feeds` | Suivi d'actualité | synthèse et regroupement par le LLM |
| `feeds.yaml` → `custom_feeds` | Blogs & personnalités suivis | **aucun LLM** — billets affichés tels quels |
| `feeds.yaml` → `forums` | Forums | **aucun LLM** — fils de discussion listés tels quels |
| `feeds.yaml` → `social` | Comptes suivis | **aucun LLM** — messages X des concurrents |

### Lancements

Les tirs proviennent de l'API publique [Launch Library 2](https://thespacedevs.com)
(aucune clé requise, deux appels par exécution : `/launches/` et `/events/`).
La fenêtre couvre les résultats récents et les tirs à venir — réglable via
`digest.launches` dans `settings.yaml`.

Les heures sont converties en heure de Paris et **jamais inventées** : quand
l'API indique une précision à l'heure ou à la journée, c'est annoncé comme tel
(« vers 16h », « heure non figée ») plutôt qu'affiché comme un T-0 ferme.

Les opérateurs listés dans `config/competitors.yaml` obtiennent une fiche
détaillée et un liseré de mise en avant ; les autres, une ligne compacte. Les
vols de constellation listés sous `routine_missions` sont regroupés en une ligne.
La fiche détaillée porte quatre lignes de faits, chacune omise si l'API ne
renseigne rien :

| Ligne | Contenu |
|---|---|
| Performance | réutilisabilité, capacité LEO et GTO, poussée, dimensions, masse au décollage, coût au tir |
| Fiabilité | nombre de tirs, taux de succès, échecs, succès consécutifs, premier vol, rotation record |
| Météo | probabilité de conditions favorables et contraintes violées sur le pas de tir |
| Dernier point | note la plus récente des contributeurs LL2 — souvent le meilleur signal sur un T-0 qui glisse |

S'y ajoutent les liens Wikipédia du lanceur et de l'opérateur.

**Fenêtre creuse.** Quand l'API ne renvoie aucun tir, la section ne disparaît
pas : elle affiche une ligne unique pointant le prochain tir au calendrier, avec
le délai en clair au-delà de demain (« dans 3 jours »). Cela coûte un troisième
appel API, uniquement ces jours-là. Un repli déclenché par un simple manque de
tir marquant serait trompeur, donc la ligne s'effface dès que la fenêtre contient
quoi que ce soit — y compris de simples vols de constellation groupés.

**Logos.** Chaque entrée porte la vignette de l'opérateur, en lien distant. Le
champ utilisé est `social_logo`, et non `logo` : il est carré chez tous les
opérateurs vérifiés et embarque son propre fond opaque, ce qui garde lisible un
logo blanc sur la carte blanche du mail. Sa vignette 256×256 est une réduction
propre, alors que `logo.thumbnail_url` est un **recadrage centré** qui réduit une
signature large à deux lettres illisibles. Les clients qui bloquent les images
distantes (Outlook, Thunderbird) affichent l'abréviation de l'opérateur en
texte alternatif.

### Événements

Le même intervalle est interrogé sur `/events/` : sorties extravéhiculaires,
amarrages, conférences de presse, événements célestes. Ils apparaissent en
sous-bloc de la section Lancements.

Ces événements sont rares — quelques uns par semaine. Sur la fenêtre par défaut
de ±1 jour, le sous-bloc est donc **vide la plupart du temps** ; élargir
`days_ahead` donne davantage de matière. Leurs descriptions viennent de l'API en
anglais et ne sont pas traduites.

### Flux customs

`custom_feeds` regroupe les voix suivies personnellement (blogs d'experts,
chercheurs). Leur formulation d'origine est ce qui a de la valeur : les billets
sont affichés verbatim, dans leur propre section, sans résumé ni reformulation,
et sans remonter dans les autres sections.

Leur fenêtre de collecte est réglée séparément (`digest.custom_feeds` dans
`settings.yaml`), car ces auteurs publient moins souvent que les sites d'actualité.

### Forums

`forums` suit des **rubriques choisies** du forum de la conquête spatiale, et
non le flux global du site. Ce forum tourne sous Forumactif, qui expose un flux
par rubrique via `/feed/?f=<id>` — y compris des sous-rubriques par acteur
(SpaceX, Rocket Lab, Blue Origin, ULA). Pour en ajouter une, relever l'id dans
l'URL de la rubrique (`/f49-spacex` → 49).

**Aucun scraping** : ces flux sont publiés par le forum lui-même, et déclarés
dans le `<head>` de ses pages. Le filtrage par sujet n'est en revanche pas
possible côté serveur — `/feed/?t=<id>` ignore le paramètre et renvoie
l'intégralité du forum.

**Détection de l'activité.** Une entrée représente un *fil*, pas un message :
le flux liste les discussions récemment animées, avec la date et le texte de
leur dernier message. Le guid servi est l'adresse du fil, donc il ne change
jamais, quel que soit le nombre de réponses. Une déduplication classique
signalerait donc chaque discussion une seule fois, puis la tairait à jamais —
l'inverse de ce qu'on attend d'un fil qu'on suit précisément pour le voir
avancer.

D'où `kind: forum`, qui intègre la date de dernière activité à la clé de
déduplication. Le fil ressort à chaque nouvelle salve de messages, et reste
silencieux tant que rien ne bouge. Comme la description du flux porte le
dernier message, chaque réapparition apporte du contenu neuf.

### Comptes suivis

`social` relaie les comptes X des concurrents. **X n'a plus d'API de lecture
gratuite depuis 2023**, ces flux passent donc par Nitter, un relais tiers. Il
faut en connaître les limites : c'est une dépendance fragile, dont les instances
publiques ferment régulièrement, et son fonctionnement se situe hors des
conditions d'utilisation de X. Si le relais tombe, la section disparaît sans
casser le digest.

Les flux marqués `kind: nitter` reçoivent un post-traitement (`src/fetcher.py`) :

- **retweets et réponses écartés** — ce qui compte pour la veille concurrentielle,
  c'est ce que l'entreprise dit elle-même ;
- **liens réécrits vers `x.com`**, pour rester valides le jour où le relais
  change ; l'identifiant du tweet, lui, est stable.

Le mécanisme `kind` est générique : une nouvelle source aux mêmes besoins
s'ajoute en déclarant une transformation dans `_TRANSFORMS`.

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