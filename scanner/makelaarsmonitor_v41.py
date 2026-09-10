#!/usr/bin/env python3
"""
Funda Makelaarsmonitor v3.2 - SNELLE SCAN MET GEBIEDSBEVEILIGING

Belangrijkste wijzigingen t.o.v. v3.1a
---------------------------------------
1. Gebiedsbeveiliging:
   Na iedere navigatie controleert de scanner of de zoekopdracht nog steeds
   het gewenste gebied bevat (standaard Uden).

2. Robuuste paginering:
   De scanner probeert niet meer blind een relatieve "?page=2"-link te volgen.
   Voor pagina 2+ wordt de bekende Funda-zoek-URL opnieuw opgebouwd waarbij
   selected_area=uden behouden blijft.

3. Toppositie als aparte sectie:
   De compacte Toppositie-advertenties boven de gewone zoekresultaten worden
   apart gelezen. Alleen voor maximaal drie Toppositie-objecten wordt een
   detailpagina bezocht.

4. Gewone resultaatkaarten blijven snelle bron:
   - adres
   - vraagprijs
   - statuslabel
   - woonoppervlakte
   - perceeloppervlakte
   - slaapkamers
   - energielabel
   - makelaar
   - detail-URL

5. Checkpoint na iedere pagina en na ieder Toppositie-detailobject.

Gebruik Uden:
    py funda_snelle_scan_v32.py --pages 13
"""

import argparse
import csv
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from _stopcontrole import ScanGestopt, controleer_stop, STOP_EXITCODE

DEFAULT_URL = "https://www.funda.nl/zoeken/koop?selected_area=uden"

# Bestandsnaam van het coöperatieve stopvlag-bestand, gezocht in --output-map
# (zie DEEL B). Zelfde mechanisme als de Business-scanner, maar in de eigen
# outputmap van deze scanner - zie ook scanner/_stopcontrole.py.
STOP_FLAG_NAAM = "_woningen_scan_stop.flag"

# Maximale wachttijd op een menscontrole/captcha voordat de scan alsnog wordt
# afgebroken (begrensde timeout, geen oneindig wachten - net als Business).
MENSCONTROLE_MAX_WACHT_S = 1800

FIELDS = [
    "Peildatum", "Plaats", "Gevonden_bij_scan",
    "Bouwcategorie", "Nieuwbouwreden",
    "Resultaatpagina", "Toppositie",
    "Adres", "Vraagprijs", "Status",
    "Woonoppervlakte_m2", "Perceeloppervlakte_m2",
    "Slaapkamers", "Energielabel", "Makelaar",
    "Funda_detail_URL", "Bron", "Waarschuwing"
]


def say(x=""):
    print(x, flush=True)


def cli():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--plaatsen",
        nargs="+",
        default=["Uden"],
        help='Eén of meer plaatsen, bv. --plaatsen Uden Volkel Nistelrode'
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=30,
        help="Veilig maximum per plaats; scanner stopt eerder als er geen nieuwe resultaten meer zijn."
    )
    p.add_argument("--delay", type=float, default=0.7)
    p.add_argument(
        "--output-map",
        default=".",
        help="Map voor checkpoint/eindbestanden en het stopvlag-bestand (zie DEEL B). "
             "Standaard de huidige map, voor compatibiliteit met eerder CLI-gebruik."
    )
    return p.parse_args()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def canonical(url):
    return (url or "").split("#")[0].rstrip("/")


def chrome_profile():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "ChromeProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)



def funda_area_slug(place):
    """Maak een praktische Funda selected_area slug voor gewone plaatsnamen."""
    s = unicodedata.normalize("NFKD", place).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def base_url_for_place(place):
    return f"https://www.funda.nl/zoeken/koop?selected_area={funda_area_slug(place)}"


def actual_place_from_url(url):
    """
    Haal de objectplaats uit een normale Funda detail-URL:
    .../koop/uden/huis-... -> uden
    .../koop/nistelrode/appartement-... -> nistelrode
    """
    try:
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            if part in ("koop", "huur") and i + 1 < len(parts):
                return parts[i + 1].strip().lower()
    except Exception:
        pass
    return ""


def normalized_place(place):
    return funda_area_slug(place)


def display_place_from_slug(slug, selected_places):
    for p in selected_places:
        if normalized_place(p) == slug:
            return p
    return slug.replace("-", " ").title() if slug else ""


def classify_newbuild(row):
    reasons = []
    makelaar = clean(str(row.get("Makelaar", ""))).casefold()
    adres = clean(str(row.get("Adres", "")))

    if makelaar == "meerdere makelaars":
        reasons.append("Makelaar = Meerdere makelaars")
    if re.search(r"\bbouwnr\.?\b", adres, re.I):
        reasons.append("Bouwnr. in adres")

    if reasons:
        row["Bouwcategorie"] = "Nieuwbouw"
        row["Nieuwbouwreden"] = "; ".join(reasons)
    else:
        row["Bouwcategorie"] = "Bestaande bouw"
        row["Nieuwbouwreden"] = ""
    return row


