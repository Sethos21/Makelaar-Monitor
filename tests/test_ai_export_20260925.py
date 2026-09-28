"""Regressietests voor de AI-analyse-export (Woningen, 2026-09-25).
Read-only tegen de bestaande productie-SQLite, geen scans, geen writes.
Alle vaste verwachtingen zijn gepind op scan_id 7 (eerste betrouwbare
post-methodiek-baseline), zodat deze tests niet afhangen van "de nieuwste scan".

Draai met:  py tests/test_ai_export_20260925.py
"""
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as appmod

checks: list[tuple[str, bool]] = []


def check(desc: str, ok: bool, extra: str = "") -> None:
    checks.append((desc, ok))
    print(("OK  " if ok else "FAIL"), desc, extra)


PLAATSEN = ["Heesch", "Heeswijk-Dinther", "Nistelrode", "Vinkel", "Vorstenbosch"]
ALLE_STATUSSEN = ["Beschikbaar", "Onder bod", "Verkocht onder voorbehoud"]
HOOFDSLEUTELS = [
    "export_info", "markt", "gebieden", "makelaars", "prijssegmenten",
    "marktdynamiek", "objecten", "methodologie", "field_definitions", "suggested_ai_task",
]


def qs(scan_id=None, plaatsen=PLAATSEN, statussen=ALLE_STATUSSEN, bouwcategorieen=("Alles",)) -> str:
    params = [("plaats", p) for p in plaatsen] + [("status", s) for s in statussen]
    params += [("bouwcategorie", b) for b in bouwcategorieen]
    if scan_id is not None:
        params.append(("scan_id", scan_id))
    return urlencode(params)


def haal_export(query: str) -> tuple[object, dict | None]:
    client = appmod.app.test_client()
    resp = client.get("/export/ai-json?" + query)
    try:
        data = json.loads(resp.data.decode("utf-8"))
    except Exception:
        data = None
    return resp, data


def db() -> sqlite3.Connection:
    return appmod.get_readonly_connection()


def alle_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from alle_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from alle_strings(v)
    elif isinstance(obj, str):
        yield obj


def test_basis_en_json_valide() -> dict:
    resp, data = haal_export(qs(scan_id=7))
    check("route /export/ai-json geeft 200", resp.status_code == 200, f"({resp.status_code})")
    check("mimetype application/json", resp.mimetype == "application/json")
    check("download-bestandsnaam bevat scan7 en .json", "scan7_" in resp.headers.get("Content-Disposition", "") and ".json" in resp.headers.get("Content-Disposition", ""))
    check("UTF-8 zonder BOM", not resp.data.startswith(b"\xef\xbb\xbf"))
    check("JSON valide (json.loads slaagt)", data is not None)
    check("hoofdstructuur exact zoals ontworpen", data is not None and list(data.keys()) == HOOFDSLEUTELS, f"({list(data.keys()) if data else None})")
    check("gebieden bevat per_plaats en per_gemeente", set(data["gebieden"]) == {"per_plaats", "per_gemeente"})
    check("suggested_ai_task letterlijk aanwezig", data["suggested_ai_task"].startswith("Analyseer deze vastgoedmarkt op basis van uitsluitend de aangeleverde data."))
    methodiek_tekst = " ".join(data["methodologie"])
    for fragment in ["aantal objecten binnen de geselecteerde markt", "100 = het aanbod", ">100", "<100",
                     "woningtekort, verkoopsnelheid of marktgezondheid", "NIET hetzelfde als 0",
                     appmod.WONINGEN_METHODIEK_WIJZIGING]:
        check(f"methodologie bevat '{fragment}'", fragment in methodiek_tekst)
    return data


