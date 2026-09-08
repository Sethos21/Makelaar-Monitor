# research/ — verkenningsscripts Funda in Business

Losse, eenmalige Playwright-verkenningsscripts, gebruikt voor het onderzoek in
`docs/FUNDA_BUSINESS_ONDERZOEK.md`. **Niet gekoppeld aan de productie-app.**

Belangrijk: fundainbusiness.nl blokkeert headless/generieke automatisering
direct. Deze scripts gebruiken daarom, net als `scanner/makelaarsmonitor_v41.py`,
een zichtbaar, echt Chrome-profiel (`channel="chrome"`, `headless=False`) via
`launch_persistent_context`. Een eigen profielmap wordt gebruikt
(`%LOCALAPPDATA%\FundaMakelaarsmonitor\ResearchProfile`), gescheiden van het
scanner-profiel.

Volgorde die tot bruikbare resultaten leidde:

1. `verken_bladeren.py` — eerste bezoek aan de "Bladeren"-hiërarchie
   (provincie/regio/gemeente/plaatsnaam), cookiebanner accepteren.
2. `verken_plaatsnaam.py` — het "Plaatsnaam"-paneel van een regio, waarin
   Uden als losse plaats verschijnt (i.p.v. de gefuseerde gemeente
   "Maashorst") met de link naar de echte listingpagina.
3. `verken_uden_lijst.py` — de daadwerkelijke resultatenlijst voor Uden:
   filters, facetten, resultaatrijen en detail-URL's.
4. `verken_detailpagina.py` — een individuele detailpagina, met de
   dt/dd-kenmerkenlijst (bouwjaar, energielabel, parkeerplaatsen, etc.).

**Werkwijze die betrouwbaar werkte:** telkens één script, één navigatie, met
een vers profiel (`Remove-Item -Recurse -Force` op de profielmap vooraf).
Meerdere navigaties/kliks binnen dezelfde sessie triggerden herhaaldelijk de
mens-/botcontrole van Funda. Zie het hoofdrapport voor de volledige analyse.

## Vervolgonderzoek: DOM-structuur van resultaatkaarten (reparatie parser)

Na een eerste live scan met `scanner/funda_business_scanner_v1.py` bleek de
kaart-parsing systematisch fout (adres/objecttype verwisseld, prijs/
oppervlakte/makelaar niet gevonden). Root cause: het aantal `<a
data-search-result-item-anchor>`-elementen per kaart varieert (afbeeldingen,
"Blikvanger"-promotekst, titel, subtitel delen soms allemaal dezelfde id) -
een positionele aanname ("1e anchor = titel, 2e = subtitel") is dus fragiel.

Deze scripts gebruiken hetzelfde `BusinessProfile` als de scanner zelf:

5. `verken_kaart_structuur.py` — toont dat `[data-search-result-listing]`
   betrouwbaar precies één element per kaart oplevert (promo én gewoon), en
   dumpt de volledige outerHTML van de eerste (promo-)kaart.
6. `verken_kaart_kenmerken.py` — leest meerdere kaarten uit via de
   semantische attributen `[data-test-search-result-header-title]` en
   `[data-test-search-result-header-subtitle]` i.p.v. anchor-posities, en
   toont de volledige kaarttekst (incl. prijs/oppervlakte/makelaar).
7. `verken_geen_prijs.py` — bevestigt het exacte brontekstformaat van
   "Prijs op aanvraag" / "Huurprijs op aanvraag" voor objecten zonder
   zichtbare prijs.

**Resultaat:** de parser gebruikt nu `[data-search-result-listing]` als
kaartcontainer en de twee `data-test-search-result-header-*`-attributen voor
adres/objecttype, in plaats van anchor-tellen/-positie. Zie
`docs/CLAUDE_STATUS.md` voor de volledige samenvatting van de reparatie.
