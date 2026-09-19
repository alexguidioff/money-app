'use client';

import { Card, CardContent } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';

// Su cento piani come questo, quanti reggono. Non e' un voto sul piano: e'
// quanto stretto e' il margine fra le spese e i rendimenti. Il colore lo dice
// prima delle parole - verde sopra il 90%, ambra fra 75 e 90, rosso sotto -
// con le tinte gia' in uso altrove nell'app.
export type MonteCarloPayload = {
  successRate: number;
  paths: number;
  volatility: number;
  medianDepletionAge: number | null;
  atHorizon: { age: number; p10: number; p50: number; p90: number } | null;
};

export function MonteCarloCard({ dati }: { dati: MonteCarloPayload }) {
  const { t, formatNumber, formatCompactEuro } = useI18n();
  // Si arrotonda per difetto, non al piu' vicino: con il 99,56% l'arrotondamento
  // normale scriveva "100 su 100 reggono" e due righe sotto spiegava cosa
  // succede quando non reggono. Ventidue percorsi su cinquemila fallivano
  // davvero. Per difetto, "cento" esce solo quando non ne fallisce nessuno, e le
  // due righe non possono piu' contraddirsi.
  const suCento = Math.floor(dati.successRate * 100);
  const colore = suCento >= 90 ? '#2d7b65' : suCento >= 75 ? '#9a7b2f' : '#bd5e46';
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="p-5">
        <p className="text-2xl font-semibold tabular-nums" style={{ color: colore }}>
          {t('fireSuccessRate', { count: suCento })}
        </p>
        <p className="mt-1 text-xs text-[#7b8784]">
          {t('fireSuccessDetail', { paths: formatNumber(dati.paths),
            volatility: formatNumber(dati.volatility * 100, { maximumFractionDigits: 1 }) })}
        </p>
        {dati.medianDepletionAge !== null && (
          <p className="mt-2 text-xs text-[#bd5e46]">{t('fireDepletionMedian', { age: dati.medianDepletionAge })}</p>
        )}
        {/* Dove si arriva, nei tre casi. La percentuale dice quanti piani
            reggono ma non con quale margine: fra restare a galla e chiudere con
            venti volte il necessario c'e' una differenza che il solo "99 su
            100" nasconde. */}
        {dati.atHorizon && <div className="mt-4 border-t border-black/[0.06] pt-3">
          <p className="text-[11px] uppercase tracking-wide text-[#87918e]">
            {t('fireHorizonTitle', { age: dati.atHorizon.age })}
          </p>
          <dl className="mt-1.5 grid grid-cols-3 gap-2 text-center">
            {([['fireHorizonLow', dati.atHorizon.p10, '#9a7b2f'],
               ['fireHorizonMid', dati.atHorizon.p50, '#3d4a47'],
               ['fireHorizonHigh', dati.atHorizon.p90, '#2d7b65']] as const).map(([chiave, valore, tinta]) => (
              <div key={chiave}>
                <dt className="text-[10px] uppercase tracking-wide text-[#9aa5a1]">{t(chiave)}</dt>
                <dd className="text-sm font-semibold tabular-nums" style={{ color: tinta }}>{formatCompactEuro(valore)}</dd>
              </div>))}
          </dl>
        </div>}
      </CardContent>
    </Card>
  );
}
