# Bugist / bekende beperkingen - bouwronde "marktanalyse + dashboard + makelaars intelligence"

_Vastgelegd tijdens de brede ontwikkelronde van 2026-09-08 (zie docs/CLAUDE_STATUS.md voor de volledige samenvatting). Geen van onderstaande punten is blokkerend voor verder gebruik - dit is de invoerlijst voor de aparte stabilisatie-/bugfixronde._

Per item: module, omschrijving, reproduceerbaar, ernst, vermoedelijke oorzaak, nog op te lossen.

---

## Update 2026-09-10 - gerichte uitbreiding: Marktintensiteit + makelaarsprofiel-context + Business-referentiedata + hernieuwd Funda-linkonderzoek

### OPGELOST/NIEUW: Marktintensiteit toegevoegd aan Verdeling per plaats/gemeente (Woningen)
- **Module:** `app.py` (`_bereken_marktintensiteit_velden()`, `bouw_plaatsverdeling()`, `bouw_gemeenteverdeling()`), `web/templates/resultaat.html`, `web/templates/makelaar_profiel.html`.
- **Omschrijving:** nieuw analytisch blok "Marktintensiteit" onder Verdeling per plaats/gemeente: aandeel aanbod, aandeel inwoners, verschil in procentpunten, verwacht aanbod en marktintensiteitsindex (100 = evenredig aan inwonertal). Formules letterlijk conform opdracht: `verwacht aanbod = totaal aanbod x (inwoners gebied / totaal inwoners selectie)`, `marktintensiteitsindex = werkelijk aanbod / verwacht aanbod x 100`. Bewust neutrale terminologie - geen conclusie over woningtekort/verkoopsnelheid/marktgezondheid. Alleen berekend als het inwonertal van het gebied EN minstens één gebied in de selectie bekend is (anders "-", nooit een gok). Nulgebieden blijven zichtbaar (ongewijzigd) omdat de bestaande `alle_plaatsen`-seeding vóór de nieuwe berekening plaatsvindt.
- **Aandachtspunt (geen bug, methodologische kanttekening):** op GEMEENTE-niveau kan de index kunstmatig hoog uitvallen wanneer maar een klein deel van een gemeente in de selectie zit (bv. Bernheze-selectie die óók Vinkel bevat, een piepklein deelgebied van gemeente 's-Hertogenbosch - diens volledige gemeentebevolking (162.272) telt dan mee terwijl er verder niets van 's-Hertogenbosch is gescand). Dit is wiskundig correct volgens de letterlijk gevraagde formule, maar vertekent de interpretatie op gemeenteniveau bij gemengde selecties. Genoteerd, niet gecorrigeerd (conform opdracht: formule letterlijk implementeren).
- **Makelaarsprofiel:** `bouw_makelaar_lokale_marktpositie()` toont inwoners/marktintensiteitsindex nu als expliciet gelabelde extra kolommen ("context") bij Marktpositie per plaats/gemeente. `marktaandeel_pct` blijft ongewijzigd `objecten_makelaar / totaal_markt * 100` - geverifieerd dat inwonertal deze berekening niet raakt.
- **Nog op te lossen:** Nee.

### NIEUW (documentatie, bewust niet gebouwd): Business-equivalent van Marktintensiteit vereist nog een officiële referentielaag
- **Module:** Bedrijfsmatig (toekomstig)
- **Omschrijving:** het inwonertal-concept is voor Bedrijfsmatig niet zinvol; de meest voor de hand liggende referentie is economisch: `actief bedrijfsmatig aanbod / aantal bedrijfsvestigingen of ondernemingen` per plaats/gemeente. Er is nu geen betrouwbare, geverifieerde bron voor vestigingsaantallen in de app (vergelijkbaar met hoe `GEO_REFERENTIE` voor inwoners handmatig via Wikipedia/CBS is opgebouwd). Voordat dit gebouwd wordt, moet een officiële bron (bij voorkeur CBS StatLine/vestigingenregister, eventueel later aangevuld met werkgelegenheids-/segmentcijfers) onderzocht en handmatig geverifieerd worden - dezelfde aanpak als bij `GEO_REFERENTIE`, niet automatisch schrapen.
- **Ernst:** N.v.t. (nog geen functionaliteit, bewust uitgesteld op expliciet verzoek).
- **Nog op te lossen:** Ja, in een toekomstige ronde: eerst CBS-vestigingendata per plaats/gemeente verifiëren, dan pas de ratio bouwen.