def test_totalen_sluiten_aan(data: dict) -> None:
    resultaat = appmod.bouw_analyseresultaat(appmod.app.test_request_context("/analyse?" + qs(scan_id=7)).request.args)
    kpis = resultaat["kpis"]
    markt = data["markt"]
    n = len(resultaat["ruwe_rijen"])
    check("markt.aantal_objecten == Analyse KPI == 118", markt["aantal_objecten"] == kpis["aantal_objecten"] == n == 118, f"({markt['aantal_objecten']})")
    check("aantal objecten in export == aantal_objecten", len(data["objecten"]) == n)
    check("som makelaars.aantal_objecten == aantal_objecten", sum(m["aantal_objecten"] for m in data["makelaars"]) == n)
    check("som per_plaats.aanbod == aantal_objecten", sum(p["aanbod"] for p in data["gebieden"]["per_plaats"]) == n)
    check("som per_gemeente.aanbod == aantal_objecten", sum(g["aanbod"] for g in data["gebieden"]["per_gemeente"]) == n)
    bekende_prijzen = [o["vraagprijs_eur"] for o in data["objecten"] if o["vraagprijs_eur"] is not None]
    check("totale_vraagwaarde_eur == som objecten.vraagprijs_eur", markt["totale_vraagwaarde_eur"] == sum(bekende_prijzen))
    check("totale_vraagwaarde_eur geformatteerd == Analyse-weergave", appmod.formatteer_bedrag(markt["totale_vraagwaarde_eur"]) == kpis["totale_vraagprijs"], f"({kpis['totale_vraagprijs']})")
    check("mediaan_vraagprijs geformatteerd == Analyse-weergave", appmod.formatteer_bedrag(markt["mediaan_vraagprijs_eur"]) == kpis["mediaan_vraagprijs"])
    check("gemiddelde €/m² geformatteerd == Analyse-weergave", appmod.formatteer_bedrag(markt["gemiddelde_prijs_per_m2_eur"]) + " /m²" == kpis["gemiddelde_m2"])
    check("mediaan €/m² geformatteerd == Analyse-weergave", appmod.formatteer_bedrag(markt["mediaan_prijs_per_m2_eur"]) + " /m²" == kpis["mediaan_m2"])
    check("totaal woonoppervlakte == som objecten", markt["totaal_woonoppervlakte_m2"] == sum(o["woonoppervlakte_m2"] for o in data["objecten"] if o["woonoppervlakte_m2"] is not None))
    check("aantal_makelaars == Analyse KPI", markt["aantal_makelaars"] == kpis["aantal_makelaars"])
    check("per_status telt op tot aantal_objecten", sum(markt["per_status"].values()) == n)
    check("gemiddelde vraagprijs alleen over bekende prijzen", markt["gemiddelde_vraagprijs_eur"] == round(sum(bekende_prijzen) / len(bekende_prijzen)))
    check("gemiddelde vraagprijs export == Analyse-KPI (zelfde centrale berekening)", appmod.formatteer_bedrag(markt["gemiddelde_vraagprijs_eur"]) == kpis["gemiddelde_vraagprijs"], f"({kpis['gemiddelde_vraagprijs']})")
    check("aantal_met_vraagprijs == aantal bekende prijzen (noemer gemiddelde)", markt["aantal_met_vraagprijs"] == len(bekende_prijzen) == 115, f"({markt['aantal_met_vraagprijs']})")
    for m_ui, m_ex in zip(resultaat["makelaarstabel"], data["makelaars"]):
        if m_ui["makelaar"] != m_ex["naam"] or m_ui["aandeel_pct"] != m_ex["marktaandeel_pct"]:
            check(f"makelaar {m_ui['makelaar']} identiek aan Analyse-tabel", False)
            break
    else:
        check("makelaars (volgorde, naam, marktaandeel) identiek aan Analyse-makelaarstabel", True)
    seg_totaal = sum(s["aantal"] for s in data["prijssegmenten"])
    check("som prijssegmenten == objecten met bekende positieve vraagprijs", seg_totaal == sum(1 for p in bekende_prijzen if p > 0), f"({seg_totaal})")
    check("prijssegmenten identiek aan Analyse (aantal/aandeel)", [(s["aantal"], s["aandeel_pct"]) for s in data["prijssegmenten"]] == [(s["aantal"], s["aandeel_pct"]) for s in resultaat["segmentanalyse"]])


def test_top5_beperkt_export_niet(data: dict) -> None:
    client = appmod.app.test_client()
    html = client.get("/analyse?" + qs(scan_id=7)).data.decode("utf-8")
    verborgen = html.count("rij-extra hidden")
    check("UI toont Top 5 (er zijn verborgen rijen)", verborgen > 0, f"({verborgen})")
    check("export bevat ALLE makelaars (27 > 5)", len(data["makelaars"]) == 27, f"({len(data['makelaars'])})")
    check("export makelaars == zichtbare 5 + verborgen rijen", len(data["makelaars"]) == 5 + verborgen)
    check("Analysepagina bevat knop 'Export voor AI-analyse'", "Export voor AI-analyse" in html)
    check("AI-exportknop geeft scan_id expliciet door", "/export/ai-json?" in html and "scan_id=7" in html.split("/export/ai-json?")[1].split('"')[0])


