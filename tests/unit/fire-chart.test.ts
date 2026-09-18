import { describe, expect, it } from 'vitest';
import { buildFireChartData } from '@/lib/fire-chart-data';
import { scalaGrafico } from '@/lib/fire-chart-scale';

describe('scala del grafico FIRE', () => {
  const righe = [
    { anno: 2026, central: 250_000, target: 1_365_430 },
    { anno: 2050, central: 1_400_000, target: 1_365_430 },
    { anno: 2063, central: 3_800_000, target: 1_365_430 },
    { anno: 2088, central: 60_000_000, target: 1_365_430 },
  ];

  it('le tacche sono quattro passi tondi, non il massimo grezzo', () => {
    const { yTicks } = scalaGrafico(righe, 2063, null, 2050);
    expect(yTicks).toHaveLength(5);
    expect(yTicks![0]).toBe(0);
    const passo = yTicks![1];
    expect(yTicks).toEqual([0, passo, 2 * passo, 3 * passo, 4 * passo]);
    expect(String(passo)).toMatch(/^(1|15|2|25|3|4|5|6|8)0*$/);
  });

  it('la coda lontana non schiaccia il resto: la scala si ferma 5 anni dopo il ritiro', () => {
    // 60 milioni nel 2088 non devono decidere la scala: il ritiro e' nel 2063.
    const { yTicks } = scalaGrafico(righe, 2063, null, 2050);
    expect(yTicks!.at(-1)).toBeGreaterThanOrEqual(3_800_000);
    expect(yTicks!.at(-1)).toBeLessThan(10_000_000);
  });

  it('gli anni vanno a decenni dentro l\'intervallo', () => {
    expect(scalaGrafico(righe, 2063, null, 2050).xTicks).toEqual([2030, 2040, 2050, 2060, 2070, 2080]);
  });

  it('senza dati niente tacche e nessun errore', () => {
    expect(scalaGrafico([], 2063, null, null)).toEqual({ yTicks: undefined, xTicks: [] });
    expect(scalaGrafico([{ anno: 2026 }], 2063, null, null).xTicks).toEqual([]);
  });
});

describe('dati del grafico FIRE', () => {
  const punto = (anno: number, capitale: number, capitale_necessario?: number) => ({ anno, capitale, capitale_necessario });

  it('la storia si ferma dove comincia la proiezione', () => {
    const righe = buildFireChartData({
      storico: [punto(2024, 100), punto(2025, 200), punto(2026, 300), punto(2027, 999)],
      centrale: [punto(2026, 300, 1000), punto(2027, 400, 1000)],
      p10: [], p90: [],
    });
    expect(righe.find((r) => r.anno === 2027)?.history).toBeUndefined();
    expect(righe.find((r) => r.anno === 2026)).toMatchObject({ history: 300, central: 300 });
  });

  it('un anno senza percentili resta senza banda, non a zero', () => {
    const righe = buildFireChartData({
      storico: [], centrale: [punto(2026, 300), punto(2027, 400)],
      p10: [punto(2026, 280)], p90: [punto(2026, 320)],
    });
    expect(righe.find((r) => r.anno === 2027)?.range).toBeUndefined();
  });

  it('la banda e\' ordinata anche se gli scenari si incrociano', () => {
    const [riga] = buildFireChartData({
      storico: [], centrale: [punto(2026, 300)], p10: [punto(2026, 350)], p90: [punto(2026, 250)],
    });
    expect(riga.range).toEqual([250, 350]);
  });
});
