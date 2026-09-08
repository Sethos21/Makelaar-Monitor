#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Makelaarsmonitor Historie v1.0

Doel
----
Voeg een volledige *_alles.csv scan toe aan een lokale SQLite-historie.

Per import:
- bewaart één snapshot per object;
- voorkomt dubbele import van dezelfde scan;
- onthoudt eerste en laatste waarneming;
- vergelijkt automatisch met de vorige scan van exact hetzelfde gebied;
- maakt een actuele objectenlijst met looptijd in de monitor;
- maakt een mutatierapport t.o.v. de vorige scan.

Voorbeeld:
    py makelaarsmonitor_historie_v10.py ^
      --scan makelaarsmonitor_uden_volkel_nistelrode_20260904_100955_alles.csv ^
      --plaatsen Uden Volkel Nistelrode

Standaard database:
    makelaarsmonitor_historie.sqlite
in dezelfde map waarin het commando wordt uitgevoerd.
"""

import argparse
import csv
import hashlib
import re
import sqlite3
from pathlib import Path
from datetime import datetime, date
from urllib.parse import urlparse

DELIM = ";"

SNAPSHOT_FIELDS = [
    "Peildatum", "Plaats", "Gevonden_bij_scan", "Bouwcategorie", "Nieuwbouwreden",
    "Resultaatpagina", "Toppositie", "Adres", "Vraagprijs", "Status",
    "Woonoppervlakte_m2", "Perceeloppervlakte_m2", "Slaapkamers",
    "Energielabel", "Makelaar", "Funda_detail_URL", "Bron", "Waarschuwing"
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
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=DELIM, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

def parse_int(v):
    s = clean(v).replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return int(round(float(s)))
    except Exception:
        return None

def parse_float(v):
    s = clean(v).replace(".", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None

def parse_scanmoment(scan_path, rows):
    """
    Gebruik bij voorkeur YYYYMMDD_HHMMSS uit bestandsnaam.
    Fallback: Peildatum + 12:00.
    """
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

def canonical_area(places):
    vals = sorted({clean(p) for p in places if clean(p)}, key=lambda x: x.casefold())
    return " | ".join(vals)

def scan_hash(scan_path, area, scanmoment):
    h = hashlib.sha256()
    h.update(Path(scan_path).read_bytes())
    h.update(area.encode("utf-8"))
    h.update(scanmoment.isoformat().encode("utf-8"))
    return h.hexdigest()

def validate_rows(rows, places):
    selected = {norm(p) for p in places}
    errors = []
    seen = set()
    cleaned = []

    for i, r in enumerate(rows, start=2):
        url = clean(r.get("Funda_detail_URL"))
        place = clean(r.get("Plaats"))

        if not url:
            errors.append(f"Rij {i}: Funda_detail_URL ontbreekt")
            continue
        if selected and norm(place) not in selected:
            errors.append(f"Rij {i}: plaats '{place}' valt buiten geselecteerd gebied")
            continue
        if url in seen:
            errors.append(f"Rij {i}: dubbele Funda_detail_URL")
            continue

        seen.add(url)
        cleaned.append(r)

    return cleaned, errors

def init_db(conn):
    conn.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS scans (
        scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_hash TEXT NOT NULL UNIQUE,
        scanmoment TEXT NOT NULL,
        peildatum TEXT,
        gebied TEXT NOT NULL,
        bronbestand TEXT NOT NULL,
        aantal_objecten INTEGER NOT NULL,
        imported_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_scans_gebied_moment
    ON scans(gebied, scanmoment);

    CREATE TABLE IF NOT EXISTS snapshots (
        scan_id INTEGER NOT NULL,
        funda_url TEXT NOT NULL,
        peildatum TEXT,
        plaats TEXT,
        gevonden_bij_scan TEXT,
        bouwcategorie TEXT,
        nieuwbouwreden TEXT,
        resultaatpagina TEXT,
        toppositie TEXT,
        adres TEXT,
        vraagprijs INTEGER,
        status TEXT,
        woonoppervlakte REAL,
        perceeloppervlakte REAL,
        slaapkamers INTEGER,
        energielabel TEXT,
        makelaar TEXT,
        bron TEXT,
        waarschuwing TEXT,
        PRIMARY KEY (scan_id, funda_url),
        FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
    );

    CREATE INDEX IF NOT EXISTS idx_snapshots_url
    ON snapshots(funda_url);

    CREATE INDEX IF NOT EXISTS idx_snapshots_scan
    ON snapshots(scan_id);
    """)
    conn.commit()

