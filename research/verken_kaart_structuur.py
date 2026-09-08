import os
from pathlib import Path

from playwright.sync_api import sync_playwright

def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "BusinessProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

UITVOER = Path(__file__).resolve().parent / "_uitvoer_kaart_structuur.txt"

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profiel_map(), channel="chrome", headless=False,
        locale="nl-NL", viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www.fundainbusiness.nl/kantoor/uden/", wait_until="commit", timeout=30000)
    page.wait_for_timeout(4000)

    regels = [f"URL: {page.url} | titel: {page.title()}"]

    # Zoek het volledige kaartcontainer-element rond het eerste resultaat via
    # het semantische data-test-attribuut van de titel (niet via anchor-tellen).
    try:
        titel = page.locator("[data-test-search-result-header-title]").first
        regels.append(f"\naantal titel-elementen op pagina: {page.locator('[data-test-search-result-header-title]').count()}")
        regels.append(f"aantal subtitel-elementen op pagina: {page.locator('[data-test-search-result-header-subtitle]').count()}")
        regels.append(f"aantal object-anchors (data-search-result-item-anchor): {page.locator('a[data-search-result-item-anchor]').count()}")

        # Klim tot een container met een redelijke, begrensde omvang en die
        # duidelijk EEN los resultaat is (bevat precies 1 titel-element).
        node = titel
        gevonden = None
        for niveau in range(10):
            node = node.locator("..")
            try:
                aantal_titels_hier = node.locator("[data-test-search-result-header-title]").count()
                html_lengte = len(node.evaluate("e => e.outerHTML"))
            except Exception:
                break
            regels.append(f"niveau {niveau}: aantal titel-elementen binnen deze ancestor = {aantal_titels_hier}, outerHTML-lengte = {html_lengte}")
            if aantal_titels_hier == 1 and html_lengte > 300:
                gevonden = node
            if aantal_titels_hier > 1 or html_lengte > 20000:
                break

        if gevonden is not None:
            html = gevonden.evaluate("e => e.outerHTML")
            regels.append("\n--- outerHTML van de gevonden kaartcontainer (eerste resultaat) ---")
            regels.append(html[:6000])
        else:
            regels.append("\nGeen eenduidige eenrresultaat-container gevonden binnen 10 niveaus.")
    except Exception as exc:
        regels.append(f"\nFout: {exc}")

    UITVOER.write_text("\n".join(regels), encoding="utf-8")
    print("weggeschreven naar", UITVOER)

    context.close()
