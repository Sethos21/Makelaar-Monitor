import os
from pathlib import Path

from playwright.sync_api import sync_playwright

def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "BusinessProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

UITVOER = Path(__file__).resolve().parent / "_uitvoer_kaart_kenmerken.txt"

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profiel_map(), channel="chrome", headless=False,
        locale="nl-NL", viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www.fundainbusiness.nl/kantoor/uden/", wait_until="commit", timeout=30000)
    page.wait_for_timeout(4000)

    regels = [f"URL: {page.url}"]

    kaarten = page.locator("[data-search-result-listing]")
    aantal = kaarten.count()
    regels.append(f"aantal [data-search-result-listing] elementen: {aantal}")

    for i in range(min(aantal, 4)):
        kaart = kaarten.nth(i)
        klasse = kaart.get_attribute("class")
        try:
            titel = kaart.locator("[data-test-search-result-header-title]").inner_text(timeout=1000).strip()
        except Exception:
            titel = "(geen titel gevonden)"
        try:
            subtitel = kaart.locator("[data-test-search-result-header-subtitle]").inner_text(timeout=1000).strip()
        except Exception:
            subtitel = "(geen subtitel gevonden)"
        try:
            href = kaart.locator("a[data-search-result-item-anchor]").first.get_attribute("href")
            oid = kaart.locator("a[data-search-result-item-anchor]").first.get_attribute("data-search-result-item-anchor")
        except Exception:
            href, oid = None, None

        regels.append(f"\n=== kaart {i}: class='{klasse}' | titel='{titel}' | subtitel='{subtitel}' | id={oid} ===")
        try:
            volledige_tekst = kaart.inner_text(timeout=2000)
            regels.append("--- volledige inner_text van de kaart ---")
            regels.append(volledige_tekst)
        except Exception as exc:
            regels.append(f"(fout bij inner_text: {exc})")

    UITVOER.write_text("\n".join(regels), encoding="utf-8")
    print("weggeschreven naar", UITVOER)

    context.close()