def test_ruwe_getallen_numeriek(data: dict) -> None:
    # Segmentlabels ("€ 300.000-400.000") zijn namen, geen waarden - de grenzen staan numeriek in ondergrens_eur/bovengrens_eur.
    zonder_labels = {k: v for k, v in data.items() if k not in ("field_definitions", "methodologie", "prijssegmenten")}
    zonder_labels["prijssegmenten"] = [{k: v for k, v in s.items() if k != "segment"} for s in data["prijssegmenten"]]
    euro_strings = [s for s in alle_strings(zonder_labels) if "€" in s]
    check("geen €-opgemaakte strings in de data (alleen ruwe getallen)", not euro_strings, f"({euro_strings[:3]})")
    numeriek = ["vraagprijs_eur", "woonoppervlakte_m2", "perceeloppervlakte_m2", "prijs_per_m2_eur", "slaapkamers", "dagen_in_monitor", "prijswijziging_sinds_eerste_waarneming_eur"]
    fout = [(o["object_id"], k) for o in data["objecten"] for k in numeriek if o[k] is not None and not isinstance(o[k], (int, float))]
    check("objectvelden numeriek of null", not fout, f"({fout[:3]})")
    markt_num = [k for k, v in data["markt"].items() if k.endswith(("_eur", "_m2")) and v is not None and not isinstance(v, (int, float))]
    check("marktvelden *_eur/*_m2 numeriek", not markt_num)
    check("marktintensiteitsindex numeriek (float) of null", all(p["marktintensiteitsindex"] is None or isinstance(p["marktintensiteitsindex"], float) for p in data["gebieden"]["per_plaats"]))
    check("voorbeeldobject: vraagprijs is int, geen string", isinstance(data["objecten"][0]["vraagprijs_eur"], int))


def test_ontbrekend_niet_verzonnen(data: dict) -> None:
    con = db()
    try:
        def telling(sql):
            return con.execute(sql).fetchone()[0]
        db_prijs_null = telling("SELECT COUNT(*) FROM snapshots WHERE scan_id=7 AND vraagprijs IS NULL")
        db_label_leeg = telling("SELECT COUNT(*) FROM snapshots WHERE scan_id=7 AND (energielabel IS NULL OR energielabel='')")
        db_slk_null = telling("SELECT COUNT(*) FROM snapshots WHERE scan_id=7 AND slaapkamers IS NULL")
        db_perceel_null = telling("SELECT COUNT(*) FROM snapshots WHERE scan_id=7 AND perceeloppervlakte IS NULL")
    finally:
        con.close()
    obj = data["objecten"]
    check("vraagprijs null == DB null (niet verzonnen)", sum(o["vraagprijs_eur"] is None for o in obj) == db_prijs_null, f"({db_prijs_null})")
    check("energielabel null == DB leeg (geen fictief label)", sum(o["energielabel"] is None for o in obj) == db_label_leeg, f"({db_label_leeg})")
    check("slaapkamers null == DB null (geen standaardwaarde)", sum(o["slaapkamers"] is None for o in obj) == db_slk_null, f"({db_slk_null})")
    check("perceeloppervlakte null == DB null", sum(o["perceeloppervlakte_m2"] is None for o in obj) == db_perceel_null, f"({db_perceel_null})")
    check("prijs_per_m2 null exact wanneer vraagprijs ontbreekt", all((o["prijs_per_m2_eur"] is None) == (o["vraagprijs_eur"] is None) for o in obj))
    check("geen verzonnen velden (geen huur/energielabel-schatting e.d.)", not any(k for o in obj for k in o if "huur" in k or "geschat" in k))
    vp = [o for o in obj if o["vraagprijs_eur"] is None]
    check("prijswijziging null als vraagprijs onbekend", all(o["prijswijziging_sinds_eerste_waarneming_eur"] is None for o in vp))


