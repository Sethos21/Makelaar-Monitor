#!/usr/bin/env python3
"""
Funda in Business - scanner v1 (Fase 1: zelfstandig testbaar, CSV-only)

Gebaseerd op het onderzoek in docs/FUNDA_BUSINESS_ONDERZOEK.md. Scrapet de
lijstpagina's van fundainbusiness.nl per objecttype + plaats (geen
detailpagina's in deze fase - dat houdt het aantal requests laag, wat
belangrijk is gezien de geconstateerde botdetectie).

BELANGRIJK
----------
- Volledig onafhankelijk van scanner/makelaarsmonitor_v41.py (de
  woningenscanner) en de woningentabellen. Niets daarvan wordt hier
  aangeraakt of geimporteerd.
- Nog NIET gekoppeld aan app.py en NIET aan de productiedatabase. Schrijft
  uitsluitend CSV-bestanden.
- Gebruikt een zichtbaar, echt Chrome-profiel (net als de woningenscanner) en
  een conservatieve, gedoseerde navigatiestrategie, omdat fundainbusiness.nl
  in het onderzoek merkbaar strenger bleek dan de residentiele site.
- "Bedrijfsruimte" (gebruikersterm) wordt technisch gemapt naar Funda's eigen
  objecttype-slug "bedrijfshal" (zie OBJECTTYPE_SLUGS hieronder).
- Een object dat zowel te koop als te huur staat, wordt als EEN uniek object
  behandeld (gededupliceerd op Funda's eigen object-ID), met zowel
  koopprijs als huurprijs op dezelfde rij indien aanwezig.

Gebruik:
    py scanner/funda_business_scanner_v1.py --plaatsen Uden
"""

import argparse
import csv
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from _stopcontrole import ScanGestopt, controleer_stop, STOP_EXITCODE

# Gebruikersterm -> Funda's eigen objecttype-slug in de URL.
OBJECTTYPE_SLUGS = {
    "Kantoor": "kantoor",
    "Bedrijfsruimte": "bedrijfshal",
}

# Bestandsnaam van het coöperatieve stopvlag-bestand, gezocht in --output-map.
# app.py (of een gebruiker vanaf de commandolijn) maakt dit bestand aan om een
# lopende scan netjes te laten afbreken; de scanner controleert het op
# meerdere veilige punten (voor elke navigatie, in de paginalus, tijdens een
# menscontrole-wachtperiode) en ruimt zelf Playwright/Chrome op vóór het stopt.
# ScanGestopt/controleer_stop/STOP_EXITCODE komen uit _stopcontrole.py, samen
# met de woningenscanner (zie DEEL B) - alleen de bestandsnaam blijft hier
# lokaal, zodat een reeds werkende constante niet hoeft te veranderen.
STOP_FLAG_NAAM = "_business_scan_stop.flag"

# Maximale wachttijd op een menscontrole/captcha voordat de scan alsnog wordt
# afgebroken (begrensde timeout, geen oneindig wachten - zie pause()).
MENSCONTROLE_MAX_WACHT_S = 1800

FIELDS = [
    "Peildatum", "Plaats", "Gezocht_categorie", "Objecttype", "Status",
    "Adres", "Koopprijs", "Koopprijs_conditie", "Huurprijs", "Huurprijs_eenheid",
    "Oppervlakte_m2", "Oppervlakte_extra_m2",
    "Berekende_huur_per_jaar", "Berekende_huur_per_maand",
    "Makelaar", "Funda_object_id", "Funda_detail_URL",
    "Bron", "Waarschuwing",
]


def say(x=""):
    print(x, flush=True)


