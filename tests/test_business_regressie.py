"""DEEL H: Business-regressie na de Woningen-gerichte bouwronde. Read-only,
echte data, geen scans."""
import sys
sys.path.insert(0, r"C:\Users\seth\Makelaar Monitor")
import app as appmod
from urllib.parse import quote

appmod.app.testing = True
client = appmod.app.test_client()

checks = []
def check(desc, ok, extra=""):
    checks.append((desc, ok))
    print(("OK  " if ok else "FAIL"), desc, extra)

con = appmod.get_business_readonly_connection()
row = con.execute("SELECT gebied, categorieen FROM business_scans ORDER BY scanmoment DESC LIMIT 1").fetchone()
gebied, categorieen = row["gebied"], row["categorieen"]
namen = [r["makelaar"] for r in con.execute("SELECT DISTINCT makelaar FROM business_snapshots WHERE makelaar IS NOT NULL AND makelaar<>''")]
con.close()
print("Business gebied:", gebied, "| categorieen:", categorieen, "| makelaars:", len(namen))

qs = [("business_plaats", p) for p in gebied.split(" | ")] + [("business_categorie", c) for c in categorieen.split(" | ")]

# Basis routes
for route, params in [
    ("/business/analyse", qs + [("business_transactietype", "Alles")]),
    ("/business/analyse", qs + [("business_transactietype", "Huur")]),
    ("/business/analyse", qs + [("business_transactietype", "Koop")]),
    ("/business/export/csv", qs),
    ("/business/scan/status", {}),
    ("/dashboard", {"markt": "bedrijfsmatig"}),
]:
    resp = client.get(route, query_string=params)
    check(f"route {route} {dict(params) if isinstance(params, list) else params}", resp.status_code == 200, f"status={resp.status_code}")

# Makelaarsprofiel Business - zelfde datatypeklasse-check als B2
for naam in namen[:4] + ["Onbekend", "Niet-bestaande Makelaar XYZ"]:
    resp = client.get("/makelaar/bedrijfsmatig/" + quote(naam), query_string=qs)
    check(f"business makelaarsprofiel '{naam}' geen serverfout", resp.status_code == 200, f"status={resp.status_code}")

# Sluitcontrole: som makelaarstabel == KPI actief_aanbod
class FakeArgs(dict):
    def getlist(self, k):
        v = dict.get(self, k)
        return [] if v is None else (v if isinstance(v, list) else [v])
    def get(self, k, default=None):
        v = dict.get(self, k, default)
        return v[0] if isinstance(v, list) else v

args = FakeArgs({"business_plaats": gebied.split(" | "), "business_categorie": categorieen.split(" | "), "business_transactietype": "Alles"})
r = appmod.bouw_business_analyseresultaat(args)
som = sum(m["aantal"] for m in r["makelaarstabel"])
check("Business: som(makelaarstabel) == kpis.actief_aanbod", som == r["kpis"]["actief_aanbod"], f"({som} vs {r['kpis']['actief_aanbod']})")
check("Business: kpis.aantal_makelaars aanwezig (nieuw in deze ronde)", "aantal_makelaars" in r["kpis"])

# Berekende jaarhuur dekking nog steeds correct (uit eerdere ronde)
check("Business: huur_kpis.berekende_jaarhuur_aantal <= huuraanbod", r["huur_kpis"]["berekende_jaarhuur_aantal"] <= r["kpis"]["huuraanbod"])

# Scanner CLI's nog intact (compile + --help, geen live scan)
import subprocess
for scanner in ["scanner/funda_business_scanner_v1.py", "scanner/makelaarsmonitor_v41.py", "scanner/_stopcontrole.py"]:
    res = subprocess.run(["py", "-m", "py_compile", scanner], cwd=r"C:\Users\seth\Makelaar Monitor", capture_output=True, text=True)
    check(f"py_compile {scanner}", res.returncode == 0, res.stderr[:300])

appmod.app.testing = False
print()
mislukt = [d for d, ok in checks if not ok]
if mislukt:
    print("MISLUKT:", mislukt)
    sys.exit(1)
print(f"ALLE {len(checks)} CHECKS GESLAAGD")