def test_filters_exact(data_alles: dict) -> None:
    _, data = haal_export(qs(scan_id=7, statussen=["Beschikbaar"], bouwcategorieen=["Bestaande bouw"]))
    con = db()
    try:
        verwacht = con.execute(
            "SELECT COUNT(DISTINCT funda_url) FROM snapshots WHERE scan_id=7 AND status='Beschikbaar' AND bouwcategorie='Bestaande bouw'"
        ).fetchone()[0]
    finally:
        con.close()
    check("filter Beschikbaar+Bestaande bouw: aantal == directe SQL-telling", data["markt"]["aantal_objecten"] == len(data["objecten"]) == verwacht, f"({len(data['objecten'])} vs {verwacht})")
    check("filter: alle objecten status Beschikbaar", all(o["status"] == "Beschikbaar" for o in data["objecten"]))
    check("filter: alle objecten Bestaande bouw", all(o["bouwcategorie"] == "Bestaande bouw" for o in data["objecten"]))
    check("filter vastgelegd in export_info.selectie", data["export_info"]["selectie"]["statussen"] == ["Beschikbaar"] and data["export_info"]["selectie"]["bouwcategorieen"] == ["Bestaande bouw"])
    check("gefilterde export kleiner dan ongefilterde", len(data["objecten"]) < len(data_alles["objecten"]))

    _, enkel = haal_export(qs(scan_id=7, plaatsen=["Nistelrode"]))
    check("plaatsfilter Nistelrode: alleen Nistelrode-objecten", enkel["objecten"] and all(o["plaats"] == "Nistelrode" for o in enkel["objecten"]))
    check("plaatsfilter Nistelrode: 26 objecten", len(enkel["objecten"]) == 26, f"({len(enkel['objecten'])})")
    check("plaatsfilter: per_plaats bevat alleen Nistelrode", [p["plaats"] for p in enkel["gebieden"]["per_plaats"]] == ["Nistelrode"])
    kordaat = next((m for m in enkel["makelaars"] if m["naam"] == "Kordaat Makelaars"), None)
    check("Kordaat Makelaars #1 in Nistelrode, 7 objecten, 26,9%", kordaat is not None and kordaat["rang_in_selectie"] == 1 and kordaat["aantal_objecten"] == 7 and kordaat["marktaandeel_pct"] == 26.9)


def test_lokale_posities(data: dict) -> None:
    kordaat = next(m for m in data["makelaars"] if m["naam"] == "Kordaat Makelaars")
    nis = next((lp for lp in kordaat["lokale_posities"]["per_plaats"] if lp["gebied"] == "Nistelrode"), None)
    check("lokale positie Kordaat/Nistelrode: 7 van 26, 26,9%, ranking 1", nis == {
        "gebied": "Nistelrode", "objecten_makelaar": 7, "totaal_markt": 26, "marktaandeel_pct": 26.9,
        "ranking": 1, "marktintensiteitsindex_gebied": 111.5,
    }, f"({nis})")
    check("ranking is een int (geen '#1'-string)", isinstance(nis["ranking"], int) if nis else False)


def test_marktintensiteit_scope(data: dict) -> None:
    gem = {g["gemeente"]: g for g in data["gebieden"]["per_gemeente"]}
    b, h = gem.get("Bernheze"), gem.get("'s-Hertogenbosch")
    check("Bernheze: inwoners_selectie 31.240 (4 geselecteerde kernen)", b and b["inwoners_selectie"] == 31240)
    check("Bernheze: officieel 32.943 alleen als context", b and b["inwoners_gemeente_officieel"] == 32943)
    check("Bernheze: marktintensiteitsindex 108.9 (niet 592.6)", b and b["marktintensiteitsindex"] == 108.9, f"({b and b['marktintensiteitsindex']})")
    check("'s-Hertogenbosch: inwoners_selectie 2.795 (alleen Vinkel)", h and h["inwoners_selectie"] == 2795)
    check("'s-Hertogenbosch: officieel 162.272 NIET als noemer (verwacht aanbod 9.7)", h and h["verwacht_aanbod"] == 9.7)
    check("som aandeel_inwoners_pct gemeenten == 100", round(sum(g["aandeel_inwoners_pct"] for g in gem.values()), 1) == 100.0)
    plaats = {p["plaats"]: p for p in data["gebieden"]["per_plaats"]}
    verwacht = {"Heeswijk-Dinther": 162.1, "Heesch": 81.4, "Nistelrode": 111.5, "Vorstenbosch": 39.6, "Vinkel": 0.0}
    check("per_plaats marktintensiteitsindex identiek aan gedocumenteerde Analyse-waarden", {k: plaats[k]["marktintensiteitsindex"] for k in verwacht} == verwacht)
    check("som inwoners_selectie gemeenten == som inwoners plaatsen (zelfde scope)", sum(g["inwoners_selectie"] for g in gem.values()) == sum(p["inwoners"] for p in plaats.values()))


