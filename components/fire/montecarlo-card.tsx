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
};

export function MonteCarloCard({ dati }: { dati: MonteCarloPayload }) {
  const { t, formatNumber } = useI18n();
  const suCento = Math.round(dati.successRate * 100);
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
      </CardContent>
    </Card>
  );
}
