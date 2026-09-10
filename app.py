"""Makelaar Monitor - lokale webapp (fundament).

Start vanuit de projectroot met:  py app.py
Bereikbaar via:                   http://127.0.0.1:5000

Leest uitsluitend read-only uit de bestaande SQLite-historie in data/.
Voert geen scans uit en wijzigt de database niet.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "makelaarsmonitor_historie.sqlite"
OUTPUT_DIR = BASE_DIR / "output"
SCANNER_PAD = BASE_DIR / "scanner" / "makelaarsmonitor_v41.py"
HISTORIE_PAD = BASE_DIR / "historie" / "makelaarsmonitor_historie_v10.py"

# Fase 3: read-only weergave van de losstaande Business-database. Deze wordt
# hier uitsluitend GELEZEN - geen schemawijzigingen, geen imports, geen scans.
BUSINESS_DB_PATH = BASE_DIR / "data" / "funda_business_historie.sqlite"

# Fase 4: paden voor de Business-scan-keten. De scanner/historie-tool zelf
# blijven ongewijzigd - dit zijn uitsluitend de paden die de webapp nodig
# heeft om ze als subprocess aan te roepen.
BUSINESS_SCANNER_PAD = BASE_DIR / "scanner" / "funda_business_scanner_v1.py"
BUSINESS_HISTORIE_PAD = BASE_DIR / "historie" / "funda_business_historie_v1.py"
BUSINESS_OUTPUT_DIR = BASE_DIR / "output" / "bedrijfsmatig"
BUSINESS_HISTORIE_OUTPUT_DIR = BUSINESS_OUTPUT_DIR / "historie"

# Coöperatief stopvlag-bestand voor een lopende Business-scan. Bestandsnaam
# moet EXACT overeenkomen met STOP_FLAG_NAAM in
# scanner/funda_business_scanner_v1.py - de scanner zelf controleert dit pad
# (afgeleid van --output-map) en sluit Playwright/Chrome netjes af zodra het
# verschijnt.
BUSINESS_SCAN_STOP_FLAG = BUSINESS_OUTPUT_DIR / "_business_scan_stop.flag"

# Zelfde mechanisme voor Woningen (DEEL B) - moet EXACT overeenkomen met
# STOP_FLAG_NAAM in scanner/makelaarsmonitor_v41.py.
SCAN_STOP_FLAG = OUTPUT_DIR / "_woningen_scan_stop.flag"

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")


@app.errorhandler(Exception)
def _onverwachte_fout(exc):
    """DEEL J: de gebruiker ziet nooit meer rechtstreeks de interactieve
    Werkzeug-debuggerpagina bij een onverwachte fout - alleen een nette
    melding. De volledige traceback blijft wél behouden: hij wordt altijd
    naar de serverconsole/log gelogd (debugbaarheid tijdens ontwikkeling
    blijft intact), en tijdens tests (app.testing=True, zie tests/) laat
    Flask dit handler-pad bewust ongemoeid - daar blijven exceptions gewoon
    zichtbaar/propagerend, zoals gevraagd."""
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        return exc

    app.logger.error("Onverwachte fout tijdens request %s %s", request.method, request.path, exc_info=exc)
    try:
        return render_template("fout.html", actieve_sectie=None), 500
    except Exception:
        return (
            "Deze pagina kon niet worden opgebouwd. De fout is vastgelegd in de serverconsole.",
            500,
        )

# Eenvoudige, in-memory scanstatus. SCAN_RUNNING_LOCK bewaakt dat er maximaal
# één scan tegelijk draait; STATE_LOCK beschermt korte lees/schrijfacties op
# SCAN_STATE tussen de Flask-requestthread en de achtergrondthread van de scan.
SCAN_RUNNING_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
SCAN_STATE: dict = {"status": "idle"}

# Fase 4: eigen statusobject voor de Business-scan, maar BEWUST DEZELFDE
# SCAN_RUNNING_LOCK hierboven - dit maakt er één gedeelde globale lock van
# over beide scantypes, zodat een woningen- en een Business-scan nooit
# tegelijk kunnen draaien (beide openen een zichtbaar Chrome-venster en
# leunen op dezelfde interactieve console voor ENTER/menscontrole; tegelijk
# draaien zou dat door elkaar halen). Zie docs/CLAUDE_STATUS.md voor de
# motivatie. BUSINESS_STATE_LOCK beschermt alleen BUSINESS_SCAN_STATE, net
# zoals STATE_LOCK dat voor SCAN_STATE doet - dit blijft dus wel een eigen,
# van woningen gescheiden statusobject.
BUSINESS_STATE_LOCK = threading.Lock()
BUSINESS_SCAN_STATE: dict = {"status": "idle"}

# Veilige referentie naar het ACTIEVE Business-scanner-subprocess (of None),
# beschermd door BUSINESS_STATE_LOCK. Nodig om een lopende scan betrouwbaar
# te kunnen afbreken - een losse boolean zou het externe subprocess niet
# stoppen.
BUSINESS_ACTIEVE_PROCES: subprocess.Popen | None = None

# Zelfde principe voor Woningen (DEEL B), beschermd door STATE_LOCK.
SCAN_ACTIEVE_PROCES: subprocess.Popen | None = None

REGIOS_STANDAARD = [
    "Uden",
    "Volkel",
    "Nistelrode",
    "Zeeland",
    "Odiliapeel",
    "Vorstenbosch",
    "Veghel",
    "Heesch",
    "Heeswijk-Dinther",
    "Boekel",
    "Schaijk",
    "Vinkel",
]
REGIO_STANDAARD = ["Uden"]

# DEEL D: kleine, lokale geografische referentielaag (plaats -> gemeente ->
# provincie + inwonertallen), volledig los van de scannerdata. GEEN live
# externe call per pagina - dit is een statische, handmatig samengestelde
# tabel op basis van officiële/openbare bronnen (Wikipedia, citerend CBS-
# cijfers). "Woonplaats" hier = de Funda-zoekplaats zoals in REGIOS_STANDAARD;
# inwoners_plaats is het inwoneraantal van die KERN (niet de gemeente).
# Peildata zijn per bron/rij vastgelegd, geen enkel cijfer is verzonnen -
# ontbrekende plaats-cijfers (bv. Boekel, waarvoor geen aparte kerncijfers
# gevonden zijn t.o.v. de gemeente) blijven bewust None.
#
# Bronnen (geraadpleegd 2026-09-09, zie docs/CLAUDE_STATUS.md DEEL M/4):
# - nl.wikipedia.org (Bernheze, Maashorst, Schaijk, Veghel (plaats),
#   Vinkel (Nederland), Boekel (gemeente), Land van Cuijk (gemeente))
# - gemeentetotalen citeren CBS per 1 januari 2026 waar vermeld.
GEO_REFERENTIE: dict[str, dict] = {
    "Uden":              {"gemeente": "Maashorst",      "provincie": "Noord-Brabant", "inwoners_plaats": 37325, "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 60060,  "peildatum_gemeente": "2026-01-01"},
    "Volkel":            {"gemeente": "Maashorst",      "provincie": "Noord-Brabant", "inwoners_plaats": 3545,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 60060,  "peildatum_gemeente": "2026-01-01"},
    "Zeeland":           {"gemeente": "Maashorst",      "provincie": "Noord-Brabant", "inwoners_plaats": 6855,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 60060,  "peildatum_gemeente": "2026-01-01"},
    "Odiliapeel":        {"gemeente": "Maashorst",      "provincie": "Noord-Brabant", "inwoners_plaats": 2075,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 60060,  "peildatum_gemeente": "2026-01-01"},
    "Schaijk":           {"gemeente": "Maashorst",      "provincie": "Noord-Brabant", "inwoners_plaats": 7360,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 60060,  "peildatum_gemeente": "2026-01-01"},
    "Nistelrode":        {"gemeente": "Bernheze",       "provincie": "Noord-Brabant", "inwoners_plaats": 6725,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 32943,  "peildatum_gemeente": "2026-01-01"},
    "Heesch":            {"gemeente": "Bernheze",       "provincie": "Noord-Brabant", "inwoners_plaats": 14165, "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 32943,  "peildatum_gemeente": "2026-01-01"},
    "Heeswijk-Dinther":  {"gemeente": "Bernheze",       "provincie": "Noord-Brabant", "inwoners_plaats": 8895,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 32943,  "peildatum_gemeente": "2026-01-01"},
    "Vorstenbosch":      {"gemeente": "Bernheze",       "provincie": "Noord-Brabant", "inwoners_plaats": 1455,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 32943,  "peildatum_gemeente": "2026-01-01"},
    "Veghel":            {"gemeente": "Meierijstad",    "provincie": "Noord-Brabant", "inwoners_plaats": 28900, "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 85236,  "peildatum_gemeente": "2026-01-01"},
    "Boekel":            {"gemeente": "Boekel",         "provincie": "Noord-Brabant", "inwoners_plaats": None,  "peildatum_plaats": None,         "inwoners_gemeente": 11685,  "peildatum_gemeente": "2026-01-01"},
    "Vinkel":            {"gemeente": "'s-Hertogenbosch","provincie": "Noord-Brabant", "inwoners_plaats": 2795,  "peildatum_plaats": "2023-01-01", "inwoners_gemeente": 162272, "peildatum_gemeente": "2026-01-01"},
}


def geo_info(plaats: str) -> dict:
    return GEO_REFERENTIE.get((plaats or "").strip(), {})


STATUSSEN = ["Beschikbaar", "Onder bod", "Verkocht onder voorbehoud"]
STATUS_STANDAARD = ["Beschikbaar"]

BOUWCATEGORIEEN = ["Bestaande bouw", "Nieuwbouw", "Alles"]
BOUWCATEGORIE_STANDAARD = ["Bestaande bouw"]

# key -> (label, sorteertype). Sorteertype stuurt de client-side tabelsortering aan:
#   "bedrag" = bedrag met €-opmaak, "getal" = kaal getal, "tekst" = alfabetisch/chronologisch (ISO-datums).
KOLOMMEN = {
    "adres": ("Adres", "tekst"),
    "plaats": ("Plaats", "tekst"),
    "vraagprijs": ("Vraagprijs", "bedrag"),
    "makelaar": ("Makelaar", "tekst"),
    "status": ("Status", "tekst"),
    "woonoppervlakte": ("Woonoppervlakte", "getal"),
    "perceeloppervlakte": ("Perceeloppervlakte", "getal"),
    "slaapkamers": ("Slaapkamers", "getal"),
    "energielabel": ("Energielabel", "tekst"),
    "bouwcategorie": ("Bouwcategorie", "tekst"),
    "prijs_per_m2": ("Prijs per m²", "bedrag"),
    "eerste_waarneming": ("Eerste waarneming", "tekst"),
    "laatste_waarneming": ("Laatste waarneming", "tekst"),
    "dagen_in_monitor": ("Dagen in monitor", "getal"),
    "prijswijziging": ("Prijswijziging", "bedrag"),
    "funda_url": ("Funda URL", "tekst"),
}
KOLOMMEN_STANDAARD = [
    "adres",
    "plaats",
    "status",
    "bouwcategorie",
    "vraagprijs",
    "woonoppervlakte",
    "perceeloppervlakte",
    "slaapkamers",
    "energielabel",
    "prijs_per_m2",
    "makelaar",
    "eerste_waarneming",
    "laatste_waarneming",
    "dagen_in_monitor",
    "funda_url",
]

# --- Fase 3: Business (bedrijfsmatig vastgoed) ---
BUSINESS_REGIO_STANDAARD = ["Uden"]

BUSINESS_CATEGORIEEN = ["Kantoor", "Bedrijfsruimte"]
BUSINESS_CATEGORIE_STANDAARD = ["Kantoor", "Bedrijfsruimte"]

BUSINESS_TRANSACTIETYPES = ["Alles", "Huur", "Koop", "Koop + Huur"]
BUSINESS_TRANSACTIETYPE_STANDAARD = "Alles"

# Fallback als de Business-database nog niet bestaat; zodra die er wel is,
# worden de daadwerkelijk aanwezige statuswaarden gebruikt (zie
# haal_business_statussen()) - geen hardcoded verlies van toekomstige statussen.
BUSINESS_STATUS_FALLBACK = ["Beschikbaar"]
BUSINESS_STATUS_STANDAARD = ["Beschikbaar"]

BUSINESS_HUUREENHEID_LABELS = {
    "per_m2_per_jaar": "/m²/jaar",
    "per_jaar": "/jaar",
    "per_maand": "/mnd",
}


def get_readonly_connection() -> sqlite3.Connection:
    """Open de historie-database strikt read-only."""
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def formatteer_bedrag(bedrag: float) -> str:
    return f"€ {bedrag:,.0f}".replace(",", ".")


def formatteer_prijswijziging(verschil: float) -> str:
    bedrag = formatteer_bedrag(abs(verschil))
    if verschil > 0:
        return f"+ {bedrag}"
    if verschil < 0:
        return f"- {bedrag}"
    return bedrag


def formatteer_scanmoment(scanmoment: str | None) -> str:
    if not scanmoment:
        return "-"
    try:
        return datetime.fromisoformat(scanmoment).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return scanmoment


def parse_filters(args) -> tuple[list[str], list[str], list[str], list[str]]:
    plaatsen = args.getlist("plaats")
    statussen = args.getlist("status") or STATUS_STANDAARD
    bouwcategorieen = args.getlist("bouwcategorie") or BOUWCATEGORIE_STANDAARD
    kolommen = [k for k in args.getlist("kolom") if k in KOLOMMEN] or KOLOMMEN_STANDAARD
    return plaatsen, statussen, bouwcategorieen, kolommen


def haal_laatste_scan_id(con: sqlite3.Connection) -> int | None:
    """De meest recente scan, bepaald op scanmoment (met scan_id als tiebreaker)."""
    row = con.execute(
        "SELECT scan_id FROM scans ORDER BY scanmoment DESC, scan_id DESC LIMIT 1"
    ).fetchone()
    return row["scan_id"] if row else None


def haal_scan_info(con: sqlite3.Connection, scan_id: int) -> dict:
    row = con.execute(
        "SELECT scan_id, scanmoment, peildatum, gebied, aantal_objecten FROM scans WHERE scan_id = ?",
        (scan_id,),
    ).fetchone()
    return dict(row) if row else {}


def haal_snapshotrijen(
    con: sqlite3.Connection,
    scan_id: int,
    plaatsen: list[str],
    statussen: list[str],
    bouwcategorieen: list[str],
) -> list[dict]:
    """Lees de rijen van één scan (= één snapshotmoment), gefilterd."""
    query = "SELECT * FROM snapshots WHERE scan_id = ?"
    params: list = [scan_id]

    if plaatsen:
        placeholders = ",".join("?" * len(plaatsen))
        query += f" AND plaats IN ({placeholders})"
        params.extend(plaatsen)

    if statussen:
        placeholders = ",".join("?" * len(statussen))
        query += f" AND status IN ({placeholders})"
        params.extend(statussen)

    if bouwcategorieen and "Alles" not in bouwcategorieen:
        placeholders = ",".join("?" * len(bouwcategorieen))
        query += f" AND bouwcategorie IN ({placeholders})"
        params.extend(bouwcategorieen)

    query += " ORDER BY plaats, adres"

    return [dict(r) for r in con.execute(query, params).fetchall()]


def dedupliceer_op_funda_url(rijen: list[dict]) -> list[dict]:
    """Voorkomt dubbele objecten binnen één scan. funda_url is de unieke sleutel
    (in de database bovendien afgedwongen via de primaire sleutel (scan_id, funda_url)).
    Objecten zonder funda_url worden voorzichtigheidshalve op adres+plaats gededupliceerd."""
    gezien: set = set()
    resultaat = []
    for rij in rijen:
        identificatie = rij.get("funda_url") or (rij.get("adres"), rij.get("plaats"))
        if identificatie in gezien:
            continue
        gezien.add(identificatie)
        resultaat.append(rij)
    return resultaat


def haal_geschiedenis(
    con: sqlite3.Connection, funda_urls: list[str], tot_en_met_scan_id: int | None = None,
) -> dict[str, dict]:
    """Eerste/laatste waarneming (datum + vraagprijs) per funda_url. Standaard
    over alle scans heen (actuele analyse). Bij het bekijken van een
    HISTORISCHE scan moet `tot_en_met_scan_id` worden meegegeven (het scan_id
    van die historische scan) zodat latere/toekomstige scans nooit in de
    "laatste waarneming" van een historisch peilmoment lekken - anders zou
    een historische analyse per ongeluk actuele informatie tonen (DEEL C2)."""
    urls = sorted({u for u in funda_urls if u})
    if not urls:
        return {}

    placeholders = ",".join("?" * len(urls))
    params: list = list(urls)
    query = (
        f"SELECT funda_url, peildatum, vraagprijs FROM snapshots "
        f"WHERE funda_url IN ({placeholders})"
    )
    if tot_en_met_scan_id is not None:
        query += " AND scan_id <= ?"
        params.append(tot_en_met_scan_id)
    query += " ORDER BY funda_url, peildatum, scan_id"

    geschiedenis: dict[str, dict] = {}
    for rij in con.execute(query, params).fetchall():
        url = rij["funda_url"]
        entry = geschiedenis.setdefault(
            url,
            {
                "eerste_datum": rij["peildatum"],
                "eerste_prijs": rij["vraagprijs"],
                "laatste_datum": rij["peildatum"],
                "laatste_prijs": rij["vraagprijs"],
            },
        )
        entry["laatste_datum"] = rij["peildatum"]
        entry["laatste_prijs"] = rij["vraagprijs"]

    return geschiedenis


def verrijk_rij(rij: dict, geschiedenis: dict[str, dict]) -> dict:
    """Voegt afgeleide velden toe en past de prijsopmaak toe. Toont '-' als een
    waarde niet betrouwbaar kan worden bepaald (geen waarden verzinnen)."""
    vraagprijs = rij.get("vraagprijs")
    woonoppervlakte = rij.get("woonoppervlakte")

    if vraagprijs and woonoppervlakte and vraagprijs > 0 and woonoppervlakte > 0:
        rij["prijs_per_m2"] = formatteer_bedrag(round(vraagprijs / woonoppervlakte)) + " /m²"
    else:
        rij["prijs_per_m2"] = "-"

    info = geschiedenis.get(rij.get("funda_url"))
    eerste_datum = info.get("eerste_datum") if info else None
    laatste_datum = info.get("laatste_datum") if info else None

    if info and eerste_datum and laatste_datum:
        rij["eerste_waarneming"] = eerste_datum
        rij["laatste_waarneming"] = laatste_datum
        try:
            dagen = (date.fromisoformat(laatste_datum) - date.fromisoformat(eerste_datum)).days
            rij["dagen_in_monitor"] = dagen
        except ValueError:
            rij["dagen_in_monitor"] = "-"

        eerste_prijs = info.get("eerste_prijs")
        if eerste_prijs is not None and vraagprijs is not None:
            rij["prijswijziging"] = formatteer_prijswijziging(vraagprijs - eerste_prijs)
        else:
            rij["prijswijziging"] = "-"
    else:
        rij["eerste_waarneming"] = "-"
        rij["laatste_waarneming"] = "-"
        rij["dagen_in_monitor"] = "-"
        rij["prijswijziging"] = "-"

    rij["vraagprijs"] = formatteer_bedrag(vraagprijs) if vraagprijs is not None else "-"

    perceeloppervlakte = rij.get("perceeloppervlakte")
    rij["woonoppervlakte"] = (
        (f"{woonoppervlakte:,.0f}".replace(",", ".") + " m²") if woonoppervlakte is not None else "-"
    )
    rij["perceeloppervlakte"] = (
        (f"{perceeloppervlakte:,.0f}".replace(",", ".") + " m²") if perceeloppervlakte is not None else "-"
    )

    return rij


def bereken_kpis(rijen: list[dict]) -> dict:
    """Verwacht ruwe (nog niet opgemaakte) rijen, dus vóór verrijk_rij().
    Status-subtellingen zijn binnen de HUIDIGE selectie: als de gebruiker al
    op één status filtert, tellen de andere logischerwijs 0 - zelfde principe
    als de Business-KPI's (geen apart 'ongefilterd totaal' erbij verzinnen)."""
    aantal = len(rijen)
    totaal = sum(r.get("vraagprijs") or 0 for r in rijen)
    gemiddeld = round(totaal / aantal) if aantal else 0
    # "Onbekend" telt nooit als makelaar (zie "Onbekend is geen makelaar");
    # objecten zonder herkende makelaar tellen wel gewoon mee in aantal/noemer.
    makelaars = {(r.get("makelaar") or "").strip() for r in rijen if (r.get("makelaar") or "").strip()}
    zonder_makelaar = sum(1 for r in rijen if not (r.get("makelaar") or "").strip())

    per_status = {"Beschikbaar": 0, "Onder bod": 0, "Verkocht onder voorbehoud": 0}
    for r in rijen:
        s = r.get("status")
        if s in per_status:
            per_status[s] += 1

    prijzen = [r["vraagprijs"] for r in rijen if r.get("vraagprijs") is not None]
    mediaan_vraagprijs = round(statistics.median(prijzen)) if prijzen else None

    prijzen_m2 = [
        r["vraagprijs"] / r["woonoppervlakte"]
        for r in rijen
        if r.get("vraagprijs") and r.get("woonoppervlakte") and r["vraagprijs"] > 0 and r["woonoppervlakte"] > 0
    ]
    gemiddelde_m2 = round(sum(prijzen_m2) / len(prijzen_m2)) if prijzen_m2 else None
    mediaan_m2 = round(statistics.median(prijzen_m2)) if prijzen_m2 else None

    oppervlaktes = [r["woonoppervlakte"] for r in rijen if r.get("woonoppervlakte") is not None]
    totaal_oppervlakte = sum(oppervlaktes) if oppervlaktes else None

    return {
        "aantal_objecten": aantal,
        "totale_vraagprijs": formatteer_bedrag(totaal),
        "gemiddelde_vraagprijs": formatteer_bedrag(gemiddeld),
        "aantal_makelaars": len(makelaars),
        "aantal_zonder_makelaar": zonder_makelaar,
        "beschikbaar": per_status["Beschikbaar"],
        "onder_bod": per_status["Onder bod"],
        "verkocht_ov": per_status["Verkocht onder voorbehoud"],
        "mediaan_vraagprijs": formatteer_bedrag(mediaan_vraagprijs) if mediaan_vraagprijs is not None else "-",
        "gemiddelde_m2": (formatteer_bedrag(gemiddelde_m2) + " /m²") if gemiddelde_m2 is not None else "-",
        "mediaan_m2": (formatteer_bedrag(mediaan_m2) + " /m²") if mediaan_m2 is not None else "-",
        "totaal_woonoppervlakte": (f"{totaal_oppervlakte:,.0f}".replace(",", ".") + " m²") if totaal_oppervlakte else "-",
    }


