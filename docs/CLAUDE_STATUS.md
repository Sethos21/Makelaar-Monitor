# Makelaar Monitor — status

_Laatst bijgewerkt: 2026-09-07_

## Uitgevoerde opdracht
Fase 4 van de Funda-in-Business-module: de knop "Nieuwe scan uitvoeren" bij Bedrijfsmatig vastgoed is nu functioneel. De volledige keten werkt vanuit de browser: geselecteerde regio's/categorieën → bestaande Business-scanner (subprocess) → nieuwe `*_alles.csv` → automatische import via de bestaande Business-historie-tool (subprocess) → Business-database → statuspagina → terug naar de actuele Business-analyse met dezelfde filters. Woningenfunctionaliteit blijft volledig intact.

## Gewijzigde bestanden
- `app.py` — nieuwe Business-scan-paden (`BUSINESS_SCANNER_PAD`, `BUSINESS_HISTORIE_PAD`, `BUSINESS_OUTPUT_DIR`, `BUSINESS_HISTORIE_OUTPUT_DIR`); nieuw statusobject `BUSINESS_SCAN_STATE` + `BUSINESS_STATE_LOCK`; nieuwe functies `_vind_nieuwste_business_alles_csv()`, `_parse_business_historie_samenvatting()`, `_importeer_in_business_historie()`, `_voer_business_scan_uit()`; nieuwe routes `POST /business/scan/start` en `GET /business/scan/status`; `index()` uitgebreid met `actieve_tab` (zodat de pagina na een Business-validatiefout op het Bedrijfsmatig-tabblad opent); `scan_start()`/`scan_status_pagina()` (woningen) minimaal uitgebreid met een `geblokkeerd`-queryparameter bij een mislukte lock-acquisitie (zuiver additief, bestaand gedrag ongewijzigd wanneer die parameter afwezig is).
- `web/templates/business_scan_status.html` — **nieuw**: statuspagina voor de Business-scan (running/importing/success/error/geblokkeerd/idle), met auto-refresh tijdens een actieve scan en de vaste menscontrole-tekst.
- `web/templates/scan_status.html` (woningen) — één additieve `{% if geblokkeerd %}`-tak toegevoegd vóór de bestaande takken; alle bestaande takken/gedrag ongewijzigd.
- `web/templates/index.html` — tabweergave en panelen nu servergestuurd via `actieve_tab` (in plaats van altijd hardcoded op Woningen actief); Business-validatiemeldingen toegevoegd; de "Nieuwe scan uitvoeren"-knop bij Bedrijfsmatig vastgoed is niet langer `disabled` en verzendt nu echt naar `/business/scan/start`.
- `web/static/js/app.js` — nieuwe `initNieuweBusinessScanValidatie()` (analoog aan de bestaande woningen-validatie: minimaal 1 regio én minimaal 1 categorie vereist vóór verzenden), aangeroepen vanuit `DOMContentLoaded`.
- `docs/CLAUDE_STATUS.md` — dit overdrachtsbestand.

Niet gewijzigd (geverifieerd via bestandstijdstempels vóór en na deze fase): `scanner/funda_business_scanner_v1.py`, `scanner/makelaarsmonitor_v41.py`, `historie/funda_business_historie_v1.py`, `historie/makelaarsmonitor_historie_v10.py`, `data/funda_business_historie.sqlite`, `data/makelaarsmonitor_historie.sqlite` (hoofdbestanden ongewijzigd van grootte/mtime; alleen `-wal`/`-shm`-sidecars aangeraakt door read-only testconnecties, inherent aan WAL-mode, 0-byte `-wal`-bestand bevestigt geen openstaande schrijfacties).

