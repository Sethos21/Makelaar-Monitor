# Makelaar Monitor

Lokale Flask-webapplicatie voor het verzamelen, historisch volgen en analyseren van:

- **Woningaanbod** (bestaande bouw/nieuwbouw) en marktposities van makelaars per regio (Funda).
- **Bedrijfsmatig vastgoed** (kantoren en bedrijfsruimtes, Funda in Business) — inclusief live scan vanuit de webinterface, automatische historie-import, mutatiedetectie, KPI's, makelaarsanalyse en een objectentabel.

Beide modules zijn volledig van elkaar gescheiden: eigen scanner, eigen historie-tool en een eigen SQLite-database per module, zodat een fout in de ene module de andere nooit kan raken.

## Starten

```
py app.py
```

Bereikbaar via `http://127.0.0.1:5000`. De webapp leest de bestaande lokale databases en start desgewenst een nieuwe scan (zichtbaar Chrome-venster, met mogelijke handmatige menscontrole).

## Structuur

- `app.py` - de Flask-webapp: hoofdscherm, filters, analysepagina's, scanstatus en -besturing voor beide modules.
- `web/` - templates (`web/templates/`) en statische bestanden (`web/static/`) van de webinterface.
- `scanner/` - verzamelen van actuele data: `makelaarsmonitor_v41.py` (woningen) en `funda_business_scanner_v1.py` (bedrijfsmatig vastgoed).
- `historie/` - historische opslag en mutatiedetectie: `makelaarsmonitor_historie_v10.py` (woningen) en `funda_business_historie_v1.py` (bedrijfsmatig vastgoed).
- `data/` - lokale SQLite-databases (apart per module: `makelaarsmonitor_historie.sqlite` en `funda_business_historie.sqlite`), niet opgenomen in Git.
- `output/` - gegenereerde CSV's, logs en scan-checkpoints, niet opgenomen in Git.
- `docs/` - projectdocumentatie en overdrachtsstatus.
- `research/` - eenmalige verkenningsscripts (Funda in Business), niet gekoppeld aan de productie-app.
