'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart';
import { useI18n } from '@/lib/i18n-context';

type PersonTotals = {
  income: number;
  expenses: number;
  savings: number;
  savingsRate: number | null;
  daysInPeriod: number;
  daysPassed: number;
  periodCompletion: number;
  netWorth: number;
  marketValue: number;
  investedCapital: number;
  gain: number;
  gainPercent: number | null;
};

type SeriesPoint = { period: string; label: string; netWorth: number; income: number; expenses: number; savings: number };
type SharedTotals = { period: string; people: Array<{ id: number; displayName: string; totals: PersonTotals }> };
type SharedTrend = {
  people: Array<{ id: number; displayName: string; series: SeriesPoint[] }>;
  family: SeriesPoint[];
};

type Metric = 'netWorth' | 'savings' | 'income' | 'expenses';

// Una tinta per persona, piu' il nero della riga di famiglia.
const COLORI = ['#6d8ff4', '#47a889', '#e0b04f', '#9479d1', '#c96a8e', '#5ab7a6'];
const COLORE_FAMIGLIA = '#173b33';
const FAMIGLIA = '__famiglia__';

/**
 * I totali di chi ha acceso la condivisione, e come si muovono nel tempo.
 *
 * Solo numeri riassuntivi: nessun movimento, nessuna categoria, nessun conto.
 * Non e' la pagina a nasconderli, e' il server che non li manda.
 */
