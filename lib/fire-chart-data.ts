// Le righe del grafico FIRE: storia, proiezione centrale, banda fra scenari e
// obiettivo, anno per anno. Fuori dal componente perche' e' logica pura: il
// componente trascina con se' la libreria dei grafici.

export type FireChartPoint = { anno: number; capitale: number; capitale_necessario?: number };
export type ChartRow = { anno: number; history?: number; central?: number; range?: [number, number]; target?: number };

export function buildFireChartData({ storico, centrale, pessimistico, ottimistico }: {
  storico: readonly FireChartPoint[]; centrale: readonly FireChartPoint[];
  pessimistico: readonly FireChartPoint[]; ottimistico: readonly FireChartPoint[];
}): ChartRow[] {
  const rows = new Map<number, ChartRow>();
  const valid = (point: FireChartPoint) => Number.isFinite(point.anno) && Number.isFinite(point.capitale);
  const start = Math.min(...centrale.filter(valid).map((point) => point.anno));
  const pessimistic = new Map(pessimistico.filter(valid).map((point) => [point.anno, point.capitale]));
  const optimistic = new Map(ottimistico.filter(valid).map((point) => [point.anno, point.capitale]));
  for (const point of storico.filter(valid)) {
    if (point.anno <= start) rows.set(point.anno, { anno: point.anno, history: point.capitale });
  }
  for (const point of centrale.filter(valid)) {
    const low = pessimistic.get(point.anno);
    const high = optimistic.get(point.anno);
    rows.set(point.anno, {
      ...rows.get(point.anno), anno: point.anno, central: point.capitale,
      target: Number.isFinite(point.capitale_necessario) ? point.capitale_necessario : undefined,
      // I percorsi possono incrociarsi: min/max preservano una banda corretta.
      // Un anno assente resta assente, non diventa un finto zero.
      range: low == null || high == null ? undefined :
        [Math.min(low, high, point.capitale), Math.max(low, high, point.capitale)],
    });
  }
  return [...rows.values()].sort((a, b) => a.anno - b.anno);
}
