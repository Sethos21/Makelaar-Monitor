"""Regressietests voor de gerichte uitbreiding 2026-09-10 (Marktintensiteit
Woningen, makelaarsprofiel-context, hernieuwd Funda-linkonderzoek). Read-only
tegen de bestaande productie-SQLite, geen scans, geen writes.

Draai met:  py tests/test_marktintensiteit_20260910.py
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


def test_formule_zuiverheid() -> None:
    """1. _bereken_marktintensiteit_velden(): kernformules exact zoals gevraagd,
    inclusief de "nooit een verzonnen waarde"-regel."""
    velden = appmod._bereken_marktintensiteit_velden(
        inwoners=1000, aantal=20, totaal_aanbod=100, totaal_inwoners_bekend=5000,
    )
    # verwacht aanbod = 100 * (1000/5000) = 20 -> index = 20/20*100 = 100
    check("verwacht_aanbod_weergave == 20,0 bij evenredige situatie", velden["verwacht_aanbod_weergave"] == "20,0", f"({velden})")
    check("marktintensiteitsindex == 100.0 bij evenredige situatie", velden["marktintensiteitsindex"] == 100.0, f"({velden})")
    check("aandeel_aanbod_pct == 20.0 (20/100)", velden["aandeel_aanbod_pct"] == 20.0)
    check("aandeel_inwoners_pct == 20.0 (1000/5000)", velden["aandeel_inwoners_pct"] == 20.0)
    check("verschil_pp == 0.0 bij evenredige situatie", velden["verschil_pp"] == 0.0)

    hoger = appmod._bereken_marktintensiteit_velden(inwoners=1000, aantal=40, totaal_aanbod=100, totaal_inwoners_bekend=5000)
    check("marktintensiteitsindex > 100 bij bovengemiddeld aanbod t.o.v. inwonertal", hoger["marktintensiteitsindex"] == 200.0, f"({hoger})")

    onbekend = appmod._bereken_marktintensiteit_velden(inwoners=None, aantal=5, totaal_aanbod=100, totaal_inwoners_bekend=5000)
    check("onbekend inwonertal -> nooit een gok, alles None/'-'", (
        onbekend["aandeel_inwoners_pct"] is None
        and onbekend["verschil_pp"] is None
        and onbekend["verwacht_aanbod_weergave"] == "-"
        and onbekend["marktintensiteitsindex"] is None
    ), f"({onbekend})")
    check("aandeel_aanbod_pct blijft wel berekend zonder inwonertal (onafhankelijk veld)", onbekend["aandeel_aanbod_pct"] == 5.0)

    geen_selectie_inwoners = appmod._bereken_marktintensiteit_velden(inwoners=1000, aantal=5, totaal_aanbod=100, totaal_inwoners_bekend=0)
    check("geen enkel gebied in de selectie met bekend inwonertal -> ook None/'-'", geen_selectie_inwoners["marktintensiteitsindex"] is None)


def test_plaatsverdeling_marktintensiteit_echte_data() -> None:
    """2. Marktintensiteit in bouw_plaatsverdeling()/bouw_gemeenteverdeling()
    tegen de echte huidige 5-plaatsenselectie (scan_id 7): totalen moeten
    intern consistent optellen, nulgebieden blijven zichtbaar."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    check("scan_id 7 actief voor deze test (118 objecten)", r["scaninfo"]["scan_id"] == 7 and r["kpis"]["aantal_objecten"] == 118)

    pv = r["plaatsverdeling"]
    check("alle 5 geselecteerde plaatsen aanwezig in plaatsverdeling (incl. nulgebieden)", len(pv) == 5, f"({[p['plaats'] for p in pv]})")

    som_aandeel_aanbod = round(sum(p["aandeel_aanbod_pct"] for p in pv if p["aandeel_aanbod_pct"] is not None), 1)
    check("som aandeel_aanbod_pct over alle plaatsen == 100.0", som_aandeel_aanbod == 100.0, f"({som_aandeel_aanbod})")

    som_aandeel_inwoners = round(sum(p["aandeel_inwoners_pct"] for p in pv if p["aandeel_inwoners_pct"] is not None), 1)
    check("som aandeel_inwoners_pct over alle plaatsen == 100.0 (alle 5 plaatsen hebben bekend inwonertal)", som_aandeel_inwoners == 100.0, f"({som_aandeel_inwoners})")

    for p in pv:
        if p["marktintensiteitsindex"] is not None:
            # Herberekening o.b.v. de al berekende aandeel-percentages (dezelfde
            # verhouding als aantal/verwacht_aanbod, maar zonder de 1-decimaal
            # afgeronde verwacht_aanbod_weergave-string opnieuw te parsen).
            herberekend = round(p["aandeel_aanbod_pct"] / p["aandeel_inwoners_pct"] * 100, 0) if p["aandeel_inwoners_pct"] else None
            check(
                f"[{p.get('plaats') or p.get('gemeente')}] marktintensiteitsindex ~ aandeel_aanbod/aandeel_inwoners*100 (binnen afrondingsmarge)",
                herberekend is not None and abs(herberekend - p["marktintensiteitsindex"]) <= 1,
                f"(index={p['marktintensiteitsindex']}, herberekend={herberekend})",
            )

    nul = next((p for p in pv if p["aantal"] == 0), None)
    check("minstens 1 nulgebied nog aanwezig (bestaande nulgebieden-fix niet geraakt)", nul is not None, f"({[(p['plaats'], p['aantal']) for p in pv]})")
    if nul:
        check(f"[{nul['plaats']}] nulgebied: aandeel_aanbod_pct == 0.0 (geen crash op aantal=0)", nul["aandeel_aanbod_pct"] == 0.0)
        check(f"[{nul['plaats']}] nulgebied: marktintensiteitsindex == 0.0 (geen aanbod, wel inwoners bekend)", nul["marktintensiteitsindex"] == 0.0, f"({nul['marktintensiteitsindex']})")

    gv = r["gemeenteverdeling"]
    check("gemeenteverdeling ook aanwezig (Bernheze)", any(g["gemeente"] == "Bernheze" for g in gv), f"({[g['gemeente'] for g in gv]})")
    bernheze = next(g for g in gv if g["gemeente"] == "Bernheze")
    check("gemeenteniveau: aandeel_aanbod_pct == 100.0 bij één gemeente in de volledige selectie", bernheze["aandeel_aanbod_pct"] == 100.0)


