# summarizer.py — résumé des articles via Google Gemini API (gemini-2.5-flash, gratuit)

import json
import re
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

ARTICLE_PROMPT = """Voici un article de veille. Produis un résumé structuré en JSON strict.

Article :
Titre : {title}
Source : {source}
Contenu : {summary}
URL : {url}

Réponds au format JSON suivant :
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

def _call_gemini(prompt: str, max_tokens: int = 512) -> str:
    """Effectue l'appel à l'API Gemini avec gestion des quotas (429) et indisponibilités (503)."""
    full_prompt = SYSTEM_PROMPT + "\n\n" + prompt
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,
                    # Forçage du mode JSON natif : supprime le besoin de regex/nettoyage de markdown
                    response_mime_type="application/json",
                ),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            # Gestion des limites de taux (429) et des indisponibilités de service (503)
            if any(k in err.lower() for k in ["429", "503", "quota", "rate", "unavailable"]):
                wait = 60 * (attempt + 1)
                print(f"    [Gemini] Erreur temporaire ({err[:40].strip()}) — attente {wait}s avant retry…")
                time.sleep(wait)
            else:
                print(f"    [Gemini] Erreur critique : {e}")
                raise
    raise RuntimeError("L'API Gemini est restée indisponible après 3 tentatives.")


def _parse_json(text: str) -> dict:
    """Parse le texte JSON reçu directement depuis l'API."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    [Parsing] Échec de la lecture du JSON natif : {e}")
        return {}


# ── Générateurs unitaires ─────────────────────────────────────────────────────

def summarize_article(article: dict) -> dict:
    """Prend un article brut, demande un résumé à Gemini et fusionne le résultat."""
    prompt = ARTICLE_PROMPT.format(
        title=article.get("title", ""),
        source=article.get("source", ""),
        summary=article.get("summary", ""),
        url=article.get("url", ""),
    )
    title_snippet = article.get("title", "")[:60]
    print(f"  Résumé: {title_snippet}…")

    try:
        json_txt = _call_gemini(prompt, max_tokens=GEMINI_MAX_TOKENS)
        ai_data = _parse_json(json_txt)
        if ai_data:
            return article | ai_data
    except Exception as e:
        print(f"    [summarize_article] Exception capturée : {e}")
    
    # Fallback si l'IA ou le parsing échouent
    return article | {
        "titre_court": article.get("title", "")[:40],
        "resume": article.get("summary", "")[:150] + "...",
        "impact": "Moyen",
        "tag_principal": "Veille",
    }


def generate_flash(articles: dict[str, list[dict]]) -> dict:
    """Génère la synthèse globale (Flash hebdomadaire)."""
    print("── Génération du flash consolidé (Gemini) ──")

    def fmt_list(lst):
        lines = []
        for a in lst:
            lines.append(
                f"- {a['title']} | Impact: {a.get('impact','?')} | {a['url']}"
            )
        return "\n".join(lines) if lines else "(aucun)"

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
        # Augmentation des tokens à la valeur de configuration maximale (2048) pour éviter les coupures au milieu du JSON
        text = _call_gemini(prompt, max_tokens=GEMINI_MAX_TOKENS)
        result = _parse_json(text)
        if not result:
            print("    [generate_flash] JSON vide ou invalide — utilisation du fallback.")
            return FLASH_FALLBACK
        return result
    except Exception as e:
        print(f"  [generate_flash] Erreur critique : {e}")
        return FLASH_FALLBACK


# ── Pipeline complet ──────────────────────────────────────────────────────────

def summarize_all(articles: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict]:
    """
    1. Résume chaque article individuellement.
    2. Génère le flash consolidé.
    Retourne (articles_enrichis, flash_dict).
    """
    print("── Résumé des articles (Gemini) ──")
    enriched = {"cyber": [], "alignement": [], "cve": articles.get("cve", [])}

    for theme in ("cyber", "alignement"):
        for art in articles.get(theme, []):
            res = summarize_article(art)
            enriched[theme].append(res)
            # Pause de sécurité entre les articles pour rester sous les 15 req/min du free tier
            time.sleep(5)

    flash = generate_flash(enriched)
    return enriched, flash
