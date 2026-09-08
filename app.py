"""Makelaar Monitor - lokale webapp (fundament).

Start vanuit de projectroot met:  py app.py
Bereikbaar via:                   http://127.0.0.1:5000

Leest uitsluitend read-only uit de bestaande SQLite-historie in data/.
Voert geen scans uit en wijzigt de database niet.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import statistics
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path

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

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

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
    "vraagprijs",
    "makelaar",
    "status",
    "woonoppervlakte",
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
        "SELECT scan_id, scanmoment, peildatum, gebied FROM scans WHERE scan_id = ?",
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


def haal_geschiedenis(con: sqlite3.Connection, funda_urls: list[str]) -> dict[str, dict]:
    """Eerste/laatste waarneming (datum + vraagprijs) per funda_url, over alle scans heen."""
    urls = sorted({u for u in funda_urls if u})
    if not urls:
        return {}

    placeholders = ",".join("?" * len(urls))
    query = (
        f"SELECT funda_url, peildatum, vraagprijs FROM snapshots "
        f"WHERE funda_url IN ({placeholders}) ORDER BY funda_url, peildatum, scan_id"
    )

    geschiedenis: dict[str, dict] = {}
    for rij in con.execute(query, urls).fetchall():
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

    return rij


def bereken_kpis(rijen: list[dict]) -> dict:
    """Verwacht ruwe (nog niet opgemaakte) rijen, dus vóór verrijk_rij()."""
    aantal = len(rijen)
    totaal = sum(r.get("vraagprijs") or 0 for r in rijen)
    gemiddeld = round(totaal / aantal) if aantal else 0
    makelaars = {r.get("makelaar") for r in rijen if r.get("makelaar")}
    return {
        "aantal_objecten": aantal,
        "totale_vraagprijs": formatteer_bedrag(totaal),
        "gemiddelde_vraagprijs": formatteer_bedrag(gemiddeld),
        "aantal_makelaars": len(makelaars),
    }


def bouw_analyseresultaat(args) -> dict:
    """Eén pipeline voor zowel de resultaatpagina als de CSV-export, zodat beide
    gegarandeerd dezelfde filters, objecten en kolommen gebruiken."""
    plaatsen, statussen, bouwcategorieen, kolommen = parse_filters(args)
    basis = {
        "plaatsen": plaatsen,
        "statussen": statussen,
        "bouwcategorieen": bouwcategorieen,
        "kolommen": kolommen,
        "kolom_labels": KOLOMMEN,
        "scaninfo": None,
        "rijen": [],
        "kpis": None,
        "foutmelding": None,
    }

    if not DB_PATH.exists():
        basis["foutmelding"] = f"Database niet gevonden op: {DB_PATH}"
        return basis

    con = get_readonly_connection()
    try:
        scan_id = haal_laatste_scan_id(con)
        if scan_id is None:
            basis["foutmelding"] = "Geen scans gevonden in de database."
            return basis

        scaninfo = haal_scan_info(con, scan_id)
        scaninfo["scanmoment_weergave"] = formatteer_scanmoment(scaninfo.get("scanmoment"))
        basis["scaninfo"] = scaninfo

        ruwe_rijen = haal_snapshotrijen(con, scan_id, plaatsen, statussen, bouwcategorieen)
        ruwe_rijen = dedupliceer_op_funda_url(ruwe_rijen)

        basis["kpis"] = bereken_kpis(ruwe_rijen)

        if ruwe_rijen:
            geschiedenis = haal_geschiedenis(con, [r.get("funda_url") for r in ruwe_rijen])
            basis["rijen"] = [verrijk_rij(dict(r), geschiedenis) for r in ruwe_rijen]

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
    Bekende jaarhuur zijn sommen van uitsluitend betrouwbare brongegevens
    (geldige numerieke Koopprijs resp. bestaande Berekende_huur_per_jaar) -
    er wordt nooit een bedrag verzonnen voor n.o.t.k./op aanvraag/ontbrekende
    data."""
    totaal_aanbod = len(rijen)
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
            "aantal": entry["aantal"],
            "aandeel_pct": round(entry["aantal"] / totaal_aanbod * 100, 1) if totaal_aanbod else 0,
            "m2_weergave": (f"{entry['m2']:,.0f}".replace(",", ".") + " m²") if entry["m2"] else "-",
            "huur": entry["huur"],
            "koop": entry["koop"],
            "koopwaarde_weergave": formatteer_bedrag(koopwaarde) if koopwaarde is not None else "-",
            "jaarhuur_weergave": formatteer_bedrag(jaarhuur) if jaarhuur is not None else "-",
        })

    tabel.sort(key=lambda x: x["aantal"], reverse=True)
    return tabel


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
        "kpis": None,
        "huur_kpis": None,
        "koop_kpis": None,
        "makelaarstabel": [],
        "mutatie_aantallen": {},
        "mutatie_rijen": [],
        "vorige_scanmoment_weergave": None,
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

        basis["scaninfo"] = {
            "scan_id": scan_id,
            "gebied": gebied,
            "categorieen": categorieen_canoniek,
            "scanmoment_weergave": formatteer_scanmoment(scanmoment),
        }

        huidige_snapshot = haal_business_snapshot(con, scan_id)
        ruwe_rijen = list(huidige_snapshot.values())

        if statussen:
            ruwe_rijen = [r for r in ruwe_rijen if r.get("status") in statussen]

        ruwe_rijen = business_pas_transactietype_toe(ruwe_rijen, transactietype)

        basis["kpis"] = bereken_business_kpis(ruwe_rijen)
        basis["huur_kpis"] = bereken_business_huur_kpis(ruwe_rijen)
        basis["koop_kpis"] = bereken_business_koop_kpis(ruwe_rijen)
        basis["makelaarstabel"] = bouw_business_makelaarstabel(ruwe_rijen)

        geschiedenis = haal_business_geschiedenis(
            con, gebied, categorieen_canoniek, [r["funda_object_id"] for r in ruwe_rijen]
        )
        basis["rijen"] = sorted(
            (verrijk_business_rij(r, geschiedenis) for r in ruwe_rijen),
            key=lambda r: ((r.get("plaats") or ""), (r.get("adres") or "")),
        )

        vorige_scan_id = haal_business_vorige_scan_id(con, gebied, categorieen_canoniek, scanmoment, scan_id)
        if vorige_scan_id:
            vorige_snapshot = haal_business_snapshot(con, vorige_scan_id)
            aantallen, mutatie_rijen = business_bouw_mutaties(vorige_snapshot, huidige_snapshot)
            basis["mutatie_aantallen"] = aantallen
            basis["mutatie_rijen"] = mutatie_rijen
            vorige_rij = con.execute(
                "SELECT scanmoment FROM business_scans WHERE scan_id = ?", (vorige_scan_id,)
            ).fetchone()
            if vorige_rij:
                basis["vorige_scanmoment_weergave"] = formatteer_scanmoment(vorige_rij["scanmoment"])

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
    het resultaat en importeert dit daarna in de historie-database."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(SCANNER_PAD), "--plaatsen", *regios]

        # Geen stdin/stdout/stderr-omleiding: de scanner opent zelf een
        # zichtbaar Chrome-venster en vraagt in de console een ENTER-
        # bevestiging (en eventueel een captcha-controle). Door niets om te
        # leiden, blijft dit gekoppeld aan hetzelfde consolevenster als
        # waarin "py app.py" draait, zodat die bevestiging daar te zien en
        # te beantwoorden is.
        try:
            proces = subprocess.Popen(cmd, cwd=str(OUTPUT_DIR))
        except OSError as exc:
            with STATE_LOCK:
                SCAN_STATE.update(
                    status="error",
                    foutmelding=f"De scanner kon niet worden gestart: {exc}",
                )
            return

        with STATE_LOCK:
            SCAN_STATE["proces_id"] = proces.pid

        returncode = proces.wait()

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


