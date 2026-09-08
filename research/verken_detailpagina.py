import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "ResearchProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


UITVOER = Path(__file__).resolve().parent / "_uitvoer_detailpagina.txt"
URL = "https://www.fundainbusiness.nl/kantoor/uden/object-43295734-oostwijk-1-b/"

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profiel_map(), channel="chrome", headless=False,
        locale="nl-NL", viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(URL, wait_until="commit", timeout=30000)
    page.wait_for_timeout(4000)

    regels = [f"URL: {page.url} | titel: {page.title()}"]

    regels.append("\n--- dt/dd paren (kenmerken) ---")
    try:
        dts = page.locator("dt")
        for i in range(dts.count()):
            dt = dts.nth(i)
            k = dt.inner_text(timeout=300).strip()
            dd = dt.locator("xpath=following-sibling::dd[1]")
            v = dd.first.inner_text(timeout=300).strip() if dd.count() else ""
            regels.append(f"{k}: {v}")
    except Exception as exc:
        regels.append(f"(fout bij dt/dd: {exc})")

    regels.append("\n--- volledige body tekst (8000 tekens) ---")
    try:
        txt = page.locator("body").inner_text(timeout=6000)
        regels.append(txt[:8000])
    except Exception as exc:
        regels.append(f"(fout: {exc})")

    UITVOER.write_text("\n".join(regels), encoding="utf-8")
    print("weggeschreven naar", UITVOER)

    context.close()