def body_text(page):
    try:
        return clean(page.locator("body").inner_text(timeout=8000))
    except Exception:
        return ""


def needs_human(page):
    t = body_text(page).lower()
    return any(x in t for x in [
        "ik ben geen robot", "captcha", "verify you are human",
        "controleer of je een mens bent", "bevestig dat je geen robot bent"
    ])


def pause(page, reason, stop_flag_pad):
    """Wacht op menscontrole/captcha ZONDER blokkerende input()/ENTER: peilt
    elke 2s of (a) een stop is aangevraagd of (b) de controle in het
    zichtbare Chrome-venster al is opgelost (needs_human() weer False), en
    gaat dan vanzelf verder. Begrensd door MENSCONTROLE_MAX_WACHT_S en op elk
    moment onderbreekbaar via het stopvlag-bestand. Zelfde patroon als de
    inmiddels goedgekeurde Business-scanner (zie DEEL A/B)."""
    say("\n" + "=" * 72)
    say("FUNDA HEEFT AANDACHT NODIG")
    say(reason)
    say(f"Pagina: {page.url}")
    say("Wacht op handmatige Funda-verificatie. Los dit op in het zichtbare "
        "Chrome-venster - de scan gaat automatisch verder zodra Funda weer "
        "normale resultaten toont. Geen ENTER nodig.")

    gewacht_s = 0.0
    while True:
        controleer_stop(stop_flag_pad)
        time.sleep(2)
        gewacht_s += 2
        if not needs_human(page):
            say("Verificatie lijkt opgelost - scan gaat automatisch verder.")
            return
        if gewacht_s >= MENSCONTROLE_MAX_WACHT_S:
            raise SystemExit(
                f"Menscontrole niet binnen {int(MENSCONTROLE_MAX_WACHT_S)}s opgelost - "
                "scan afgebroken. Start opnieuw en los de controle sneller op."
            )


def safe_goto(page, url, stop_flag_pad, wait_ms=1400):
    while True:
        controleer_stop(stop_flag_pad)
        try:
            page.goto(url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(wait_ms)
            if needs_human(page):
                pause(page, "Robot-/menscontrole zichtbaar.", stop_flag_pad)
                continue
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            pause(page, f"Navigatieprobleem: {exc}", stop_flag_pad)


def resultaten_automatisch_gereed(page, stop_flag_pad, timeout_ms=15000):
    """
    Probeert zonder handmatige ENTER-bevestiging vast te stellen dat de
    normale Funda-zoekresultaten klaar zijn om uit te lezen:

      1. wacht tot minstens één detail-link in de DOM aanwezig is
         (dezelfde selector als all_detail_anchors() gebruikt voor het
         daadwerkelijke uitlezen, dus geen nieuwe/afwijkende aanname);
      2. controleert daarna nogmaals op een mens-/captchacontrole
         (dezelfde needs_human()-detectie als elders in dit script).

    Retourneert True als dit automatisch is vastgesteld (of als er een
    menscontrole was die inmiddels is opgelost). Retourneert False als er
    binnen timeout_ms geen enkele detail-link verscheen - de aanroeper
    behandelt dat NIET als fout maar als een mogelijk geldig "0 aanbod"-
    resultaat (zie DEEL A: geen blokkerende ENTER-fallback meer)."""
    try:
        page.locator('a[href*="/detail/koop/"]').first.wait_for(
            state="attached", timeout=timeout_ms
        )
    except PlaywrightTimeoutError:
        if needs_human(page):
            pause(page, "Mens-/captchacontrole gedetecteerd (timeout bij wachten op resultaten).", stop_flag_pad)
            return resultaten_automatisch_gereed(page, stop_flag_pad, timeout_ms)
        return False

    if needs_human(page):
        pause(page, "Mens-/captchacontrole gedetecteerd vóór het uitlezen.", stop_flag_pad)

    return True


def scroll_results(page):
    for frac in (0.2, 0.45, 0.7, 1.0):
        try:
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {frac})")
            page.wait_for_timeout(250)
        except Exception:
            pass


def query_params(url):
    return dict(parse_qsl(urlparse(url).query, keep_blank_values=True))


def wanted_area(base_url):
    q = query_params(base_url)
    return q.get("selected_area", "").strip().lower()