def test_nulgebieden(data: dict) -> None:
    vinkel = next((p for p in data["gebieden"]["per_plaats"] if p["plaats"] == "Vinkel"), None)
    check("Vinkel aanwezig als nulgebied", vinkel is not None and vinkel["aanbod"] == 0 and vinkel["is_nulgebied"] is True)
    check("Vinkel: inwoners bekend (2795), index 0.0 (niet null)", vinkel and vinkel["inwoners"] == 2795 and vinkel["marktintensiteitsindex"] == 0.0)
    check("Vinkel: prijsvelden null (geen aanbod, niet 0)", vinkel and vinkel["totale_vraagwaarde_eur"] is None and vinkel["gemiddelde_vraagprijs_eur"] is None)
    check("Vinkel: aanbod_per_1000 == 0.0", vinkel and vinkel["aanbod_per_1000_inwoners"] == 0.0)
    check("geen waarschuwing 'buiten scangebied' voor Vinkel (wel gescand)", not any("buiten het scangebied" in w for w in data["export_info"]["waarschuwingen"]))


def test_historisch_en_methodiekgrens() -> None:
    con = db()
    try:
        laatste = appmod.haal_laatste_scan_id(con)
        scans = [dict(r) for r in con.execute("SELECT scan_id, scanmoment, gebied FROM scans ORDER BY scan_id")]
    finally:
        con.close()

    _, s7 = haal_export(qs(scan_id=7))
    check("scan 7: analysetype klopt t.o.v. nieuwste scan", s7["export_info"]["analysetype"] == ("actueel" if laatste == 7 else "historisch"))
    check("scan 7: valt onder nieuwe methodiek, eerste van nieuwe meetreeks", s7["export_info"]["methodiek"]["scan_valt_onder_nieuwe_methodiek"] and s7["export_info"]["methodiek"]["eerste_scan_van_nieuwe_meetreeks"])
    check("scan 7: GEEN vergelijking met pre-methodiek-scan 6", s7["marktdynamiek"]["vergelijking_beschikbaar"] is False and s7["marktdynamiek"]["vorige_scan"] is None)
    check("scan 7: geen mutatiecijfers opgenomen", "gefilterde_selectie" not in s7["marktdynamiek"] and "mutaties" not in s7["marktdynamiek"])

    _, s6 = haal_export(qs(scan_id=6))
    check("scan 6 (historisch): analysetype historisch", s6["export_info"]["analysetype"] == "historisch")
    check("scan 6: gemarkeerd als oude methodiek + waarschuwing", not s6["export_info"]["methodiek"]["scan_valt_onder_nieuwe_methodiek"] and any("vóór de methodiekwijziging" in w for w in s6["export_info"]["waarschuwingen"]))
    md = s6["marktdynamiek"]
    check("scan 6: vergelijkt met oude scan 5 (beide oude methodiek)", md["vergelijking_beschikbaar"] and md["vorige_scan"]["scan_id"] == 5 and md["beide_scans_oude_methodiek"] is True)
    check("scan 6: volledige scan-referentie 39 -> 37 (DEEL E-validatiecase)", md["volledige_scan_referentie"] == {"vorige_aantal": 39, "huidige_aantal": 37, "netto_verandering": -2})
    check("scan 6: 'Uit aanbod' 2 in volledige-selectie-export", md["gefilterde_selectie"]["mutaties_per_type"].get("Uit aanbod") == 2, f"({md['gefilterde_selectie']['mutaties_per_type']})")
    check("scan 6: mutatieprijzen numeriek of null", all(m["vraagprijs_oud_eur"] is None or isinstance(m["vraagprijs_oud_eur"], int) for m in md["mutaties"]))

    # Elke scan in de database: een vergelijking gaat NOOIT over de methodiekgrens heen.
    over_grens = []
    for s in scans:
        _, d = haal_export(qs(scan_id=s["scan_id"], plaatsen=[]))
        md = d["marktdynamiek"]
        if md["vergelijking_beschikbaar"]:
            vorige_moment = md["vorige_scan"]["scanmoment"]
            if appmod.woningen_is_nieuwe_methodiek(vorige_moment) != appmod.woningen_is_nieuwe_methodiek(s["scanmoment"]):
                over_grens.append(s["scan_id"])
    check(f"geen enkele van {len(scans)} scans vergelijkt over de methodiekgrens heen", not over_grens, f"({over_grens})")

    # Vangnet in de export zelf (los van haal_vorige_scan_id): gesimuleerd.
    resultaat = appmod.bouw_analyseresultaat(appmod.app.test_request_context("/analyse?" + qs(scan_id=7)).request.args)
    resultaat["vorige_scan_id"] = 6
    resultaat["vorige_scanmoment"] = "2026-09-08T16:09:28"
    gesimuleerd = appmod.bouw_woningen_ai_export(resultaat)
    check("vangnet: pre-methodiek vorige scan wordt nooit als vergelijking gepresenteerd", gesimuleerd["marktdynamiek"]["vergelijking_beschikbaar"] is False)


