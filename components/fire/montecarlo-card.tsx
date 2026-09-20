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
  // Il rischio di sequenza: quanto regge il piano fra i percorsi che iniziano
  // peggio, sui primi `window` anni di ritiro. `value` e' null quando quel
  // ritiro e' piu' corto della finestra, e allora c'e' il motivo al posto del
  // numero - "valore o motivo", come i rendimenti.
  sorr: { value: number | null; reason: string | null; window: number };
};

export function MonteCarloCard({ dati }: { dati: MonteCarloPayload }) {
  const { t, formatNumber, formatCompactEuro } = useI18n();
  // Si arrotonda per difetto, non al piu' vicino: con il 99,56% l'arrotondamento
  // normale scriveva "100 su 100 reggono" e due righe sotto spiegava cosa
  // succede quando non reggono. Ventidue percorsi su cinquemila fallivano
  // davvero. Per difetto, "cento" esce solo quando non ne fallisce nessuno, e le
  // due righe non possono piu' contraddirsi.
  const suCento = Math.floor(dati.successRate * 100);
  const colore = suCento >= 90 ? '#237056' : suCento >= 75 ? '#7d6119' : '#a94f3a';
  // Il rischio di sequenza si legge con la stessa scala del successo: e' la
  // stessa domanda ("quanto e' stretto il margine") posta ai soli percorsi che
  // iniziano male. Arrotondato per difetto anche questo, per lo stesso motivo.
  const sorrSuCento = dati.sorr.value === null ? null : Math.floor(dati.sorr.value * 100);
  const coloreSorr = sorrSuCento === null ? '#5e6c68' : sorrSuCento >= 90 ? '#237056' : sorrSuCento >= 75 ? '#7d6119' : '#a94f3a';
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="p-5">
        <p className="text-2xl font-semibold tabular-nums" style={{ color: colore }}>
          {t('fireSuccessRate', { count: suCento })}
        </p>
        <p className="mt-1 text-xs text-[#5e6c68]">
          {t('fireSuccessDetail', { paths: formatNumber(dati.paths),
            volatility: formatNumber(dati.volatility * 100, { maximumFractionDigits: 1 }) })}
        </p>
        {dati.medianDepletionAge !== null && (
          <p className="mt-2 text-xs text-[#a94f3a]">{t('fireDepletionMedian', { age: dati.medianDepletionAge })}</p>
        )}
        {/* La media dei rendimenti nasconde proprio il rischio che conta: due
            piani con la stessa media finiscono lontanissimi se i primi anni di
            prelievo vanno male. Questo numero guarda solo il quartile che
            inizia peggio, e la finestra la dice il testo - "SORR" da solo non
            direbbe di che anni si sta parlando. */}
        <p className="mt-2 text-xs" style={{ color: coloreSorr }}>
          {sorrSuCento !== null
            ? t('fireSorr', { years: dati.sorr.window, count: sorrSuCento })
            : t('fireSorrMissing', { years: dati.sorr.window })}
        </p>
        {/* Dove si arriva, nei tre casi. La percentuale dice quanti piani
            reggono ma non con quale margine: fra restare a galla e chiudere con
            venti volte il necessario c'e' una differenza che il solo "99 su
            100" nasconde. */}
        {dati.atHorizon && <div className="mt-4 border-t border-black/[0.06] pt-3">
          <p className="text-[11px] uppercase tracking-wide text-[#5e6c68]">
            {t('fireHorizonTitle', { age: dati.atHorizon.age })}
          </p>
          <dl className="mt-1.5 grid grid-cols-3 gap-2 text-center">
            {([['fireHorizonLow', dati.atHorizon.p10, '#7d6119'],
               ['fireHorizonMid', dati.atHorizon.p50, '#3d4a47'],
               ['fireHorizonHigh', dati.atHorizon.p90, '#237056']] as const).map(([chiave, valore, tinta]) => (
              <div key={chiave}>
                <dt className="text-[10px] uppercase tracking-wide text-[#5e6c68]">{t(chiave)}</dt>
                <dd className="text-sm font-semibold tabular-nums" style={{ color: tinta }}>{formatCompactEuro(valore)}</dd>
              </div>))}
          </dl>
        </div>}
      </CardContent>
    </Card>
  );
}