def cli():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--plaatsen", nargs="+", default=["Uden"],
        help="Een of meer plaatsen, bv. --plaatsen Uden",
    )
    p.add_argument(
        "--objecttypes", nargs="+", default=list(OBJECTTYPE_SLUGS.keys()),
        choices=list(OBJECTTYPE_SLUGS.keys()),
        help="Gebruikerstermen; worden intern gemapt naar Funda-slugs.",
    )
    p.add_argument(
        "--max-pages", type=int, default=10,
        help="Veiligheidsmaximum per categorie/plaats; stopt eerder zodra er geen nieuwe objecten meer zijn.",
    )
    p.add_argument(
        "--delay", type=float, default=2.5,
        help="Extra wachttijd (s) tussen paginanavigaties; bewust conservatiever dan de woningenscanner.",
    )
    p.add_argument("--output-map", default="output/bedrijfsmatig")
    return p.parse_args()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def chrome_profile():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "FundaMakelaarsmonitor" / "BusinessProfile"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def funda_area_slug(place):
    s = unicodedata.normalize("NFKD", place).encode("ascii", "ignore").decode("ascii")
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def actual_place_from_url(url):
    """
    Haal de objectplaats uit een fundainbusiness-detail-URL:
    .../kantoor/uden/object-123-adres/ -> uden
    """
    try:
        path = urlparse(url).path.strip("/")
        parts = [p for p in path.split("/") if p]
        for i, part in enumerate(parts):
            if part in OBJECTTYPE_SLUGS.values() and i + 1 < len(parts):
                return parts[i + 1].strip().lower()
    except Exception:
        pass
    return ""


def normalized_place(place):
    return funda_area_slug(place)


def needs_human(page):
    try:
        t = clean(page.locator("body").inner_text(timeout=3000)).lower()
    except Exception:
        return False
    return any(x in t for x in [
        "ik ben geen robot", "captcha", "verify you are human",
        "controleer of je een mens bent", "bevestig dat je geen robot bent",
        "moeten we soms verifi",
    ])


def pause(page, reason, stop_flag_pad):
    """Wacht op menscontrole/captcha ZONDER blokkerende input()/ENTER: dit
    peilt elke 2s of (a) een stop is aangevraagd of (b) de controle in het
    zichtbare Chrome-venster al is opgelost (needs_human() weer False),
    en gaat dan vanzelf verder - 'Wacht op handmatige Funda-verificatie',
    geen actie nodig behalve het oplossen in Chrome zelf. Begrensd door
    MENSCONTROLE_MAX_WACHT_S; blijft daarnaast op elk moment onderbreekbaar
    via het stopvlag-bestand (ook tijdens deze wachtperiode)."""
    say("\n" + "=" * 72)
    say("FUNDA IN BUSINESS HEEFT AANDACHT NODIG")
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


def safe_goto(page, url, stop_flag_pad, wait_ms=1800):
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


def accept_cookies(page):
    try:
        knop = page.get_by_text("Alles accepteren", exact=False).first
        if knop.count():
            knop.click(timeout=3000)
            page.wait_for_timeout(800)
    except Exception:
        pass


def resultaten_gereed(page, stop_flag_pad, timeout_ms=15000):
    """Conservatieve, automatische gereedheidscheck (zie ook de aanpassing in
    de woningenscanner). Bij een timeout wordt eerst gecontroleerd of dit een
    menscontrole is (dan pauzeren) voordat dit als "geen resultaten" wordt
    behandeld - anders verdwijnt een botwal onterecht stilletjes."""
    controleer_stop(stop_flag_pad)
    try:
        page.locator("[data-search-result-listing]").first.wait_for(
            state="attached", timeout=timeout_ms
        )
    except PlaywrightTimeoutError:
        if needs_human(page):
            pause(page, "Mens-/captchacontrole gedetecteerd (timeout bij wachten op resultaten).", stop_flag_pad)
            return resultaten_gereed(page, stop_flag_pad, timeout_ms)
        return False

    if needs_human(page):
        pause(page, "Mens-/captchacontrole gedetecteerd vóór het uitlezen.", stop_flag_pad)

    return True


def categorie_url(objecttype_slug, plaats_slug, pagina):
    basis = f"https://www.fundainbusiness.nl/{objecttype_slug}/{plaats_slug}/"
    if pagina > 1:
        return f"{basis}p{pagina}/"
    return basis


def vind_volgende_pagina_url(page):
    """Zoekt de ECHTE 'volgende pagina'-link in de DOM (rel="next"), i.p.v.
    zelf een pagina-URL te raden. Retourneert de absolute URL, of None als er
    geen volgende pagina meer is.

    Dit is het betrouwbare stopsignaal, bevestigd via live DOM-onderzoek: op
    de laatste echte pagina ontbreekt a[rel="next"] volledig. Een geraden
    pagina-URL vóórbij het einde (het oude gedrag) geeft bij Funda soms een
    stille herhaling van pagina 1 terug en soms een trage 'geen
    resultaten'-timeout - beide zorgden voor een onnodig, verwarrend traag
    extra paginabezoek dat aanvoelde als vastlopen."""
    try:
        link = page.locator("a[rel='next']").first
        if link.count() == 0:
            return None
        href = link.get_attribute("href", timeout=2000)
        if not href:
            return None
        return urljoin(page.url, href)
    except Exception:
        return None


