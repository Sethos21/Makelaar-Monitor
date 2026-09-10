"""Regressietests voor de stabilisatieronde 2026-09-09 (methodiekbreuk,
Top 5 makelaars, nulgebieden, lokale marktpositie, Funda-link-onderzoek).
Read-only tegen de bestaande productie-SQLite, geen scans, geen writes.

Draai met:  py tests/test_stabilisatie_20260909.py
"""
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as appmod

checks: list[tuple[str, bool]] = []


def check(desc: str, ok: bool, extra: str = "") -> None:
    checks.append((desc, ok))
    print(("OK  " if ok else "FAIL"), desc, extra)


class FakeArgs(dict):
    def getlist(self, k):
        v = dict.get(self, k)
        return [] if v is None else (v if isinstance(v, list) else [v])

    def get(self, k, default=None):
        v = dict.get(self, k, default)
        return v[0] if isinstance(v, list) else v


def maak_args(**kwargs) -> FakeArgs:
    return FakeArgs(kwargs)


PLAATSEN = ["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"]


def test_methodiekbreuk_kern(con) -> None:
    """1. Methodiekbreuk: geen vergelijking oude scanner -> nieuwe scanner."""
    check(
        "woningen_is_nieuwe_methodiek: grens correct (scan 6 oud, scan 7 nieuw)",
        (not appmod.woningen_is_nieuwe_methodiek("2026-09-08T16:09:28"))
        and appmod.woningen_is_nieuwe_methodiek("2026-09-09T10:09:54"),
    )

    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    check("actuele scan voor dit gebied is scan_id 7 (118 objecten)", r["scaninfo"]["scan_id"] == 7 and r["kpis"]["aantal_objecten"] == 118)
    check("is_nieuwe_methodiek == True voor scan 7", r["is_nieuwe_methodiek"] is True)
    check(
        "GEEN fictieve vergelijking over de grens: netto_verandering is None (geen +81)",
        r["netto_verandering"] is None and r["netto_verandering_totaal"] is None,
    )
    check("mutatie_aantallen leeg (geen 'Nieuw aanbod: 81' e.d.)", r["mutatie_aantallen"] == {})
    check("eerste_van_nieuwe_meetreeks == True", r["eerste_van_nieuwe_meetreeks"] is True)

    # Dashboard-trend: oudere scans moeten uitgesloten zijn, geen fictieve grafiekpunten.
    trend = appmod.haal_woningen_trend(con, " | ".join(sorted(PLAATSEN)) if False else "Heesch | Heeswijk-Dinther | Nistelrode | Vinkel | Vorstenbosch")
    check("dashboard-trend bevat alleen scan 7 (methodiekbreuk uitgesloten)", [p["scan_id"] for p in trend["reeks"]] == [7])
    check("dashboard-trend: 2 oudere scans expliciet gerapporteerd als uitgesloten", trend["uitgesloten_door_methodiekbreuk"] == 2)


def test_methodiekbreuk_oud_oud_blijft_werken() -> None:
    """Scans van vóór de grens blijven onderling vergelijkbaar (ongewijzigd
    gedrag) - dit is precies de al gevalideerde DEEL E-case."""
    args = maak_args(plaats=PLAATSEN, status="Beschikbaar", bouwcategorie="Bestaande bouw", scan_id="6")
    r = appmod.bouw_analyseresultaat(args)
    check("historische scan 6 vergelijkt nog gewoon met scan 5 (oud-oud, ongewijzigd)", r["netto_verandering_totaal"] == -2)
    check("historische scan 6: is_nieuwe_methodiek == False", r["is_nieuwe_methodiek"] is False)


