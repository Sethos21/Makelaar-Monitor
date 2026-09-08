# Makelaar Monitor — status

_Laatst bijgewerkt: 2026-09-08_

## Milestone
Deze milestone bundelt twee afgeronde, functioneel goedgekeurde opdrachten sinds commit `7f35040a470f447ac5e02f9f97c6190a3dc57deb` ("feat: unify market UX and expand residential analysis"):

1. **Business-scanbesturing**: paginatie-fix (vastlopen rond pagina 2) + betrouwbaar "Scan afbreken".
2. **Business-analyse**: "Bekende jaarhuur" → "Berekende jaarhuur" hernoemd, met dekkingsinformatie.

Beide zijn afzonderlijk getest en door de gebruiker functioneel akkoord bevonden. Wordt nu vastgelegd als nieuwe stabiele Git-milestone.

---

## 1. Business-scanbesturing (paginatie + scan afbreken)

### Oorzaak paginatie-probleem
Live gereproduceerd: Funda Business reageert inconsistent op een door de scanner zelf geraden, niet-bestaande pagina-URL (stille herhaling van pagina 1, of een trage 15s-timeout). De oude dubbel-detectie vergeleek alleen met de vorige pagina en miste dit één pagina te laat — vandaar de vertraging exact rond pagina 2. Geen crash, geen infinite loop, maar een verwarrende, onnodige vertraging.

### Fix
- Paginatie leest nu de echte `a[rel="next"]`-link uit de DOM (`vind_volgende_pagina_url()`) i.p.v. zelf een volgende pagina-URL te construeren — werkt generiek voor elk aantal pagina's.
- Dubbel-detectie stopt zodra een pagina 0 nieuwe objecten oplevert t.o.v. alles tot dan toe gezien (i.p.v. alleen vergeleken met de vorige pagina).
- Een `bezochte_urls`-set voorkomt herverwerking van dezelfde pagina-URL.
- Live vóór/na getest tegen de echte site (Uden, Kantoor + Bedrijfsruimte): identiek eindresultaat (35 unieke objecten, 18/18, 3 dual-listed), maar zonder de vroegere onnodige extra paginaronde.

### Scan afbreken
- Coöperatief stopvlag-bestand (`_business_scan_stop.flag`) in de scanner's output-map; scanner controleert dit op meerdere veilige punten (vóór navigatie, in de paginalus, elke 2s tijdens menscontrole) en sluit zelf netjes af via `ScanGestopt` (erft van `BaseException`, wordt nooit per ongeluk opgevangen door generieke `except Exception`).
- `app.py` bewaart een echte referentie naar het actieve `subprocess.Popen`-proces (`BUSINESS_ACTIEVE_PROCES`), geen losse boolean.
- Escalatie bij stop: (1) 15s coöperatief, (2) `terminate()` + 5s, (3) laatste redmiddel `taskkill /PID <pid> /T /F` — uitsluitend die ene procesboom, nooit een generieke chrome.exe-kill.
- `stop_aangevraagd` is de enige gezaghebbende bron van waarheid voor "bewust gestopt" (ongeacht exitcode); bij een stop wordt de finale `*_alles.csv` nooit geschreven en dus nooit geïmporteerd — geen incomplete scan in de database.
- Menscontrole/captcha: geen blokkerende `input()`/ENTER meer; niet-blokkerende polling (elke 2s, max 1800s) die automatisch doorgaat zodra de gebruiker de controle in het zichtbare Chrome-venster oplost, en zelf ook onderbreekbaar via de stopknop.
- Lock (`SCAN_RUNNING_LOCK`, gedeeld met woningen) wordt altijd via de bestaande `finally` vrijgegeven.
- Statuspagina: nieuwe "Scan afbreken"-knop (idempotent — tweede klik toont "Stop wordt uitgevoerd..."), nieuwe status "gestopt" met duidelijke melding en "Nieuwe scan starten"/"Terug naar Bedrijfsmatig".

