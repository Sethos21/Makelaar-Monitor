# Makelaar Monitor — status

_Laatst bijgewerkt: 2026-09-08_

## Uitgevoerde opdracht
Gerichte uitbreiding van de WONINGEN-analysepagina (`/analyse`) zodat deze qua informatiegehalte en gebruikservaring aansluit op de bestaande Bedrijfsmatig-analyse: uitgebreide KPI's, een makelaarsanalyse-tabel, een verdeling-per-plaats-tabel, een mutatieblok (nieuw/uit aanbod, prijs-/status-/makelaar-/oppervlaktewijzigingen) en een inhoudelijk sterkere, klikbare objectentabel. Daarnaast een generieke fix van de bestaande JS-sorteerfunctie voor Nederlandse getalnotatie (gold al voor Business, nu ook correct voor Woningen). **Nog niet gecommit/gepusht** — wacht op handmatige test door de gebruiker, zoals gevraagd. Herstelpunt blijft `71b287e3708107e84c97728d0b1fd4db9d3b0452`.

## Gewijzigde bestanden
- `app.py`:
  - `KOLOMMEN_STANDAARD` uitgebreid van 6 naar 15 kolommen (adres, plaats, status, bouwcategorie, vraagprijs, woonoppervlakte, perceeloppervlakte, slaapkamers, energielabel, prijs_per_m2, makelaar, eerste_waarneming, laatste_waarneming, dagen_in_monitor, funda_url) — dit zijn ALLEMAAL kolommen die al in de bestaande `KOLOMMEN`-dict/tabelinfrastructuur bestonden; alleen de standaard-selectie is verbreed. De "Gewenste gegevens"-checkboxen op het hoofdscherm blijven volledig functioneel voor wie een smallere selectie wil.
  - `verrijk_rij()`: `woonoppervlakte`/`perceeloppervlakte` krijgen nu dezelfde opmaak-behandeling als `vraagprijs` al had (bv. "196 m²" i.p.v. de rauwe Python-`float`-weergave "196.0").
  - `bereken_kpis()` fors uitgebreid (backwards compatible: alle bestaande sleutels blijven bestaan) met status-subtellingen (Beschikbaar/Onder bod/Verkocht onder voorbehoud), mediaan vraagprijs, gemiddelde/mediaan €/m², totaal bekende woonoppervlakte.
  - Nieuw: `bouw_makelaarstabel()`, `bouw_plaatsverdeling()`, `haal_vorige_scan_id()`, `haal_scan_snapshot()`, `woning_mutatie_naam()`, `woning_mutatie_details()`, `woning_bouw_mutaties()` — allemaal naar hetzelfde patroon als de al bestaande Business-equivalenten, maar met woningen-specifieke velden (vraagprijs/status/makelaar/woonoppervlakte, funda_url als sleutel i.p.v. funda_object_id).
  - `bouw_analyseresultaat()`: bouwt nu ook `makelaarstabel`, `plaatsverdeling`, `mutatie_aantallen`, `mutatie_rijen`, `vorige_scanmoment_weergave` op, allemaal uit dezelfde SQLite-query-pipeline (geen losse databronnen, geen browserstate).
  - `analyse()`-route geeft deze nieuwe velden door aan het template.
- `web/templates/resultaat.html` — grondig uitgebreid: 11 KPI-kaarten, "Aanbod per makelaar"-tabel (compacte stijl, hergebruikt de bestaande `.data-table-compact`-CSS-klasse van Business), "Verdeling per plaats"-tabel (alleen zichtbaar bij >1 plaats in de selectie), mutatieblok (kpi-grid-3 met 7 categorieën + detailtabel), objectentabel met klikbaar Adres (naar Funda) en de verbrede standaardkolommenset. Actiebalk samengevoegd tot één rij: Terug naar filters / Nieuwe scan uitvoeren / Dashboard / Export CSV. De disabled "Export Google Sheets"- en "Toevoegen aan Dashboard"-knoppen zijn verwijderd (expliciet gevraagd: CSV is de enige exportfunctie die nu nodig is, geen verwarrende dode knoppen).
- `web/static/js/app.js` — `parseGetal()` generiek robuust gemaakt voor Nederlandse getalnotatie (duizendtal-punt correct verwijderd, komma als decimaalteken herkend, niet-cijfertekens zoals "m²"/"dagen" genegeerd). Dit is dezelfde functie die door zowel de woningen- als de Business-objectentabel wordt gebruikt (`sorteerTabel()`/`initSorteerbareTabel()`, ongewijzigd) — dus de al langer bekende Business-beperking (duizendtal-punt verkeerd als decimaalteken gelezen) is hiermee voor **beide** modules verholpen, zonder de rest van de sorteerlogica aan te raken.
- `docs/CLAUDE_STATUS.md` — dit overdrachtsbestand.