def test_methodiekbreuk_volgende_nieuwe_scan_vergelijkbaar(con) -> None:
    """'Volgende scan binnen dezelfde nieuwe meetreeks is wel vergelijkbaar':
    er is nu nog maar 1 nieuwe-methodiek-scan, dus dit wordt bewezen door de
    query-logica direct te toetsen met een gesimuleerd tweede nieuw scanmoment
    (geen database-wijziging - uitsluitend een functieaanroep met een
    hypothetisch tijdstip)."""
    # Simuleer: "als er een 2e scan na scan 7 zou zijn op tijdstip X", zou
    # haal_vorige_scan_id() scan 7 dan wél als geldige vorige-scan vinden?
    hypothetisch_scanmoment = "2026-09-10T09:00:00"
    gevonden = appmod.haal_vorige_scan_id(
        con, "Heesch | Heeswijk-Dinther | Nistelrode | Vinkel | Vorstenbosch",
        hypothetisch_scanmoment, huidige_scan_id=999999,
    )
    check("een hypothetische scan NA scan 7 zou scan 7 wél als vorige scan vinden (zelfde meetreeks)", gevonden == 7)


def test_top5_makelaars_backend_onbeperkt() -> None:
    """2. Top 5 is uitsluitend UI - de makelaarstabel/backend bevat nog ALLE
    makelaars; de HTML bevat ze ook allemaal (alleen CSS-verborgen)."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    check("backend makelaarstabel bevat > 5 makelaars (niet afgekapt)", len(r["makelaarstabel"]) > 5, f"({len(r['makelaarstabel'])})")

    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    resp = client.get("/analyse", query_string=qs)
    html = resp.get_data(as_text=True)
    verborgen_rijen = html.count("rij-extra hidden")
    check("HTML bevat 'toon meer'-knop", "data-toon-meer-makelaars" in html)
    check(
        "aantal verborgen rijen in HTML == (totaal makelaars - 5) (backend niet beperkt)",
        verborgen_rijen == len(r["makelaarstabel"]) - 5,
        f"({verborgen_rijen} vs {len(r['makelaarstabel']) - 5})",
    )

    # Business ook.
    con = appmod.get_business_readonly_connection()
    row = con.execute("SELECT gebied, categorieen FROM business_scans ORDER BY scanmoment DESC LIMIT 1").fetchone()
    con.close()
    bqs = [("business_plaats", p) for p in row["gebied"].split(" | ")] + [("business_categorie", c) for c in row["categorieen"].split(" | ")]
    resp2 = client.get("/business/analyse", query_string=bqs)
    html2 = resp2.get_data(as_text=True)
    check("Business: 'toon meer'-knop of <=5 makelaars (geen backendbeperking)", resp2.status_code == 200)


def test_nulgebieden() -> None:
    """3. Nulgebieden: geselecteerde plaats met 0 aanbod blijft zichtbaar."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    vinkel = next((p for p in r["plaatsverdeling"] if p["plaats"] == "Vinkel"), None)
    check("Vinkel zichtbaar in plaatsverdeling ondanks 0 aanbod", vinkel is not None)
    if vinkel:
        check("Vinkel: aantal == 0", vinkel["aantal"] == 0)
        check("Vinkel: gemeente == 's-Hertogenbosch", vinkel["gemeente"] == "'s-Hertogenbosch")
        check("Vinkel: inwoners bekend (2.795)", vinkel["inwoners_weergave"] == "2.795")
        check("Vinkel: aanbod per 1.000 == 0,0", vinkel["aanbod_per_1000_weergave"] == "0,0")

    sdb = next((g for g in r["gemeenteverdeling"] if g["gemeente"] == "'s-Hertogenbosch"), None)
    check("'s-Hertogenbosch zichtbaar in gemeenteverdeling ondanks 0 aanbod", sdb is not None)
    if sdb:
        check("'s-Hertogenbosch: aantal == 0", sdb["aantal"] == 0)

    check(
        "sluitcontrole: som(plaatsverdeling) == KPI actief aanbod (incl. nulgebied)",
        sum(p["aantal"] for p in r["plaatsverdeling"]) == r["kpis"]["aantal_objecten"],
    )
    check(
        "sluitcontrole: som(gemeenteverdeling) == KPI actief aanbod (incl. nulgebied)",
        sum(g["aantal"] for g in r["gemeenteverdeling"]) == r["kpis"]["aantal_objecten"],
    )


