# Makelaar Monitor — status

_Laatst bijgewerkt: 2026-09-10_

# Correctieronde 2026-09-10: Marktintensiteit per gemeente - noemer beperkt tot geselecteerde inwoners (meest recent - lees dit eerst)

**Opdracht:** de gemelde 592,6-index (Bernheze) in Marktintensiteit → Per gemeente was nog niet gecorrigeerd. Gevraagd: de inwonersnoemer voor de Marktintensiteitsberekening mag uitsluitend bestaan uit de daadwerkelijk geselecteerde plaatsen (niet het volledige officiële gemeentelijke inwonertal), zodat teller en noemer exact dezelfde geografische scope hebben. De bovenste tabel ("Verdeling per plaats/gemeente") moet het volledige officiële inwonertal wél als context blijven tonen.

**Root cause:** `bouw_gemeenteverdeling()` in `app.py` gaf hetzelfde veld (`inwoners` = `inwoners_gemeente`, het volledige officiële CBS-inwonertal) door aan zowel de bovenste tabel ALS aan `_bereken_marktintensiteit_velden()`. Bij een gemengde selectie (4 Bernheze-kernen + Vinkel, dat bij gemeente 's-Hertogenbosch hoort) telde daardoor de volledige bevolking van 's-Hertogenbosch (162.272) mee in de noemer, terwijl er verder niets van die gemeente gescand was.

**Fix (`app.py`, `bouw_gemeenteverdeling()`):**
- Elke gemeente-entry houdt nu een `plaatsen_in_selectie`-set bij (gevuld vanuit zowel de expliciete regiofilter `alle_plaatsen` als de daadwerkelijke rijen).
- Nieuw, apart veld `inwoners_selectie` = som van `inwoners_plaats` (uit `GEO_REFERENTIE`) van uitsluitend die geselecteerde plaatsen binnen de gemeente. `None` (nooit een gok) als geen van die plaatsen een bekend plaats-inwonertal heeft.
- `totaal_inwoners_bekend` (de globale noemer-basis) is nu de som van `inwoners_selectie` over alle gemeenten, niet meer de som van de volledige officiële gemeentetallen.
- `_bereken_marktintensiteit_velden()` (ongewijzigde, generieke formule) wordt nu gevoed met `inwoners_selectie` in plaats van het officiële `inwoners`.
- Het bestaande veld `inwoners_weergave` (= volledig officieel gemeentelijk inwonertal, gebruikt in de bovenste tabel + "Aanbod per 1.000 inw.") blijft **ongewijzigd**. Nieuw veld `inwoners_selectie_weergave` wordt gebruikt als "Inwoners"-kolom in de Marktintensiteit-tabel. `bouw_plaatsverdeling()` kreeg hetzelfde `inwoners_selectie_weergave`-veld (identiek aan `inwoners_weergave` op plaatsniveau, want een plaats IS al de kleinste scope) zodat de template op beide niveaus (Per plaats/Per gemeente) hetzelfde veld kan gebruiken in de Marktintensiteit-tabel.
- `web/templates/resultaat.html`: de "Inwoners"-kolom in de Marktintensiteit-subtabel (regel ~281) leest nu `g.inwoners_selectie_weergave` in plaats van `g.inwoners_weergave`. De bovenste "Verdeling per plaats/gemeente"-tabel is niet aangeraakt.
- `bouw_makelaar_lokale_marktpositie()` (makelaarsprofiel) is niet gewijzigd - die hergebruikt `marktintensiteitsindex`/`inwoners_weergave` uit de al-berekende `context_tabel` (plaatsverdeling/gemeenteverdeling), dus profiteert automatisch van de fix (de `marktintensiteitsindex` in het profiel is nu ook correct) zonder eigen codewijziging.

**Geverifieerd op de echte, actuele selectie (scan_id 7, 118 objecten - Heesch/Heeswijk-Dinther/Nistelrode/Vinkel/Vorstenbosch, alle statussen, Alles-bouwcategorie), zowel via directe functieaanroep als via de daadwerkelijk gerenderde HTML van `/analyse?...&geo_niveau=gemeente`:**

| Gemeente | Officieel inwonertal (bovenste tabel, ongewijzigd) | Geselecteerde inwoners (nieuwe noemer) | Aandeel inwoners | Aanbod | Aandeel aanbod | Verwacht aanbod | Marktintensiteitsindex |
|---|---|---|---|---|---|---|---|
| Bernheze | 32.943 | **31.240** (Heesch 14.165 + Heeswijk-Dinther 8.895 + Nistelrode 6.725 + Vorstenbosch 1.455) | 91,8% | 118 | 100,0% | 108,3 | **108,9** (was 592,6) |
| 's-Hertogenbosch | 162.272 | **2.795** (alleen Vinkel) | 8,2% | 0 | 0,0% | 9,7 | **0,0** (ongewijzigd, was al 0,0 - Vinkel heeft geen aanbod) |

Som geselecteerde inwoners = 31.240 + 2.795 = 34.035, exact gelijk aan de som van `inwoners_weergave` in de plaatsverdeling voor dezelfde selectie (sluitcontrole: teller en noemer nu aantoonbaar dezelfde scope). De bovenste tabel toont nog steeds 32.943 en 162.272 (bevestigd in de gerenderde HTML, ongewijzigd).

**Test:** `tests/test_marktintensiteit_20260910.py` uitgebreid met `test_gemeente_marktintensiteit_gebruikt_alleen_geselecteerde_inwoners()` (16 nieuwe checks, incl. een live route-check die expliciet controleert dat de foutieve 592,6 niet meer in de HTML voorkomt en dat 162.272 niet meer als noemer in de Marktintensiteit-tabel staat, terwijl de bovenste tabel dat cijfer wél nog bevat). **Resultaat: alle 66 checks in dit bestand geslaagd** (was 50, nu 66).

Regressie: `tests/test_regressie.py` 79/79, `tests/test_business_regressie.py` 18/18, `tests/test_stabilisatie_20260909.py` 41/41 - allemaal ongewijzigd geslaagd, geen regressie. `docs/BUGLIST.md`-item "Aandachtspunt (geen bug, methodologische kanttekening)" over de 592,6-index is hiermee opgelost (niet meer van toepassing - de scope-inconsistentie is gecorrigeerd, geen documentatie-only-item meer).

**Gewijzigde bestanden:** `app.py` (`bouw_gemeenteverdeling()`, kleine additie aan `bouw_plaatsverdeling()`), `web/templates/resultaat.html` (1 regel), `tests/test_marktintensiteit_20260910.py` (nieuwe testfunctie). Geen schemawijziging, geen andere functionaliteit gewijzigd, geen commit/push (per opdracht).

---

# Vorige ronde 2026-09-10: Marktintensiteit + makelaarsprofiel-context + Business-referentiedata + hernieuwd Funda-linkonderzoek

Voortgezet op de ongewijzigde working tree (geen reset/rollback/database-opruiming/commit/push). Herstelpunt blijft `0b464ed750708c4c61e9f35df0ed4791e46f7a99`. Alle eerder handmatig geteste functionaliteit blijft volledig behouden - dit was één gerichte uitbreiding met 4 onderdelen, geen commit/push (per opdracht).

## 1. Marktintensiteit (Woningen) - Verdeling per plaats/gemeente

**Doel:** inwonertal ook analytisch benutten naast het bestaande aanbodcijfer, met neutrale terminologie (geen conclusie over woningtekort/verkoopsnelheid/marktgezondheid).

**Implementatie:** nieuwe functie `_bereken_marktintensiteit_velden()` in `app.py`, gedeeld tussen `bouw_plaatsverdeling()` en `bouw_gemeenteverdeling()` (geen duplicatie van de formule). Velden per plaats/gemeente: aandeel aanbod (%), aandeel inwoners (%), verschil in procentpunten, verwacht aanbod, marktintensiteitsindex. Formules letterlijk zoals gevraagd:
- `verwacht aanbod = totaal aanbod x (inwoners gebied / totaal inwoners selectie)`
- `marktintensiteitsindex = werkelijk aanbod / verwacht aanbod x 100` (100 = evenredig aan inwonertal, >100 = relatief hoge aanbodintensiteit, <100 = relatief lage - geen verdere interpretatie).

Alleen berekend als het inwonertal van het gebied zelf EN van minstens één gebied in de selectie bekend is (via de bestaande, handmatig geverifieerde `GEO_REFERENTIE`) - anders overal "-"/`None`, nooit een gok. Volgt dezelfde scan/regio/status/bouwcategorie-filters als de rest van de pagina (herberekend per aanroep van `bouw_analyseresultaat()`). De bestaande nulgebieden-fix (`alle_plaatsen`-seeding) blijft volledig intact - nulgebieden tonen aandeel aanbod 0% en index 0 i.p.v. te verdwijnen.

**Weergave:** nieuwe sub-sectie "Marktintensiteit" in `web/templates/resultaat.html`, direct onder de bestaande Verdeling per plaats/gemeente-tabel (die zelf ongewijzigd is), binnen dezelfde plaats/gemeente-toggle. Geen nieuwe pagina, geen schemawijziging.

**Resultaat voor de huidige 5 plaatsen (scan_id 7, 118 objecten, alle statussen/bouwcategorieën):**

| Plaats | Aanbod | Aandeel aanbod | Inwoners (bron: GEO_REFERENTIE) | Aandeel inwoners | Marktintensiteitsindex |
|---|---|---|---|---|---|
| Heeswijk-Dinther | 50 | 42,4% | 8.895 | 26,1% | 162,1 |
| Heesch | 40 | 33,9% | 14.165 | 41,6% | 81,4 |
| Nistelrode | 26 | 22,0% | 6.725 | 19,8% | 111,5 |
| Vorstenbosch | 2 | 1,7% | 1.455 | 4,3% | 39,6 |
| Vinkel | 0 | 0,0% | 2.795 | 8,2% | 0,0 |

Som aandeel aanbod en som aandeel inwoners kloppen beide exact op 100,0% (geverifieerd in `tests/test_marktintensiteit_20260910.py`). Op gemeenteniveau: Bernheze (118 objecten, 100% van het aanbod, 32.943 inwoners, 16,9% van de gecombineerde bevolking) versus 's-Hertogenbosch (0 objecten - Vinkel, het enige gescande deel van deze gemeente, heeft nu geen aanbod - 162.272 inwoners, 83,1%), vandaar de hieronder toegelichte 592,6-kanttekening voor Bernheze.

**Methodologische kanttekening (geen bug, letterlijk zo gevraagd):** op GEMEENTE-niveau geeft de huidige selectie (4 Bernheze-plaatsen + Vinkel, dat bij gemeente 's-Hertogenbosch hoort) een marktintensiteitsindex van 592,6 voor Bernheze, omdat de volledige bevolking van 's-Hertogenbosch (162.272) meetelt in de noemer terwijl er verder niets van 's-Hertogenbosch gescand is. Dit is wiskundig correct volgens de gevraagde formule, maar vertekent de interpretatie bij gemengde gemeente-selecties. Gedocumenteerd in `docs/BUGLIST.md`, niet gecorrigeerd.

## 2. Makelaarsprofiel: marktintensiteit als lokale context

`bouw_makelaar_lokale_marktpositie()` kreeg een optionele `context_tabel`-parameter (de al-berekende `plaatsverdeling`/`gemeenteverdeling`) en voegt `inwoners_weergave`/`marktintensiteitsindex` toe als extra, expliciet gelabelde ("context") kolommen bij Marktpositie per plaats/gemeente. **`marktaandeel_pct` blijft ongewijzigd `objecten_makelaar / totaal_markt * 100`** - geverifieerd dat inwonertal deze berekening nooit raakt (zowel met als zonder `context_tabel` identiek marktaandeel). Achterwaarts compatibel: zonder `context_tabel` (bv. een oudere aanroep) valt de context netjes terug op "-"/`None` i.p.v. te crashen.

**Concreet voorbeeld — Kordaat Makelaars in Nistelrode** (dezelfde 5-plaatsenselectie, alle statussen): 7 objecten op een totale lokale markt van 26 → marktaandeel 26,9%, ranking #1. Context: 6.725 inwoners, marktintensiteitsindex 111,5 (Nistelrode heeft dus een licht bovengemiddeld aanbod t.o.v. het inwonertal binnen de huidige selectie). Op gemeenteniveau: 7 van 118 objecten in heel Bernheze → marktaandeel 5,9%, ranking #5, met de hierboven genoemde 592,6-kanttekening als context.

## 3. Bedrijfsmatig: Business-equivalent bewust NIET gebouwd

Op expliciet verzoek alleen gedocumenteerd (geen code): het Business-equivalent van marktintensiteit heeft geen zinvolle inwonertal-basis; de meest logische referentie is economisch (`actief bedrijfsmatig aanbod / aantal bedrijfsvestigingen of ondernemingen`). Er is nu geen betrouwbare, handmatig geverifieerde bron voor vestigingsaantallen per plaats/gemeente in de app (vergelijkbaar met hoe `GEO_REFERENTIE` is opgebouwd). Aanbevolen vervolgstap: CBS StatLine/vestigingenregister onderzoeken en handmatig verifiëren per plaats/gemeente, pas dan de ratio bouwen. Zie `docs/BUGLIST.md`.

## 4. Funda-link makelaar: hernieuwd onderzoek via de woningdetailpagina - nog steeds geen betrouwbare bron

Op verzoek specifiek onderzocht of een NORMALE Funda woningdetailpagina (niet alleen zoekresultaatkaarten) een kantoor-ID/makelaarsprofiel-URL prijsgeeft. Getest: Kordaat Makelaars (3 detailpagina's in Nistelrode) en Heuvel Makelaars (Heesch).

**Resultaat:** zowel een kale HTTP-fetch als een Playwright/Chromium-fetch (zonder de sessie/profiel van de productiescanner) kregen consequent Funda's bot-detectie-interstitial "Je bent bijna op de pagina die je zoekt" terug — geen echte pagina-inhoud, dus niets om een kantoor-ID uit te lezen. De bestaande, geautoriseerde scanner werkt alleen dankzij een niet-headless, persistente, echte Chrome-profielsessie (`launch_persistent_context(channel="chrome", headless=False, ...)`) MET een expliciet `needs_human()`/`pause()`-mechanisme voor als Funda zelfs dan nog een controle toont. Een onbeheerde achtergrondcontrole kan dat niet gebruiken zonder een zichtbaar browserscherm te openen en mogelijk een captcha te moeten oplossen - dat is bewust niet geprobeerd (geen ongevraagd browserscherm, geen bot-detectie-omzeiling buiten de geautoriseerde scanscope, sessieprofiel van de echte scanner niet belasten).

**Conclusie:** nog steeds geen betrouwbare, automatiseerbare bron. Conform instructie: geen link/knop gebouwd, nooit een URL geraden op basis van de naam. `bepaal_funda_makelaar_link()` blijft ongewijzigd (`None`). Geen schema-wijziging (geverifieerd: geen nieuwe kolom in `snapshots`), geen wijziging aan historische scans, scanner-completenessfix niet aangeraakt. Zie `docs/BUGLIST.md` voor het optionele toekomstige pad (incidenteel een detailpagina bezoeken tijdens een normale, door de gebruiker gestarte scan).

## 5. Tests en database/schema

Nieuw: `tests/test_marktintensiteit_20260910.py` - **50/50 checks geslaagd** (formulezuiverheid inclusief "nooit gokken", totalen/aandelen op echte data, nulgebieden, enkele-plaats-filter, makelaarsprofiel-context zonder invloed op marktaandeel, live route-checks voor zowel analysepagina als makelaarsprofiel, Funda-linkonderzoek, schema-ongewijzigd-check).

Bestaande suites herdraaid, **geen regressies**:
- `tests/test_regressie.py` (Woningen): 79/79.
- `tests/test_business_regressie.py`: 18/18.
- `tests/test_stabilisatie_20260909.py`: 41/41 (bevestigt ook expliciet dat `bouw_makelaar_lokale_marktpositie()` zonder `context_tabel` blijft werken).

**Database/schema:** geen enkele wijziging. Beide SQLite-bestanden read-only benaderd; geen nieuwe kolommen, geen migratie, geen scans uitgevoerd, geen historische data aangepast.

---

# Laatste stabilisatieronde 2026-09-09 (avond)

Voortgezet op de ongewijzigde working tree (geen reset/rollback/database-opruiming/commit/push). Herstelpunt blijft `0b464ed750708c4c61e9f35df0ed4791e46f7a99`. De handmatig geteste functionaliteit uit eerdere ronden is volledig behouden - deze ronde loste uitsluitend de 6 onderstaande, specifiek gevraagde punten op.

## 1. Methodiekbreuk Woningen opgelost

**Probleem:** de ancestor_card-fix (vorige ronde) toonde aan dat oude Woningen-scans structureel onvolledig waren. De eerste betrouwbare productie-baseline is scan_id 7 (09-09-2026 10:09:54, Heesch/Heeswijk-Dinther/Nistelrode/Vinkel/Vorstenbosch, **118 objecten** - tegenover 37 in de vorige scan). Een rechtstreekse vergelijking (37 → 118) zou een fictieve aanbodsprong van +81 tonen.

**Oplossing (bewust GEEN database-/schemawijziging):** een simpele, globale tijdstempel-constante `WONINGEN_METHODIEK_WIJZIGING = "2026-09-09T10:09:54"` in `app.py`. Elke scan met `scanmoment >= deze grens` telt als "nieuwe, betrouwbare methodiek"; elke scan ervoor als "oude methodiek". Twee plekken respecteren deze grens nu:
- `haal_vorige_scan_id()` (Marktdynamiek): een scan van de nieuwe methodiek zoekt zijn "vorige scan" nooit voorbij de grens. Een scan van de OUDE methodiek blijft ongewijzigd vergelijken met een andere oude scan (bestaande, al gevalideerde 39→37/-2-case blijft dus gewoon werken bij historische weergave).
- `haal_woningen_trend()` (Dashboard): de trendreeks voor een gebied bevat alleen scans uit dezelfde "episode" als de meest recente scan van dat gebied. Oudere scans worden niet stilzwijgend meegenomen, en het aantal uitgesloten scans wordt expliciet getoond ("2 oudere scan(s) ... bewust uitgesloten").

**Resultaat:** scan_id 7 toont nu correct GEEN Marktdynamiek-vergelijking, met de duidelijke melding "Dit is de eerste scan van de nieuwe, betrouwbare meetreeks... Vanaf de volgende scan werkt dit weer normaal." Het Dashboard toont dezelfde waarschuwing en een trendgrafiek met precies 1 punt (i.p.v. een misleidende lijn van 37 naar 118). Oude scans (incl. scan 5→6, 39→37) blijven **volledig intact en raadpleegbaar** via Historische Analyse - niets verwijderd of herschreven. Zodra een 2e scan met de nieuwe methodiek binnenkomt, werken Marktdynamiek/Dashboard automatisch weer normaal (geverifieerd via een directe aanroep van `haal_vorige_scan_id()` met een gesimuleerd toekomstig scanmoment).

De peildatumselector op de Analysepagina markeert oude scans nu met "(vóór methodiekwijziging)".

## 2. Makelaarspositie Top 5

Woningen- en Business-makelaarstabel tonen standaard de eerste 5 rijen (bestaande rangschikking, marktleider bovenaan - ongewijzigd); de overige rijen staan gewoon in de HTML (`<tr class="rij-extra hidden">`), niet in de backend beperkt. Een knop "Alle makelaars tonen (N)" (N dynamisch) toont/verbergt ze via een kleine, dependency-vrije JS-toggle in `web/static/js/app.js` (`initMakelaarsTop5()`); na openen verschijnt "Minder tonen". Alle KPI's, marktaandelen, sluitcontroles en CSV-export blijven ongewijzigd over ALLE makelaars rekenen (niets in de backend aangepast) - expliciet geverifieerd: de HTML bevat exact (totaal makelaars − 5) verborgen rijen.

## 3. Nulgebieden

`bouw_plaatsverdeling()` en `bouw_gemeenteverdeling()` krijgen nu de volledige, actief geselecteerde plaatsenlijst mee (`alle_plaatsen=plaatsen`) en zaaien daarmee een 0-rij voor elke geselecteerde plaats/gemeente die geen objecten oplevert, vóórdat de daadwerkelijke rijen worden geteld. Resultaat, exact zoals gevraagd: `Vinkel | 's-Hertogenbosch | 2.795 inwoners | 0 aanbod | 0,0 per 1.000` (en 's-Hertogenbosch verschijnt ook met 0 in de gemeente-weergave). Sluitcontrole bevestigd: som(plaatsverdeling) == som(gemeenteverdeling) == KPI actief aanbod, inclusief het nulgebied.

## 4. Makelaarsprofiel: lokale marktpositie

Nieuwe secties "Marktpositie per plaats" en "Marktpositie per gemeente" op het Woningen-makelaarsprofiel (`bouw_makelaar_lokale_marktpositie()`), volledig dynamisch berekend binnen exact dezelfde scan/filtercontext als het profiel (dezelfde `ruwe_rijen`, geen aparte query). Toont per gebied: objecten van de makelaar, totaal van de markt in dat gebied, marktaandeel, en ranking (bij gelijke aantallen delen makelaars dezelfde rang, bv. #2/#2/#4). Bevestigd met echte, actuele data: **Kordaat Makelaars is #1 in Nistelrode met 7 van de 26 objecten (26,9%)** - dit illustreert exact het effect van de scanner-completenessfix uit de vorige ronde (Kordaat was voorheen vrijwel onzichtbaar). Cijfers zijn dynamisch herberekend, niet de verouderde voorbeeldcijfers uit de opdracht (die dateerden van vóór deze scan).

## 5. Funda-link op makelaarsprofiel - onderzocht, bewust NIET getoond

**Onderzoeksvraag:** kan een betrouwbare Funda-kantoor-ID/-URL uit bestaande scan-/detaildata worden afgeleid?

**Bevinding: NEE, momenteel niet.** `parse_broker()` in `scanner/makelaarsmonitor_v41.py` inspecteert weliswaar `href`/`aria-label` van kandidaat-links om te herkennen dat een link "iets met makelaar" is, maar geeft alleen de TEKSTUELE naam terug - de href zelf (die naar Funda's eigen `/makelaar/<id>-<slug>/`-pagina zou kunnen wijzen) wordt nergens vastgehouden of opgeslagen. Er bestaat dus geen kantoor-ID/URL-veld in de snapshots.

**Beslissing (conform de expliciete instructie):** `bepaal_funda_makelaar_link()` in `app.py` retourneert daarom altijd `None` - er wordt NOOIT een URL gegokt op basis van alleen de naam, en zonder betrouwbare bron wordt geen knop getoond. Dit is bewust generiek (geldt voor elke makelaar, niet alleen Kordaat) en getest voor zowel Kordaat Makelaars als Bernheze Makelaars: beiden krijgen correct geen knop.

**Roadmap (niet geïmplementeerd):** de scanner zou de al-geïnspecteerde `href` in `parse_broker()` kunnen gaan vastleggen (bv. als `Makelaar_Funda_URL`/`Makelaar_Funda_ID`-veld) - dat vereist een kleine scanneraanpassing + een nieuwe CSV/database-kolom, wat bewust buiten deze ronde valt ("vermijd een grote schemawijziging", "tast de scanner-completenessfix niet aan"). Funda's kantoor-ID is in principe een stabielere makelaarsidentiteit dan de tekstuele naam (voorkomt naamvariant-problemen, zie eerdere ronde) en is een logische kandidaat voor een toekomstige Makelaar Intelligence-uitbreiding (zie ook DEEL O in de sectie "Vorige ronde" hieronder).

## 6. Geo-roadmap

Geen CBS/BAG/PDOK-integratie gebouwd (zoals gevraagd). Vastgelegd: de huidige statische `GEO_REFERENTIE`-tabel in `app.py` dekt uitsluitend de 12 plaatsen uit `REGIOS_STANDAARD`; bij uitbreiding van het scanbereik naar nieuwe plaatsen moet deze tabel handmatig (of via een toekomstige, apart te ontwerpen bronkoppeling) worden aangevuld. Zie ook `docs/BUGLIST.md`.

## Getest (deze ronde)
- `py -m py_compile` op alle gewijzigde Python-bestanden - geslaagd.
- `tests/test_regressie.py`: **79/79 checks geslaagd** (was 62 - uitgebreid doordat de makelaarstabel nu meer makelaars bevat; 2 checks moesten worden herkalibreerd op de nieuwe, echte scan_id 7-data - geen van beide was een applicatiebug, zie hieronder).
- `tests/test_business_regressie.py`: **18/18 checks geslaagd**, ongewijzigd.
- **Nieuw:** `tests/test_stabilisatie_20260909.py`: **41/41 checks geslaagd** - dekt specifiek methodiekbreuk (kern + oud-oud-compatibiliteit + toekomstige-vergelijkbaarheid-simulatie), Top 5 (backend onbeperkt), nulgebieden (incl. sluitcontrole), lokale marktpositie (Kordaat + Bernheze Makelaars), en Funda-link-afwezigheid (Kordaat + Bernheze Makelaars + fictieve naam).
- Bevindingen tijdens het testen (geen bugs, test-kalibratie): (1) de eerder vastgelegde DEEL E-validatiecase (scan 5→6) is niet meer "de actuele scan" nu scan 7 bestaat - de test vraagt nu expliciet `scan_id=6` op (dit test tegelijk Historische Analyse); (2) de sluitcontrole "som marktaandeel ≈100%" had een te strakke tolerantie voor een selectie met 27 makelaars (rondingsaccumulatie, elk individueel cijfer bleek exact correct via de aparte formule-check) - tolerantie verbreed naar een aantal-afhankelijke marge.
- Brede routesweep (`/`, `/scan/status`, `/business/scan/status`, `/dashboard` beide markten, `/analyse` met/zonder `geo_niveau`/`scan_id`, `/export/csv`, `/business/analyse`, `/business/export/csv`) - allemaal 200.
- Database-bestandsgroottes vóór/na ongewijzigd door dit werk (enige wijziging: de echte scan die de gebruiker zelf al vóór deze ronde had uitgevoerd).

## Gewijzigde/nieuwe bestanden deze ronde
- `app.py` - methodiekbreuk-logica, nulgebieden in plaats-/gemeenteverdeling, lokale marktpositie, Funda-link-onderzoeksfunctie
- `web/templates/resultaat.html` - methodiekbreuk-banners, Top 5-toggle-knop
- `web/templates/business_resultaat.html` - Top 5-toggle-knop
- `web/templates/dashboard.html` - methodiekbreuk-uitsluitingsmelding
- `web/templates/makelaar_profiel.html` - Funda-linkknop (conditioneel), lokale marktpositietabellen
- `web/static/js/app.js` - `initMakelaarsTop5()`
- `web/static/css/app.css` - kleine, additieve klassen
- `tests/test_stabilisatie_20260909.py` **(nieuw)**
- `tests/test_regressie.py` - 2 checks herkalibreerd (zie boven)
- `docs/CLAUDE_STATUS.md`, `docs/BUGLIST.md` - deze update

## Database (expliciet bevestigd)
Geen schemawijziging, geen productiedata gewijzigd door dit werk. De methodiekbreuk-grens is een code-constante, geen databaseveld. Bestandsgroottes ongewijzigd sinds vóór deze ronde.

---

# Ontwikkel- en stabilisatieronde 2026-09-09 (ochtend/middag)

Voortgezet op de ongewijzigde working tree van de vorige ronde (geen reset/rollback/commit/push, zoals gevraagd). Herstelpunt blijft `0b464ed750708c4c61e9f35df0ed4791e46f7a99`. **Nog steeds geen commit/push** - de gebruiker verzamelt eerst zelf bevindingen; daarna volgt een aparte stabilisatie-/bugfixronde.

## 1. Scanner completeness / Kordaat - ERNSTIGE BUG GEVONDEN EN OPGELOST

**Bevinding:** Kordaat Makelaars ontbrak niet door een filterverschil - de Woningenscanner miste structureel tot ~80% van de echte Funda-resultaten per plaats.

**Onderzoek (live, tegen de echte site, read-only totdat de fix geverifieerd was):**
- Funda meldt zelf "27 koopwoningen in Nistelrode"; een live crawl van pagina 1+2 vond ook exact 27 unieke detail-links. Onze database bevatte voor dezelfde plaats (nieuwste scan, alle statussen) slechts **6** objecten.
- Root cause getraceerd tot `ancestor_card()` in `scanner/makelaarsmonitor_v41.py`: deze functie liep de DOM-ouders van elke detail-link af op zoek naar "de kaart", en accepteerde een niveau alleen als dat niveau **≤2** `<a href="/detail/koop/...">`-elementen bevatte. Funda's huidige kaartontwerp bevat per object vaak 3+ eigen links naar dezelfde detailpagina (foto, titel, "Bekijk dit huis"-knop) - de oude telling overschatte daardoor structureel het aantal "kaarten" en verwierp geldige, complete kaarten.
- Uitgesloten als oorzaak: timing/race conditions (10s extra wachten + `networkidle` veranderde het resultaat niet - exact dezelfde 15 van de 19 links bleven falen), en pagineringslogica (ongewijzigd sinds de vorige ronde, functioneerde correct).
- Live DOM-vergelijking van een geslaagde vs. mislukte kaart bevestigde het verschil concreet (zie `_unieke_detail_urls_in()`-commentaar in de code).

**Fix:** `ancestor_card()` telt nu het aantal **unieke** detail-URL's binnen een DOM-node (via `_unieke_detail_urls_in()`), niet het aantal `<a>`-elementen. Een kaart met meerdere zelf-referentiële links naar dezelfde detailpagina telt nu correct als 1 kaart.

**Resultaat (live geverifieerd met de daadwerkelijke, gefixte productiecode):**
- Nistelrode pagina 1-3: 46 kaarten verwerkt, **0 mislukkingen** (was: tientallen mislukkingen per pagina).
- Een volledige live scan van uitsluitend Nistelrode leverde **26 unieke objecten** op (vs. 6 hiervoor) - nagenoeg gelijk aan Funda's eigen "27".
- **Kordaat Makelaars bleek de GROOTSTE makelaar in Nistelrode: 7 objecten** (5 Beschikbaar, 2 Verkocht onder voorbehoud) - meer dan Bernheze Makelaars (5). Dit is exact de data die eerder ontbrak.
- Regressie op Uden (andere plaats, andere content-mix): 19/19 kaarten correct, geen regressie.
- Deze diagnostische/proef-CSV's zijn **niet geïmporteerd** in de productiedatabase (conform DEEL A4: geen database-reparatie, de scanner zelf is gefixt). De bestaande, oudere scans in de database reflecteren nog de oude, onvolledige resultaten totdat de gebruiker opnieuw scant.

**DEEL A4 (makelaarspagina als toekomstige bron, nu niet bouwen):** vastgelegd in de roadmapsectie hieronder (§ Toekomstige Makelaars Intelligence, al aanwezig sinds de vorige ronde) - geen tweede scraper gebouwd, geen data van de makelaarspagina in de database gezet.

## 2. Makelaarsprofiel-crash - ROOT CAUSE en STRUCTURELE FIX

**Root cause (bevestigd via reproductie, Werkzeug-traceback):** `TypeError: '>' not supported between instances of 'str' and 'int'` in `bouw_makelaar_profiel_woningen()`, bij `r["vraagprijs"] > 0`.

De route `/makelaar/<module>/<naam>` gaf `resultaat["rijen"]` door aan de profielberekening. Dat zijn de door `verrijk_rij()` **al geformatteerde presentatierijen** - `verrijk_rij()` overschrijft `vraagprijs`/`woonoppervlakte` daadwerkelijk met strings zoals `"€ 795.000"`/`"614 m²"`. Zodra een makelaar minstens één object met een bekende vraagprijs had, crashte de vergelijking `"€ 795.000" > 0`. (De eerdere, kennelijk "geslaagde" test in de vorige ronde testte toevallig een makelaar zonder matchende objecten binnen die specifieke filterselectie, waardoor de buggy vergelijking nooit werd bereikt - een test-blinde-vlek, geen echte fix.)

Business bleek NIET vatbaar voor dezelfde klasse fout: `verrijk_business_rij()` overschrijft de ruwe velden niet, maar voegt uitsluitend nieuwe `*_weergave`-sleutels toe - de ruwe `koopprijs`/`oppervlakte_m2`/`berekende_huur_per_jaar` blijven numeriek. Expliciet getest en bevestigd (zie hieronder).

**Structurele fix (niet zomaar een lokale patch):**
- `bouw_analyseresultaat()` en `bouw_business_analyseresultaat()` geven nu **ook** `ruwe_rijen` terug (numeriek, ongeformatteerd) naast de bestaande, geformatteerde `rijen`.
- De route `/makelaar/<module>/<naam>` gebruikt nu uitsluitend `resultaat["ruwe_rijen"]` voor de profielberekening.
- Regel toegevoegd als code-commentaar bij beide basis-dicts: "calculatie op raw data, formattering pas bij presentatie" - berekeningen mogen `rijen` (de geformatteerde variant) nooit meer gebruiken.

**Getest (Flask test_client + echte, actuele read-only data, `tests/test_regressie.py` + `tests/test_business_regressie.py`):**
- Alle 11 echte makelaars uit de nieuwste Woningen-scan (incl. Bernheze Makelaars, Kordaat Makelaars, en een makelaar met precies 1 object) + "Onbekend" + een fictieve naam: **geen enkele TypeError/serverfout**.
- Business: 4 echte makelaars + "Onbekend" + fictieve naam: eveneens geen fout.
- Object met ontbrekende vraagprijs/woonoppervlakte kwam niet apart voor in de huidige dataset (alle velden bekend), maar het codepad is expliciet gedekt door de structurele fix (raw `None`-waarden geven nooit een `TypeError`, ongeacht welke makelaar).

## 3. Historische Analyse (DEEL C) - nieuw gebouwd

- **Geen schemawijziging nodig**: `snapshots` was al per `scan_id` opgeslagen met alle destijds geldende waarden (vraagprijs, status, makelaar, oppervlakte, plaats) - dit ONDERSTEUNDE historische analyse al, alleen ontbrak de UI/route-ondersteuning om een ander scan_id dan "het nieuwste" te kiezen.
- Nieuwe peildatum-/scanmomentselector bovenaan de Woningen-analysepagina (`Analyse van: [ peildatum ▼ ]`), toont alle scans met EXACT hetzelfde gebied, nieuwste eerst, met aantal objecten erbij. Wisselen herbouwt dezelfde 6 secties (Kerncijfers/Makelaarspositie/Verdeling/Prijssegmenten/Marktdynamiek/Export) volledig uit dat gekozen snapshot.
- Duidelijke gele waarschuwingsbalk bovenaan bij een historische weergave + knop "Terug naar actuele analyse".
- **DEEL C2 (kritiek, expliciet getest):** `haal_geschiedenis()` (eerste/laatste waarneming, gebruikt voor "dagen in monitor") kreeg een `tot_en_met_scan_id`-parameter. Bij een historische analyse wordt dit altijd meegegeven, zodat een LATERE (nog "toekomstige" t.o.v. het gekozen peilmoment) scan nooit in de weergave van een oudere scan lekt. Zonder deze fix zou "laatste waarneming" bij een historische scan per ongeluk actuele informatie kunnen tonen.
- Marktdynamiek vergelijkt bij een historische scan automatisch met DIENS eigen voorganger (niet met de nieuwste scan) - werkt via de bestaande `haal_vorige_scan_id()`, die al relatief aan het gekozen scanmoment werkt.
- Ongeldig/niet-bestaand `scan_id` in de URL valt veilig terug op de nieuwste scan (geen crash).
- CSV-export (`/export/csv`) respecteert hetzelfde `scan_id` (zelfde pipeline).
- **Getest (DEEL G, check C8):** dezelfde historische scan + dezelfde filters geven deterministisch dezelfde uitkomst (2x aangeroepen, identiek resultaat).
- **Business:** geen historische-analyse-UI toegevoegd deze ronde (scope bewust op Woningen gehouden, zie DEEL H hieronder) - Business blijft ongewijzigd op "altijd de nieuwste scan".

## 4. Geografische data: Plaats → Gemeente → Provincie + inwoners (DEEL D)

**Bron:** geen live CBS/PDOK-koppeling (voorkomt afhankelijkheid per pageload). In plaats daarvan een kleine, statische Python-constante `GEO_REFERENTIE` in `app.py`, handmatig samengesteld op 2026-09-09 uit **geverifieerde** bronnen (nl.wikipedia.org, citerend CBS-cijfers) - expliciet gecontroleerd via live webonderzoek, NIET uit aannames:

- Uden, Volkel, Zeeland, Odiliapeel, **Schaijk** → gemeente **Maashorst** (60.060 inw., 1-1-2026). Let op: dit weerlegde mijn eigen aanvankelijke aanname dat Schaijk bij "Land van Cuijk" zou horen - expliciet geverifieerd en gecorrigeerd via twee onafhankelijke Wikipedia-bronnen.
- Nistelrode, Heesch, Heeswijk-Dinther, Vorstenbosch → gemeente **Bernheze** (32.943 inw., 1-1-2026).
- Veghel → gemeente **Meierijstad** (85.236 inw., 1-1-2026; Veghel-kern zelf 28.900, 1-1-2023).
- Boekel → gemeente **Boekel** (zelfstandig, niet gefuseerd in 2022; 11.685 inw., 1-1-2026). Geen apart kern-inwonertal gevonden t.o.v. de gemeente - bewust op `None` gelaten i.p.v. een schatting te verzinnen.
- **Vinkel → gemeente 's-Hertogenbosch** (162.272 inw., 1-1-2026; Vinkel zelf 2.795, 1-1-2023) - Vinkel was tot en met 2014 onderdeel van de (opgeheven) gemeente Maasdonk en ging op 1-1-2015 naar 's-Hertogenbosch. Dit verklaart ook mede waarom Funda "vinkel" niet als eigen `selected_area`-slug herkent (zie punt 1 hierboven/vorige ronde): het is een kleine, relatief recent geannexeerde kern binnen een grote stad.
- Alle plaatsen: provincie Noord-Brabant.

**DEEL D2 (geen live call):** volledig statisch, geen netwerkverkeer tijdens page load - de scannerdata blijft onafhankelijk van deze referentielaag.

**DEEL D3/D4:** sectie "3. Verdeling per plaats/gemeente" op de Woningen-analysepagina kreeg een toggle "Per plaats" / "Per gemeente" (GET-parameter `geo_niveau`, zelfde patroon als de Dashboard-selectors). Beide tabellen tonen: Gemeente, Provincie (alleen gemeente-weergave), Inwoners, Actief aanbod, **Aanbod per 1.000 inwoners** (`= actief aanbod / inwoners × 1000`, alleen berekend als het inwonertal bekend is), plus de bestaande Beschikbaar/Onder bod/VOV/Totale vraagwaarde/Gem. €/m². Ontbrekende inwonerdata toont altijd "-", nooit een verzonnen waarde. Makelaarspositie staat ongewijzigd BOVEN deze sectie.

**DEEL D5 (marktaandeel per gemeente in makelaarsprofiel):** niet geïmplementeerd deze ronde (tijdsafweging) - het makelaarsprofiel toont nog steeds alleen plaatsen or segmenten, geen gemeente-uitsplitsing. Genoteerd in `docs/BUGLIST.md` als vervolgstap; is met de nu aanwezige `GEO_REFERENTIE`-laag een kleine toevoeging.

## 5. Marktdynamiek gevalideerd + filterconsistentie (DEEL E)

De exacte, door de gebruiker bevestigde case (scan_id 5→6, 39→37, netto -2, 2× "Uit aanbod") is nu **vastgelegd als permanente regressietest** (`tests/test_regressie.py::test_deel_e_validatiecase`) en slaagt.

**Filterconsistentie-fix:** Marktdynamiek toont nu PRIMAIR de cijfers binnen dezelfde actieve filters als de Analyse zelf (bv. "9 → 7" voor Beschikbaar+Bestaande bouw), met de volledige, ongefilterde scanvergelijking (39 → 37) als kleine, apart gelabelde referentieregel eronder ("Volledige scan (ongefilterd, ter referentie - NIET dezelfde populatie als hierboven)"). De twee populaties zijn nooit meer in dezelfde primaire KPI-reeks vermengd. Live geverifieerd: gefilterd 11→9 (netto -2, "Uit aanbod: 2") vs. totaal 39→37 (netto -2) - beide kloppen onafhankelijk van elkaar, en zijn nu duidelijk als verschillende populaties gelabeld.

## 6. Makelaarspositie blijft prioriteit (DEEL F)

Ongewijzigd behouden (geen regressie): sectievolgorde (Kerncijfers → Makelaarspositie → Verdeling → Segmenten/Prijssegmenten → Marktdynamiek → Export voor beide modules), marktaandeelbalk, standaard marktleider bovenaan, "Onbekend" apart gemarkeerd en uitgesloten van "Aantal bekende makelaars" maar wel in de marktaandeel-noemer. Bevestigd via de regressietestsuite.

## 7. Automatische sluitcontroles (DEEL G)

Alle 10 gevraagde controles geïmplementeerd als herhaalbare test in `tests/test_regressie.py` (Woningen) resp. `tests/test_business_regressie.py` (Business, waar van toepassing), draaiend tegen de nieuwste echte scan. Zie ook DEEL 3 hierboven voor C8. **Resultaat: alle checks slagen voor alle 4 in DEEL L gevraagde filtercombinaties.**

## 8. Business-regressie (DEEL H)

`tests/test_business_regressie.py` (18 checks, allemaal geslaagd): Business-analyse (Alles/Huur/Koop), CSV-export, Dashboard, makelaarsprofiel (4 echte makelaars + Onbekend + fictief - **expliciet gecontroleerd op dezelfde raw/string-datatypefout als bij Woningen: niet aanwezig**), som(makelaarstabel)==KPI actief_aanbod, nieuwe `kpis.aantal_makelaars` aanwezig, berekende-jaarhuur-dekking nog intact, alle 3 scanner-bestanden compileren. Business is niet inhoudelijk gewijzigd deze ronde (geen historische analyse, geen geo-laag) - uitsluitend gecontroleerd op regressie.

## 9. Scanner-regressie (DEEL I)

`ancestor_card()` is de ENIGE gewijzigde functie in de kaart-/paginaverwerking. Bevestigd via `git diff` dat `area_guard()` (gebiedsherkenning/Vinkel-fallback-detectie), de paginalus, en het volledige stopmechanisme (`_stopcontrole.py`, `pause()`, `controleer_stop()`) dit keer NIET zijn aangeraakt - dat is precies dezelfde, in de vorige ronde goedgekeurde code. De live diagnostische scan tijdens het Kordaat-onderzoek (5 plaatsen, incl. Vinkel) bevestigde nogmaals dat Vinkel geen contaminatie/fallback veroorzaakt. Geen nieuwe live regressietest van het stopmechanisme nodig geacht (ongewijzigde code, al goedgekeurd in de vorige ronde).

## 10. UX/foutafhandeling (DEEL J)

Nieuwe centrale `@app.errorhandler(Exception)` in `app.py`: een onverwachte fout toont nu `web/templates/fout.html` ("Deze analyse kon niet worden opgebouwd. De fout is vastgelegd.") met status 500, in plaats van de interactieve Werkzeug-debugger. HTTP-fouten (404 e.d.) blijven ongewijzigd normaal werken. De volledige traceback wordt nog steeds via `app.logger.error(..., exc_info=exc)` naar de serverconsole/log geschreven - debugbaarheid tijdens ontwikkeling blijft dus behouden, alleen niet meer rechtstreeks zichtbaar voor de eindgebruiker. `app.run(...)` staat nu op `debug=False`. Getest: nette foutpagina + status 500 bij een kunstmatig geforceerde fout, GEEN ruwe traceback in de HTML, volledige traceback wél op de console, 404 ongewijzigd.

## Nieuwe/gewijzigde bestanden deze ronde
- `app.py` - kernwijzigingen (zie boven: ruwe_rijen, historische analyse, geo-laag, filterconsistente marktdynamiek, foutafhandeling, ancestor_card-fix is in de scanner, niet hier)
- `scanner/makelaarsmonitor_v41.py` - `ancestor_card()`/nieuwe `_unieke_detail_urls_in()` (de kernfix van deze ronde)
- `web/templates/resultaat.html` - peildatumselector, historisch-banner, plaats/gemeente-toggle, filterconsistente marktdynamiek
- `web/templates/fout.html` **(nieuw)** - nette foutpagina
- `tests/` **(nieuw)** - `test_regressie.py` (62 checks) + `test_business_regressie.py` (18 checks), persistente regressiefixtures (DEEL E/G/L)
- `docs/CLAUDE_STATUS.md`, `docs/BUGLIST.md` - deze update

Niet gewijzigd: databaseschema (beide databases), `historie/*.py`, Business-analyselogica/-templates, woningmodule buiten wat hierboven genoemd is.

## Database (expliciet bevestigd)
- **Geen schemawijziging** aan `data/makelaarsmonitor_historie.sqlite` of `data/funda_business_historie.sqlite`.
- **Geen productiedata gewijzigd** door dit werk: alle diagnostiek/tests waren read-only; de enige wijzigingen aan beide databases tijdens deze sessie kwamen van reguliere, echte scans die de gebruiker zelf via de webinterface heeft uitgevoerd (zichtbaar aan de nieuwe scan_id's/tijdstempels tijdens het werk).
- Scans/historie volledig intact - niets verwijderd, niets teruggezet.

## Git
`git status --short` (op het moment van opleveren van deze ronde):
```
 M app.py
 M docs/CLAUDE_STATUS.md
 M scanner/funda_business_scanner_v1.py
 M scanner/makelaarsmonitor_v41.py
 M web/static/css/app.css
 M web/templates/business_resultaat.html
 M web/templates/dashboard.html
 M web/templates/resultaat.html
 M web/templates/scan_status.html
?? docs/BUGLIST.md
?? scanner/_stopcontrole.py
?? tests/
?? web/templates/fout.html
?? web/templates/makelaar_profiel.html
```
**Geen commit, geen push** (zoals gevraagd).

## Aanbevolen volgende stap
1. **Nieuwe scan draaien** voor Nistelrode (of de volledige 5-plaatsen-selectie) via de webinterface, om de scanner-fix ECHT in de productiedatabase te krijgen (de huidige database bevat nog de oude, onvolledige cijfers).
2. Handmatig doorlopen: historische analyse (peildatumselector), plaats/gemeente-toggle, makelaarsprofiel (incl. Kordaat na de herscan), foutpagina (desgewenst een fout forceren om te zien dat de nette pagina verschijnt).
3. Daarna: gezamenlijke stabilisatie-/bugfixronde op basis van verzamelde bevindingen + bijgewerkte `docs/BUGLIST.md`.

---

## Vorige ronde (2026-09-08) - hieronder ongewijzigd bewaard

## Deze bouwronde
Brede ontwikkelronde bovenop de stabiele milestone `0b464ed750708c4c61e9f35df0ed4791e46f7a99` ("feat: improve business scan control and rent analysis"), volgens de "sneller bouwen"-werkwijze: onderdelen achter elkaar geïmplementeerd, alleen blokkerende problemen direct opgelost, niet-blokkerende bevindingen in `docs/BUGLIST.md`. **Geen commit/push in deze ronde** - dat gebeurt pas na de gezamenlijke integrale test die de gebruiker zelf uitvoert.

Herstelpunt (vorige stabiele milestone): `0b464ed750708c4c61e9f35df0ed4791e46f7a99`.

Productrichting die is aangehouden: Scanner verzamelt data → Analyse verklaart de actuele selectie → Dashboard toont ontwikkeling/vergelijking door de tijd → Makelaar Intelligence verklaart de positie van makelaars → Export bevat de volledige detaildata. Dezelfde info is bewust niet op vijf plekken gedupliceerd.

## Aanvulling: Makelaarspositie is de primaire analyse
Productbesluit tijdens dezelfde bouwronde: Makelaar Monitor is primair gericht op marktaandeel/marktpositie van makelaars; geografische verdeling is ondersteunend. Doorgevoerd:

- **Sectievolgorde aangepast** (beide modules): Makelaarspositie staat nu direct onder Kerncijfers, vóór Verdeling per plaats/Markt-plaatsverdeling. Woningen: 1 Kerncijfers → 2 Makelaarspositie → 3 Verdeling per plaats → 4 Prijssegmenten → 5 Marktdynamiek → 6 Export. Business: 1 Kerncijfers → 2 Makelaarspositie → 3 Markt-/plaatsverdeling → 4 Segmentanalyse → 5 Marktdynamiek → 6 Export. "Verdeling per plaats" blijft (ongewijzigd) verborgen bij één geselecteerde plaats.
- **Compacte horizontale marktaandeel-balk** toegevoegd in beide makelaarstabellen (pure CSS, geen nieuwe dependency) - exacte percentage blijft altijd naast de balk zichtbaar. Standaardsortering was al aflopend op Objecten (= marktleider bovenaan); ongewijzigd, nu expliciet in de subtekst benoemd.
- **"Onbekend is geen makelaar"**: nieuwe KPI "Aantal bekende makelaars" (beide modules, `kpis.aantal_makelaars`/`kpis.aantal_zonder_makelaar` - Business kreeg deze KPI's nu voor het eerst). De "Onbekend"-rij in de makelaarstabel is visueel gemarkeerd (cursief, grijze balk, tooltip) en telt nooit mee in "Aantal bekende makelaars" - de objecten tellen wél gewoon mee in de marktaandeel-noemer (totaal), zoals gevraagd.
- **Dashboard herprioriteerd**: 1 Aanbodontwikkeling → 2 Marktaandeel makelaars (nu vóór prijsontwikkeling) → 3 Prijsontwikkeling → 4 Overige marktinformatie. Marktaandeelontwikkeling wordt getoond in **procentpunt** (nooit procentuele groei), bv. "22,9% → 22,9%, 0,0 pp" - bij precies 1 beschikbare scan toont het Dashboard uitsluitend de huidige positie met een duidelijke melding dat ontwikkeling na de volgende vergelijkbare scan verschijnt.
- **Sluitcontroles uitgevoerd tegen echte, actuele data** (3 selecties, waaronder exact het scenario uit de oorspronkelijke bugmelding: Nistelrode/Vorstenbosch/Heesch/Heeswijk-Dinther/Vinkel): som(Objecten in makelaarstabel) == KPI Actief aanbod/aantal_objecten (incl. Onbekend) ✓; per-makelaar marktaandeel == objecten/totaal×100 ✓; som van alle marktaandelen (incl. Onbekend) ≈ 100% (100,2-100,3% door afronding) ✓; naamvariant-detectie (casefold-botsing tussen verschillende ruwe schrijfwijzen, bv. "Kordaat Makelaars" vs "Kordaat makelaars") uitgevoerd - **geen** variant gevonden in de huidige data (dus geen fuzzy-matching toegepast, conform de instructie om dit niet zonder bewijs te doen). 12/12 checks geslaagd.

**Live bevestiging van de DEEL A-scannerfix (Vinkel):** tijdens deze sessie heeft de gebruiker zelf onafhankelijk een echte scan uitgevoerd voor exact Nistelrode/Vorstenbosch/Heesch/Heeswijk-Dinther/Vinkel (scan_id 5, 08-09-2026 15:02). Resultaat in de database: Heesch 2, Heeswijk-Dinther 29, Nistelrode 6, Vorstenbosch 2, **Vinkel 0 (geen enkele rij)** - de scan is niet vastgelopen, geen contaminatie van andere plaatsen, en Vinkel is correct als leeg/overgeslagen resultaat verwerkt. Dit is een directe, live bevestiging van de root-cause-fix uit DEEL A, en lost daarmee BUGLIST.md #8 grotendeels op (alle 5 oorspronkelijk gemelde plaatsen zijn nu in één scan bevestigd zonder problemen doorlopen).

## Gewijzigde/nieuwe bestanden
- `app.py` - kernwijzigingen (zie hieronder)
- `scanner/makelaarsmonitor_v41.py` - robuustere gebiedsherkenning + stopmechanisme
- `scanner/funda_business_scanner_v1.py` - klein: nu importeert `ScanGestopt`/`controleer_stop`/`STOP_EXITCODE` uit de nieuwe gedeelde module i.p.v. eigen kopie (gedrag ongewijzigd)
- `scanner/_stopcontrole.py` **(nieuw)** - kleine gedeelde helper voor coöperatieve scan-stop
- `web/templates/business_resultaat.html` - volledig herstructureerd (DEEL D)
- `web/templates/resultaat.html` - volledig herstructureerd (DEEL I)
- `web/templates/scan_status.html` - "Scan afbreken"-knop + "gestopt"-status (Woningen)
- `web/templates/dashboard.html` - volledig herbouwd (was placeholder)
- `web/templates/makelaar_profiel.html` **(nieuw)**
- `web/static/css/app.css` - kleine, additieve klassen (dekking, detail-toggle, grafiek, dashboard-filters, profiel)
- `docs/BUGLIST.md` **(nieuw)**

Niet gewijzigd (bevestigd via `git diff --stat`): `historie/*.py`, `web/templates/index.html`, `web/templates/base.html`, `web/templates/business_scan_status.html`, `web/static/js/app.js`, databaseschema, `data/`, `output/`.

---

## DEEL A - Woningenscanner robuuster

### Root cause (live bevestigd, geen aanname)
Live onderzoek tegen de echte site (`funda.nl/zoeken/koop?selected_area=vinkel`, ook met alternatieve URL-encoderingen getest) toont: Funda herkent de slug "vinkel" niet als eigen zoekgebied en valt **deterministisch** stil terug op de algemene, landelijke `/zoeken/koop`-resultatenpagina (94 detail-links door het hele land, geen enkele relatie met Vinkel). Dit is **geen** "0 aanbod" - dat is een aparte, geldige uitkomst. De oude code (`area_guard()` faalt → blokkerende `pause()`/`input()`) liet de scan hier onbeperkt wachten op een ENTER-bevestiging die niemand zag, wat exact overeenkomt met de gemelde klacht ("Chrome kwam terecht op funda.nl/zoeken/koop zonder zichtbare plaatsselectie").

### Fix
- Twee situaties nu expliciet onderscheiden: (1) gebied niet herkend door Funda (URL verliest `selected_area`) → plaats overgeslagen met duidelijke waarschuwing, GEEN objecten van de fallback-pagina worden ooit aan die plaats toegekend; (2) gebied wél herkend, 0 detail-links gevonden → behandeld als geldig "0 aanbod"-resultaat, scan gaat gewoon door.
- Bij (1): één keer automatisch herladen (transiënte-glitch-vangnet), pas daarna overslaan - geen blokkerende `pause()`/`input()` meer voor dit scenario.
- Menscontrole/captcha (`needs_human()`) blijft wél afgehandeld, maar nu net als bij Business niet-blokkerend: peilt elke 2s, gaat automatisch verder zodra de controle in het zichtbare Chrome-venster is opgelost, begrensd op 1800s.
- De laatste blokkerende ENTER-fallback (bij een niet-automatisch-bevestigde pagina 1) is verwijderd; de bestaande paginalus detecteert een echt leeg resultaat toch al correct.
- `bezochte_pagina_urls`-set toegevoegd als extra vangnet tegen het opnieuw verwerken van dezelfde pagina-URL.
- Diagnostische logging per plaats uitgebreid: aangevraagde URL, uiteindelijke URL, gebiedsherkenning ja/nee, reden bij afwijzing.
- Coöperatief stopmechanisme toegevoegd (zie DEEL B) via de nieuwe gedeelde module `scanner/_stopcontrole.py`.
- Nieuwe `--output-map`-parameter (zelfde patroon als Business) zodat checkpoint/eindbestanden en het stopvlag-bestand op een voorspelbare plek staan i.p.v. impliciet op de cwd te vertrouwen.

### Test
Live geverifieerd voor Vinkel (root cause bevestigd) en Odiliapeel/Uden (normale, herkende gebieden - ongewijzigd correct gedrag). De overige oorspronkelijk gemelde plaatsen (Nistelrode, Vorstenbosch, Heesch, Heeswijk-Dinther) zijn **niet** stuk voor stuk apart live opnieuw doorlopen (zie BUGLIST.md #8) - de kernoorzaak en de fix zijn generiek (gebaseerd op het URL-signaal, niet op een plaatsnaam-specifieke aanname), dus de fix is naar verwachting op alle vijf van toepassing, maar dit verdient een bevestigende livetest door de gebruiker.

---

## DEEL B - Scan afbreken voor Woningen
Zelfde, bewezen patroon als Business, met een **kleine gedeelde helper** i.p.v. twee losse kopieën (zoals gevraagd): `_wacht_op_scan_proces(proces, state, state_lock)` in `app.py` wordt nu door zowel `_wacht_op_woningen_proces()` als `_wacht_op_business_proces()` aangeroepen; `_verwijder_stop_flag(pad)` en `_forceer_procesboom_stop(pid)` zijn generiek gemaakt. Nieuwe route `POST /scan/stop`, nieuwe status "gestopt" met bijbehorende UI in `scan_status.html` (knop is idempotent, tweede klik toont "Stop wordt uitgevoerd...", geen historie-import bij een afgebroken scan). SCAN_RUNNING_LOCK (gedeeld met Business) blijft ongewijzigd altijd via de bestaande `finally` vrijgegeven.

**Getest (mocks, geen echte processen):** route bestaat, idempotente dubbele klik, coöperatieve stop-levenscyclus via `_wacht_op_woningen_proces`, lock niet vastgelopen na een stop, Business-stopmechanisme blijft intact na de generalisatie (9/9 checks geslaagd). Scanner-niveau: `pause()` in de woningenscanner is bevestigd onderbreekbaar via het stopvlag-bestand (`ScanGestopt`), net als bij Business.

---

## DEEL C - Analysepagina's opgeschoond
De volledige objectentabel staat niet meer standaard open op de primaire analysepagina (Woningen + Business): verplaatst naar een dichtgeklapt `<details>`-blok onderaan, onder de nieuwe sectie "Export", met duidelijke tekst dat dit drill-down is en normaal niet nodig. **Niets verwijderd** uit de analysemotor of database - `bouw_analyseresultaat()`/`bouw_business_analyseresultaat()` berekenen `rijen` nog steeds volledig, de tabel-HTML bestaat nog gewoon (alleen ingeklapt), en CSV-export bevat nog steeds alle velden (zie DEEL Q).

---

## DEEL D - Nieuwe structuur Business-analyse
`business_resultaat.html` volgt nu: **1. Kerncijfers** (bestaande KPI's behouden: actief aanbod, kantoor, bedrijfsruimte, dual-listed, m², koop-/huuraanbod, koopvraagwaarde, huur €/m²/jaar, berekende jaarhuur+dekking) → **2. Marktverdeling per plaats** (alleen zichtbaar bij >1 plaats) → **3. Makelaars** → **4. Segmentanalyse Bedrijfsruimte** → **5. Marktdynamiek** (mutaties + netto verandering + dagen in monitor) → **6. Export** (CSV-knop + ingeklapte objectentabel).

---

## DEEL E - Makelaarsanalyse Bedrijfsmatig uitgebreid
`bouw_business_makelaarstabel()` toont nu per makelaar: Objecten, Marktaandeel objecten (noemer = "Actief aanbod" van de huidige selectie, expliciet vermeld), Aangeboden m², **Marktaandeel m²** (nieuw), Huur, Koop, Koopvraagwaarde **+ dekking** (aantal objecten waarop het bedrag is gebaseerd), **Aandeel koopvraagwaarde** (nieuw), Berekende jaarhuur **+ dekking**, **Aandeel jaarhuur** (nieuw). Alle aandelen zijn uitsluitend gebaseerd op bekende/betrouwbare waarden (geen schijnprecisie) en tonen "-" bij een lege noemer. Makelaarsnaam is nu klikbaar naar het nieuwe makelaarsprofiel (DEEL N).

---

## DEEL F - Segmentanalyse Bedrijfsmatig
Nieuwe sectie "Segmentanalyse Bedrijfsruimte": vaste grootteklassen `< 250 m²`, `250-500 m²`, `500-1.000 m²`, `1.000-2.500 m²`, `> 2.500 m²` (ondergrens inclusief, bovengrens exclusief - elk object valt in exact één segment, geverifieerd via `_segment_van()`). Per segment: aantal, m², huurobjecten + gem./mediaan €/m²/jaar (uitsluitend echte per_m²/jaar-tarieven, nooit maandhuur), koopobjecten + gem./mediaan €/m² (uitsluitend bekende koopprijs + oppervlakte > 0). Generiek opgezet (`bereken_business_segmentanalyse(rijen, categorie, grenzen)`) zodat Kantoor later eigen grenzen kan krijgen zonder herbouw (nog niet in de UI - zie BUGLIST.md #2).

---

## DEEL G - Analyse per plaats (Bedrijfsmatig)
Nieuwe sectie "Marktverdeling per plaats" (alleen zichtbaar bij meerdere geselecteerde plaatsen): actief aanbod, m², huur, koop, gem./mediaan huur €/m²/jaar, gem./mediaan koop €/m², aantal actieve makelaars - per plaats, geen objectenlijsten. Live getest met een echte 4-plaatsen-selectie (Schaijk/Uden/Veghel/Zeeland).

---

## DEEL H - Marktdynamiek
Zowel Woningen als Business tonen nu, naast de bestaande mutatietelling (nieuw/uit aanbod/gewijzigd/ongewijzigd): een expliciet "Vorige scan → Huidige scan → Netto verandering"-blok (bv. 35 → 35, netto 0) en gemiddelde/mediaan "dagen in monitor" over de huidige selectie. Semantische waarschuwing behouden: "uit aanbod" ≠ verkocht/verhuurd. Een correctheidsfout tijdens het bouwen zelf gevonden en direct gefixt: het "huidige scan"-cijfer in dit blok wees aanvankelijk naar het (mogelijk status-/transactietype-)gefilterde KPI-aantal i.p.v. hetzelfde ongefilterde totaal als "netto verandering" - opgelost door `scaninfo.aantal_objecten` overal consistent op het volledige, ongefilterde snapshot te baseren.

---

## DEEL I - Woningenanalyse, zelfde filosofie
`resultaat.html` volgt nu dezelfde 6-delige structuur: **1. Kerncijfers** → **2. Verdeling per plaats** (bestond al, ongewijzigde logica) → **3. Makelaarspositie** (bestond al, makelaarsnaam nu klikbaar) → **4. Prijssegmenten** (nieuw: `< €300k`, `€300-400k`, `€400-500k`, `€500-750k`, `€750k-1M`, `> €1M`, met aantal/aandeel/gem./mediaan vraagprijs/€m²) → **5. Marktdynamiek** (nieuw: netto verandering + dagen in monitor, bestaande mutatielogica behouden) → **6. Export**.

---

## DEEL J/K/L/M - Dashboard nu functioneel
Volledig herbouwd (was placeholder). Bovenaan een marktkeuze (Woningen/Bedrijfsmatig, aparte trends - niet gecombineerd, zoals toegestaan). Een filter kiest de EXACTE scanreeks (gebied, voor Business ook categorieën) - verschillende scanregio's worden nooit stilzwijgend vergeleken; de gekozen scanreeks staat altijd zichtbaar in de selector.

**Data (één query voor de scans + één query voor alle bijbehorende snapshotrijen per reeks - geen N+1, zie DEEL R):**
- Woningen: totaal/beschikbaar aanbod, gem./mediaan vraagprijs, gem./mediaan €/m², nieuw/uit aanbod per scan-paar, marktaandeel topmakelaars.
- Business: actief aanbod, aangeboden m², gem./mediaan huur €/m²/jaar, totale koopvraagwaarde, berekende jaarhuur, nieuw/uit aanbod, marktaandeel topmakelaars.

**Grafieken (DEEL M):** eenvoudige, dependency-vrije server-gerenderde SVG-lijngrafieken (`bouw_svg_lijngrafiek()` in `app.py`, geen nieuwe framework/CDN-afhankelijkheid) voor prioriteit 1 (aanbodontwikkeling) en 2 (prijsontwikkeling), met as-labels, eenheden en een native SVG `<title>`-tooltip per punt. Marktaandeel topmakelaars (prioriteit 3) is een compacte tabel i.p.v. een grafiek (zie BUGLIST.md #3). Bij <2 scans in een reeks toont het Dashboard expliciet dat een trend nog niet mogelijk is (i.p.v. een misleidende lege/vlakke grafiek).

Live geverifieerd met de echte, huidige historie (Business: 3 scans voor gebied "Uden" - inmiddels 4 na een losstaande scan van de gebruiker tijdens deze sessie - tonen een correcte trend, grafiek en makelaars-aandeel-tabel; Woningen: gebied "Nistelrode | Uden | Volkel" heeft 2 vergelijkbare scans).

---

## DEEL N - Makelaarsprofiel (basisversie)
Nieuwe route `/makelaar/<woningen|bedrijfsmatig>/<naam>` + `web/templates/makelaar_profiel.html`, klikbaar vanuit beide makelaarstabellen. Berekend binnen de filterselectie van de analysepagina van herkomst (querystring wordt doorgegeven), zodat marktaandeel een duidelijke noemer heeft. Toont per module de gevraagde velden (naam, actief aanbod, marktaandeel, m², vraagwaarde/koopvraagwaarde+jaarhuur, gem./mediaan prijs, plaatsen, segmenten). **Nog geen** ontwikkeling door de tijd (zie BUGLIST.md #1) en **geen** externe KvK/Google/reviewdata (bewust, conform DEEL O).

---

## DEEL O - Roadmap: Toekomstige Makelaars Intelligence
_Niet geïmplementeerd in deze ronde, behalve het eenvoudige interne makelaarsprofiel uit DEEL N hierboven._

1. **Makelaars vergelijken** - twee of meer makelaarsprofielen naast elkaar (marktaandeel, prijsniveau, segmentpositie).
2. **Makelaarsprofiel uitbreiden** - ontwikkeling door de tijd (zie BUGLIST.md #1), langere geschiedenis, meer segmentdetail.
3. **Kaart/geografische analyse** - zie DEEL P hieronder.
4. **CBS-context** - openbare regionale marktcijfers naast onze eigen data, uitsluitend ter context (nooit vermengd met onze feitelijke metingen).
5. **KvK/bedrijfsinformatie** - bedrijfsprofiel/vestigingsgegevens per makelaar.
6. **Reviews/openbare reputatiedata** - géén samengestelde score; hooguit losse, herkenbaar-gebronde signalen.
7. **Regionale benchmarks** - een makelaar/plaats vergelijken tegen een breder regionaal gemiddelde.

Expliciete richtlijn (herhaald vanuit de opdracht): **geen arbitraire "MakelaarScore 0-100"** bouwen. De kracht blijft feitelijke marktdata: marktaandeel objecten/m², vraagwaarde, huurwaarde, prijsniveau, segmentpositie, plaatspositie, ontwikkeling door de tijd.

## DEEL P - Roadmap: Kaart (nog niet bouwen)
Vastgelegd als volgende grote feature, bewust NIET gebouwd in deze ronde: interactieve kaart voor objecten/€m²/makelaar/dagen-in-monitor/marktconcentraties. Bedrijfsmatig: bedrijventerreinen/geografische clusters. Woningen: plaats/wijk/postcodeverdeling. De huidige database bevat geen betrouwbare geocoördinaten per object - implementatie zou een apart geocoding-traject vereisen, wat expliciet buiten deze ronde valt. Pas oppakken als geocoördinaten al betrouwbaar aanwezig zijn óf als een geocoding-project apart wordt goedgekeurd.

---

## DEEL Q - Export blijft de detaillaag
- Woningen: bestaande `/export/csv` ongewijzigd, bevat nog steeds alle geconfigureerde kolommen (spot-check: 15 kolommen, 109 rijen voor Uden/Beschikbaar/Bestaande bouw).
- Business: **nieuwe** route `/business/export/csv` toegevoegd (bestond nog niet) - 21 kolommen inclusief alle brongegevens (koopprijs, huurprijs+eenheid, oppervlakte, berekende huur, makelaar, waarnemingsdata, dagen in monitor). Zelfde pipeline (`bouw_business_analyseresultaat`) als de analysepagina, dus gegarandeerd dezelfde filters/objecten.
- Geen Google Sheets-functionaliteit toegevoegd.

## DEEL R - Performance
Geen N+1-patronen geïntroduceerd: alle nieuwe aggregaties (segmentanalyse, plaatsvergelijking, makelaarsprofiel) werken op reeds opgehaalde Python-rijen, zonder extra queries. De nieuwe dashboard-trendfuncties (`haal_woningen_trend`/`haal_business_trend`) doen exact twee queries per scanreeks (één voor de scans, één voor ALLE bijbehorende snapshotrijen via een enkele `IN (...)`-clause), ongeacht het aantal scans in die reeks - aggregatie gebeurt daarna in Python.

## DEEL S - Buglist
Zie `docs/BUGLIST.md` voor alle 9 genoteerde, niet-blokkerende punten (o.a. Kantoor-segmentanalyse nog niet in UI, makelaarsprofiel nog zonder tijdreeks, makelaarstabel-kolombreedtes nog niet visueel gecontroleerd, Dashboard Woningen nog zonder bouwcategorie-filter).

## DEEL T - Veiligheid data (nageleefd)
Geen databaseschema-wijzigingen, geen bestaande historie aangetast, geen database verwijderd/gereset, `data/` en `output/` blijven buiten Git (`.gitignore` ongewijzigd, gecontroleerd), geen enkele test heeft geschreven naar de productiedatabases (alle databasetoegang tijdens deze ronde was read-only; de enige wijziging aan beide databases tijdens deze sessie kwam van een losstaande, echte scan die de gebruiker zelf via de webinterface heeft uitgevoerd).

---

## Technische regressiecontrole (DEEL U/V)
- `py -m py_compile` op alle gewijzigde/nieuwe Python-bestanden (`app.py`, beide scanners, `_stopcontrole.py`) - geslaagd.
- CLI's van beide scanners (`--help`) nog intact, inclusief nieuwe `--output-map`-optie bij Woningen.
- Alle routes gerenderd via de Flask-testclient zonder traceback: `/`, `/scan/status`, `/business/scan/status`, `/dashboard` (met en zonder `markt`-parameter), `/analyse`, `/business/analyse`, `/export/csv`, `/business/export/csv`, `/makelaar/woningen/<naam>`, `/makelaar/bedrijfsmatig/<naam>` (inclusief onbekende/niet-bestaande makelaarsnamen - geen crash, nette "0 objecten"-weergave).
- Multi-plaats-secties (Verdeling per plaats / Marktverdeling per plaats) live geverifieerd met echte 8-plaatsen (Woningen) en 4-plaatsen (Business) selecties.
- `POST /scan/start` en `POST /business/scan/start` zonder plaats: redirecten correct, starten GEEN scan, laten de state op "idle".
- Stopmechanisme Woningen + Business: 9/9 gemockte checks geslaagd (zie DEEL B), inclusief bevestiging dat de generalisatie het bestaande, goedgekeurde Business-stopgedrag niet heeft aangetast.
- Databases: uitsluitend read-only benaderd; bestandsgroottes/tijdstempels bevestigen dat geen enkele test heeft geschreven (de enige wijziging kwam van een losstaande echte scan van de gebruiker zelf).
- Eén correctheidsfout tijdens het bouwen zelf gevonden en direct gefixt (zie DEEL H - "huidige scan"-cijfer in marktdynamiek).

## Wat moet handmatig getest worden
1. **Woningenscanner-fix live**: een scan starten voor Nistelrode, Vorstenbosch, Heesch, Heeswijk-Dinther, Vinkel en bevestigen dat (a) Vinkel netjes wordt overgeslagen met een duidelijke waarschuwing i.p.v. vast te lopen, (b) de overige plaatsen normaal doorlopen, (c) er nergens objecten van de verkeerde plaats in het resultaat terechtkomen.
2. **Scan afbreken - Woningen**: een scan starten en tijdens het lopen op "Scan afbreken" klikken; controleren dat Chrome netjes sluit, de status "Scan afgebroken" toont, er geen historie-import plaatsvindt, en direct daarna een nieuwe Woningen- én Business-scan gewoon kan starten (lock niet vastgelopen).
3. **Analysepagina's**: controleren dat de nieuwe indeling (Kerncijfers/Verdeling/Makelaars/Segmenten/Marktdynamiek/Export) prettig leesbaar is, dat de ingeklapte objectentabel werkt, en dat de CSV-export-knoppen (beide modules) een bruikbaar bestand opleveren.
4. **Makelaarstabel-kolombreedtes** (Business, 11 kolommen) visueel controleren op een normale desktopbreedte (zie BUGLIST.md #5).
5. **Dashboard**: beide markten doorlopen, scanreeks wisselen, grafieken en makelaars-aandeel-tabel controleren op leesbaarheid.
6. **Makelaarsprofiel**: vanuit beide makelaarstabellen doorklikken en de cijfers herkennen t.o.v. de analysepagina.

## Wat is bewust doorgeschoven (zie ook BUGLIST.md)
Kantoor-segmentanalyse in de UI, makelaarsprofiel-ontwikkeling door de tijd, makelaars-marktaandeel als grafiek i.p.v. tabel, bouwcategorie-filter op het Woningen-dashboard, makelaars vergelijken, kaartfunctionaliteit, CBS/KvK/reviewdata, MakelaarScore (expliciet NIET bouwen).

## Wat is nog beperkt zichtbaar door weinig historische data
Dashboard-trends zijn dun voor regio's met maar 1-2 scans (de meeste huidige scanreeksen); dit lost zichzelf op naarmate er meer scans worden uitgevoerd. Marktaandeel-topmakelaars-trend is beperkt betekenisvol bij een korte reeks.

## Volgende stap
Geen commit/push (zoals gevraagd). De gebruiker doorloopt nu zelf de applicatie en verzamelt bevindingen; daarna volgt een aparte, gezamenlijke stabilisatie-/bugfixronde op basis van deze bevindingen + `docs/BUGLIST.md`.

CLAUDE_STATUS.md bijgewerkt.