def bouw_makelaarstabel(rijen: list[dict]) -> list[dict]:
    """Marktaandeel = aandeel binnen de HUIDIGE selectie (rijen is al
    gefilterd op plaats/status/bouwcategorie vóórdat dit wordt aangeroepen -
    dus bij een 'Bestaande bouw'-filter tellen nieuwbouwprojecten hier al
    niet meer mee). Zelfde aanpak als bouw_business_makelaarstabel()."""
    totaal = len(rijen)
    per_makelaar: dict[str, dict] = {}
    for r in rijen:
        naam = (r.get("makelaar") or "").strip() or "Onbekend"
        entry = per_makelaar.setdefault(naam, {
            "makelaar": naam, "aantal": 0, "prijzen": [], "prijzen_m2": [], "oppervlakte": 0.0,
        })
        entry["aantal"] += 1
        if r.get("vraagprijs") is not None:
            entry["prijzen"].append(r["vraagprijs"])
        if r.get("vraagprijs") and r.get("woonoppervlakte") and r["vraagprijs"] > 0 and r["woonoppervlakte"] > 0:
            entry["prijzen_m2"].append(r["vraagprijs"] / r["woonoppervlakte"])
        if r.get("woonoppervlakte") is not None:
            entry["oppervlakte"] += r["woonoppervlakte"]

    tabel = []
    for entry in per_makelaar.values():
        totale_vraagwaarde = sum(entry["prijzen"]) if entry["prijzen"] else None
        gemiddelde_vraagprijs = round(sum(entry["prijzen"]) / len(entry["prijzen"])) if entry["prijzen"] else None
        mediaan_vraagprijs = round(statistics.median(entry["prijzen"])) if entry["prijzen"] else None
        gemiddelde_m2 = round(sum(entry["prijzen_m2"]) / len(entry["prijzen_m2"])) if entry["prijzen_m2"] else None
        tabel.append({
            "makelaar": entry["makelaar"],
            "is_onbekend": entry["makelaar"] == "Onbekend",
            "aantal": entry["aantal"],
            "aandeel_pct": round(entry["aantal"] / totaal * 100, 1) if totaal else 0,
            "totale_vraagwaarde_weergave": formatteer_bedrag(totale_vraagwaarde) if totale_vraagwaarde is not None else "-",
            "gemiddelde_vraagprijs_weergave": formatteer_bedrag(gemiddelde_vraagprijs) if gemiddelde_vraagprijs is not None else "-",
            "mediaan_vraagprijs_weergave": formatteer_bedrag(mediaan_vraagprijs) if mediaan_vraagprijs is not None else "-",
            "gemiddelde_m2_weergave": (formatteer_bedrag(gemiddelde_m2) + " /m²") if gemiddelde_m2 is not None else "-",
            "oppervlakte_weergave": (f"{entry['oppervlakte']:,.0f}".replace(",", ".") + " m²") if entry["oppervlakte"] else "-",
        })

    # Marktleider bovenaan: standaard aflopend op Objecten (== aflopend op
    # Marktaandeel, want beide delen dezelfde noemer "totaal").
    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


def _bereken_marktintensiteit_velden(
    inwoners: int | None, aantal: int, totaal_aanbod: int, totaal_inwoners_bekend: int,
) -> dict:
    """Uitbreiding "Marktintensiteit" (nieuwe gerichte ronde): geeft de vijf
    afgeleide velden terug voor één gebied (plaats of gemeente) - gedeeld
    tussen bouw_plaatsverdeling()/bouw_gemeenteverdeling() om duplicatie van
    de formules te voorkomen.

    Alleen berekend als het inwonertal van DIT gebied bekend is EN er
    minstens één gebied in de selectie een bekend inwonertal heeft (nooit
    een verzonnen waarde) - anders overal "-"/None. `totaal_aanbod` is het
    VOLLEDIGE aanbod van de huidige selectie (dezelfde noemer als elders in
    de app voor "aandeel"); `totaal_inwoners_bekend` is de som van inwoners
    over uitsluitend de gebieden met een bekend inwonertal.

    Formules (letterlijk zoals gevraagd):
      verwacht aanbod = totaal aanbod x (inwoners gebied / totaal inwoners selectie)
      marktintensiteitsindex = werkelijk aanbod / verwacht aanbod x 100
    Interpretatie is bewust neutraal: 100 = evenredig aan inwonertal, geen
    conclusie over woningtekort/verkoopsnelheid/marktgezondheid."""
    aandeel_aanbod_pct = round(aantal / totaal_aanbod * 100, 1) if totaal_aanbod else None

    if not inwoners or not totaal_inwoners_bekend:
        return {
            "aandeel_aanbod_pct": aandeel_aanbod_pct,
            "aandeel_inwoners_pct": None,
            "verschil_pp": None,
            "verwacht_aanbod_weergave": "-",
            "marktintensiteitsindex": None,
        }

    aandeel_inwoners_pct = round(inwoners / totaal_inwoners_bekend * 100, 1)
    verwacht_aanbod = totaal_aanbod * (inwoners / totaal_inwoners_bekend)
    marktintensiteitsindex = round(aantal / verwacht_aanbod * 100, 1) if verwacht_aanbod else None
    return {
        "aandeel_aanbod_pct": aandeel_aanbod_pct,
        "aandeel_inwoners_pct": aandeel_inwoners_pct,
        "verschil_pp": (
            round(aandeel_aanbod_pct - aandeel_inwoners_pct, 1) if aandeel_aanbod_pct is not None else None
        ),
        "verwacht_aanbod_weergave": f"{verwacht_aanbod:.1f}".replace(".", ","),
        "marktintensiteitsindex": marktintensiteitsindex,
    }


def bouw_plaatsverdeling(rijen: list[dict], alle_plaatsen: list[str] | None = None) -> list[dict]:
    """Compacte verdeling per plaats van de HUIDIGE selectie - alleen zinvol
    om te tonen als er meerdere plaatsen in de selectie zitten (bepaalt de
    template, niet deze functie).

    Nulgebieden (stabilisatieronde 2026-09-09): een expliciet geselecteerde
    plaats (`alle_plaatsen`, de actieve regiofilter) blijft altijd zichtbaar
    met 0 aanbod als de huidige filters daar geen objecten opleveren - anders
    lijkt het gebied ten onrechte niet meegenomen te zijn."""
    per_plaats: dict[str, dict] = {}
    for p in (alle_plaatsen or []):
        p = (p or "").strip()
        if p:
            per_plaats.setdefault(p, {
                "plaats": p, "aantal": 0, "beschikbaar": 0, "onder_bod": 0, "verkocht_ov": 0,
                "prijzen": [], "prijzen_m2": [],
            })
    for r in rijen:
        plaats = (r.get("plaats") or "").strip() or "Onbekend"
        entry = per_plaats.setdefault(plaats, {
            "plaats": plaats, "aantal": 0, "beschikbaar": 0, "onder_bod": 0, "verkocht_ov": 0,
            "prijzen": [], "prijzen_m2": [],
        })
        entry["aantal"] += 1
        status = r.get("status")
        if status == "Beschikbaar":
            entry["beschikbaar"] += 1
        elif status == "Onder bod":
            entry["onder_bod"] += 1
        elif status == "Verkocht onder voorbehoud":
            entry["verkocht_ov"] += 1
        if r.get("vraagprijs") is not None:
            entry["prijzen"].append(r["vraagprijs"])
        if r.get("vraagprijs") and r.get("woonoppervlakte") and r["vraagprijs"] > 0 and r["woonoppervlakte"] > 0:
            entry["prijzen_m2"].append(r["vraagprijs"] / r["woonoppervlakte"])

    totaal_aanbod = sum(e["aantal"] for e in per_plaats.values())
    totaal_inwoners_bekend = sum(
        geo_info(e["plaats"]).get("inwoners_plaats") or 0 for e in per_plaats.values()
    )

    tabel = []
    for entry in per_plaats.values():
        totale_vraagwaarde = sum(entry["prijzen"]) if entry["prijzen"] else None
        gemiddelde_vraagprijs = round(sum(entry["prijzen"]) / len(entry["prijzen"])) if entry["prijzen"] else None
        gemiddelde_m2 = round(sum(entry["prijzen_m2"]) / len(entry["prijzen_m2"])) if entry["prijzen_m2"] else None
        info = geo_info(entry["plaats"])
        inwoners = info.get("inwoners_plaats")
        aanbod_per_1000 = round(entry["aantal"] / inwoners * 1000, 1) if inwoners else None
        rij = {
            "plaats": entry["plaats"],
            "aantal": entry["aantal"],
            "beschikbaar": entry["beschikbaar"],
            "onder_bod": entry["onder_bod"],
            "verkocht_ov": entry["verkocht_ov"],
            "totale_vraagwaarde_weergave": formatteer_bedrag(totale_vraagwaarde) if totale_vraagwaarde is not None else "-",
            "gemiddelde_vraagprijs_weergave": formatteer_bedrag(gemiddelde_vraagprijs) if gemiddelde_vraagprijs is not None else "-",
            "gemiddelde_m2_weergave": (formatteer_bedrag(gemiddelde_m2) + " /m²") if gemiddelde_m2 is not None else "-",
            # DEEL D: geografische context - "-" als bron/inwonertal ontbreekt
            # (nooit een verzonnen waarde tonen).
            "gemeente": info.get("gemeente") or "-",
            "inwoners_weergave": f"{inwoners:,}".replace(",", ".") if inwoners else "-",
            "aanbod_per_1000_weergave": (f"{aanbod_per_1000}".replace(".", ",")) if aanbod_per_1000 is not None else "-",
            # Op plaatsniveau is "inwoners van dit gebied" al exact de plaats
            # zelf, dus identiek aan de scope van de selectie (geen apart
            # gemeente/plaats-onderscheid nodig zoals bij bouw_gemeenteverdeling
            # hieronder). Zelfde veldnaam als daar, zodat de Marktintensiteit-
            # tabel in de template dit veld op beide niveaus kan gebruiken.
            "inwoners_selectie_weergave": f"{inwoners:,}".replace(",", ".") if inwoners else "-",
        }
        rij.update(_bereken_marktintensiteit_velden(inwoners, entry["aantal"], totaal_aanbod, totaal_inwoners_bekend))
        tabel.append(rij)

    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


def bouw_gemeenteverdeling(rijen: list[dict], alle_plaatsen: list[str] | None = None) -> list[dict]:
    """DEEL D3/D4: dezelfde verdeling, maar gegroepeerd op gemeente i.p.v.
    plaats (via GEO_REFERENTIE). Ondersteunt de plaats/gemeente-toggle -
    vervangt bouw_plaatsverdeling() niet, is een aparte, kleine weergave op
    dezelfde ruwe rijen. Plaatsen zonder bekende gemeente vallen in
    'Gemeente onbekend' (nooit stilzwijgend genegeerd).

    Nulgebieden: elke gemeente van een expliciet geselecteerde plaats
    (`alle_plaatsen`) blijft zichtbaar met 0 aanbod, ook als GEEN van de
    plaatsen in die gemeente objecten opleverde.

    Marktintensiteit-correctie (2026-09-10): de teller (aanbod) van de
    Marktintensiteit-berekening is altijd beperkt tot de daadwerkelijk
    geselecteerde plaatsen. De noemer (inwoners) moet EXACT dezelfde
    geografische scope hebben - dus NIET het volledige officiële
    gemeentelijke inwonertal (`inwoners_gemeente`, dat blijft uitsluitend de
    context in de bovenste tabel/"Aanbod per 1.000 inw."), maar de SOM van
    `inwoners_plaats` van alléén de geselecteerde plaatsen die tot die
    gemeente behoren (`inwoners_selectie`). Bij een gemengde selectie zoals
    Bernheze (4 kernen) + Vinkel ('s-Hertogenbosch) voorkomt dit dat de
    volledige bevolking van 's-Hertogenbosch (162.272) meetelt terwijl er
    verder niets van die gemeente gescand is - zie docs/BUGLIST.md."""
    per_gemeente: dict[str, dict] = {}
    for p in (alle_plaatsen or []):
        p = (p or "").strip()
        if not p:
            continue
        info = geo_info(p)
        gemeente = info.get("gemeente") or "Gemeente onbekend"
        entry = per_gemeente.setdefault(gemeente, {
            "gemeente": gemeente, "aantal": 0, "beschikbaar": 0, "onder_bod": 0, "verkocht_ov": 0,
            "prijzen": [], "prijzen_m2": [],
            "inwoners": info.get("inwoners_gemeente"), "provincie": info.get("provincie") or "-",
            "plaatsen_in_selectie": set(),
        })
        entry["plaatsen_in_selectie"].add(p)
    for r in rijen:
        plaats = (r.get("plaats") or "").strip()
        info = geo_info(plaats)
        gemeente = info.get("gemeente") or "Gemeente onbekend"
        entry = per_gemeente.setdefault(gemeente, {
            "gemeente": gemeente, "aantal": 0, "beschikbaar": 0, "onder_bod": 0, "verkocht_ov": 0,
            "prijzen": [], "prijzen_m2": [],
            "inwoners": info.get("inwoners_gemeente"), "provincie": info.get("provincie") or "-",
            "plaatsen_in_selectie": set(),
        })
        if plaats:
            entry["plaatsen_in_selectie"].add(plaats)
        entry["aantal"] += 1
        status = r.get("status")
        if status == "Beschikbaar":
            entry["beschikbaar"] += 1
        elif status == "Onder bod":
            entry["onder_bod"] += 1
        elif status == "Verkocht onder voorbehoud":
            entry["verkocht_ov"] += 1
        if r.get("vraagprijs") is not None:
            entry["prijzen"].append(r["vraagprijs"])
        if r.get("vraagprijs") and r.get("woonoppervlakte") and r["vraagprijs"] > 0 and r["woonoppervlakte"] > 0:
            entry["prijzen_m2"].append(r["vraagprijs"] / r["woonoppervlakte"])

    def _inwoners_selectie(entry: dict) -> int | None:
        """Som van inwoners_plaats van uitsluitend de geselecteerde plaatsen
        binnen deze gemeente. None (nooit 0-als-gok) als geen van die
        plaatsen een bekend inwonertal heeft."""
        totaal = sum(
            geo_info(p).get("inwoners_plaats") or 0 for p in entry["plaatsen_in_selectie"]
        )
        return totaal or None

    for entry in per_gemeente.values():
        entry["inwoners_selectie"] = _inwoners_selectie(entry)

    totaal_aanbod = sum(e["aantal"] for e in per_gemeente.values())
    # Noemer voor de Marktintensiteitsberekening: som van de per-gemeente
    # geselecteerde-inwoners (dezelfde scope als de teller), NIET de som van
    # de volledige officiële gemeentelijke inwonertallen.
    totaal_inwoners_bekend = sum(e["inwoners_selectie"] or 0 for e in per_gemeente.values())

    tabel = []
    for entry in per_gemeente.values():
        totale_vraagwaarde = sum(entry["prijzen"]) if entry["prijzen"] else None
        gemiddelde_vraagprijs = round(sum(entry["prijzen"]) / len(entry["prijzen"])) if entry["prijzen"] else None
        gemiddelde_m2 = round(sum(entry["prijzen_m2"]) / len(entry["prijzen_m2"])) if entry["prijzen_m2"] else None
        # "inwoners" = het volledige officiële gemeentelijke inwonertal -
        # blijft uitsluitend de context in de bovenste tabel/"Aanbod per
        # 1.000 inw.". "inwoners_selectie" (hierboven berekend) is de enige
        # die de Marktintensiteitsformule hieronder mag voeden.
        inwoners = entry["inwoners"]
        inwoners_selectie = entry["inwoners_selectie"]
        aanbod_per_1000 = round(entry["aantal"] / inwoners * 1000, 1) if inwoners else None
        rij = {
            "gemeente": entry["gemeente"],
            "provincie": entry["provincie"],
            "aantal": entry["aantal"],
            "beschikbaar": entry["beschikbaar"],
            "onder_bod": entry["onder_bod"],
            "verkocht_ov": entry["verkocht_ov"],
            "totale_vraagwaarde_weergave": formatteer_bedrag(totale_vraagwaarde) if totale_vraagwaarde is not None else "-",
            "gemiddelde_vraagprijs_weergave": formatteer_bedrag(gemiddelde_vraagprijs) if gemiddelde_vraagprijs is not None else "-",
            "gemiddelde_m2_weergave": (formatteer_bedrag(gemiddelde_m2) + " /m²") if gemiddelde_m2 is not None else "-",
            "inwoners_weergave": f"{inwoners:,}".replace(",", ".") if inwoners else "-",
            "aanbod_per_1000_weergave": (f"{aanbod_per_1000}".replace(".", ",")) if aanbod_per_1000 is not None else "-",
            "inwoners_selectie_weergave": f"{inwoners_selectie:,}".replace(",", ".") if inwoners_selectie else "-",
        }
        rij.update(_bereken_marktintensiteit_velden(inwoners_selectie, entry["aantal"], totaal_aanbod, totaal_inwoners_bekend))
        tabel.append(rij)

    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


WONING_PRIJSSEGMENTEN = [
    ("< € 300.000", 0, 300_000),
    ("€ 300.000-400.000", 300_000, 400_000),
    ("€ 400.000-500.000", 400_000, 500_000),
    ("€ 500.000-750.000", 500_000, 750_000),
    ("€ 750.000-1.000.000", 750_000, 1_000_000),
    ("> € 1.000.000", 1_000_000, None),
]