## Waarom de Business-scanner niet is gewijzigd
De opdracht vroeg om eerst te controleren welke CLI-argumenten de bestaande scanner al ondersteunt. `scanner/funda_business_scanner_v1.py` bleek al een `--objecttypes {Kantoor,Bedrijfsruimte}`-argument te hebben (default: beide) — functioneel identiek aan de gevraagde categorie-selectie. Er was dus geen enkele wijziging aan de scanner nodig; `app.py` roept 'm aan met `--plaatsen <geselecteerde regio's> --objecttypes <geselecteerde categorieën> --output-map output/bedrijfsmatig`.

## Eén globale scan-lock (bewuste keuze, zoals voorkeur gebruiker)
`app.py` had al `SCAN_RUNNING_LOCK` voor de woningenscan. In plaats van een aparte lock voor Business, hergebruikt `business_scan_start()` **exact hetzelfde lock-object**: dat maakt er zonder enige wijziging aan de bestaande woningen-lock-logica één gedeelde globale lock van over beide scantypes. Reden (conform de expliciete voorkeur uit de opdracht): beide scanners openen een zichtbaar Chrome-venster en leunen op dezelfde interactieve console voor ENTER/menscontrole — gelijktijdig draaien zou dat door elkaar halen. `BUSINESS_SCAN_STATE`/`BUSINESS_STATE_LOCK` blijven wél een eigen, van woningen gescheiden statusobject (geen inhoudelijke vermenging, alleen de mutex is gedeeld). Dit was zonder regressierisico mogelijk: de bestaande woningenroutes zijn functioneel ongewijzigd, alleen een optionele `geblokkeerd`-queryparameter is toegevoegd voor een duidelijkere melding wanneer de lock al bezet is door de andere scan.

## Scan-scope vs. analysefilter
Zoals gevraagd: de scan-scope wordt uitsluitend bepaald door de geselecteerde **regio's** en **categorieën**. Status en transactietype worden bij het starten van een scan wél meegestuurd/onthouden (om na afloop naar exact dezelfde analyseweergave terug te kunnen linken), maar sturen de scanner zelf niet aan — die haalt altijd beide prijssoorten per object op.

## Exacte scanidentiteit
`app.py` geeft de door de gebruiker geselecteerde plaatsen/categorieën ongewijzigd door aan zowel de scanner (`--plaatsen`, `--objecttypes`) als de historie-tool (`--plaatsen`, `--categorieen`) — identieke lijsten, geen verbreding/versmalling. De historie-tool bepaalt zelf de canonieke identiteit (sortering/uniek/samenvoegen) zoals in Fase 2/3 al vastgelegd; daar is niets aan gewijzigd.

## Succes-/foutafhandeling
- Scanner-subprocess zonder stdin/stdout/stderr-omleiding (zelfde reden als bij woningen: laat de zichtbare Chrome + interactieve ENTER-/menscontrole-flow in hetzelfde consolevenster werken). Geen `shell=True`, overal `sys.executable`.
- Na een succesvolle scanner-run wordt uitsluitend het nieuwste `funda_business_*_alles.csv`-bestand gekozen (mtime ≥ scan-starttijd − 2s); `*_checkpoint.csv` wordt door het glob-patroon zelf al nooit gekozen.
- Scanner-exitcode ≠ 0 → geen historie-import, status "error", geen traceback aan de gebruiker.
- Historie-import (niet-interactief) loopt via `subprocess.run` met capture van stdout/stderr naar een logbestand `output/bedrijfsmatig/_business_historie_import_log_<stamp>.txt`; bij een fout daar wordt alleen de laatste zinvolle regel getoond (nooit een volledige Python-traceback).
- Dubbele import: de historie-tool herkent dit zelf via `scan_hash` en beëindigt netjes (geen foutmelding, geen crash) — de webapp toont in dat geval gewoon de succesmelding van de subprocess-aanroep.
- Bij succes toont de statuspagina: regio's, categorieën, starttijd, eindtijd, aantal objecten (CSV), CSV-bestandsnaam, "Historie-import geslaagd"-melding, en (best-effort, uit de tekstuitvoer van de historie-tool geparsed) een nulmeting-melding of een tabelletje met mutatieaantallen — puur informatief, nooit een verzonnen getal.
- Knop "Bekijk actuele analyse" linkt naar `/business/analyse` met exact dezelfde regio's/categorieën/status/transactietype als bij het starten van de scan.

## Bekende beperking (zoals gevraagd te documenteren)
Bij een herstart van de Flask-app tijdens een lopende scan gaat de in-memory status (`SCAN_STATE`/`BUSINESS_SCAN_STATE`, beide locks) verloren — dit was al zo voor woningen en is in Fase 4 bewust niet veranderd (geen aanvullende persistentie toegevoegd, blijft buiten scope).

## Tests
Automatisch getest (venv + `flask.test_client()` + gemockte `subprocess.Popen`/`subprocess.run`, **geen live Funda-scan**), 59 checks, 0 gefaald:
1–2. Validatie zonder regio/zonder categorie → nette redirect + melding op het juiste (Bedrijfsmatig-)tabblad. ✅
3. Business-scan-lock: tweede poging tijdens een actieve Business-scan start geen tweede subprocess en toont de bestaande status. ✅
4–5. Globale lock werkt in beide richtingen: een actieve woningen-scan blokkeert een Business-scan en andersom (beide met een duidelijke "geblokkeerd"-melding + link naar de andere statuspagina). ✅
6–9, 12. Subprocess-commando's geverifieerd: juiste `--plaatsen`/`--objecttypes` naar de scanner, juiste `--plaatsen`/`--categorieen` naar de historie-tool, juiste `--output-map`, `sys.executable` gebruikt, nergens `shell=True`. ✅
10–11. Alleen de nieuwe `*_alles.csv` (op mtime) wordt gekozen; een `*_checkpoint.csv` wordt nooit als resultaat herkend. ✅
13. Scanner-exitcode ≠ 0 → historie-tool wordt niet aangeroepen. ✅
14. Gesimuleerde historie-fout → status "error", geen volledige traceback in de getoonde melding. ✅
15. Volledig gemockte succesflow → status "success", juiste aantal objecten/CSV-naam/eindtijd, nulmeting correct herkend. ✅
16. Link "Bekijk actuele analyse" bevat exact dezelfde regio/categorie/status/transactietype-parameters als de gestarte scan. ✅
17. Business-analyse-regressie (vóór een nieuwe scan): Actief aanbod 35, Kantoor 18, Bedrijfsruimte 18, Dual-listed 3, Huuraanbod 28 (23 + 5), Koopaanbod 10 (8 + 2), bekende berekende jaarhuur € 483.123, totale bekende koopvraagprijs € 19.252.000, 1 Uit aanbod (Neutronenlaan 70), 35 Ongewijzigd — allemaal exact zoals opgegeven. ✅
18. Woningen-regressie: `/`, `/analyse`, `/scan/start`-validatie, `/scan/status`, `/dashboard` — allemaal nog status 200. ✅
- `py -m py_compile` geslaagd voor `app.py` én (ongewijzigd) beide scanners/historie-tools; `--help` van beide Business-tools nog steeds correct (bevestigt dat de CLI intact is).
- Bestandstijdstempels van beide scanners, beide historie-tools en beide databases: ongewijzigd t.o.v. vóór deze fase.

## Live eindtest (door de gebruiker uit te voeren — bewust niet autonoom gedaan i.v.m. mogelijke menscontrole)
1. Start `py app.py`.
2. Open Makelaar Monitor in de browser.
3. Ga naar het tabblad "Bedrijfsmatig vastgoed".
4. Regio: Uden (of naar keuze).
5. Categorieën: Kantoor + Bedrijfsruimte (of naar keuze).
6. Klik "Nieuwe scan uitvoeren".
7. **Controleer wat te checken is:** de statuspagina toont "Business-scan wordt uitgevoerd" met auto-refresh en de menscontrole-tekst; in het Chrome-venster dat de scanner opent, los een eventuele menscontrole/captcha handmatig op en druk zo nodig ENTER in het consolevenster waarin `py app.py` draait.
8. Wacht tot de statuspagina "Scanner klaar, historie-import bezig" en daarna "Business-scan volledig geslaagd" toont, met regio's/categorieën/starttijd/eindtijd/aantal objecten/CSV-bestandsnaam en de historie-importmelding (en, als dit niet de eerste scan voor deze combinatie is, een mutatietabel).
9. Klik "Bekijk actuele analyse" en controleer dat `/business/analyse` opent met dezelfde regio/categorie-selectie en de nieuwe cijfers (KPI's, makelaarstabel, objectentabel, en — als er een vorige vergelijkbare scan was — een bijgewerkte mutatiesectie).
10. Optioneel: probeer tijdens deze scan een woningen-scan te starten (of omgekeerd) om te zien dat dit netjes wordt geblokkeerd met een duidelijke melding.

## Nog NIET gedaan (bewust, buiten scope van deze fase)
Detailpagina-verrijking, nieuwe dashboards/grafieken, Google Sheets-export, automatische planning, cloudhosting, database-schemawijzigingen.

## Git status
```
On branch main
Your branch is up to date with 'origin/main'.
Changes not staged for commit:
	modified:   .gitignore
	modified:   README.md
Untracked files:
	CLAUDE.md
	app.py
	docs/
	historie/
	research/
	scanner/
	web/
```
Geen commit of push uitgevoerd.

## Aanbevolen volgende stap
De live eindtest hierboven laten uitvoeren door de gebruiker in de browser. Bij akkoord: overwegen of de Business-tab ook een dashboard-uitbreiding of Google Sheets-export nodig heeft, of dat de huidige functionaliteit voorlopig volstaat.
