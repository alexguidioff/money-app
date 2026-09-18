"""La larghezza del futuro: molte traiettorie invece di una sola.

Il piano deterministico dice cosa succede **se** il rendimento e' quello, ogni
anno. Ma il rischio che una persona deve conoscere non e' la media dei
rendimenti, e' la loro *sequenza*: due portafogli con la stessa media arrivano
in posti diversi se i primi anni di prelievo vanno male, perche' una perdita
all'inizio lascia meno capitale a lavorare dopo. Un rendimento costante non
produce mai una sequenza sfortunata, quindi non puo' dirlo.

Qui il rendimento di ogni anno viene estratto attorno alla media dichiarata nel
profilo, con la larghezza dichiarata nel profilo. Il risultato non e' una cifra
ma una distribuzione: la quota di percorsi che non esauriscono il capitale, e i
percentili del saldo anno per anno.

**Perche' `float` qui, contro la regola del progetto.** Misurato su questa
macchina, 5.000 percorsi x 63 anni: `float` 0,25 s con i percentili dentro,
`Decimal` 0,94 s per il solo ciclo, senza contarli. Sei volte tanto, a ogni
apertura della pagina FIRE, per un risultato identico. E la precisione non
serve a niente qui: su 5.000 percorsi l'errore di campionamento sulla
percentuale di successo e' gia' di circa un punto, mille volte piu' grande di
qualunque arrotondamento. `Decimal` protegge i saldi che l'utente deve far
quadrare al centesimo; un percentile non e' quel saldo. Il `Decimal` torna
**in uscita**, sui punti che finiscono nella risposta dell'API.

Questo modulo non tocca `fire_engine`: il motore deterministico resta l'unico
che calcola il piano, e le rendite - pensioni, stime anticipate, erosione
dell'inflazione - arrivano da li' gia' calcolate, eta' per eta'.
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from decimal import Decimal
from statistics import median_low

CENT = Decimal("0.01")

# I percentili che la pagina disegna e legge.
PERCENTILI = (10, 50, 90)

# Il rendimento di un anno non scende sotto -95%. Una gaussiana con media 6% e
# sigma 20% produce code sotto -100% abbastanza spesso da capitarci in 5.000
# percorsi, e un fattore negativo ribalterebbe il saldo: un saldo negativo
# moltiplicato per un rendimento positivo risalirebbe dal nulla.
RENDIMENTO_MINIMO = -0.95

# Il numero di percorsi predefinito: sotto i 100 la percentuale di successo e'
# rumore, sopra i 20.000 la pagina aspetta senza guadagnare precisione utile.
PERCORSI_PREDEFINITI = 5000


@dataclass(frozen=True)
class PuntoMC:
    """Il saldo a inizio anno, un percentile alla volta.

    E' il gemello di `PuntoFire` ma senza i flussi: qui interessa solo dove
    arriva il capitale, non come ci e' arrivato.
    """

    eta: int
    capitale: Decimal


@dataclass(frozen=True)
class EsitoMonteCarlo:
    successo: Decimal          # quota di percorsi che reggono, fra 0 e 1
    percorsi: int
    percentili: dict[int, tuple[PuntoMC, ...]]   # {10: serie, 50: serie, 90: serie}
    eta_esaurimento_mediana: int | None          # fra i percorsi falliti, None se nessuno fallisce


def _seme(capitale: float, spese_annue: float, versamenti_annui: float,
          eta_oggi: int, eta_ritiro: int, eta_fine: int,
          rendimento_medio: float, volatilita: float, seme: int) -> int:
    """Il seme ricavato dal piano: stesso piano, stessi numeri.

    `hash()` di Python non va bene qui: e' randomizzato a ogni avvio del
    processo, e lo stesso piano darebbe una percentuale diversa a ogni riavvio
    dell'API senza che niente sia cambiato. `crc32` no.

    `percorsi` non entra nella firma: allungare la simulazione deve raffinare
    la stima, non cambiare i percorsi gia' estratti.
    """
    firma = "|".join(repr(valore) for valore in (
        capitale, spese_annue, versamenti_annui, eta_oggi, eta_ritiro, eta_fine,
        rendimento_medio, volatilita, seme))
    return zlib.crc32(firma.encode("utf-8"))


def _percentile(ordinati: list[float], quota: float) -> float:
    """Il percentile con interpolazione fra i due valori vicini.

    Monotono in `quota`: e' quello che garantisce p10 <= p50 <= p90 ogni anno,
    che con un arrotondamento all'indice vicino non sarebbe garantito.
    """
    posizione = (len(ordinati) - 1) * quota
    basso = int(posizione)
    alto = min(basso + 1, len(ordinati) - 1)
    return ordinati[basso] + (ordinati[alto] - ordinati[basso]) * (posizione - basso)


def simula(capitale: float, spese_annue: float, versamenti_annui: float,
           entrate_per_eta: dict[int, float], eta_oggi: int, eta_ritiro: int,
           eta_fine: int, rendimento_medio: float, volatilita: float,
           aliquota_prelievo: float, percorsi: int = PERCORSI_PREDEFINITI,
           seme: int = 0) -> EsitoMonteCarlo:
    """Il ciclo di accumulo e prelievo ripetuto `percorsi` volte.

    `entrate_per_eta` sono le rendite gia' calcolate dal motore deterministico,
    eta' per eta': non si ricalcolano qui. Il rischio di sequenza riguarda i
    rendimenti del capitale, non le pensioni, e una seconda implementazione dei
    flussi prima o poi divergerebbe dalla prima - e' l'errore che la tabella
    della leva racconta di aver gia' fatto una volta.

    `spese_annue` sono quelle del ritiro (il motore le chiama cosi'). Il ciclo
    e' quello di `_calcola` in `fire_engine`, con il rendimento estratto invece
    che costante.
    """
    if not 0.0 <= volatilita <= 1.0:
        raise ValueError("volatilita")
    if not 100 <= percorsi <= 20000:
        raise ValueError("percorsi")
    # Si divide per (1 - aliquota): un valore assoluto pari a uno non e' un
    # piano, e' una divisione per zero con un traceback invece di un 422.
    if not 0.0 <= aliquota_prelievo < 1.0:
        raise ValueError("aliquota_prelievo")
    if not eta_oggi <= eta_ritiro <= eta_fine:
        raise ValueError("orizzonte")

    netto = 1.0 - aliquota_prelievo
    # Una sola istanza per l'intera simulazione, non una per percorso: un
    # `Random` nuovo con semi vicini produce sequenze correlate, e i percorsi
    # smetterebbero di essere indipendenti.
    rnd = random.Random(_seme(capitale, spese_annue, versamenti_annui, eta_oggi,
                              eta_ritiro, eta_fine, rendimento_medio, volatilita, seme))
    saldi: list[list[float]] = []
    esaurimenti: list[int] = []
    successi = 0
    for _ in range(percorsi):
        saldo = capitale
        percorso: list[float] = []
        esaurito = False
        for eta in range(eta_oggi, eta_fine + 1):
            # ponytail: rendimenti estratti indipendenti da una gaussiana.
            # Ignora le code spesse e il ritorno alla media dei mercati veri.
            # Se un giorno c'e' una serie storica di rendimenti in archivio, si
            # passa al bootstrap storico e cambia solo questa riga.
            r = max(rnd.gauss(rendimento_medio, volatilita), RENDIMENTO_MINIMO)
            rendite = entrate_per_eta.get(eta, 0.0)
            spesa = spese_annue if eta >= eta_ritiro else 0.0
            versato = versamenti_annui if eta < eta_ritiro else 0.0
            prelievo = max(0.0, spesa - rendite) / netto
            avanzo = max(0.0, rendite - spesa)
            disponibile = saldo + versato + avanzo
            percorso.append(saldo)
            if prelievo > disponibile and not esaurito:
                esaurito = True
                esaurimenti.append(eta)
            saldo = max(0.0, disponibile - prelievo) * (1.0 + r)
        saldi.append(percorso)
        if not esaurito:
            successi += 1

    # I percentili si calcolano anno per anno sul saldo, non scegliendo il
    # percorso mediano: la curva p10 e' l'insieme dei decimi percentili
    # annuali, non una singola storia sfortunata. Nessun percorso reale la segue.
    percentili: dict[int, list[PuntoMC]] = {quota: [] for quota in PERCENTILI}
    for passo, eta in enumerate(range(eta_oggi, eta_fine + 1)):
        ordinati = sorted(percorso[passo] for percorso in saldi)
        for quota in PERCENTILI:
            valore = Decimal(str(_percentile(ordinati, quota / 100))).quantize(CENT)
            percentili[quota].append(PuntoMC(eta, valore))

    return EsitoMonteCarlo(
        successo=Decimal(successi) / Decimal(percorsi),
        percorsi=percorsi,
        percentili={quota: tuple(serie) for quota, serie in percentili.items()},
        eta_esaurimento_mediana=median_low(esaurimenti) if esaurimenti else None,
    )
