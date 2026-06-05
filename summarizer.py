# summarizer.py — résumé des articles via Google Gemini API (gemini-1.5-flash, gratuit)

import json
import re
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MAX_TOKENS, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)


# ── Prompts ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es un analyste expert en veille technologique spécialisé sur deux thèmes :
1. L'évolution de l'IA dans la cybersécurité
2. L'alignement et l'humanisation de l'IA

Ton rôle est de produire des résumés concis, factuels et directement actionnables pour un public technique.
Réponds toujours en français. Sois précis, évite les formulations vagues.
Ne commence jamais une réponse par "Bien sûr" ou équivalent.
Réponds UNIQUEMENT avec le JSON demandé — aucun texte avant ou après, aucun bloc markdown, aucun backtick."""

ARTICLE_PROMPT = """Voici un article de veille. Produis un résumé structuré en JSON strict.

Article :
Titre : {title}
Source : {source}
Contenu : {summary}
URL : {url}

Réponds UNIQUEMENT avec ce JSON (pas de backticks, pas de markdown) :
{{
  "titre_court": "<titre reformulé en 8 mots max>",
  "resume": "<2-3 phrases factuelles sur ce qui se passe et pourquoi c'est important>",
  "impact": "<Faible|Moyen|Élevé>",
  "tag_principal": "<un seul tag parmi : #CVE #LLM-exploit #agent-security #détection #red-team #RLHF #gouvernance #biais #regulation-EU #AGI-safety #IA-offensive>",
  "action": "<Surveiller|Approfondir|Traiter en urgence>"
}}"""

FLASH_PROMPT = """Tu as analysé {n_cyber} articles sur l'IA & cybersécurité et {n_align} articles sur l'alignement IA cette semaine.

Articles cyber (résumés) :
{cyber_list}

Articles alignement (résumés) :
{align_list}

CVE critiques CISA cette semaine :
{cve_list}

Produis le flash hebdo complet en JSON strict (pas de backticks, pas de markdown) :
{{
  "chiffre_semaine": {{
    "valeur": "<chiffre ou stat clé extraite des articles>",
    "contexte": "<une phrase expliquant ce chiffre>"
  }},
  "signal_fort_cyber": {{
    "titre": "<titre court>",
    "resume": "<3 phrases : ce qui s'est passé, pourquoi c'est important, ce qu'il faut faire>",
    "source": "<nom de la source>",
    "url": "<url>",
    "impact": "<Faible|Moyen|Élevé>"
  }},
  "signal_fort_alignement": {{
    "titre": "<titre court>",
    "resume": "<3 phrases>",
    "source": "<nom de la source>",
    "url": "<url>",
    "impact": "<Faible|Moyen|Élevé>"
  }},
  "signal_croise": "<2 phrases sur un sujet touchant à la fois cyber et alignement cette semaine>",
  "a_surveiller": ["<point 1>", "<point 2>", "<point 3>"],
  "tableau_cyber": [
    {{"titre": "", "source": "", "tag": "", "pertinence": 1}},
    {{"titre": "", "source": "", "tag": "", "pertinence": 2}},
    {{"titre": "", "source": "", "tag": "", "pertinence": 3}}
  ],
  "tableau_alignement": [
    {{"titre": "", "source": "", "tag": "", "pertinence": 1}},
    {{"titre": "", "source": "", "tag": "", "pertinence": 2}},
    {{"titre": "", "source": "", "tag": "", "pertinence": 3}}
  ]
}}"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _call_gemini(prompt: str, max_tokens: int = 512) -> str:
    full_prompt = SYSTEM_PROMPT + "\n\n" + prompt
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.2,
                    # Force Gemini à répondre en JSON pur (pas de texte, pas de backticks ```json)
                    response_mime_type="application/json", 
                ),
            )
            return response.text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "rate" in err.lower():
                wait = 60 * (attempt + 1)
                print(f"    [Gemini] Rate limit — attente {wait}s…")
                time.sleep(wait)
            else:
                print(f"    [Gemini] Erreur: {e}")
                raise
    raise RuntimeError("Gemini rate limit persistant après 3 tentatives")


def _parse_json(text: str) -> dict:
    """Nettoie et parse le JSON retourné par Gemini."""
    # Supprime les éventuels blocs ```json … ``` malgré les instructions
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$",          "", text, flags=re.MULTILINE)
    return json.loads(text.strip())


# ── Résumé d'un article individuel ───────────────────────────────────────────

def summarize_article(article: dict) -> dict:
    """Résume un article via Gemini et retourne un dict enrichi."""
    prompt = ARTICLE_PROMPT.format(
        title=article["title"],
        source=article["source"],
        summary=article["summary"],
        url=article["url"],
    )
    # Pause préventive : free tier = 15 req/min → 1 req toutes les 4s minimum
    time.sleep(4)
    try:
        text   = _call_gemini(prompt, max_tokens=512)
        parsed = _parse_json(text)
        return {**article, **parsed}
    except Exception as e:
        print(f"    [summarize_article] Fallback sur: {article['title'][:50]}… ({e})")
        return {
            **article,
            "titre_court":  article["title"][:60],
            "resume":       article["summary"][:200],
            "impact":       "Moyen",
            "tag_principal":"#veille",
            "action":       "Surveiller",
        }


# ── Génération du flash consolidé ────────────────────────────────────────────

def generate_flash(articles: dict[str, list[dict]]) -> dict:
    """
    Synthèse globale de la semaine en un seul appel Gemini.
    Produit le JSON complet du flash hebdo.
    """
    def fmt_list(lst: list[dict]) -> str:
        lines = []
        for i, a in enumerate(lst, 1):
            lines.append(
                f"{i}. [{a.get('titre_court', a['title'])}] "
                f"({a['source']}) — {a.get('resume', a['summary'][:150])} "
                f"| Impact: {a.get('impact','?')} | {a['url']}"
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
        # Utilisez GEMINI_MAX_TOKENS (2048) au lieu de laisser la valeur par défaut à 512
        text = _call_gemini(prompt, max_tokens=GEMINI_MAX_TOKENS)
        return _parse_json(text)
    except Exception as e:
        print(f"  [generate_flash] Erreur: {e}")
        return {}


# ── Pipeline complet ──────────────────────────────────────────────────────────

def summarize_all(articles: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict]:
    """
    1. Résume chaque article individuellement (avec pause anti-rate-limit).
    2. Génère le flash consolidé.
    Retourne (articles_enrichis, flash_dict).
    """
    print("── Résumé des articles (Gemini) ──")
    enriched = {"cyber": [], "alignement": [], "cve": articles.get("cve", [])}

    for theme in ("cyber", "alignement"):
        for art in articles[theme]:
            print(f"  Résumé: {art['title'][:55]}…")
            enriched[theme].append(summarize_article(art))

    print("── Génération du flash consolidé (Gemini) ──")
    flash = generate_flash(enriched)

    return enriched, flash
