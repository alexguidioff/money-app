'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';

// La leva: quanti anni al piano al variare del tasso di risparmio. Le righe le
// calcola il motore sul server, con lo stesso piano del resto della pagina:
// una formula a parte qui diceva 24 anni dove il piano ne diceva 29.
export type LeverageRow = {
  savingsRate: number;          // 0-100
  annualSavings: number;        // risparmio annuo a quel tasso, spese invariate
  yearsLeft: number | null;     // null: non raggiunto nell'orizzonte
  current: boolean;
};

export function LeverageTable({ rows }: { rows: readonly LeverageRow[] }) {
  const { t, formatEuro, formatNumber } = useI18n();
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireLeverageTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireLeverageSubtitle')}</p>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[11px] uppercase tracking-wide text-[#87918e]">
              <th className="pb-2 font-medium">%</th>
              <th className="pb-2 text-right font-medium">{t('fireLeverageYears')}</th>
              <th className="pb-2 text-right font-medium">{t('fireLeverageSavings')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.savingsRate} className={row.current ? 'bg-[#e5f3ed]' : 'border-t border-black/5'}>
                <td className="py-2 pr-3 font-semibold tabular-nums">
                  {formatNumber(row.savingsRate, { maximumFractionDigits: 1 })}% {row.current && <span className="ml-1 text-[10px] text-[#2d7b65]">· {t('fireLeverageYourRow')}</span>}
                </td>
                <td className="py-2 text-right tabular-nums">{row.yearsLeft ?? '∞'}</td>
                <td className="py-2 text-right text-xs text-[#87918e] tabular-nums">{formatEuro(row.annualSavings)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