def page_url(base_url, nr):
    """
    Bouw iedere pagina opnieuw op vanuit de basis-URL zodat selected_area
    gegarandeerd behouden blijft.
    Probeert Funda's huidige 'page'-parameter.
    """
    parts = list(urlparse(base_url))
    q = dict(parse_qsl(parts[4], keep_blank_values=True))
    q.pop("search_result", None)
    q.pop("page", None)
    if nr > 1:
        q["page"] = str(nr)
    parts[4] = urlencode(q)
    return urlunparse(parts)


def area_guard(page, expected_area):
    """
    1. URL moet selected_area behouden.
    2. Paginatekst moet nog het gewenste gebied tonen.
    """
    current = page.url or ""
    q = query_params(current)
    current_area = q.get("selected_area", "").strip().lower()

    if expected_area and current_area != expected_area:
        return False, f"selected_area ontbreekt/veranderd in URL ({current_area!r})"

    txt = body_text(page).lower()
    if expected_area:
        # verwachte patronen zoals "187 koopwoningen in Uden"
        if f"in {expected_area}" not in txt and expected_area not in txt[:2500]:
            return False, f"gebied '{expected_area}' niet herkenbaar in paginainhoud"

    return True, ""


def all_detail_anchors(page):
    try:
        loc = page.locator('a[href*="/detail/koop/"]')
        out = []
        seen = set()
        for i in range(loc.count()):
            a = loc.nth(i)
            href = a.get_attribute("href") or ""
            if href.startswith("/"):
                href = urljoin(page.url, href)
            href = canonical(href)
            if href and href not in seen:
                seen.add(href)
                out.append((a, href))
        return out
    except Exception:
        return []


def _unieke_detail_urls_in(node, base_url):
    """Aantal UNIEKE koop-detailpagina's waarnaar binnen deze DOM-node wordt
    verwezen. Telt bewust UNIEKE URL's, niet het aantal <a>-elementen: Funda's
    huidige resultaatkaart-ontwerp bevat vaak meerdere losse links naar
    dezelfde eigen detailpagina (foto, titel, 'Bekijk dit huis'-knop) - een
    kale telling van <a>-elementen overschat daardoor het aantal kaarten en
    laat een geldige, enkele kaart onterecht afvallen (root cause van het
    ontbreken van een groot deel van de resultaten, live bevestigd op
    2026-09-09 - zie docs/CLAUDE_STATUS.md)."""
    hrefs = node.locator('a[href*="/detail/koop/"]').evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    uniek = set()
    for h in hrefs:
        if not h:
            continue
        if h.startswith("/"):
            h = urljoin(base_url, h)
        uniek.add(canonical(h))
    return len(uniek)


def ancestor_card(anchor, max_text=2200):
    node = anchor
    best = None
    base_url = anchor.page.url
    for _ in range(9):
        try:
            node = node.locator("..")
            txt = clean(node.inner_text(timeout=400))
            tag = node.evaluate("e => e.tagName.toLowerCase()")
            unieke_urls = _unieke_detail_urls_in(node, base_url)
        except Exception:
            break
        if txt and unieke_urls == 1 and len(txt) <= max_text and ("€" in txt or "m²" in txt):
            best = node
            if tag in ("article", "li"):
                return node
        if unieke_urls > 1 or len(txt) > 3000:
            break
    return best


def parse_price(txt):
    for raw in re.findall(r"€\s*([\d\.\,]+)", txt or ""):
        try:
            v = int(float(raw.replace(".", "").replace(",", ".")))
            if v >= 50000:
                return v
        except Exception:
            pass
    return ""


def parse_status(txt):
    t = clean(txt).lower()
    if "verkocht onder voorbehoud" in t:
        return "Verkocht onder voorbehoud"
    if "onder bod" in t:
        return "Onder bod"
    return "Beschikbaar"


def parse_address(card, anchor):
    try:
        t = anchor.inner_text(timeout=350)
        for line in t.splitlines():
            line = clean(line)
            if re.search(r"\d", line) and "€" not in line and len(line) < 130:
                return line
    except Exception:
        pass

    for sel in ("h2", "h3", "h4"):
        try:
            loc = card.locator(sel)
            for i in range(min(loc.count(), 5)):
                t = clean(loc.nth(i).inner_text(timeout=350))
                if t and re.search(r"\d", t):
                    return t
        except Exception:
            pass

    try:
        for line in card.inner_text(timeout=500).splitlines():
            line = clean(line)
            if re.search(r"\d", line) and "€" not in line and "m²" not in line and len(line) < 130:
                return line
    except Exception:
        pass
    return ""