def haal_kaarten(page):
    """Eén Playwright-locator per resultaatkaart. [data-search-result-listing]
    is een semantisch, doelbewust attribuut van Funda zelf dat zowel gewone
    als "Blikvanger"-promokaarten dekt (bevestigd via live DOM-inspectie:
    research/verken_kaart_kenmerken.py) - veel robuuster dan het tellen of
    positioneel interpreteren van <a data-search-result-item-anchor>-links,
    waarvan het aantal per kaart varieert (afbeeldingen, promotekst, titel,
    subtitel delen soms allemaal dezelfde id)."""
    return page.locator("[data-search-result-listing]")


def lees_kaart(kaart):
    """Leest één resultaatkaart uit via specifieke, semantische
    data-test-attributen in plaats van fragiele positie-aannames over welke
    anchor de titel/subtitel zou zijn."""
    try:
        object_id = kaart.locator("a[data-search-result-item-anchor]").first.get_attribute(
            "data-search-result-item-anchor", timeout=2000
        )
    except Exception:
        object_id = None

    try:
        href = kaart.locator("a[data-search-result-item-anchor]").first.get_attribute(
            "href", timeout=2000
        )
    except Exception:
        href = None

    try:
        adres_tekst = clean(
            kaart.locator("[data-test-search-result-header-title]").inner_text(timeout=2000)
        )
    except Exception:
        adres_tekst = ""

    try:
        objecttype_tekst = clean(
            kaart.locator("[data-test-search-result-header-subtitle]").inner_text(timeout=2000)
        )
    except Exception:
        objecttype_tekst = ""

    try:
        kaart_tekst = kaart.inner_text(timeout=2000)
    except Exception:
        kaart_tekst = ""

    # Promokaarten ("Blikvanger") bevatten een marketing-blurb die soms
    # zelf een m²-getal noemt (bv. "...van 405 m² op de begane grond...").
    # Die tekst verwijderen we vóór het parsen, anders kan dat getal
    # ten onrechte als (extra) oppervlakte worden gelezen.
    try:
        promolabel = kaart.locator(".search-promolabel-new").first
        if promolabel.count():
            promo_tekst = promolabel.inner_text(timeout=1000)
            if promo_tekst:
                kaart_tekst = kaart_tekst.replace(promo_tekst, "", 1)
    except Exception:
        pass

    return object_id, href, adres_tekst, objecttype_tekst, kaart_tekst


def _naar_bedrag(tekst):
    return int(float(tekst.replace(".", "").replace(",", ".")))


def prijs_op_aanvraag(txt):
    """Alleen 'op aanvraag' (bv. "Prijs op aanvraag", "Huurprijs op
    aanvraag") - bewust NIET "n.o.t.k.", want dat is specifiek een
    koopprijs-sentinel en wordt apart afgehandeld in parse_rij()."""
    return bool(re.search(r"op aanvraag", txt, re.I))


def parse_koopprijs(txt):
    """Retourneert (bedrag, conditie). Conditie bv. 'k.k.' (kosten koper) of
    'v.o.n.' (vrij op naam) - bewust apart bewaard, niet in het bedrag verwerkt."""
    m = re.search(r"€\s*([\d.,]+)\s*(k\.k\.|v\.o\.n\.)", txt, re.I)
    if m:
        try:
            return _naar_bedrag(m.group(1)), m.group(2).lower()
        except Exception:
            return None, ""
    return None, ""


def parse_huurprijs(txt):
    """Retourneert (bedrag, eenheid). Het bedrag is ALTIJD de brontarief-waarde
    zoals Funda die toont (bv. 89 bij "€ 89 /m²/jaar") - nooit omgerekend naar
    een totaalbedrag. Ondersteunt minimaal: per_m2_per_jaar, per_jaar,
    per_maand, en 'op_aanvraag' (geen bedrag bekend/getoond)."""
    m = re.search(r"€\s*([\d.,]+)\s*/\s*m[²2]\s*/\s*jaar", txt, re.I)
    if m:
        try:
            return _naar_bedrag(m.group(1)), "per_m2_per_jaar"
        except Exception:
            pass

    m = re.search(r"€\s*([\d.,]+)\s*(?:/mnd|/maand|per maand)", txt, re.I)
    if m:
        try:
            return _naar_bedrag(m.group(1)), "per_maand"
        except Exception:
            pass

    m = re.search(r"€\s*([\d.,]+)\s*(?:/jaar|per jaar)\b", txt, re.I)
    if m:
        try:
            return _naar_bedrag(m.group(1)), "per_jaar"
        except Exception:
            pass

    if prijs_op_aanvraag(txt):
        return None, "op_aanvraag"

    return None, ""


