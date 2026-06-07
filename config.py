# config.py — configuration centralisée du pipeline de veille
# Remplissez vos clés API ici ou passez-les en variables d'environnement

import os

# ── Clés API ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "VOTRE_CLE_GEMINI")
NOTION_TOKEN   = os.getenv("NOTION_TOKEN",   "secret_VOTRE_TOKEN")

# ID de la page Notion parente dans laquelle créer chaque flash hebdo
# Récupérable dans l'URL : notion.so/{workspace}/{PAGE_ID}?v=...
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

# ── Sources RSS ───────────────────────────────────────────────────────────────
RSS_FEEDS = {
    "cyber": [
        "https://www.cert.ssi.gouv.fr/feed/",
        "https://krebsonsecurity.com/feed/",
        "https://veillecyberland.wordpress.com/feed/",
        "https://dcod.ch/feed/",
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.darkreading.com/rss.xml",
    ],
    "alignement": [
        "https://actuia.com/feed/",
        "https://intelligence-artificielle-generale.com/feed/",
        "https://www.lesswrong.com/feed.xml",
        "https://aisnakeoil.substack.com/feed",
    ],
}

# Flux arXiv — catégories cs.CR (crypto/sécurité) et cs.AI (IA)
ARXIV_CATEGORIES = ["cs.CR", "cs.AI", "cs.LG"]
ARXIV_MAX_RESULTS = 30  # articles récupérés par catégorie

# ── Mots-clés de filtrage ─────────────────────────────────────────────────────
KEYWORDS = {
    "cyber": [
        "AI", "LLM", "machine learning", "cybersecurity", "vulnerability", "CVE",
        "exploit", "threat", "malware", "phishing", "deepfake", "agent",
        "prompt injection", "IA", "cybersécurité", "menace", "attaque",
    ],
    "alignement": [
        "alignment", "safety", "RLHF", "Constitutional AI", "ethics", "bias",
        "regulation", "governance", "AGI", "humanization", "interpretability",
        "alignement", "éthique", "biais", "gouvernance", "règlement", "IA Act",
    ],
}

# Seuil de score TF-IDF minimum pour retenir un article (0.0 à 1.0)
SCORE_THRESHOLD = 0.15

# Nombre maximum d'articles retenus par thème pour le résumé IA
MAX_ARTICLES_PER_THEME = 8

# ── Modèle Gemini (free tier) ─────────────────────────────────────────────────
# Limites free tier : 15 req/min, 1 500 req/jour, 1M tokens/min
# À 5s de pause entre requêtes, ~12 articles = ~1 min → on reste sous les 15 req/min
# Modèles disponibles gratuitement : gemini-2.5-flash (recommandé)
GEMINI_MODEL      = "gemini-2.5-flash"
GEMINI_MAX_TOKENS = 2048

# Flash de secours utilisé si Gemini ne parvient pas à générer le JSON consolidé.
# Garantit que la publication Notion a toujours lieu même en cas d'erreur API.
FLASH_FALLBACK: dict = {
    "titre_editorial": "Veille hebdomadaire IA & Cybersécurité",
    "en_bref": "Le flash consolidé n'a pas pu être généré cette semaine (erreur Gemini). "
               "Les articles individuels restent disponibles dans les sections ci-dessous.",
    "points_cles": [
        "Voir les articles résumés dans les sections Cybersécurité et Alignement.",
        "Les CVE critiques CISA sont listées dans la section dédiée.",
    ],
    "tendances": [],
    "a_surveiller": [],
}

# ── Scheduling ────────────────────────────────────────────────────────────────
# Utilisé par run_pipeline.py si lancé en mode démon (schedule)
RUN_DAY  = "friday"   # jour de la semaine
RUN_TIME = "07:00"    # heure locale HH:MM