def insert_scan(conn, scan_hash_value, scanmoment, peildatum, gebied, bronbestand, rows):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO scans(scan_hash, scanmoment, peildatum, gebied, bronbestand, aantal_objecten, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_hash_value,
        scanmoment.isoformat(timespec="seconds"),
        peildatum,
        gebied,
        Path(bronbestand).name,
        len(rows),
        datetime.now().isoformat(timespec="seconds"),
    ))
    scan_id = cur.lastrowid

    payload = []
    for r in rows:
        payload.append((
            scan_id,
            clean(r.get("Funda_detail_URL")),
            clean(r.get("Peildatum")),
            clean(r.get("Plaats")),
            clean(r.get("Gevonden_bij_scan")),
            clean(r.get("Bouwcategorie")),
            clean(r.get("Nieuwbouwreden")),
            clean(r.get("Resultaatpagina")),
            clean(r.get("Toppositie")),
            clean(r.get("Adres")),
            parse_int(r.get("Vraagprijs")),
            clean(r.get("Status")),
            parse_float(r.get("Woonoppervlakte_m2")),
            parse_float(r.get("Perceeloppervlakte_m2")),
            parse_int(r.get("Slaapkamers")),
            clean(r.get("Energielabel")),
            clean(r.get("Makelaar")),
            clean(r.get("Bron")),
            clean(r.get("Waarschuwing")),
        ))

    cur.executemany("""
        INSERT INTO snapshots(
            scan_id, funda_url, peildatum, plaats, gevonden_bij_scan, bouwcategorie,
            nieuwbouwreden, resultaatpagina, toppositie, adres, vraagprijs, status,
            woonoppervlakte, perceeloppervlakte, slaapkamers, energielabel,
            makelaar, bron, waarschuwing
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, payload)

    conn.commit()
    return scan_id

def get_previous_scan_id(conn, gebied, current_scanmoment, current_scan_id):
    row = conn.execute("""
        SELECT scan_id
        FROM scans
        WHERE gebied = ?
          AND scanmoment < ?
          AND scan_id <> ?
        ORDER BY scanmoment DESC
        LIMIT 1
    """, (gebied, current_scanmoment.isoformat(timespec="seconds"), current_scan_id)).fetchone()
    return row[0] if row else None

def fetch_snapshot(conn, scan_id):
    rows = conn.execute("""
        SELECT
            funda_url, peildatum, plaats, gevonden_bij_scan, bouwcategorie,
            nieuwbouwreden, resultaatpagina, toppositie, adres, vraagprijs, status,
            woonoppervlakte, perceeloppervlakte, slaapkamers, energielabel,
            makelaar, bron, waarschuwing
        FROM snapshots
        WHERE scan_id = ?
    """, (scan_id,)).fetchall()

    cols = [
        "Funda_detail_URL", "Peildatum", "Plaats", "Gevonden_bij_scan",
        "Bouwcategorie", "Nieuwbouwreden", "Resultaatpagina", "Toppositie",
        "Adres", "Vraagprijs", "Status", "Woonoppervlakte_m2",
        "Perceeloppervlakte_m2", "Slaapkamers", "Energielabel",
        "Makelaar", "Bron", "Waarschuwing"
    ]
    return {r[0]: dict(zip(cols, r)) for r in rows}

def mutation_name(old, new):
    if old is None:
        return "Nieuw aanbod"
    if new is None:
        return "Uit aanbod"

    changes = []
    if old.get("Vraagprijs") != new.get("Vraagprijs"):
        changes.append("Prijs gewijzigd")
    if clean(old.get("Status")) != clean(new.get("Status")):
        changes.append("Status gewijzigd")
    if clean(old.get("Makelaar")) != clean(new.get("Makelaar")):
        changes.append("Makelaar gewijzigd")

    return " + ".join(changes) if changes else "Ongewijzigd"

def build_mutations(old, new, previous_scanmoment, current_scanmoment):
    out = []
    for url in sorted(set(old) | set(new)):
        o = old.get(url)
        n = new.get(url)
        mut = mutation_name(o, n)

        op = o.get("Vraagprijs") if o else None
        np = n.get("Vraagprijs") if n else None
        diff = ""
        diff_pct = ""
        if op is not None and np is not None and op != np:
            diff = np - op
            if op:
                diff_pct = round(100 * diff / op, 2)

        out.append({
            "Mutatie": mut,
            "Plaats": clean((n or o).get("Plaats")),
            "Adres": clean((n or o).get("Adres")),
            "Bouwcategorie": clean((n or o).get("Bouwcategorie")),
            "Makelaar_oud": clean(o.get("Makelaar")) if o else "",
            "Makelaar_nieuw": clean(n.get("Makelaar")) if n else "",
            "Status_oud": clean(o.get("Status")) if o else "",
            "Status_nieuw": clean(n.get("Status")) if n else "",
            "Vraagprijs_oud": op if op is not None else "",
            "Vraagprijs_nieuw": np if np is not None else "",
            "Prijsverschil": diff,
            "Prijsverschil_pct": diff_pct,
            "Vorige_scanmoment": previous_scanmoment or "",
            "Huidige_scanmoment": current_scanmoment,
            "Funda_detail_URL": url,
        })
    return out

def first_last_seen(conn, gebied, urls):
    """
    Eerste/laatste waarneming binnen exact hetzelfde gebied.
    """
    result = {}
    if not urls:
        return result

    placeholders = ",".join("?" for _ in urls)
    sql = f"""
        SELECT s.funda_url, MIN(sc.scanmoment), MAX(sc.scanmoment), COUNT(*)
        FROM snapshots s
        JOIN scans sc ON sc.scan_id = s.scan_id
        WHERE sc.gebied = ?
          AND s.funda_url IN ({placeholders})
        GROUP BY s.funda_url
    """
    params = [gebied] + list(urls)
    for url, first_seen, last_seen, count_seen in conn.execute(sql, params):
        result[url] = (first_seen, last_seen, count_seen)
    return result

def current_enriched(conn, gebied, current_scan_id, current_scanmoment):
    current = fetch_snapshot(conn, current_scan_id)
    fl = first_last_seen(conn, gebied, current.keys())
    out = []

    for url, r in current.items():
        first_seen, last_seen, count_seen = fl.get(url, ("", "", 1))
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
    ap.add_argument("--scan", required=True, help="Volledig *_alles.csv bestand")
    ap.add_argument("--plaatsen", nargs="+", required=True, help="Exact gekozen scanplaatsen")
    ap.add_argument("--database", default="makelaarsmonitor_historie.sqlite")
    ap.add_argument("--output-map", default=".")
    args = ap.parse_args()

    scan_path = Path(args.scan)
    if not scan_path.exists():
        raise SystemExit(f"Scanbestand niet gevonden: {scan_path}")

    rows = read_csv(scan_path)
    gebied = canonical_area(args.plaatsen)
    scanmoment = parse_scanmoment(scan_path, rows)
    peildatum = scanmoment.date().isoformat()

    rows, errors = validate_rows(rows, args.plaatsen)
    if errors:
        print("WAARSCHUWINGEN BIJ VALIDATIE:")
        for e in errors[:20]:
            print(" -", e)
        if len(errors) > 20:
            print(f" - ... en nog {len(errors)-20}")
        print("Alleen geldige, unieke rijen worden opgeslagen.")

    if not rows:
        raise SystemExit("Geen geldige objecten over om te importeren.")

    db_path = Path(args.database)
    conn = sqlite3.connect(db_path)
    init_db(conn)

    shash = scan_hash(scan_path, gebied, scanmoment)
    existing = conn.execute("SELECT scan_id FROM scans WHERE scan_hash = ?", (shash,)).fetchone()
    if existing:
        scan_id = existing[0]
        print("=" * 72)
        print("DEZE SCAN STOND AL IN DE HISTORIE")
        print(f"Scan-ID: {scan_id}")
        print(f"Database: {db_path.resolve()}")
        conn.close()
        return

    scan_id = insert_scan(
        conn, shash, scanmoment, peildatum, gebied, str(scan_path), rows
    )

    previous_scan_id = get_previous_scan_id(conn, gebied, scanmoment, scan_id)
    current = fetch_snapshot(conn, scan_id)

    previous = {}
    previous_scanmoment = ""
    if previous_scan_id:
        previous = fetch_snapshot(conn, previous_scan_id)
        row = conn.execute(
            "SELECT scanmoment FROM scans WHERE scan_id = ?",
            (previous_scan_id,)
        ).fetchone()
        previous_scanmoment = row[0] if row else ""

    mutations = build_mutations(
        previous, current, previous_scanmoment, scanmoment.isoformat(timespec="seconds")
    ) if previous_scan_id else []

    enriched = current_enriched(
        conn, gebied, scan_id, scanmoment.isoformat(timespec="seconds")
    )

    outdir = Path(args.output_map)
    outdir.mkdir(parents=True, exist_ok=True)

    area_name = "_".join(
        re.sub(r"[^a-z0-9]+", "_", p.casefold()).strip("_")
        for p in args.plaatsen
    )
    stamp = scanmoment.strftime("%Y%m%d_%H%M%S")

    current_path = outdir / f"historie_{area_name}_{stamp}_huidige_status.csv"
    current_fields = [
        "Peildatum", "Plaats", "Bouwcategorie", "Adres", "Vraagprijs", "Status",
        "Woonoppervlakte_m2", "Perceeloppervlakte_m2", "Slaapkamers",
        "Energielabel", "Makelaar", "Eerste_waarneming", "Laatste_waarneming",
        "Aantal_scans_gezien", "Dagen_in_monitor", "Funda_detail_URL",
        "Gevonden_bij_scan", "Nieuwbouwreden", "Bron", "Waarschuwing"
    ]
    write_csv(current_path, enriched, current_fields)

    mutation_path = None
    if previous_scan_id:
        mutation_path = outdir / f"historie_{area_name}_{stamp}_mutaties_tov_vorige.csv"
        mutation_fields = [
            "Mutatie", "Plaats", "Adres", "Bouwcategorie",
            "Makelaar_oud", "Makelaar_nieuw", "Status_oud", "Status_nieuw",
            "Vraagprijs_oud", "Vraagprijs_nieuw", "Prijsverschil",
            "Prijsverschil_pct", "Vorige_scanmoment", "Huidige_scanmoment",
            "Funda_detail_URL"
        ]
        write_csv(mutation_path, mutations, mutation_fields)

    total_scans = conn.execute(
        "SELECT COUNT(*) FROM scans WHERE gebied = ?", (gebied,)
    ).fetchone()[0]

    print("=" * 72)
    print("MAKELAARSMONITOR HISTORIE v1.0")
    print("=" * 72)
    print(f"Gebied: {gebied}")
    print(f"Scanmoment: {scanmoment.isoformat(timespec='seconds')}")
    print(f"Objecten geïmporteerd: {len(current)}")
    print(f"Aantal opgeslagen scans voor dit gebied: {total_scans}")
    print(f"Database: {db_path.resolve()}")

    if previous_scan_id:
        counts = {}
        for m in mutations:
            counts[m["Mutatie"]] = counts.get(m["Mutatie"], 0) + 1

        print("")
        print("Mutaties t.o.v. vorige scan:")
        for key in sorted(counts):
            print(f"  {key}: {counts[key]}")
    else:
        print("")
        print("Dit is de eerste opgeslagen scan voor dit gebied.")
        print("Vanaf de volgende scan worden mutaties automatisch berekend.")

    print("")
    print("Uitvoer:")
    print(f"  Huidige status: {current_path}")
    if mutation_path:
        print(f"  Mutaties:       {mutation_path}")

    conn.close()

if __name__ == "__main__":
    main()
