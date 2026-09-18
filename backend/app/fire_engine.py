"""Contratto FIRE per l'API: importi netti in euro di oggi, tassi frazionari.

Nessun accesso a DB, calendario di sistema o regole pensionistiche nazionali.
I flussi futuri sono espressi in potere d'acquisto alla prima erogazione; per
quelli gia' attivi l'importo e' quello di oggi. L'inflazione erode solo le
rendite non indicizzate, dalla prima erogazione (o da oggi se gia' attive).
I capitali una tantum sono importi reali alla data di disponibilita'.

Convenzione annuale prudente: flussi, versamenti e prelievi a inizio anno,
rendimento dopo. I versamenti sono risparmio netto del lavoro, esclusi i
flussi qui elencati. L'aliquota dichiarata si applica all'intero prelievo:
non si inventano costo fiscale, plusvalenze o tasse nazionali.

Il ponte numerico finanzia la transizione fino al regime di tutti i flussi,
anche quando la fase visiva ponte finisce al primo flusso. Il rabbocco e' la
riserva perpetua residua / SWR, attualizzata e riconciliata con la transizione.
Per rendite non indicizzate con inflazione positiva, si modellano i pagamenti
fino all'orizzonte dichiarato e poi si escludono dalla riserva perpetua: e'
una scelta conservativa ESPLICITA, non una promessa di precisione infinita.

Il capitale necessario e la serie sono scenari deterministici, non probabilita'
di successo: SWR non e' una garanzia e un rendimento costante non modella il
rischio di sequenza. Il grafico richiede scenari separati o una didascalia.

Integrazione: anno_oggi arriva dall'API. Se assente, restano disponibili eta'
e anni mancanti, senza inventare anni di calendario. Lean richiede spese
esplicite. Inflazione obbligatoria per ogni rendita non indicizzata.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

D = Decimal
ZERO = D(0)
ONE = D(1)
CENT = D('0.01')
# Fin dove proietta il motore, in eta'. Non e' la fine del grafico (piu' corta):
# e' l'orizzonte che la simulazione deve copiare per risultare uguale al motore
# quando la volatilita' e' zero.
ETA_FINE_PROIEZIONE = 100


@dataclass(frozen=True)
class Flusso:
    nome: str
    tipo: Literal['annuity', 'capital']
    importo: Decimal
    eta_inizio: int
    indicizzato: bool = True
    paese: str = ''
    # Solo stime fornite dall'utente: importo resta la stima se continua.
    importo_se_smetti_oggi: Decimal | None = None


@dataclass(frozen=True)
class StimaPensione:
    importo_annuo: Decimal
    perdita_annua: Decimal
    costo_annuo_per_anno_anticipo: Decimal
    anni_anticipo: int


@dataclass(frozen=True)
class Fase:
    tipo: Literal['accumulo', 'ponte', 'pensione']
    eta_inizio: int
    eta_fine: int | None
    anno_inizio: int | None
    anno_fine: int | None


@dataclass(frozen=True)
class PuntoFire:
    eta: int
    anno: int | None
    capitale_necessario: Decimal
    capitale: Decimal  # Saldo a inizio anno, prima dei flussi di quell'anno.
    rendite: Decimal
    capitali: Decimal
    versamenti: Decimal
    spese: Decimal
    prelievo_lordo: Decimal
    deficit: Decimal  # Spese non finanziabili; non si simulano prestiti impliciti.
    capitale_fine: Decimal


@dataclass(frozen=True)
class Traguardo:
    capitale_necessario: Decimal | None
    raggiunto: bool | None


@dataclass(frozen=True)
class PianoFire:
    fasi: tuple[Fase, ...]
    capitale_necessario: Decimal
    capitale_ponte: Decimal
    capitale_rabbocco: Decimal
    eta_regime: int
    eta_raggiungimento: int | None
    anno_raggiungimento: int | None
    anni_mancanti: int | None
    eta_esaurimento: int | None
    serie: tuple[PuntoFire, ...]
    traguardi: dict[str, Traguardo]
    pensioni: dict[str, StimaPensione]
    avvertenze: tuple[str, ...]


def _numero(valore: Decimal, nome: str, minimo: Decimal = ZERO) -> None:
    if not isinstance(valore, Decimal) or not valore.is_finite() or valore < minimo:
        raise ValueError(nome)


def _eta(valore: int) -> None:
    if type(valore) is not int or not 0 <= valore <= 150:
        raise ValueError('eta')


def stima_pensione_anticipata(pensione_se_smetti_oggi: Decimal,
                              pensione_se_continui: Decimal,
                              eta_oggi: int, eta_pensione: int,
                              eta_ritiro: int) -> StimaPensione:
    """Interpolazione lineare delle due stime, non calcolo previdenziale."""
    for valore in (pensione_se_smetti_oggi, pensione_se_continui):
        _numero(valore, 'pensione')
    for eta in (eta_oggi, eta_pensione, eta_ritiro):
        _eta(eta)
    if eta_ritiro < eta_oggi or eta_pensione <= eta_oggi:
        raise ValueError('intervallo_pensione')
    if pensione_se_continui < pensione_se_smetti_oggi:
        raise ValueError('stime_pensione_invertite')
    anni = max(0, eta_pensione - eta_ritiro)
    costo = (pensione_se_continui - pensione_se_smetti_oggi) / D(eta_pensione - eta_oggi)
    perdita = costo * anni
    return StimaPensione((pensione_se_continui - perdita).quantize(CENT),
                         perdita.quantize(CENT), costo.quantize(CENT), anni)


def piano_fire(capitale: Decimal, spese_annue: Decimal,
               flussi: list[Flusso], eta_oggi: int, eta_ritiro: int,
               rendimento_reale: Decimal, swr: Decimal,
               aliquota_prelievo: Decimal, *,
               versamenti_annui: Decimal = ZERO,
               spese_annue_ritiro: Decimal | None = None,
               inflazione: Decimal | None = None,
               anno_oggi: int | None = None,
               eta_fine_proiezione: int = ETA_FINE_PROIEZIONE,
               spese_lean_annue: Decimal | None = None) -> PianoFire:
    for nome, valore in (('capitale', capitale), ('spese_annue', spese_annue),
                          ('versamenti_annui', versamenti_annui), ('swr', swr),
                          ('aliquota_prelievo', aliquota_prelievo)):
        _numero(valore, nome)
    _numero(rendimento_reale, 'rendimento_reale', D('-1'))
    if rendimento_reale <= -1 or rendimento_reale > 1 or not ZERO < swr <= ONE or aliquota_prelievo >= 1:
        raise ValueError('tassi_fuori_intervallo')
    for eta in (eta_oggi, eta_ritiro, eta_fine_proiezione):
        _eta(eta)
    if not eta_oggi <= eta_ritiro < eta_fine_proiezione:
        raise ValueError('orizzonte')
    if anno_oggi is not None and (type(anno_oggi) is not int or not 1 <= anno_oggi <= 9800):
        raise ValueError('anno_oggi')
    spese = spese_annue if spese_annue_ritiro is None else spese_annue_ritiro
    _numero(spese, 'spese_annue_ritiro')
    if spese_lean_annue is not None:
        _numero(spese_lean_annue, 'spese_lean_annue')
        if spese_lean_annue > spese:
            raise ValueError('lean_superiore_alle_spese')
    if inflazione is not None:
        _numero(inflazione, 'inflazione')
        if inflazione > 1:
            raise ValueError('inflazione')
    nomi = set()
    for f in flussi:
        if not isinstance(f, Flusso) or not f.nome.strip() or f.nome in nomi:
            raise ValueError('flusso_o_nome_duplicato')
        nomi.add(f.nome)
        _numero(f.importo, 'importo_flusso')
        _eta(f.eta_inizio)
        if f.tipo not in ('annuity', 'capital') or type(f.indicizzato) is not bool:
            raise ValueError('tipo_flusso')
        if f.tipo == 'annuity' and not f.indicizzato and inflazione is None:
            raise ValueError('inflazione_richiesta')
        if f.importo_se_smetti_oggi is not None:
            if f.tipo != 'annuity':
                raise ValueError('stima_solo_per_rendita')
            stima_pensione_anticipata(f.importo_se_smetti_oggi, f.importo,
                                     eta_oggi, f.eta_inizio, eta_ritiro)
    with localcontext() as ctx:
        # Si arrotonda soltanto l'uscita: gli arrotondamenti annuali accumulati
        # sposterebbero artificiosamente l'anno di indipendenza finanziaria.
        ctx.prec = 40
        return _calcola(capitale, spese, flussi, eta_oggi, eta_ritiro,
                        rendimento_reale, swr, aliquota_prelievo, versamenti_annui,
                        inflazione or ZERO, anno_oggi, eta_fine_proiezione, spese_lean_annue)


def _calcola(capitale, spese, flussi, oggi, ritiro, rendimento, swr, tasse,
             versamenti, inflazione, anno_oggi, fine, lean):
    fattore = ONE + rendimento
    netto = ONE - tasse
    erosi = any(f.tipo == 'annuity' and not f.indicizzato and inflazione > 0
                and f.importo > 0 for f in flussi)

    def anno(eta):
        return None if anno_oggi is None or eta is None else anno_oggi + eta - oggi

    def importo(f, pensionamento):
        if f.importo_se_smetti_oggi is None:
            return f.importo
        return stima_pensione_anticipata(f.importo_se_smetti_oggi, f.importo,
                                         oggi, f.eta_inizio, pensionamento).importo_annuo

    def entrate(eta, pensionamento):
        rendite = capitali = ZERO
        for f in flussi:
            if f.tipo == 'capital':
                # Un capitale gia' ricevuto appartiene al saldo iniziale.
                if eta == f.eta_inizio:
                    capitali += f.importo
            elif eta >= f.eta_inizio:
                valore = importo(f, pensionamento)
                if not f.indicizzato:
                    valore /= (ONE + inflazione) ** (eta - max(oggi, f.eta_inizio))
                rendite += valore
        return rendite, capitali

    def fabbisogno(pensionamento, budget, stima_ritiro=None):
        stima_ritiro = pensionamento if stima_ritiro is None else stima_ritiro
        # Tutti gli eventi futuri devono entrare prima della riserva perpetua;
        # l'orizzonte grafico non puo' far sparire un capitale tardivo.
        regime = max([pensionamento, fine if erosi else pensionamento]
                     + [f.eta_inizio + (f.tipo == 'capital') for f in flussi
                        if f.importo > 0 and f.eta_inizio >= pensionamento])
        perpetue = sum((importo(f, stima_ritiro) for f in flussi
                        if f.tipo == 'annuity' and (f.indicizzato or inflazione == 0)), ZERO)
        riserva = max(ZERO, budget - perpetue) / netto / swr
        totale, ponte = riserva, ZERO
        for eta in range(regime - 1, pensionamento - 1, -1):
            rendite, capitali = entrate(eta, stima_ritiro)
            fabbisogno = max(ZERO, budget - rendite) / netto
            surplus = max(ZERO, rendite - budget)
            movimento = fabbisogno - surplus - capitali
            totale = max(ZERO, movimento + totale / fattore)
            ponte = max(ZERO, movimento + ponte / fattore)
        return totale, ponte, regime

    necessario, ponte, regime = fabbisogno(ritiro, spese)
    coast = necessario
    for eta in range(ritiro - 1, oggi - 1, -1):
        rendite, capitali = entrate(eta, ritiro)
        coast = max(ZERO, coast / fattore - rendite - capitali)

    # La data FI risponde a "se continuo a risparmiare": e' distinta dalla
    # serie che applica davvero la data di ritiro scelta, anche se prematura.
    accumulato = capitale
    raggiungimento = None
    for eta in range(oggi, fine + 1):
        if accumulato >= fabbisogno(eta, spese)[0]:
            raggiungimento = eta
            break
        rendite, capitali = entrate(eta, eta + 1)
        accumulato = (accumulato + versamenti + rendite + capitali) * fattore

    serie = []
    saldo, esaurimento = capitale, None
    for eta in range(oggi, fine):
        rendite, capitali = entrate(eta, ritiro)
        spesa = spese if eta >= ritiro else ZERO
        versato = versamenti if eta < ritiro else ZERO
        prelievo = max(ZERO, spesa - rendite) / netto
        avanzo = max(ZERO, rendite - spesa)
        disponibile = saldo + capitali + versato + avanzo
        deficit = max(ZERO, prelievo - disponibile)
        if deficit > ZERO and esaurimento is None:
            esaurimento = eta
        finale = max(ZERO, disponibile - prelievo) * fattore
        serie.append(PuntoFire(eta, anno(eta), fabbisogno(eta, spese, ritiro)[0].quantize(CENT), saldo.quantize(CENT), rendite.quantize(CENT),
                                capitali.quantize(CENT), versato.quantize(CENT), spesa.quantize(CENT),
                                prelievo.quantize(CENT), deficit.quantize(CENT), finale.quantize(CENT)))
        saldo = finale
    primo_flusso = min((max(ritiro, f.eta_inizio) for f in flussi if f.importo > 0
                        and (f.tipo == 'annuity' or f.eta_inizio >= ritiro)), default=None)
    fasi = (Fase('accumulo', oggi, ritiro, anno(oggi), anno(ritiro)),
            Fase('ponte', ritiro, primo_flusso, anno(ritiro), anno(primo_flusso)))
    if primo_flusso is not None:
        fasi += (Fase('pensione', primo_flusso, None, anno(primo_flusso), None),)

    def traguardo(valore):
        return Traguardo(None if valore is None else valore.quantize(CENT),
                          None if valore is None else capitale >= valore)

    warnings = ['scenario_non_previsione', 'swr_non_garantisce_solvibilita']
    if erosi:
        warnings.append('rendite_non_indicizzate_escluse_dopo_regime')
    if esaurimento is not None:
        warnings.append('capitale_insufficiente_nella_proiezione')
    pensioni = {f.nome: stima_pensione_anticipata(f.importo_se_smetti_oggi, f.importo,
                                                oggi, f.eta_inizio, ritiro)
               for f in flussi if f.importo_se_smetti_oggi is not None}
    if pensioni:
        warnings.append('pensione_interpolata_non_stima_ente')
    necessario = necessario.quantize(CENT)
    ponte = ponte.quantize(CENT)
    return PianoFire(fasi, necessario, ponte, necessario - ponte, regime,
                     raggiungimento, anno(raggiungimento),
                     None if raggiungimento is None else raggiungimento - oggi,
                     esaurimento, tuple(serie),
                     {'coast': traguardo(coast),
                      'solo_ponte': traguardo(fabbisogno(oggi, spese)[1]),
                      'lean': traguardo(None if lean is None else fabbisogno(oggi, lean)[0]),
                      'fi': traguardo(fabbisogno(oggi, spese)[0])},
                     pensioni, tuple(warnings))
