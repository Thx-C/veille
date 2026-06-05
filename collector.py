# collector.py — collecte RSS, arXiv et CISA KEV + filtrage par pertinence

import hashlib
import json
import math
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import arxiv
import feedparser
import requests
import xml.etree.ElementTree as ET
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
            # Extraction du nom de domaine pour un affichage plus propre
            domain = url.split("//")[-1].split("/")[0]
            feed = feedparser.parse(url)
            feed_title = feed.feed.get("title", domain)
            
            print(f"    -> Lecture de : {feed_title}")
        except Exception as e:
            print(f"    [RSS] Erreur sur {url}: {e}")
            continue

        count_feed = 0
        for entry in feed.entries:
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
                "source":  feed_title,
                "theme":   theme,
                "type":    "rss",
            })
            count_feed += 1
            
        if count_feed > 0:
            print(f"       + {count_feed} articles trouvés cette semaine")

    return articles


# ── Collecte arXiv ────────────────────────────────────────────────────────────

def _arxiv_session() -> requests.Session:
    """Session requests avec retry automatique adapté aux limites d'arXiv."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=3,
        status_forcelist=[429, 503], # Intercepte aussi les 429 (Too Many Requests)
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "veille-ia-pipeline/1.0 (contact: ton@email.com)"
    })
    return session

def fetch_arxiv() -> list[dict]:
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    ns = "{http://www.w3.org/2005/Atom}"
    session = _arxiv_session()

    for cat in ARXIV_CATEGORIES:
        print(f"    -> Interrogation de arXiv [{cat}] (max={ARXIV_MAX_RESULTS})...")
        url = (
            "https://export.arxiv.org/api/query"
            f"?search_query=cat:{cat}"
            f"&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={ARXIV_MAX_RESULTS}"
        )
        try:
            # Réduction du timeout à 30s pour éviter les blocages infinis de GitHub Actions
            r = session.get(url, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            
            entries = root.findall(f"{ns}entry")
            total_cat = 0
            filtered_cat = 0
            
            for entry in entries:
                published = entry.findtext(f"{ns}published", "")
                try:
                    pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if pub_dt < cutoff:
                    continue
                    
                title   = (entry.findtext(f"{ns}title") or "").strip()
                summary = (entry.findtext(f"{ns}summary") or "").strip()[:600]
                link    = entry.findtext(f"{ns}id") or ""
                if not title or not link:
                    continue
                    
                total_cat += 1
                sc    = {t: _score_article(title, summary, t) for t in ("cyber", "alignement")}
                theme = max(sc, key=sc.get)
                
                if sc[theme] < SCORE_THRESHOLD:
                    filtered_cat += 1
                    continue
                    
                articles.append({
                    "title": title, "summary": summary,
                    "url": link, "source": f"arXiv:{cat}",
                    "theme": theme, "type": "arxiv",
                })
            
            print(f"       + {total_cat} récents | {filtered_cat} rejetés par le score | {total_cat - filtered_cat} retenus")
            
            # Pause de sécurité recommandée par arXiv entre deux requêtes
            time.sleep(5) 
        except Exception as e:
            print(f"    [arXiv] ⚠️ Impossible de récupérer {cat} : {e}")

    return articles


# ── Collecte CISA KEV ─────────────────────────────────────────────────────────

def fetch_cisa_kev() -> list[dict]:
    """Télécharge le catalogue CISA KEV et extrait les vulnérabilités récentes."""
    url    = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    print("    -> Téléchargement du catalogue CISA KEV...")
    try:
        data = requests.get(url, timeout=15).json()
    except Exception as e:
        print(f"    [CISA] Erreur de récupération: {e}")
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

    print(f"       + {len(new_cves)} nouvelles CVE critiques identifiées cette semaine")
    return new_cves


# ── Pipeline de filtrage ──────────────────────────────────────────────────────

def collect_and_filter() -> dict[str, list[dict]]:
    print("═══ DEBUT DE LA COLLECTE DES SOURCES ═══")
    seen = _load_seen()
    print(f"  [Cache] {len(seen)} articles déjà enregistrés lors des semaines passées.")
    all_articles: list[dict] = []

    print("\n[1/3] Collecte des flux RSS...")
    for theme in ("cyber", "alignement"):
        print(f"  Thématique : {theme.upper()}")
        all_articles += fetch_rss(theme)

    print("\n[2/3] Collecte des papiers de recherche arXiv...")
    all_articles += fetch_arxiv()

    print("\n[3/3] Collecte des vulnérabilités CISA...")
    cves = fetch_cisa_kev()

    print("\n═══ PHASE DE FILTRAGE ET DE-DUPLICATION ═══")
    unique, new_seen = [], set()
    kept_count = 0
    duplicate_count = 0
    low_score_count = 0

    for art in all_articles:
        h = _hash(art["title"] + art["url"])
        if h in seen or h in new_seen:
            duplicate_count += 1
            continue
        new_seen.add(h)
        
        # Attribution du score final
        art["score"] = _score_article(art["title"], art["summary"], art["theme"])
        if art["score"] >= SCORE_THRESHOLD:
            unique.append(art)
            kept_count += 1
        else:
            low_score_count += 1

    print(f"  - Total d'articles analysés : {len(all_articles)}")
    print(f"  - Doublons éliminés : {duplicate_count}")
    print(f"  - Éliminés par le filtre de pertinence (< {SCORE_THRESHOLD}) : {low_score_count}")
    print(f"  - Nouveaux articles valides : {kept_count}")

    # Sauvegarde dans le cache local
    _save_seen(seen | new_seen)

    # Sélection des Top-N par thématique
    result = {"cyber": [], "alignement": [], "cve": cves[:10]}
    for theme in ("cyber", "alignement"):
        themed = [a for a in unique if a["theme"] == theme]
        themed.sort(key=lambda x: x["score"], reverse=True)
        result[theme] = themed[:MAX_ARTICLES_PER_THEME]

    print("\n═══ SELECTION FINALE POUR COMPILATION GEMINI ═══")
    print(f"  → Sélection Cyber : {len(result['cyber'])} articles (Top {MAX_ARTICLES_PER_THEME})")
    print(f"  → Sélection Alignement : {len(result['alignement'])} articles (Top {MAX_ARTICLES_PER_THEME})")
    print(f"  → Sélection CVE critiques : {len(result['cve'])} éléments")
    print("================================================\n")
    
    return result
