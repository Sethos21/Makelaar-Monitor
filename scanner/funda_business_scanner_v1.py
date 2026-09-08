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
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Gebruikersterm -> Funda's eigen objecttype-slug in de URL.
OBJECTTYPE_SLUGS = {
    "Kantoor": "kantoor",
    "Bedrijfsruimte": "bedrijfshal",
}

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


def pause(url, reason):
    say("\n" + "=" * 72)
    say("FUNDA IN BUSINESS HEEFT AANDACHT NODIG")
    say(reason)
    say(f"Pagina: {url}")
    try:
        input("Controleer Chrome, los zo nodig de controle op en druk ENTER... ")
    except EOFError:
        raise SystemExit(
            "Geen interactieve console beschikbaar om ENTER te bevestigen "
            "(stdin gesloten). De scan is gestopt bij een menscontrole die "
            "handmatige actie vereist - start de scanner opnieuw vanuit een "
            "console waarin je zelf kunt reageren."
        )


def safe_goto(page, url, wait_ms=1800):
    while True:
        try:
            page.goto(url, wait_until="commit", timeout=30000)
            page.wait_for_timeout(wait_ms)
            if needs_human(page):
                pause(url, "Robot-/menscontrole zichtbaar.")
                continue
            return
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            pause(url, f"Navigatieprobleem: {exc}")


def accept_cookies(page):
    try:
        knop = page.get_by_text("Alles accepteren", exact=False).first
        if knop.count():
            knop.click(timeout=3000)
            page.wait_for_timeout(800)
    except Exception:
        pass


def resultaten_gereed(page, timeout_ms=15000):
    """Conservatieve, automatische gereedheidscheck (zie ook de aanpassing in
    de woningenscanner). Bij een timeout wordt eerst gecontroleerd of dit een
    menscontrole is (dan pauzeren) voordat dit als "geen resultaten" wordt
    behandeld - anders verdwijnt een botwal onterecht stilletjes."""
    try:
        page.locator("[data-search-result-listing]").first.wait_for(
            state="attached", timeout=timeout_ms
        )
    except PlaywrightTimeoutError:
        if needs_human(page):
            pause(page.url, "Mens-/captchacontrole gedetecteerd (timeout bij wachten op resultaten).")
            return resultaten_gereed(page, timeout_ms)
        return False

    if needs_human(page):
        pause(page.url, "Mens-/captchacontrole gedetecteerd vóór het uitlezen.")

    return True


def categorie_url(objecttype_slug, plaats_slug, pagina):
    basis = f"https://www.fundainbusiness.nl/{objecttype_slug}/{plaats_slug}/"
    if pagina > 1:
        return f"{basis}p{pagina}/"
    return basis


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

        for plaats in places:
            plaats_slug = funda_area_slug(plaats)

            for categorie in opt.objecttypes:
                slug = OBJECTTYPE_SLUGS[categorie]
                say(f"\n--- {plaats} / {categorie} ({slug}) ---")

                gevonden_hier = {}
                vorige_ids = None

                for nr in range(1, opt.max_pages + 1):
                    url = categorie_url(slug, plaats_slug, nr)
                    safe_goto(page, url, 1800)
                    if nr == 1:
                        accept_cookies(page)
                        page.wait_for_timeout(1000)

                    if not resultaten_gereed(page):
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

                    if vorige_ids is not None and huidige_ids == vorige_ids:
                        say(f"Pagina {nr}: zelfde objecten als vorige pagina -> einde {categorie} in {plaats}.")
                        break

                    say(f"Pagina {nr}: {len(huidige_ids)} objecten op pagina | {nieuw_op_pagina} nieuw | totaal {categorie}: {len(gevonden_hier)}")

                    write_csv(checkpoint, list(combined_by_id.values()) + list(gevonden_hier.values()))

                    vorige_ids = huidige_ids
                    if opt.delay:
                        time.sleep(opt.delay)

                for oid, rij in gevonden_hier.items():
                    if oid in combined_by_id:
                        combined_by_id[oid] = merge_duplicate(combined_by_id[oid], rij)
                    else:
                        combined_by_id[oid] = rij

                samenvatting.append((plaats, categorie, len(gevonden_hier)))
                write_csv(checkpoint, list(combined_by_id.values()))

        context.close()

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
        say("\nScan afgebroken. Laatste checkpoint blijft behouden.")