Niet gewijzigd: `scanner/makelaarsmonitor_v41.py`, `scanner/funda_business_scanner_v1.py`, `historie/makelaarsmonitor_historie_v10.py`, `historie/funda_business_historie_v1.py` (bestandstijdstempels geverifieerd ongewijzigd), `web/templates/business_resultaat.html`/`business_scan_status.html`/`index.html`/`base.html`/`scan_status.html`, `web/static/css/app.css` (de gebruikte klassen `.kpi-card.kerncijfer`, `.kpi-grid-3`, `.data-table-compact` bestonden al van de vorige UX-harmonisatieronde en zijn hergebruikt, niet opnieuw gewijzigd). Geen databaseschema-wijziging — alle nieuwe functies zijn pure `SELECT`-queries op de al bestaande `scans`/`snapshots`-tabellen.

## Nieuwe woning-KPI's (sectie 2)
| KPI | Berekening |
|---|---|
| Actief aanbod | Aantal objecten in de huidige selectie (na plaats/status/bouwcategorie-filter) |
| Beschikbaar / Onder bod / Verkocht onder voorbehoud | Subtelling per status BINNEN de huidige selectie (net als bij Business: filtert de gebruiker al op één status, dan tellen de andere logischerwijs 0) |
| Totale bekende vraagwaarde | Som van `vraagprijs` over de selectie (visueel gemarkeerd als "kerncijfer", zelfde stijl als de Business-hoofdwaarden) |
| Gemiddelde / mediaan vraagprijs | Over objecten met bekende `vraagprijs` |
| Gemiddelde / mediaan prijs per m² | `vraagprijs / woonoppervlakte` per object, alleen waar beide bekend en > 0 zijn |
| Totaal bekende woonoppervlakte | Som van `woonoppervlakte` over objecten waar dat bekend is |
| Aantal makelaars | Aantal unieke, niet-lege `makelaar`-waarden in de selectie |

Geen enkele waarde wordt verzonnen: ontbrekende data resulteert in "-", nooit in een aanname.

## Bouwcategorie (sectie 3)
Ongewijzigd: `bouwcategorie` komt rechtstreeks uit de database (al bepaald door de bestaande historie-tool volgens de daar al vastgelegde regel — geen nieuwe classificatielogica toegevoegd). Het bestaande filter (`bouwcategorie IN (...)` in `haal_snapshotrijen()`) filtert de rijen al vóórdat KPI's/makelaarstabel/plaatsverdeling worden berekend, dus "Bestaande bouw" bevat gegarandeerd geen nieuwbouw-objecten in de makelaarsvergelijking (expliciet getest). De actieve bouwcategorie staat zichtbaar in de bestaande `.analyse-context`-balk.

## Makelaarsanalyse (sectie 4)
Kolommen: Makelaar, Objecten, Marktaandeel, Totale vraagwaarde, Gem. vraagprijs, Mediaan vraagprijs, Gem. €/m², Woonoppervlak. Marktaandeel = `aantal objecten van makelaar / totaal objecten in de HUIDIGE selectie × 100` (dezelfde noemer-logica als bij Business). Compacte tabelstijl (`data-table-compact` + `colgroup`, geen horizontaal scrollen op normale desktopbreedte), sortering standaard op aantal objecten aflopend.

## Mutatielogica (sectie 5/6)
- Vergelijkt de VOLLEDIGE, ongefilterde snapshot van de huidige scan met de meest recente EERDERE scan die EXACT hetzelfde `gebied` heeft (het gebied van de scan zelf zoals opgeslagen door de historie-tool — niet de eventueel smallere plaats-filter die de gebruiker net heeft gekozen). Dit is bewust hetzelfde principe als bij Business: een objectieve scanvergelijking, geen weergavefilter.
- Categorieën: Nieuw aanbod, Uit aanbod, Vraagprijs gewijzigd, Status gewijzigd, Makelaar gewijzigd, Oppervlakte gewijzigd, Ongewijzigd (kan gecombineerd voorkomen, bv. "Vraagprijs gewijzigd + Status gewijzigd").
- "Uit aanbod" betekent uitsluitend: object niet meer aangetroffen in de volgende scan. **Nooit** automatisch geïnterpreteerd als verkocht/verhuurd/transactie afgerond — expliciet in de UI-tekst benoemd en in tests geverifieerd.
- Detailtabel toont per gewijzigd object: Adres, Plaats, Mutatie, Wijziging (bv. "Vraagprijs: € 625.000 → € 599.000", "Status: Beschikbaar → Onder bod"), Makelaar, Funda-link. Oude/nieuwe waarden worden alleen getoond wanneer beide kanten van de vergelijking daadwerkelijk bestaan (dus nooit bij Nieuw/Uit aanbod).
- Als er geen vorige vergelijkbare scan bestaat (nulmeting voor dat exacte gebied) verschijnt het hele mutatieblok simpelweg niet — geen misleidende "0 mutaties"-melding.