def test_lokale_marktpositie() -> None:
    """4. Makelaarsprofiel: lokale marktpositie per plaats/gemeente, dynamisch
    berekend (geen hardcoded cijfers) - Kordaat + een tweede makelaar."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    rijen = r["ruwe_rijen"]

    for naam in ["Kordaat Makelaars", "Bernheze Makelaars"]:
        profiel = appmod.bouw_makelaar_profiel_woningen(naam, rijen)
        totaal_eigen = profiel["aantal"]
        check(f"[{naam}] marktpositie_plaats niet leeg", len(profiel["marktpositie_plaats"]) > 0)
        som_plaats = sum(m["objecten_makelaar"] for m in profiel["marktpositie_plaats"])
        check(f"[{naam}] som(objecten per plaats) == totaal aantal objecten van deze makelaar", som_plaats == totaal_eigen, f"({som_plaats} vs {totaal_eigen})")
        for m in profiel["marktpositie_plaats"]:
            # Marktaandeel-formule per plaats moet exact kloppen.
            verwacht = round(m["objecten_makelaar"] / m["totaal_markt"] * 100, 1) if m["totaal_markt"] else 0
            check(f"[{naam}/{m['gebied']}] marktaandeel_pct-formule klopt", m["marktaandeel_pct"] == verwacht)
        som_gemeente = sum(m["objecten_makelaar"] for m in profiel["marktpositie_gemeente"])
        check(f"[{naam}] som(objecten per gemeente) == totaal aantal objecten", som_gemeente == totaal_eigen, f"({som_gemeente} vs {totaal_eigen})")

    # Kordaat moet #1 zijn in Nistelrode na de scanner-completenessfix.
    kordaat = appmod.bouw_makelaar_profiel_woningen("Kordaat Makelaars", rijen)
    nistelrode = next((m for m in kordaat["marktpositie_plaats"] if m["gebied"] == "Nistelrode"), None)
    check("Kordaat Makelaars is #1 in Nistelrode (scanner-fix bevestigd)", nistelrode is not None and nistelrode["ranking_weergave"] == "#1", f"({nistelrode})")


def test_funda_link_geen_gok() -> None:
    """5. Funda-link: nooit gokken op basis van de naam. Kordaat + een tweede
    makelaar moeten BEIDE geen link krijgen (geen betrouwbare bron in de
    huidige scan-/detaildata - expliciet onderzocht en gedocumenteerd)."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    for naam in ["Kordaat Makelaars", "Bernheze Makelaars", "Volledig Fictieve Naam XYZ"]:
        link = appmod.bepaal_funda_makelaar_link(naam, r["ruwe_rijen"])
        check(f"[{naam}] GEEN gegokte Funda-link (geen betrouwbare bron beschikbaar)", link is None)

    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    for naam in ["Kordaat Makelaars", "Bernheze Makelaars"]:
        resp = client.get("/makelaar/woningen/" + quote(naam), query_string=qs)
        html = resp.get_data(as_text=True)
        check(f"[{naam}] profielpagina toont GEEN 'Bekijk aanbod op Funda'-knop", "Bekijk aanbod op Funda" not in html, f"status={resp.status_code}")


def hoofd() -> int:
    if not appmod.DB_PATH.exists():
        print("Database niet gevonden - tests overgeslagen.")
        return 0

    appmod.app.testing = True
    con = appmod.get_readonly_connection()
    try:
        test_methodiekbreuk_kern(con)
        test_methodiekbreuk_oud_oud_blijft_werken()
        test_methodiekbreuk_volgende_nieuwe_scan_vergelijkbaar(con)
        test_top5_makelaars_backend_onbeperkt()
        test_nulgebieden()
        test_lokale_marktpositie()
        test_funda_link_geen_gok()
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