def _voer_business_scan_uit(
    plaatsen: list[str], categorieen: list[str], statussen: list[str],
    transactietype: str, gestart_om: datetime,
) -> None:
    """Draait in een achtergrondthread: start de bestaande Business-scanner,
    wacht op het resultaat en importeert dit daarna via de bestaande
    Business-historie-tool. Analoog aan _voer_scan_uit() voor woningen, maar
    volledig gescheiden state/output/database - alleen SCAN_RUNNING_LOCK
    (hierboven) is gedeeld met woningen."""
    try:
        BUSINESS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(BUSINESS_SCANNER_PAD),
            "--plaatsen", *plaatsen,
            "--objecttypes", *categorieen,
            "--output-map", str(BUSINESS_OUTPUT_DIR),
        ]

        # Zelfde reden als bij de woningenscanner: geen stdin/stdout/stderr-
        # omleiding, zodat de zichtbare Chrome + interactieve ENTER-/
        # menscontrole-flow in hetzelfde consolevenster als "py app.py"
        # blijft werken. Geen shell=True, geen headless-conversie.
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

        returncode = proces.wait()

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
            BUSINESS_SCAN_STATE.update(
                status="error",
                foutmelding=(
                    "Onverwachte fout tijdens de Business-scan. Controleer het "
                    "consolevenster waarin de webapp is gestart voor details."
                ),
            )
    finally:
        SCAN_RUNNING_LOCK.release()


