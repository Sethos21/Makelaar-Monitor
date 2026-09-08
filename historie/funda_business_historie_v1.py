#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Funda in Business - Historie v1.0 (Fase 2)

Doel
----
Voeg een Business-CSV (*_alles.csv van scanner/funda_business_scanner_v1.py)
toe aan een LOSSTAANDE SQLite-historie voor bedrijfsmatig vastgoed.

BELANGRIJK
----------
- Volledig onafhankelijk van historie/makelaarsmonitor_historie_v10.py (de
  woninghistorie-tool) en de woningentabellen. Niets daarvan wordt hier
  aangeraakt, geimporteerd of gewijzigd.
- Gebruikt een EIGEN, aparte SQLite-database (standaard
  data/funda_business_historie.sqlite), NIET de bestaande
  data/makelaarsmonitor_historie.sqlite. Zie docs/CLAUDE_STATUS.md voor de
  motivatie van deze keuze.
- Vergelijkt scans alleen binnen exact dezelfde combinatie van gebied
  (geselecteerde plaatsen) EN categorieen (geselecteerde objecttypes).
- "Uit aanbod" betekent uitsluitend: niet meer aangetroffen bij de vorige
  vergelijkbare scan. Nooit automatisch "verkocht"/"verhuurd" veronderstellen.
- Berekende_huur_per_jaar/_per_maand zijn afgeleide velden en worden NOOIT
  gebruikt voor mutatiedetectie - alleen Huurprijs + Huurprijs_eenheid tellen.

Voorbeeld:
    py historie\\funda_business_historie_v1.py --scan "<csv>" ^
        --plaatsen Uden --categorieen Kantoor Bedrijfsruimte

Standaard database:
    data/funda_business_historie.sqlite