export function SharedTotalsView({ apiUrl, year, month }: { apiUrl: string; year: number; month: number | null }) {
  const { t, formatEuro, formatCompactEuro, formatNumber, formatPeriodLabel } = useI18n();
  const [totals, setTotals] = useState<SharedTotals | null>(null);
  const [trend, setTrend] = useState<SharedTrend | null>(null);
  const [totalsLoading, setTotalsLoading] = useState(true);
  const [trendLoading, setTrendLoading] = useState(true);
  const [totalsError, setTotalsError] = useState(false);
  const [trendError, setTrendError] = useState(false);
  const [metric, setMetric] = useState<Metric>('netWorth');
  const [months, setMonths] = useState<12 | 24 | 36>(12);
  const [nascosti, setNascosti] = useState<Set<string>>(new Set());

  const loadTotals = useCallback(async () => {
    setTotalsLoading(true);
    setTotalsError(false);
    try {
      const periodo = `year=${year}${month ? `&month=${month}` : ''}`;
      const response = await fetch(`${apiUrl}/api/shared/totals?${periodo}`);
      if (!response.ok) throw new Error('shared-totals');
      setTotals(await response.json() as SharedTotals);
    } catch {
      setTotals(null);
      setTotalsError(true);
    } finally {
      setTotalsLoading(false);
    }
  }, [apiUrl, year, month]);

  const loadTrend = useCallback(async () => {
    setTrendLoading(true);
    setTrendError(false);
    setTrend(null);
    try {
      const response = await fetch(`${apiUrl}/api/shared/trend?year=${year}&month=${month ?? 0}&months=${months}`);
      if (!response.ok) throw new Error('shared-trend');
      setTrend(await response.json() as SharedTrend);
    } catch {
      setTrendError(true);
    } finally {
      setTrendLoading(false);
    }
  }, [apiUrl, year, month, months]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTotals(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTotals]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadTrend(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTrend]);

  // Le chiavi della serie usano l'id: due persone omonime devono restare due
  // curve distinte, mentre il nome rimane soltanto l'etichetta leggibile.
  const dati = useMemo(() => (trend?.family ?? []).map((punto, indice) => ({
    label: punto.label,
    [FAMIGLIA]: punto[metric],
    ...Object.fromEntries((trend?.people ?? []).map((p) => [`person-${p.id}`, p.series[indice]?.[metric] ?? null])),
  })), [trend, metric]);
  const config = useMemo<ChartConfig>(() => ({
    [FAMIGLIA]: { label: t('sharedFamily'), color: COLORE_FAMIGLIA },
    ...Object.fromEntries((trend?.people ?? []).map((p, i) => [`person-${p.id}`, { label: p.displayName, color: COLORI[i % COLORI.length] }])),
  }), [trend, t]);
  const linee = useMemo(() => {
    const persone = trend?.people ?? [];
    const tutte = [
      { chiave: FAMIGLIA, etichetta: t('sharedFamily'), colore: COLORE_FAMIGLIA, spessore: 2.8 },
      ...persone.map((p, i) => ({ chiave: `person-${p.id}`, etichetta: p.displayName, colore: COLORI[i % COLORI.length], spessore: 2 })),
    ];
    // Con una persona sola la riga di famiglia sarebbe la stessa curva ripetuta.
    return persone.length > 1 ? tutte : tutte.slice(1);
  }, [trend, t]);
  const totaleFamiglia = useMemo(() => (totals?.people ?? []).reduce((somma, p) => ({
    netWorth: somma.netWorth + p.totals.netWorth,
    savings: somma.savings + p.totals.savings,
    income: somma.income + p.totals.income,
    expenses: somma.expenses + p.totals.expenses,
  }), { netWorth: 0, savings: 0, income: 0, expenses: 0 }), [totals]);

  if (totalsLoading) return <p className="py-16 text-center text-sm text-[#87918e]">{t('loading')}</p>;

  if (totalsError) {
    return <Card className="border-[#efc4b8] bg-[#fff6f3] shadow-sm"><CardContent className="py-14 text-center"><p role="alert" className="text-sm font-medium text-[#a94f3a]">{t('sharedLoadError')}</p><Button type="button" variant="outline" className="mt-4" onClick={() => { void loadTotals(); void loadTrend(); }}>{t('retry')}</Button></CardContent></Card>;
  }

  if (!totals || totals.people.length === 0) {
    return (
      <Card className="border-black/6 bg-white shadow-sm">
        <CardContent className="py-14 text-center">
          <p className="text-sm font-medium text-[#173b33]">{t('sharedEmptyTitle')}</p>
          <p className="mx-auto mt-2 max-w-md text-xs leading-5 text-[#7b8784]">{t('sharedEmptyHint')}</p>
        </CardContent>
      </Card>
    );
  }

  const metriche: Array<[Metric, string]> = [
    ['netWorth', t('netWorthNet')],
    ['savings', t('saved')],
    ['income', t('income')],
    ['expenses', t('expenses')],
  ];

  const riga = (etichetta: string, valore: string, tono?: 'positivo' | 'negativo') => (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <span className="text-xs text-[#7b8784]">{etichetta}</span>
      <span className={`text-sm font-semibold tabular-nums ${
        tono === 'positivo' ? 'text-[#2d7b65]' : tono === 'negativo' ? 'text-[#bd5e46]' : 'text-[#173b33]'
      }`}>{valore}</span>
    </div>
  );

  return (
    <div className="space-y-5">
      <p className="text-xs text-[#7b8784]">{t('sharedSubtitle')}</p>

      {totals.people.length > 1 && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {([['netWorthNet', totaleFamiglia.netWorth], ['saved', totaleFamiglia.savings],
             ['income', totaleFamiglia.income], ['expenses', totaleFamiglia.expenses]] as const).map(([chiave, valore]) => (
            <Card key={chiave} className="border-black/6 bg-white shadow-sm">
              <CardContent className="p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-[#87918e]">
                  {t('sharedFamily')} · {t(chiave)}
                </p>
                <p className="mt-1 text-xl font-semibold tabular-nums text-[#173b33]">{formatEuro(valore)}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle className="text-[17px]">{t('sharedTrendTitle')}</CardTitle>
            <p className="mt-1 text-xs text-[#7b8784]">{t('sharedTrendSubtitle')}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <fieldset className="flex gap-1 rounded-lg border border-black/6 bg-[#f4f5f1] p-1 text-xs">
              <legend className="sr-only">{t('sharedMetricSelector')}</legend>
              {metriche.map(([chiave, etichetta]) => (
                <button key={chiave} type="button" aria-pressed={metric === chiave} onClick={() => setMetric(chiave)}
                  className={`rounded-md px-2.5 py-1 font-medium transition ${metric === chiave ? 'bg-white text-[#173b33] shadow-sm' : 'text-[#71807c] hover:text-[#173b33]'}`}>
                  {etichetta}
                </button>
              ))}
            </fieldset>
            <fieldset className="flex gap-1 rounded-lg border border-black/6 bg-[#f4f5f1] p-1 text-xs">
              <legend className="sr-only">{t('sharedRangeSelector')}</legend>
              {([12, 24, 36] as const).map((valore) => (
                <button key={valore} type="button" aria-pressed={months === valore} onClick={() => setMonths(valore)}
                  className={`rounded-md px-2.5 py-1 font-medium transition ${months === valore ? 'bg-white text-[#173b33] shadow-sm' : 'text-[#71807c] hover:text-[#173b33]'}`}>
                  {valore}m
                </button>
              ))}
            </fieldset>
          </div>
        </CardHeader>
        <CardContent>
          {trendLoading ? <p className="flex h-[320px] items-center justify-center text-sm text-[#87918e]">{t('loading')}</p> : trendError ? <div className="flex h-[320px] flex-col items-center justify-center text-center"><p role="alert" className="text-sm text-[#a94f3a]">{t('sharedLoadError')}</p><Button type="button" variant="outline" className="mt-4" onClick={() => void loadTrend()}>{t('retry')}</Button></div> : <>
          <fieldset className="mb-3 flex flex-wrap gap-1.5">
            <legend className="sr-only">{t('sharedLinesSelector')}</legend>
            {linee.map((linea) => {
              const attiva = !nascosti.has(linea.chiave);
              return (
                <button key={linea.chiave} type="button" aria-pressed={attiva}
                  onClick={() => setNascosti((prima) => {
                    const dopo = new Set(prima);
                    if (dopo.has(linea.chiave)) dopo.delete(linea.chiave); else dopo.add(linea.chiave);
                    return dopo;
                  })}
                  className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    attiva ? 'border-black/10 text-[#173b33]' : 'border-black/6 text-[#a3adaa]'}`}>
                  <span className="size-2.5 rounded-full" style={{ backgroundColor: attiva ? linea.colore : '#cbd2ce' }} />
                  {linea.etichetta}
                </button>
              );
            })}
          </fieldset>
          <ChartContainer config={config} className="h-[320px] w-full">
            <LineChart accessibilityLayer data={dati}>
              <CartesianGrid vertical={false} strokeDasharray="3 5" />
              <XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={20} />
              <YAxis tickLine={false} axisLine={false} width={84} tickFormatter={(v) => formatCompactEuro(Number(v))} />
              <ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(v) => formatEuro(Number(v))} />} />
              {linee.filter((l) => !nascosti.has(l.chiave)).map((linea) => (
                <Line key={linea.chiave} type="monotone" dataKey={linea.chiave} name={linea.etichetta}
                      stroke={linea.colore} strokeWidth={linea.spessore} dot={false} connectNulls />
              ))}
            </LineChart>
          </ChartContainer>
          </>}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {totals.people.map((persona, indice) => {
          const totali = persona.totals;
          return (
            <Card key={persona.id} className="border-black/6 bg-white shadow-sm">
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2.5 text-[17px]">
                  <span className="flex size-7 items-center justify-center rounded-full text-xs font-semibold text-white"
                        style={{ backgroundColor: COLORI[indice % COLORI.length] }}>
                    {persona.displayName.slice(0, 1).toUpperCase()}
                  </span>
                  {persona.displayName}
                </CardTitle>
              </CardHeader>
              <CardContent className="divide-y divide-black/5">
                <div className="pb-2">
                  {riga(t('income'), formatEuro(totali.income))}
                  {riga(t('expenses'), formatEuro(totali.expenses))}
                  {riga(t('saved'), formatEuro(totali.savings), totali.savings >= 0 ? 'positivo' : 'negativo')}
                  {totali.savingsRate !== null && riga(t('periodSavingsRate'), `${formatNumber(totali.savingsRate * 100, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`)}
                </div>
                <div className="py-2">{riga(t('netWorthNet'), formatEuro(totali.netWorth))}</div>
                <div className="py-2">
                  {riga(t('value'), formatEuro(totali.marketValue))}
                  {riga(t('investedCapital'), formatEuro(totali.investedCapital))}
                  {riga(t('profitLoss'), `${formatEuro(totali.gain)}${totali.gainPercent !== null ? ` (${formatNumber(totali.gainPercent, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%)` : ''}`,
                        totali.gain >= 0 ? 'positivo' : 'negativo')}
                </div>
                <div className="pt-2">{riga(t('periodCompletion'), `${totali.daysPassed} / ${totali.daysInPeriod}`)}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
