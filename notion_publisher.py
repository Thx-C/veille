# notion_publisher.py — crée la page de flash hebdo dans Notion

from datetime import datetime, timedelta

from notion_client import Client

from config import NOTION_PARENT_PAGE_ID, NOTION_TOKEN

notion = Client(auth=NOTION_TOKEN)


# ── Helpers de blocs Notion ───────────────────────────────────────────────────

def _h1(text: str) -> dict:
    return {"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

def _h2(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

def _h3(text: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

def _p(text: str, bold: bool = False, color: str = "default") -> dict:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text},
                                     "annotations": {"bold": bold, "color": color}}]},
    }

def _callout(text: str, emoji: str = "💡") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": emoji},
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "color": "gray_background",
        },
    }

def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}

def _toggle(title: str, children: list[dict]) -> dict:
    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": title},
                           "annotations": {"bold": True}}],
            "children": children,
        },
    }

def _table_row(cells: list[str]) -> dict:
    return {
        "type": "table_row",
        "table_row": {"cells": [[{"type": "text", "text": {"content": c}}] for c in cells]},
    }

def _table(headers: list[str], rows: list[list[str]]) -> dict:
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": [_table_row(headers)] + [_table_row(r) for r in rows],
        },
    }


# ── Construction des blocs de la page ────────────────────────────────────────

def _build_blocks(flash: dict, articles: dict, week_label: str) -> list[dict]:
    """Construit la liste complète des blocs Notion pour la page."""
    blocks = []

    # ── En-tête ──
    blocks.append(_callout(
        f"Pipeline automatique · {week_label} · "
        f"{len(articles['cyber'])} articles cyber · "
        f"{len(articles['alignement'])} articles alignement",
        "🤖"
    ))
    blocks.append(_divider())

    # ── Chiffre de la semaine ──
    chiffre = flash.get("chiffre_semaine", {})
    if chiffre:
        blocks.append(_h2("🔢 Chiffre de la semaine"))
        blocks.append(_callout(
            f"{chiffre.get('valeur', '—')} — {chiffre.get('contexte', '')}",
            "📊"
        ))
        blocks.append(_divider())

    # ── Signal fort cyber ──
    sc = flash.get("signal_fort_cyber", {})
    if sc:
        blocks.append(_h2("🛡️ IA & cybersécurité — signal fort"))
        blocks.append(_p(sc.get("titre", ""), bold=True))
        blocks.append(_p(sc.get("resume", "")))
        impact = sc.get("impact", "")
        color  = {"Élevé": "red", "Moyen": "orange", "Faible": "green"}.get(impact, "default")
        blocks.append(_p(f"Impact : {impact}  ·  Source : {sc.get('source', '')}  ·  {sc.get('url', '')}", color=color))

    # ── Tableau cyber ──
    rows_c = flash.get("tableau_cyber", [])
    if rows_c:
        blocks.append(_h3("À retenir aussi"))
        rows = [[str(r.get("pertinence","?")), r.get("titre",""), r.get("source",""), r.get("tag","")] for r in rows_c]
        blocks.append(_table(["#", "Titre", "Source", "Tag"], rows))
    blocks.append(_divider())

    # ── CVE critiques ──
    cves = articles.get("cve", [])
    if cves:
        blocks.append(_h3("Vulnérabilités critiques CISA (semaine)"))
        cve_children = [
            _bullet(f"{c['title']} — {c['url']}")
            for c in cves[:8]
        ]
        blocks.append(_toggle("Voir les CVE (" + str(len(cves[:8])) + ")", cve_children))
        blocks.append(_divider())

    # ── Signal fort alignement ──
    sa = flash.get("signal_fort_alignement", {})
    if sa:
        blocks.append(_h2("🤖 Alignement & humanisation — signal fort"))
        blocks.append(_p(sa.get("titre", ""), bold=True))
        blocks.append(_p(sa.get("resume", "")))
        impact = sa.get("impact", "")
        color  = {"Élevé": "red", "Moyen": "orange", "Faible": "green"}.get(impact, "default")
        blocks.append(_p(f"Impact : {impact}  ·  Source : {sa.get('source', '')}  ·  {sa.get('url', '')}", color=color))

    # ── Tableau alignement ──
    rows_a = flash.get("tableau_alignement", [])
    if rows_a:
        blocks.append(_h3("À retenir aussi"))
        rows = [[str(r.get("pertinence","?")), r.get("titre",""), r.get("source",""), r.get("tag","")] for r in rows_a]
        blocks.append(_table(["#", "Titre", "Source", "Tag"], rows))
    blocks.append(_divider())

    # ── Signal croisé ──
    croise = flash.get("signal_croise", "")
    if croise:
        blocks.append(_h2("🔀 Signal croisé"))
        blocks.append(_callout(croise, "⚡"))
        blocks.append(_divider())

    # ── À surveiller ──
    a_surveiller = flash.get("a_surveiller", [])
    if a_surveiller:
        blocks.append(_h2("📅 À surveiller la semaine prochaine"))
        for item in a_surveiller:
            blocks.append(_bullet(item))
        blocks.append(_divider())

    # ── Articles complets dans des toggles ──
    for theme, label, emoji in [("cyber", "IA & cybersécurité", "🛡️"), ("alignement", "Alignement", "🤖")]:
        theme_articles = articles.get(theme, [])
        if not theme_articles:
            continue
        children = []
        for a in theme_articles:
            children.append(_p(a.get("titre_court", a["title"]), bold=True))
            children.append(_p(a.get("resume", a["summary"][:200])))
            tag  = a.get("tag_principal", "")
            imp  = a.get("impact", "")
            act  = a.get("action", "")
            score = round(a.get("score", 0), 2)
            children.append(_p(f"{tag}  ·  Impact : {imp}  ·  {act}  ·  Score : {score}"))
            children.append(_p(a["url"]))
            children.append(_divider())
        blocks.append(_toggle(f"{emoji} Tous les articles — {label} ({len(theme_articles)})", children))

    return blocks


# ── Création de la page Notion ────────────────────────────────────────────────

def publish_to_notion(flash: dict, articles: dict) -> str:
    """
    Crée une nouvelle page dans NOTION_PARENT_PAGE_ID.
    Retourne l'URL de la page créée.
    """
    now        = datetime.now()
    # Vendredi de la semaine courante
    friday     = now - timedelta(days=(now.weekday() - 4) % 7)
    week_start = friday - timedelta(days=6)
    week_label = f"{week_start.strftime('%d/%m')} → {friday.strftime('%d/%m/%Y')}"
    page_title = f"Flash veille IA — {week_label}"

    print(f"── Publication Notion : «{page_title}» ──")

    blocks = _build_blocks(flash, articles, week_label)

    # Notion limite à 100 blocs par appel — on découpe si nécessaire
    CHUNK = 100
    page = notion.pages.create(
        parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
        properties={
            "title": {"title": [{"type": "text", "text": {"content": page_title}}]}
        },
        children=blocks[:CHUNK],
    )

    page_id = page["id"]

    # Blocs supplémentaires si > 100
    for i in range(CHUNK, len(blocks), CHUNK):
        notion.blocks.children.append(
            block_id=page_id,
            children=blocks[i : i + CHUNK],
        )

    url = page.get("url", f"https://notion.so/{page_id.replace('-','')}")
    print(f"  → Page créée : {url}")
    return url