"""

import argparse
import csv
import hashlib
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

DELIM = ";"

CSV_INVOERVELDEN = [
    "Peildatum", "Plaats", "Gezocht_categorie", "Objecttype", "Status",
    "Adres", "Koopprijs", "Koopprijs_conditie", "Huurprijs", "Huurprijs_eenheid",
    "Oppervlakte_m2", "Oppervlakte_extra_m2",
    "Berekende_huur_per_jaar", "Berekende_huur_per_maand",
    "Makelaar", "Funda_object_id", "Funda_detail_URL", "Bron", "Waarschuwing",
]


def clean(v):
    return "" if v is None else str(v).strip()


def norm(v):
    return clean(v).casefold()


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=DELIM))


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=DELIM, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def parse_int(v):
    s = clean(v)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def parse_float(v):
    s = clean(v)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def parse_scanmoment(scan_path, rows):
    """Bij voorkeur YYYYMMDD_HHMMSS uit de bestandsnaam (zoals de scanner die
    zelf gebruikt). Fallback: Peildatum + 12:00. Laatste redmiddel: mtime."""
    name = Path(scan_path).name
    m = re.search(r"(20\d{6})_(\d{6})", name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")

    for r in rows:
        p = clean(r.get("Peildatum"))
        if p:
            try:
                return datetime.strptime(p + " 12:00:00", "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

    return datetime.fromtimestamp(Path(scan_path).stat().st_mtime)


def canonical_gebied(places):
    vals = sorted({clean(p) for p in places if clean(p)}, key=lambda x: x.casefold())
    return " | ".join(vals)


def canonical_categorieen(categorieen):
    vals = sorted({clean(c) for c in categorieen if clean(c)}, key=lambda x: x.casefold())
    return " | ".join(vals)


def scan_hash(scan_path, gebied, categorieen, scanmoment):
    h = hashlib.sha256()
    h.update(Path(scan_path).read_bytes())
    h.update(gebied.encode("utf-8"))
    h.update(categorieen.encode("utf-8"))
    h.update(scanmoment.isoformat().encode("utf-8"))
    return h.hexdigest()


def object_id_uit_url(url):
    """Aanvullende controle/fallback: het numerieke object-ID staat ook
    ingebed in de detail-URL (.../object-{id}-adres-slug/)."""
    m = re.search(r"/object-(\d+)-", url or "")
    return m.group(1) if m else ""


def validate_rows(rows, plaatsen, categorieen):
    """Analoog aan validate_rows() in de woninghistorie, maar met
    Funda_object_id als primaire sleutel (URL als aanvullende controle) en
    een categorie-overlapcontrole i.p.v. een enkelvoudig transactietype."""
    selected_places = {norm(p) for p in plaatsen}
    selected_cats = {norm(c) for c in categorieen}
    errors = []
    seen_ids = set()
    cleaned = []

    for i, r in enumerate(rows, start=2):
        object_id = clean(r.get("Funda_object_id"))
        url = clean(r.get("Funda_detail_URL"))
        plaats = clean(r.get("Plaats"))
        gezocht = clean(r.get("Gezocht_categorie"))

        if not object_id:
            afgeleid = object_id_uit_url(url)
            if afgeleid:
                r["Funda_object_id"] = afgeleid
                object_id = afgeleid
                errors.append(f"Rij {i}: Funda_object_id ontbrak, afgeleid uit URL ({afgeleid})")
            else:
                errors.append(f"Rij {i}: Funda_object_id ontbreekt en kon niet uit de URL worden afgeleid")
                continue
        else:
            url_id = object_id_uit_url(url)
            if url_id and url_id != object_id:
                errors.append(
                    f"Rij {i}: Funda_object_id ({object_id}) komt niet overeen met het ID in de URL ({url_id}) "
                    "- Funda_object_id wordt als leidend beschouwd"
                )

        if selected_places and norm(plaats) not in selected_places:
            errors.append(f"Rij {i}: plaats '{plaats}' valt buiten geselecteerd gebied")
            continue

        if selected_cats:
            rij_cats = {norm(c) for c in gezocht.split(";")}
            # ook enkelvoudige waarden met "|" of "," als scheidingsteken tolereren
            for sep in (",", "|"):
                if sep in gezocht:
                    rij_cats |= {norm(c) for c in gezocht.split(sep)}
            if not (rij_cats & selected_cats):
                errors.append(f"Rij {i}: gezochte categorie '{gezocht}' valt buiten geselecteerde categorieen")
                continue

        if object_id in seen_ids:
            errors.append(f"Rij {i}: dubbel Funda_object_id ({object_id}) binnen dit bestand")
            continue

        seen_ids.add(object_id)
        cleaned.append(r)

    return cleaned, errors


def init_db(conn):
    conn.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS business_scans (
        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_hash TEXT NOT NULL UNIQUE,
        scanmoment TEXT NOT NULL,
        peildatum TEXT,
        gebied TEXT NOT NULL,
        categorieen TEXT NOT NULL,
        bronbestand TEXT NOT NULL,
        aantal_objecten INTEGER NOT NULL,
        imported_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_business_scans_scope_moment
    ON business_scans(gebied, categorieen, scanmoment);

    CREATE TABLE IF NOT EXISTS business_snapshots (
        scan_id INTEGER NOT NULL,
        funda_object_id TEXT NOT NULL,
        funda_url TEXT,
        peildatum TEXT,
        plaats TEXT,
        gezocht_categorie TEXT,
        objecttype TEXT,
        status TEXT,
        adres TEXT,
        koopprijs INTEGER,
        koopprijs_conditie TEXT,
        huurprijs INTEGER,
        huurprijs_eenheid TEXT,
        oppervlakte_m2 REAL,
        oppervlakte_extra_m2 REAL,
        berekende_huur_per_jaar INTEGER,
        berekende_huur_per_maand INTEGER,
        makelaar TEXT,
        bron TEXT,
        waarschuwing TEXT,
        PRIMARY KEY (scan_id, funda_object_id),
        FOREIGN KEY (scan_id) REFERENCES business_scans(scan_id)
    );

    CREATE INDEX IF NOT EXISTS idx_business_snapshots_object
    ON business_snapshots(funda_object_id);

    CREATE INDEX IF NOT EXISTS idx_business_snapshots_scan
    ON business_snapshots(scan_id);
    """)
    conn.commit()