@app.route("/")
def index():
    fout = request.args.get("fout")
    return render_template(
        "index.html",
        regios=REGIOS_STANDAARD,
        regio_standaard=REGIO_STANDAARD,
        statussen=STATUSSEN,
        status_standaard=STATUS_STANDAARD,
        bouwcategorieen=BOUWCATEGORIEEN,
        bouwcategorie_standaard=BOUWCATEGORIE_STANDAARD,
        kolommen={k: v[0] for k, v in KOLOMMEN.items()},
        kolommen_standaard=KOLOMMEN_STANDAARD,
        fout=fout,
        actieve_tab="bedrijfsmatig" if (fout or "").startswith("business_") else "woningen",
        business_categorieen=BUSINESS_CATEGORIEEN,
        business_categorie_standaard=BUSINESS_CATEGORIE_STANDAARD,
        business_transactietypes=BUSINESS_TRANSACTIETYPES,
        business_transactietype_standaard=BUSINESS_TRANSACTIETYPE_STANDAARD,
        business_regio_standaard=BUSINESS_REGIO_STANDAARD,
        business_statussen=haal_business_statussen(),
        business_status_standaard=BUSINESS_STATUS_STANDAARD,
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


@app.route("/scan/status")
def scan_status_pagina():
    with STATE_LOCK:
        scan = dict(SCAN_STATE)
    return render_template(
        "scan_status.html",
        scan=scan,
        kolommen_standaard=KOLOMMEN_STANDAARD,
        geblokkeerd=bool(request.args.get("geblokkeerd")),
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


@app.route("/business/scan/status")
def business_scan_status_pagina():
    with BUSINESS_STATE_LOCK:
        scan = dict(BUSINESS_SCAN_STATE)
    return render_template(
        "business_scan_status.html",
        scan=scan,
        geblokkeerd=bool(request.args.get("geblokkeerd")),
    )


@app.route("/analyse")
def analyse():
    resultaat = bouw_analyseresultaat(request.args)

    return render_template(
        "resultaat.html",
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
        querystring=request.query_string.decode(),
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
        mutatie_aantallen=resultaat["mutatie_aantallen"],
        mutatie_rijen=resultaat["mutatie_rijen"],
        vorige_scanmoment_weergave=resultaat["vorige_scanmoment_weergave"],
    )


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)