def bereken_woning_segmentanalyse(
    rijen: list[dict], grenzen: list[tuple[str, float, float | None]] | None = None,
) -> list[dict]:
    """Eenvoudige prijssegmentanalyse (DEEL I), configureerbare grenzen.
    Alleen objecten met een betrouwbaar bekende, positieve vraagprijs tellen
    mee. Elk object valt exact in één segment (zie _segment_van)."""
    grenzen = grenzen or WONING_PRIJSSEGMENTEN
    per_segment: dict[str, dict] = {
        label: {"segment": label, "aantal": 0, "prijzen": [], "prijzen_m2": []}
        for label, _, _ in grenzen
    }
    totaal = 0

    for r in rijen:
        prijs = r.get("vraagprijs")
        if prijs is None or prijs <= 0:
            continue
        segment = _segment_van(prijs, grenzen)
        if segment is None:
            continue
        totaal += 1
        entry = per_segment[segment]
        entry["aantal"] += 1
        entry["prijzen"].append(prijs)
        m2 = r.get("woonoppervlakte")
        if m2:
            entry["prijzen_m2"].append(prijs / m2)

    resultaat = []
    for label, _, _ in grenzen:
        entry = per_segment[label]
        gem = round(sum(entry["prijzen"]) / len(entry["prijzen"])) if entry["prijzen"] else None
        med = round(statistics.median(entry["prijzen"])) if entry["prijzen"] else None
        gem_m2 = round(sum(entry["prijzen_m2"]) / len(entry["prijzen_m2"])) if entry["prijzen_m2"] else None
        med_m2 = round(statistics.median(entry["prijzen_m2"])) if entry["prijzen_m2"] else None
        resultaat.append({
            "segment": label,
            "aantal": entry["aantal"],
            "aandeel_pct": round(entry["aantal"] / totaal * 100, 1) if totaal else 0,
            "gemiddelde_vraagprijs": formatteer_bedrag(gem) if gem is not None else "-",
            "mediaan_vraagprijs": formatteer_bedrag(med) if med is not None else "-",
            "gemiddelde_m2": (formatteer_bedrag(gem_m2) + " /m²") if gem_m2 is not None else "-",
            "mediaan_m2": (formatteer_bedrag(med_m2) + " /m²") if med_m2 is not None else "-",
        })
    return resultaat


# Stabilisatieronde 2026-09-09 (middag): de ancestor_card-fix in
# scanner/makelaarsmonitor_v41.py (zie docs/CLAUDE_STATUS.md) toonde aan dat
# Woningen-scans van VOOR dit moment structureel onvolledig konden zijn
# (soms <25% van het werkelijke aanbod). Dit tijdstip is exact het
# scanmoment van de eerste scan met de gefixte scanner (Heesch/Heeswijk-
# Dinther/Nistelrode/Vinkel/Vorstenbosch, 118 objecten - de eerste
# betrouwbare productie-baseline). Bewust GEEN databasewijziging: dit is een
# simpele, globale tijdstempel-grens die bepaalt welke scans onderling
# vergelijkbaar zijn voor trends/mutaties. Oude scans blijven ONGEWIJZIGD en
# volledig raadpleegbaar via Historische Analyse - alleen automatische
# vergelijkingen (Marktdynamiek, Dashboard-trends) die deze grens zouden
# overschrijden worden onderdrukt, om een fictieve aanbodsprong (+81 e.d.)
# te voorkomen. Geldt uitsluitend voor Woningen (de Business-scanner had dit
# specifieke compleetheidsprobleem niet).
WONINGEN_METHODIEK_WIJZIGING = "2026-09-09T10:09:54"


def woningen_is_nieuwe_methodiek(scanmoment: str | None) -> bool:
    return bool(scanmoment) and scanmoment >= WONINGEN_METHODIEK_WIJZIGING


def haal_vorige_scan_id(con: sqlite3.Connection, gebied: str, huidige_scanmoment: str, huidige_scan_id: int):
    """Meest recente eerdere scan met EXACT hetzelfde gebied als de huidige
    scan (zelfde matching-principe als get_previous_scan_id() in de
    historie-tool en als de Business-mutatielogica) - dus geen vergelijking
    met een scan die een andere regioselectie had.

    Overschrijdt nooit de methodiekbreuk-grens hierboven: als de huidige scan
    al met de nieuwe, betrouwbare scanner is gemaakt, komt een 'vorige scan'
    van vóór die grens NOOIT in aanmerking (zou een fictieve aanbodsprong
    suggereren). Voor een scan die zelf nog van vóór de grens is, blijft het
    bestaande gedrag (vergelijking met een andere oude scan) ongewijzigd."""
    query = "SELECT scan_id FROM scans WHERE gebied = ? AND scanmoment < ? AND scan_id <> ?"
    params: list = [gebied, huidige_scanmoment, huidige_scan_id]
    if woningen_is_nieuwe_methodiek(huidige_scanmoment):
        query += " AND scanmoment >= ?"
        params.append(WONINGEN_METHODIEK_WIJZIGING)
    query += " ORDER BY scanmoment DESC LIMIT 1"
    row = con.execute(query, params).fetchone()
    return row["scan_id"] if row else None


def haal_scan_snapshot(con: sqlite3.Connection, scan_id: int) -> dict[str, dict]:
    """Alle rijen van één scan (ONGEFILTERD), op funda_url - de unieke
    sleutel binnen een scan. Gebruikt voor mutatiedetectie, niet voor de
    weergave (die gebruikt de wél gefilterde haal_snapshotrijen())."""
    rijen = con.execute("SELECT * FROM snapshots WHERE scan_id = ?", (scan_id,)).fetchall()
    return {r["funda_url"]: dict(r) for r in rijen if r["funda_url"]}


def woning_mutatie_naam(oud: dict | None, nieuw: dict | None) -> str:
    if oud is None:
        return "Nieuw aanbod"
    if nieuw is None:
        return "Uit aanbod"

    wijzigingen = []
    if oud.get("vraagprijs") != nieuw.get("vraagprijs"):
        wijzigingen.append("Vraagprijs gewijzigd")
    if (oud.get("status") or "") != (nieuw.get("status") or ""):
        wijzigingen.append("Status gewijzigd")
    if (oud.get("makelaar") or "") != (nieuw.get("makelaar") or ""):
        wijzigingen.append("Makelaar gewijzigd")
    if oud.get("woonoppervlakte") != nieuw.get("woonoppervlakte"):
        wijzigingen.append("Oppervlakte gewijzigd")

    return " + ".join(wijzigingen) if wijzigingen else "Ongewijzigd"


def woning_mutatie_details(oud: dict, nieuw: dict) -> list[str]:
    """Bouwt 'oud → nieuw'-regels, uitsluitend wanneer zowel de oude als de
    nieuwe snapshot betrouwbaar aanwezig zijn (dus nooit voor 'Nieuw aanbod'/
    'Uit aanbod')."""
    details = []
    if oud.get("vraagprijs") != nieuw.get("vraagprijs"):
        oud_w = formatteer_bedrag(oud["vraagprijs"]) if oud.get("vraagprijs") is not None else "-"
        nieuw_w = formatteer_bedrag(nieuw["vraagprijs"]) if nieuw.get("vraagprijs") is not None else "-"
        details.append(f"Vraagprijs: {oud_w} → {nieuw_w}")
    if (oud.get("status") or "") != (nieuw.get("status") or ""):
        details.append(f"Status: {oud.get('status') or '-'} → {nieuw.get('status') or '-'}")
    if (oud.get("makelaar") or "") != (nieuw.get("makelaar") or ""):
        details.append(f"Makelaar: {oud.get('makelaar') or '-'} → {nieuw.get('makelaar') or '-'}")
    if oud.get("woonoppervlakte") != nieuw.get("woonoppervlakte"):
        oud_w = f"{oud['woonoppervlakte']:,.0f} m²".replace(",", ".") if oud.get("woonoppervlakte") is not None else "-"
        nieuw_w = f"{nieuw['woonoppervlakte']:,.0f} m²".replace(",", ".") if nieuw.get("woonoppervlakte") is not None else "-"
        details.append(f"Oppervlakte: {oud_w} → {nieuw_w}")
    return details


def woning_bouw_mutaties(vorige_snapshot: dict, huidige_snapshot: dict):
    """Vergelijkt de VOLLEDIGE (ongefilterde) snapshots van twee scans - net
    als bij Business. Mutaties zijn een objectieve scanvergelijking, geen
    weergavefilter. 'Uit aanbod' betekent uitsluitend: niet meer aangetroffen
    in de volgende scan; NOOIT automatisch 'verkocht'/'verhuurd'/'transactie
    afgerond'."""
    ids = sorted(set(vorige_snapshot) | set(huidige_snapshot))
    aantallen: dict[str, int] = {}
    gewijzigd = []

    for url in ids:
        oud = vorige_snapshot.get(url)
        nieuw = huidige_snapshot.get(url)
        naam = woning_mutatie_naam(oud, nieuw)
        aantallen[naam] = aantallen.get(naam, 0) + 1
        if naam != "Ongewijzigd":
            bron = nieuw or oud
            details = woning_mutatie_details(oud, nieuw) if (oud is not None and nieuw is not None) else []
            gewijzigd.append({
                "mutatie": naam,
                "adres": bron.get("adres"),
                "plaats": bron.get("plaats"),
                "makelaar": bron.get("makelaar"),
                "funda_url": url,
                "details": details,
            })

    return aantallen, gewijzigd


def haal_vergelijkbare_scans(con: sqlite3.Connection, gebied: str) -> list[dict]:
    """Alle scans met EXACT hetzelfde gebied, nieuwste eerst - vult de
    peildatum-/scanmomentselector voor historische analyse (DEEL C)."""
    rows = con.execute(
        "SELECT scan_id, scanmoment, aantal_objecten FROM scans WHERE gebied = ? "
        "ORDER BY scanmoment DESC, scan_id DESC",
        (gebied,),
    ).fetchall()
    return [
        {
            "scan_id": r["scan_id"],
            "scanmoment_weergave": formatteer_scanmoment(r["scanmoment"]),
            "aantal_objecten": r["aantal_objecten"],
            "is_nieuwe_methodiek": woningen_is_nieuwe_methodiek(r["scanmoment"]),
        }
        for r in rows
    ]


def bouw_analyseresultaat(args) -> dict:
    """Eén pipeline voor zowel de resultaatpagina als de CSV-export, zodat beide
    gegarandeerd dezelfde filters, objecten en kolommen gebruiken. Ondersteunt
    optioneel een historisch scan_id (DEEL C) - zonder geldige scan_id-
    queryparameter wordt de meest recente scan gebruikt (ongewijzigd gedrag)."""
    plaatsen, statussen, bouwcategorieen, kolommen = parse_filters(args)
    basis = {
        "plaatsen": plaatsen,
        "statussen": statussen,
        "bouwcategorieen": bouwcategorieen,
        "kolommen": kolommen,
        "kolom_labels": KOLOMMEN,
        "scaninfo": None,
        "scan_opties": [],
        "is_historisch": False,
        "laatste_scan_id": None,
        "rijen": [],
        # RUWE (nog niet geformatteerde) rijen - vraagprijs/woonoppervlakte
        # blijven hier numeriek (int/float/None), in tegenstelling tot
        # basis["rijen"] die door verrijk_rij() al naar presentatiestrings
        # ("€ 795.000") is omgezet. Berekeningen (bv. makelaarsprofiel) MOETEN
        # dit veld gebruiken - nooit basis["rijen"] - conform de regel
        # "calculatie op raw data, formattering pas bij presentatie".
        "ruwe_rijen": [],
        "kpis": None,
        "makelaarstabel": [],
        "plaatsverdeling": [],
        "gemeenteverdeling": [],
        "segmentanalyse": [],
        "dagen_in_monitor_stats": {"aantal": 0, "gemiddelde": None, "mediaan": None},
        "mutatie_aantallen": {},
        "mutatie_rijen": [],
        "vorige_scanmoment_weergave": None,
        "vorige_aantal_objecten": None,
        "netto_verandering": None,
        # Volledige (ongefilterde) scanteruggang - alleen ter referentie, NOOIT
        # in dezelfde primaire KPI-reeks als de gefilterde selectie (DEEL E).
        "vorige_aantal_objecten_totaal": None,
        "huidige_aantal_objecten_totaal": None,
        "netto_verandering_totaal": None,
        # Methodiekbreuk (stabilisatieronde 2026-09-09) - zie
        # WONINGEN_METHODIEK_WIJZIGING hierboven.
        "is_nieuwe_methodiek": True,
        "methodiek_wijziging_weergave": formatteer_scanmoment(WONINGEN_METHODIEK_WIJZIGING),
        "eerste_van_nieuwe_meetreeks": False,
        "foutmelding": None,
    }

    if not DB_PATH.exists():
        basis["foutmelding"] = f"Database niet gevonden op: {DB_PATH}"
        return basis

    con = get_readonly_connection()
    try:
        laatste_scan_id = haal_laatste_scan_id(con)
        if laatste_scan_id is None:
            basis["foutmelding"] = "Geen scans gevonden in de database."
            return basis
        basis["laatste_scan_id"] = laatste_scan_id

        # DEEL C: optioneel een historische scan kiezen i.p.v. de meest
        # recente. Een ongeldig/niet-bestaand scan_id valt stil terug op de
        # meest recente scan (zelfde, veilige patroon als elders in de app
        # voor ongeldige filterwaarden).
        scan_id = laatste_scan_id
        gekozen_scan_id = args.get("scan_id")
        if gekozen_scan_id:
            try:
                gekozen_scan_id = int(gekozen_scan_id)
                if con.execute("SELECT 1 FROM scans WHERE scan_id = ?", (gekozen_scan_id,)).fetchone():
                    scan_id = gekozen_scan_id
            except (TypeError, ValueError):
                pass
        basis["is_historisch"] = scan_id != laatste_scan_id

        scaninfo = haal_scan_info(con, scan_id)
        scaninfo["scanmoment_weergave"] = formatteer_scanmoment(scaninfo.get("scanmoment"))
        basis["scaninfo"] = scaninfo
        basis["scan_opties"] = haal_vergelijkbare_scans(con, scaninfo.get("gebied", ""))
        basis["is_nieuwe_methodiek"] = woningen_is_nieuwe_methodiek(scaninfo.get("scanmoment"))

        ruwe_rijen = haal_snapshotrijen(con, scan_id, plaatsen, statussen, bouwcategorieen)
        ruwe_rijen = dedupliceer_op_funda_url(ruwe_rijen)
        basis["ruwe_rijen"] = ruwe_rijen

        basis["kpis"] = bereken_kpis(ruwe_rijen)
        basis["makelaarstabel"] = bouw_makelaarstabel(ruwe_rijen)
        basis["plaatsverdeling"] = bouw_plaatsverdeling(ruwe_rijen, alle_plaatsen=plaatsen)
        basis["gemeenteverdeling"] = bouw_gemeenteverdeling(ruwe_rijen, alle_plaatsen=plaatsen)
        basis["segmentanalyse"] = bereken_woning_segmentanalyse(ruwe_rijen)

        if ruwe_rijen:
            # DEEL C2: bij een historische scan mag "laatste waarneming" nooit
            # een LATERE (nog toekomstige, t.o.v. dit peilmoment) scan tonen -
            # daarom altijd begrensd tot en met de gekozen scan_id.
            geschiedenis = haal_geschiedenis(
                con, [r.get("funda_url") for r in ruwe_rijen], tot_en_met_scan_id=scan_id
            )
            basis["rijen"] = [verrijk_rij(dict(r), geschiedenis) for r in ruwe_rijen]
            basis["dagen_in_monitor_stats"] = bereken_dagen_in_monitor_stats(basis["rijen"])

        # Mutaties: vergelijking t.o.v. de vorige exact vergelijkbare scan
        # (zelfde gebied), altijd relatief aan de GEKOZEN scan (dus ook
        # correct bij een historische analyse - vergelijkt dan met DIENS
        # voorganger, niet met de nieuwste scan).
        vorige_scan_id = haal_vorige_scan_id(con, scaninfo.get("gebied", ""), scaninfo.get("scanmoment", ""), scan_id)
        if vorige_scan_id is None and basis["is_nieuwe_methodiek"]:
            # Onderscheid tonen tussen "dit is de allereerste scan ooit voor
            # dit gebied" en "dit is de eerste scan van de NIEUWE, betrouwbare
            # meetreeks - er is wel oudere (methodiek-incompatibele) historie,
            # die bewust niet als vergelijking wordt gebruikt".
            oudere_scan_bestaat = con.execute(
                "SELECT 1 FROM scans WHERE gebied = ? AND scanmoment < ? LIMIT 1",
                (scaninfo.get("gebied", ""), scaninfo.get("scanmoment", "")),
            ).fetchone()
            basis["eerste_van_nieuwe_meetreeks"] = oudere_scan_bestaat is not None
        if vorige_scan_id:
            huidige_snapshot = haal_scan_snapshot(con, scan_id)
            vorige_snapshot = haal_scan_snapshot(con, vorige_scan_id)
            vorige_rij = con.execute(
                "SELECT scanmoment, aantal_objecten FROM scans WHERE scan_id = ?", (vorige_scan_id,)
            ).fetchone()

            # DEEL E: de PRIMAIRE marktdynamiek volgt dezelfde actieve filters
            # als de Analyse zelf (niet de volledige, ongefilterde scan) -
            # anders lijken bv. "9" (gefilterde selectie) en "37" (volledige
            # scan) onterecht dezelfde populatie te vertegenwoordigen.
            vorige_ruwe_rijen = dedupliceer_op_funda_url(
                haal_snapshotrijen(con, vorige_scan_id, plaatsen, statussen, bouwcategorieen)
            )
            huidige_gefilterd = {r["funda_url"]: dict(r) for r in ruwe_rijen if r.get("funda_url")}
            vorige_gefilterd = {r["funda_url"]: dict(r) for r in vorige_ruwe_rijen if r.get("funda_url")}
            aantallen, mutatie_rijen = woning_bouw_mutaties(vorige_gefilterd, huidige_gefilterd)
            basis["mutatie_aantallen"] = aantallen
            basis["mutatie_rijen"] = mutatie_rijen
            basis["vorige_scanmoment_weergave"] = (
                formatteer_scanmoment(vorige_rij["scanmoment"]) if vorige_rij else None
            )
            basis["vorige_aantal_objecten"] = len(vorige_gefilterd)
            basis["netto_verandering"] = len(huidige_gefilterd) - len(vorige_gefilterd)

            # Volledige scan (ongefilterd) - uitsluitend als kleine, apart
            # gelabelde referentie, nooit vermengd met de gefilterde cijfers.
            if vorige_rij:
                basis["vorige_aantal_objecten_totaal"] = vorige_rij["aantal_objecten"]
                basis["huidige_aantal_objecten_totaal"] = len(huidige_snapshot)
                basis["netto_verandering_totaal"] = len(huidige_snapshot) - vorige_rij["aantal_objecten"]

        return basis
    finally:
        con.close()


def get_business_readonly_connection() -> sqlite3.Connection:
    """Open de Business-database strikt read-only. Aparte database, aparte
    connectie - deelt niets met de woningen-database."""
    uri = f"file:{BUSINESS_DB_PATH.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def haal_business_statussen() -> list[str]:
    """Leest de daadwerkelijk aanwezige statuswaarden uit de Business-database
    (geen hardcoded lijst), met een nette fallback als de database nog niet
    bestaat of leeg is."""
    if not BUSINESS_DB_PATH.exists():
        return list(BUSINESS_STATUS_FALLBACK)
    try:
        con = get_business_readonly_connection()
        try:
            rijen = con.execute(
                "SELECT DISTINCT status FROM business_snapshots "
                "WHERE status IS NOT NULL AND status <> '' ORDER BY status"
            ).fetchall()
            statussen = [r["status"] for r in rijen]
            return statussen or list(BUSINESS_STATUS_FALLBACK)
        finally:
            con.close()
    except sqlite3.Error:
        return list(BUSINESS_STATUS_FALLBACK)


