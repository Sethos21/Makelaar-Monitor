"""Kleine, gedeelde helper voor coöperatieve scan-stop, gebruikt door zowel
funda_business_scanner_v1.py als makelaarsmonitor_v41.py. Bewust een klein,
losstaand module-bestand (geen zware refactor): beide scanners importeren
dezelfde STOP_FLAG_NAAM/STOP_EXITCODE/ScanGestopt/controleer_stop() in plaats
van elk hun eigen kopie te onderhouden.

Werking: app.py maakt in de --output-map van de actieve scan een leeg
bestand met naam STOP_FLAG_NAAM aan zodra de gebruiker op "Scan afbreken"
klikt. De scanner controleert dit bestand op meerdere veilige punten
(voor navigatie, in de paginalus, tijdens een wachtperiode) en breekt
daarna zelf netjes af (sluit Playwright/Chrome via het bestaande
context.close()/finally-pad van de aanroeper)."""

from pathlib import Path

# Let op: STOP_FLAG_NAAM zelf blijft bewust PER SCANNER lokaal gedefinieerd
# (funda_business_scanner_v1.py / makelaarsmonitor_v41.py) - ze gebruiken
# toch al aparte --output-map-mappen, en de Business-naam ("_business_scan_
# stop.flag") staat al vast in het reeds goedgekeurde app.py. Hier wordt
# uitsluitend het generieke, scanner-onafhankelijke gedrag gedeeld.

# Afsluitcode bij een bewust (coöperatief of Ctrl+C) afgebroken scan - apart
# van 0 (succes) en de standaard-1-bij-onverwachte-fout, zodat app.py een
# gebruikersstop betrouwbaar kan onderscheiden van een echte crash.
STOP_EXITCODE = 3


class ScanGestopt(BaseException):
    """Intern signaal dat de gebruiker een stop heeft aangevraagd (via het
    stopvlag-bestand). Erft bewust van BaseException (net als
    KeyboardInterrupt/SystemExit) zodat generieke 'except Exception'-blokken
    dit nooit per ongeluk opvangen en als een gewone navigatie-/scanfout
    behandelen."""


def controleer_stop(stop_flag_pad: Path) -> None:
    if stop_flag_pad.exists():
        raise ScanGestopt()
