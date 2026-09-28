"""Regressietests voor de AI-analyse-export Bedrijfsmatig (2026-09-25).
Read-only tegen de bestaande productie-SQLite, geen scans, geen writes.

Business kiest altijd de nieuwste scan voor exact regio + categorieën (geen
peildatumselector). Deze tests hardcoden daarom geen aantallen, maar vergelijken
de export met de Business-analysepipeline en met directe SQL-tellingen op het
scan_id dat de pipeline gebruikt - zo blijven ze geldig als er nieuwe scans bijkomen.

Draai met:  py tests/test_business_ai_export_20260925.py
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


HOOFDSLEUTELS = [
    "export_info", "markt", "gebieden", "makelaars", "segmenten",
    "marktdynamiek", "objecten", "methodologie", "field_definitions", "suggested_ai_task",
]
WONING_VELDEN = {"vraagprijs_eur", "slaapkamers", "energielabel", "bouwcategorie", "marktintensiteitsindex",
                 "perceeloppervlakte_m2", "prijssegmenten"}


def business_selecties() -> list[list[str]]:
    """Alle bestaande Business-scanreeksen (gebied) met beide categorieën."""
    con = appmod.get_business_readonly_connection()
    try:
        rows = con.execute(
            "SELECT DISTINCT gebied FROM business_scans WHERE categorieen = 'Bedrijfsruimte | Kantoor'"
        ).fetchall()
    finally:
        con.close()
    return [[p.strip() for p in r["gebied"].split("|")] for r in rows]


def qs(plaatsen, categorieen=("Kantoor", "Bedrijfsruimte"), statussen=(), transactietype=None, verwacht=None) -> str:
    params = [("business_plaats", p) for p in plaatsen] + [("business_categorie", c) for c in categorieen]
    params += [("business_status", s) for s in statussen]
    if transactietype:
        params.append(("business_transactietype", transactietype))
    if verwacht is not None:
        params.append(("verwacht_scan_id", verwacht))
    return urlencode(params)


def haal(query: str):
    resp = appmod.app.test_client().get("/business/export/ai-json?" + query)
    try:
        data = json.loads(resp.data.decode("utf-8"))
    except Exception:
        data = None
    return resp, data


def pipeline(query: str) -> dict:
    return appmod.bouw_business_analyseresultaat(appmod.app.test_request_context("/business/analyse?" + query).request.args)


def alle_sleutels(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from alle_sleutels(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from alle_sleutels(v)


def alle_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from alle_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from alle_strings(v)
    elif isinstance(obj, str):
        yield obj


def sql(scan_id: int, where: str = "1=1") -> int:
    con = appmod.get_business_readonly_connection()
    try:
        return con.execute(f"SELECT COUNT(*) FROM business_snapshots WHERE scan_id = ? AND {where}", (scan_id,)).fetchone()[0]
    finally:
        con.close()


def test_basis(plaatsen) -> dict:
    label = "/".join(plaatsen)
    q = qs(plaatsen)
    resp, data = haal(q)
    check(f"[{label}] route geeft 200", resp.status_code == 200, f"({resp.status_code})")
    check(f"[{label}] JSON valide", data is not None)
    check(f"[{label}] UTF-8 zonder BOM, application/json", not resp.data.startswith(b"\xef\xbb\xbf") and resp.mimetype == "application/json")
    check(f"[{label}] bestandsnaam bevat 'bedrijfsmatig'", "ai_export_bedrijfsmatig_scan" in resp.headers.get("Content-Disposition", ""))
    check(f"[{label}] hoofdstructuur", list(data.keys()) == HOOFDSLEUTELS, f"({list(data.keys())})")
    check(f"[{label}] export_info.module == 'bedrijfsmatig'", data["export_info"]["module"] == "bedrijfsmatig")
    check(f"[{label}] geen woningvelden geforceerd", not (set(alle_sleutels({k: v for k, v in data.items() if k not in ("field_definitions", "methodologie")})) & WONING_VELDEN))
    check(f"[{label}] Business-specifieke AI-taak", data["suggested_ai_task"] == appmod.AI_EXPORT_SUGGESTED_TASK_BUSINESS and "koop- en huurmarkt" in data["suggested_ai_task"])
    tekst = " ".join(data["methodologie"])
    for fragment in ["koopaanbod", "huuraanbod", "Berekende jaarhuur", "NIET hetzelfde als 0", "aantal aangeboden objecten", "Kantoor/Bedrijfsruimte"]:
        check(f"[{label}] methodologie bevat '{fragment}'", fragment in tekst)

    r = pipeline(q)
    scan_id = r["scaninfo"]["scan_id"]
    con = appmod.get_business_readonly_connection()
    try:
        verwacht_id, _ = appmod.haal_business_scan_id(con, appmod.business_canonical(plaatsen), "Bedrijfsruimte | Kantoor")
    finally:
        con.close()
    check(f"[{label}] scan = nieuwste voor exact deze regio + categorieën", data["export_info"]["scan"]["scan_id"] == scan_id == verwacht_id)
    check(f"[{label}] selectie vastgelegd", data["export_info"]["selectie"] == {"plaatsen": plaatsen, "categorieen": ["Kantoor", "Bedrijfsruimte"], "statussen": [], "transactietype": "Alles"})
    check(f"[{label}] peildatum aanwezig", bool(data["export_info"]["scan"]["peildatum"]))
    return {"data": data, "r": r, "scan_id": scan_id, "q": q}


def test_totalen(label, ctx) -> None:
    d, r, sid = ctx["data"], ctx["r"], ctx["scan_id"]
    n = r["kpis"]["actief_aanbod"]
    obj = d["objecten"]
    check(f"[{label}] aantal == Business-Analyse KPI == SQL", d["markt"]["aantal_objecten"] == n == len(obj) == sql(sid), f"({n})")
    check(f"[{label}] som makelaars == aantal", sum(m["aantal_objecten"] for m in d["makelaars"]) == n)
    check(f"[{label}] som per_plaats == aantal", sum(p["aanbod"] for p in d["gebieden"]["per_plaats"]) == n)
    check(f"[{label}] koop.aanbod == Analyse == som is_koopaanbod", d["markt"]["koop"]["aanbod"] == r["kpis"]["koopaanbod"] == sum(o["is_koopaanbod"] for o in obj))
    check(f"[{label}] huur.aanbod == Analyse == som is_huuraanbod", d["markt"]["huur"]["aanbod"] == r["kpis"]["huuraanbod"] == sum(o["is_huuraanbod"] for o in obj))
    check(f"[{label}] koop met/zonder prijs sluit", d["markt"]["koop"]["met_prijs"] + d["markt"]["koop"]["zonder_prijs_notk"] == d["markt"]["koop"]["aanbod"])
    check(f"[{label}] huur met prijs/op aanvraag sluit", d["markt"]["huur"]["met_prijs"] + d["markt"]["huur"]["op_aanvraag"] == d["markt"]["huur"]["aanbod"])
    check(f"[{label}] koop_en_huur_met_prijs == Analyse dual_listed", d["markt"]["koop_en_huur_met_prijs"] == r["kpis"]["dual_listed"] == sum(o["is_koop_en_huur_met_prijs"] for o in obj))
    koopprijzen = [o["koopprijs_eur"] for o in obj if o["koopprijs_eur"] is not None]
    check(f"[{label}] totale koopvraagwaarde == som objecten == Analyse", d["markt"]["koop"]["totale_koopvraagwaarde_eur"] == (sum(koopprijzen) if koopprijzen else None) == r["koop_kpis"]["totaal"])
    check(f"[{label}] gem./mediaan koopprijs == Analyse", (d["markt"]["koop"]["gemiddelde_koopprijs_eur"], d["markt"]["koop"]["mediaan_koopprijs_eur"]) == (r["koop_kpis"]["gemiddelde"], r["koop_kpis"]["mediaan"]))
    check(f"[{label}] huur €/m²/jaar == Analyse", d["markt"]["huur"]["huurprijs_per_m2_per_jaar"] == {"aantal": r["huur_kpis"]["aantal_m2_jaar"], "gemiddelde_eur": r["huur_kpis"]["gemiddelde_m2_jaar"], "mediaan_eur": r["huur_kpis"]["mediaan_m2_jaar"]})
    jaarhuren = [o["berekend_jaarhuur_eur"] for o in obj if o["berekend_jaarhuur_eur"] is not None]
    check(f"[{label}] berekende jaarhuur totaal == som objecten == Analyse", d["markt"]["huur"]["berekende_jaarhuur"]["totaal_eur"] == (sum(jaarhuren) if jaarhuren else None) == r["huur_kpis"]["berekende_jaarhuur_totaal"])
    m2 = [o["oppervlakte_m2"] for o in obj if o["oppervlakte_m2"] is not None]
    check(f"[{label}] totaal oppervlakte == som objecten", d["markt"]["oppervlakte"]["totaal_m2"] == (sum(m2) if m2 else None))
    check(f"[{label}] Analyse-weergave totaal m² == export", r["kpis"]["totaal_m2_weergave"] == (f"{d['markt']['oppervlakte']['totaal_m2']:,.0f}".replace(",", ".") + " m²"))
    check(f"[{label}] aantal_makelaars == Analyse", d["markt"]["aantal_makelaars"] == r["kpis"]["aantal_makelaars"])
    per_eenheid_sql = {}
    con = appmod.get_business_readonly_connection()
    try:
        for row in con.execute("SELECT COALESCE(NULLIF(huurprijs_eenheid, ''), 'geen') e, COUNT(*) n FROM business_snapshots WHERE scan_id = ? GROUP BY 1", (sid,)):
            per_eenheid_sql[row["e"]] = row["n"]
    finally:
        con.close()
    check(f"[{label}] huur per eenheid == SQL", d["markt"]["huur"]["per_eenheid"] == per_eenheid_sql, f"({per_eenheid_sql})")
    html = appmod.app.test_client().get("/business/analyse?" + ctx["q"]).data.decode("utf-8")
    if r["koop_kpis"]["totaal"] is not None:
        check(f"[{label}] Analyse-HTML toont dezelfde koopvraagwaarde", appmod.formatteer_bedrag(r["koop_kpis"]["totaal"]) in html)
    check(f"[{label}] Analyse-HTML heeft knop + verwacht_scan_id", "Export voor AI-analyse" in html and f"verwacht_scan_id={sid}" in html)

    # Makelaars identiek aan Analyse-tabel en ALLE opgenomen (Top 5 beperkt niets)
    check(f"[{label}] makelaars (volgorde, naam, marktaandeel) == Analyse-tabel", [(m["naam"], m["marktaandeel_pct"]) for m in d["makelaars"]] == [(m["makelaar"], m["aandeel_pct"]) for m in r["makelaarstabel"]])
    verborgen = html.count("rij-extra hidden")
    check(f"[{label}] Top 5 UI beperkt export niet", len(d["makelaars"]) == len(r["makelaarstabel"]) and (len(d["makelaars"]) <= 5 or len(d["makelaars"]) == 5 + verborgen), f"({len(d['makelaars'])} makelaars, {verborgen} verborgen)")


def test_numeriek_en_herkomst(label, ctx) -> None:
    d = ctx["data"]
    obj = d["objecten"]
    num = ["koopprijs_eur", "huurprijs_eur", "oppervlakte_m2", "oppervlakte_extra_m2", "berekend_jaarhuur_eur", "berekend_maandhuur_eur", "berekend_koopprijs_per_m2_eur", "dagen_in_monitor"]
    fout = [(o["object_id"], k) for o in obj for k in num if o[k] is not None and (isinstance(o[k], bool) or not isinstance(o[k], (int, float)))]
    check(f"[{label}] objectprijzen/-huren/-oppervlaktes numeriek of null", not fout, f"({fout[:3]})")
    euro = [s for s in alle_strings({k: v for k, v in d.items() if k not in ("field_definitions", "methodologie")}) if "€" in s]
    check(f"[{label}] geen €-opgemaakte strings", not euro, f"({euro[:3]})")
    check(f"[{label}] markt-oppervlakte numeriek", all(v is None or isinstance(v, (int, float)) for v in d["markt"]["oppervlakte"].values()))

    sleutels = set(obj[0].keys()) if obj else set()
    herkomst = set(appmod.BUSINESS_AI_BRONVELDEN) | set(appmod.BUSINESS_AI_BEREKENDE_VELDEN)
    check(f"[{label}] elk objectveld is als bron óf berekend geclassificeerd", sleutels == herkomst, f"({sleutels ^ herkomst})")
    check(f"[{label}] bron- en berekende lijsten overlappen niet", not (set(appmod.BUSINESS_AI_BRONVELDEN) & set(appmod.BUSINESS_AI_BEREKENDE_VELDEN)))
    check(f"[{label}] alle 'berekend_'-velden staan in de berekende lijst", all(k in appmod.BUSINESS_AI_BEREKENDE_VELDEN for k in sleutels if k.startswith("berekend_")))
    check(f"[{label}] veldherkomst in field_definitions", "veldherkomst" in d["field_definitions"])

    jaar_fout = [o["object_id"] for o in obj if o["berekend_jaarhuur_eur"] is not None and not (
        o["huurprijs_eenheid"] == "per_m2_per_jaar" and o["oppervlakte_m2"] and abs(o["berekend_jaarhuur_eur"] - o["huurprijs_eur"] * o["oppervlakte_m2"]) <= 1)]
    check(f"[{label}] berekend_jaarhuur == huurprijs/m²/jaar x oppervlakte (anders null)", not jaar_fout, f"({jaar_fout[:3]})")
    check(f"[{label}] maand-/jaarhuur NIET omgerekend (berekend null)", all(o["berekend_jaarhuur_eur"] is None for o in obj if o["huurprijs_eenheid"] in ("per_maand", "per_jaar")))
    kpm = [o["object_id"] for o in obj if (o["berekend_koopprijs_per_m2_eur"] is not None) != (o["koopprijs_eur"] is not None and bool(o["oppervlakte_m2"]))
           or (o["berekend_koopprijs_per_m2_eur"] is not None and o["berekend_koopprijs_per_m2_eur"] != round(o["koopprijs_eur"] / o["oppervlakte_m2"]))]
    check(f"[{label}] berekend_koopprijs_per_m2 correct en alleen bij beide bekend", not kpm, f"({kpm[:3]})")


def test_niet_verzonnen(label, ctx) -> None:
    d, sid = ctx["data"], ctx["scan_id"]
    obj = d["objecten"]
    for veld, kolom in [("koopprijs_eur", "koopprijs"), ("huurprijs_eur", "huurprijs"), ("oppervlakte_m2", "oppervlakte_m2"),
                        ("oppervlakte_extra_m2", "oppervlakte_extra_m2"), ("berekend_jaarhuur_eur", "berekende_huur_per_jaar")]:
        check(f"[{label}] {veld} null == DB null", sum(o[veld] is None for o in obj) == sql(sid, f"{kolom} IS NULL"))
    check(f"[{label}] koopprijs_conditie null == DB leeg", sum(o["koopprijs_conditie"] is None for o in obj) == sql(sid, "(koopprijs_conditie IS NULL OR koopprijs_conditie = '')"))
    check(f"[{label}] huurprijs_eenheid null == DB leeg", sum(o["huurprijs_eenheid"] is None for o in obj) == sql(sid, "(huurprijs_eenheid IS NULL OR huurprijs_eenheid = '')"))
    check(f"[{label}] makelaar null == DB leeg", sum(o["makelaar"] is None for o in obj) == sql(sid, "(makelaar IS NULL OR TRIM(makelaar) = '')"))
    onbekende_gemeente = [o for o in obj if o["plaats"] not in appmod.GEO_REFERENTIE]
    check(f"[{label}] gemeente null voor plaatsen buiten referentietabel (niet geraden)", all(o["gemeente"] is None for o in onbekende_gemeente))


def test_segmenten(label, ctx) -> None:
    d, r = ctx["data"], ctx["r"]
    seg = d["segmenten"]
    check(f"[{label}] segmenten: per_categorie = bestaande categorieën", [c["categorie"] for c in seg["per_categorie"]] == ["Kantoor", "Bedrijfsruimte"])
    for c in seg["per_categorie"]:
        n = sum(1 for o in d["objecten"] if c["categorie"] in o["gezochte_categorieen"])
        check(f"[{label}] {c['categorie']}: aantal == objecten in categorie", c["aantal"] == n, f"({c['aantal']} vs {n})")
        check(f"[{label}] {c['categorie']}: som makelaars == aantal", sum(m["objecten"] for m in c["makelaars"]) == c["aantal"])
    check(f"[{label}] Kantoor/Bedrijfsruimte-aantallen == Analyse-KPI", (seg["per_categorie"][0]["aantal"], seg["per_categorie"][1]["aantal"]) == (r["kpis"]["kantoor"], r["kpis"]["bedrijfsruimte"]))
    check(f"[{label}] grootteklassen == bestaande Bedrijfsruimte-segmentanalyse", [(s["segment"], s["aantal"]) for s in seg["grootteklassen_bedrijfsruimte"]] == [(s["segment"], s["aantal"]) for s in r["segmentanalyse_bedrijfsruimte"]])
    check(f"[{label}] grootteklassen: numerieke grenzen", all(isinstance(s["ondergrens_m2"], (int, float)) for s in seg["grootteklassen_bedrijfsruimte"]))
    totaal_types = sum(t["aantal"] for t in seg["funda_objecttypen"])
    check(f"[{label}] funda_objecttypen tellen letterlijke Funda-typen", totaal_types >= len(d["objecten"]))
    for m in d["makelaars"]:
        if m["segmentposities"]:
            if sum(sp["objecten"] for sp in m["segmentposities"]) < m["aantal_objecten"]:
                check(f"[{label}] segmentposities {m['naam']} dekken alle objecten", False)
                break
    else:
        check(f"[{label}] segmentposities dekken per makelaar alle objecten (overlap toegestaan)", True)


def test_marktdynamiek(label, ctx) -> None:
    d, r = ctx["data"], ctx["r"]
    md = d["marktdynamiek"]
    if r["vorige_scan_id"] is None:
        check(f"[{label}] geen vorige scan -> vergelijking_beschikbaar false + reden, geen cijfers", md["vergelijking_beschikbaar"] is False and md["reden_geen_vergelijking"] and "volledige_scan" not in md)
    else:
        check(f"[{label}] vergelijking met vorige scan van exact dezelfde reeks", md["vergelijking_beschikbaar"] and md["vorige_scan"]["scan_id"] == r["vorige_scan_id"])
        check(f"[{label}] scope expliciet 'volledige_scan'", md["scope"] == "volledige_scan")
        check(f"[{label}] netto == huidig - vorig", md["volledige_scan"]["netto_verandering"] == md["volledige_scan"]["huidige_aantal"] - md["volledige_scan"]["vorige_aantal"])
        niet_ongewijzigd = sum(v for k, v in md["volledige_scan"]["mutaties_per_type"].items() if k != "Ongewijzigd")
        check(f"[{label}] aantal mutatieregels == mutaties excl. Ongewijzigd", len(md["mutaties"]) == niet_ongewijzigd)


def test_filters(plaatsen) -> None:
    label = "/".join(plaatsen)
    alles = haal(qs(plaatsen))[1]
    for tt, veld in [("Huur", "is_huuraanbod"), ("Koop", "is_koopaanbod")]:
        _, d = haal(qs(plaatsen, transactietype=tt))
        r = pipeline(qs(plaatsen, transactietype=tt))
        check(f"[{label}] transactietype {tt}: alle objecten {veld}", d["objecten"] and all(o[veld] for o in d["objecten"]))
        check(f"[{label}] transactietype {tt}: aantal == Analyse", d["markt"]["aantal_objecten"] == r["kpis"]["actief_aanbod"] == sum(o[veld] for o in alles["objecten"]))
        check(f"[{label}] transactietype {tt} vastgelegd in selectie", d["export_info"]["selectie"]["transactietype"] == tt)
    _, kh = haal(qs(plaatsen, transactietype="Koop + Huur"))
    check(f"[{label}] Koop + Huur: alle objecten beide indicaties", all(o["is_koopaanbod"] and o["is_huuraanbod"] for o in kh["objecten"]))
    _, st = haal(qs(plaatsen, statussen=["Beschikbaar"]))
    check(f"[{label}] statusfilter Beschikbaar vastgelegd en toegepast", st["export_info"]["selectie"]["statussen"] == ["Beschikbaar"] and all(o["status"] == "Beschikbaar" for o in st["objecten"]))
    resp, _ = haal(qs(plaatsen, categorieen=["Kantoor"]))
    r = pipeline(qs(plaatsen, categorieen=["Kantoor"]))
    check(f"[{label}] categorie Kantoor: zelfde gedrag als Analyse (export alleen bij bestaande scan)", (resp.status_code == 404) == bool(r["foutmelding"]))


def test_verwacht_scan_id(ctx) -> None:
    plaatsen = ctx["data"]["export_info"]["selectie"]["plaatsen"]
    goed, _ = haal(qs(plaatsen, verwacht=ctx["scan_id"]))
    fout, _ = haal(qs(plaatsen, verwacht=ctx["scan_id"] + 1000))
    check("verwacht_scan_id == getoonde scan -> 200", goed.status_code == 200)
    check("verwacht_scan_id wijkt af (nieuwere scan) -> 409, geen stille andere export", fout.status_code == 409)
    geen, _ = haal(urlencode([("business_categorie", "Kantoor")]))
    check("geen regio -> 404 met melding (zelfde als Analyse)", geen.status_code == 404)


def test_woningen_ongewijzigd() -> None:
    resp = appmod.app.test_client().get("/export/ai-json?" + urlencode([("plaats", "Nistelrode"), ("scan_id", "7")]))
    d = json.loads(resp.data.decode("utf-8"))
    check("Woningen-export blijft module 'woningen' met eigen structuur", d["export_info"]["module"] == "woningen" and "prijssegmenten" in d and "segmenten" not in d)
    check("Woningen-export behoudt eigen AI-taak", d["suggested_ai_task"] == appmod.AI_EXPORT_SUGGESTED_TASK)


if __name__ == "__main__":
    appmod.app.testing = True
    dbs = [appmod.DB_PATH, appmod.BUSINESS_DB_PATH]
    voor = [(p.stat().st_size, p.stat().st_mtime) for p in dbs]

    selecties = business_selecties()
    check("er zijn Business-scanreeksen om te testen", len(selecties) >= 2, f"({selecties})")
    contexten = []
    for plaatsen in selecties:
        ctx = test_basis(plaatsen)
        label = "/".join(plaatsen)
        test_totalen(label, ctx)
        test_numeriek_en_herkomst(label, ctx)
        test_niet_verzonnen(label, ctx)
        test_segmenten(label, ctx)
        test_marktdynamiek(label, ctx)
        contexten.append(ctx)
    check("minstens één reeks met en één zonder vergelijkbare vorige scan getest",
          any(c["r"]["vorige_scan_id"] for c in contexten) and any(c["r"]["vorige_scan_id"] is None for c in contexten))
    grootste = max(contexten, key=lambda c: len(c["data"]["makelaars"]))
    check("minstens één selectie met > 5 makelaars (Top 5-test niet triviaal)", len(grootste["data"]["makelaars"]) > 5)
    test_filters(grootste["data"]["export_info"]["selectie"]["plaatsen"])
    test_verwacht_scan_id(grootste)
    test_woningen_ongewijzigd()

    na = [(p.stat().st_size, p.stat().st_mtime) for p in dbs]
    check("beide databasebestanden ongewijzigd (grootte + mtime)", voor == na)
    appmod.app.testing = False

    gefaald = [d for d, ok in checks if not ok]
    print()
    if gefaald:
        print(f"{len(gefaald)} VAN {len(checks)} CHECKS GEFAALD:")
        for d in gefaald:
            print("  -", d)
        sys.exit(1)
    print(f"ALLE {len(checks)} CHECKS GESLAAGD")