def test_actueel() -> None:
    con = db()
    try:
        laatste = appmod.haal_laatste_scan_id(con)
        gebied = appmod.haal_scan_info(con, laatste)["gebied"]
    finally:
        con.close()
    plaatsen = [p.strip() for p in gebied.split("|")]
    _, d = haal_export(qs(plaatsen=plaatsen))
    check("zonder scan_id: nieuwste scan geëxporteerd", d["export_info"]["scan"]["scan_id"] == laatste)
    check("zonder scan_id: analysetype 'actueel'", d["export_info"]["analysetype"] == "actueel")
    check("actueel: aantal objecten == objecten in export", d["markt"]["aantal_objecten"] == len(d["objecten"]))
    # Standaardfilters (Beschikbaar + Bestaande bouw) zonder scan_id, zoals de knop op de actuele Analyse-pagina.
    standaard = urlencode([("plaats", p) for p in plaatsen])
    _, ds = haal_export(standaard)
    r = appmod.bouw_analyseresultaat(appmod.app.test_request_context("/analyse?" + standaard).request.args)
    check("actueel standaardfilters: selectie == Beschikbaar/Bestaande bouw", ds["export_info"]["selectie"]["statussen"] == ["Beschikbaar"] and ds["export_info"]["selectie"]["bouwcategorieen"] == ["Bestaande bouw"])
    check("actueel standaardfilters: aantal == Analyse-KPI", ds["markt"]["aantal_objecten"] == r["kpis"]["aantal_objecten"] > 0, f"({ds['markt']['aantal_objecten']})")
    check("actueel standaardfilters: gem. vraagprijs export == Analyse-KPI", ds["markt"]["gemiddelde_vraagprijs_eur"] is not None and appmod.formatteer_bedrag(ds["markt"]["gemiddelde_vraagprijs_eur"]) == r["kpis"]["gemiddelde_vraagprijs"], f"({r['kpis']['gemiddelde_vraagprijs']})")
    check("actueel standaardfilters: totale vraagwaarde export == Analyse-KPI", appmod.formatteer_bedrag(ds["markt"]["totale_vraagwaarde_eur"]) == r["kpis"]["totale_vraagprijs"])
    check("actueel standaardfilters: alle objecten Beschikbaar + Bestaande bouw", all(o["status"] == "Beschikbaar" and o["bouwcategorie"] == "Bestaande bouw" for o in ds["objecten"]))
    _, buiten = haal_export(qs(scan_id=7, plaatsen=PLAATSEN + ["Uden"]))
    check("plaats buiten scangebied -> expliciete waarschuwing", any("Uden" in w and "buiten het scangebied" in w for w in buiten["export_info"]["waarschuwingen"]))


def test_geen_schema_of_datawijziging(voor: tuple) -> None:
    na = (appmod.DB_PATH.stat().st_size, appmod.DB_PATH.stat().st_mtime)
    check("database-bestand ongewijzigd (grootte + mtime)", voor == na)


if __name__ == "__main__":
    appmod.app.testing = True
    voor = (appmod.DB_PATH.stat().st_size, appmod.DB_PATH.stat().st_mtime)
    data = test_basis_en_json_valide()
    test_totalen_sluiten_aan(data)
    test_top5_beperkt_export_niet(data)
    test_ruwe_getallen_numeriek(data)
    test_ontbrekend_niet_verzonnen(data)
    test_filters_exact(data)
    test_lokale_posities(data)
    test_marktintensiteit_scope(data)
    test_nulgebieden(data)
    test_historisch_en_methodiekgrens()
    test_actueel()
    test_geen_schema_of_datawijziging(voor)
    appmod.app.testing = False

    gefaald = [d for d, ok in checks if not ok]
    print()
    if gefaald:
        print(f"{len(gefaald)} VAN {len(checks)} CHECKS GEFAALD:")
        for d in gefaald:
            print("  -", d)
        sys.exit(1)
    print(f"ALLE {len(checks)} CHECKS GESLAAGD")