def business_canonical(waarden: list[str]) -> str:
    """Zelfde canonieke identificatie als historie/funda_business_historie_v1.py:
    gesorteerde, unieke, opgeschoonde waarden samengevoegd met ' | '."""
    vals = sorted({(w or "").strip() for w in waarden if (w or "").strip()}, key=lambda x: x.casefold())
    return " | ".join(vals)


def business_categorieen_van_rij(rij: dict) -> set[str]:
    ruw = rij.get("gezocht_categorie") or ""
    return {d.strip() for d in re.split(r"[;|,]", ruw) if d.strip()}


def business_heeft_koopprijs(rij: dict) -> bool:
    return rij.get("koopprijs") is not None


def business_heeft_huurprijs(rij: dict) -> bool:
    return rij.get("huurprijs") is not None


def business_is_koop_aanbod(rij: dict) -> bool:
    """Breed: 'wordt dit object te koop aangeboden', incl. n.o.t.k. (geen
    prijs bekend, wel een koopindicatie). Gebruikt voor het transactietype-
    filter en de makelaarstabel - NIET voor de Koop-KPI's (die vereisen een
    daadwerkelijk numerieke koopprijs, zie business_heeft_koopprijs)."""
    if rij.get("koopprijs") is not None:
        return True
    if (rij.get("koopprijs_conditie") or "").strip():
        return True
    if "n.o.t.k" in (rij.get("waarschuwing") or "").lower():
        return True
    return False


def business_is_huur_aanbod(rij: dict) -> bool:
    """Breed: incl. 'huurprijs op aanvraag' (bevestigd opgeslagen als
    huurprijs_eenheid == 'op_aanvraag', huurprijs leeg)."""
    if rij.get("huurprijs") is not None:
        return True
    if (rij.get("huurprijs_eenheid") or "") == "op_aanvraag":
        return True
    return False


def business_is_dual_listed(rij: dict) -> bool:
    """Strikt: daadwerkelijk zowel een koop- als een huurprijs bekend."""
    return rij.get("koopprijs") is not None and rij.get("huurprijs") is not None


def business_pas_transactietype_toe(rijen: list[dict], transactietype: str) -> list[dict]:
    if transactietype == "Huur":
        return [r for r in rijen if business_is_huur_aanbod(r)]
    if transactietype == "Koop":
        return [r for r in rijen if business_is_koop_aanbod(r)]
    if transactietype == "Koop + Huur":
        return [r for r in rijen if business_is_koop_aanbod(r) and business_is_huur_aanbod(r)]
    return rijen  # "Alles": geen transactiefilter


def haal_business_scan_id(con: sqlite3.Connection, gebied: str, categorieen: str):
    """Meest recente scan die EXACT bij dit gebied + deze categorieen hoort."""
    row = con.execute(
        "SELECT scan_id, scanmoment FROM business_scans WHERE gebied = ? AND categorieen = ? "
        "ORDER BY scanmoment DESC, scan_id DESC LIMIT 1",
        (gebied, categorieen),
    ).fetchone()
    return (row["scan_id"], row["scanmoment"]) if row else (None, None)


def haal_business_vorige_scan_id(con, gebied, categorieen, huidige_scanmoment, huidige_scan_id):
    row = con.execute(
        "SELECT scan_id FROM business_scans WHERE gebied = ? AND categorieen = ? "
        "AND scanmoment < ? AND scan_id <> ? ORDER BY scanmoment DESC LIMIT 1",
        (gebied, categorieen, huidige_scanmoment, huidige_scan_id),
    ).fetchone()
    return row["scan_id"] if row else None


def haal_business_snapshot(con: sqlite3.Connection, scan_id: int) -> dict[str, dict]:
    rijen = con.execute("SELECT * FROM business_snapshots WHERE scan_id = ?", (scan_id,)).fetchall()
    return {r["funda_object_id"]: dict(r) for r in rijen}


def haal_business_geschiedenis(con, gebied: str, categorieen: str, object_ids: list[str]) -> dict[str, tuple]:
    """Eerste/laatste scanmoment per funda_object_id, over alle Business-scans
    binnen exact dit gebied + deze categorieen."""
    ids = sorted({i for i in object_ids if i})
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    query = f"""
        SELECT s.funda_object_id, MIN(sc.scanmoment) AS eerste, MAX(sc.scanmoment) AS laatste
        FROM business_snapshots s
        JOIN business_scans sc ON sc.scan_id = s.scan_id
        WHERE sc.gebied = ? AND sc.categorieen = ? AND s.funda_object_id IN ({placeholders})
        GROUP BY s.funda_object_id
    """
    resultaat = {}
    for rij in con.execute(query, [gebied, categorieen] + ids).fetchall():
        resultaat[rij["funda_object_id"]] = (rij["eerste"], rij["laatste"])
    return resultaat


def bereken_business_kpis(rijen: list[dict]) -> dict:
    """Huuraanbod/Koopaanbod zijn hier de BREDE marktdefinities (inclusief
    'op aanvraag'/n.o.t.k.), niet alleen objecten met een numerieke prijs -
    zie business_is_huur_aanbod()/business_is_koop_aanbod(). De twee subtellingen
    (met prijs / op aanvraag resp. n.o.t.k.) zijn een sluitende partitie van het
    totaal: elk object valt in precies één van beide sub-categorieën."""
    aantal = len(rijen)
    kantoor = sum(1 for r in rijen if "Kantoor" in business_categorieen_van_rij(r))
    bedrijfsruimte = sum(1 for r in rijen if "Bedrijfsruimte" in business_categorieen_van_rij(r))
    dual = sum(1 for r in rijen if business_is_dual_listed(r))
    totaal_m2 = sum(r["oppervlakte_m2"] for r in rijen if r.get("oppervlakte_m2") is not None)

    huur_met_prijs = sum(1 for r in rijen if business_heeft_huurprijs(r))
    huur_op_aanvraag = sum(
        1 for r in rijen
        if r.get("huurprijs") is None and (r.get("huurprijs_eenheid") or "") == "op_aanvraag"
    )
    koop_met_prijs = sum(1 for r in rijen if business_heeft_koopprijs(r))
    koop_zonder_prijs = sum(
        1 for r in rijen if r.get("koopprijs") is None and business_is_koop_aanbod(r)
    )
    bekende_makelaars = {(r.get("makelaar") or "").strip() for r in rijen if (r.get("makelaar") or "").strip()}
    zonder_makelaar = sum(1 for r in rijen if not (r.get("makelaar") or "").strip())

    return {
        "actief_aanbod": aantal,
        "kantoor": kantoor,
        "bedrijfsruimte": bedrijfsruimte,
        "dual_listed": dual,
        "totaal_m2_weergave": (f"{totaal_m2:,.0f}".replace(",", ".") + " m²") if totaal_m2 else "-",
        "koopaanbod": koop_met_prijs + koop_zonder_prijs,
        "koopaanbod_met_prijs": koop_met_prijs,
        "koopaanbod_notk": koop_zonder_prijs,
        "huuraanbod": huur_met_prijs + huur_op_aanvraag,
        "huuraanbod_met_prijs": huur_met_prijs,
        "huuraanbod_op_aanvraag": huur_op_aanvraag,
        # Aantal BEKENDE makelaars ("Onbekend" telt hier nooit als makelaar,
        # zie DEEL "Onbekend is geen makelaar") - objecten zonder herkende
        # makelaar tellen wel gewoon mee in actief_aanbod/de noemer.
        "aantal_makelaars": len(bekende_makelaars),
        "aantal_zonder_makelaar": zonder_makelaar,
    }


def bereken_business_huur_kpis(rijen: list[dict]) -> dict:
    """Uitsluitend objecten met huurprijs_eenheid == per_m2_per_jaar tellen mee
    voor de €/m²/jaar-statistiek - per maand/per jaar/op aanvraag worden nooit
    meegemengd."""
    per_m2_jaar = [
        r["huurprijs"] for r in rijen
        if r.get("huurprijs_eenheid") == "per_m2_per_jaar" and r.get("huurprijs") is not None
    ]
    berekende_waarden = [
        r["berekende_huur_per_jaar"] for r in rijen if r.get("berekende_huur_per_jaar") is not None
    ]
    return {
        "aantal_m2_jaar": len(per_m2_jaar),
        "gemiddelde_m2_jaar": (round(sum(per_m2_jaar) / len(per_m2_jaar)) if per_m2_jaar else None),
        "mediaan_m2_jaar": (round(statistics.median(per_m2_jaar)) if per_m2_jaar else None),
        "berekende_jaarhuur_totaal": sum(berekende_waarden) if berekende_waarden else None,
        "berekende_jaarhuur_aantal": len(berekende_waarden),
    }


def bereken_business_koop_kpis(rijen: list[dict]) -> dict:
    """Uitsluitend rijen met een daadwerkelijk numerieke Koopprijs - n.o.t.k.,
    prijs op aanvraag en lege koopprijs tellen niet mee in som/gemiddelde."""
    prijzen = [r["koopprijs"] for r in rijen if r.get("koopprijs") is not None]
    if not prijzen:
        return {"aantal": 0, "totaal": None, "gemiddelde": None, "mediaan": None}
    return {
        "aantal": len(prijzen),
        "totaal": sum(prijzen),
        "gemiddelde": round(sum(prijzen) / len(prijzen)),
        "mediaan": round(statistics.median(prijzen)),
    }


def bouw_business_makelaarstabel(rijen: list[dict]) -> list[dict]:
    """Aandeel = unieke actieve objecten van de makelaar / totaal unieke actieve
    objecten binnen de HUIDIGE selectie (rijen is al gefilterd op status/
    transactietype vóórdat deze functie wordt aangeroepen). Koopwaarde en
    Berekende jaarhuur zijn sommen van uitsluitend betrouwbare brongegevens
    (geldige numerieke Koopprijs resp. bestaande Berekende_huur_per_jaar) -
    er wordt nooit een bedrag verzonnen voor n.o.t.k./op aanvraag/ontbrekende
    data. Marktaandeel m²/koopwaarde/jaarhuur wordt uitsluitend berekend als
    de noemer (totaal binnen de selectie) > 0 is, en is altijd een aandeel
    van uitsluitend de bekende/betrouwbare waarden (DEEL E: dekking wordt
    apart getoond, geen schijnprecisie)."""
    totaal_aanbod = len(rijen)
    totaal_m2 = sum(r["oppervlakte_m2"] for r in rijen if r.get("oppervlakte_m2") is not None)
    totaal_koopwaarde = sum(r["koopprijs"] for r in rijen if r.get("koopprijs") is not None)
    totaal_jaarhuur = sum(r["berekende_huur_per_jaar"] for r in rijen if r.get("berekende_huur_per_jaar") is not None)

    per_makelaar: dict[str, dict] = {}
    for r in rijen:
        naam = (r.get("makelaar") or "").strip() or "Onbekend"
        entry = per_makelaar.setdefault(naam, {
            "makelaar": naam, "aantal": 0, "m2": 0.0,
            "huur": 0, "koop": 0, "koopprijzen": [], "jaarhuren": [],
        })
        entry["aantal"] += 1
        if r.get("oppervlakte_m2") is not None:
            entry["m2"] += r["oppervlakte_m2"]
        if business_is_huur_aanbod(r):
            entry["huur"] += 1
        if business_is_koop_aanbod(r):
            entry["koop"] += 1
        if r.get("koopprijs") is not None:
            entry["koopprijzen"].append(r["koopprijs"])
        if r.get("berekende_huur_per_jaar") is not None:
            entry["jaarhuren"].append(r["berekende_huur_per_jaar"])

    tabel = []
    for entry in per_makelaar.values():
        koopwaarde = sum(entry["koopprijzen"]) if entry["koopprijzen"] else None
        jaarhuur = sum(entry["jaarhuren"]) if entry["jaarhuren"] else None
        tabel.append({
            "makelaar": entry["makelaar"],
            "is_onbekend": entry["makelaar"] == "Onbekend",
            "aantal": entry["aantal"],
            "aandeel_pct": round(entry["aantal"] / totaal_aanbod * 100, 1) if totaal_aanbod else 0,
            "m2_weergave": (f"{entry['m2']:,.0f}".replace(",", ".") + " m²") if entry["m2"] else "-",
            "aandeel_m2_pct": round(entry["m2"] / totaal_m2 * 100, 1) if totaal_m2 else None,
            "huur": entry["huur"],
            "koop": entry["koop"],
            "koopwaarde_weergave": formatteer_bedrag(koopwaarde) if koopwaarde is not None else "-",
            "koopwaarde_dekking": len(entry["koopprijzen"]),
            "aandeel_koopwaarde_pct": (
                round(koopwaarde / totaal_koopwaarde * 100, 1) if koopwaarde and totaal_koopwaarde else None
            ),
            "jaarhuur_weergave": formatteer_bedrag(jaarhuur) if jaarhuur is not None else "-",
            "jaarhuur_dekking": len(entry["jaarhuren"]),
            "aandeel_jaarhuur_pct": (
                round(jaarhuur / totaal_jaarhuur * 100, 1) if jaarhuur and totaal_jaarhuur else None
            ),
        })

    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


BUSINESS_BEDRIJFSRUIMTE_SEGMENTEN = [
    ("< 250 m²", 0, 250),
    ("250-500 m²", 250, 500),
    ("500-1.000 m²", 500, 1000),
    ("1.000-2.500 m²", 1000, 2500),
    ("> 2.500 m²", 2500, None),
]


def _segment_van(waarde: float, grenzen: list[tuple[str, float, float | None]]) -> str | None:
    """Wijst waarde toe aan exact één segment (ondergrens inclusief, bovengrens
    exclusief) - geen overlap, geen gat, zodat elk object in precies één
    segment valt (DEEL F/I harde eis)."""
    for label, ondergrens, bovengrens in grenzen:
        if waarde < ondergrens:
            continue
        if bovengrens is not None and waarde >= bovengrens:
            continue
        return label
    return None


def bereken_business_segmentanalyse(
    rijen: list[dict], categorie: str = "Bedrijfsruimte",
    grenzen: list[tuple[str, float, float | None]] | None = None,
) -> list[dict]:
    """Compacte segmentanalyse naar objectgrootte (DEEL F), initieel voor
    Bedrijfsruimte. Generiek opgezet (categorie + grenzen als parameter) zodat
    Kantoor later eigen segmentgrenzen kan krijgen zonder herbouw - de UI
    roept dit voorlopig alleen voor Bedrijfsruimte aan.

    Alleen objecten van de gevraagde categorie MET een betrouwbaar bekende,
    positieve oppervlakte tellen mee (anders geen segment-toewijzing
    mogelijk). Koopprijs/m² en huurprijs/m² per segment volgen dezelfde
    betrouwbaarheidsregels als de KPI's elders: alleen numerieke koopprijs
    resp. huurprijs_eenheid == per_m2_per_jaar tellen mee, nooit maandhuur."""
    grenzen = grenzen or BUSINESS_BEDRIJFSRUIMTE_SEGMENTEN
    per_segment: dict[str, dict] = {
        label: {"segment": label, "aantal": 0, "m2": 0.0, "huur_aantal": 0, "huur_m2jaar": [],
                "koop_aantal": 0, "koop_m2prijs": []}
        for label, _, _ in grenzen
    }

    for r in rijen:
        if categorie not in business_categorieen_van_rij(r):
            continue
        m2 = r.get("oppervlakte_m2")
        if m2 is None or m2 <= 0:
            continue
        segment = _segment_van(m2, grenzen)
        if segment is None:
            continue
        entry = per_segment[segment]
        entry["aantal"] += 1
        entry["m2"] += m2

        if r.get("huurprijs_eenheid") == "per_m2_per_jaar" and r.get("huurprijs") is not None:
            entry["huur_aantal"] += 1
            entry["huur_m2jaar"].append(r["huurprijs"])

        if r.get("koopprijs") is not None and r["koopprijs"] > 0:
            entry["koop_aantal"] += 1
            entry["koop_m2prijs"].append(r["koopprijs"] / m2)

    resultaat = []
    for label, _, _ in grenzen:
        entry = per_segment[label]
        gem_huur = round(sum(entry["huur_m2jaar"]) / len(entry["huur_m2jaar"])) if entry["huur_m2jaar"] else None
        med_huur = round(statistics.median(entry["huur_m2jaar"])) if entry["huur_m2jaar"] else None
        gem_koop = round(sum(entry["koop_m2prijs"]) / len(entry["koop_m2prijs"])) if entry["koop_m2prijs"] else None
        med_koop = round(statistics.median(entry["koop_m2prijs"])) if entry["koop_m2prijs"] else None
        resultaat.append({
            "segment": label,
            "aantal": entry["aantal"],
            "m2_weergave": (f"{entry['m2']:,.0f}".replace(",", ".") + " m²") if entry["m2"] else "-",
            "huur_aantal": entry["huur_aantal"],
            "gemiddelde_huur_m2jaar": (formatteer_bedrag(gem_huur) + " /m²/jaar") if gem_huur is not None else "-",
            "mediaan_huur_m2jaar": (formatteer_bedrag(med_huur) + " /m²/jaar") if med_huur is not None else "-",
            "koop_aantal": entry["koop_aantal"],
            "gemiddelde_koop_m2": (formatteer_bedrag(gem_koop) + " /m²") if gem_koop is not None else "-",
            "mediaan_koop_m2": (formatteer_bedrag(med_koop) + " /m²") if med_koop is not None else "-",
        })
    return resultaat


def bouw_business_plaatsvergelijking(rijen: list[dict]) -> list[dict]:
    """Compacte vergelijking per plaats (DEEL G) - alleen zinvol te tonen bij
    meerdere geselecteerde plaatsen (bepaalt de template). Zelfde
    betrouwbaarheidsregels als de hoofd-KPI's: huurprijs/m² alleen bij
    per_m2_per_jaar, koopprijs/m² alleen bij bekende koopprijs + oppervlakte."""
    per_plaats: dict[str, dict] = {}
    for r in rijen:
        plaats = (r.get("plaats") or "").strip() or "Onbekend"
        entry = per_plaats.setdefault(plaats, {
            "plaats": plaats, "aantal": 0, "m2": 0.0, "huur": 0, "koop": 0,
            "huur_m2jaar": [], "koop_m2prijs": [], "makelaars": set(),
        })
        entry["aantal"] += 1
        if r.get("oppervlakte_m2") is not None:
            entry["m2"] += r["oppervlakte_m2"]
        if business_is_huur_aanbod(r):
            entry["huur"] += 1
        if business_is_koop_aanbod(r):
            entry["koop"] += 1
        if r.get("huurprijs_eenheid") == "per_m2_per_jaar" and r.get("huurprijs") is not None:
            entry["huur_m2jaar"].append(r["huurprijs"])
        if r.get("koopprijs") is not None and r.get("oppervlakte_m2"):
            entry["koop_m2prijs"].append(r["koopprijs"] / r["oppervlakte_m2"])
        makelaar = (r.get("makelaar") or "").strip()
        if makelaar:
            entry["makelaars"].add(makelaar)

    tabel = []
    for entry in per_plaats.values():
        gem_huur = round(sum(entry["huur_m2jaar"]) / len(entry["huur_m2jaar"])) if entry["huur_m2jaar"] else None
        med_huur = round(statistics.median(entry["huur_m2jaar"])) if entry["huur_m2jaar"] else None
        gem_koop = round(sum(entry["koop_m2prijs"]) / len(entry["koop_m2prijs"])) if entry["koop_m2prijs"] else None
        med_koop = round(statistics.median(entry["koop_m2prijs"])) if entry["koop_m2prijs"] else None
        tabel.append({
            "plaats": entry["plaats"],
            "aantal": entry["aantal"],
            "m2_weergave": (f"{entry['m2']:,.0f}".replace(",", ".") + " m²") if entry["m2"] else "-",
            "huur": entry["huur"],
            "koop": entry["koop"],
            "gemiddelde_huur_m2jaar": (formatteer_bedrag(gem_huur) + " /m²/jaar") if gem_huur is not None else "-",
            "mediaan_huur_m2jaar": (formatteer_bedrag(med_huur) + " /m²/jaar") if med_huur is not None else "-",
            "gemiddelde_koop_m2": (formatteer_bedrag(gem_koop) + " /m²") if gem_koop is not None else "-",
            "mediaan_koop_m2": (formatteer_bedrag(med_koop) + " /m²") if med_koop is not None else "-",
            "aantal_makelaars": len(entry["makelaars"]),
        })
    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