def parse_broker(card):
    try:
        links = card.locator("a")
        for i in range(min(links.count(), 50)):
            a = links.nth(i)
            txt = clean(a.inner_text(timeout=220))
            href = (a.get_attribute("href") or "").lower()
            aria = (a.get_attribute("aria-label") or "").lower()
            signal = " ".join([txt.lower(), href, aria])
            if txt and len(txt) <= 120 and "makelaar" in signal:
                if txt.lower() not in {"makelaar", "verkoopmakelaar", "contact met makelaar"}:
                    return txt
    except Exception:
        pass

    try:
        txt = card.inner_text(timeout=500)
        for line in reversed(txt.splitlines()):
            line = clean(line)
            low = line.lower()
            if line and len(line) <= 120 and any(x in low for x in ["makela", "re/max", "remax", "vastgoed"]):
                return line
    except Exception:
        pass
    return ""


def parse_features(card):
    """
    Gebaseerd op de door gebruiker bevestigde visuele volgorde:
      1 = wonen
      2 = perceel
      3 = slaapkamers
      4 = energielabel
    """
    try:
        raw = card.inner_text(timeout=700)
    except Exception:
        raw = ""

    wonen = perceel = slaapkamers = label = ""

    m2s = [int(v) for v in re.findall(r"(\d{1,4})\s*m²", raw)]
    if m2s:
        wonen = m2s[0]
    if len(m2s) > 1:
        perceel = m2s[1]

    # Expliciete woorden eerst
    m = re.search(r"(?:slaapkamers?|bedrooms?)\s*[:\-]?\s*(\d+)", raw, re.I)
    if m:
        slaapkamers = int(m.group(1))

    lm = re.search(r"energielabel\s*([A-G](?:\+{1,4})?)", raw, re.I)
    if lm:
        label = lm.group(1).upper()

    # DOM fallback: korte losse tokens in volgorde
    if not slaapkamers or not label:
        tokens = []
        selectors = ["li", "dd", "span"]
        for sel in selectors:
            try:
                loc = card.locator(sel)
                for i in range(min(loc.count(), 120)):
                    x = clean(loc.nth(i).inner_text(timeout=150))
                    if not x or len(x) > 25:
                        continue
                    if re.fullmatch(r"\d{1,2}", x) or re.fullmatch(r"[A-G](?:\+{1,4})?", x, re.I):
                        tokens.append(x)
            except Exception:
                pass

        if not slaapkamers:
            for x in tokens:
                if re.fullmatch(r"\d{1,2}", x):
                    slaapkamers = int(x)
                    break

        if not label:
            for x in tokens:
                if re.fullmatch(r"[A-G](?:\+{1,4})?", x, re.I):
                    label = x.upper()
                    break

    return wonen, perceel, slaapkamers, label


def parse_regular_card(anchor, url, page_nr, place):
    card = ancestor_card(anchor)
    if card is None:
        return None

    try:
        raw = card.inner_text(timeout=650)
    except Exception:
        raw = ""

    adres = parse_address(card, anchor)
    prijs = parse_price(raw)
    makelaar = parse_broker(card)
    status = parse_status(raw)
    wonen, perceel, slaapkamers, label = parse_features(card)

    warnings = []
    if not adres:
        warnings.append("adres ontbreekt")
    if not prijs:
        warnings.append("vraagprijs ontbreekt")
    if not makelaar:
        warnings.append("makelaar ontbreekt")
    if not wonen:
        warnings.append("woonoppervlakte ontbreekt")

    row = {
        "Peildatum": datetime.now().strftime("%Y-%m-%d"),
        "Plaats": place,
        "Gevonden_bij_scan": place,
        "Bouwcategorie": "",
        "Nieuwbouwreden": "",
        "Resultaatpagina": page_nr,
        "Toppositie": "Nee",
        "Adres": adres,
        "Vraagprijs": prijs,
        "Status": status,
        "Woonoppervlakte_m2": wonen,
        "Perceeloppervlakte_m2": perceel,
        "Slaapkamers": slaapkamers,
        "Energielabel": label,
        "Makelaar": makelaar,
        "Funda_detail_URL": url,
        "Bron": "resultaatkaart",
        "Waarschuwing": "; ".join(warnings),
    }
    return classify_newbuild(row)


def find_toppositie_section(page):
    """
    Zoek de sectie boven de tekst '<n> koopwoningen in <plaats>'.
    Dit is de Toppositie-carrousel.
    """
    try:
        marker = page.get_by_text(re.compile(r"\d+\s+koopwoningen", re.I)).first
        if marker.count() == 0:
            return None

        # Loop omhoog tot een container die zowel 'Toppositie' als marker bevat
        node = marker
        for _ in range(7):
            node = node.locator("..")
            txt = clean(node.inner_text(timeout=500))
            if "toppositie" in txt.lower():
                return node
    except Exception:
        pass

    # Fallback: element met tekst Toppositie en parent
    try:
        loc = page.get_by_text("Toppositie", exact=False)
        if loc.count():
            node = loc.first
            for _ in range(4):
                node = node.locator("..")
                txt = clean(node.inner_text(timeout=500))
                if len(txt) < 3000:
                    return node
    except Exception:
        pass

    return None