def test_gemeente_marktintensiteit_gebruikt_alleen_geselecteerde_inwoners() -> None:
    """2b. REGRESSIETEST (correctie 2026-09-10): bij een gemengde selectie
    (4 Bernheze-kernen + Vinkel, dat bij gemeente 's-Hertogenbosch hoort) mag
    de Marktintensiteit-noemer NOOIT het volledige officiële gemeentelijke
    inwonertal van 's-Hertogenbosch (162.272) gebruiken - alleen Bernheze
    is voor 100% gescand, van 's-Hertogenbosch is uitsluitend Vinkel (2.795
    van de 34.035 kern-inwoners) onderdeel van de selectie. Teller en noemer
    moeten exact dezelfde geografische scope hebben. De bovenste tabel
    ("Verdeling per plaats/gemeente") mag het volledige officiële
    inwonertal wél als context blijven tonen - dat is een ANDER veld
    (`inwoners_weergave`) en wordt hier expliciet ongewijzigd gecontroleerd."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    gv = r["gemeenteverdeling"]

    bernheze = next(g for g in gv if g["gemeente"] == "Bernheze")
    denbosch = next(g for g in gv if g["gemeente"] == "'s-Hertogenbosch")

    # Bovenste tabel: officiële volledige gemeentelijke inwonertallen blijven ongewijzigd context.
    check("Bernheze: officieel inwonertal (bovenste tabel) blijft 32.943", bernheze["inwoners_weergave"] == "32.943", f"({bernheze['inwoners_weergave']})")
    check("'s-Hertogenbosch: officieel inwonertal (bovenste tabel) blijft 162.272", denbosch["inwoners_weergave"] == "162.272", f"({denbosch['inwoners_weergave']})")

    # Marktintensiteit-noemer: uitsluitend de daadwerkelijk geselecteerde kernen.
    # Bernheze = Heesch 14.165 + Heeswijk-Dinther 8.895 + Nistelrode 6.725 + Vorstenbosch 1.455 = 31.240.
    # 's-Hertogenbosch (selectie) = Vinkel 2.795. Totaal geselecteerd = 34.035.
    check("Bernheze: inwoners_selectie_weergave == 31.240 (som van de 4 geselecteerde kernen)", bernheze["inwoners_selectie_weergave"] == "31.240", f"({bernheze['inwoners_selectie_weergave']})")
    check("'s-Hertogenbosch: inwoners_selectie_weergave == 2.795 (alleen Vinkel)", denbosch["inwoners_selectie_weergave"] == "2.795", f"({denbosch['inwoners_selectie_weergave']})")

    check("Bernheze: aandeel_inwoners_pct == 91.8 (31.240/34.035)", bernheze["aandeel_inwoners_pct"] == 91.8, f"({bernheze['aandeel_inwoners_pct']})")
    check("'s-Hertogenbosch: aandeel_inwoners_pct == 8.2 (2.795/34.035)", denbosch["aandeel_inwoners_pct"] == 8.2, f"({denbosch['aandeel_inwoners_pct']})")

    check("Bernheze: marktintensiteitsindex fors lager dan de oude, foutieve 592.6 (nu 108.9)", bernheze["marktintensiteitsindex"] == 108.9, f"({bernheze['marktintensiteitsindex']})")
    check("'s-Hertogenbosch: marktintensiteitsindex == 0.0 (Vinkel heeft 0 aanbod, wel bekend inwonertal)", denbosch["marktintensiteitsindex"] == 0.0, f"({denbosch['marktintensiteitsindex']})")

    # Sluitcontrole: som van de scope-consistente inwonersgetallen == 34.035, en
    # exact gelijk aan de som van de plaatsverdeling (dezelfde selectie, dezelfde noemer).
    pv = r["plaatsverdeling"]
    som_plaats_inwoners = sum(int(p["inwoners_weergave"].replace(".", "")) for p in pv if p["inwoners_weergave"] != "-")
    som_gemeente_inwoners_selectie = sum(
        int(g["inwoners_selectie_weergave"].replace(".", "")) for g in gv if g["inwoners_selectie_weergave"] != "-"
    )
    check("som(inwoners_selectie over gemeenten) == som(inwoners over plaatsen) == 34.035 (zelfde scope)", som_plaats_inwoners == 34035 and som_gemeente_inwoners_selectie == 34035, f"(plaats={som_plaats_inwoners}, gemeente={som_gemeente_inwoners_selectie})")

    # Live route-check: de daadwerkelijk gerenderde HTML van de Marktintensiteit-sectie.
    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles"), ("geo_niveau", "gemeente")]
    resp = client.get("/analyse", query_string=qs)
    html = resp.get_data(as_text=True)
    marktintensiteit_html = html[html.find("Marktintensiteit</strong>"):]
    check("HTML Marktintensiteit-tabel bevat 31.240 (Bernheze, niet meer 32.943 als noemer)", "31.240" in marktintensiteit_html)
    check("HTML Marktintensiteit-tabel bevat 2.795 (Vinkel/'s-Hertogenbosch, niet meer 162.272 als noemer)", "2.795" in marktintensiteit_html)
    check("HTML Marktintensiteit-tabel bevat de gecorrigeerde index 108.9", "108.9" in marktintensiteit_html)
    check("HTML Marktintensiteit-tabel bevat NIET meer de foutieve index 592.6", "592.6" not in marktintensiteit_html)
    check("HTML Marktintensiteit-tabel bevat NIET het volledige officiële 162.272 als noemer", "162.272" not in marktintensiteit_html)
    # De bovenste tabel (vóór de Marktintensiteit-sectie) mag het officiële inwonertal wél tonen.
    bovenste_tabel_html = html[html.find("3. Verdeling"):html.find("Marktintensiteit</strong>")]
    check("HTML bovenste tabel bevat nog steeds het volledige officiële 32.943 (Bernheze)", "32.943" in bovenste_tabel_html)
    check("HTML bovenste tabel bevat nog steeds het volledige officiële 162.272 ('s-Hertogenbosch)", "162.272" in bovenste_tabel_html)
    appmod.app.testing = False


def test_enkele_plaats_selectie_evenredig() -> None:
    """3. Filters: bij een selectie van precies 1 plaats is die plaats per
    definitie 100% van zowel aanbod als (bekende) inwoners -> index moet
    exact 100.0 zijn, ongeacht het daadwerkelijke aanbod."""
    args = maak_args(plaats=["Nistelrode"], status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    pv = r["plaatsverdeling"]
    check("precies 1 plaats in de verdeling", len(pv) == 1, f"({[p['plaats'] for p in pv]})")
    nistelrode = pv[0]
    check("Nistelrode alleen: aandeel_aanbod_pct == 100.0", nistelrode["aandeel_aanbod_pct"] == 100.0)
    check("Nistelrode alleen: aandeel_inwoners_pct == 100.0", nistelrode["aandeel_inwoners_pct"] == 100.0)
    check("Nistelrode alleen: marktintensiteitsindex == 100.0 (per definitie evenredig bij 1 gebied)", nistelrode["marktintensiteitsindex"] == 100.0, f"({nistelrode['marktintensiteitsindex']})")


def test_makelaarsprofiel_context_geen_invloed_op_marktaandeel() -> None:
    """4. Makelaarsprofiel: marktintensiteit als context bij Marktpositie per
    plaats/gemeente - marktaandeel_pct blijft exact objecten_makelaar/
    totaal_markt*100, ongeacht de context. Kordaat Makelaars/Nistelrode als
    concreet voorbeeld (7 objecten, markt Nistelrode = 26)."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    profiel = appmod.bouw_makelaar_profiel_woningen(
        "Kordaat Makelaars", r["ruwe_rijen"], r["plaatsverdeling"], r["gemeenteverdeling"],
    )
    nistelrode = next((m for m in profiel["marktpositie_plaats"] if m["gebied"] == "Nistelrode"), None)
    check("Kordaat/Nistelrode aanwezig in marktpositie_plaats", nistelrode is not None)
    if nistelrode:
        check("Kordaat/Nistelrode: objecten_makelaar == 7", nistelrode["objecten_makelaar"] == 7, f"({nistelrode})")
        check("Kordaat/Nistelrode: totaal_markt == 26", nistelrode["totaal_markt"] == 26, f"({nistelrode})")
        check(
            "Kordaat/Nistelrode: marktaandeel_pct == objecten_makelaar/totaal_markt*100 (ongeacht context)",
            nistelrode["marktaandeel_pct"] == round(nistelrode["objecten_makelaar"] / nistelrode["totaal_markt"] * 100, 1),
            f"({nistelrode})",
        )
        check("Kordaat/Nistelrode: context-veld inwoners_weergave gevuld", nistelrode["inwoners_weergave"] not in (None, "-"), f"({nistelrode['inwoners_weergave']})")
        check("Kordaat/Nistelrode: context-veld marktintensiteitsindex gevuld", nistelrode["marktintensiteitsindex"] is not None, f"({nistelrode['marktintensiteitsindex']})")

    # Zonder context_tabel (bv. oudere aanroep) moet de functie blijven werken en nette lege context tonen.
    zonder_context = appmod.bouw_makelaar_lokale_marktpositie("Kordaat Makelaars", r["ruwe_rijen"], "plaats", None)
    check("bouw_makelaar_lokale_marktpositie() werkt ook zonder context_tabel (achterwaarts compatibel)", len(zonder_context) > 0)
    nistelrode_leeg = next((m for m in zonder_context if m["gebied"] == "Nistelrode"), None)
    check("zonder context_tabel: inwoners_weergave valt netjes terug op '-'", nistelrode_leeg is not None and nistelrode_leeg["inwoners_weergave"] == "-")
    check("zonder context_tabel: marktaandeel_pct ongewijzigd t.o.v. mét context", nistelrode_leeg["marktaandeel_pct"] == nistelrode["marktaandeel_pct"])