## Verdeling per plaats (sectie 7)
Compacte tabel (Plaats, Aantal, Beschikbaar, Onder bod, VOV, Totale vraagwaarde, Gem. €/m²) — wordt in het template alleen getoond als de huidige selectie objecten uit **meer dan één** plaats bevat; bij één plaats vervalt de tabel (visueel netter, zoals gevraagd).

## Objectentabel (sectie 8)
Adres is nu klikbaar naar de opgeslagen Funda-URL (zelfde patroon als Business), onafhankelijk van of "Funda URL" zelf als aparte kolom is geselecteerd. Dagen in monitor hergebruikt de al bestaande `haal_geschiedenis()`/`verrijk_rij()`-logica (Eerste_waarneming/Laatste_waarneming over alle scans heen) — geen nieuwe berekening nodig. De bestaande, flexibele kolommenkiezer op het hoofdscherm is intact gebleven (bewust geen refactor naar een vast Business-achtig kolomschema, om de bestaande "Gewenste gegevens"-functionaliteit niet te breken); de standaardweergave toont nu wel alle in de opdracht gevraagde kolommen.

## Sortering (sectie 9)
`parseGetal()` in `app.js` is generiek herschreven: duizendtal-punten worden verwijderd, een komma wordt als decimaalteken herkend, niet-cijfertekens (bv. "m²", "dagen") worden genegeerd, negatieve waarden blijven werken. Dit is dezelfde, gedeelde functie voor Woningen én Business (via `initSorteerbareTabel()`/`sorteerTabel()`, beide ongewijzigd) — dus de eerder bekende Business-beperking is nu ook voor Business zelf verholpen, niet alleen "niet overgenomen" voor Woningen. `parseBedrag()` (voor €-kolommen) was al correct (strip alle niet-cijfers) en is niet aangepast.

## Actiebalk / export (sectie 10/11)
Actiebalk op de woninganalyse: Terug naar filters (met behoud van filters via queryparameters) / Nieuwe scan uitvoeren (mini-formulier, post naar bestaande `/scan/start`) / Dashboard / Export CSV. Geen Google Sheets-knop. De disabled "Toevoegen aan Dashboard"-knop is verwijderd (was alleen verwarrend, geen functie). CSV-export (`/export/csv`) gebruikt dezelfde `bouw_analyseresultaat()`-pipeline en dus gegarandeerd dezelfde filters en dezelfde (nu bredere) standaardkolommen als de analysepagina zelf — getest: exact evenveel rijen in de CSV als op de pagina voor identieke filters.

## Analyse uit SQLite (sectie 12)
Geen wijziging aan het bestaande principe: alles wordt bij elke request opnieuw met `SELECT`-queries uit `data/makelaarsmonitor_historie.sqlite` opgebouwd (read-only connectie, `mode=ro`). Geen nieuwe databronnen, geen client-side cache, geen losse tijdelijke CSV gebruikt. Bewust géén generieke woningen/Business-superhelper gebouwd (zou een grotere refactor betekenen voor beperkte winst) — de nieuwe woningen-functies zijn naar hetzelfde patroon geschreven als hun Business-tegenhangers, maar blijven bewust apart (stabiliteit boven architecturale elegantie, zoals gevraagd).