### Testresultaten
- 23 gemockte checks (stopscenario's A-F: tijdens pagina 1, tijdens navigatie, tijdens captcha, herstart Business/woningen na stop, dubbele klik) — allemaal geslaagd, inclusief geverifieerde `taskkill /PID <pid> /T /F`-scoping.
- Twee live scans (vóór/na fix) tegen de echte Funda Business-site — data-parity bevestigd, geen wasted round-trip meer.
- Regressie Woningen (30 checks) en Business (baseline 35 objecten/18-18/3 dual-listed/€19.227.000 koopvraagprijs) volledig ongewijzigd bevestigd.
- Databases (Business + Woningen) tijdens alle tests ongewijzigd gebleven qua bestandsgrootte (diagnostische live scans zijn bewust niet geïmporteerd).

---

## 2. Business-analyse: "Berekende jaarhuur"

### Bevinding
De onderliggende berekening (`Berekende_huur_per_jaar` in de scanner) was al correct: `oppervlakte_m2 × huurprijs_per_m2_per_jaar`, alleen bij `huur_eenheid == "per_m2_per_jaar"` + bekende huurprijs + bekende oppervlakte > 0. `app.py` gebruikte dit veld al correct in KPI's, makelaarstabel en objectentabel, zonder parallelle berekening. Het probleem zat uitsluitend in naamgeving en ontbrekende dekkingsinformatie.

### Wijzigingen
- "Bekende jaarhuur" / "Bekende berekende jaarhuur" → **"Berekende jaarhuur"** op alle drie plekken (KPI-kaart, makelaarstabel-kolomkop, objectentabel-kolomkop).
- KPI-sublabel toont nu expliciet de dekking: `Gebaseerd op 10 van 28 huurobjecten` (N = objecten met geldige berekening, M = `kpis.huuraanbod`, beide uit dezelfde gefilterde rijenset — geen nieuwe backend-berekening nodig).

### Test met echte data (Uden, 35 objecten, scan_id 3)
- 28 huurobjecten totaal (10× per_m2_per_jaar, 13× per_maand, 5× op_aanvraag).
- 10 objecten met geldige berekende jaarhuur; onafhankelijk herberekend en exact gelijk aan de opgeslagen waarden (0 afwijkingen).
- Som berekende jaarhuur: € 483.123 — gelijk aan zowel de KPI-totaal als de som over makelaarstabel/objectniveau.
- 3 concrete voorbeelden geverifieerd: Mandenmakerstraat 17 (1.233 m² × € 95/m²/jaar = € 117.135, correct berekend), Brabantplein 30 ("op aanvraag", geen berekening), Bedafseweg 22 (€ 1.356/mnd, expliciet NIET als €/m²/jaar behandeld).
- Regressie: alle transactietype-filters, hoofdscherm, woningen-analyse en dashboard blijven ongewijzigd werkend.

---

## Gewijzigde bestanden (deze milestone)
- `app.py`
- `scanner/funda_business_scanner_v1.py`
- `web/templates/business_resultaat.html`
- `web/templates/business_scan_status.html`

Niet gewijzigd (bevestigd via `git diff --stat`): `historie/*.py`, `scanner/makelaarsmonitor_v41.py`, overige templates, databaseschema, dashboard.

## Controles vóór commit (allemaal uitgevoerd en geslaagd)
- `git status`: exact de 4 bovenstaande bestanden + `docs/CLAUDE_STATUS.md` gewijzigd, verder niets.
- `.gitignore` dekt `*.sqlite`, `*.csv`, `data/`, `output/` — geen data-/database-/CSV-bestanden in de commit.
- Geen databaseschemawijzigingen (geen `.sql`/migratiebestanden gewijzigd).
- Woningmodule (`scanner/makelaarsmonitor_v41.py`) en historie-tools (`historie/*.py`) ongewijzigd.
- `py -m py_compile app.py scanner/funda_business_scanner_v1.py` — geslaagd.
- Korte regressietest via de Flask-testclient: `/`, `/analyse`, `/dashboard`, `/scan/status`, `/business/scan/status`, `/business/analyse` allemaal 200, geen traceback, nieuwe route `/business/scan/stop` geregistreerd, labels + dekkingstekst correct.

## Resterende beperkingen
- Statuspagina toont geen live "huidige pagina/aantal objecten" tijdens een lopende scan (zou stdout-streaming vereisen — bewust buiten scope, geen brede refactor).
- Menscontrole-wachttijd begrensd op 30 minuten (arbitrair maar redelijk).
- Geen losse "Huureenheid"-kolom in de objectentabel; de eenheid staat al impliciet in de Huurprijs-cel.
- Randgeval "huurprijs exact € 0" wordt in de scanner niet apart uitgesloten (alleen `huurprijs is None`) — nooit voorgekomen in de praktijk, buiten scope van de jaarhuur-opdracht.

## Aanbevolen volgende stap
Milestone-commit vastleggen en pushen naar `origin/main`, daarna dit document aanvullen met de nieuwe commit-hash.

CLAUDE_STATUS.md bijgewerkt.
