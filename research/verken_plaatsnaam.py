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
        user_data_dir=profiel_map(), channel="chrome", headless=False,
        locale="nl-NL", viewport={"width": 1440, "height": 1000},
    )
    page = context.pages[0] if context.pages else context.new_page()

    page.goto(
        "https://www.fundainbusiness.nl/alle-bedrijfsaanbod/bladeren/regio-noordoost-noord-brabant/?actpnl=Plaatsnaam",
        wait_until="commit", timeout=30000,
    )
    page.wait_for_timeout(4000)

    print("URL:", page.url, "| titel:", page.title())
    txt = page.locator("body").inner_text(timeout=5000)
    print("\n--- body (3000 tekens) ---")
    print(txt[:3000])

    print("\n--- links met uden ---")
    anchors = page.locator("a")
    for i in range(min(anchors.count(), 400)):
        a = anchors.nth(i)
        href = a.get_attribute("href") or ""
        if "uden" in href.lower():
            try:
                tekst = a.inner_text(timeout=150).strip()
            except Exception:
                tekst = ""
            print(f"{href}  ::  {tekst[:40]}")

    context.close()
