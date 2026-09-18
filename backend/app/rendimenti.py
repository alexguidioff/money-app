"""Quanto ha reso il portafoglio, e quando non si puo' dirlo.

Il rendimento di un portafoglio che riceve denaro ogni mese non e' il guadagno
diviso il capitale: se versi mentre il mercato e' fermo quel rapporto si muove
senza che sia successo niente, e chi legge crede di aver guadagnato. Qui si
misura il tempo: quanto ha reso il denaro **mentre era dentro**.

Il metodo e' Modified Dietz, periodo per periodo, concatenato. E' quello giusto
quando si conoscono le valutazioni solo agli estremi del periodo, ed e' il caso
di questa app: le quotazioni sono chiusure di fine mese, non prezzi di ogni
giorno, e inventarne una serie giornaliera per far contenta la formula sarebbe
piu' lavoro per una precisione che a questa scala non cambia una decisione.

Modulo puro: riceve valutazioni e flussi gia' raccolti e non interroga niente,
come `calculation_engine` e `fire_montecarlo`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

ZERO = Decimal("0")
UNO = Decimal("1")

# I rendimenti si mostrano con quattro decimali, come il `returnRate` delle
# posizioni: e' l'ordine di grandezza di un rendimento, non di un saldo, e la
# catena viaggia a precisione piena fino a qui.
QUATTRO_DECIMALI = Decimal("0.0001")

# Perche' un rendimento non c'e'. Sono codici, non frasi: la frase la sceglie
# l'interfaccia nella lingua dell'utente. Nessuno di questi e' uno zero: uno
# zero si legge "non ho guadagnato niente", che e' un'affermazione, e spesso
# falsa.
PREZZO_MANCANTE = "prezzo_mancante"
STORIA_TROPPO_CORTA = "storia_troppo_corta"


@dataclass(frozen=True)
class Valutazione:
    """Quanto valeva il portafoglio a una certa data.

    ``valore`` a ``None`` vuol dire "non calcolabile", non "zero": un mese in
    cui uno strumento posseduto non ha quotazione non vale zero, e trattarlo
    come tale farebbe sembrare quel mese una perdita totale.
    """

    giorno: date
    valore: Decimal | None


@dataclass(frozen=True)
class Flusso:
    """Denaro che entra (positivo) o esce (negativo) dal portafoglio.

    Comprare non e' un flusso: sposta denaro da contante a titoli dentro lo
    stesso portafoglio. Un dividendo nemmeno, se resta sul conto: e' proprio il
    rendimento che si sta misurando. Chi costruisce i flussi decide, e la
    differenza e' la cosa che si sbaglia piu' facilmente di tutte.
    """

    giorno: date
    importo: Decimal


@dataclass(frozen=True)
class Rendimento:
    """Un valore, oppure il motivo per cui non c'e'. Mai un valore inventato."""

    valore: Decimal | None
    motivo: str | None


def _quantizza(valore: Decimal) -> Decimal:
    return valore.quantize(QUATTRO_DECIMALI)


def twr(valutazioni: Sequence[Valutazione], flussi: Iterable[Flusso] = ()) -> Rendimento:
    """Il rendimento del portafoglio, concatenando i periodi.

    I periodi sono quelli fra una valutazione e la successiva: le date le sceglie
    chi chiama. Un flusso pesa la frazione di periodo in cui e' rimasto
    investito, ``(fine - giorno_del_flusso) / giorni``: un flusso del giorno 10
    di un periodo di 30 giorni pesa 21/30, perche' i giorni dal 10 alla fine -
    il giorno del flusso compreso - sono il tempo in cui quel denaro ha potuto
    rendere. Senza una convenzione dichiarata due implementazioni dello stesso
    numero divergono, e nessuna delle due ha torto.
    """
    punti = sorted(valutazioni, key=lambda punto: punto.giorno)
    if len(punti) < 2:
        return Rendimento(None, STORIA_TROPPO_CORTA)
    if any(punto.valore is None for punto in punti):
        # Un buco non si riempie con il valore del mese prima: quel mese
        # risulterebbe piatto, e la differenza finirebbe tutta nel mese dopo.
        return Rendimento(None, PREZZO_MANCANTE)
    ordinati = sorted(flussi, key=lambda flusso: flusso.giorno)
    catena = UNO
    for inizio, fine in zip(punti, punti[1:]):
        giorni = Decimal((fine.giorno - inizio.giorno).days)
        if giorni <= ZERO:
            continue
        dentro = [flusso for flusso in ordinati if inizio.giorno < flusso.giorno <= fine.giorno]
        totale = sum((flusso.importo for flusso in dentro), ZERO)
        pesato = sum((flusso.importo * Decimal((fine.giorno - flusso.giorno).days) / giorni
                      for flusso in dentro), ZERO)
        denominatore = inizio.valore + pesato
        # Denominatore zero vuol dire portafoglio vuoto e nessun flusso: non c'e'
        # niente da misurare, e non e' un rendimento a zero.
        if denominatore <= ZERO:
            continue
        catena *= UNO + (fine.valore - inizio.valore - totale) / denominatore
    return Rendimento(_quantizza(catena - UNO), None)