def test_makelaarsprofiel_route_bevat_marktintensiteit_kolommen() -> None:
    """4b. Live route-check (Flask test_client): profielpagina rendert de
    nieuwe context-kolommen zonder crash, met de juiste waarden voor Kordaat."""
    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    resp = client.get("/makelaar/woningen/" + quote("Kordaat Makelaars"), query_string=qs)
    check("makelaarsprofiel-route geeft 200 OK", resp.status_code == 200, f"(status={resp.status_code})")
    html = resp.get_data(as_text=True)
    check("pagina bevat 'Marktintensiteitsindex (context)'-kolomkop", "Marktintensiteitsindex (context)" in html)
    check("pagina bevat 'Inwoners (context)'-kolomkop", "Inwoners (context)" in html)
    check("pagina bevat het cijfer 111.5 (marktintensiteitsindex Nistelrode voor Kordaat)", "111.5" in html)
    check("pagina bevat marktaandeel 26.9% (ongewijzigde formule)", "26.9%" in html)
    appmod.app.testing = False


def test_resultaatpagina_marktintensiteit_sectie() -> None:
    """4c. Analysepagina zelf: nieuwe 'Marktintensiteit'-tabel rendert correct
    onder Verdeling per plaats/gemeente, zonder de bestaande tabel te breken."""
    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    resp = client.get("/analyse", query_string=qs)
    check("analysepagina geeft 200 OK", resp.status_code == 200, f"(status={resp.status_code})")
    html = resp.get_data(as_text=True)
    check("pagina bevat de sectie 'Marktintensiteit'", "Marktintensiteit" in html)
    check("pagina bevat nog steeds de bestaande sectietitel 'Verdeling per plaats/gemeente'", "Verdeling per plaats/gemeente" in html or "3. Verdeling" in html)
    check("pagina bevat nog steeds sectie 4 'Prijssegmenten' (bestaande structuur niet gebroken)", "Prijssegmenten" in html)
    appmod.app.testing = False