def top_cards(page):
    section = find_toppositie_section(page)
    if section is None:
        return []

    try:
        anchors = section.locator('a[href*="/detail/koop/"]')
        out, seen = [], set()
        for i in range(anchors.count()):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            if href.startswith("/"):
                href = urljoin(page.url, href)
            href = canonical(href)
            if href and href not in seen:
                seen.add(href)
                out.append((a, href))
        return out[:3]
    except Exception:
        return []


def parse_top_card(anchor, url, place):
    card = ancestor_card(anchor, max_text=1200)
    if card is None:
        row = {
            "Peildatum": datetime.now().strftime("%Y-%m-%d"),
            "Plaats": place,
            "Bouwcategorie": "",
            "Nieuwbouwreden": "",
            "Resultaatpagina": 1,
            "Toppositie": "Ja",
            "Adres": "",
            "Vraagprijs": "",
            "Status": "Beschikbaar",
            "Woonoppervlakte_m2": "",
            "Perceeloppervlakte_m2": "",
            "Slaapkamers": "",
            "Energielabel": "",
            "Makelaar": "",
            "Funda_detail_URL": url,
            "Bron": "Toppositie-kaart",
            "Waarschuwing": "Toppositie-kaart niet goed gelezen",
        }
        return classify_newbuild(row)

    try:
        raw = card.inner_text(timeout=500)
    except Exception:
        raw = ""

    row = {
        "Peildatum": datetime.now().strftime("%Y-%m-%d"),
        "Plaats": place,
        "Gevonden_bij_scan": place,
        "Bouwcategorie": "",
        "Nieuwbouwreden": "",
        "Resultaatpagina": 1,
        "Toppositie": "Ja",
        "Adres": parse_address(card, anchor),
        "Vraagprijs": parse_price(raw),
        "Status": parse_status(raw),
        "Woonoppervlakte_m2": "",
        "Perceeloppervlakte_m2": "",
        "Slaapkamers": "",
        "Energielabel": "",
        "Makelaar": parse_broker(card),
        "Funda_detail_URL": url,
        "Bron": "Toppositie-kaart",
        "Waarschuwing": "",
    }
    return classify_newbuild(row)


def detail_pairs(page):
    pairs = {}
    try:
        dts = page.locator("dt")
        for i in range(dts.count()):
            dt = dts.nth(i)
            k = clean(dt.inner_text(timeout=350)).casefold()
            dd = dt.locator("xpath=following-sibling::dd[1]")
            if k and dd.count():
                v = clean(dd.first.inner_text(timeout=350))
                if v:
                    pairs.setdefault(k, v)
    except Exception:
        pass
    return pairs


def parse_m2(txt):
    m = re.search(r"(\d{1,4})\s*m²", txt or "")
    return int(m.group(1)) if m else ""


def enrich_top_detail(page, row, stop_flag_pad):
    safe_goto(page, row["Funda_detail_URL"], stop_flag_pad, 1500)
    pairs = detail_pairs(page)
    txt = body_text(page)

    try:
        h1 = page.locator("h1").first.inner_text(timeout=2500)
        addr = next((clean(x) for x in h1.splitlines() if clean(x)), "")
        if addr:
            row["Adres"] = addr
    except Exception:
        pass

    if not row.get("Vraagprijs"):
        row["Vraagprijs"] = parse_price(pairs.get("vraagprijs", "") or txt[:5000])

    wonen = parse_m2(pairs.get("wonen", ""))
    perceel = parse_m2(pairs.get("perceel", ""))
    if wonen:
        row["Woonoppervlakte_m2"] = wonen
    if perceel:
        row["Perceeloppervlakte_m2"] = perceel

    m = re.search(r"(\d+)\s+slaapkamers?", txt, re.I)
    if m:
        row["Slaapkamers"] = int(m.group(1))

    lm = re.search(r"\b([A-G](?:\+{1,4})?)\b", pairs.get("energielabel", ""), re.I)
    if lm:
        row["Energielabel"] = lm.group(1).upper()

    status = clean(pairs.get("status", ""))
    if status in {"Beschikbaar", "Onder bod", "Verkocht onder voorbehoud"}:
        row["Status"] = status
    else:
        row["Status"] = parse_status(txt)

    # Detail fallback broker
    if not row.get("Makelaar"):
        try:
            links = page.locator("a")
            for i in range(min(links.count(), 250)):
                a = links.nth(i)
                at = clean(a.inner_text(timeout=200))
                href = (a.get_attribute("href") or "").lower()
                aria = (a.get_attribute("aria-label") or "").lower()
                if at and len(at) <= 120 and "makelaar" in " ".join([at.lower(), href, aria]):
                    if at.lower() not in {"makelaar", "verkoopmakelaar", "contact met makelaar"}:
                        row["Makelaar"] = at
                        break
        except Exception:
            pass

    row["Bron"] = "detailpagina (Toppositie)"
    return classify_newbuild(row)