### BEVESTIGD (hernieuwd onderzoek): Funda-link op makelaarsprofiel - detailpagina biedt geen bruikbare toegang
- **Module:** Makelaarsprofiel (Woningen), scanner (onderzoek, geen wijziging)
- **Omschrijving:** op verzoek opnieuw onderzocht of een NORMALE Funda woningdetailpagina (niet alleen zoekresultaatkaarten) betrouwbaar een kantoor-ID/makelaarsprofiel-URL prijsgeeft. Getest: Kordaat Makelaars (Nistelrode, 3 losse detailpagina's) en Heuvel Makelaars (Heesch). Resultaat: elke poging - zowel een kale HTTP-fetch als een Playwright/Chromium-fetch zonder de sessie/profiel van de productiescanner - kreeg de Funda bot-detectie-interstitial "Je bent bijna op de pagina die je zoekt" terug, géén echte paginainhoud, dus geen HTML om een kantoor-ID/link uit te lezen. De bestaande, geautoriseerde scanner omzeilt dit alleen dankzij een niet-headless, persistente, echte Chrome-profielsessie (`launch_persistent_context(channel="chrome", headless=False, ...)`) MET een expliciet `needs_human()`/`pause()`-mechanisme voor het geval Funda zelfs dan nog een robot-/menscontrole toont - d.w.z. zelfs de legitieme, geautoriseerde scan vereist soms handmatig ingrijpen. Een onbeheerde achtergrondcontrole (zoals dit onderzoek) kan dat mechanisme niet gebruiken zonder een zichtbaar browserscherm te openen op de machine van de gebruiker en mogelijk een captcha te moeten oplossen - dat is bewust NIET geprobeerd, om (a) geen ongevraagd zichtbaar browserscherm te openen, (b) geen bot-detectie-omzeiling te forceren buiten de al-geautoriseerde scanscope, en (c) het cookie-/sessieprofiel van de echte productiescanner niet te belasten met een niet-essentiële testaanvraag.
- **Conclusie:** geen betrouwbare, automatiseerbare bron voor een Funda-kantoor-ID/profiel-URL beschikbaar zonder een handmatig-gesuperviseerde scan-sessie. Er is dus, conform instructie, GEEN link/knop gebouwd en NOOIT een URL geraden op basis van de makelaarsnaam. `bepaal_funda_makelaar_link()` blijft ongewijzigd (retourneert altijd `None`). Geen schema-wijziging, geen wijziging aan historische scans, scanner-completenessfix niet aangeraakt.
- **Mogelijk toekomstig pad (niet gebouwd):** als de gebruiker dit alsnog wil, zou de bestaande, al-werkende scanner-sessie (dezelfde `launch_persistent_context`, tijdens een normale, door de gebruiker gestarte en zo nodig handmatig begeleide scan) incidenteel één detailpagina per nieuwe makelaar kunnen bezoeken en het resultaat cachen - dat vereist een bewuste architectuurkeuze (extra paginabezoeken per scan, dus langzamer en een groter oppervlak voor bot-detectie) en is nu bewust niet genomen.
- **Nog op te lossen:** Optioneel/toekomstig, alleen na expliciete keuze van de gebruiker om de extra scan-tijd/risico te accepteren.

---

## Update 2026-09-09 (avond) - laatste stabilisatieronde

### OPGELOST: methodiekbreuk Woningen (fictieve aanbodsprong voorkomen)
- **Module:** `app.py` (Marktdynamiek + Dashboard-trend), Woningen.
- **Omschrijving:** zie CLAUDE_STATUS.md § 1. Scans van vóór/na de scanner-completenessfix (grens `2026-09-09T10:09:54`) worden niet meer automatisch met elkaar vergeleken.
- **Nog op te lossen:** Nee. Werkt automatisch weer normaal zodra een 2e scan met de nieuwe methodiek binnenkomt (gesimuleerd getest, nog niet met echte 2e scan bevestigd - zie hieronder).

### NIEUW (niet-blokkerend): methodiekbreuk-mechanisme nog niet bevestigd met een ECHTE 2e nieuwe-methodiek-scan
- **Module:** `app.py`
- **Omschrijving:** de logica ("volgende scan binnen dezelfde nieuwe meetreeks is weer vergelijkbaar") is bewezen via een directe functieaanroep met een gesimuleerd toekomstig scanmoment (`tests/test_stabilisatie_20260909.py`), niet met een daadwerkelijke 2e scan na scan_id 7 (die bestaat nog niet).
- **Ernst:** Laag (de onderliggende query-logica is eenvoudig en direct getest; risico op een verrassing bij de eerste echte 2e scan wordt als klein ingeschat).
- **Nog op te lossen:** Aanbevolen: bevestigen zodra de gebruiker een 2e scan voor hetzelfde gebied uitvoert.

### OPGELOST: Funda-link op makelaarsprofiel - onderzocht, terecht niet gebouwd
- **Module:** Makelaarsprofiel (Woningen)
- **Omschrijving:** zie CLAUDE_STATUS.md § 5. Geen betrouwbare kantoor-ID/URL in bestaande scan-/detaildata; bewust geen gok, geen knop getoond. Roadmap vastgelegd (scanner zou de al-geïnspecteerde `href` in `parse_broker()` kunnen gaan opslaan).
- **Nog op te lossen:** Optioneel/toekomstig - vereist een kleine scanner- + schemawijziging, bewust buiten deze ronde.

### NIEUW (niet-blokkerend): geo-referentietabel blijft handmatig, dekt alleen de huidige 12 plaatsen
- **Module:** `app.py`, `GEO_REFERENTIE`
- **Omschrijving:** ongewijzigd t.o.v. de vorige ronde (zie item hieronder) - nog steeds geen CBS/BAG/PDOK-koppeling, zoals gevraagd niet gebouwd deze ronde.
- **Nog op te lossen:** Optioneel, bij uitbreiding van het scanbereik.

### Test-kalibraties uitgevoerd (geen applicatiebugs)
- `tests/test_regressie.py`: DEEL E-validatiecase vraagt nu expliciet `scan_id=6` op (was impliciet "actueel", wat sinds scan_id 7 niet meer klopt); G3-marktaandeel-tolerantie verbreed voor selecties met veel makelaars (rondingsaccumulatie, elk individueel cijfer was al correct). Zie CLAUDE_STATUS.md voor details.

---

## Update 2026-09-09 (nieuwe ontwikkel-/stabilisatieronde)

### OPGELOST: scanner miste tot ~80% van de resultaten per plaats (was NIET eerder in deze lijst genoteerd - direct als kritiek geclassificeerd en gefixt)
- **Module:** Woningenscanner (`scanner/makelaarsmonitor_v41.py`, `ancestor_card()`)
- **Omschrijving:** Funda's huidige kaartontwerp bevat vaak 3+ zelf-referentiële links per object (foto/titel/CTA-knop); de oude `links <= 2`-heuristiek verwierp daardoor de meeste geldige kaarten. Live bevestigd: Nistelrode 6 van de 27 werkelijke objecten opgeslagen (22%); na de fix 26 van de 27 (96%). Kordaat Makelaars bleek hierdoor de grootste makelaar in Nistelrode (7 objecten) - dit verklaarde de oorspronkelijke "Kordaat ontbreekt"-melding volledig.
- **Ernst:** Was Hoog (kernfunctionaliteit - marktaandeel was structureel onbetrouwbaar voor bijna elke plaats).
- **Fix:** telt nu unieke detail-URL's i.p.v. `<a>`-elementen (`_unieke_detail_urls_in()`).
- **Nog op te lossen:** Nee, maar **de bestaande database bevat nog OUDE, onvolledige scans** - pas na een nieuwe scan via de webinterface is de productiedata zelf gecorrigeerd. Zie CLAUDE_STATUS.md "Aanbevolen volgende stap".

### OPGELOST: Woningen-makelaarsprofiel crashte met TypeError
- **Module:** `app.py`, `bouw_makelaar_profiel_woningen()` + route `/makelaar/<module>/<naam>`
- **Omschrijving:** kreeg de al-geformatteerde presentatierijen (`resultaat["rijen"]`, met `vraagprijs` als string "€ 795.000") in plaats van ruwe numerieke data. Crashte zodra een makelaar minstens één object met bekende vraagprijs had.
- **Ernst:** Was Hoog (bevestigde crash bij een concrete, door de gebruiker beschreven route).
- **Fix:** nieuw `ruwe_rijen`-veld toegevoegd aan `bouw_analyseresultaat()`/`bouw_business_analyseresultaat()`; de profielroute gebruikt nu uitsluitend dat veld. Business bleek niet vatbaar voor dezelfde fout (apart bevestigd).
- **Nog op te lossen:** Nee - 11 echte makelaars + randgevallen getest, 0 fouten (zie `tests/test_regressie.py`).

### NIEUW (deze ronde, niet-blokkerend): geen gemeente-uitsplitsing in het makelaarsprofiel (DEEL D5)
- **Module:** Makelaarsprofiel
- **Omschrijving:** de nieuwe `GEO_REFERENTIE`-laag (plaats→gemeente→provincie+inwoners) wordt nog niet gebruikt in `/makelaar/<module>/<naam>` - het profiel toont nog plaatsen/segmenten, geen "X objecten in gemeente Bernheze, Y% marktaandeel" per gemeente.
- **Ernst:** Laag (expliciet als optioneel benoemd in de opdracht: "als dit eenvoudig volgt... mag het al worden opgenomen").
- **Nog op te lossen:** Optioneel, kleine toevoeging gezien de nu aanwezige referentielaag.

### NIEUW (deze ronde, niet-blokkerend): historische analyse alleen voor Woningen
- **Module:** Business-analyse
- **Omschrijving:** De peildatum-/scanmomentselector (DEEL C) is alleen op de Woningen-analysepagina gebouwd; Business toont nog altijd uitsluitend de nieuwste scan. Bewuste scope-keuze ("deze ronde vooral gericht op Woningen").
- **Ernst:** Laag.
- **Nog op te lossen:** Optioneel, zelfde patroon (ruwe_rijen bestaat al voor Business, scan-selectorlogica zou grotendeels herbruikbaar zijn).

### NIEUW (deze ronde, niet-blokkerend): geo-referentietabel dekt alleen de huidige 12 REGIOS_STANDAARD-plaatsen
- **Module:** `app.py`, `GEO_REFERENTIE`
- **Omschrijving:** een plaats buiten de huidige standaardlijst (bv. een toekomstige nieuwe scanregio) krijgt automatisch "Gemeente onbekend" / "-" voor inwonerdata (veilig, geen crash, geen verzonnen waarde) maar mist dan de geografische context totdat de tabel handmatig wordt uitgebreid.
- **Ernst:** Laag (correct, degradeert veilig - geen foute data).
- **Nog op te lossen:** Optioneel, bij uitbreiding van het scanbereik.

---

## 1. Makelaarsprofiel toont geen ontwikkeling door de tijd
- **Module:** Makelaarsprofiel (Woningen + Bedrijfsmatig)
- **Omschrijving:** Het profiel (`/makelaar/<module>/<naam>`) toont alleen de huidige snapshot (actief aanbod, marktaandeel, m², vraagwaarde/jaarhuur, plaatsen, segmenten). "Ontwikkeling door de tijd" (DEEL N) is expliciet nog niet geïmplementeerd - dit vereist het herleiden van dezelfde makelaar over meerdere exact-vergelijkbare scans heen, wat een aparte, iets grotere aggregatiestap is.
- **Reproduceerbaar:** Ja - elk makelaarsprofiel toont de tekst "Ontwikkeling door de tijd is voor een individuele makelaar nog niet beschikbaar".
- **Ernst:** Laag (expliciet als toekomstwerk benoemd, functionaliteit werkt verder correct).
- **Vermoedelijke oorzaak:** Bewuste scope-keuze binnen de tijdsdruk van deze bouwronde.
- **Nog op te lossen:** Ja, in een volgende ronde (mogelijk samen met DEEL O "Makelaarsprofiel uitbreiden").

## 2. Segmentanalyse Kantoor nog niet in de UI
- **Module:** Bedrijfsmatig - analysepagina
- **Omschrijving:** `bereken_business_segmentanalyse()` is generiek opgezet (categorie + grenzen als parameter, zie DEEL F), maar de analysepagina roept dit alleen aan voor Bedrijfsruimte. Kantoor heeft nog geen eigen segmentgrenzen/-sectie.
- **Reproduceerbaar:** Ja - er is geen "Segmentanalyse Kantoor"-sectie.
- **Ernst:** Laag (expliciet toegestaan door de opdracht: "maak de UI nu niet onnodig ingewikkeld").
- **Vermoedelijke oorzaak:** Bewuste scope-keuze.
- **Nog op te lossen:** Optioneel, op verzoek.

## 3. Dashboard: marktaandeel topmakelaars als tabel, niet als grafiek
- **Module:** Dashboard (beide markten)
- **Omschrijving:** DEEL M noemt "marktaandeel makelaars" als 3e prioriteit voor grafieken. Dit is nu een compacte tabel (makelaar × scanmoment, percentage) i.p.v. een lijngrafiek, om de bouwronde behapbaar te houden - de aanbod- en prijsontwikkeling (prioriteit 1 en 2) zijn wél als SVG-lijngrafiek gebouwd.
- **Reproduceerbaar:** Ja - sectie "Marktaandeel topmakelaars" op het Dashboard.
- **Ernst:** Laag (tabel is functioneel volwaardig, informatie is niet verloren).
- **Vermoedelijke oorzaak:** Bewuste, tijdgedreven vereenvoudiging.
- **Nog op te lossen:** Optioneel - zou eenvoudig kunnen met dezelfde `bouw_svg_lijngrafiek()`-helper.

## 4. Dashboard Woningen: nog geen bouwcategorie-filter (bestaande bouw/nieuwbouw)
- **Module:** Dashboard - Woningen
- **Omschrijving:** DEEL K noemt "bestaande bouw/nieuwbouw waar zinvol" als mogelijk filter. Nu wordt per scanreeks de VOLLEDIGE, ongefilterde snapshot gebruikt (incl. eventuele nieuwbouw) voor de trend. Er is alleen een regiofilter (scanreeks-keuze).
- **Reproduceerbaar:** Ja - geen bouwcategorie-selector op het Dashboard.
- **Ernst:** Middel (kan de trendcijfers laten afwijken van de "Bestaande bouw"-standaardweergave op de analysepagina).
- **Vermoedelijke oorzaak:** Scope-keuze binnen de tijdsdruk van deze bouwronde; `haal_woningen_trend()` zou een `bouwcategorie`-parameter moeten krijgen.
- **Nog op te lossen:** Ja, aanbevolen voor de stabilisatieronde.

## 5. Makelaarstabel Bedrijfsmatig: kolombreedtes niet handmatig verfijnd
- **Module:** Bedrijfsmatig - analysepagina, sectie Makelaars
- **Omschrijving:** De makelaarstabel is uitgebreid van 8 naar 11 kolommen (DEEL E: marktaandeel m², aandeel koopwaarde, aandeel jaarhuur + dekking). De vaste `<colgroup>`-breedtes van de oude 8-kolomsversie zijn losgelaten (tabel valt terug op automatische kolomverdeling binnen `data-table-compact`). Nog niet visueel gecontroleerd in een browser op een normale desktopbreedte.
- **Reproduceerbaar:** Nog te controleren (visuele check, geen geautomatiseerde test mogelijk in deze omgeving).
- **Ernst:** Laag (cosmetisch, geen functionele impact).
- **Vermoedelijke oorzaak:** Kolomaantal gewijzigd zonder nieuwe expliciete breedteverdeling.
- **Nog op te lossen:** Ja, tijdens handmatige controle/stabilisatieronde.

## 6. Marktaandeel-percentages bij kleine aantallen kunnen "hard" ogen
- **Module:** Makelaarstabel (beide markten), Segmentanalyse, Plaatsvergelijking
- **Omschrijving:** Bij een kleine selectie (bv. 3-4 objecten) kan een marktaandeel van 25%/33%/50% als schijnprecies overkomen, ook al is de berekening zelf correct en wordt de noemer altijd getoond.
- **Reproduceerbaar:** Ja, bij elke kleine selectie.
- **Ernst:** Laag (geen fout, wel een aandachtspunt voor interpretatie).
- **Vermoedelijke oorzaak:** Inherent aan percentages bij kleine steekproeven.
- **Nog op te lossen:** Optioneel - eventueel een minimale-steekproef-waarschuwing toevoegen.

## 7. Historische reeksen zijn nog kort (weinig scans per exacte regio)
- **Module:** Dashboard (beide markten)
- **Omschrijving:** Sommige scanreeksen hebben pas 1-2 scans; het Dashboard toont dan terecht "onvoldoende scans voor een trend", maar de grafieken/tabellen ogen daardoor nog leeg voor recent toegevoegde regio's.
- **Reproduceerbaar:** Ja, bij een nieuwe/zelden gescande regio.
- **Ernst:** Laag (correct, verwacht gedrag - genoemd in de opdracht als "door gebrek aan historische data nog beperkt zichtbaar").
- **Vermoedelijke oorzaak:** Simpelweg nog weinig scans.
- **Nog op te lossen:** Nee - lost zichzelf op naarmate er meer scans bijkomen.

## 8. Woningenscanner: "gebiedscontrole mislukt" bij 0 aanbod van een WEL herkend gebied
- **Module:** Woningenscanner (DEEL A)
- **Status: OPGELOST/BEVESTIGD.** De gebruiker heeft tijdens deze sessie zelf, onafhankelijk, een echte scan uitgevoerd voor exact de 5 oorspronkelijk gemelde plaatsen (Nistelrode, Vorstenbosch, Heesch, Heeswijk-Dinther, Vinkel - scan_id 5, 08-09-2026 15:02). Resultaat in de database: Heesch 2, Heeswijk-Dinther 29, Nistelrode 6, Vorstenbosch 2, **Vinkel 0** (geen enkele rij, geen contaminatie van andere plaatsen). De scan is niet vastgelopen. Dit bevestigt de fix voor alle 5 plaatsen, niet alleen voor Vinkel in isolatie.
- **Reproduceerbaar:** N.v.t. meer - live bevestigd.
- **Ernst:** N.v.t. meer.
- **Nog op te lossen:** Nee.

## 9. Geen automatische db-migratie-check voor ontbrekende `aantal_objecten` in oudere databases
- **Module:** `haal_scan_info()` (Woningen)
- **Omschrijving:** `aantal_objecten` is toegevoegd aan de SELECT van `haal_scan_info()`. De kolom bestond al in het `scans`-schema (ongewijzigd schema, geen migratie nodig), maar dit is niet apart getest tegen een hypothetische, zeer oude database zonder die kolom.
- **Reproduceerbaar:** Niet van toepassing op de huidige database (kolom bestaat al langer).
- **Ernst:** Laag.
- **Vermoedelijke oorzaak:** N.v.t. - preventieve notitie.
- **Nog op te lossen:** Nee, tenzij een oudere database-export ooit wordt teruggezet.

## 10. Naamvariant-detectie makelaars: uitgevoerd, niets gevonden
- **Module:** Makelaarstabel (beide markten)
- **Omschrijving:** Op verzoek gecontroleerd of dezelfde makelaar per ongeluk onder verschillende schrijfwijzen wordt gegroepeerd (bv. "Kordaat Makelaars" vs "Kordaat makelaars"), via een casefold-botsingscheck over 3 echte selecties (incl. de brede Woningen- en Business-selectie). Er is bewust GEEN fuzzy matching toegepast (conform de instructie "niet zonder bewijs").
- **Reproduceerbaar:** N.v.t. - controle uitgevoerd, geen variant gevonden in de huidige data.
- **Ernst:** N.v.t.
- **Nog op te lossen:** Nee nu; wel aan te raden om deze check periodiek te herhalen naarmate de data groeit (bv. als onderdeel van een toekomstige geautomatiseerde regressietest).

---

## Samenvatting voor de stabilisatieronde
Prioriteit voor de volgende gezamenlijke bugfixronde: **#4** (bouwcategorie-filter Dashboard Woningen). **#8 is inmiddels live bevestigd/opgelost** tijdens deze sessie. Overige punten zijn cosmetisch of bewust uitgesteld toekomstwerk.