def test_funda_link_hernieuwd_onderzoek_geen_wijziging() -> None:
    """5. Funda-link: hernieuwd onderzoek via de woningdetailpagina bevestigt
    dat er geen betrouwbare bron is (zie docs/BUGLIST.md) - bepaal_funda_makelaar_link()
    blijft ongewijzigd altijd None, geen knop, geen gok, geen schemawijziging."""
    args = maak_args(plaats=PLAATSEN, status=list(appmod.STATUSSEN), bouwcategorie=["Alles"])
    r = appmod.bouw_analyseresultaat(args)
    for naam in ["Kordaat Makelaars", "Heuvel Makelaars", "Volledig Fictieve Naam XYZ"]:
        link = appmod.bepaal_funda_makelaar_link(naam, r["ruwe_rijen"])
        check(f"[{naam}] nog steeds geen gegokte Funda-link", link is None)

    appmod.app.testing = True
    client = appmod.app.test_client()
    qs = [("plaats", p) for p in PLAATSEN] + [("status", s) for s in appmod.STATUSSEN] + [("bouwcategorie", "Alles")]
    resp = client.get("/makelaar/woningen/" + quote("Kordaat Makelaars"), query_string=qs)
    html = resp.get_data(as_text=True)
    check("profielpagina toont nog steeds GEEN 'Bekijk aanbod op Funda'-knop", "Bekijk aanbod op Funda" not in html)
    appmod.app.testing = False

    import sqlite3
    con = sqlite3.connect(str(appmod.DB_PATH))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(snapshots)")
    kolommen = {r[1] for r in cur.fetchall()}
    con.close()
    check("geen schemawijziging: geen nieuwe kantoor-ID/funda-link-kolom in snapshots", not any("kantoor" in k.lower() or "makelaarslink" in k.lower() for k in kolommen), f"({sorted(kolommen)})")


def hoofd() -> int:
    if not appmod.DB_PATH.exists():
        print("Database niet gevonden - tests overgeslagen.")
        return 0

    appmod.app.testing = True
    try:
        test_formule_zuiverheid()
        test_plaatsverdeling_marktintensiteit_echte_data()
        test_gemeente_marktintensiteit_gebruikt_alleen_geselecteerde_inwoners()
        test_enkele_plaats_selectie_evenredig()
        test_makelaarsprofiel_context_geen_invloed_op_marktaandeel()
        test_makelaarsprofiel_route_bevat_marktintensiteit_kolommen()
        test_resultaatpagina_marktintensiteit_sectie()
        test_funda_link_hernieuwd_onderzoek_geen_wijziging()
    finally:
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
