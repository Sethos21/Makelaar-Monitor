"""Regressietests voor Makelaar Monitor - Woningen-analyse (DEEL G/L, bouwronde
2026-09-09). Read-only tegen de bestaande productie-SQLite (geen scans, geen
schrijfacties, geen fixtures die de database veranderen). Draai met:

    py tests/test_regressie.py

Geen pytest-afhankelijkheid nodig (geen extra dependency) - eenvoudige
assert-gebaseerde checks, consistent met de rest van dit project.

Vastgelegde validatiecase (DEEL E): de scan van 08-09-2026 (scan_id 5 -> 6,
gebied "Heesch | Heeswijk-Dinther | Nistelrode | Vinkel | Vorstenbosch") ging
van 39 naar 37 objecten (netto -2, 2x "Uit aanbod", bevestigd door de
gebruiker als een echte, kloppende marktverandering - GEEN scannerfout). Deze
exacte reeks wordt hier als vaste regressiecontrole gebruikt zolang scan_id 5
en 6 in de database aanwezig blijven."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as appmod

checks: list[tuple[str, bool]] = []


def check(desc: str, ok: bool, extra: str = "") -> None:
    checks.append((desc, ok))
    print(("OK  " if ok else "FAIL"), desc, extra)


class FakeArgs(dict):
    """Minimale stand-in voor Flask's request.args (MultiDict) met
    getlist()/get(), voor rechtstreekse aanroepen van bouw_analyseresultaat()
    buiten een echte request om."""

    def getlist(self, k):
        v = dict.get(self, k)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def get(self, k, default=None):
        v = dict.get(self, k, default)
        return v[0] if isinstance(v, list) else v


def maak_args(**kwargs) -> FakeArgs:
    return FakeArgs(kwargs)


def test_routes_geen_serverfout(client) -> None:
    """Brede sweep: alle bekende routes moeten < 400 teruggeven (DEEL J: een
    onverwachte fout zou nu de nette foutpagina + status 500 opleveren i.p.v.
    een ruwe traceback, dus status_code is de betrouwbare test-indicator)."""
    plaatsen = ["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"]
    routes = [
        ("/", {}),
        ("/scan/status", {}),
        ("/business/scan/status", {}),
        ("/dashboard", {"markt": "woningen"}),
        ("/dashboard", {"markt": "bedrijfsmatig"}),
        ("/analyse", {"plaats": plaatsen, "status": "Beschikbaar", "bouwcategorie": "Bestaande bouw"}),
        ("/analyse", {"plaats": plaatsen, "status": "Beschikbaar", "bouwcategorie": "Bestaande bouw", "geo_niveau": "gemeente"}),
        ("/export/csv", {"plaats": plaatsen, "status": "Beschikbaar", "bouwcategorie": "Bestaande bouw"}),
        ("/business/analyse", {"business_plaats": "Uden", "business_categorie": ["Kantoor", "Bedrijfsruimte"]}),
    ]
    for route, params in routes:
        resp = client.get(route, query_string=params)
        check(f"route {route} {params} -> <400", resp.status_code < 400, f"(status={resp.status_code})")


def test_deel_e_validatiecase(con) -> None:
    """De vastgelegde, door de gebruiker bevestigde marktdynamiek-case
    (scan_id 5 -> 6, 39 -> 37 objecten). Sinds de stabilisatieronde van
    2026-09-09 is scan_id 6 niet meer 'de actuele scan' voor dit gebied
    (scan_id 7, 118 objecten, is nieuwer) - deze case wordt daarom nu
    expliciet historisch opgevraagd (scan_id=6), wat meteen ook dient als
    regressietest voor Historische Analyse (DEEL C, "historische scans
    blijven raadpleegbaar")."""
    scan5 = con.execute("SELECT aantal_objecten FROM scans WHERE scan_id = 5").fetchone()
    scan6 = con.execute("SELECT aantal_objecten FROM scans WHERE scan_id = 6").fetchone()
    if not scan5 or not scan6:
        check("DEEL E validatiecase (scan_id 5/6 aanwezig)", False, "scans niet (meer) aanwezig - case niet herhaalbaar")
        return
    check("DEEL E: scan_id 5 heeft 39 objecten", scan5["aantal_objecten"] == 39, f"(was {scan5['aantal_objecten']})")
    check("DEEL E: scan_id 6 heeft 37 objecten", scan6["aantal_objecten"] == 37, f"(was {scan6['aantal_objecten']})")

    args = maak_args(
        plaats=["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"],
        status="Beschikbaar", bouwcategorie="Bestaande bouw", scan_id="6",
    )
    r = appmod.bouw_analyseresultaat(args)
    check("DEEL E: scaninfo.scan_id == 6 (historische weergave gevonden)", r["scaninfo"]["scan_id"] == 6)
    check("DEEL E: kpis.aantal_objecten (gefilterd) == 9", r["kpis"]["aantal_objecten"] == 9, f"(was {r['kpis']['aantal_objecten']})")
    check("DEEL E: netto_verandering_totaal == -2", r["netto_verandering_totaal"] == -2, f"(was {r['netto_verandering_totaal']})")
    check("DEEL E: vorige/huidige totaal == 39/37", (r["vorige_aantal_objecten_totaal"], r["huidige_aantal_objecten_totaal"]) == (39, 37))
    check(
        "DEEL E: gefilterde marktdynamiek verschilt bewust van het totaal (geen vermengde populaties)",
        r["netto_verandering"] != r["netto_verandering_totaal"] or r["kpis"]["aantal_objecten"] != r["huidige_aantal_objecten_totaal"],
    )


def test_deel_g_sluitcontroles(con, filter_combo: dict, label: str) -> dict:
    """DEEL G, checks 1/2/3/4/7 voor Woningen, voor één filtercombinatie.
    Retourneert een samenvattingsdict voor de cross-checktabel (DEEL L)."""
    args = maak_args(**filter_combo)
    r = appmod.bouw_analyseresultaat(args)
    resultaat = {"label": label, "sluit": True}

    if r["foutmelding"]:
        check(f"[{label}] geen foutmelding", False, r["foutmelding"])
        resultaat["sluit"] = False
        return resultaat

    totaal = r["kpis"]["aantal_objecten"]
    resultaat["analyse_aantal"] = totaal

    # 1) KPI actief aanbod == som objecten makelaarstabel (incl. Onbekend)
    som_makelaars = sum(m["aantal"] for m in r["makelaarstabel"])
    ok1 = som_makelaars == totaal
    check(f"[{label}] G1: som(makelaarstabel) == KPI aantal_objecten", ok1, f"({som_makelaars} vs {totaal})")
    resultaat["makelaarstabel_aantal"] = som_makelaars
    resultaat["sluit"] &= ok1

    # 2) KPI actief aanbod == som objecten geografische verdeling (plaats-niveau)
    som_plaats = sum(p["aantal"] for p in r["plaatsverdeling"])
    ok2 = som_plaats == totaal
    check(f"[{label}] G2: som(plaatsverdeling) == KPI aantal_objecten", ok2, f"({som_plaats} vs {totaal})")
    resultaat["plaatsverdeling_aantal"] = som_plaats
    resultaat["sluit"] &= ok2

    som_gemeente = sum(g["aantal"] for g in r["gemeenteverdeling"])
    ok2b = som_gemeente == totaal
    check(f"[{label}] G2b: som(gemeenteverdeling) == KPI aantal_objecten", ok2b, f"({som_gemeente} vs {totaal})")
    resultaat["sluit"] &= ok2b

    # 3) Som marktaandeel (incl. Onbekend) circa 100%
    som_pct = sum(m["aandeel_pct"] for m in r["makelaarstabel"])
    # Tolerantie schaalt licht mee met het aantal makelaars: elke rij rondt
    # onafhankelijk af op 1 decimaal, dus met veel (kleine) makelaars stapelt
    # de afrondingsafwijking legitiem op (bv. 27 makelaars -> tot ~1pp) -
    # geen berekeningsfout, zie ook docs/BUGLIST.md.
    tolerantie = max(1.0, 0.05 * len(r["makelaarstabel"]))
    ok3 = totaal == 0 or abs(som_pct - 100) < tolerantie
    check(f"[{label}] G3: som(marktaandeel) circa 100%", ok3, f"({som_pct}%, tolerantie {tolerantie})")
    resultaat["sluit"] &= ok3

    # 4) Marktaandeel per makelaar == objecten / totaal * 100
    ok4 = all(m["aandeel_pct"] == (round(m["aantal"] / totaal * 100, 1) if totaal else 0) for m in r["makelaarstabel"])
    check(f"[{label}] G4: marktaandeel-formule klopt per makelaar", ok4)
    resultaat["sluit"] &= ok4

    # 5) Totale vraagwaarde KPI == som vraagwaarde per makelaar (zelfde populatie)
    som_makelaar_waarde = sum(
        int(m["totale_vraagwaarde_weergave"].replace("€", "").replace(".", "").strip())
        for m in r["makelaarstabel"] if m["totale_vraagwaarde_weergave"] != "-"
    )
    kpi_waarde = int(r["kpis"]["totale_vraagprijs"].replace("€", "").replace(".", "").strip())
    ok5 = som_makelaar_waarde == kpi_waarde
    check(f"[{label}] G5: som(vraagwaarde makelaars) == KPI totale vraagwaarde", ok5, f"({som_makelaar_waarde} vs {kpi_waarde})")
    resultaat["sluit"] &= ok5

    # 6) Totale vraagwaarde KPI == som vraagwaarde per plaats
    som_plaats_waarde = sum(
        int(p["totale_vraagwaarde_weergave"].replace("€", "").replace(".", "").strip())
        for p in r["plaatsverdeling"] if p["totale_vraagwaarde_weergave"] != "-"
    )
    ok6 = som_plaats_waarde == kpi_waarde
    check(f"[{label}] G6: som(vraagwaarde plaatsen) == KPI totale vraagwaarde", ok6, f"({som_plaats_waarde} vs {kpi_waarde})")
    resultaat["sluit"] &= ok6

    # 7) Prijssegmenten: som objecten met bekende positieve vraagprijs ==
    #    aantal geselecteerde objecten met bekende positieve vraagprijs
    som_segmenten = sum(s["aantal"] for s in r["segmentanalyse"])
    aantal_met_prijs = sum(1 for row in r["ruwe_rijen"] if row.get("vraagprijs") and row["vraagprijs"] > 0)
    ok7 = som_segmenten == aantal_met_prijs
    check(f"[{label}] G7: som(segmentanalyse) == aantal met bekende positieve vraagprijs", ok7, f"({som_segmenten} vs {aantal_met_prijs})")
    resultaat["sluit"] &= ok7

    resultaat["dashboard_vergelijkbaar"] = "n.v.t. (zie test_deel_g9_analyse_vs_dashboard)"
    return resultaat


def test_deel_g10_naamvarianten(con) -> None:
    """G10: geen makelaar mag door naamformattering onbedoeld dubbel
    voorkomen (casefold-botsing tussen verschillende ruwe schrijfwijzen) -
    signaleren, GEEN agressieve fuzzy matching."""
    scan_id = appmod.haal_laatste_scan_id(con)
    namen = [r["makelaar"] for r in con.execute(
        "SELECT DISTINCT makelaar FROM snapshots WHERE scan_id = ? AND makelaar IS NOT NULL AND makelaar <> ''",
        (scan_id,),
    )]
    per_casefold: dict[str, set] = {}
    for naam in namen:
        per_casefold.setdefault(naam.strip().casefold(), set()).add(naam.strip())
    varianten = {k: v for k, v in per_casefold.items() if len(v) > 1}
    check("G10: geen makelaars-naamvarianten (casefold-botsing) in nieuwste scan", not varianten, f"{varianten}" if varianten else "")


def test_deel_c8_deterministisch(con) -> None:
    """C8: dezelfde scan + dezelfde filters moet deterministisch dezelfde
    uitkomst geven."""
    args = maak_args(
        plaats=["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"],
        status="Beschikbaar", bouwcategorie="Bestaande bouw", scan_id="5",
    )
    r1 = appmod.bouw_analyseresultaat(args)
    r2 = appmod.bouw_analyseresultaat(args)
    check(
        "C8: dezelfde historische scan+filters -> deterministisch dezelfde uitkomst",
        r1["kpis"]["aantal_objecten"] == r2["kpis"]["aantal_objecten"]
        and r1["makelaarstabel"] == r2["makelaarstabel"]
        and r1["scaninfo"]["scan_id"] == r2["scaninfo"]["scan_id"] == 5,
    )


def test_deel_b2_makelaarsprofiel_geen_typeerror(client, con) -> None:
    """B2: geen enkele makelaarsprofielroute mag TypeError geven - test met
    de nieuwste scan, meerdere echte makelaars + randgevallen."""
    from urllib.parse import quote

    scan_id = appmod.haal_laatste_scan_id(con)
    namen = [r["makelaar"] for r in con.execute(
        "SELECT DISTINCT makelaar FROM snapshots WHERE scan_id = ? AND makelaar IS NOT NULL AND makelaar <> ''",
        (scan_id,),
    )]
    qs = [("plaats", p) for p in ["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"]] + \
         [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    for naam in namen + ["Onbekend", "Niet-bestaande Makelaar XYZ"]:
        resp = client.get("/makelaar/woningen/" + quote(naam), query_string=qs)
        check(f"B2: makelaarsprofiel '{naam}' geen serverfout", resp.status_code == 200, f"(status={resp.status_code})")


def hoofd() -> int:
    if not appmod.DB_PATH.exists():
        print("Database niet gevonden - regressietests overgeslagen.")
        return 0

    appmod.app.testing = True
    client = appmod.app.test_client()
    con = appmod.get_readonly_connection()
    try:
        test_routes_geen_serverfout(client)
        test_deel_e_validatiecase(con)
        test_deel_c8_deterministisch(con)
        test_deel_b2_makelaarsprofiel_geen_typeerror(client, con)
        test_deel_g10_naamvarianten(con)

        print("\n=== DEEL L: cross-checktabel (4 filtercombinaties, nieuwste 5-plaatsen-scan) ===")
        plaatsen = ["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"]
        combos = {
            "A. Beschikbaar + Bestaande bouw": dict(plaats=plaatsen, status="Beschikbaar", bouwcategorie="Bestaande bouw"),
            "B. Alle statussen + Bestaande bouw": dict(plaats=plaatsen, status=list(appmod.STATUSSEN), bouwcategorie="Bestaande bouw"),
            "C. Beschikbaar + Alle bouwcategorieën": dict(plaats=plaatsen, status="Beschikbaar", bouwcategorie="Alles"),
            "D. Alle statussen + Alle bouwcategorieën": dict(plaats=plaatsen, status=list(appmod.STATUSSEN), bouwcategorie="Alles"),
        }
        print(f"{'Filter':45} | {'Analyse':>7} | {'Makelaars':>9} | {'Plaats/gem':>10} | Sluit?")
        for label, combo in combos.items():
            res = test_deel_g_sluitcontroles(con, combo, label)
            print(
                f"{label:45} | {res.get('analyse_aantal', '-'):>7} | "
                f"{res.get('makelaarstabel_aantal', '-'):>9} | {res.get('plaatsverdeling_aantal', '-'):>10} | "
                f"{'JA' if res['sluit'] else 'NEE'}"
            )
    finally:
        con.close()
        appmod.app.testing = False

    print()
    mislukt = [d for d, ok in checks if not ok]
    if mislukt:
        print(f"MISLUKT ({len(mislukt)}):")
        for m in mislukt:
            print("  -", m)
        return 1
    print(f"ALLE {len(checks)} CHECKS GESLAAGD")
    return 0


if __name__ == "__main__":
    sys.exit(hoofd())
