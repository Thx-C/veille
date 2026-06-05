# collector.py — collecte RSS, arXiv et CISA KEV + filtrage par pertinence

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import arxiv
import feedparser
import requests

from config import (
    ARXIV_CATEGORIES,
    ARXIV_MAX_RESULTS,
    KEYWORDS,
    MAX_ARTICLES_PER_THEME,
    RSS_FEEDS,
    SCORE_THRESHOLD,
)

# Fichier de cache local pour éviter les doublons d'une semaine à l'autre
SEEN_HASHES_FILE = Path("seen_hashes.json")


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _hash(text: str) -> str:
    """SHA1 court pour identifier un article de façon unique."""
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _load_seen() -> set:
    if SEEN_HASHES_FILE.exists():
        return set(json.loads(SEEN_HASHES_FILE.read_text()))
    return set()


def _save_seen(seen: set) -> None:
    SEEN_HASHES_FILE.write_text(json.dumps(list(seen)))


def _clean(text: str) -> str:
    """Supprime les balises HTML et normalise les espaces."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


# ── Score TF-IDF simplifié ────────────────────────────────────────────────────

def _score_article(title: str, summary: str, theme: str) -> float:
    """
    Score de pertinence basé sur la densité de mots-clés thématiques.
    Retourne un float entre 0.0 et 1.0.
    """
    corpus = (title + " " + summary).lower()
    words  = re.findall(r"\w+", corpus)
    if not words:
        return 0.0

    kw_list = [k.lower() for k in KEYWORDS[theme]]
    hits    = sum(1 for w in words if any(kw in w for kw in kw_list))
    tf      = hits / len(words)

    # Bonus si un mot-clé apparaît dans le titre
    title_lower = title.lower()
    title_bonus = 0.1 * sum(1 for kw in kw_list if kw in title_lower)

    return min(1.0, tf * 10 + title_bonus)


# ── Collecte RSS ──────────────────────────────────────────────────────────────

def fetch_rss(theme: str) -> list[dict]:
    """Parcourt tous les flux RSS du thème et retourne les articles de la semaine."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    articles = []

    for url in RSS_FEEDS[theme]:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [RSS] Erreur sur {url}: {e}")
            continue

        for entry in feed.entries:
            # Date de publication
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub:
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue

            title   = _clean(entry.get("title", ""))
            summary = _clean(entry.get("summary", "") or entry.get("content", [{}])[0].get("value", ""))
            link    = entry.get("link", "")

            if not title or not link:
                continue

            articles.append({
                "title":   title,
                "summary": summary[:800],
                "url":     link,
                "source":  feed.feed.get("title", url),
                "theme":   theme,
                "type":    "rss",
            })

    return articles


# ── Collecte arXiv ────────────────────────────────────────────────────────────

def fetch_arxiv() -> list[dict]:
    """Récupère les prépublications récentes des catégories configurées."""
    articles = []
    cutoff   = datetime.now(timezone.utc) - timedelta(days=7)

    for cat in ARXIV_CATEGORIES:
        try:
            search = arxiv.Search(
                query=f"cat:{cat}",
                max_results=ARXIV_MAX_RESULTS,
                sort_by=arxiv.SortCriterion.SubmittedDate,
            )
            for result in search.results():
                if result.published.replace(tzinfo=timezone.utc) < cutoff:
                    continue
                # Détermine le thème dominant par score
                title   = result.title
                summary = result.summary[:600]
                sc      = {t: _score_article(title, summary, t) for t in ("cyber", "alignement")}
                theme   = max(sc, key=sc.get)
                if sc[theme] < SCORE_THRESHOLD:
                    continue

                articles.append({
                    "title":   title,
                    "summary": summary,
                    "url":     result.entry_id,
                    "source":  f"arXiv:{cat}",
                    "theme":   theme,
                    "type":    "arxiv",
                })
        except Exception as e:
            print(f"  [arXiv] Erreur sur {cat}: {e}")

    return articles


# ── Collecte CISA KEV ─────────────────────────────────────────────────────────

def fetch_cisa_kev() -> list[dict]:
    """
    Télécharge le catalogue CISA Known Exploited Vulnerabilities et retourne
    les CVE ajoutées cette semaine.
    """
    url    = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    try:
        data = requests.get(url, timeout=15).json()
    except Exception as e:
        print(f"  [CISA] Erreur: {e}")
        return []

    new_cves = []
    for vuln in data.get("vulnerabilities", []):
        date_added = vuln.get("dateAdded", "")
        try:
            added_dt = datetime.fromisoformat(date_added).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if added_dt < cutoff:
            continue

        new_cves.append({
            "title":   f"{vuln['cveID']} — {vuln['vendorProject']} {vuln['product']}",
            "summary": vuln.get("shortDescription", "")[:400],
            "url":     f"https://nvd.nist.gov/vuln/detail/{vuln['cveID']}",
            "source":  "CISA KEV",
            "theme":   "cyber",
            "type":    "cve",
            "cvss":    vuln.get("cvssScore", "N/A"),
        })

    return new_cves


# ── Pipeline de filtrage ──────────────────────────────────────────────────────

def collect_and_filter() -> dict[str, list[dict]]:
    """
    Point d'entrée principal.
    Retourne un dict {"cyber": [...], "alignement": [...], "cve": [...]}
    avec au maximum MAX_ARTICLES_PER_THEME articles par thème, dédupliqués.
    """
    print("── Collecte des sources ──")
    seen = _load_seen()
    all_articles: list[dict] = []

    for theme in ("cyber", "alignement"):
        print(f"  RSS [{theme}]...")
        all_articles += fetch_rss(theme)

    print("  arXiv...")
    all_articles += fetch_arxiv()

    print("  CISA KEV...")
    cves = fetch_cisa_kev()

    # Déduplication par hash titre+url
    unique, new_seen = [], set()
    for art in all_articles:
        h = _hash(art["title"] + art["url"])
        if h in seen or h in new_seen:
            continue
        new_seen.add(h)
        art["score"] = _score_article(art["title"], art["summary"], art["theme"])
        if art["score"] >= SCORE_THRESHOLD:
            unique.append(art)

    # Mise à jour du cache
    _save_seen(seen | new_seen)

    # Tri et sélection top-N par thème
    result = {"cyber": [], "alignement": [], "cve": cves[:10]}
    for theme in ("cyber", "alignement"):
        themed = [a for a in unique if a["theme"] == theme]
        themed.sort(key=lambda x: x["score"], reverse=True)
        result[theme] = themed[:MAX_ARTICLES_PER_THEME]

    print(f"  → {len(result['cyber'])} cyber | {len(result['alignement'])} alignement | {len(result['cve'])} CVE")
    return result