def bereken_afgeleide_huur(huurprijs, huur_eenheid, oppervlakte):
    """Afgeleide analysevelden, uitsluitend berekend als de brongegevens dat
    betrouwbaar toelaten (tarief per m²/jaar + bekende oppervlakte). Vervangt
    nooit de oorspronkelijke Funda-prijs - alleen extra, apart bewaarde
    kolommen."""
    if huur_eenheid != "per_m2_per_jaar" or huurprijs is None or not oppervlakte:
        return None, None
    per_jaar = huurprijs * oppervlakte
    per_maand = round(per_jaar / 12)
    return per_jaar, per_maand


def parse_oppervlaktes(txt):
    """Retourneert (oppervlakte, extra_oppervlakte). 'Extra oppervlakte' is
    uitsluitend de echte dubbele notatie "X m² / Y m²" (bv. verhuurbaar/
    totaal). Een "Units vanaf X m²"-regel (minimale deeloppervlakte per unit)
    wordt bewust NIET als extra oppervlakte gelezen - dat is een ander
    gegeven en zou anders ten onrechte in Oppervlakte_extra_m2 belanden."""
    dual = re.search(r"([\d.,]+)\s*m[²2]\s*/\s*([\d.,]+)\s*m[²2]", txt)
    if dual:
        try:
            return _naar_bedrag(dual.group(1)), _naar_bedrag(dual.group(2))
        except Exception:
            pass

    for regel in txt.splitlines():
        regel_clean = clean(regel)
        if not regel_clean or "units vanaf" in regel_clean.lower():
            continue
        m = re.search(r"([\d.,]+)\s*m[²2]", regel_clean)
        if m:
            try:
                return _naar_bedrag(m.group(1)), None
            except Exception:
                continue

    return None, None


def parse_makelaar(txt):
    regels = [clean(r) for r in txt.splitlines() if clean(r)]
    if not regels:
        return ""
    laatste = regels[-1]
    if "€" in laatste or re.search(r"m[²2]", laatste) or re.fullmatch(r"[\d.,]+", laatste):
        return ""
    return laatste


def parse_rij(object_id, href, adres_tekst, objecttype_tekst, row_txt, plaats_arg, gezocht_categorie):
    if "," in adres_tekst:
        adres, plaats_ruw = adres_tekst.rsplit(",", 1)
        adres = clean(adres)
        plaats_ruw = clean(plaats_ruw)
    else:
        adres, plaats_ruw = adres_tekst, ""

    koopprijs, koop_conditie = parse_koopprijs(row_txt)
    huurprijs, huur_eenheid = parse_huurprijs(row_txt)
    oppervlakte, oppervlakte_extra = parse_oppervlaktes(row_txt)
    makelaar = parse_makelaar(row_txt)
    huur_per_jaar, huur_per_maand = bereken_afgeleide_huur(huurprijs, huur_eenheid, oppervlakte)

    warnings = []
    if not adres:
        warnings.append("adres ontbreekt")
    if koopprijs is None and huurprijs is None:
        if huur_eenheid == "op_aanvraag":
            pass  # bekende, expliciete toestand - geen parseerprobleem
        elif "n.o.t.k" in row_txt.lower():
            warnings.append("prijs n.o.t.k.")
        else:
            warnings.append("geen koop- of huurprijs herkend")
    if not oppervlakte:
        warnings.append("oppervlakte ontbreekt")
    if not makelaar:
        warnings.append("makelaar ontbreekt")
    if not row_txt:
        warnings.append("kaarttekst niet gevonden")

    return {
        "Peildatum": datetime.now().strftime("%Y-%m-%d"),
        "Plaats": plaats_ruw or plaats_arg,
        "Gezocht_categorie": gezocht_categorie,
        "Objecttype": objecttype_tekst,
        "Status": "Beschikbaar",  # standaardlijst toont uitsluitend actief aangeboden objecten (zie onderzoek)
        "Adres": adres,
        "Koopprijs": koopprijs if koopprijs is not None else "",
        "Koopprijs_conditie": koop_conditie,
        "Huurprijs": huurprijs if huurprijs is not None else "",
        "Huurprijs_eenheid": huur_eenheid,
        "Oppervlakte_m2": oppervlakte if oppervlakte is not None else "",
        "Oppervlakte_extra_m2": oppervlakte_extra if oppervlakte_extra is not None else "",
        "Berekende_huur_per_jaar": huur_per_jaar if huur_per_jaar is not None else "",
        "Berekende_huur_per_maand": huur_per_maand if huur_per_maand is not None else "",
        "Makelaar": makelaar,
        "Funda_object_id": object_id,
        "Funda_detail_URL": href,
        "Bron": "fundainbusiness-lijst",
        "Waarschuwing": "; ".join(warnings),
    }