def insert_scan(conn, scan_hash_value, scanmoment, peildatum, gebied, categorieen, bronbestand, rows):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO business_scans(
            scan_hash, scanmoment, peildatum, gebied, categorieen,
            bronbestand, aantal_objecten, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_hash_value,
        scanmoment.isoformat(timespec="seconds"),
        peildatum,
        gebied,
        categorieen,
        Path(bronbestand).name,
        len(rows),
        datetime.now().isoformat(timespec="seconds"),
    ))
    scan_id = cur.lastrowid

    payload = []
    for r in rows:
        payload.append((
            scan_id,
            clean(r.get("Funda_object_id")),
            clean(r.get("Funda_detail_URL")),
            clean(r.get("Peildatum")),
            clean(r.get("Plaats")),
            clean(r.get("Gezocht_categorie")),
            clean(r.get("Objecttype")),
            clean(r.get("Status")),
            clean(r.get("Adres")),
            parse_int(r.get("Koopprijs")),
            clean(r.get("Koopprijs_conditie")),
            parse_int(r.get("Huurprijs")),
            clean(r.get("Huurprijs_eenheid")),
            parse_float(r.get("Oppervlakte_m2")),
            parse_float(r.get("Oppervlakte_extra_m2")),
            parse_int(r.get("Berekende_huur_per_jaar")),
            parse_int(r.get("Berekende_huur_per_maand")),
            clean(r.get("Makelaar")),
            clean(r.get("Bron")),
            clean(r.get("Waarschuwing")),
        ))

    cur.executemany("""
        INSERT INTO business_snapshots(
            scan_id, funda_object_id, funda_url, peildatum, plaats,
            gezocht_categorie, objecttype, status, adres,
            koopprijs, koopprijs_conditie, huurprijs, huurprijs_eenheid,
            oppervlakte_m2, oppervlakte_extra_m2,
            berekende_huur_per_jaar, berekende_huur_per_maand,
            makelaar, bron, waarschuwing
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, payload)

    conn.commit()
    return scan_id


def get_previous_scan_id(conn, gebied, categorieen, current_scanmoment, current_scan_id):
    """Alleen scans met EXACT hetzelfde gebied EN dezelfde categorieen tellen
    mee als 'vergelijkbaar'."""
    row = conn.execute("""
        SELECT scan_id
        FROM business_scans
        WHERE gebied = ?
          AND categorieen = ?
          AND scanmoment < ?
          AND scan_id <> ?
        ORDER BY scanmoment DESC
        LIMIT 1
    """, (gebied, categorieen, current_scanmoment.isoformat(timespec="seconds"), current_scan_id)).fetchone()
    return row[0] if row else None


def fetch_snapshot(conn, scan_id):
    kolommen = [
        "funda_object_id", "funda_url", "peildatum", "plaats", "gezocht_categorie",
        "objecttype", "status", "adres", "koopprijs", "koopprijs_conditie",
        "huurprijs", "huurprijs_eenheid", "oppervlakte_m2", "oppervlakte_extra_m2",
        "berekende_huur_per_jaar", "berekende_huur_per_maand", "makelaar", "bron", "waarschuwing",
    ]
    rows = conn.execute(
        f"SELECT {', '.join(kolommen)} FROM business_snapshots WHERE scan_id = ?", (scan_id,)
    ).fetchall()

    veldnamen = [
        "Funda_object_id", "Funda_detail_URL", "Peildatum", "Plaats", "Gezocht_categorie",
        "Objecttype", "Status", "Adres", "Koopprijs", "Koopprijs_conditie",
        "Huurprijs", "Huurprijs_eenheid", "Oppervlakte_m2", "Oppervlakte_extra_m2",
        "Berekende_huur_per_jaar", "Berekende_huur_per_maand", "Makelaar", "Bron", "Waarschuwing",
    ]
    return {r[0]: dict(zip(veldnamen, r)) for r in rows}


def mutation_name(old, new):
    """Bewust GEEN gebruik van Berekende_huur_per_*: uitsluitend de
    brongegevens (Koopprijs, Huurprijs, Huurprijs_eenheid, Status, Makelaar,
    Oppervlakte_m2, Objecttype, Gezocht_categorie) tellen voor mutatiedetectie."""
    if old is None:
        return "Nieuw aanbod"
    if new is None:
        return "Uit aanbod"

    changes = []
    if old.get("Koopprijs") != new.get("Koopprijs"):
        changes.append("Koopprijs gewijzigd")
    if old.get("Huurprijs") != new.get("Huurprijs"):
        changes.append("Huurprijs gewijzigd")
    if clean(old.get("Huurprijs_eenheid")) != clean(new.get("Huurprijs_eenheid")):
        changes.append("Huurprijs_eenheid gewijzigd")
    if clean(old.get("Status")) != clean(new.get("Status")):
        changes.append("Status gewijzigd")
    if clean(old.get("Makelaar")) != clean(new.get("Makelaar")):
        changes.append("Makelaar gewijzigd")
    if old.get("Oppervlakte_m2") != new.get("Oppervlakte_m2"):
        changes.append("Oppervlakte gewijzigd")
    if clean(old.get("Objecttype")) != clean(new.get("Objecttype")) or \
       clean(old.get("Gezocht_categorie")) != clean(new.get("Gezocht_categorie")):
        changes.append("Objecttype/categorie gewijzigd")

    return " + ".join(changes) if changes else "Ongewijzigd"


def build_mutations(old, new, previous_scanmoment, current_scanmoment):
    out = []
    for object_id in sorted(set(old) | set(new)):
        o = old.get(object_id)
        n = new.get(object_id)
        mut = mutation_name(o, n)

        out.append({
            "Mutatie": mut,
            "Plaats": clean((n or o).get("Plaats")),
            "Adres": clean((n or o).get("Adres")),
            "Objecttype_oud": clean(o.get("Objecttype")) if o else "",
            "Objecttype_nieuw": clean(n.get("Objecttype")) if n else "",
            "Gezocht_categorie_oud": clean(o.get("Gezocht_categorie")) if o else "",
            "Gezocht_categorie_nieuw": clean(n.get("Gezocht_categorie")) if n else "",
            "Makelaar_oud": clean(o.get("Makelaar")) if o else "",
            "Makelaar_nieuw": clean(n.get("Makelaar")) if n else "",
            "Status_oud": clean(o.get("Status")) if o else "",
            "Status_nieuw": clean(n.get("Status")) if n else "",
            "Koopprijs_oud": o.get("Koopprijs") if o and o.get("Koopprijs") is not None else "",
            "Koopprijs_nieuw": n.get("Koopprijs") if n and n.get("Koopprijs") is not None else "",
            "Koopprijs_conditie_oud": clean(o.get("Koopprijs_conditie")) if o else "",
            "Koopprijs_conditie_nieuw": clean(n.get("Koopprijs_conditie")) if n else "",
            "Huurprijs_oud": o.get("Huurprijs") if o and o.get("Huurprijs") is not None else "",
            "Huurprijs_nieuw": n.get("Huurprijs") if n and n.get("Huurprijs") is not None else "",
            "Huurprijs_eenheid_oud": clean(o.get("Huurprijs_eenheid")) if o else "",
            "Huurprijs_eenheid_nieuw": clean(n.get("Huurprijs_eenheid")) if n else "",
            "Oppervlakte_oud": o.get("Oppervlakte_m2") if o and o.get("Oppervlakte_m2") is not None else "",
            "Oppervlakte_nieuw": n.get("Oppervlakte_m2") if n and n.get("Oppervlakte_m2") is not None else "",
            "Vorige_scanmoment": previous_scanmoment or "",
            "Huidige_scanmoment": current_scanmoment,
            "Funda_object_id": object_id,
            "Funda_detail_URL": (n or o).get("Funda_detail_URL"),
        })
    return out


def first_last_seen(conn, gebied, categorieen, object_ids):
    """Eerste/laatste waarneming binnen exact hetzelfde gebied EN dezelfde
    categorieen."""
    result = {}
    if not object_ids:
        return result

    placeholders = ",".join("?" for _ in object_ids)
    sql = f"""
        SELECT s.funda_object_id, MIN(sc.scanmoment), MAX(sc.scanmoment), COUNT(*)
        FROM business_snapshots s
        JOIN business_scans sc ON sc.scan_id = s.scan_id
        WHERE sc.gebied = ?
          AND sc.categorieen = ?
          AND s.funda_object_id IN ({placeholders})
        GROUP BY s.funda_object_id
    """
    params = [gebied, categorieen] + list(object_ids)
    for object_id, first_seen, last_seen, count_seen in conn.execute(sql, params):
        result[object_id] = (first_seen, last_seen, count_seen)
    return result


def current_enriched(conn, gebied, categorieen, current_scan_id, current_scanmoment):
    current = fetch_snapshot(conn, current_scan_id)
    fl = first_last_seen(conn, gebied, categorieen, current.keys())
    out = []

    for object_id, r in current.items():
        first_seen, last_seen, count_seen = fl.get(object_id, ("", "", 1))
        days = ""
        if first_seen:
            try:
                d1 = datetime.fromisoformat(first_seen)
                d2 = datetime.fromisoformat(current_scanmoment)
                days = max(0, (d2.date() - d1.date()).days)
            except Exception:
                pass

        row = dict(r)
        row["Eerste_waarneming"] = first_seen
        row["Laatste_waarneming"] = last_seen
        row["Aantal_scans_gezien"] = count_seen
        row["Dagen_in_monitor"] = days
        out.append(row)

    out.sort(key=lambda r: (norm(r.get("Plaats")), norm(r.get("Adres"))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", required=True, help="Volledig *_alles.csv bestand van funda_business_scanner_v1.py")
    ap.add_argument("--plaatsen", nargs="+", required=True, help="Exact gekozen scanplaatsen")
    ap.add_argument("--categorieen", nargs="+", required=True, help="Exact gekozen categorieen (bv. Kantoor Bedrijfsruimte)")
    ap.add_argument("--database", default="data/funda_business_historie.sqlite")
    ap.add_argument("--output-map", default="output/bedrijfsmatig/historie")
    args = ap.parse_args()

    scan_path = Path(args.scan)
    if not scan_path.exists():
        raise SystemExit(f"Scanbestand niet gevonden: {scan_path}")

    rows = read_csv(scan_path)
    gebied = canonical_gebied(args.plaatsen)
    categorieen = canonical_categorieen(args.categorieen)
    scanmoment = parse_scanmoment(scan_path, rows)
    peildatum = scanmoment.date().isoformat()

    rows, errors = validate_rows(rows, args.plaatsen, args.categorieen)
    if errors:
        print("WAARSCHUWINGEN BIJ VALIDATIE:")
        for e in errors[:20]:
            print(" -", e)
        if len(errors) > 20:
            print(f" - ... en nog {len(errors) - 20}")
        print("Alleen geldige, unieke rijen worden opgeslagen.")

    if not rows:
        raise SystemExit("Geen geldige objecten over om te importeren.")

    db_path = Path(args.database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    init_db(conn)

    shash = scan_hash(scan_path, gebied, categorieen, scanmoment)
    existing = conn.execute("SELECT scan_id FROM business_scans WHERE scan_hash = ?", (shash,)).fetchone()
    if existing:
        scan_id = existing[0]
        print("=" * 72)
        print("DEZE BUSINESS-SCAN STOND AL IN DE HISTORIE")
        print(f"Scan-ID: {scan_id}")
        print(f"Database: {db_path.resolve()}")
        conn.close()
        return

    scan_id = insert_scan(
        conn, shash, scanmoment, peildatum, gebied, categorieen, str(scan_path), rows
    )

    previous_scan_id = get_previous_scan_id(conn, gebied, categorieen, scanmoment, scan_id)
    current = fetch_snapshot(conn, scan_id)

    previous = {}
    previous_scanmoment = ""
    if previous_scan_id:
        previous = fetch_snapshot(conn, previous_scan_id)
        row = conn.execute(
            "SELECT scanmoment FROM business_scans WHERE scan_id = ?",
            (previous_scan_id,)
        ).fetchone()
        previous_scanmoment = row[0] if row else ""

    mutations = build_mutations(
        previous, current, previous_scanmoment, scanmoment.isoformat(timespec="seconds")
    ) if previous_scan_id else []

    enriched = current_enriched(conn, gebied, categorieen, scan_id, scanmoment.isoformat(timespec="seconds"))

    outdir = Path(args.output_map)
    outdir.mkdir(parents=True, exist_ok=True)

    scope_naam = "_".join(
        re.sub(r"[^a-z0-9]+", "_", p.casefold()).strip("_")
        for p in (list(args.plaatsen) + list(args.categorieen))
    )
    stamp = scanmoment.strftime("%Y%m%d_%H%M%S")

    current_path = outdir / f"funda_business_historie_{scope_naam}_{stamp}_huidige_status.csv"
    current_fields = [
        "Peildatum", "Plaats", "Gezocht_categorie", "Objecttype", "Status", "Adres",
        "Koopprijs", "Koopprijs_conditie", "Huurprijs", "Huurprijs_eenheid",
        "Oppervlakte_m2", "Oppervlakte_extra_m2",
        "Berekende_huur_per_jaar", "Berekende_huur_per_maand",
        "Eerste_waarneming", "Laatste_waarneming", "Aantal_scans_gezien", "Dagen_in_monitor",
        "Makelaar", "Funda_object_id", "Funda_detail_URL", "Bron", "Waarschuwing",
    ]
    write_csv(current_path, enriched, current_fields)

    mutation_path = None
    if previous_scan_id:
        mutation_path = outdir / f"funda_business_historie_{scope_naam}_{stamp}_mutaties_tov_vorige.csv"
        mutation_fields = [
            "Mutatie", "Plaats", "Adres", "Objecttype_oud", "Objecttype_nieuw",
            "Gezocht_categorie_oud", "Gezocht_categorie_nieuw",
            "Makelaar_oud", "Makelaar_nieuw", "Status_oud", "Status_nieuw",
            "Koopprijs_oud", "Koopprijs_nieuw", "Koopprijs_conditie_oud", "Koopprijs_conditie_nieuw",
            "Huurprijs_oud", "Huurprijs_nieuw", "Huurprijs_eenheid_oud", "Huurprijs_eenheid_nieuw",
            "Oppervlakte_oud", "Oppervlakte_nieuw",
            "Vorige_scanmoment", "Huidige_scanmoment", "Funda_object_id", "Funda_detail_URL",
        ]
        write_csv(mutation_path, mutations, mutation_fields)

    total_scans = conn.execute(
        "SELECT COUNT(*) FROM business_scans WHERE gebied = ? AND categorieen = ?", (gebied, categorieen)
    ).fetchone()[0]

    print("=" * 72)
    print("FUNDA IN BUSINESS HISTORIE v1.0")
    print("=" * 72)
    print(f"Gebied: {gebied}")
    print(f"Categorieen: {categorieen}")
    print(f"Scanmoment: {scanmoment.isoformat(timespec='seconds')}")
    print(f"Objecten geimporteerd: {len(current)}")
    print(f"Aantal opgeslagen scans voor deze gebied+categorie-combinatie: {total_scans}")
    print(f"Database: {db_path.resolve()}")

    if previous_scan_id:
        counts = {}
        for m in mutations:
            counts[m["Mutatie"]] = counts.get(m["Mutatie"], 0) + 1

        print("")
        print("Mutaties t.o.v. vorige vergelijkbare scan:")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
    else:
        print("")
        print("Dit is de eerste opgeslagen scan (nulmeting) voor deze exacte combinatie")
        print("van gebied + categorieen. Er is nog geen vorige vergelijkbare scan om")
        print("mee te vergelijken - vanaf de volgende scan met dezelfde combinatie")
        print("worden mutaties automatisch berekend.")

    print("")
    print("Uitvoer:")
    print(f"  Huidige status: {current_path}")
    if mutation_path:
        print(f"  Mutaties:       {mutation_path}")

    conn.close()


if __name__ == "__main__":
    main()