def bereken_dagen_in_monitor_stats(rijen: list[dict], veld: str = "dagen_in_monitor") -> dict:
    """Gemiddelde/mediaan 'dagen in monitor' over objecten waarvoor dit
    betrouwbaar bekend is (verrijkte rijen - veld is een int of '-'). Gedeeld
    tussen Woningen en Business (DEEL H/marktdynamiek)."""
    waarden = [r[veld] for r in rijen if isinstance(r.get(veld), int)]
    if not waarden:
        return {"aantal": 0, "gemiddelde": None, "mediaan": None}
    return {
        "aantal": len(waarden),
        "gemiddelde": round(sum(waarden) / len(waarden)),
        "mediaan": round(statistics.median(waarden)),
    }


def business_huurprijs_weergave(rij: dict) -> str:
    """Toont de brontarief + eenheid zoals Funda het vermeldt, nooit een
    omgerekend totaalbedrag. Gedeeld tussen de objectentabel en de
    mutatiedetails, zodat beide exact dezelfde weergave gebruiken."""
    if rij.get("huurprijs") is not None:
        eenheid_label = BUSINESS_HUUREENHEID_LABELS.get(rij.get("huurprijs_eenheid") or "", "")
        return (formatteer_bedrag(rij["huurprijs"]) + " " + eenheid_label).strip()
    if (rij.get("huurprijs_eenheid") or "") == "op_aanvraag":
        return "Op aanvraag"
    return "-"


def business_koopprijs_weergave(rij: dict) -> str:
    """Combineert Koopprijs + Koopprijs_conditie in één leesbare cel (bv.
    '€ 895.000 k.k.'). Bij een ontbrekende prijs maar wel een koopindicatie
    (koopprijs_conditie of 'n.o.t.k.' in de waarschuwing) wordt dat getoond
    zonder een bedrag te verzinnen."""
    if rij.get("koopprijs") is not None:
        conditie = (rij.get("koopprijs_conditie") or "").strip()
        return (formatteer_bedrag(rij["koopprijs"]) + (" " + conditie if conditie else "")).strip()
    if (rij.get("koopprijs_conditie") or "").strip():
        return rij["koopprijs_conditie"].strip()
    if "n.o.t.k" in (rij.get("waarschuwing") or "").lower():
        return "n.o.t.k."
    return "-"


def business_type_weergave(rij: dict) -> str:
    """Compacte type-weergave die Objecttype en gezochte categorie combineert
    zonder dubbele tekst: als de categorie al (deels) in het objecttype
    voorkomt, wordt alleen het specifiekere objecttype getoond."""
    objecttype = (rij.get("objecttype") or "").strip().replace("|", " / ")
    categorie = (rij.get("gezocht_categorie") or "").strip()
    if not objecttype:
        return categorie or "-"
    if not categorie:
        return objecttype
    if categorie.lower() in objecttype.lower() or objecttype.lower() in categorie.lower():
        return objecttype
    return f"{objecttype} · {categorie}"


def verrijk_business_rij(rij: dict, geschiedenis: dict[str, tuple]) -> dict:
    rij = dict(rij)
    eerste, laatste = geschiedenis.get(rij.get("funda_object_id"), (None, None))
    rij["eerste_waarneming"] = formatteer_scanmoment(eerste) if eerste else "-"
    rij["laatste_waarneming"] = formatteer_scanmoment(laatste) if laatste else "-"

    if eerste and laatste:
        try:
            d1 = datetime.fromisoformat(eerste).date()
            d2 = datetime.fromisoformat(laatste).date()
            rij["dagen_in_monitor"] = (d2 - d1).days
        except ValueError:
            rij["dagen_in_monitor"] = "-"
    else:
        rij["dagen_in_monitor"] = "-"

    rij["dagen_in_monitor_weergave"] = (
        f"{rij['dagen_in_monitor']} dagen" if isinstance(rij["dagen_in_monitor"], int) else "-"
    )
    if rij["eerste_waarneming"] != "-" or rij["laatste_waarneming"] != "-":
        rij["waarneming_tooltip"] = (
            f"Eerste waarneming: {rij['eerste_waarneming']} · Laatste waarneming: {rij['laatste_waarneming']}"
        )
    else:
        rij["waarneming_tooltip"] = ""

    rij["koopprijs_weergave"] = (
        formatteer_bedrag(rij["koopprijs"]) if rij.get("koopprijs") is not None else "-"
    )
    rij["koopprijs_gecombineerd"] = business_koopprijs_weergave(rij)
    rij["huurprijs_weergave"] = business_huurprijs_weergave(rij)
    rij["type_weergave"] = business_type_weergave(rij)

    rij["berekende_jaarhuur_weergave"] = (
        formatteer_bedrag(rij["berekende_huur_per_jaar"]) if rij.get("berekende_huur_per_jaar") is not None else "-"
    )
    rij["oppervlakte_weergave"] = (
        (f"{rij['oppervlakte_m2']:,.0f}".replace(",", ".") + " m²") if rij.get("oppervlakte_m2") is not None else "-"
    )
    rij["koopprijs_conditie_weergave"] = (rij.get("koopprijs_conditie") or "-")
    return rij


def business_mutatie_naam(oud: dict | None, nieuw: dict | None) -> str:
    """Alleen de brongegevens tellen mee (geen Berekende_huur_per_*)."""
    if oud is None:
        return "Nieuw aanbod"
    if nieuw is None:
        return "Uit aanbod"

    wijzigingen = []
    if oud.get("koopprijs") != nieuw.get("koopprijs"):
        wijzigingen.append("Koopprijs gewijzigd")
    if oud.get("huurprijs") != nieuw.get("huurprijs"):
        wijzigingen.append("Huurprijs gewijzigd")
    if (oud.get("huurprijs_eenheid") or "") != (nieuw.get("huurprijs_eenheid") or ""):
        wijzigingen.append("Huurprijs-eenheid gewijzigd")
    if (oud.get("status") or "") != (nieuw.get("status") or ""):
        wijzigingen.append("Status gewijzigd")
    if (oud.get("makelaar") or "") != (nieuw.get("makelaar") or ""):
        wijzigingen.append("Makelaar gewijzigd")
    if oud.get("oppervlakte_m2") != nieuw.get("oppervlakte_m2"):
        wijzigingen.append("Oppervlakte gewijzigd")

    return " + ".join(wijzigingen) if wijzigingen else "Ongewijzigd"


def business_mutatie_details(oud: dict, nieuw: dict) -> list[str]:
    """Bouwt 'oud → nieuw'-regels voor de velden die daadwerkelijk wijzigden,
    uitsluitend wanneer zowel de oude als de nieuwe snapshot betrouwbaar
    aanwezig zijn (dus nooit voor 'Nieuw aanbod'/'Uit aanbod' - daar is er
    per definitie maar één kant van de vergelijking)."""
    details = []
    if oud.get("koopprijs") != nieuw.get("koopprijs"):
        oud_w = business_koopprijs_weergave(oud)
        nieuw_w = business_koopprijs_weergave(nieuw)
        details.append(f"Koopprijs: {oud_w} → {nieuw_w}")
    if (oud.get("huurprijs") != nieuw.get("huurprijs")
            or (oud.get("huurprijs_eenheid") or "") != (nieuw.get("huurprijs_eenheid") or "")):
        oud_w = business_huurprijs_weergave(oud)
        nieuw_w = business_huurprijs_weergave(nieuw)
        details.append(f"Huurprijs: {oud_w} → {nieuw_w}")
    if (oud.get("status") or "") != (nieuw.get("status") or ""):
        details.append(f"Status: {oud.get('status') or '-'} → {nieuw.get('status') or '-'}")
    if (oud.get("makelaar") or "") != (nieuw.get("makelaar") or ""):
        details.append(f"Makelaar: {oud.get('makelaar') or '-'} → {nieuw.get('makelaar') or '-'}")
    if oud.get("oppervlakte_m2") != nieuw.get("oppervlakte_m2"):
        oud_w = f"{oud['oppervlakte_m2']:,.0f} m²".replace(",", ".") if oud.get("oppervlakte_m2") is not None else "-"
        nieuw_w = f"{nieuw['oppervlakte_m2']:,.0f} m²".replace(",", ".") if nieuw.get("oppervlakte_m2") is not None else "-"
        details.append(f"Oppervlakte: {oud_w} → {nieuw_w}")
    return details


def business_bouw_mutaties(vorige_snapshot: dict, huidige_snapshot: dict):
    """Vergelijkt de VOLLEDIGE (ongefilterde) snapshots van twee scans - niet
    de door status/transactietype gefilterde weergave-rijen. Mutaties zijn een
    objectief scanvergelijking, geen weergavefilter. 'Uit aanbod' betekent
    uitsluitend: niet meer aangetroffen; nooit 'verkocht'/'verhuurd'."""
    ids = sorted(set(vorige_snapshot) | set(huidige_snapshot))
    aantallen: dict[str, int] = {}
    gewijzigd = []

    for oid in ids:
        oud = vorige_snapshot.get(oid)
        nieuw = huidige_snapshot.get(oid)
        naam = business_mutatie_naam(oud, nieuw)
        aantallen[naam] = aantallen.get(naam, 0) + 1
        if naam != "Ongewijzigd":
            bron = nieuw or oud
            details = business_mutatie_details(oud, nieuw) if (oud is not None and nieuw is not None) else []
            gewijzigd.append({
                "mutatie": naam,
                "adres": bron.get("adres"),
                "plaats": bron.get("plaats"),
                "funda_object_id": oid,
                "funda_url": bron.get("funda_url"),
                "details": details,
            })

    return aantallen, gewijzigd


def bouw_business_analyseresultaat(args) -> dict:
    """Eén read-only pipeline voor de Business-resultaatpagina: exacte
    scanselectie op gebied+categorieen, filtering, KPI's, makelaarstabel en
    mutaties t.o.v. de vorige exact vergelijkbare scan."""
    plaatsen = [p for p in args.getlist("business_plaats") if p.strip()]
    categorieen = [c for c in args.getlist("business_categorie") if c in BUSINESS_CATEGORIEEN]
    if not categorieen:
        categorieen = list(BUSINESS_CATEGORIE_STANDAARD)
    statussen = args.getlist("business_status")
    transactietype = args.get("business_transactietype") or BUSINESS_TRANSACTIETYPE_STANDAARD
    if transactietype not in BUSINESS_TRANSACTIETYPES:
        transactietype = BUSINESS_TRANSACTIETYPE_STANDAARD

    basis = {
        "plaatsen": plaatsen,
        "categorieen": categorieen,
        "statussen": statussen,
        "transactietype": transactietype,
        "foutmelding": None,
        "scaninfo": None,
        "rijen": [],
        # RUWE rijen (numeriek koopprijs/huurprijs/oppervlakte_m2, geen
        # presentatiestrings) - gebruik dit voor berekeningen zoals het
        # makelaarsprofiel, nooit basis["rijen"].
        "ruwe_rijen": [],
        "kpis": None,
        "huur_kpis": None,
        "koop_kpis": None,
        "makelaarstabel": [],
        "segmentanalyse_bedrijfsruimte": [],
        "plaatsvergelijking": [],
        "dagen_in_monitor_stats": {"aantal": 0, "gemiddelde": None, "mediaan": None},
        "mutatie_aantallen": {},
        "mutatie_rijen": [],
        "vorige_scanmoment_weergave": None,
        "vorige_aantal_objecten": None,
        "netto_verandering": None,
    }

    if not plaatsen:
        basis["foutmelding"] = "Selecteer minimaal één regio."
        return basis

    if not BUSINESS_DB_PATH.exists():
        basis["foutmelding"] = f"Business-database niet gevonden op: {BUSINESS_DB_PATH}"
        return basis

    gebied = business_canonical(plaatsen)
    categorieen_canoniek = business_canonical(categorieen)

    con = get_business_readonly_connection()
    try:
        scan_id, scanmoment = haal_business_scan_id(con, gebied, categorieen_canoniek)
        if scan_id is None:
            basis["foutmelding"] = (
                "Voor deze combinatie van regio en categorieën is nog geen Business-scan beschikbaar."
            )
            return basis

        huidige_snapshot = haal_business_snapshot(con, scan_id)
        ruwe_rijen = list(huidige_snapshot.values())

        basis["scaninfo"] = {
            "scan_id": scan_id,
            "gebied": gebied,
            "categorieen": categorieen_canoniek,
            "scanmoment_weergave": formatteer_scanmoment(scanmoment),
            # Ongefilterd totaal (niet de status-/transactietype-gefilterde
            # kpis.actief_aanbod) - consistent met vorige_aantal_objecten/
            # netto_verandering hieronder, die ook op het volledige snapshot
            # zijn gebaseerd (DEEL H).
            "aantal_objecten": len(huidige_snapshot),
        }

        if statussen:
            ruwe_rijen = [r for r in ruwe_rijen if r.get("status") in statussen]

        ruwe_rijen = business_pas_transactietype_toe(ruwe_rijen, transactietype)
        basis["ruwe_rijen"] = ruwe_rijen

        basis["kpis"] = bereken_business_kpis(ruwe_rijen)
        basis["huur_kpis"] = bereken_business_huur_kpis(ruwe_rijen)
        basis["koop_kpis"] = bereken_business_koop_kpis(ruwe_rijen)
        basis["makelaarstabel"] = bouw_business_makelaarstabel(ruwe_rijen)
        basis["segmentanalyse_bedrijfsruimte"] = bereken_business_segmentanalyse(ruwe_rijen, "Bedrijfsruimte")
        basis["plaatsvergelijking"] = bouw_business_plaatsvergelijking(ruwe_rijen)

        geschiedenis = haal_business_geschiedenis(
            con, gebied, categorieen_canoniek, [r["funda_object_id"] for r in ruwe_rijen]
        )
        basis["rijen"] = sorted(
            (verrijk_business_rij(r, geschiedenis) for r in ruwe_rijen),
            key=lambda r: ((r.get("plaats") or ""), (r.get("adres") or "")),
        )
        basis["dagen_in_monitor_stats"] = bereken_dagen_in_monitor_stats(basis["rijen"])

        vorige_scan_id = haal_business_vorige_scan_id(con, gebied, categorieen_canoniek, scanmoment, scan_id)
        if vorige_scan_id:
            vorige_snapshot = haal_business_snapshot(con, vorige_scan_id)
            aantallen, mutatie_rijen = business_bouw_mutaties(vorige_snapshot, huidige_snapshot)
            basis["mutatie_aantallen"] = aantallen
            basis["mutatie_rijen"] = mutatie_rijen
            vorige_rij = con.execute(
                "SELECT scanmoment, aantal_objecten FROM business_scans WHERE scan_id = ?", (vorige_scan_id,)
            ).fetchone()
            if vorige_rij:
                basis["vorige_scanmoment_weergave"] = formatteer_scanmoment(vorige_rij["scanmoment"])
                basis["vorige_aantal_objecten"] = vorige_rij["aantal_objecten"]
                basis["netto_verandering"] = len(huidige_snapshot) - vorige_rij["aantal_objecten"]

        return basis
    finally:
        con.close()