def merge_duplicate(bestaand, nieuw):
    """Zelfde object gevonden onder meerdere categorieen: combineer velden,
    behoud beide gezochte categorieen, geef voorkeur aan de rij met de meeste
    ingevulde velden voor de overige kenmerken."""
    categorieen = []
    for waarde in (bestaand.get("Gezocht_categorie", ""), nieuw.get("Gezocht_categorie", "")):
        for c in str(waarde).split(";"):
            c = clean(c)
            if c and c not in categorieen:
                categorieen.append(c)

    def score(r):
        keys = ["Adres", "Koopprijs", "Huurprijs", "Oppervlakte_m2", "Makelaar"]
        return sum(1 for k in keys if clean(str(r.get(k, ""))))

    beste = nieuw if score(nieuw) > score(bestaand) else bestaand
    beste["Gezocht_categorie"] = "; ".join(categorieen)

    # Koop- en huurprijs (met bijbehorende velden) onafhankelijk samenvoegen
    # (dual-listed objecten: koop onder de ene categorie, huur onder de andere).
    prijsgroepen = [
        ("Koopprijs", ["Koopprijs_conditie"]),
        ("Huurprijs", ["Huurprijs_eenheid", "Berekende_huur_per_jaar", "Berekende_huur_per_maand"]),
    ]
    for prijsveld, bijbehorende_velden in prijsgroepen:
        if not clean(str(beste.get(prijsveld, ""))):
            for bron in (nieuw, bestaand):
                if clean(str(bron.get(prijsveld, ""))):
                    beste[prijsveld] = bron[prijsveld]
                    for veld in bijbehorende_velden:
                        beste[veld] = bron.get(veld, "")
                    break
    return beste


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
    final_all = outdir / f"funda_business_{label}_{stamp}_alles.csv"
    checkpoint = outdir / f"funda_business_{label}_{stamp}_checkpoint.csv"

    say("=" * 72)
    say("FUNDA IN BUSINESS SCANNER v1 (Fase 1 - lijstpagina's, CSV-only)")
    say(f"Plaatsen: {', '.join(places)}")
    say(f"Categorieen: {', '.join(opt.objecttypes)} (technisch: "
        f"{', '.join(OBJECTTYPE_SLUGS[c] for c in opt.objecttypes)})")
    say("Conservatieve aanpak: zichtbaar Chrome-profiel, gedoseerde navigatie, "
        f"delay={opt.delay}s, max {opt.max_pages} pagina's per categorie/plaats.")
    say("=" * 72)

    combined_by_id = {}
    samenvatting = []
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
            for plaats in places:
                plaats_slug = funda_area_slug(plaats)

                for categorie in opt.objecttypes:
                    controleer_stop(stop_flag_pad)
                    slug = OBJECTTYPE_SLUGS[categorie]
                    say(f"\n--- {plaats} / {categorie} ({slug}) ---")

                    gevonden_hier = {}
                    bezochte_urls = set()
                    volgende_url = categorie_url(slug, plaats_slug, 1)
                    nr = 0

                    while volgende_url and nr < opt.max_pages:
                        controleer_stop(stop_flag_pad)
                        nr += 1

                        if volgende_url in bezochte_urls:
                            say(f"Pagina {nr}: URL al eerder bezocht ({volgende_url}) -> einde {categorie} in {plaats}.")
                            break
                        bezochte_urls.add(volgende_url)

                        huidige_url = volgende_url
                        safe_goto(page, huidige_url, stop_flag_pad, 1800)
                        if nr == 1:
                            accept_cookies(page)
                            page.wait_for_timeout(1000)

                        if not resultaten_gereed(page, stop_flag_pad):
                            say(f"Pagina {nr}: geen resultaten gevonden binnen de tijd -> einde {categorie} in {plaats}.")
                            break

                        kaarten = haal_kaarten(page)
                        aantal_op_pagina = kaarten.count()

                        if not aantal_op_pagina:
                            say(f"Pagina {nr}: geen objecten -> einde {categorie} in {plaats}.")
                            break

                        huidige_ids = set()
                        nieuw_op_pagina = 0
                        for idx in range(aantal_op_pagina):
                            oid, href, adres_tekst, objecttype_tekst, row_txt = lees_kaart(kaarten.nth(idx))
                            if not oid:
                                continue
                            huidige_ids.add(oid)
                            if oid in gevonden_hier:
                                continue
                            rij = parse_rij(oid, href, adres_tekst, objecttype_tekst, row_txt, plaats, categorie)
                            gevonden_hier[oid] = rij
                            nieuw_op_pagina += 1

                        duplicaten_op_pagina = len(huidige_ids) - nieuw_op_pagina
                        say(
                            f"Pagina {nr}: {aantal_op_pagina} kaarten | {len(huidige_ids)} unieke ID's | "
                            f"{nieuw_op_pagina} nieuw | {duplicaten_op_pagina} duplicaat(en) | "
                            f"totaal {categorie}: {len(gevonden_hier)}"
                        )

                        write_csv(checkpoint, list(combined_by_id.values()) + list(gevonden_hier.values()))

                        if nieuw_op_pagina == 0:
                            # Robuuster dan de oude "zelfde als vórige pagina"-check: dit
                            # vangt óók een pagina die (bv. bij een geraden/ongeldige
                            # pagina-URL) toevallig een EERDERE pagina herhaalt in plaats
                            # van alleen de allerlaatste - voorkomt een extra onnodig
                            # paginabezoek voordat wordt gestopt.
                            say(f"Pagina {nr}: geen nieuwe objecten t.o.v. eerder gezien -> einde {categorie} in {plaats}.")
                            break

                        if opt.delay:
                            time.sleep(opt.delay)

                        controleer_stop(stop_flag_pad)
                        volgende_url = vind_volgende_pagina_url(page)
                        if not volgende_url:
                            say(f"Pagina {nr}: geen 'volgende pagina'-link meer gevonden -> einde {categorie} in {plaats}.")

                    for oid, rij in gevonden_hier.items():
                        if oid in combined_by_id:
                            combined_by_id[oid] = merge_duplicate(combined_by_id[oid], rij)
                        else:
                            combined_by_id[oid] = rij

                    samenvatting.append((plaats, categorie, len(gevonden_hier)))
                    write_csv(checkpoint, list(combined_by_id.values()))
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

    combined_rows = list(combined_by_id.values())
    combined_rows.sort(key=lambda r: (normalized_place(r.get("Plaats", "")), clean(r.get("Adres", "")).casefold()))
    write_csv(final_all, combined_rows)

    dual_listed = sum(
        1 for r in combined_rows
        if clean(str(r.get("Koopprijs", ""))) and clean(str(r.get("Huurprijs", "")))
    )

    say("\n" + "=" * 72)
    say("FUNDA IN BUSINESS SCANNER v1 KLAAR")
    say("")
    say("Per plaats/categorie:")
    for plaats, categorie, aantal in samenvatting:
        say(f"  {plaats} / {categorie}: {aantal} objecten")
    say("")
    say(f"Totaal unieke objecten (na dedupe op object-ID): {len(combined_rows)}")
    say(f"Waarvan zowel koop- als huurprijs (dual-listed): {dual_listed}")
    say("")
    say("Bestand:")
    say(f"  Alles:      {final_all}")
    say(f"  Checkpoint: {checkpoint}")
    say("\nDit bestand is nog NIET gekoppeld aan app.py of de productiedatabase.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nScan afgebroken (Ctrl+C). Laatste checkpoint blijft behouden.")
        raise SystemExit(STOP_EXITCODE)
