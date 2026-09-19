'use client';

import { useCallback, useEffect, useState } from 'react';
import { Flame } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';
import { FireChart, type FireChartPoint } from '@/components/fire-chart';
import { MilestonesCard } from '@/components/fire/milestones-card';
import { MonteCarloCard, type MonteCarloPayload } from '@/components/fire/montecarlo-card';
import { LeverageTable, type Leverage } from '@/components/fire/leverage-table';
import { AgeShiftSlider } from '@/components/fire/age-shift-slider';

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

export function FirePage({ apiUrl, onOpenSettings }: { apiUrl: string; onOpenSettings: () => void }) {
  const { t, formatEuro, formatCompactEuro, formatNumber } = useI18n();
  const [dati, setDati] = useState<FireData | null>(null);
  const [errore, setErrore] = useState(false);

  const carica = useCallback(async () => {
    try {
      const risposta = await fetch(`${apiUrl}/api/fire`);
      if (!risposta.ok) throw new Error('fire');
      setDati(await risposta.json() as FireData);
      setErrore(false);
    } catch { setErrore(true); setDati(null); }
  }, [apiUrl]);

  useEffect(() => { void carica(); }, [carica]);

  if (errore) return <Card className="border-[#efc4b8] bg-[#fff6f3] shadow-sm"><CardContent className="py-14 text-center"><p role="alert" className="text-sm text-[#bd5e46]">{t('fireProfileLoadError')}</p><Button variant="outline" size="sm" className="mt-3" onClick={() => void carica()}>{t('retry')}</Button></CardContent></Card>;
  if (!dati) return <p className="py-16 text-center text-sm text-[#87918e]">{t('loading')}</p>;

  // Senza profilo non si mostra un piano costruito su ipotesi che nessuno ha
  // dichiarato: si chiede di configurarlo.
  if (!dati.configured || !dati.plan) {
    return <Card className="border-black/6 bg-white shadow-sm"><CardContent className="py-14 text-center">
      <Flame className="mx-auto mb-3 size-8 text-[#87918e]" />
      <p className="text-sm text-[#173b33]">{t('fireNotConfigured')}</p>
      <Button className="mt-4 bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]" onClick={onOpenSettings}>{t('fireGoToSettings')}</Button>
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
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Card className="border-0 bg-[var(--money-deep)] text-white shadow-sm"><CardContent className="p-5">
        <p className="text-sm text-white/55">{t('fireCapitalNeeded')}</p>
        <p className="mt-2 text-3xl font-semibold tabular-nums">{formatEuro(piano.capitalNeeded)}</p>
        {/* Senza pensioni non c'e' ponte: "ponte €0 · rabbocco = tutto" e' rumore. */}
        {pensione && <p className="mt-2 text-xs text-white/65">{t('fireBridgeCapital')} {formatCompactEuro(piano.bridgeCapital)} · {t('fireTopUpCapital')} {formatCompactEuro(piano.topUpCapital)}</p>}
      </CardContent></Card>
      <Card className="border-black/6 bg-white shadow-sm"><CardContent className="p-5">
        <p className="text-sm text-[#71807c]">{t('fireSupportsToday')}</p>
        <p className="mt-2 text-3xl font-semibold tabular-nums">{t('firePerMonth', { amount: formatEuro(alMese) })}</p>
        {dati.expensesUsed !== undefined && <p className="mt-2 text-xs text-[#87918e]">{t('fireExpensesUsed', { amount: formatCompactEuro(dati.expensesUsed) })}</p>}
        {dati.retirementExpenses !== undefined && dati.retirementExpenses !== dati.expensesUsed && <p className="mt-1 text-xs text-[#87918e]">{t('fireRetirementExpenses', { amount: formatCompactEuro(dati.retirementExpenses) })}</p>}
        {dati.annualSavings !== undefined && <p className="mt-1 text-xs text-[#87918e]">{t('fireSavingsUsed', { amount: formatCompactEuro(dati.annualSavings), rate: formatNumber(dati.savingsRate ?? 0, { maximumFractionDigits: 1 }) })}</p>}
      </CardContent></Card>
      <Card className="border-black/6 bg-white shadow-sm sm:col-span-2"><CardContent className="p-5">
        {piano.reachedAtAge !== null && piano.reachedInYear !== null
          ? <p className="text-2xl font-semibold">{t('fireReachedAt', { age: piano.reachedAtAge, year: piano.reachedInYear })}</p>
          : <p className="text-sm text-[#71807c]">{t('fireNotReached')}</p>}
        {piano.yearsLeft !== null && <p className="mt-1 text-xs text-[#87918e]">{t('fireYearsLeft', { years: piano.yearsLeft })}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          {piano.phases.map((fase) => (
            <span key={fase.kind} className="rounded-lg bg-[#f4f5f1] px-2.5 py-1 text-xs text-[#52615d]">
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
      {piano.leverage.length > 0 && <LeverageTable rows={piano.leverage[0].rows} />}
      <AgeShiftSlider apiUrl={apiUrl} />
    </div>

    {/* Le avvertenze del motore arrivano a chi guarda, non restano nel backend:
        una proiezione presentata senza riserve e' una promessa. */}
    {/* Lo scenario-non-previsione lo dice gia' il grafico: ripeterlo qui era
        un doppione. Un codice sconosciuto non si mostra grezzo. */}
    {avvisi.length > 0 && <Card className="border-black/6 bg-[#fafaf8] shadow-sm"><CardContent className="space-y-1 p-4">
      {avvisi.map(([codice, testo]) => <p key={codice} className="text-xs leading-5 text-[#71807c]">{testo}</p>)}
    </CardContent></Card>}
  </div>;
}
