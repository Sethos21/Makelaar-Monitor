# Funda in Business — technisch onderzoek

_Datum: 2026-09-04_
_Status: onderzoeksfase (Fase 1-4). Geen productiecode gebouwd. Geen bestaande scanner/historie-tool/database gewijzigd._

Dit document onderzoekt hoe `fundainbusiness.nl` is opgebouwd en stelt een concreet, met de bestaande architectuur verenigbaar implementatievoorstel op voor bedrijfsmatig vastgoed binnen Makelaar Monitor. Alle bevindingen in secties 1-7 zijn live geverifieerd met Playwright tegen de echte site (testgebied: Uden), tenzij expliciet als "niet bevestigd" gemarkeerd. Er is niet gegokt: velden die niet betrouwbaar konden worden vastgesteld, zijn als zodanig benoemd.

De ruwe verkenningsscripts staan onder `research/` (zie `research/LEESMIJ.md`) en zijn niet aan de productie-app gekoppeld.

---

## 1. Bevindingen Funda in Business (samenvatting)

- Funda in Business draait op hetzelfde platform/template-familie als de residentiële `funda.nl` (identieke cookiebanner, identieke `dt`/`dd`-kenmerkenstructuur op detailpagina's, identiek "funda"-merkgevoel). De bestaande `detail_pairs()`-aanpak uit `makelaarsmonitor_v41.py` is dus structureel herbruikbaar.
- De site heeft een strengere, consistent geactiveerde bot-/mensdetectie dan waar de woningenscanner tot nu toe last van had (zie sectie 11). **Headless browsergebruik wordt direct geblokkeerd**, en zelfs een gewone HTTP-fetch (buiten een browser om, getest via `robots.txt`) wordt geblokkeerd. Alleen een zichtbaar, echt Chrome-profiel met rustige, gedoseerde navigatie (één navigatie per verse profielsessie tijdens dit onderzoek) kwam betrouwbaar door.
- Er bestaat een navigeerbare "Bladeren"-hiërarchie: **Provincie → Regio → Gemeente/Plaatsnaam**, los van de directe zoek-URL's per plaats/categorie.
- Belangrijk: door de gemeentelijke herindeling is **Uden geen aparte "Gemeente" meer** (opgegaan in "Maashorst"), maar wel nog een aparte **"Plaatsnaam"** met eigen aanbod (67 objecten op onderzoeksmoment). Alle overige Woningen-plaatsen (Volkel, Nistelrode, Zeeland, Odiliapeel, Vorstenbosch, Veghel, Heesch, Heeswijk-Dinther, Boekel, Schaijk, Vinkel) kwamen eveneens los voor in dezelfde regio-plaatsnaamlijst (Noordoost-Noord-Brabant), dus dezelfde regioselectie is bruikbaar.
- **Terminologie-mismatch (belangrijk voor Fase 4):** Funda's eigen objecttype-taxonomie kent geen categorie "Bedrijfsruimte". De volledige lijst is: *Kantoor, Bedrijfshal, Winkel, Horeca, Agrarisch bedrijf, Agrarische grond, Bouwgrond, Garagebox, Praktijkruimte, Toerisme & dagrecreatie, Sportinstelling, Culturele instelling, Religieuze instelling, Onderwijsinstelling, Zorginstelling, Verhard buitenterrein*. De meest voor de hand liggende vertaling van "Bedrijfsruimte" is **"Bedrijfshal"** — dit moet bevestigd worden met de gebruiker vóórdat de scraper wordt gebouwd (zie sectie 11/12).
- Objecten kunnen **tegelijk te koop én te huur** staan (koopprijs én huurprijs op hetzelfde object) — het datamodel moet dit toestaan, niet een enkel "transactietype per object" afdwingen.
- "Aangeboden sinds" (Funda's eigen eerste-vermeldingsdatum) staat **achter een login** en is dus niet publiek uitleesbaar — "eerste waarneming" moet, net als bij Woningen, volledig uit onze eigen scangeschiedenis komen, niet uit Funda's eigen data.

## 2. URL-structuur

Alle onderstaande URL's zijn live bevestigd (status 200, echte inhoud), tenzij anders vermeld.

| Doel | URL-patroon | Voorbeeld |
|---|---|---|
| Bladeren-startpunt | `/alle-bedrijfsaanbod/bladeren/` | — |
| Provincie | `/alle-bedrijfsaanbod/bladeren/provincie-{provincie-slug}/` | `provincie-noord-brabant` |
| Regio | `/alle-bedrijfsaanbod/bladeren/regio-{regio-slug}/` | `regio-noordoost-noord-brabant` |
| Plaatsnaam-paneel binnen een regio | `...?actpnl=Plaatsnaam` | — |
| **Alle aanbod voor een plaats** | `/alle-bedrijfsaanbod/{plaats-slug}/` | `/alle-bedrijfsaanbod/uden/` (67 objecten) |
| **Aanbod per objecttype + plaats** | `/{objecttype-slug}/{plaats-slug}/` | `/kantoor/uden/`, `/bedrijfshal/uden/`, `/winkel/uden/` |
| Paginering | `/{...}/{plaats-slug}/p{nr}/` (pad-segment, geen querystring) | `/alle-bedrijfsaanbod/uden/p2/` |
| Verkocht | `/alle-bedrijfsaanbod/{plaats-slug}/verkocht/koop/` | bevestigd |
| Verhuurd | `/alle-bedrijfsaanbod/{plaats-slug}/verhuurd/huur/` | bevestigd |
| Kaartweergave | `/kaart/alle-bedrijfsaanbod/{plaats-slug}/` | bevestigd |
| Detailpagina | `/{objecttype-slug}/{plaats-slug}/object-{objectid}-{adres-slug}/` | `/kantoor/uden/object-43295734-oostwijk-1-b/` |
| Makelaarpagina | `/makelaar/{makelaarid}-{makelaar-slug}/` | `/makelaar/11181-bernheze-makelaars/` |

**Niet bevestigd:** het exacte URL-patroon om **transactietype (huur/koop) te combineren** met objecttype + plaats in één navigeerbare URL. Op de resultatenpagina bestaan filterknoppen "Koop / Huur / Beide", maar deze gedroegen zich als client-side/AJAX-filters (geen kant-en-klare `<a href>` gevonden binnen de tijd van dit onderzoek) — vermoedelijk een querystring-parameter die na paginalaad wordt toegepast, of een POST-achtige interactie. **Dit moet in een vervolgstap bevestigd worden** (zie sectie 12, stap 1) voordat de scraper transactietype-filtering kan bouwen. Als praktische tussenoplossing kan de scraper in eerste instantie alle objecten per objecttype+plaats ophalen (zonder URL-filter) en zelf op koopprijs/huurprijs-aanwezigheid classificeren — dit is namelijk al zichtbaar per object in de resultatenlijst zelf (zie sectie 4).

Plaatsnaam-slug volgt dezelfde eenvoudige lowercase-normalisatie als de residentiële site (`uden` → `uden`); niet apart met afwijkende diakritische/spatienormalisatie getest, maar geen reden om aan te nemen dat dit afwijkt van de bestaande `funda_area_slug()`-logica in `makelaarsmonitor_v41.py`.

## 3. Selectors / DOM-structuur

**Resultatenlijst** (bevestigd via live `outerHTML`-inspectie):

```html
<div class="search-result__header-title-col">
  <a data-object-url-tracking="resultlist"
     href="https://www.fundainbusiness.nl/bedrijfshal/uden/object-43193545-mandenmakerstraat-17/?navigateSource=resultlist"
     data-search-result-item-anchor="43193545"
     data-track-click="Listing Results Clicked" ...>
    <h2 class="search-result__header-title fd-m-none" data-test-search-result-header-title="">
      Mandenmakerstraat 17, Uden
    </h2>
  </a>
  <a ...>
    <h4 class="search-result__header-subtitle fd-m-none" data-test-search-result-header-subtitle="">
      Bedrijfshal
    </h4>
  </a>
</div>
```

Aanbevolen selector voor detail-links: **`a[data-search-result-item-anchor]`** — betrouwbaarder dan de residentiële `a[href*="/detail/koop/"]`-aanpak, omdat het object-ID rechtstreeks als attribuut beschikbaar is (geen aparte regex nodig om het ID uit de URL te halen).

**Detailpagina**: gebruikt dezelfde `dt`/`dd`-structuur als de residentiële site (kopjes zoals "Overdracht", "Bouw", "Oppervlakten", "Indeling", "Energie", "Omgeving", "Parkeergelegenheid" groeperen de `dt`/`dd`-paren). De bestaande `detail_pairs()`-functie uit `makelaarsmonitor_v41.py` (loopt over alle `dt` en pakt de eerstvolgende `dd`) werkt hier zonder aanpassing.

**Niet onderzocht:** of er, net als bij Woningen, een aparte "Toppositie"-carrousel-sectie bestaat. Op de onderzochte Uden-resultatenpagina (67 objecten, kleine markt) is **geen** Toppositie-achtige sectie aangetroffen — geen kop, geen apart DOM-blok. Dit kan betekenen dat het mechanisme niet bestaat voor bedrijfsmatig, of dat het alleen verschijnt bij grotere/drukkere markten. **Nader onderzoek nodig met een grotere plaats** voordat dit met zekerheid kan worden uitgesloten.

## 4. Beschikbare velden

### Betrouwbaar uit het zoekresultaat zelf

Bevestigd door live inspectie van de resultatenrijen op `/alle-bedrijfsaanbod/uden/`:

| Veld | Betrouwbaar beschikbaar? | Opmerking |
|---|---|---|
| Adres | Ja | Gecombineerd met plaats in `<h2>` ("Mandenmakerstraat 17, Uden") — zelfde patroon als Woningen, plaats moet uit de tekst/URL gesplitst worden |
| Plaats | Ja | Zie hierboven; ook als pad-segment in de detail-URL beschikbaar (betrouwbaarder, zie sectie 6) |
| Postcode | **Nee, niet inline zichtbaar** | "Postcode" staat wel als sorteeroptie vermeld (dus het onderliggende model heeft dit veld), maar wordt niet in de resultaatrij getoond. Wel aanwezig op de detailpagina (bv. "5406 XT Uden") |
| Objecttype | Ja | `<h4>`-subtitel, bv. "Kantoor", "Bedrijfshal", "Winkel", soms samengesteld ("Bedrijfshal\|Garagebox") |
| Huur/koop | Ja (afgeleid) | Herkenbaar aan prijsnotatie: `k.k.` = koop, `/mnd`, `/m²/jaar` = huur. Een object kan beide tonen |
| Vraagprijs (koop) | Ja, indien van toepassing | bv. "€ 415.000 k.k." |
| Huurprijs | Ja, indien van toepassing | bv. "€ 95 /m²/jaar" of "€ 2.600 /mnd" — **twee verschillende prijs-eenheden voor huur zijn gezien** (per m²/jaar én per maand) |
| Prijs-eenheid | Ja (afgeleid uit prijstekst) | k.k. / /m²/jaar / /mnd |
| Oppervlakte | Ja | soms dubbel genoteerd, bv. "70 m² / 70 m²" (vermoedelijk verhuurbaar/totaal, of unit-gerelateerd) |
| Eventuele deeloppervlaktes | Gedeeltelijk | vrije tekst zoals "Units vanaf 250 m²" komt voor bij objecten die opgesplitst kunnen worden — geen apart gestructureerd veld, wel herkenbaar patroon |
| Status | **Indirect** | Niet als label per rij op de standaardlijst; "beschikbaar" is impliciet (de standaardlijst toont alleen niet-verkocht/verhuurd objecten); verkocht/verhuurd staat op aparte URL's (zie sectie 2 en 6) |
| Makelaar | Ja | Naam + link naar makelaarpagina |
| Funda-detail-URL | Ja | Zie sectie 2/3, inclusief stabiel object-ID |

### Betrouwbaar op de detailpagina (live bevestigd op een kantoorobject)

| Veld | Waarde in voorbeeld | Opmerking |
|---|---|---|
| Vraagprijs | € 415.000 kosten koper | |
| Huurprijs | € 2.600 per maand | |
| Servicekosten | "Geen servicekosten bekend" (sentinelwaarde) of een bedrag | |
| Aangeboden sinds | **"Log in om te bekijken"** | **Niet publiek beschikbaar — inloggen vereist** |
| Status | Beschikbaar | |
| Aanvaarding | In overleg | |
| Hoofdfunctie | Kantoor | |
| Soort bouw | Bestaande bouw | zelfde concept als Woningen |
| Bouwjaar | 1998 | |
| Oppervlakte | 348 m² | |
| Perceel | 3.190 m² | |
| Aantal bouwlagen | 1 bouwlaag | |
| Voorzieningen | vrije tekst (bv. "Te openen ramen, systeemplafond, toilet, pantry, verwarming en kamerindeling") | niet gestructureerd, wel bruikbaar als vrij tekstveld |
| Energielabel | A+++ | |
| Ligging | Bedrijventerrein | |
| Bereikbaarheid | vrije tekst (afstand bushalte/station/snelweg) | |
| Parkeerplaatsen | "10 niet-overdekte parkeerplaatsen" (tekst) | getal is uit tekst te parsen, niet als kaal getal beschikbaar |

**Niet bevestigd / niet gezien op dit voorbeeldobject:** aparte kantooroppervlak- vs. bedrijfsruimteoppervlak-splitsing, BTW-vermelding. Dit kan per objecttype verschillen (bv. bij agrarische grond of bedrijfshallen met meerdere units) — **niet aannemen dat deze velden universeel ontbreken, alleen dat ze niet op dít object voorkwamen.** Verder onderzoek met meerdere objecttypes nodig voor volledige zekerheid.

## 5. Voorbeeldrecords (echte, live opgehaalde data — Uden, 2026-09-04)

**Zoekresultaat-rij (ruwe tekst zoals in de DOM):**
```
Oostwijk 1-B, Uden
Kantoor
€ 415.000 k.k.
€ 89 /m²/jaar
348 m²
Van der Krabben Bedrijfsmakelaars
```
→ Funda-detail-URL: `https://www.fundainbusiness.nl/kantoor/uden/object-43295734-oostwijk-1-b/`

**Detailpagina-kenmerken (zelfde object):**
```
Vraagprijs: € 415.000 kosten koper
Huurprijs: € 2.600 per maand
Servicekosten: Geen servicekosten bekend
Aangeboden sinds: Log in om te bekijken
Status: Beschikbaar
Aanvaarding: In overleg
Hoofdfunctie: Kantoor
Soort bouw: Bestaande bouw
Bouwjaar: 1998
Oppervlakte: 348 m²
Perceel: 3.190 m²
Aantal bouwlagen: 1 bouwlaag
Voorzieningen: Te openen ramen, systeemplafond, toilet, pantry, verwarming en kamerindeling
Energielabel: A+++
Ligging: Bedrijventerrein
Bereikbaarheid: Bushalte op 500 m tot 1000 m, NS Intercitystation op 5000 m of meer en snelwegafrit op 3000 m tot 4000 m
Parkeerplaatsen: 10 niet-overdekte parkeerplaatsen
```

**Nog een resultaatrij, met dubbele oppervlaktenotatie en unit-splitsing:**
```
Bitswijk 1, Uden
Winkel met showroom
Prijs n.o.t.k.
3.688 m² / 3.688 m²
Units vanaf 250 m²
Van der Krabben Bedrijfsmakelaars
```

## 6. Statuslogica

- **Beschikbaar** wordt op de detailpagina expliciet vermeld (`Status: Beschikbaar`).
- Op de **standaard resultatenlijst per plaats** (`/alle-bedrijfsaanbod/uden/`) staan alleen actief aangeboden objecten (67 stuks) — geen inline statuskolom per rij.
- **Verkocht** en **verhuurd** zijn aparte, losse URL's/lijsten (`/verkocht/koop/`, `/verhuurd/huur/`), niet een statuswaarde binnen de hoofdlijst. Er verscheen een facet "Status ▪ Verkocht/verhuurd (63)" op de hoofdlijst, wat suggereert dat dit optioneel is bij te schakelen als filter, maar de aparte URL's zijn de meest expliciete/betrouwbare bron.
- **"Onder bod"/"onder optie"** (tussenstatussen zoals bij Woningen) zijn **niet waargenomen** in deze dataset van 67 objecten. Niet uit te sluiten dat deze bestaan (Funda in Business kent immers een "Aanvaarding"-veld en mogelijk optiestatussen bij specifieke objecten), maar niet bevestigd — **nader onderzoek nodig**, bij voorkeur met een grotere/drukkere markt.
- **Aanbeveling voor het datamodel:** behandel "status" bij bedrijfsmatig vooralsnog als minimaal `Beschikbaar` / `Verkocht` / `Verhuurd`, met ruimte om later tussenstatussen toe te voegen zodra bevestigd.

## 7. Advertenties / dedupe

- **Geen Toppositie-sectie aangetroffen** in dit onderzoek (zie sectie 3) — het specifieke dedupe-probleem van Woningen (een object dat zowel in de Toppositie-carrousel als in de gewone resultaten voorkomt) is dus **niet bevestigd van toepassing** op bedrijfsmatig, maar ook niet met zekerheid uitgesloten (kleine testmarkt).
- Elk object heeft een **stabiel numeriek object-ID**, zowel in de URL (`object-{id}-...`) als in het `data-search-result-item-anchor`-attribuut. Dit is een sterkere, direct herbruikbare dedupe-sleutel dan bij Woningen (waar de volledige `Funda_detail_URL` als sleutel dient) — het object-ID alleen volstaat al, en is ongevoelig voor eventuele adres-slug-wijzigingen in de URL.
- Paginering is pad-gebaseerd en telt netjes op tot het totaal (67 objecten / 4 pagina's), zonder aanwijzingen voor overlap tussen pagina's in de steekproef die is bekeken.
- **Aanbeveling:** dedupliceer net als bij Woningen defensief op de unieke sleutel (hier: object-ID), ook al is het risico kleiner ingeschat dan bij Woningen.

---

## 8. Voorgesteld datamodel

### Overwogen architecturen

**A. Eén `snapshots`-tabel voor beide markten**
Zou de bestaande, in productie draaiende `historie_v10.py`/`snapshots`-tabel moeten wijzigen (extra kolommen, mogelijk NULL-waarden voor woningen-specifieke of bedrijfsmatige velden). **Afgewezen**: expliciet verboden door de opdracht ("migreer de productiedatabase nog niet", "wijzig historie_v10.py nog niet"), en het zou de twee heel verschillende datasets (woningen vs. bedrijfsmatig met dubbele prijzen, bouwlagen, servicekosten, etc.) onnodig door elkaar vlechten.

**B. Volledig aparte `snapshots`-tabellen per markt**
Eenvoudig te bouwen zonder enig risico voor de bestaande woningendata: een nieuwe `scans_bedrijfsmatig` + `snapshots_bedrijfsmatig` naast de bestaande `scans`/`snapshots`, met een eigen, op maat gesneden kolommenset. Nadeel: op termijn twee losse datamodellen die niet natuurlijk samenkomen voor cross-markt dashboards ("totaal aantal objecten over beide markten" vereist dan een UNION met kolomherschikking).

**C. Generieke basistabel + markt-specifieke detailtabel**
Eén generieke tabel met de door de gebruiker zelf voorgestelde velden (`bron`, `markt`, `transactietype`, `objecttype`, `plaats`, `adres`, `vraagprijs`, `prijs_eenheid`, `oppervlakte`, `status`, `makelaar`, `funda_url`, `scanmoment`), aangevuld met een aparte detailtabel voor bedrijfsmatige extra velden (servicekosten, bouwjaar, bouwlagen, ligging, bereikbaarheid, parkeerplaatsen, voorzieningen, aanvaarding), gekoppeld via de generieke sleutel.

### Aanbeveling: **C, maar alléén nieuw geïntroduceerd voor bedrijfsmatig — woningen blijft ongemoeid**

C is architecturaal het meest onderhoudbaar: één plek voor generieke KPI's/dashboards over beide markten, met markt-specifieke details netjes gescheiden zodat bedrijfsmatige velden niet als overbodige NULL-kolommen in woningenrijen (of andersom) terechtkomen. Dat is ook precies wat de gebruiker zelf al schetste met de generieke veldenlijst.

Om het expliciete "niet migreren, niet aanraken" van de bestaande woningendata te respecteren, wordt dit **niet met terugwerkende kracht op de bestaande `snapshots`-tabel toegepast**. In plaats daarvan:

1. De bestaande `scans`/`snapshots`-tabellen (Woningen) blijven **exact** zoals ze zijn; `historie_v10.py` blijft ongewijzigd.
2. Er komen **nieuwe** tabellen, uitsluitend voor bedrijfsmatig, opgezet volgens het generieke model (zodat een latere, aparte migratiestap woningen er eventueel bij zou kunnen aansluiten — maar dat is expliciet toekomstig werk, geen onderdeel van deze opdracht).

### Voorgestelde tabellen (nieuw, nog niet aangemaakt)

```sql
CREATE TABLE scans_bedrijfsmatig (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_hash TEXT NOT NULL UNIQUE,
    scanmoment TEXT NOT NULL,
    peildatum TEXT,
    gebied TEXT NOT NULL,          -- zelfde concept als bij Woningen (gekozen plaatsen)
    bronbestand TEXT NOT NULL,
    aantal_objecten INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE snapshots_bedrijfsmatig (
    scan_id INTEGER NOT NULL,
    object_id TEXT NOT NULL,           -- Funda's eigen numerieke object-ID (stabielere sleutel dan de URL)
    funda_url TEXT NOT NULL,
    bron TEXT NOT NULL DEFAULT 'funda_business',
    markt TEXT NOT NULL DEFAULT 'bedrijfsmatig',
    objecttype TEXT,                   -- Kantoor / Bedrijfshal / Winkel / ...
    transactietype TEXT,               -- koop / huur / koop_en_huur
    plaats TEXT,
    adres TEXT,
    postcode TEXT,
    vraagprijs INTEGER,                -- koopprijs, indien van toepassing
    huurprijs INTEGER,
    prijs_eenheid TEXT,                -- 'k.k.' / '/m2/jaar' / '/mnd'
    oppervlakte REAL,
    status TEXT,
    makelaar TEXT,
    peildatum TEXT,
    waarschuwing TEXT,
    PRIMARY KEY (scan_id, object_id),
    FOREIGN KEY (scan_id) REFERENCES scans_bedrijfsmatig(scan_id)
);

CREATE TABLE snapshots_bedrijfsmatig_detail (
    scan_id INTEGER NOT NULL,
    object_id TEXT NOT NULL,
    servicekosten TEXT,
    bouwjaar INTEGER,
    soort_bouw TEXT,
    perceeloppervlakte REAL,
    aantal_bouwlagen TEXT,
    voorzieningen TEXT,
    energielabel TEXT,
    ligging TEXT,
    bereikbaarheid TEXT,
    parkeerplaatsen TEXT,
    aanvaarding TEXT,
    PRIMARY KEY (scan_id, object_id),
    FOREIGN KEY (scan_id, object_id) REFERENCES snapshots_bedrijfsmatig(scan_id, object_id)
);
```

Dit is een **voorstel**, nog niet uitgevoerd — er is geen database gewijzigd.

---

## 9. Historie-aanpak

Analoog aan de bestaande `historie_v10.py`-logica (`first_last_seen`, `build_mutations`), maar in een **nieuwe**, aparte tool (werktitel `historie_bedrijfsmatig_v1.py`), zodat `historie_v10.py` niet wordt aangeraakt:

- **Eerste/laatste waarneming**: `MIN`/`MAX(scanmoment)` per `object_id` binnen hetzelfde `gebied`, exact zoals bij Woningen — dit is een eigen-scangeschiedenis-concept, onafhankelijk van Funda's (login-gated) "aangeboden sinds".
- **Dagen in monitor**: verschil in dagen tussen eerste en laatste waarneming.
- **Nieuw aanbod**: `object_id` komt voor het eerst voor in de nieuwste scan, niet in de vorige scan van hetzelfde gebied.
- **Uit aanbod**: `object_id` kwam wél voor in de vorige scan, niet meer in de nieuwste. **Expliciet, zoals gevraagd: dit betekent uitsluitend "niet meer aangetroffen bij een vergelijkbare scan"** — geen automatische aanname van "verkocht" of "verhuurd". Een latere, optionele verrijking zou dit kunnen kruisen met de aparte `/verkocht/`- en `/verhuurd/`-URL's (sectie 2/6) om een geïnformeerde (nog steeds niet 100% zekere) aanduiding te geven — dat is toekomstig werk, geen onderdeel van deze fase.
- **Prijswijziging**: verschil tussen eerste en laatste geregistreerde `vraagprijs` **en** apart tussen eerste en laatste `huurprijs` (omdat beide onafhankelijk van elkaar kunnen wijzigen bij dual-listed objecten).
- **Statuswijziging / makelaarswijziging**: zelfde vergelijkingslogica als `mutation_name()` in `historie_v10.py`, toegepast op de nieuwe `snapshots_bedrijfsmatig`-tabel.

## 10. Webapp-aanpak

Het bestaande tabblad "Bedrijfsmatig vastgoed" (nu een placeholder met alleen de aankondigingstekst) krijgt dezelfde opbouw als het Woningen-tabblad:

- **Transactie**: `[ ] Huur` `[ ] Koop` (checkboxes, net als Status bij Woningen).
- **Objecttype**: `[ ] Kantoor` `[ ] Bedrijfsruimte` — de checkbox-*labels* kunnen "Bedrijfsruimte" blijven tonen (herkenbare taal voor de gebruiker), met **intern** een mapping naar Funda's eigen term "Bedrijfshal" zodra bevestigd (zie sectie 1/11).
- **Regio**: identiek mechanisme als Woningen — dezelfde plaatsenlijst, dezelfde "Plaats toevoegen"/"Alles selecteren"/"Alles wissen"-knoppen, herbruikbare JS/CSS.
- **Acties**: "Analyse huidige database" (leest `snapshots_bedrijfsmatig` read-only, zelfde patroon als `bouw_analyseresultaat()`) en "Nieuwe scan uitvoeren" (zelfde achtergrondthread/lock-aanpak als de bestaande `/scan/start`, maar wijzend naar de nieuwe bedrijfsmatige scanner en historie-tool).
- Geen grote refactor van `app.py` nodig: de bestaande patronen (parse_filters, bouw_analyseresultaat, scan-lock, statuspagina) zijn generiek genoeg om te hergebruiken met een tweede set routes/constantes voor bedrijfsmatig, zonder de woningen-routes aan te raken.

**Toekomstige dashboard-KPI's** (niet in deze fase bouwen, wel alvast passend in het datamodel hierboven): aantal beschikbare objecten, totaal beschikbaar m², gemiddelde/mediane huur- en koopprijs per m², aanbod per plaats, aanbod per makelaar, marktaandeel makelaars, nieuwe/verdwenen objecten, prijswijzigingen, gemiddelde looptijd — stuk voor stuk afleidbaar uit `snapshots_bedrijfsmatig` (+ detailtabel), analoog aan hoe de Woningen-KPI's nu al uit `snapshots` worden berekend.

---

## 11. Risico's / onzekerheden

1. **Bot-/mensdetectie is merkbaar strenger dan bij de residentiële scanner.** Headless en losse HTTP-requests (ook `robots.txt`) worden altijd geblokkeerd; zelfs met een zichtbaar, echt Chrome-profiel triggerden meerdere navigaties/kliks ná elkaar in dezelfde sessie herhaaldelijk de menscontrole. Wat wél betrouwbaar werkte: één navigatie per verse profielsessie, rustig getimed. Een toekomstige bedrijfsmatige scanner moet hier expliciet rekening mee houden (mogelijk vaker een handmatige captcha-bevestiging nodig dan bij Woningen, zeker bij multi-pagina scans in één sessie) — dit is een reëel risico voor de haalbaarheid van volautomatisch scannen en verdient een eigen vervolgonderzoek voordat er veel bouwtijd in gaat.
2. **Terminologie "Bedrijfsruimte" vs. Funda's "Bedrijfshal"** moet expliciet bevestigd worden met de gebruiker — anders loopt de scraper/filter op een niet-bestaande categorie.
3. **Transactietype-URL-combinatie niet bevestigd** (zie sectie 2) — vereist een gerichte vervolgtest voordat de scraper gebouwd wordt, of een tijdelijke workaround (classificeren op basis van getoonde prijzen i.p.v. URL-filter).
4. **"Aangeboden sinds" is login-gated** — geen risico voor onze aanpak (we gebruiken toch onze eigen scangeschiedenis), maar wel een beperking als iemand later verwacht Funda's eigen listingdatum te kunnen uitlezen.
5. **Toppositie/dedupe-mechanisme en "onder bod"-achtige tussenstatussen niet bevestigd aanwezig of afwezig** — steekproef was klein (67 objecten, één plaats). Een tweede verkenning met een grotere markt (bv. Den Bosch, 253+ objecten in de eerdere plaatsnaamlijst) zou meer zekerheid geven.
6. **Dual-listed objecten (koop én huur tegelijk)** vereisen zorgvuldige datamodel-/dedupe-logica; een simpel "één transactietype per rij"-model zou data verliezen.
7. Geen juridisch advies, maar feitelijk relevant: de merkbaar strengere geautomatiseerde-toegangsbeveiliging van fundainbusiness.nl (in vergelijking met de residentiële site die al langer in productie wordt gescand) is een signaal om bij de implementatie extra behoudend te zijn met scanfrequentie/-omvang.

## 12. Concreet stappenplan voor implementatie

1. **Bevestig openstaande onzekerheden** uit sectie 11 met één gerichte, rustige vervolgverkenning (grotere plaats, transactietype-URL, toppositie-check) vóórdat er scrapercode wordt geschreven.
2. **Bevestig met de gebruiker**: is "Bedrijfshal" de juiste vertaling van "Bedrijfsruimte"? Moeten meer Funda-objecttypes (Winkel, Horeca, ...) later ook mee, of blijft het bij Kantoor + Bedrijfshal?
3. **Bouw een nieuwe, losstaande scanner** (bv. `scanner/makelaarsmonitor_bedrijfsmatig_v1.py`) naar analogie van `makelaarsmonitor_v41.py` — eigen FIELDS-lijst volgens sectie 4/8, eigen CLI, zelfde voorzichtige navigatiestrategie (zichtbaar Chrome-profiel, gedoseerde navigatie, captcha-pauze). **Niet** de bestaande woningenscanner wijzigen.
4. **Bouw een nieuwe, losstaande historie-tool** (bv. `historie/historie_bedrijfsmatig_v1.py`) die de nieuwe tabellen uit sectie 8 vult, met de mutatielogica uit sectie 9. **Niet** `historie_v10.py` wijzigen.
5. **Voeg backend-ondersteuning toe in `app.py`** voor bedrijfsmatig: eigen constantes/routes naast de bestaande (geen grote refactor — hergebruik dezelfde functiepatronen als bij Woningen).
6. **Werk het bestaande "Bedrijfsmatig vastgoed"-tabblad uit** volgens sectie 10 (filters, "Analyse huidige database", "Nieuwe scan uitvoeren"), ter vervanging van de huidige aankondigingstekst.
7. **Test eerst uitsluitend met Uden** (klein, overzichtelijk aantal objecten — 67), zowel de scan als de historie-import, vóór opschaling naar meer regio's.
8. **Dashboard-KPI's pas bouwen** zodra er meerdere scandagen beschikbaar zijn om trends zinvol te tonen (zelfde volgorde als bij Woningen gehanteerd).

---

## Randvoorwaarden nageleefd tijdens dit onderzoek

- `scanner/makelaarsmonitor_v41.py` en `historie/makelaarsmonitor_historie_v10.py`: niet gewijzigd.
- Productiedatabase: niet gewijzigd, niet gemigreerd.
- Geen bestaande data verwijderd.
- Geen grote refactor van `app.py`.
- Geen volledige Funda Business-scraper gebouwd — uitsluitend onderzoek en een voorstel.
- Onderzoeksscripts staan onder `research/`, losgekoppeld van de productie-app.
