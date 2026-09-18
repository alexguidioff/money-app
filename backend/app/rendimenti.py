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
CENTO = Decimal("100")
# Le curve del confronto si leggono a occhio: due decimali bastano, e tenerne
# di piu' darebbe l'idea di una precisione che il metodo non ha.
CENTESIMI = Decimal("0.01")

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
NESSUN_FLUSSO = "nessun_flusso"
FLUSSI_SENZA_CAMBIO_DI_SEGNO = "flussi_senza_cambio_di_segno"
XIRR_NON_CONVERGE = "xirr_non_converge"

# Dove si cerca il tasso: sotto -99% il valore attuale esplode, e sopra il 1000%
# annuo non c'e' nessun investimento reale da descrivere.
XIRR_MINIMO = -0.99
XIRR_MASSIMO = 10.0
# Cento dimezzamenti portano l'intervallo sotto qualunque tolleranza; il numero
# e' un tetto, non un lavoro: il ciclo esce prima.
XIRR_ITERAZIONI = 100
XIRR_TOLLERANZA = 1e-7
# I giorni divisi per 365, come fa il XIRR di un foglio di calcolo: e' la
# convenzione che rende confrontabile questo numero con quello che l'utente
# puo' essersi calcolato altrove.
GIORNI_ANNO = 365.0


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


def catena(valutazioni: Sequence[Valutazione], flussi: Iterable[Flusso] = ()) -> list[Decimal] | None:
    """Il valore cumulato di un euro investito all'inizio, a ogni valutazione.

    Il primo vale 1 per definizione, l'ultimo meno uno e' il TWR. La catena
    intera serve al confronto con un indice: due curve che partono dallo stesso
    100 nello stesso mese si leggono una sopra l'altra, mentre un guadagno in
    euro e una quotazione in punti indice non si leggono affatto.

    ``None`` quando non e' calcolabile, per le stesse due ragioni di ``twr``:
    meno di due valutazioni non sono un periodo, e un valore mancante non si
    riempie con quello del mese prima - quel mese risulterebbe piatto e la
    differenza finirebbe tutta nel mese dopo.
    """
    punti = sorted(valutazioni, key=lambda punto: punto.giorno)
    if len(punti) < 2 or any(punto.valore is None for punto in punti):
        return None
    ordinati = sorted(flussi, key=lambda flusso: flusso.giorno)
    fattori = [UNO]
    for inizio, fine in zip(punti, punti[1:]):
        giorni = Decimal((fine.giorno - inizio.giorno).days)
        if giorni <= ZERO:
            fattori.append(fattori[-1])
            continue
        dentro = [flusso for flusso in ordinati if inizio.giorno < flusso.giorno <= fine.giorno]
        totale = sum((flusso.importo for flusso in dentro), ZERO)
        pesato = sum((flusso.importo * Decimal((fine.giorno - flusso.giorno).days) / giorni
                      for flusso in dentro), ZERO)
        denominatore = inizio.valore + pesato
        # Denominatore zero vuol dire portafoglio vuoto e nessun flusso: non c'e'
        # niente da misurare, e non e' un rendimento a zero. Il fattore resta
        # quello di prima, come per un periodo di durata nulla.
        if denominatore <= ZERO:
            fattori.append(fattori[-1])
            continue
        fattori.append(fattori[-1] * (UNO + (fine.valore - inizio.valore - totale) / denominatore))
    return fattori


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
    fattori = catena(valutazioni, flussi)
    if fattori is None:
        punti = sorted(valutazioni, key=lambda punto: punto.giorno)
        return Rendimento(None, STORIA_TROPPO_CORTA if len(punti) < 2 else PREZZO_MANCANTE)
    return Rendimento(_quantizza(fattori[-1] - UNO), None)


