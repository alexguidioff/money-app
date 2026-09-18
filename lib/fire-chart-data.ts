// Le righe del grafico FIRE: storia, proiezione centrale, banda dal decimo al
// novantesimo percentile e obiettivo, anno per anno. Fuori dal componente
// perche' e' logica pura: il componente trascina con se' la libreria dei
// grafici.

export type FireChartPoint = { anno: number; capitale: number; capitale_necessario?: number };
export type ChartRow = { anno: number; history?: number; central?: number; range?: [number, number]; target?: number };

export function buildFireChartData({ storico, centrale, p10, p90 }: {
  storico: readonly FireChartPoint[]; centrale: readonly FireChartPoint[];
  p10: readonly FireChartPoint[]; p90: readonly FireChartPoint[];
}): ChartRow[] {
  const rows = new Map<number, ChartRow>();
  const valid = (point: FireChartPoint) => Number.isFinite(point.anno) && Number.isFinite(point.capitale);
  const start = Math.min(...centrale.filter(valid).map((point) => point.anno));
  const basso = new Map(p10.filter(valid).map((point) => [point.anno, point.capitale]));
  const alto = new Map(p90.filter(valid).map((point) => [point.anno, point.capitale]));
  for (const point of storico.filter(valid)) {
    if (point.anno <= start) rows.set(point.anno, { anno: point.anno, history: point.capitale });
  }
  for (const point of centrale.filter(valid)) {
    const low = basso.get(point.anno);
    const high = alto.get(point.anno);
    rows.set(point.anno, {
      ...rows.get(point.anno), anno: point.anno, central: point.capitale,
      target: Number.isFinite(point.capitale_necessario) ? point.capitale_necessario : undefined,
      // I percentili possono incrociare la linea centrale: min/max preservano
      // una banda corretta. Un anno assente resta assente, non diventa zero.
      range: low == null || high == null ? undefined :
        [Math.min(low, high, point.capitale), Math.max(low, high, point.capitale)],
    });
  }
  return [...rows.values()].sort((a, b) => a.anno - b.anno);
}