def write_csv(path, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def finalize_row_place(row, selected_places):
    """Zet Plaats op de werkelijke URL-plaats en retourneer of die binnen selectie valt."""
    url = row.get("Funda_detail_URL", "")
    slug = actual_place_from_url(url)
    selected_slugs = {normalized_place(p) for p in selected_places}

    if not slug:
        row["Waarschuwing"] = clean(
            (row.get("Waarschuwing", "") + "; objectplaats niet uit URL te bepalen").strip("; ")
        )
        return False

    row["Plaats"] = display_place_from_slug(slug, selected_places)
    return slug in selected_slugs


def merge_duplicate(existing, incoming):
    """
    Houd één object per Funda-URL.
    Bewaar wel alle scans waarin het object is opgedoken.
    Voorkeur voor de rij met de meeste ingevulde velden.
    """
    origins = []
    for value in (existing.get("Gevonden_bij_scan", ""), incoming.get("Gevonden_bij_scan", "")):
        for x in str(value).split(";"):
            x = clean(x)
            if x and x not in origins:
                origins.append(x)

    def score(r):
        keys = [
            "Adres", "Vraagprijs", "Status", "Woonoppervlakte_m2",
            "Perceeloppervlakte_m2", "Slaapkamers", "Energielabel", "Makelaar"
        ]
        return sum(1 for k in keys if clean(str(r.get(k, ""))))

    keep = incoming if score(incoming) > score(existing) else existing
    keep["Gevonden_bij_scan"] = "; ".join(origins)
    return keep


def safe_output_name(places):
    s = "_".join(funda_area_slug(p) for p in places)
    return s[:120] or "scan"


def main():
    opt = cli()
    places = [clean(p) for p in opt.plaatsen if clean(p)]
    if not places:
        raise SystemExit("Geen plaatsen opgegeven.")

    outdir = Path(opt.output_map)
    outdir.mkdir(parents=True, exist_ok=True)
    stop_flag_pad = outdir / STOP_FLAG_NAAM

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = safe_output_name(places)

    checkpoint = outdir / f"makelaarsmonitor_{label}_{stamp}_checkpoint.csv"
    final_all = outdir / f"makelaarsmonitor_{label}_{stamp}_alles.csv"
    final_existing = outdir / f"makelaarsmonitor_{label}_{stamp}_bestaande_bouw.csv"
    final_new = outdir / f"makelaarsmonitor_{label}_{stamp}_nieuwbouw.csv"

    combined_by_url = {}
    summary = []
    gestopt = False

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=chrome_profile(),
            channel="chrome",
            headless=False,
            locale="nl-NL",
            viewport={"width": 1440, "height": 1000},
            args=["--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            for place_index, place in enumerate(places, 1):
                controleer_stop(stop_flag_pad)
                base_url = base_url_for_place(place)
                expected = wanted_area(base_url)

                say("\n" + "=" * 72)
                say(f"PLAATS {place_index}/{len(places)}: {place}")
                say(f"Aangevraagde URL: {page_url(base_url, 1)}")
                say(f"Funda gebied (verwacht): {expected}")

                safe_goto(page, page_url(base_url, 1), stop_flag_pad, 1800)
                say(f"Uiteindelijke URL: {page.url}")

                ok, reason = area_guard(page, expected)
                if not ok:
                    # BELANGRIJK (DEEL A): een mislukte gebiedscontrole betekent
                    # hier NIET automatisch een captcha - meestal herkent Funda
                    # de plaats-slug simpelweg niet en valt stilletjes terug op
                    # de algemene /zoeken/koop-resultaten (bevestigd via live
                    # onderzoek, bv. bij Vinkel). Dat is GEEN geldige "0 aanbod"-
                    # uitkomst en mag nooit als plaatsresultaat worden gebruikt.
                    # Geen blokkerende pause()/ENTER hier - éénmalig herladen
                    # als mogelijke transiënte glitch, anders deze plaats
                    # overslaan met duidelijke logging en doorgaan.
                    say(f"Gebiedscontrole mislukt voor {place}: {reason}")
                    controleer_stop(stop_flag_pad)
                    time.sleep(2)
                    safe_goto(page, page_url(base_url, 1), stop_flag_pad, 1800)
                    say(f"Uiteindelijke URL (2e poging): {page.url}")
                    ok, reason = area_guard(page, expected)

                if not ok:
                    say(
                        f"WAARSCHUWING: Funda herkent '{place}' niet als eigen zoekgebied "
                        f"(reden: {reason}). Dit lijkt een algemene fallback-pagina, GEEN "
                        f"plaatsspecifiek resultaat - er worden GEEN objecten van deze "
                        f"fallback-pagina als resultaat van {place} opgeslagen. {place} "
                        "wordt overgeslagen (dit is NIET hetzelfde als '0 aanbod')."
                    )
                    summary.append((place, 0, 0, 0, 0, 0, 0, "gebied niet herkend door Funda"))
                    continue

                if resultaten_automatisch_gereed(page, stop_flag_pad):
                    say("Resultaten automatisch gereed bevonden (resultaatcontainer met detail-links aanwezig).")
                else:
                    say(
                        "Geen enkele detail-link gevonden binnen de wachttijd. Gebied is wél "
                        "herkend door Funda, dus dit wordt behandeld als een mogelijk geldig "
                        "'0 aanbod'-resultaat voor deze plaats (geen blokkerende ENTER meer)."
                    )

                scroll_results(page)

                # Toppositie uitsluitend op pagina 1 van deze plaats.
                top_rows = {}
                for a, u in top_cards(page):
                    if u not in top_rows:
                        top_rows[u] = parse_top_card(a, u, place)

                rows = {}
                previous_page_urls = None
                bezochte_pagina_urls = set()

                for nr in range(1, opt.max_pages + 1):
                    controleer_stop(stop_flag_pad)
                    url = page_url(base_url, nr)

                    if url in bezochte_pagina_urls:
                        say(f"Pagina {nr:>2}: URL al eerder bezocht -> einde {place}.")
                        break
                    bezochte_pagina_urls.add(url)

                    safe_goto(page, url, stop_flag_pad, 1300)

                    ok, reason = area_guard(page, expected)
                    if not ok:
                        # Gebied kwijtgeraakt halverwege de paginering (bv. Funda
                        # geeft bij een hoog paginanummer soms een fallback). Geen
                        # blokkerende pause meer: eerder verzamelde pagina's van
                        # DEZE plaats blijven gewoon geldig, we stoppen alleen de
                        # verdere paginering hier.
                        say(f"Pagina {nr:>2} van {place} buiten het gewenste gebied ({reason}) -> einde {place}.")
                        break

                    scroll_results(page)
                    anchors = all_detail_anchors(page)
                    current_urls = [u for _, u in anchors]

                    # Automatisch einde:
                    # - helemaal geen resultaatlinks, of
                    # - exact dezelfde set als vorige pagina.
                    if not current_urls:
                        say(f"Pagina {nr:>2}: geen resultaten meer -> einde {place}.")
                        break

                    if previous_page_urls is not None and set(current_urls) == set(previous_page_urls):
                        say(f"Pagina {nr:>2}: dezelfde resultaten als vorige pagina -> einde {place}.")
                        break

                    previous_page_urls = current_urls

                    before = len(rows)
                    for a, u in anchors:
                        if u in top_rows or u in rows:
                            continue
                        row = parse_regular_card(a, u, nr, place)
                        if row is not None:
                            rows[u] = row

                    # Als een pagina alleen duplicaten oplevert, stoppen we ook.
                    new_count = len(rows) - before
                    if nr > 1 and new_count == 0:
                        say(f"Pagina {nr:>2}: geen nieuwe unieke objecten -> einde {place}.")
                        break

                    preview = list(combined_by_url.values()) + list(top_rows.values()) + list(rows.values())
                    write_csv(checkpoint, preview)

                    say(
                        f"Pagina {nr:>2}: {len(anchors):>2} detail-links | "
                        f"{new_count:>2} nieuwe gewone objecten | "
                        f"totaal {place}: {len(top_rows) + len(rows)}"
                    )

                    if opt.delay:
                        time.sleep(opt.delay)

                # Alleen Toppositie-details bezoeken.
                if top_rows:
                    say(f"\nToppositie {place}: {len(top_rows)} object(en) via detailpagina aanvullen.")
                    for i, u in enumerate(list(top_rows.keys())[:3], 1):
                        controleer_stop(stop_flag_pad)
                        say(f"  [{i}/{min(3, len(top_rows))}] {u}")
                        try:
                            top_rows[u] = enrich_top_detail(page, top_rows[u], stop_flag_pad)
                        except ScanGestopt:
                            raise
                        except Exception as exc:
                            top_rows[u]["Waarschuwing"] = clean(
                                (
                                    top_rows[u].get("Waarschuwing", "")
                                    + f"; detailfout: {exc}"
                                ).strip("; ")
                            )
                        write_csv(
                            checkpoint,
                            list(combined_by_url.values()) + list(top_rows.values()) + list(rows.values())
                        )

                raw_place_rows = list(top_rows.values()) + list(rows.values())

                # Werkelijke plaats uit URL bepalen en buitengebied-advertenties verwijderen.
                place_rows = []
                rejected = 0
                for r in raw_place_rows:
                    classify_newbuild(r)
                    if not finalize_row_place(r, places):
                        rejected += 1
                        continue
                    place_rows.append(r)

                    u = r.get("Funda_detail_URL", "")
                    if u in combined_by_url:
                        combined_by_url[u] = merge_duplicate(combined_by_url[u], r)
                    else:
                        combined_by_url[u] = r

                available = sum(1 for r in place_rows if r.get("Status") == "Beschikbaar")
                existing = sum(1 for r in place_rows if r.get("Bouwcategorie") == "Bestaande bouw")
                newbuild = sum(1 for r in place_rows if r.get("Bouwcategorie") == "Nieuwbouw")

                opmerking = "" if raw_place_rows else "0 aanbod (geldig resultaat, gebied wel herkend)"
                summary.append((place, len(raw_place_rows), len(place_rows), rejected, existing, newbuild, available, opmerking))

                say(
                    f"\n{place} klaar: {len(raw_place_rows)} bruto | "
                    f"{len(place_rows)} binnen gekozen gebied | {rejected} buitengebied verwijderd"
                )

                write_csv(checkpoint, list(combined_by_url.values()))
        except ScanGestopt:
            gestopt = True
            say("\nStop aangevraagd door gebruiker - scan wordt netjes afgebroken.")
        finally:
            # Playwright/Chrome ALTIJD netjes sluiten, ook bij een stop of een
            # onverwachte fout - voorkomt een wees-Chrome-proces.
            context.close()

    if gestopt:
        say("Scan afgebroken. Het checkpoint-bestand blijft staan (voor debugging), "
            "maar er wordt bewust GEEN *_alles.csv geschreven en dus ook geen "
            "historie-import uitgevoerd voor deze onvolledige scan.")
        raise SystemExit(STOP_EXITCODE)

    # Eindbestanden: één rij per unieke Funda-URL.
    combined_rows = list(combined_by_url.values())
    combined_rows.sort(key=lambda r: (
        normalized_place(r.get("Plaats", "")),
        clean(r.get("Adres", "")).casefold()
    ))

    existing_rows = [r for r in combined_rows if r.get("Bouwcategorie") == "Bestaande bouw"]
    new_rows = [r for r in combined_rows if r.get("Bouwcategorie") == "Nieuwbouw"]

    write_csv(final_all, combined_rows)
    write_csv(final_existing, existing_rows)
    write_csv(final_new, new_rows)

    say("\n" + "=" * 72)
    say("MAKELAARSMONITOR v4.1 KLAAR")
    say("")
    say("Per scan:")
    for place, bruto, accepted, rejected, existing, newbuild, available, opmerking in summary:
        say(
            f"  {place}: {bruto} bruto | {accepted} binnen selectie | "
            f"{rejected} buitengebied verwijderd"
            + (f" | {opmerking}" if opmerking else "")
        )

    say("\nWerkelijke objecten per plaats na deduplicatie:")
    for place in places:
        n = sum(1 for r in combined_rows if normalized_place(r.get("Plaats", "")) == normalized_place(place))
        say(f"  {place}: {n}")

    say("")
    urls = [r.get("Funda_detail_URL", "") for r in combined_rows if r.get("Funda_detail_URL")]
    duplicate_count = len(urls) - len(set(urls))
    outside_count = sum(
        1 for r in combined_rows
        if normalized_place(r.get("Plaats", "")) not in {normalized_place(p) for p in places}
    )

    say(f"Totaal unieke objecten: {len(combined_rows)}")
    say(f"Dubbele Funda-URLs in eindbestand: {duplicate_count}")
    say(f"Objecten buiten gekozen plaatsen: {outside_count}")
    say(f"Bestaande bouw: {len(existing_rows)}")
    say(f"Nieuwbouw: {len(new_rows)}")

    statuses = {}
    for r in existing_rows:
        key = r.get("Status") or "Onbekend"
        statuses[key] = statuses.get(key, 0) + 1

    say(
        "Status bestaande bouw: "
        + " | ".join(f"{k}: {v}" for k, v in statuses.items())
    )

    say("\nBestanden:")
    say(f"  Alles:           {final_all}")
    say(f"  Bestaande bouw:  {final_existing}")
    say(f"  Nieuwbouw:       {final_new}")
    say(f"  Checkpoint:      {checkpoint}")
    say("\nUpload bij voorkeur het bestand *_alles.csv in ChatGPT.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nScan afgebroken (Ctrl+C). Laatste checkpoint blijft behouden.")
        raise SystemExit(STOP_EXITCODE)