## Testresultaten
Alle tests via `flask.test_client()` + directe functieaanroepen tegen de **echte, ongewijzigde** database (geen nieuwe live scan), plus gerichte unit-tests met synthetische mutatiedata (omdat de meest recente echte scan toevallig een nulmeting is voor haar exacte regiocombinatie — zie hieronder). In totaal 45+ checks, 0 gefaald na correctie van twee te brede testscript-aannames (bevestigd als testartefacten, geen app-bugs):
- Woning-regressie: `/`, `/dashboard`, `/scan/start`-validatie, `/scan/status`, `/analyse`, `/export/csv` — allemaal status 200, geen traceback.
- Analysepagina: KPI's, makelaarstabel, objectentabel renderen; geen Google Sheets-/dode-Dashboard-knop meer; Export CSV-knop aanwezig.
- Filters blijven behouden: back-link bevat exact `plaats`/`status`/`bouwcategorie` van de huidige weergave.
- CSV-export: exact evenveel objecten als de analysepagina voor identieke filters; header bevat de gevraagde velden.
- Bouwcategorie-filter: "Bestaande bouw"-selectie bevat aantoonbaar geen enkele rij met `bouwcategorie == "Nieuwbouw"`.
- Objecttabel: Adres is een werkende link naar de echte Funda-URL.
- Mutatielogica (synthetische data, 4 objecten: 1 nieuw, 1 uit aanbod, 1 prijswijziging, 1 statuswijziging): aantallen kloppen exact, detailregels tonen correct "€ 625.000 → € 599.000" en "Beschikbaar → Onder bod", "Uit aanbod"-object heeft geen (want onmogelijke) oud/nieuw-details en de mutatietekst bevat nergens "verkocht"/"verhuurd"/"afgerond". Ook het template zelf gerenderd met deze data: geen crash, toont de juiste teksten.
- Business-baseline (regressie, ongewijzigd): Actief aanbod 35, Kantoor 18, Bedrijfsruimte 18, Dual-listed 3, Huuraanbod 28, Koopaanbod 10, koopvraagprijs € 19.227.000, exact 1 mutatie (Oostwijk 1), 34 ongewijzigd — allemaal nog exact zoals opgegeven.
- Databasebestanden (hoofdbestand, exacte bytegrootte): identiek vóór en na alle tests — geen enkele schrijfactie.
- `py -m py_compile app.py` geslaagd.

## Actuele sanity-check cijfers (sectie 15, echte database, alle statussen + alle bouwcategorieën, laatste scan)
- Regio's van de laatste scan: Boekel, Heesch, Odiliapeel, Schaijk, Uden, Veghel, Volkel, Zeeland — scanmoment 08-09-2026 08:59.
- **Totaal objecten: 554** (Beschikbaar 409, Onder bod 14, Verkocht onder voorbehoud 131).
- Totale bekende vraagwaarde: € 316.128.987.
- Gemiddelde vraagprijs: € 570.630 — mediaan: € 495.000.
- Gemiddelde prijs per m²: € 4.538 — mediaan: € 4.367.
- Totaal bekende woonoppervlakte: 86.595 m² — 68 unieke makelaars.
- Top 5 makelaars op objectaantal: Meerdere makelaars (129, 23,3%), Van der Krabben Makelaardij Uden (58, 10,5%), Bernheze Makelaars (38, 6,9%), Van de Ven Garantiemakelaars (32, 5,8%), Meierijstad Makelaardij (25, 4,5%).
- Mutaties: **geen** — deze scan (8-regio-combinatie) is een nulmeting; er bestaat nog geen eerdere scan met exact diezelfde 8 regio's om mee te vergelijken. Dit is correct gedrag, geen fout (het mutatieblok verschijnt daarom terecht niet op de pagina voor deze selectie).

## Bekende beperkingen
- Het mutatieblok verschijnt alleen wanneer er een eerdere scan bestaat met EXACT dezelfde regiocombinatie als de huidige (meest recente) scan. De huidige laatste scan (8 regio's) heeft die nog niet — dit is inherent aan het bestaande "altijd de allerlaatste scan tonen"-principe van de woningenmodule (ongewijzigd) en geen gebrek in de nieuwe mutatielogica zelf (die is apart met synthetische data geverifieerd correct).
- De objectentabel blijft de bestaande, door de gebruiker aanpasbare kolommenselectie gebruiken (nu met een bredere standaardset) in plaats van een vast Business-achtig kolomschema — een bewuste keuze om de bestaande flexibiliteit niet te verliezen, zoals gevraagd ("geen grote refactor").
- Geen `data-table-compact`-layout op de hoofdobjectentabel zelf (wél op de nieuwe makelaars-/plaatsverdelingstabellen): door de variabele, door de gebruiker gekozen kolomaantal is een vaste `colgroup`-breedteverdeling niet praktisch zonder extra logica; de bestaande `overflow-x:auto`-fallback blijft hier het vangnet, net als vóór deze wijziging.

## Aanbevolen volgende stap
Handmatig testen zoals gevraagd: filters instellen, "Analyse huidige database" bekijken (KPI's/makelaarstabel/objectentabel), eventueel een regiocombinatie kiezen die al wél een eerdere vergelijkbare scan heeft om het mutatieblok in het echt te zien, en de CSV-export controleren. Pas na akkoord: committen/pushen (nadrukkelijk niet in deze ronde gedaan).
