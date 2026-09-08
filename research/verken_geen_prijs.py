import os
from pathlib import Path

from playwright.sync_api import sync_playwright

def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "BusinessProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)

UITVOER = Path(__file__).resolve().parent / "_uitvoer_geen_prijs.txt"

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
    for i in range(kaarten.count()):
        kaart = kaarten.nth(i)
        try:
            titel = kaart.locator("[data-test-search-result-header-title]").inner_text(timeout=1000).strip()
        except Exception:
            titel = ""
        if any(naam in titel for naam in ["Brabantplein 30", "Loopkantstraat 23", "Neutronenlaan 70", "Vluchtoord 7", "Protonenlaan 2"]):
            try:
                tekst = kaart.inner_text(timeout=2000)
            except Exception:
                tekst = "(fout)"
            regels.append(f"\n=== {titel} ===")
            regels.append(tekst)

    UITVOER.write_text("\n".join(regels), encoding="utf-8")
    print("weggeschreven naar", UITVOER)
    context.close()
