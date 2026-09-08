"""Research-only verkenning van fundainbusiness.nl (koppelt niet aan de app).
Gebruikt een zichtbaar, echt Chrome-profiel (net als de woningenscanner),
omdat de site headless/generieke Chromium blokkeert met een verificatiewand.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def profiel_map():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "ResearchProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=profiel_map(),
        channel="chrome",
        headless=False,
        locale="nl-NL",
        viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(
        "https://www.fundainbusiness.nl/alle-bedrijfsaanbod/bladeren/",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    page.wait_for_timeout(3000)

    print("URL:", page.url)
    print("Titel:", page.title())

    print("\n--- Body tekst (eerste 2500 tekens) ---")
    try:
        txt = page.locator("body").inner_text(timeout=5000)
    except Exception as exc:
        txt = f"(fout: {exc})"
    print(txt[:2500])

    print("\n--- Links met huur/koop/kantoor/bedrijfsruimte/zoeken ---")
    anchors = page.locator("a")
    seen = set()
    for i in range(min(anchors.count(), 500)):
        a = anchors.nth(i)
        href = a.get_attribute("href") or ""
        laag = href.lower()
        if any(k in laag for k in ["huur", "koop", "kantoor", "bedrijfsruimte", "zoeken", "uden"]):
            if href and href not in seen:
                seen.add(href)
                print(href)

    context.close()
