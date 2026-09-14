"""Lettura di importi e date dagli estratti conto: una sola implementazione,
usata sia dal parser PDF sia da quello CSV."""

from __future__ import annotations

import re
from datetime import datetime

DATE_FORMATS = [
    '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
    '%Y/%m/%d', '%Y-%m-%d', '%Y.%m.%d',
    '%d/%m/%y', '%d-%m-%y', '%d.%m.%y',
    '%Y%m%d',
]


def parse_amount(amount_str: str) -> float:
    """Converte un importo in float, qualunque sia la convenzione dei separatori.

    L'ultimo separatore vale come decimale solo se lo seguono una o due cifre:
    cosi' `1.500` resta millecinquecento e `1.500,00` non finisce a zero.
    """
    if not amount_str:
        return 0.0
    testo = str(amount_str).strip()
    if not testo:
        return 0.0
    # Le banche scrivono il negativo in tre modi: -100, 100-, (100).
    negativo = '-' in testo or ('(' in testo and ')' in testo)
    pulito = re.sub(r'[^\d,.]', '', testo)
    if not pulito:
        return 0.0
    separatore = max(pulito.rfind(','), pulito.rfind('.'))
    if separatore == -1:
        numero = pulito
    elif len(pulito) - separatore - 1 in (1, 2):
        numero = re.sub(r'[,.]', '', pulito[:separatore]) + '.' + pulito[separatore + 1:]
    else:
        numero = re.sub(r'[,.]', '', pulito)
    try:
        valore = float(numero)
    except ValueError:
        return 0.0
    return -valore if negativo else valore


# Mesi scritti per esteso o abbreviati ("01 lug 2026", "3 August 2026"), nelle
# lingue dell'app. Si confronta l'inizio della parola: "juin" e "juil" vanno
# tenuti distinti, per il resto bastano tre lettere.
MESI = {
    'gen': 1, 'jan': 1, 'ene': 1, 'feb': 2, 'fév': 2, 'fev': 2, 'mar': 3, 'mär': 3,
    'apr': 4, 'avr': 4, 'abr': 4, 'mag': 5, 'may': 5, 'mai': 5, 'giu': 6, 'jun': 6, 'juin': 6,
    'lug': 7, 'jul': 7, 'juil': 7, 'ago': 8, 'aug': 8, 'aoû': 8, 'aou': 8, 'set': 9, 'sep': 9,
    'ott': 10, 'oct': 10, 'okt': 10, 'nov': 11, 'dic': 12, 'dec': 12, 'dez': 12, 'déc': 12,
}


def parse_date(date_str: str) -> str | None:
    """Data in ISO, o None se non e' riconoscibile. Non inventa la data di oggi."""
    if not date_str:
        return None
    a_parole = re.fullmatch(r'\s*(\d{1,2})\.?\s+([^\W\d]+)\.?\s+(\d{4})\s*', str(date_str))
    if a_parole:
        giorno, nome, anno = a_parole.groups()
        nome = nome.lower()
        mese = next((MESI[k] for k in sorted(MESI, key=len, reverse=True) if nome.startswith(k)), None)
        try:
            return datetime(int(anno), mese, int(giorno)).strftime('%Y-%m-%d') if mese else None
        except ValueError:
            return None
    pulito = re.sub(r'[^\d\-/.]', '', str(date_str).strip())
    for formato in DATE_FORMATS:
        try:
            return datetime.strptime(pulito, formato).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None
