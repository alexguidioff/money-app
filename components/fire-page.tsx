'use client';

import { useCallback, useEffect, useState } from 'react';
import { Flame, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { TabStrip } from '@/components/ui/tab-strip';
import { useI18n } from '@/lib/i18n-context';
import { FireChart, type FireChartPoint } from '@/components/fire-chart';
import { MilestonesCard } from '@/components/fire/milestones-card';
import { MonteCarloCard, type MonteCarloPayload } from '@/components/fire/montecarlo-card';
import { LeverageTable, type Leverage } from '@/components/fire/leverage-table';
import { AgeShiftSlider } from '@/components/fire/age-shift-slider';
import { FireSettingsSection } from '@/components/settings/fire-settings-section';

/**
 * Quanto capitale serve, e quando arriva.
 *
 * Il numero non e' `spese × 25`: con una pensione pubblica che parte a un'eta'
 * fissa il capitale deve finanziare il **ponte** fra il giorno in cui smetti e
 * quello in cui la pensione comincia, piu' l'eventuale differenza fra spese e
 * pensione. Per chi vive in Europa il secondo termine e' spesso zero, e il
 * numero cala anche della meta' rispetto alla regola del 4%.
 *
 * Il capitale necessario **dipende dall'eta' di ritiro**: a trentasei anni il
 * ponte e' lungo, a cinquanta e' corto. Il numero grande e' quello per l'eta'
 * scelta; la curva accanto porta il valore anno per anno, ed e' quella che
 * dice quando le due si incontrano.
 *
 * Senza nessun flusso che parte dopo il ritiro il ponte e' vuoto, e il capitale
 * torna a essere la riserva perpetua - spese diviso prelievo - uguale a
 * qualunque eta': e' il caso che la tabella delle leve dichiara invece di
 * disegnarlo come se fosse una risposta.
 *
 * Due schede: il piano, e i dati che lo producono. Stanno insieme perche' sono
 * la stessa domanda vista dai due lati - chi cambia il profilo vuole vedere
 * subito cosa succede al numero grande, e chi guarda il numero vuole poterlo
 * correggere dove e' nato.
 *
 * La seconda scheda non si chiama "Impostazioni": quel nome ce l'ha gia' la
 * voce della barra laterale, e due cose diverse con lo stesso nome in due punti
 * dello schermo si cercano nel posto sbagliato. Si chiama come quello che c'e'
 * dentro, e il riquadro "non configurato" manda li'.
 */

type Milestone = { capitalNeeded: number | null; reached: boolean | null };
type SeriePunto = { age: number; year: number | null; capital: number; capitalNeeded: number };

export type FirePlan = {
  phases: Array<{ kind: string; fromAge: number; toAge: number | null; fromYear: number | null; toYear: number | null }>;
  capitalNeeded: number;
  bridgeCapital: number;
  topUpCapital: number;
  reachedAtAge: number | null;
  reachedInYear: number | null;
  yearsLeft: number | null;
  depletedAtAge: number | null;
  leverage: Leverage[];
  leanCapped: boolean;
  series: SeriePunto[];
  history: Array<{ year: number; capital: number }>;
  // p10 e p90: i percentili della simulazione, non due scenari a rendimento
  // spostato. Le eta' sono le stesse della serie centrale.
  scenarios: Record<string, Array<{ age: number; year: number | null; capital: number }>>;
  monteCarlo: MonteCarloPayload;
  milestones: Record<string, Milestone>;
  warnings: string[];
};

export type FireData = {
  configured: boolean;
  age?: number;
  netWorth: number;
  expensesUsed?: number;
  retirementExpenses?: number;
  savingsRate?: number;
  annualSavings?: number;
  profile?: { withdrawalRate: number; realReturn: number; country: string; leanAnnualExpenses: number | null };
  plan: FirePlan | null;
};

const ETICHETTE_FASE: Record<string, string> = {
  accumulo: 'firePhaseAccumulation', ponte: 'firePhaseBridge', pensione: 'firePhasePension',
};

/**
 * L'ultimo piano calcolato, e quando.
 *
 * `/api/fire` non e' una lettura: il motore rifa' il piano, le quattro leve
 * (cinque righe ciascuna) e la simulazione su 5000 percorsi - quasi un secondo
 * di server. Uscire dalla sezione smonta questa pagina, quindi ogni rientro lo
 * rifaceva da capo per mostrare gli stessi numeri: qui resta l'ultima risposta,
 * e il rientro la mostra subito.
 *
 * Sta fuori dal componente proprio perche' il componente si smonta, e ha
 * addosso la persona: la memoria del modulo sopravvive al cambio di account -
 * uscire non ricarica la pagina - e senza quel nome i numeri di uno
 * finirebbero sotto gli occhi dell'altro.
 *
 * Non e' una verita': e' quello che il server ha risposto. La riga "aggiornato
 * alle" dice da quando, e il tasto rifa' il conto quando si vuole.
 */
let memoria: { utente: number | null; dati: FireData; quando: number } | null = null;

/**
 * La richiesta gia' partita e non ancora risposta.
 *
 * Il piano e' lento, e la memoria sopra si riempie solo quando la risposta
 * arriva: uscire dalla sezione un istante dopo essere entrati e rientrare
 * faceva partire un secondo conto identico, perche' nel frattempo in memoria
 * non c'era ancora niente. Qui c'e' la richiesta in volo, e chi rientra aspetta
 * quella. Porta il nome della persona come la memoria: una richiesta partita
 * per uno non deve finire sotto gli occhi dell'altro.
 */
let inVolo: { utente: number | null; dati: Promise<FireData> } | null = null;

/** Il piano in memoria, ma solo se e' di questa persona. */
function daMemoria(utente: number | null) {
  return memoria && memoria.utente === utente ? memoria : null;
}

/** Le due schede: il piano, e i dati che lo producono. */
type Scheda = 'piano' | 'profilo';

export function FirePage({ apiUrl, utente }: { apiUrl: string; utente: number | null }) {
  const { t, formatEuro, formatCompactEuro, formatNumber, formatDate } = useI18n();
  const [dati, setDati] = useState<FireData | null>(() => daMemoria(utente)?.dati ?? null);
  const [quando, setQuando] = useState<number | null>(() => daMemoria(utente)?.quando ?? null);
  const [errore, setErrore] = useState(false);
  const [inCorso, setInCorso] = useState(false);
  const [scheda, setScheda] = useState<Scheda>('piano');

  const carica = useCallback(async () => {
    setInCorso(true);
    try {
      let volo = inVolo;
      if (volo?.utente !== utente) {
        const dati = fetch(`${apiUrl}/api/fire`).then(async (risposta) => {
          if (!risposta.ok) throw new Error('fire');
          return await risposta.json() as FireData;
        });
        volo = inVolo = { utente, dati };
        // Il posto si libera quando la risposta arriva, buona o cattiva: chi
        // rientra dopo non deve agganciarsi a una richiesta che non c'e' piu'.
        void dati.catch(() => undefined).finally(() => { if (inVolo === volo) inVolo = null; });
      }
      const nuovi = await volo.dati;
      // La memoria si aggiorna solo con una risposta buona: un errore di rete
      // non deve cancellare il piano che si sta guardando.
      memoria = { utente, dati: nuovi, quando: Date.now() };
      setDati(nuovi);
      setQuando(memoria.quando);
      setErrore(false);
    } catch { setErrore(true); } finally { setInCorso(false); }
  }, [apiUrl, utente]);

  // Si chiede al server solo quando in memoria non c'e' niente di buono: al
  // primo ingresso della pagina, e dopo un ritorno dalle impostazioni, che
  // azzera la memoria perche' li' si cambiano i dati che il piano usa.
  useEffect(() => {
    if (scheda !== 'piano') return;
    if (daMemoria(utente)) return;
    void carica();
  }, [scheda, carica, utente]);

  function cambiaScheda(nuova: Scheda) {
    // Tornando dal profilo il piano in memoria non vale piu': mostrare il
    // numero di prima accanto a un profilo appena salvato sarebbe la bugia
    // peggiore. Si rifa', e la riga della data sotto dice quando.
    // Anche la richiesta in volo cade per lo stesso motivo: e' partita prima
    // che il profilo cambiasse, ed e' il conto vecchio che sta arrivando.
    if (nuova === 'piano' && scheda === 'profilo') { memoria = null; inVolo = null; }
    setScheda(nuova);
  }

  function contenutoPiano() {
    // Senza piano da mostrare l'errore prende la pagina; se invece un piano
    // c'e' gia' (il ricalcolo non e' riuscito) resta a video, con una riga che
    // dice che e' quello di prima: toglierlo sarebbe perdere l'unica cosa che
    // l'utente stava guardando.
    if (errore && !dati) return <Card className="border-[var(--money-allarme-bordo)] bg-[var(--money-allarme-velo)] shadow-sm"><CardContent className="py-14 text-center"><p role="alert" className="text-sm text-[var(--money-allarme)]">{t('fireProfileLoadError')}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void carica()}>{t('retry')}</Button></CardContent></Card>;
    if (!dati) return <p className="py-16 text-center text-sm text-[var(--money-testo-tenue)]">{t('loading')}</p>;

    // Senza profilo non si mostra un piano costruito su ipotesi che nessuno ha
    // dichiarato: si chiede di compilarlo, e i dati si compilano qui accanto.
    if (!dati.configured || !dati.plan) {
      return <Card className="border-[var(--money-velo)]/6 bg-[var(--money-superficie)] shadow-sm"><CardContent className="py-14 text-center">
        <Flame className="mx-auto mb-3 size-8 text-[var(--money-testo-tenue)]" />
        <p className="text-sm text-[var(--money-marca)]">{t('fireNotConfigured')}</p>
        <Button className="mt-4 bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]" onClick={() => setScheda('profilo')}>{t('fireGoToSettings')}</Button>
      </CardContent></Card>;
    }

    const piano = dati.plan;
    const swr = dati.profile?.withdrawalRate ?? 4;
    // Quanto il capitale di oggi mantiene al mese: piu' leggibile di una
    // percentuale di avanzamento, e confrontabile con le spese vere.
    const alMese = (dati.netWorth * swr) / 100 / 12;
    const fase = (tipo: string) => piano.phases.find((f) => f.kind === tipo);
    const pensione = fase('pensione');
    // Senza pensioni inserite la fase dopo il ritiro non e' un ponte verso
    // qualcosa: e' il ritiro vissuto sul capitale, e va chiamato cosi'.
    const etichettaFase = (tipo: string) => tipo === 'ponte' && !pensione
      ? t('firePhaseRetirement') : t(ETICHETTE_FASE[tipo] as Parameters<typeof t>[0]);
    const punti = (righe: Array<{ year: number | null; capital: number; capitalNeeded?: number }>): FireChartPoint[] =>
      righe.filter((r) => r.year !== null).map((r) => ({
        anno: r.year as number, capitale: r.capital,
        ...(r.capitalNeeded !== undefined ? { capitale_necessario: r.capitalNeeded } : {}),
      }));

    const testiAvvisi: Record<string, string> = {
      swr_non_garantisce_solvibilita: t('fireWarningSwr'),
      rendite_non_indicizzate_escluse_dopo_regime: t('fireWarningErosion'),
      capitale_insufficiente_nella_proiezione: t('fireWarningDepleted', { age: piano.depletedAtAge ?? '' }),
      pensione_interpolata_non_stima_ente: t('fireWarningInterpolated'),
      montecarlo_rendimenti_indipendenti: t('fireWarningMonteCarlo'),
    };
    const avvisi = piano.warnings.filter((c) => c in testiAvvisi).map((c) => [c, testiAvvisi[c]] as const);

    return <div className="space-y-5">
      {errore && <p role="alert" className="rounded-xl border border-[var(--money-allarme-bordo)] bg-[var(--money-allarme-velo)] px-4 py-2.5 text-xs text-[var(--money-allarme)]">{t('fireRefreshFailed')}</p>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="border-0 bg-[var(--money-deep)] text-white shadow-sm"><CardContent className="p-5">
          <p className="text-sm text-white/70">{t('fireCapitalNeeded')}</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{formatEuro(piano.capitalNeeded)}</p>
          {/* Senza pensioni non c'e' ponte: "ponte €0 · rabbocco = tutto" e' rumore. */}
          {pensione && <p className="mt-2 text-xs text-white/70">{t('fireBridgeCapital')} {formatCompactEuro(piano.bridgeCapital)} · {t('fireTopUpCapital')} {formatCompactEuro(piano.topUpCapital)}</p>}
        </CardContent></Card>
        <Card className="border-[var(--money-velo)]/6 bg-[var(--money-superficie)] shadow-sm"><CardContent className="p-5">
          <p className="text-sm text-[var(--money-testo-tenue)]">{t('fireSupportsToday')}</p>
          <p className="mt-2 text-3xl font-semibold tabular-nums">{t('firePerMonth', { amount: formatEuro(alMese) })}</p>
          {dati.expensesUsed !== undefined && <p className="mt-2 text-xs text-[var(--money-testo-tenue)]">{t('fireExpensesUsed', { amount: formatCompactEuro(dati.expensesUsed) })}</p>}
          {dati.retirementExpenses !== undefined && dati.retirementExpenses !== dati.expensesUsed && <p className="mt-1 text-xs text-[var(--money-testo-tenue)]">{t('fireRetirementExpenses', { amount: formatCompactEuro(dati.retirementExpenses) })}</p>}
          {dati.annualSavings !== undefined && <p className="mt-1 text-xs text-[var(--money-testo-tenue)]">{t('fireSavingsUsed', { amount: formatCompactEuro(dati.annualSavings), rate: formatNumber(dati.savingsRate ?? 0, { maximumFractionDigits: 1 }) })}</p>}
        </CardContent></Card>
        <Card className="border-[var(--money-velo)]/6 bg-[var(--money-superficie)] shadow-sm sm:col-span-2"><CardContent className="p-5">
          {piano.reachedAtAge !== null && piano.reachedInYear !== null
            ? <p className="text-2xl font-semibold">{t('fireReachedAt', { age: piano.reachedAtAge, year: piano.reachedInYear })}</p>
            : <p className="text-sm text-[var(--money-testo-tenue)]">{t('fireNotReached')}</p>}
          {piano.yearsLeft !== null && <p className="mt-1 text-xs text-[var(--money-testo-tenue)]">{t('fireYearsLeft', { years: piano.yearsLeft })}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            {piano.phases.map((fase) => (
              <span key={fase.kind} className="rounded-lg bg-[var(--money-superficie-hover)] px-2.5 py-1 text-xs text-[var(--money-testo-muto)]">
                <b className="font-semibold">{etichettaFase(fase.kind)}</b>{' '}
                {fase.toAge === null ? t('firePhaseOpen', { from: fase.fromAge }) : t('firePhaseRange', { from: fase.fromAge, to: fase.toAge })}
              </span>
            ))}
          </div>
        </CardContent></Card>
      </div>

      <FireChart
        storico={punti(piano.history.map((r) => ({ year: r.year, capital: r.capital })))}
        centrale={punti(piano.series)}
        p10={punti(piano.scenarios.p10 ?? [])}
        p90={punti(piano.scenarios.p90 ?? [])}
        annoRitiro={fase('accumulo')?.toYear ?? new Date().getFullYear()}
        annoPrimoFlusso={pensione?.fromYear ?? null}
        labels={{
          title: t('fireChartTitle'), description: t('fireChartDescription'),
          scenarioNotice: t('fireWarningScenario'),
          accumulation: t('firePhaseAccumulation'), bridge: etichettaFase('ponte'),
          retirement: t('firePhasePension'), history: t('fireChartHistory'),
          central: t('fireScenariosCentral'), range: t('fireChartRange'),
          target: t('fireCapitalNeeded'), intersection: t('fireChartIntersection'),
          year: t('fireChartYear'), table: t('fireChartTable'), empty: t('fireChartEmpty'),
        }}
      />

      <div className="grid gap-5 xl:grid-cols-2">
        <MilestonesCard milestones={pensione ? piano.milestones : { ...piano.milestones, bridge: undefined }}
                        capital={dati.netWorth}
                        leanNote={piano.leanCapped ? t('fireMilestoneLeanCapped', {
                          lean: formatEuro(dati.profile?.leanAnnualExpenses ?? 0),
                          expenses: formatEuro(dati.expensesUsed ?? 0) }) : undefined} />
        <MonteCarloCard dati={piano.monteCarlo} />
        {piano.leverage.length > 0 && <LeverageTable leve={piano.leverage} />}
        <AgeShiftSlider apiUrl={apiUrl} />
      </div>

      {/* Le avvertenze del motore arrivano a chi guarda, non restano nel backend:
          una proiezione presentata senza riserve e' una promessa. */}
      {/* Lo scenario-non-previsione lo dice gia' il grafico: ripeterlo qui era
          un doppione. Un codice sconosciuto non si mostra grezzo. */}
      {avvisi.length > 0 && <Card className="border-[var(--money-velo)]/6 bg-[var(--money-superficie-tenue)] shadow-sm"><CardContent className="space-y-1 p-4">
        {avvisi.map(([codice, testo]) => <p key={codice} className="text-xs leading-5 text-[var(--money-testo-tenue)]">{testo}</p>)}
      </CardContent></Card>}
    </div>;
  }

  return <div className="space-y-5">
    {/* La striscia e' la stessa dei Movimenti e del Budget: un contorno, un
        fondo, un pulsante acceso. Il piano e i dati che lo producono sono due
        viste della stessa pagina, non due pagine. */}
    <div className="flex flex-wrap items-center justify-between gap-3">
      <TabStrip id="fire-tabs" label={t('fireSection')} value={scheda} onChange={cambiaScheda}
        options={[['piano', t('fireTabPlan')], ['profilo', t('fireTabSettings')]] as const} />
      {/* Da quando e' quel numero, e il tasto per rifarlo: la prima cosa che si
          cerca quando una cifra non torna. Il tasto resta anche mentre gira,
          spento, cosi' la riga non salta. */}
      {scheda === 'piano' && <div className="flex items-center gap-2 text-xs text-[var(--money-testo-tenue)]">
        {quando !== null && !inCorso && <span>{t('fireCalculatedAt', { time: formatDate(new Date(quando), { hour: '2-digit', minute: '2-digit' }) })}</span>}
        <Button variant="outline" size="sm" disabled={inCorso} onClick={() => void carica()} className="h-8 gap-1.5 bg-[var(--money-superficie)] text-xs">
          <RefreshCw className={`size-3.5 ${inCorso ? 'animate-spin' : ''}`} />{inCorso ? t('updating') : t('fireRecalculate')}
        </Button>
      </div>}
    </div>
    {/* La scheda nascosta non si tiene montata: le impostazioni si rileggono
        ogni volta che ci si entra, e i flussi salvati prima ci sono. */}
    {scheda === 'piano' ? contenutoPiano() : <FireSettingsSection apiUrl={apiUrl} />}
  </div>;
}
