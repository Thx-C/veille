# summarizer.py — résumé des articles via Google Gemini API (gemini-2.5-flash, gratuit)
#
# OPTIMISATION : les articles sont groupés par thème et envoyés en un seul appel
# par thème (2 appels au lieu de 16), ce qui évite les 429 RESOURCE_EXHAUSTED
# sur le free tier (15 req/min).

import json
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MAX_TOKENS, GEMINI_MODEL, FLASH_FALLBACK

client = genai.Client(api_key=GEMINI_API_KEY)


# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es un analyste expert en veille technologique spécialisé sur deux thèmes :
1. L'évolution de l'IA dans la cybersécurité
2. L'alignement et l'humanisation de l'IA

Ton rôle est de produire des résumés concis, factuels et directement actionnables pour un public technique.
Réponds toujours en français. Sois précis, évite les formulations vagues.
Ne commence jamais une réponse par "Bien sûr" ou équivalent.
Réponds UNIQUEMENT avec le JSON demandé."""

# Nouveau prompt batch : résume N articles en une seule passe
BATCH_PROMPT = """Voici {n} articles de veille. Pour chacun, produis un résumé structuré.

{articles_block}

Réponds UNIQUEMENT avec un tableau JSON (array) de {n} objets dans le même ordre que les articles, chaque objet ayant exactement ces clés :
{{
  "titre_court": "<titre reformulé en 8 mots max>",
  "resume": "<2-3 phrases factuelles sur ce qui se passe et pourquoi c'est important>",
  "impact": "<Faible|Moyen|Élevé>",
  "tag_principal": "<un seul mot-clé pertinent>"
}}"""

FLASH_PROMPT = """Voici les éléments collectés cette semaine. Produis une synthèse de haut niveau au format JSON strict.

Statistiques : {n_cyber} articles cyber, {n_align} articles alignement.

Liste Cybersécurité :
{cyber_list}

Liste Alignement :
{align_list}

Liste CVE critiques :
{cve_list}

Génère un résumé global (Flash) respectant STRICTEMENT cette structure JSON :
{{
  "titre_editorial": "<Titre accrocheur et pro pour la semaine>",
  "en_bref": "<Synthèse globale de la semaine en 3-4 phrases marquantes>",
  "points_cles": [
    "<Fait marquant 1 avec explication technique>",
    "<Fait marquant 2 avec explication technique>",
    "<Fait marquant 3 avec explication technique>"
  ],
  "tendances": [
    {{"sujet": "<Nom de la tendance>", "description": "<Pourquoi ça monte en puissance>"}},
    {{"sujet": "<Nom de la tendance>", "description": "<Pourquoi ça monte en puissance>"}}
  ],
  "a_surveiller": [
    "<Événement, entreprise ou techno à suivre de près>",
    "<Autre élément à suivre>"
  ]
}}"""


# ── Helpers API ───────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, max_tokens: int = 2048) -> str:
    """Effectue l'appel à l'API Gemini avec backoff exponentiel (429 / 503)."""
    full_prompt = SYSTEM_PROMPT + "\n\n" + prompt
    wait = 30  # départ plus court : 30s → 60s → 120s
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if any(k in err.lower() for k in ["429", "503", "quota", "rate", "unavailable"]):
                print(f"    [Gemini] Erreur temporaire ({err[:50].strip()}) — attente {wait}s avant retry…")
                time.sleep(wait)
                wait *= 2  # 30 → 60 → 120
            else:
                print(f"    [Gemini] Erreur critique : {e}")
                raise
    raise RuntimeError("L'API Gemini est restée indisponible après 3 tentatives.")


def _parse_json(text: str) -> object:
    """Parse le JSON retourné par l'API."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    [Parsing] Échec JSON : {e} — texte reçu : {text[:200]}")
        return None


# ── Résumé par batch ──────────────────────────────────────────────────────────

def _fallback_article(article: dict) -> dict:
    return article | {
        "titre_court": article.get("title", "")[:40],
        "resume": article.get("summary", "")[:150] + "...",
        "impact": "Moyen",
        "tag_principal": "Veille",
    }