def da_cento(valori: Sequence[Decimal]) -> list[Decimal]:
    """La serie riportata a 100 al primo valore.

    E' quello che rende confrontabili due curve: cento euro investiti all'inizio
    e cento punti di indice, letti insieme, dicono chi ha reso di piu'. Vuota
    resta vuota, e una serie che parte da zero non si riporta a niente.
    """
    if not valori or valori[0] <= ZERO:
        return []
    return [(CENTO * valore / valori[0]).quantize(CENTESIMI) for valore in valori]


def xirr(flussi: Sequence[Flusso], finale: Valutazione) -> Rendimento:
    """Il tasso annuo che annulla il valore attuale dei flussi.

    ``finale`` e' il valore del portafoglio all'ultima data, e va contato come
    l'incasso di chi e' rimasto investito fino in fondo: senza, il tasso
    descriverebbe un investimento che non ha mai restituito niente.

    Il segno dei flussi si ribalta qui dentro: un versamento entra nel
    portafoglio, quindi esce dalla tasca di chi investe. E' l'unico punto in cui
    il segno conta davvero, perche' il tasso esiste solo se il denaro esce e
    rientra almeno una volta.

    ponytail: bisezione invece di Newton. Piu' iterazioni, nessuna derivata, e
    soprattutto non diverge: su flussi irregolari Newton restituisce numeri
    assurdi con l'aria di aver funzionato, e un tasso sbagliato e' peggio di un
    motivo scritto per esteso.
    """
    if finale.valore is None:
        return Rendimento(None, PREZZO_MANCANTE)
    # Un flusso datato dopo la valutazione finale non fa parte di questo
    # rendimento: il periodo finisce dove finisce l'ultima valutazione.
    dentro = sorted((flusso for flusso in flussi if flusso.giorno <= finale.giorno),
                    key=lambda flusso: flusso.giorno)
    if not dentro:
        return Rendimento(None, NESSUN_FLUSSO)
    if not any(flusso.importo > ZERO for flusso in dentro) or finale.valore <= ZERO:
        # Solo uscite, o nessun valore finale: il denaro esce e non rientra mai,
        # e un tasso non esiste. Non e' un caso di scuola: e' un portafoglio
        # svuotato, o una serie di prelievi senza piu' niente dentro.
        return Rendimento(None, FLUSSI_SENZA_CAMBIO_DI_SEGNO)
    # Da qui in poi `float`, contro la regola del progetto, per la stessa
    # ragione per cui lo fa `fire_montecarlo`: cento iterazioni su decine di
    # flussi con `Decimal` a quaranta cifre costano molto per un tasso che si
    # mostra con quattro decimali, e la precisione in piu' non sposta la quarta
    # cifra. Il `Decimal` torna in uscita, sul numero che finisce in pagina.
    base = min(flusso.giorno for flusso in dentro + [finale])
    periodi = [((flusso.giorno - base).days / GIORNI_ANNO, -float(flusso.importo)) for flusso in dentro]
    periodi.append(((finale.giorno - base).days / GIORNI_ANNO, float(finale.valore)))

    def valore_attuale(tasso: float) -> float:
        return sum((importo / (1.0 + tasso) ** anni for anni, importo in periodi), 0.0)

    basso, alto = XIRR_MINIMO, XIRR_MASSIMO
    if valore_attuale(basso) * valore_attuale(alto) > 0.0:
        # Nessun cambio di segno nell'intervallo: non c'e' una radice da
        # trovare, e restituire l'estremo piu' vicino sarebbe un numero
        # inventato con l'aria di essere la risposta.
        return Rendimento(None, XIRR_NON_CONVERGE)
    for _ in range(XIRR_ITERAZIONI):
        mezzo = (basso + alto) / 2.0
        if valore_attuale(mezzo) > 0.0:
            basso = mezzo
        else:
            alto = mezzo
        if alto - basso < XIRR_TOLLERANZA:
            break
    return Rendimento(_quantizza(Decimal(str((basso + alto) / 2.0))), None)
