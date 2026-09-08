import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "ResearchProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


UITVOER = Path(__file__).resolve().parent / "_uitvoer_uden_lijst.txt"

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profiel_map(), channel="chrome", headless=False,
        locale="nl-NL", viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www.fundainbusiness.nl/alle-bedrijfsaanbod/uden/", wait_until="commit", timeout=30000)
    page.wait_for_timeout(4000)

    regels = []
    regels.append(f"URL: {page.url} | titel: {page.title()}")
    txt = page.locator("body").inner_text(timeout=5000)
    regels.append("\n--- body (6000 tekens) ---")
    regels.append(txt[:6000])

    regels.append("\n--- alle /alle-bedrijfsaanbod/ links (uniek) ---")
    anchors = page.locator("a")
    seen = set()
    for i in range(min(anchors.count(), 600)):
        a = anchors.nth(i)
        href = a.get_attribute("href") or ""
        if "/alle-bedrijfsaanbod/" in href and href not in seen:
            seen.add(href)
            try:
                tekst = a.inner_text(timeout=150).strip()
            except Exception:
                tekst = ""
            regels.append(f"{href}  ::  {tekst[:60]}")

    UITVOER.write_text("\n".join(regels), encoding="utf-8")
    print("weggeschreven naar", UITVOER)

    context.close()