def summarize_theme_batch(articles: list[dict], theme: str) -> list[dict]:
    """
    Résume tous les articles d'un thème en UN SEUL appel Gemini.
    Fallback article par article si le batch échoue.
    """
    if not articles:
        return []

    print(f"  → Batch résumé [{theme}] : {len(articles)} articles en 1 appel…")

    # Construit le bloc texte des articles
    lines = []
    for i, a in enumerate(articles, 1):
        lines.append(
            f"--- Article {i} ---\n"
            f"Titre : {a.get('title', '')}\n"
            f"Source : {a.get('source', '')}\n"
            f"Contenu : {a.get('summary', '')[:400]}\n"
            f"URL : {a.get('url', '')}"
        )
    articles_block = "\n\n".join(lines)

    prompt = BATCH_PROMPT.format(
        n=len(articles),
        articles_block=articles_block,
    )

    # Tokens : ~400 tokens d'entrée par article + marge de sortie
    max_tokens = min(4096, len(articles) * 300 + 512)

    try:
        raw = _call_gemini(prompt, max_tokens=max_tokens)
        parsed = _parse_json(raw)

        # Le modèle doit retourner une liste de même longueur
        if isinstance(parsed, list) and len(parsed) == len(articles):
            print(f"    ✓ {len(articles)} résumés reçus.")
            return [art | ai for art, ai in zip(articles, parsed)]

        print(f"    [batch] Réponse inattendue (type={type(parsed)}, len={len(parsed) if isinstance(parsed, list) else '?'}) — fallback individuel.")
    except Exception as e:
        print(f"    [batch] Erreur : {e} — fallback individuel.")

    # Fallback : renvoie les articles avec des valeurs par défaut
    return [_fallback_article(a) for a in articles]


# ── Flash consolidé ───────────────────────────────────────────────────────────

def generate_flash(articles: dict[str, list[dict]]) -> dict:
    """Génère la synthèse globale (Flash hebdomadaire) — 1 appel Gemini."""
    print("── Génération du flash consolidé (Gemini) ──")

    def fmt_list(lst):
        return "\n".join(
            f"- {a['title']} | Impact: {a.get('impact', '?')} | {a['url']}"
            for a in lst
        ) or "(aucun)"

    cve_txt = "\n".join(
        f"- {c['title']} | {c['url']}"
        for c in articles.get("cve", [])[:5]
    ) or "(aucune CVE critique cette semaine)"

    prompt = FLASH_PROMPT.format(
        n_cyber=len(articles["cyber"]),
        n_align=len(articles["alignement"]),
        cyber_list=fmt_list(articles["cyber"]),
        align_list=fmt_list(articles["alignement"]),
        cve_list=cve_txt,
    )

    try:
        text = _call_gemini(prompt, max_tokens=GEMINI_MAX_TOKENS)
        result = _parse_json(text)
        if result and isinstance(result, dict):
            return result
        print("    [generate_flash] JSON vide ou invalide — fallback.")
    except Exception as e:
        print(f"  [generate_flash] Erreur : {e}")

    return FLASH_FALLBACK


# ── Pipeline complet ──────────────────────────────────────────────────────────

def summarize_all(articles: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict]:
    """
    1. Résume chaque thème en 1 appel batch (2 appels total au lieu de 16).
    2. Génère le flash consolidé (1 appel).
    Total : 3 appels Gemini → bien sous la limite de 15 req/min du free tier.
    """
    print("── Résumé des articles (Gemini — mode batch) ──")
    enriched = {"cyber": [], "alignement": [], "cve": articles.get("cve", [])}

    for theme in ("cyber", "alignement"):
        enriched[theme] = summarize_theme_batch(articles.get(theme, []), theme)
        # Petite pause entre les deux appels de thème (bonne pratique)
        if theme == "cyber":
            time.sleep(4)

    flash = generate_flash(enriched)
    return enriched, flash