def _vind_nieuwste_alles_csv(sinds: datetime) -> Path | None:
    """Zoekt het *_alles.csv-bestand dat bij deze scan hoort: het meest recente
    bestand met dat patroon in output/, aangemaakt na de start van deze scan."""
    grens = sinds.timestamp() - 2
    kandidaten = sorted(
        OUTPUT_DIR.glob("makelaarsmonitor_*_alles.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for pad in kandidaten:
        if pad.stat().st_mtime >= grens:
            return pad
    return None


def _tel_csv_rijen(csv_pad: Path) -> int:
    with csv_pad.open("r", encoding="utf-8-sig", newline="") as f:
        aantal_regels = sum(1 for _ in csv.reader(f, delimiter=";"))
    return max(0, aantal_regels - 1)


def _laatste_zinvolle_regel(tekst: str | None) -> str | None:
    """Pakt de laatste niet-lege regel uit scanner-/historie-uitvoer als
    beknopte foutreden. Bevat de uitvoer een Python-traceback, dan geven we
    niets terug (die tonen we nooit rechtstreeks aan de gebruiker)."""
    if not tekst:
        return None
    if "traceback (most recent call last)" in tekst.lower():
        return None
    regels = [r.strip() for r in tekst.splitlines() if r.strip()]
    return regels[-1] if regels else None


def _importeer_in_historie(csv_pad: Path, regios: list[str]) -> tuple[bool, str]:
    """Roept de bestaande historie-tool aan als losse Python-subprocess.
    Niet-interactief, dus stdout/stderr worden hier wel afgevangen."""
    cmd = [
        sys.executable, str(HISTORIE_PAD),
        "--scan", str(csv_pad),
        "--plaatsen", *regios,
        "--database", str(DB_PATH),
        "--output-map", str(OUTPUT_DIR),
    ]
    try:
        resultaat = subprocess.run(
            cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return False, "Historie-import duurde te lang en is afgebroken."
    except OSError as exc:
        return False, f"Historie-import kon niet worden gestart: {exc}"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpad = OUTPUT_DIR / f"_historie_import_log_{stamp}.txt"
    try:
        logpad.write_text((resultaat.stdout or "") + "\n" + (resultaat.stderr or ""), encoding="utf-8")
    except OSError:
        pass

    if resultaat.returncode != 0:
        reden = _laatste_zinvolle_regel(resultaat.stderr) or _laatste_zinvolle_regel(resultaat.stdout)
        return False, reden or f"Historie-import is mislukt (zie {logpad.name} in output/)."

    return True, "Historie succesvol bijgewerkt."


def _voer_scan_uit(regios: list[str], gestart_om: datetime) -> None:
    """Draait in een achtergrondthread: start de bestaande scanner, wacht op
    het resultaat (onderbreekbaar via SCAN_STATE["stop_aangevraagd"] - zie
    DEEL B) en importeert dit daarna in de historie-database. Analoog aan
    _voer_business_scan_uit(), maar met de eigen woningen-state/output/
    database - alleen SCAN_RUNNING_LOCK is gedeeld met Business."""
    global SCAN_ACTIEVE_PROCES
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Voorkomt dat een stopvlag van een eerdere, al afgehandelde scan
        # deze NIEUWE scan per ongeluk direct zou laten stoppen.
        _verwijder_stop_flag(SCAN_STOP_FLAG)

        cmd = [
            sys.executable, str(SCANNER_PAD),
            "--plaatsen", *regios,
            "--output-map", str(OUTPUT_DIR),
        ]

        # Geen stdin/stdout/stderr-omleiding: de scanner opent zelf een
        # zichtbaar Chrome-venster en kan een menscontrole tonen (niet-
        # blokkerend opgelost in het Chrome-venster zelf - zie DEEL A). Door
        # niets om te leiden, blijft dit gekoppeld aan hetzelfde console-
        # venster als waarin "py app.py" draait.
        try:
            proces = subprocess.Popen(cmd, cwd=str(BASE_DIR))
        except OSError as exc:
            with STATE_LOCK:
                SCAN_STATE.update(
                    status="error",
                    foutmelding=f"De scanner kon niet worden gestart: {exc}",
                )
            return

        with STATE_LOCK:
            SCAN_STATE["proces_id"] = proces.pid
            SCAN_ACTIEVE_PROCES = proces

        returncode = _wacht_op_woningen_proces(proces)

        with STATE_LOCK:
            SCAN_ACTIEVE_PROCES = None
            gestopt_door_gebruiker = SCAN_STATE.get("stop_aangevraagd", False)

        _verwijder_stop_flag(SCAN_STOP_FLAG)

        if gestopt_door_gebruiker:
            # Harde eis: bij een door de gebruiker afgebroken scan NOOIT de
            # CSV opzoeken of historie-import starten, ongeacht de
            # afsluitcode van het (mogelijk geforceerd beëindigde) proces.
            with STATE_LOCK:
                SCAN_STATE.update(
                    status="gestopt",
                    foutmelding=None,
                    eind_om_weergave=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                )
            return

        if returncode != 0:
            with STATE_LOCK:
                SCAN_STATE.update(
                    status="error",
                    foutmelding=(
                        f"De scanner is gestopt met afsluitcode {returncode}. "
                        "Controleer het consolevenster waarin de webapp is gestart voor details."
                    ),
                )
            return

        csv_pad = _vind_nieuwste_alles_csv(gestart_om)
        if csv_pad is None:
            with STATE_LOCK:
                SCAN_STATE.update(
                    status="error",
                    foutmelding="De scan is voltooid, maar er is geen *_alles.csv-bestand gevonden in output/.",
                )
            return

        try:
            aantal_objecten = _tel_csv_rijen(csv_pad)
        except OSError:
            aantal_objecten = None

        historie_ok, historie_melding = _importeer_in_historie(csv_pad, regios)

        with STATE_LOCK:
            SCAN_STATE.update(
                status="success",
                csv_naam=csv_pad.name,
                aantal_objecten=aantal_objecten,
                scanmoment_weergave=datetime.now().strftime("%d-%m-%Y %H:%M"),
                historie_ok=historie_ok,
                historie_melding=historie_melding,
            )
    except Exception:
        with STATE_LOCK:
            SCAN_ACTIEVE_PROCES = None
            SCAN_STATE.update(
                status="error",
                foutmelding=(
                    "Onverwachte fout tijdens het scannen. Controleer het "
                    "consolevenster waarin de webapp is gestart voor details."
                ),
            )
    finally:
        SCAN_RUNNING_LOCK.release()


def _vind_nieuwste_business_alles_csv(sinds: datetime) -> Path | None:
    """Analoog aan _vind_nieuwste_alles_csv() voor woningen, maar voor het
    Business-scanner bestandspatroon in output/bedrijfsmatig/. De glob-
    patroon sluit *_checkpoint.csv al vanzelf uit (dat heet nooit "_alles.csv"),
    en de mtime-grens voorkomt dat per ongeluk een oudere *_alles.csv van een
    eerdere scan wordt geimporteerd."""
    grens = sinds.timestamp() - 2
    kandidaten = sorted(
        BUSINESS_OUTPUT_DIR.glob("funda_business_*_alles.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for pad in kandidaten:
        if pad.stat().st_mtime >= grens:
            return pad
    return None


def _parse_business_historie_samenvatting(stdout: str) -> dict:
    """Best-effort, niet-kritieke afleiding van een korte samenvatting uit de
    tekstuitvoer van historie/funda_business_historie_v1.py (zie het eigen
    print()-format daar). Faalt nooit hard: bij een onverwacht/gewijzigd
    format blijft dit gewoon een lege dict - de webapp verzint zelf geen
    cijfers en de eigenlijke analyse leest sowieso live uit de database."""
    samenvatting: dict = {}
    try:
        m = re.search(r"Objecten geïmporteerd:\s*(\d+)", stdout)
        if m:
            samenvatting["objecten_geimporteerd"] = int(m.group(1))

        if "Dit is de eerste opgeslagen scan (nulmeting)" in stdout:
            samenvatting["nulmeting"] = True
        elif "Mutaties t.o.v. vorige vergelijkbare scan:" in stdout:
            samenvatting["nulmeting"] = False
            mutaties = {}
            na_kop = False
            for regel in stdout.splitlines():
                if "Mutaties t.o.v. vorige vergelijkbare scan:" in regel:
                    na_kop = True
                    continue
                if na_kop:
                    m2 = re.match(r"\s{2}(.+?):\s*(\d+)\s*$", regel)
                    if m2:
                        mutaties[m2.group(1)] = int(m2.group(2))
                    elif regel.strip() == "":
                        break
            if mutaties:
                samenvatting["mutaties"] = mutaties
    except Exception:
        return {}
    return samenvatting


def _importeer_in_business_historie(csv_pad: Path, plaatsen: list[str], categorieen: list[str]) -> tuple[bool, str, dict]:
    """Roept de bestaande Business-historie-tool aan als losse subprocess,
    met EXACT dezelfde plaatsen/categorieen als bij de scan (vereist voor de
    exacte scanidentiteit die de historie-tool gebruikt). Analoog aan
    _importeer_in_historie() voor woningen, maar met de Business-database/
    -outputmap. Niet-interactief, dus stdout/stderr worden hier afgevangen."""
    cmd = [
        sys.executable, str(BUSINESS_HISTORIE_PAD),
        "--scan", str(csv_pad),
        "--plaatsen", *plaatsen,
        "--categorieen", *categorieen,
        "--database", str(BUSINESS_DB_PATH),
        "--output-map", str(BUSINESS_HISTORIE_OUTPUT_DIR),
    ]
    try:
        resultaat = subprocess.run(
            cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        return False, "Business-historie-import duurde te lang en is afgebroken.", {}
    except OSError as exc:
        return False, f"Business-historie-import kon niet worden gestart: {exc}", {}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logpad = BUSINESS_OUTPUT_DIR / f"_business_historie_import_log_{stamp}.txt"
    try:
        logpad.write_text((resultaat.stdout or "") + "\n" + (resultaat.stderr or ""), encoding="utf-8")
    except OSError:
        pass

    if resultaat.returncode != 0:
        reden = _laatste_zinvolle_regel(resultaat.stderr) or _laatste_zinvolle_regel(resultaat.stdout)
        return False, reden or f"Business-historie-import is mislukt (zie {logpad.name} in output/bedrijfsmatig/).", {}

    samenvatting = _parse_business_historie_samenvatting(resultaat.stdout or "")
    return True, "Business-historie succesvol bijgewerkt.", samenvatting


def _verwijder_stop_flag(pad: Path) -> None:
    try:
        pad.unlink(missing_ok=True)
    except OSError:
        pass


def _forceer_procesboom_stop(pid: int) -> None:
    """Laatste redmiddel bij het afbreken van een scan (Woningen of
    Business): Windows-veilige, geforceerde beëindiging van uitsluitend DIT
    ene PID + zijn kindprocessen (o.a. het Chrome-proces dat Playwright apart
    start en dat bij het enkel doden van de Python-ouder anders wees zou
    blijven draaien). Gebruikt bewust `taskkill /PID <pid> /T /F` - scoped
    tot deze ene procesboom, NOOIT een generieke 'kill alle Chrome.exe'-
    aanpak die andere Chrome-sessies van de gebruiker zou kunnen raken."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


def _wacht_op_scan_proces(proces: subprocess.Popen, state: dict, state_lock: threading.Lock) -> int:
    """Generieke wachtlus voor een actief scanner-subprocess (Woningen of
    Business - zie DEEL B), zodat beide scantypes precies dezelfde,
    goedgekeurde stop-escalatielogica delen in plaats van twee losse kopieën.
    Blijft ondertussen elke seconde controleren of de gebruiker intussen een
    stop heeft aangevraagd via de bijbehorende state-dict. Voorkeursvolgorde
    bij een stopverzoek: (1) coöperatief - de scanner zelf ziet het
    stopvlag-bestand en sluit Playwright/Chrome netjes af; (2) een nette
    terminate(); (3) pas als laatste redmiddel een geforceerde procesboom-
    kill van dit ene PID."""
    COOPERATIEVE_WACHTTIJD_S = 15
    TERMINATE_WACHTTIJD_S = 5

    while True:
        try:
            return proces.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

        with state_lock:
            stop_gevraagd = state.get("stop_aangevraagd", False)
        if not stop_gevraagd:
            continue

        try:
            return proces.wait(timeout=COOPERATIEVE_WACHTTIJD_S)
        except subprocess.TimeoutExpired:
            pass

        try:
            proces.terminate()
        except Exception:
            pass
        try:
            return proces.wait(timeout=TERMINATE_WACHTTIJD_S)
        except subprocess.TimeoutExpired:
            pass

        _forceer_procesboom_stop(proces.pid)
        try:
            return proces.wait(timeout=10)
        except subprocess.TimeoutExpired:
            resultaat = proces.poll()
            return resultaat if resultaat is not None else -1


def _wacht_op_business_proces(proces: subprocess.Popen) -> int:
    return _wacht_op_scan_proces(proces, BUSINESS_SCAN_STATE, BUSINESS_STATE_LOCK)


def _wacht_op_woningen_proces(proces: subprocess.Popen) -> int:
    return _wacht_op_scan_proces(proces, SCAN_STATE, STATE_LOCK)


def _voer_business_scan_uit(
    plaatsen: list[str], categorieen: list[str], statussen: list[str],
    transactietype: str, gestart_om: datetime,
) -> None:
    """Draait in een achtergrondthread: start de bestaande Business-scanner,
    wacht op het resultaat (onderbreekbaar via BUSINESS_SCAN_STATE
    ["stop_aangevraagd"]) en importeert dit daarna via de bestaande
    Business-historie-tool. Analoog aan _voer_scan_uit() voor woningen, maar
    volledig gescheiden state/output/database - alleen SCAN_RUNNING_LOCK
    (hierboven) is gedeeld met woningen."""
    global BUSINESS_ACTIEVE_PROCES
    try:
        BUSINESS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Voorkomt dat een stopvlag van een eerdere, al afgehandelde scan
        # deze NIEUWE scan per ongeluk direct zou laten stoppen.
        _verwijder_stop_flag(BUSINESS_SCAN_STOP_FLAG)

        cmd = [
            sys.executable, str(BUSINESS_SCANNER_PAD),
            "--plaatsen", *plaatsen,
            "--objecttypes", *categorieen,
            "--output-map", str(BUSINESS_OUTPUT_DIR),
        ]

        # Zelfde reden als bij de woningenscanner: geen stdin/stdout/stderr-
        # omleiding, zodat de zichtbare Chrome + interactieve menscontrole-
        # flow in hetzelfde consolevenster als "py app.py" blijft werken.
        # Geen shell=True, geen headless-conversie.
        try:
            proces = subprocess.Popen(cmd, cwd=str(BASE_DIR))
        except OSError as exc:
            with BUSINESS_STATE_LOCK:
                BUSINESS_SCAN_STATE.update(
                    status="error",
                    foutmelding=f"De Business-scanner kon niet worden gestart: {exc}",
                )
            return

        with BUSINESS_STATE_LOCK:
            BUSINESS_SCAN_STATE["proces_id"] = proces.pid
            BUSINESS_ACTIEVE_PROCES = proces

        returncode = _wacht_op_business_proces(proces)

        with BUSINESS_STATE_LOCK:
            BUSINESS_ACTIEVE_PROCES = None
            gestopt_door_gebruiker = BUSINESS_SCAN_STATE.get("stop_aangevraagd", False)

        _verwijder_stop_flag(BUSINESS_SCAN_STOP_FLAG)

        if gestopt_door_gebruiker:
            # Harde eis: bij een door de gebruiker afgebroken scan NOOIT de
            # CSV opzoeken of historie-import starten, ongeacht de
            # afsluitcode van het (mogelijk geforceerd beëindigde) proces.
            with BUSINESS_STATE_LOCK:
                BUSINESS_SCAN_STATE.update(
                    status="gestopt",
                    foutmelding=None,
                    eind_om_weergave=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                )
            return

        if returncode != 0:
            with BUSINESS_STATE_LOCK:
                BUSINESS_SCAN_STATE.update(
                    status="error",
                    foutmelding=(
                        f"De Business-scanner is gestopt met afsluitcode {returncode}. "
                        "Controleer het consolevenster waarin de webapp is gestart voor details."
                    ),
                )
            return

        csv_pad = _vind_nieuwste_business_alles_csv(gestart_om)
        if csv_pad is None:
            with BUSINESS_STATE_LOCK:
                BUSINESS_SCAN_STATE.update(
                    status="error",
                    foutmelding=(
                        "De scan is voltooid, maar er is geen nieuw "
                        "funda_business_*_alles.csv-bestand gevonden in output/bedrijfsmatig/."
                    ),
                )
            return

        try:
            aantal_objecten = _tel_csv_rijen(csv_pad)
        except OSError:
            aantal_objecten = None

        with BUSINESS_STATE_LOCK:
            BUSINESS_SCAN_STATE.update(
                status="importing",
                csv_naam=csv_pad.name,
                aantal_objecten=aantal_objecten,
            )

        historie_ok, historie_melding, mutatie_samenvatting = _importeer_in_business_historie(
            csv_pad, plaatsen, categorieen
        )

        with BUSINESS_STATE_LOCK:
            BUSINESS_SCAN_STATE.update(
                status="success" if historie_ok else "error",
                eind_om_weergave=datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
                historie_ok=historie_ok,
                historie_melding=historie_melding,
                mutatie_samenvatting=mutatie_samenvatting,
                foutmelding=None if historie_ok else historie_melding,
            )
    except Exception:
        with BUSINESS_STATE_LOCK:
            BUSINESS_ACTIEVE_PROCES = None
            BUSINESS_SCAN_STATE.update(
                status="error",
                foutmelding=(
                    "Onverwachte fout tijdens de Business-scan. Controleer het "
                    "consolevenster waarin de webapp is gestart voor details."
                ),
            )
    finally:
        SCAN_RUNNING_LOCK.release()


def haal_laatste_scan_samenvatting() -> dict | None:
    """Eenvoudige 'laatste scan'-snelkoppeling voor het hoofdscherm: de meest
    recente woningen-scan, ongeacht regio (bouw_analyseresultaat() gebruikt
    toch altijd de meest recente scan, dus dit is exact wat 'Bekijk analyse'
    al zou laten zien). Geen nieuwe state-opslag - rechtstreeks uit
    scans.gebied/aantal_objecten."""
    if not DB_PATH.exists():
        return None
    try:
        con = get_readonly_connection()
        try:
            row = con.execute(
                "SELECT gebied, scanmoment, aantal_objecten FROM scans "
                "ORDER BY scanmoment DESC, scan_id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return {
                "plaatsen": [p.strip() for p in (row["gebied"] or "").split("|") if p.strip()],
                "scanmoment_weergave": formatteer_scanmoment(row["scanmoment"]),
                "aantal_objecten": row["aantal_objecten"],
            }
        finally:
            con.close()
    except sqlite3.Error:
        return None


def haal_laatste_business_scan_samenvatting() -> dict | None:
    """Analoog aan haal_laatste_scan_samenvatting(), maar voor de losstaande
    Business-database: simpelweg de meest recente scan ongeacht gebied/
    categorieen-combinatie, uitsluitend als snelkoppeling op het hoofdscherm.
    De analysepagina zelf blijft de exacte gebied+categorieen-matching
    gebruiken (business_analyse), hier alleen ter informatie/doorlinken."""
    if not BUSINESS_DB_PATH.exists():
        return None
    try:
        con = get_business_readonly_connection()
        try:
            row = con.execute(
                "SELECT gebied, categorieen, scanmoment, aantal_objecten FROM business_scans "
                "ORDER BY scanmoment DESC, scan_id DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            return {
                "plaatsen": [p.strip() for p in (row["gebied"] or "").split("|") if p.strip()],
                "categorieen": [c.strip() for c in (row["categorieen"] or "").split("|") if c.strip()],
                "scanmoment_weergave": formatteer_scanmoment(row["scanmoment"]),
                "aantal_objecten": row["aantal_objecten"],
            }
        finally:
            con.close()
    except sqlite3.Error:
        return None


@app.route("/")
def index():
    fout = request.args.get("fout")
    tab_param = request.args.get("tab")
    if tab_param in ("woningen", "bedrijfsmatig"):
        actieve_tab = tab_param
    elif (fout or "").startswith("business_"):
        actieve_tab = "bedrijfsmatig"
    else:
        actieve_tab = "woningen"

    # UX-harmonisatie: filterstate behouden via queryparameters. De
    # bestaande "*_standaard"-context-variabelen bepalen al welke
    # checkboxen aangevinkt zijn (zie index.html) - door hier de
    # binnenkomende queryparameters te gebruiken (met de oorspronkelijke
    # standaardwaarden als fallback) werkt "terug naar filters" met behoud
    # van selectie zonder dat de template zelf hoeft te veranderen.
    geselecteerde_plaatsen = request.args.getlist("plaats") or list(REGIO_STANDAARD)
    geselecteerde_statussen = request.args.getlist("status") or list(STATUS_STANDAARD)
    geselecteerde_bouwcategorie = request.args.getlist("bouwcategorie") or list(BOUWCATEGORIE_STANDAARD)
    geselecteerde_kolommen = [k for k in request.args.getlist("kolom") if k in KOLOMMEN] or list(KOLOMMEN_STANDAARD)

    geselecteerde_business_plaatsen = request.args.getlist("business_plaats") or list(BUSINESS_REGIO_STANDAARD)
    geselecteerde_business_categorieen = (
        [c for c in request.args.getlist("business_categorie") if c in BUSINESS_CATEGORIEEN]
        or list(BUSINESS_CATEGORIE_STANDAARD)
    )
    geselecteerde_business_statussen = request.args.getlist("business_status") or list(BUSINESS_STATUS_STANDAARD)
    geselecteerde_business_transactietype = request.args.get("business_transactietype") or BUSINESS_TRANSACTIETYPE_STANDAARD
    if geselecteerde_business_transactietype not in BUSINESS_TRANSACTIETYPES:
        geselecteerde_business_transactietype = BUSINESS_TRANSACTIETYPE_STANDAARD

    return render_template(
        "index.html",
        regios=REGIOS_STANDAARD,
        regio_standaard=geselecteerde_plaatsen,
        statussen=STATUSSEN,
        status_standaard=geselecteerde_statussen,
        bouwcategorieen=BOUWCATEGORIEEN,
        bouwcategorie_standaard=geselecteerde_bouwcategorie,
        kolommen={k: v[0] for k, v in KOLOMMEN.items()},
        kolommen_standaard=geselecteerde_kolommen,
        fout=fout,
        actieve_tab=actieve_tab,
        actieve_sectie=actieve_tab,
        business_categorieen=BUSINESS_CATEGORIEEN,
        business_categorie_standaard=geselecteerde_business_categorieen,
        business_transactietypes=BUSINESS_TRANSACTIETYPES,
        business_transactietype_standaard=geselecteerde_business_transactietype,
        business_regio_standaard=geselecteerde_business_plaatsen,
        business_statussen=haal_business_statussen(),
        business_status_standaard=geselecteerde_business_statussen,
        laatste_scan_woningen=haal_laatste_scan_samenvatting(),
        laatste_scan_business=haal_laatste_business_scan_samenvatting(),
    )


@app.route("/scan/start", methods=["POST"])
def scan_start():
    regios = [r.strip() for r in request.form.getlist("plaats") if r.strip()]
    if not regios:
        return redirect(url_for("index", fout="regio_verplicht"))

    verkregen = SCAN_RUNNING_LOCK.acquire(blocking=False)
    if not verkregen:
        return redirect(url_for("scan_status_pagina", geblokkeerd=1))

    gestart_om = datetime.now()
    with STATE_LOCK:
        SCAN_STATE.clear()
        SCAN_STATE.update(
            status="running",
            regios=regios,
            gestart_om_weergave=gestart_om.strftime("%d-%m-%Y %H:%M:%S"),
        )

    thread = threading.Thread(target=_voer_scan_uit, args=(regios, gestart_om), daemon=True)
    thread.start()

    return redirect(url_for("scan_status_pagina"))


@app.route("/scan/stop", methods=["POST"])
def scan_stop():
    """Vraagt een lopende Woningen-scan aan om af te breken. Zelfde,
    idempotente patroon als /business/scan/stop (DEEL B): een tweede/derde
    klik terwijl de stop al onderweg is zet niets opnieuw in werking."""
    with STATE_LOCK:
        actief = SCAN_STATE.get("status") == "running"
        al_aangevraagd = SCAN_STATE.get("stop_aangevraagd", False)
        if actief and not al_aangevraagd:
            SCAN_STATE["stop_aangevraagd"] = True

    if actief and not al_aangevraagd:
        try:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            SCAN_STOP_FLAG.touch()
        except OSError:
            pass

    return redirect(url_for("scan_status_pagina"))


@app.route("/scan/status")
def scan_status_pagina():
    with STATE_LOCK:
        scan = dict(SCAN_STATE)
    return render_template(
        "scan_status.html",
        scan=scan,
        kolommen_standaard=KOLOMMEN_STANDAARD,
        geblokkeerd=bool(request.args.get("geblokkeerd")),
        actieve_sectie="woningen",
    )


@app.route("/business/scan/start", methods=["POST"])
def business_scan_start():
    """Start de bestaande Business-scanner (scanner/funda_business_scanner_v1.py)
    als subprocess, met exact de op het hoofdscherm geselecteerde regio's en
    categorieen. Status-/transactietypefilter bepalen bewust NIET de scan-
    scope (de scanner haalt sowieso beide prijssoorten per object op) - die
    worden alleen bewaard om na afloop terug te kunnen linken naar dezelfde
    analyseweergave."""
    plaatsen = [p.strip() for p in request.form.getlist("business_plaats") if p.strip()]
    categorieen = [c.strip() for c in request.form.getlist("business_categorie") if c.strip() in BUSINESS_CATEGORIEEN]
    statussen = request.form.getlist("business_status")
    transactietype = request.form.get("business_transactietype") or BUSINESS_TRANSACTIETYPE_STANDAARD
    if transactietype not in BUSINESS_TRANSACTIETYPES:
        transactietype = BUSINESS_TRANSACTIETYPE_STANDAARD

    if not plaatsen:
        return redirect(url_for("index", fout="business_regio_verplicht"))
    if not categorieen:
        return redirect(url_for("index", fout="business_categorie_verplicht"))

    # Gedeelde lock met de woningenscan: nooit twee scans tegelijk (beide
    # gebruiken een zichtbaar Chrome-venster + interactieve console).
    verkregen = SCAN_RUNNING_LOCK.acquire(blocking=False)
    if not verkregen:
        return redirect(url_for("business_scan_status_pagina", geblokkeerd=1))

    gestart_om = datetime.now()
    with BUSINESS_STATE_LOCK:
        BUSINESS_SCAN_STATE.clear()
        BUSINESS_SCAN_STATE.update(
            status="running",
            plaatsen=plaatsen,
            categorieen=categorieen,
            statussen=statussen,
            transactietype=transactietype,
            gestart_om_weergave=gestart_om.strftime("%d-%m-%Y %H:%M:%S"),
        )

    thread = threading.Thread(
        target=_voer_business_scan_uit,
        args=(plaatsen, categorieen, statussen, transactietype, gestart_om),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("business_scan_status_pagina"))


@app.route("/business/scan/stop", methods=["POST"])
def business_scan_stop():
    """Vraagt een lopende Business-scan aan om af te breken. Idempotent
    (zoals gevraagd): een tweede/derde klik terwijl de stop al onderweg is
    zet niets opnieuw in werking en veroorzaakt geen dubbele terminate,
    KeyError of traceback - `stop_aangevraagd` wordt maar één keer op True
    gezet. De daadwerkelijke afhandeling (coöperatief -> terminate ->
    geforceerd, cleanup, lock vrijgeven, geen historie-import) gebeurt in
    _wacht_op_business_proces()/_voer_business_scan_uit() in de
    achtergrondthread - deze route registreert alleen het verzoek."""
    with BUSINESS_STATE_LOCK:
        actief = BUSINESS_SCAN_STATE.get("status") in ("running", "importing")
        al_aangevraagd = BUSINESS_SCAN_STATE.get("stop_aangevraagd", False)
        if actief and not al_aangevraagd:
            BUSINESS_SCAN_STATE["stop_aangevraagd"] = True

    if actief and not al_aangevraagd:
        try:
            BUSINESS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            BUSINESS_SCAN_STOP_FLAG.touch()
        except OSError:
            pass

    return redirect(url_for("business_scan_status_pagina"))


@app.route("/business/scan/status")
def business_scan_status_pagina():
    with BUSINESS_STATE_LOCK:
        scan = dict(BUSINESS_SCAN_STATE)
    return render_template(
        "business_scan_status.html",
        scan=scan,
        geblokkeerd=bool(request.args.get("geblokkeerd")),
        actieve_sectie="bedrijfsmatig",
    )


@app.route("/analyse")
def analyse():
    resultaat = bouw_analyseresultaat(request.args)

    # DEEL D3: toggle-links voor de plaats/gemeente-weergave - dezelfde
    # actieve filters/scan_id behouden, alleen geo_niveau wisselt.
    basis_params = [(k, v) for k, v in request.args.items(multi=True) if k != "geo_niveau"]
    geo_qs_plaats = urlencode(basis_params + [("geo_niveau", "plaats")])
    geo_qs_gemeente = urlencode(basis_params + [("geo_niveau", "gemeente")])

    return render_template(
        "resultaat.html",
        geo_qs_plaats=geo_qs_plaats,
        geo_qs_gemeente=geo_qs_gemeente,
        foutmelding=resultaat["foutmelding"],
        rijen=resultaat["rijen"],
        kolommen=resultaat["kolommen"],
        kolom_labels={k: v[0] for k, v in KOLOMMEN.items()},
        kolom_sorteertype={k: v[1] for k, v in KOLOMMEN.items()},
        kpis=resultaat["kpis"],
        scaninfo=resultaat["scaninfo"],
        plaatsen=resultaat["plaatsen"],
        statussen=resultaat["statussen"],
        bouwcategorieen=resultaat["bouwcategorieen"],
        makelaarstabel=resultaat["makelaarstabel"],
        plaatsverdeling=resultaat["plaatsverdeling"],
        gemeenteverdeling=resultaat["gemeenteverdeling"],
        geo_niveau=("gemeente" if request.args.get("geo_niveau") == "gemeente" else "plaats"),
        segmentanalyse=resultaat["segmentanalyse"],
        dagen_in_monitor_stats=resultaat["dagen_in_monitor_stats"],
        vorige_aantal_objecten=resultaat["vorige_aantal_objecten"],
        netto_verandering=resultaat["netto_verandering"],
        vorige_aantal_objecten_totaal=resultaat["vorige_aantal_objecten_totaal"],
        huidige_aantal_objecten_totaal=resultaat["huidige_aantal_objecten_totaal"],
        netto_verandering_totaal=resultaat["netto_verandering_totaal"],
        mutatie_aantallen=resultaat["mutatie_aantallen"],
        mutatie_rijen=resultaat["mutatie_rijen"],
        vorige_scanmoment_weergave=resultaat["vorige_scanmoment_weergave"],
        scan_opties=resultaat["scan_opties"],
        is_historisch=resultaat["is_historisch"],
        laatste_scan_id=resultaat["laatste_scan_id"],
        is_nieuwe_methodiek=resultaat["is_nieuwe_methodiek"],
        methodiek_wijziging_weergave=resultaat["methodiek_wijziging_weergave"],
        eerste_van_nieuwe_meetreeks=resultaat["eerste_van_nieuwe_meetreeks"],
        querystring=request.query_string.decode(),
        actieve_sectie="woningen",
    )


@app.route("/export/csv")
def export_csv():
    resultaat = bouw_analyseresultaat(request.args)
    if resultaat["foutmelding"]:
        return resultaat["foutmelding"], 404

    kolommen = resultaat["kolommen"]
    buffer = io.StringIO()
    schrijver = csv.writer(buffer, delimiter=";")
    schrijver.writerow([KOLOMMEN[k][0] for k in kolommen])
    for rij in resultaat["rijen"]:
        schrijver.writerow([rij.get(k, "") for k in kolommen])

    inhoud = buffer.getvalue().encode("utf-8-sig")
    return Response(
        inhoud,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=makelaar_monitor_export.csv"},
    )


BUSINESS_CSV_KOLOMMEN = [
    ("funda_object_id", "Funda_object_id"),
    ("plaats", "Plaats"),
    ("gezocht_categorie", "Gezocht_categorie"),
    ("objecttype", "Objecttype"),
    ("status", "Status"),
    ("adres", "Adres"),
    ("koopprijs", "Koopprijs"),
    ("koopprijs_conditie", "Koopprijs_conditie"),
    ("huurprijs", "Huurprijs"),
    ("huurprijs_eenheid", "Huurprijs_eenheid"),
    ("oppervlakte_m2", "Oppervlakte_m2"),
    ("oppervlakte_extra_m2", "Oppervlakte_extra_m2"),
    ("berekende_huur_per_jaar", "Berekende_huur_per_jaar"),
    ("berekende_huur_per_maand", "Berekende_huur_per_maand"),
    ("makelaar", "Makelaar"),
    ("funda_url", "Funda_URL"),
    ("bron", "Bron"),
    ("waarschuwing", "Waarschuwing"),
    ("eerste_waarneming", "Eerste_waarneming"),
    ("laatste_waarneming", "Laatste_waarneming"),
    ("dagen_in_monitor", "Dagen_in_monitor"),
]


@app.route("/business/export/csv")
def business_export_csv():
    """DEEL Q: CSV blijft de volledige detail-export voor Business, ook nu de
    objectentabel niet meer standaard zichtbaar is op de analysepagina.
    Zelfde pipeline (bouw_business_analyseresultaat) als de analysepagina
    zelf, dus gegarandeerd dezelfde filters/objecten."""
    resultaat = bouw_business_analyseresultaat(request.args)
    if resultaat["foutmelding"]:
        return resultaat["foutmelding"], 404

    buffer = io.StringIO()
    schrijver = csv.writer(buffer, delimiter=";")
    schrijver.writerow([label for _, label in BUSINESS_CSV_KOLOMMEN])
    for rij in resultaat["rijen"]:
        schrijver.writerow([rij.get(k, "") for k, _ in BUSINESS_CSV_KOLOMMEN])

    inhoud = buffer.getvalue().encode("utf-8-sig")
    return Response(
        inhoud,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=makelaar_monitor_business_export.csv"},
    )


@app.route("/business/analyse")
def business_analyse():
    resultaat = bouw_business_analyseresultaat(request.args)
    return render_template(
        "business_resultaat.html",
        foutmelding=resultaat["foutmelding"],
        scaninfo=resultaat["scaninfo"],
        plaatsen=resultaat["plaatsen"],
        categorieen=resultaat["categorieen"],
        statussen=resultaat["statussen"],
        transactietype=resultaat["transactietype"],
        rijen=resultaat["rijen"],
        kpis=resultaat["kpis"],
        huur_kpis=resultaat["huur_kpis"],
        koop_kpis=resultaat["koop_kpis"],
        makelaarstabel=resultaat["makelaarstabel"],
        segmentanalyse_bedrijfsruimte=resultaat["segmentanalyse_bedrijfsruimte"],
        plaatsvergelijking=resultaat["plaatsvergelijking"],
        dagen_in_monitor_stats=resultaat["dagen_in_monitor_stats"],
        vorige_aantal_objecten=resultaat["vorige_aantal_objecten"],
        netto_verandering=resultaat["netto_verandering"],
        mutatie_aantallen=resultaat["mutatie_aantallen"],
        mutatie_rijen=resultaat["mutatie_rijen"],
        vorige_scanmoment_weergave=resultaat["vorige_scanmoment_weergave"],
        querystring=request.query_string.decode(),
        actieve_sectie="bedrijfsmatig",
    )


def bouw_svg_lijngrafiek(
    reeks_labels: list[str], series: list[dict], eenheid: str = "", hoogte: int = 220, breedte: int = 720,
) -> str:
    """Eenvoudige, dependency-vrije server-gerenderde lijngrafiek (DEEL M).
    `series` = lijst van {"naam": str, "kleur": "#hex", "waarden": [float|None, ...]}
    (waarden uitgelijnd met reeks_labels - None = geen datapunt op dat moment,
    laat een gat in de lijn i.p.v. te verzinnen). Labels/eenheid staan altijd
    op de assen; elk punt heeft een native SVG <title> als hover-tooltip -
    geen JS-framework nodig. Geeft "" terug als er niets te tonen valt."""
    alle_waarden = [w for s in series for w in s["waarden"] if w is not None]
    if not reeks_labels or not alle_waarden:
        return ""

    marge_links, marge_onder, marge_boven, marge_rechts = 62, 42, 16, 16
    plot_breedte = breedte - marge_links - marge_rechts
    plot_hoogte = hoogte - marge_boven - marge_onder

    y_min = min(0, min(alle_waarden))
    y_max = max(alle_waarden)
    if y_max == y_min:
        y_max = y_min + 1
    span = y_max - y_min

    n = len(reeks_labels)
    stap_x = plot_breedte / (n - 1) if n > 1 else 0

    def naar_x(i):
        return marge_links + i * stap_x

    def naar_y(v):
        return marge_boven + plot_hoogte - ((v - y_min) / span) * plot_hoogte

    delen = []
    delen.append(f'<svg viewBox="0 0 {breedte} {hoogte}" xmlns="http://www.w3.org/2000/svg" role="img" font-family="Segoe UI, Arial, sans-serif">')

    # Horizontale gridlijnen + y-as labels (0%, 50%, 100% van de schaal).
    for frac in (0.0, 0.5, 1.0):
        y = marge_boven + plot_hoogte * (1 - frac)
        waarde = y_min + span * frac
        delen.append(f'<line x1="{marge_links}" y1="{y:.1f}" x2="{breedte - marge_rechts}" y2="{y:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        delen.append(f'<text x="{marge_links - 8}" y="{y + 4:.1f}" font-size="10" fill="#5b6b7c" text-anchor="end">{waarde:,.0f}'.replace(",", ".") + '</text>')

    # X-as labels (scanmomenten) - bij veel punten alleen begin/midden/eind tonen.
    toon_indices = set([0, n - 1]) if n <= 6 else set([0, n // 2, n - 1])
    for i, label in enumerate(reeks_labels):
        if i not in toon_indices:
            continue
        x = naar_x(i)
        delen.append(f'<text x="{x:.1f}" y="{hoogte - marge_onder + 16}" font-size="10" fill="#5b6b7c" text-anchor="middle">{label}</text>')

    for s in series:
        kleur = s.get("kleur", "#2f6fed")
        punten = [(naar_x(i), naar_y(w)) for i, w in enumerate(s["waarden"]) if w is not None]
        segment_indices = [i for i, w in enumerate(s["waarden"]) if w is not None]
        if len(punten) >= 2:
            pad = " ".join(f"{x:.1f},{y:.1f}" for x, y in punten)
            delen.append(f'<polyline points="{pad}" fill="none" stroke="{kleur}" stroke-width="2.2"/>')
        for idx, (x, y) in zip(segment_indices, punten):
            waarde_weergave = f"{s['waarden'][idx]:,.0f}".replace(",", ".") + (f" {eenheid}" if eenheid else "")
            delen.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{kleur}">'
                f'<title>{s["naam"]} · {reeks_labels[idx]}: {waarde_weergave}</title></circle>'
            )

    delen.append(f'<line x1="{marge_links}" y1="{marge_boven + plot_hoogte:.1f}" x2="{breedte - marge_rechts}" y2="{marge_boven + plot_hoogte:.1f}" stroke="#cbd5e1" stroke-width="1"/>')
    delen.append("</svg>")
    return "".join(delen)


def haal_woningen_scanreeksen(con: sqlite3.Connection) -> list[dict]:
    """Elke DISTINCT scanregio (exacte 'gebied'-string) met het aantal scans
    en het laatste scanmoment - vult de dashboardfilter. Verschillende
    gebiedssets worden NOOIT als dezelfde trend behandeld (DEEL K)."""
    rows = con.execute(
        "SELECT gebied, COUNT(*) AS aantal_scans, MAX(scanmoment) AS laatste "
        "FROM scans GROUP BY gebied ORDER BY laatste DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def haal_woningen_trend(con: sqlite3.Connection, gebied: str) -> dict:
    """Eén query voor de scans + één query voor ALLE bijbehorende snapshot-
    rijen (geen N+1 - DEEL R), daarna aggregatie in Python per scan_id.

    Methodiekbreuk (stabilisatieronde 2026-09-09): scans van vóór
    WONINGEN_METHODIEK_WIJZIGING worden hier NOOIT samen met scans van erna
    in dezelfde trend getoond - dat zou een fictieve aanbodsprong/prijs-
    ontwikkeling/marktaandeelverandering suggereren. Bepaald door de MEEST
    RECENTE scan van dit gebied: is die nieuw, dan tellen alleen andere
    nieuwe scans mee (oudere scans blijven gewoon in de database en raadpleeg-
    baar via Historische Analyse - alleen niet in deze trend)."""
    alle_scans = con.execute(
        "SELECT scan_id, scanmoment, aantal_objecten FROM scans WHERE gebied = ? ORDER BY scanmoment, scan_id",
        (gebied,),
    ).fetchall()
    if not alle_scans:
        return {"reeks": [], "top_makelaars": [], "uitgesloten_door_methodiekbreuk": 0}

    nieuwste_methodiek = woningen_is_nieuwe_methodiek(alle_scans[-1]["scanmoment"])
    if nieuwste_methodiek:
        scans = [s for s in alle_scans if woningen_is_nieuwe_methodiek(s["scanmoment"])]
    else:
        scans = alle_scans
    uitgesloten_door_methodiekbreuk = len(alle_scans) - len(scans)

    scan_ids = [s["scan_id"] for s in scans]
    placeholders = ",".join("?" * len(scan_ids))
    rows = con.execute(
        f"SELECT scan_id, funda_url, vraagprijs, woonoppervlakte, status, makelaar "
        f"FROM snapshots WHERE scan_id IN ({placeholders})",
        scan_ids,
    ).fetchall()

    per_scan: dict[int, list] = {}
    for r in rows:
        per_scan.setdefault(r["scan_id"], []).append(r)

    reeks = []
    vorige_urls: set[str] | None = None
    for s in scans:
        rs = per_scan.get(s["scan_id"], [])
        beschikbaar = [r for r in rs if r["status"] == "Beschikbaar"]
        prijzen = [r["vraagprijs"] for r in beschikbaar if r["vraagprijs"]]
        m2prijzen = [
            r["vraagprijs"] / r["woonoppervlakte"] for r in beschikbaar
            if r["vraagprijs"] and r["woonoppervlakte"]
        ]
        huidige_urls = {r["funda_url"] for r in rs if r["funda_url"]}
        if vorige_urls is None:
            nieuw = uit = None
        else:
            nieuw = len(huidige_urls - vorige_urls)
            uit = len(vorige_urls - huidige_urls)
        vorige_urls = huidige_urls

        reeks.append({
            "scan_id": s["scan_id"],
            "scanmoment_weergave": formatteer_scanmoment(s["scanmoment"]).split(" ")[0],
            "totaal_aanbod": len(rs),
            "beschikbaar_aanbod": len(beschikbaar),
            "gemiddelde_vraagprijs": round(sum(prijzen) / len(prijzen)) if prijzen else None,
            "mediaan_vraagprijs": round(statistics.median(prijzen)) if prijzen else None,
            "gemiddelde_m2": round(sum(m2prijzen) / len(m2prijzen)) if m2prijzen else None,
            "mediaan_m2": round(statistics.median(m2prijzen)) if m2prijzen else None,
            "nieuw_aanbod": nieuw,
            "uit_aanbod": uit,
        })

    top_makelaars = _bereken_top_makelaars_trend(scans, per_scan)
    return {
        "reeks": reeks,
        "top_makelaars": top_makelaars,
        "uitgesloten_door_methodiekbreuk": uitgesloten_door_methodiekbreuk,
    }


def _bereken_top_makelaars_trend(scans, per_scan: dict[int, list], top_n: int = 5) -> list[dict]:
    """Marktaandeel (op objectaantal) van de makelaars die in de MEEST RECENTE
    scan het grootst zijn, gevolgd over alle scans in de reeks - gedeeld
    tussen Woningen/Business-dashboard. Rijen zonder makelaar tellen niet mee."""
    if not scans:
        return []
    laatste = per_scan.get(scans[-1]["scan_id"], [])
    aantallen: dict[str, int] = {}
    for r in laatste:
        naam = (r["makelaar"] or "").strip()
        if naam:
            aantallen[naam] = aantallen.get(naam, 0) + 1
    top = sorted(aantallen, key=lambda k: aantallen[k], reverse=True)[:top_n]

    resultaat = []
    for naam in top:
        pct_per_scan = []
        for s in scans:
            rs = per_scan.get(s["scan_id"], [])
            totaal = len(rs)
            aantal = sum(1 for r in rs if (r["makelaar"] or "").strip() == naam)
            pct_per_scan.append(round(aantal / totaal * 100, 1) if totaal else None)

        huidige_pct = pct_per_scan[-1] if pct_per_scan else None
        vorige_pct = pct_per_scan[-2] if len(pct_per_scan) >= 2 else None
        # Procentpunt-verandering (NIET procentuele groei) tussen de laatste
        # twee vergelijkbare scans - bv. 24,0% -> 28,5% = +4,5 procentpunt.
        procentpunt_ontwikkeling = (
            round(huidige_pct - vorige_pct, 1)
            if huidige_pct is not None and vorige_pct is not None else None
        )
        resultaat.append({
            "makelaar": naam,
            "aandeel_per_scan": pct_per_scan,
            "huidige_pct": huidige_pct,
            "vorige_pct": vorige_pct,
            "procentpunt_ontwikkeling": procentpunt_ontwikkeling,
        })

    resultaat.sort(key=lambda x: x["huidige_pct"] or 0, reverse=True)
    return resultaat


def haal_business_scanreeksen(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        "SELECT gebied, categorieen, COUNT(*) AS aantal_scans, MAX(scanmoment) AS laatste "
        "FROM business_scans GROUP BY gebied, categorieen ORDER BY laatste DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def haal_business_trend(con: sqlite3.Connection, gebied: str, categorieen: str) -> dict:
    scans = con.execute(
        "SELECT scan_id, scanmoment FROM business_scans WHERE gebied = ? AND categorieen = ? "
        "ORDER BY scanmoment, scan_id",
        (gebied, categorieen),
    ).fetchall()
    if not scans:
        return {"reeks": [], "top_makelaars": []}

    scan_ids = [s["scan_id"] for s in scans]
    placeholders = ",".join("?" * len(scan_ids))
    rows = con.execute(
        f"SELECT scan_id, funda_object_id, koopprijs, huurprijs, huurprijs_eenheid, "
        f"oppervlakte_m2, berekende_huur_per_jaar, makelaar "
        f"FROM business_snapshots WHERE scan_id IN ({placeholders})",
        scan_ids,
    ).fetchall()

    per_scan: dict[int, list] = {}
    for r in rows:
        per_scan.setdefault(r["scan_id"], []).append(r)

    reeks = []
    vorige_ids: set[str] | None = None
    for s in scans:
        rs = per_scan.get(s["scan_id"], [])
        m2_totaal = sum(r["oppervlakte_m2"] for r in rs if r["oppervlakte_m2"] is not None)
        huur_m2jaar = [r["huurprijs"] for r in rs if r["huurprijs_eenheid"] == "per_m2_per_jaar" and r["huurprijs"] is not None]
        koopwaarde = sum(r["koopprijs"] for r in rs if r["koopprijs"] is not None)
        jaarhuur = sum(r["berekende_huur_per_jaar"] for r in rs if r["berekende_huur_per_jaar"] is not None)
        huidige_ids = {r["funda_object_id"] for r in rs if r["funda_object_id"]}
        if vorige_ids is None:
            nieuw = uit = None
        else:
            nieuw = len(huidige_ids - vorige_ids)
            uit = len(vorige_ids - huidige_ids)
        vorige_ids = huidige_ids

        reeks.append({
            "scan_id": s["scan_id"],
            "scanmoment_weergave": formatteer_scanmoment(s["scanmoment"]).split(" ")[0],
            "actief_aanbod": len(rs),
            "m2_totaal": round(m2_totaal) if m2_totaal else None,
            "gemiddelde_huur_m2jaar": round(sum(huur_m2jaar) / len(huur_m2jaar)) if huur_m2jaar else None,
            "mediaan_huur_m2jaar": round(statistics.median(huur_m2jaar)) if huur_m2jaar else None,
            "koopwaarde_totaal": koopwaarde or None,
            "jaarhuur_totaal": jaarhuur or None,
            "nieuw_aanbod": nieuw,
            "uit_aanbod": uit,
        })

    top_makelaars = _bereken_top_makelaars_trend(scans, per_scan)
    return {"reeks": reeks, "top_makelaars": top_makelaars}


def bouw_makelaar_lokale_marktpositie(
    naam: str, rijen: list[dict], niveau: str = "plaats", context_tabel: list[dict] | None = None,
) -> list[dict]:
    """DEEL 4 (stabilisatieronde 2026-09-09): marktpositie van deze makelaar
    per plaats/gemeente, dynamisch berekend binnen exact dezelfde scan/
    filtercontext als het profiel zelf (geen hardcoded cijfers). Toont
    alleen gebieden waar de makelaar daadwerkelijk actief is. Bij gelijke
    aantallen delen makelaars dezelfde ranking (bv. #2, #2, #4).

    `marktaandeel_pct` blijft ALTIJD objecten_makelaar/totaal_markt*100 -
    inwonertal/marktintensiteit veranderen deze formule nooit. `context_tabel`
    (optioneel: de al-berekende plaatsverdeling/gemeenteverdeling met
    marktintensiteit uit bouw_analyseresultaat()) wordt uitsluitend gebruikt
    om inwoners/marktintensiteitsindex als AANVULLENDE context te tonen -
    hergebruikt dezelfde, al geteste berekening i.p.v. de formule hier
    opnieuw te implementeren (nieuwe gerichte ronde, DEEL 2)."""
    per_gebied: dict[str, dict] = {}
    for r in rijen:
        plaats = (r.get("plaats") or "").strip() or "Onbekend"
        if niveau == "gemeente":
            gebied = geo_info(plaats).get("gemeente")
            if not gebied:
                continue  # DEEL 4: alleen tonen als de gemeente betrouwbaar bekend is
        else:
            gebied = plaats
        entry = per_gebied.setdefault(gebied, {"gebied": gebied, "totaal": 0, "makelaars": {}})
        entry["totaal"] += 1
        mk = (r.get("makelaar") or "").strip() or "Onbekend"
        entry["makelaars"][mk] = entry["makelaars"].get(mk, 0) + 1

    sleutel_veld = "gemeente" if niveau == "gemeente" else "plaats"
    context_per_gebied = {c[sleutel_veld]: c for c in (context_tabel or [])}

    resultaat = []
    for entry in per_gebied.values():
        eigen_aantal = entry["makelaars"].get(naam, 0)
        if eigen_aantal == 0:
            continue
        aantallen_dalend = sorted(entry["makelaars"].values(), reverse=True)
        rang = aantallen_dalend.index(eigen_aantal) + 1
        ctx = context_per_gebied.get(entry["gebied"], {})
        resultaat.append({
            "gebied": entry["gebied"],
            "objecten_makelaar": eigen_aantal,
            "totaal_markt": entry["totaal"],
            "marktaandeel_pct": round(eigen_aantal / entry["totaal"] * 100, 1) if entry["totaal"] else 0,
            "ranking_weergave": f"#{rang}",
            # Marktintensiteit: uitsluitend context, geen invloed op marktaandeel_pct hierboven.
            "inwoners_weergave": ctx.get("inwoners_weergave", "-"),
            "marktintensiteitsindex": ctx.get("marktintensiteitsindex"),
        })
    resultaat.sort(key=lambda x: x["objecten_makelaar"], reverse=True)
    return resultaat


def bouw_makelaar_profiel_woningen(
    naam: str, rijen: list[dict], plaatsverdeling: list[dict] | None = None, gemeenteverdeling: list[dict] | None = None,
) -> dict:
    """Eenvoudig makelaarsprofiel (DEEL N) uit de rijen van de HUIDIGE
    woningenselectie (dezelfde bouw_analyseresultaat()-pipeline als de
    analysepagina). 'Ontwikkeling door de tijd' is bewust nog niet
    meegenomen (zie docs/BUGLIST.md) - alleen betrouwbaar uit de huidige
    snapshot afgeleide velden.

    `plaatsverdeling`/`gemeenteverdeling` (optioneel: al berekend door
    bouw_analyseresultaat(), inclusief marktintensiteit) worden alleen
    doorgegeven aan bouw_makelaar_lokale_marktpositie() als aanvullende
    context - zie daar voor de garantie dat marktaandeel_pct hierdoor nooit
    verandert (nieuwe gerichte ronde, DEEL 2)."""
    eigen = [r for r in rijen if (r.get("makelaar") or "").strip() == naam]
    totaal = len(rijen)
    prijzen = [r["vraagprijs"] for r in eigen if r.get("vraagprijs") is not None]
    prijzen_m2 = [
        r["vraagprijs"] / r["woonoppervlakte"] for r in eigen
        if r.get("vraagprijs") and r.get("woonoppervlakte") and r["vraagprijs"] > 0 and r["woonoppervlakte"] > 0
    ]
    m2_totaal = sum(r["woonoppervlakte"] for r in eigen if r.get("woonoppervlakte") is not None)
    plaatsen = sorted({(r.get("plaats") or "").strip() for r in eigen if (r.get("plaats") or "").strip()})
    segmenten = sorted({
        _segment_van(r["vraagprijs"], WONING_PRIJSSEGMENTEN) for r in eigen
        if r.get("vraagprijs") and _segment_van(r["vraagprijs"], WONING_PRIJSSEGMENTEN)
    })

    return {
        "naam": naam,
        "aantal": len(eigen),
        "marktaandeel_pct": round(len(eigen) / totaal * 100, 1) if totaal else 0,
        "marktaandeel_noemer": totaal,
        "m2_weergave": (f"{m2_totaal:,.0f}".replace(",", ".") + " m²") if m2_totaal else "-",
        "totale_vraagwaarde_weergave": formatteer_bedrag(sum(prijzen)) if prijzen else "-",
        "gemiddelde_vraagprijs_weergave": formatteer_bedrag(round(sum(prijzen) / len(prijzen))) if prijzen else "-",
        "mediaan_vraagprijs_weergave": formatteer_bedrag(round(statistics.median(prijzen))) if prijzen else "-",
        "gemiddelde_m2_weergave": (formatteer_bedrag(round(sum(prijzen_m2) / len(prijzen_m2))) + " /m²") if prijzen_m2 else "-",
        "plaatsen": plaatsen,
        "segmenten": segmenten,
        "objecten": eigen,
        "marktpositie_plaats": bouw_makelaar_lokale_marktpositie(naam, rijen, "plaats", plaatsverdeling),
        "marktpositie_gemeente": bouw_makelaar_lokale_marktpositie(naam, rijen, "gemeente", gemeenteverdeling),
    }


def bouw_makelaar_profiel_business(naam: str, rijen: list[dict]) -> dict:
    """Analoog aan bouw_makelaar_profiel_woningen() maar voor Business."""
    eigen = [r for r in rijen if (r.get("makelaar") or "").strip() == naam]
    totaal = len(rijen)
    m2_totaal = sum(r["oppervlakte_m2"] for r in eigen if r.get("oppervlakte_m2") is not None)
    koopprijzen = [r["koopprijs"] for r in eigen if r.get("koopprijs") is not None]
    jaarhuren = [r["berekende_huur_per_jaar"] for r in eigen if r.get("berekende_huur_per_jaar") is not None]
    plaatsen = sorted({(r.get("plaats") or "").strip() for r in eigen if (r.get("plaats") or "").strip()})
    segmenten = sorted({
        _segment_van(r["oppervlakte_m2"], BUSINESS_BEDRIJFSRUIMTE_SEGMENTEN) for r in eigen
        if r.get("oppervlakte_m2") and "Bedrijfsruimte" in business_categorieen_van_rij(r)
        and _segment_van(r["oppervlakte_m2"], BUSINESS_BEDRIJFSRUIMTE_SEGMENTEN)
    })

    return {
        "naam": naam,
        "aantal": len(eigen),
        "marktaandeel_pct": round(len(eigen) / totaal * 100, 1) if totaal else 0,
        "marktaandeel_noemer": totaal,
        "m2_weergave": (f"{m2_totaal:,.0f}".replace(",", ".") + " m²") if m2_totaal else "-",
        "huur": sum(1 for r in eigen if business_is_huur_aanbod(r)),
        "koop": sum(1 for r in eigen if business_is_koop_aanbod(r)),
        "koopwaarde_weergave": formatteer_bedrag(sum(koopprijzen)) if koopprijzen else "-",
        "koopwaarde_dekking": len(koopprijzen),
        "jaarhuur_weergave": formatteer_bedrag(sum(jaarhuren)) if jaarhuren else "-",
        "jaarhuur_dekking": len(jaarhuren),
        "plaatsen": plaatsen,
        "segmenten": segmenten,
        "objecten": eigen,
    }


def bepaal_funda_makelaar_link(naam: str, rijen: list[dict]) -> dict | None:
    """DEEL 5 (stabilisatieronde 2026-09-09): onderzocht of een betrouwbare
    Funda-makelaarspagina-URL/kantoor-ID uit bestaande scan-/detaildata kan
    worden afgeleid, in plaats van te GOKKEN op basis van alleen de naam.

    Uitkomst van dat onderzoek: NEE, nog niet. `scanner/makelaarsmonitor_v41.py`
    (`parse_broker()`) herkent weliswaar of een link op de resultaatkaart
    'iets met makelaar' is (het inspecteert daarvoor al href/aria-label), maar
    bewaart uitsluitend de tekstuele naam - de href zelf (die naar Funda's
    eigen /makelaar/<id>-<slug>/-pagina zou kunnen wijzen) wordt nergens
    opgeslagen. Er is dus GEEN kantoor-ID/URL-veld beschikbaar in de huidige
    snapshots. Deze functie retourneert daarom bewust altijd None (geen knop)
    totdat de scanner is uitgebreid om die href daadwerkelijk vast te leggen
    - zie de roadmapnotitie in docs/CLAUDE_STATUS.md. Bewust GEEN naam-
    gebaseerde gok/constructie hier, ook niet als fallback."""
    return None


@app.route("/makelaar/<module>/<path:naam>")
def makelaar_profiel(module, naam):
    """Eerste, eenvoudige makelaarsdetailpagina (DEEL N), uit onze EIGEN data
    - geen externe KvK/Google/reviewdata. Het profiel is altijd berekend
    binnen de huidige filterselectie (dezelfde query-parameters als de
    analysepagina van herkomst), zodat 'marktaandeel' een duidelijke, voor de
    gebruiker zichtbare noemer heeft."""
    if module == "woningen":
        resultaat = bouw_analyseresultaat(request.args)
        if resultaat["foutmelding"]:
            return render_template("makelaar_profiel.html", module=module, foutmelding=resultaat["foutmelding"], profiel=None, actieve_sectie="woningen")
        profiel = bouw_makelaar_profiel_woningen(
            naam, resultaat["ruwe_rijen"], resultaat.get("plaatsverdeling"), resultaat.get("gemeenteverdeling"),
        )
        return render_template(
            "makelaar_profiel.html", module=module, foutmelding=None, profiel=profiel,
            funda_makelaar_link=bepaal_funda_makelaar_link(naam, resultaat["ruwe_rijen"]),
            is_historisch=resultaat["is_historisch"],
            terug_url=url_for("analyse", plaats=resultaat["plaatsen"], status=resultaat["statussen"], bouwcategorie=resultaat["bouwcategorieen"]),
            actieve_sectie="woningen",
        )

    if module == "bedrijfsmatig":
        resultaat = bouw_business_analyseresultaat(request.args)
        if resultaat["foutmelding"]:
            return render_template("makelaar_profiel.html", module=module, foutmelding=resultaat["foutmelding"], profiel=None, actieve_sectie="bedrijfsmatig")
        profiel = bouw_makelaar_profiel_business(naam, resultaat["ruwe_rijen"])
        return render_template(
            "makelaar_profiel.html", module=module, foutmelding=None, profiel=profiel,
            terug_url=url_for("business_analyse", business_plaats=resultaat["plaatsen"], business_categorie=resultaat["categorieen"], business_status=resultaat["statussen"], business_transactietype=resultaat["transactietype"]),
            actieve_sectie="bedrijfsmatig",
        )

    return "Onbekende module.", 404


@app.route("/dashboard")
def dashboard():
    markt = request.args.get("markt") or "woningen"
    if markt not in ("woningen", "bedrijfsmatig"):
        markt = "woningen"

    context = {"actieve_sectie": "dashboard", "markt": markt}

    if markt == "woningen":
        if not DB_PATH.exists():
            return render_template("dashboard.html", **context, scanreeksen=[], gebied=None, trend=None)
        con = get_readonly_connection()
        try:
            scanreeksen = haal_woningen_scanreeksen(con)
            gebied = request.args.get("gebied") or (scanreeksen[0]["gebied"] if scanreeksen else None)
            trend = haal_woningen_trend(con, gebied) if gebied else None
        finally:
            con.close()
        return render_template(
            "dashboard.html", **context,
            scanreeksen=scanreeksen, gebied=gebied, trend=trend,
            methodiek_wijziging_weergave=formatteer_scanmoment(WONINGEN_METHODIEK_WIJZIGING),
            grafiek_aanbod=(
                bouw_svg_lijngrafiek(
                    [p["scanmoment_weergave"] for p in trend["reeks"]],
                    [
                        {"naam": "Totaal aanbod", "kleur": "#2f6fed", "waarden": [p["totaal_aanbod"] for p in trend["reeks"]]},
                        {"naam": "Beschikbaar", "kleur": "#1b7a4b", "waarden": [p["beschikbaar_aanbod"] for p in trend["reeks"]]},
                    ],
                    eenheid="objecten",
                ) if trend and trend["reeks"] else ""
            ),
            grafiek_prijs=(
                bouw_svg_lijngrafiek(
                    [p["scanmoment_weergave"] for p in trend["reeks"]],
                    [
                        {"naam": "Gemiddelde vraagprijs", "kleur": "#2f6fed", "waarden": [p["gemiddelde_vraagprijs"] for p in trend["reeks"]]},
                        {"naam": "Mediaan vraagprijs", "kleur": "#b3261e", "waarden": [p["mediaan_vraagprijs"] for p in trend["reeks"]]},
                    ],
                    eenheid="€",
                ) if trend and trend["reeks"] else ""
            ),
        )

    # markt == "bedrijfsmatig"
    if not BUSINESS_DB_PATH.exists():
        return render_template("dashboard.html", **context, scanreeksen=[], gebied=None, categorieen=None, trend=None)
    con = get_business_readonly_connection()
    try:
        scanreeksen = haal_business_scanreeksen(con)
        sleutel = request.args.get("reeks")
        if sleutel and "||" in sleutel:
            gebied, categorieen = sleutel.split("||", 1)
        elif scanreeksen:
            gebied, categorieen = scanreeksen[0]["gebied"], scanreeksen[0]["categorieen"]
        else:
            gebied = categorieen = None
        trend = haal_business_trend(con, gebied, categorieen) if gebied else None
    finally:
        con.close()
    return render_template(
        "dashboard.html", **context,
        scanreeksen=scanreeksen, gebied=gebied, categorieen=categorieen, trend=trend,
        grafiek_aanbod=(
            bouw_svg_lijngrafiek(
                [p["scanmoment_weergave"] for p in trend["reeks"]],
                [{"naam": "Actief aanbod", "kleur": "#2f6fed", "waarden": [p["actief_aanbod"] for p in trend["reeks"]]}],
                eenheid="objecten",
            ) if trend and trend["reeks"] else ""
        ),
        grafiek_prijs=(
            bouw_svg_lijngrafiek(
                [p["scanmoment_weergave"] for p in trend["reeks"]],
                [
                    {"naam": "Gem. huur €/m²/jaar", "kleur": "#2f6fed", "waarden": [p["gemiddelde_huur_m2jaar"] for p in trend["reeks"]]},
                    {"naam": "Mediaan huur €/m²/jaar", "kleur": "#b3261e", "waarden": [p["mediaan_huur_m2jaar"] for p in trend["reeks"]]},
                ],
                eenheid="€/m²/jaar",
            ) if trend and trend["reeks"] else ""
        ),
    )


if __name__ == "__main__":
    # DEEL J: debug=False zodat een onverwachte fout de eigen foutpagina
    # toont i.p.v. de interactieve Werkzeug-debugger. Volledige tracebacks
    # blijven zichtbaar op de console via _onverwachte_fout() hierboven -
    # debugbaarheid tijdens ontwikkeling blijft dus intact, alleen niet meer
    # rechtstreeks in de browser van de gebruiker.
    logging.basicConfig(level=logging.INFO)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
