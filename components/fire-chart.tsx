'use client';

import { useMemo } from 'react';
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceDot, Tooltip, XAxis, YAxis } from 'recharts';
import { ChartContainer, ChartLegend, ChartLegendContent, type ChartConfig } from '@/components/ui/chart';
import { useI18n } from '@/lib/i18n-context';
import { scalaGrafico } from '@/lib/fire-chart-scale';
import { buildFireChartData, type FireChartPoint } from '@/lib/fire-chart-data';

export type { FireChartPoint };

export type FireChartLabels = Record<
  'title' | 'description' | 'scenarioNotice' | 'accumulation' | 'bridge' | 'retirement' |
  'history' | 'central' | 'range' | 'target' | 'intersection' | 'year' | 'table' | 'empty', string
>;

// Il componente pagina passa testi ottenuti da t(): le traduzioni appartengono
// al junior e qui non si aggiungono chiavi o ripieghi italiani nascosti.
// Chiavi proposte: fireChart + ciascun nome di FireChartLabels in PascalCase.
// scenarioNotice deve spiegare che la banda e' fra scenari, non un intervallo
// di confidenza. Tutti gli importi devono avere lo stesso perimetro investibile
// e la stessa base reale; lo storico non viene usato per stimare rendimenti.
export type FireChartProps = {
  storico: readonly FireChartPoint[];
  centrale: readonly FireChartPoint[];
  pessimistico: readonly FireChartPoint[];
  ottimistico: readonly FireChartPoint[];
  annoRitiro: number;
  annoPrimoFlusso: number | null;
  labels: FireChartLabels;
};

export function FireChart(props: FireChartProps) {
  const { formatEuro, formatCompactEuro, locale } = useI18n();
  const { labels, annoRitiro, annoPrimoFlusso } = props;
  const rows = useMemo(() => buildFireChartData(props),
    [props.storico, props.centrale, props.pessimistico, props.ottimistico]);
  const projected = rows.filter((row) => row.central != null);
  const start = projected[0]?.anno;
  const end = rows.at(-1)?.anno;
  const crossing = projected.find((row) => row.target != null && row.central! >= row.target);
  const previous = crossing ? projected[projected.indexOf(crossing) - 1] : undefined;
  let intersection = crossing ? { anno: crossing.anno, capitale: crossing.central! } : null;
  if (crossing && previous?.target != null && crossing.target != null) {
    // Interpolazione della sola geometria fra punti annuali: non si dichiara
    // una data FIRE mensile piu' precisa di quanto il motore possa conoscere.
    const before = previous.central! - previous.target;
    const after = crossing.central! - crossing.target;
    const ratio = -before / (after - before);
    if (Number.isFinite(ratio) && ratio >= 0 && ratio <= 1) {
      intersection = { anno: previous.anno + ratio * (crossing.anno - previous.anno),
        capitale: previous.central! + ratio * (crossing.central! - previous.central!) };
    }
  }
  const { yTicks, xTicks } = scalaGrafico(rows, annoRitiro, annoPrimoFlusso, crossing?.anno ?? null);
  const config = {
    history: { label: labels.history, color: '#71807c' },
    central: { label: labels.central, color: '#2d7b65' },
    range: { label: labels.range, color: '#b6d9cd' },
    target: { label: labels.target, color: '#bd5e46' },
  } satisfies ChartConfig;
  const year = (value: number) => value.toLocaleString(locale, { useGrouping: false, maximumFractionDigits: 0 });
  return <section aria-label={labels.title} className="space-y-3">
    <h3 className="text-base font-semibold">{labels.title}</h3>
    <p className="text-sm text-muted-foreground">{labels.description}</p>
    <p role="note" className="text-sm text-muted-foreground">{labels.scenarioNotice}</p>
    {!rows.length ? <p role="status">{labels.empty}</p> : <>
      <ChartContainer config={config} className="h-[360px] w-full">
        <ComposedChart data={rows} accessibilityLayer margin={{ top: 26, right: 24, bottom: 8, left: 12 }}>
          <CartesianGrid vertical={false} strokeDasharray="3 5" />
          <XAxis dataKey="anno" type="number" domain={['dataMin', 'dataMax']} allowDecimals={false} tickFormatter={year} ticks={xTicks.length > 1 ? xTicks : undefined} />
          <YAxis width={80} domain={yTicks ? [0, yTicks[4]] : [0, 'auto']} ticks={yTicks} allowDataOverflow tickFormatter={(value) => formatCompactEuro(Number(value))} />
          {start != null && end != null && <>
            {start < annoRitiro && <ReferenceArea x1={start} x2={Math.min(annoRitiro, end)} fill="#e5f3ed" fillOpacity={0.5} label={labels.accumulation} />}
            {annoRitiro < end && (annoPrimoFlusso == null || annoPrimoFlusso > annoRitiro) && <ReferenceArea x1={Math.max(start, annoRitiro)} x2={Math.min(annoPrimoFlusso ?? end, end)} fill="#fff1d8" fillOpacity={0.5} label={labels.bridge} />}
            {annoPrimoFlusso != null && annoPrimoFlusso < end && <ReferenceArea x1={Math.max(start, annoRitiro, annoPrimoFlusso)} x2={end} fill="#e9eef5" fillOpacity={0.5} label={labels.retirement} />}
          </>}
          <Area dataKey="range" type="linear" stroke="none" fill="var(--color-range)" fillOpacity={0.6} connectNulls={false} isAnimationActive={false} />
          <Line dataKey="history" type="linear" stroke="var(--color-history)" strokeWidth={2} dot={rows.filter((r) => r.history != null).length === 1} connectNulls={false} isAnimationActive={false} />
          <Line dataKey="target" type="linear" stroke="var(--color-target)" strokeDasharray="5 4" dot={false} connectNulls={false} isAnimationActive={false} />
          <Line dataKey="central" type="linear" stroke="var(--color-central)" strokeWidth={2.5} dot={projected.length === 1} connectNulls={false} isAnimationActive={false} />
          {intersection && <ReferenceDot x={intersection.anno} y={intersection.capitale} r={5} fill="#2d7b65" stroke="white" label={labels.intersection} />}
          <Tooltip labelFormatter={(label) => year(Number(label))} formatter={(value, name) => [
            Array.isArray(value) ? value.map((v) => formatEuro(Number(v))).join(' – ') : formatEuro(Number(value)),
            config[name as keyof typeof config]?.label ?? name,
          ]} />
          <ChartLegend content={<ChartLegendContent />} />
        </ComposedChart>
      </ChartContainer>
      <details><summary className="cursor-pointer text-sm">{labels.table}</summary>
        <div className="max-h-72 overflow-auto"><table className="w-full text-sm"><caption className="sr-only">{labels.title}</caption>
          <thead><tr>{[labels.year, labels.history, labels.central, labels.range, labels.target].map((label) => <th key={label} scope="col" className="p-2 text-left">{label}</th>)}</tr></thead>
          <tbody>{rows.map((row) => <tr key={row.anno}><th scope="row" className="p-2 text-left">{year(row.anno)}</th>{[row.history, row.central, row.range, row.target].map((value, index) => <td key={index} className="p-2 tabular-nums">{value == null ? '—' : Array.isArray(value) ? value.map(formatEuro).join(' – ') : formatEuro(value)}</td>)}</tr>)}</tbody>
        </table></div>
      </details>
    </>}
  </section>;
}
