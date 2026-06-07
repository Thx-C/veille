# run_pipeline.py — point d'entrée du pipeline de veille hebdomadaire
#
# Usage :
#   python run_pipeline.py            → exécution immédiate
#   python run_pipeline.py --daemon   → boucle planifiée (chaque vendredi à 07:00)
#
# Cron (exécution directe) :
#   0 7 * * 5 cd /chemin/vers/veille_ia && python run_pipeline.py >> logs/veille.log 2>&1
#
# GitHub Actions : voir .github/workflows/veille.yml généré plus bas

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import schedule

from collector import collect_and_filter
from config import RUN_DAY, RUN_TIME
from notion_publisher import publish_to_notion
from summarizer import summarize_all

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler("logs/veille.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline() -> None:
    start = datetime.now()
    log.info("═══════════════════════════════════════")
    log.info(f"  Pipeline veille IA — {start.strftime('%A %d %B %Y %H:%M')}")
    log.info("═══════════════════════════════════════")

    try:
        # 1. Collecte + filtrage
        articles = collect_and_filter()
        total = len(articles["cyber"]) + len(articles["alignement"]) + len(articles["cve"])
        if total == 0:
            log.warning("Aucun article collecté cette semaine. Pipeline interrompu.")
            return

        # 2. Résumé IA
        enriched, flash = summarize_all(articles)

        if not flash:
            log.warning("Le flash consolidé n'a pas pu être généré (erreur Gemini) — publication sans flash.")

        # 3. Publication Notion
        notion_url = publish_to_notion(flash, enriched)

        elapsed = (datetime.now() - start).total_seconds()
        log.info(f"✓ Pipeline terminé en {elapsed:.1f}s — {notion_url}")

    except Exception:
        log.exception("Erreur inattendue dans le pipeline")
        raise


# ── Mode démon (schedule) ─────────────────────────────────────────────────────

def run_daemon() -> None:
    log.info(f"Mode démon activé — exécution chaque {RUN_DAY} à {RUN_TIME}")
    getattr(schedule.every(), RUN_DAY).at(RUN_TIME).do(run_pipeline)

    # Exécution immédiate au démarrage si on est un vendredi
    if datetime.now().strftime("%A").lower() == RUN_DAY:
        log.info("Nous sommes vendredi → exécution immédiate")
        run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Entrée ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de veille IA automatisé")
    parser.add_argument("--daemon", action="store_true", help="Mode planifié (boucle)")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
    else:
        run_pipeline()
