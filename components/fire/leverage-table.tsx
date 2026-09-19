'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';
import { colonneFerme } from '@/lib/fire-leverage';
import type { TranslationKey } from '@/lib/translations';

// Le leve: quanti anni al piano, e quanto capitale servirebbe, al variare di
// una cosa sola. Le righe le calcola il motore sul server, con lo stesso piano
// del resto della pagina: una formula a parte qui diceva 24 anni dove il piano
// ne diceva 29.
export type LeverageRow = {
  value: number;                    // il valore della leva: tasso, eta' o spese
  annualSavings: number | null;     // risparmio annuo: solo la leva del risparmio lo muove
  capitalNeeded: number;            // il capitale che servirebbe a quella riga
  yearsLeft: number | null;         // null: non raggiunto nell'orizzonte
  current: boolean;
};

export type LeverageKey = 'savingsRate' | 'return' | 'retirementAge' | 'retirementExpenses';

export type Leverage = {
  key: LeverageKey;
  rows: LeverageRow[];
};

/** Come si chiama ogni leva, e come si scrive il suo valore. */
const LEVE: Record<LeverageKey, { nome: TranslationKey; formato: 'percento' | 'anni' | 'euro' }> = {
  savingsRate: { nome: 'fireLeverageSavingsRate', formato: 'percento' },
  return: { nome: 'fireLeverageReturn', formato: 'percento' },
  retirementAge: { nome: 'fireLeverageRetirementAge', formato: 'anni' },
  retirementExpenses: { nome: 'fireLeverageRetirementExpenses', formato: 'euro' },
};

/**
 * Le quattro leve del piano, una tabella per volta.
 *
 * Una tabella per leva sarebbero venti righe che si somigliano e quattro
 * titoli da leggere per capire che dicono la stessa cosa: il selettore sopra
 * tiene una tabella sola, con la colonna di sinistra che cambia nome.
 *
 * Le righe arrivano tutte calcolate dal server: cambiare leva non costa una
 * seconda chiamata. Quella dell'utente e' evidenziata, e sotto c'e' scritto
 * che si muove una variabile per volta - una sensibilita' senza quella riga
 * si legge come se le cose cambiassero insieme.
 *
 * Chi non arriva entro l'orizzonte ha `∞`: e' quello che il motore ha detto,
 * e un anno inventato al suo posto sarebbe la solita stima plausibile e falsa.
 *
 * Una colonna che non si muove non si disegna: si scrive una volta che con
 * quella leva non cambia. E quando non si muove niente, al posto della tabella
 * c'e' la riga che dice perche' e cosa la farebbe muovere - cinque righe uguali
 * sotto un'intestazione che promette una sensibilita' sono una promessa non
 * mantenuta, non un risultato.
 */
export function LeverageTable({ leve }: { leve: readonly Leverage[] }) {
  const { t, formatEuro, formatNumber } = useI18n();
  const [chiave, setChiave] = useState<LeverageKey | null>(null);
  // Senza scelta si mostra la prima leva della risposta, che e' quella del
  // risparmio: chi apre la pagina vede quello che vedeva prima.
  const scelta = leve.find((leva) => leva.key === chiave) ?? leve[0];
  if (!scelta) return null;
  const nome = t(LEVE[scelta.key].nome);
  const scrivi = (valore: number) => {
    const formato = LEVE[scelta.key].formato;
    if (formato === 'euro') return formatEuro(valore);
    if (formato === 'anni') return formatNumber(valore);
    return `${formatNumber(valore, { maximumFractionDigits: 1 })}%`;
  };
  const risparmi = scelta.key === 'savingsRate';
  const ferme = colonneFerme(scelta.rows);
  // Nessuna delle due colonne si muove: la tabella non e' una tabella, e'
  // cinque volte la stessa riga. Si dice cosa succede e cosa lo farebbe
  // muovere, invece di far credere che quelle cinque prove siano una risposta.
  const inutile = ferme.anni && ferme.capitale;

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireLeverageTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireLeverageSubtitle')}</p>
        <div className="mt-2 flex items-center gap-2">
          <label htmlFor="fire-lever" className="text-[11px] uppercase tracking-wide text-[#87918e]">
            {t('fireLeveragePick')}
          </label>
          <select id="fire-lever" value={scelta.key} onChange={(evento) => setChiave(evento.target.value as LeverageKey)}
                  className="h-8 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus:border-ring">
            {leve.map((leva) => <option key={leva.key} value={leva.key}>{t(LEVE[leva.key].nome)}</option>)}
          </select>
        </div>
      </CardHeader>
      <CardContent>
        {inutile
          ? <div>
              <p className="text-sm font-semibold text-[#173b33]">{t('fireLeverageNoEffect')}</p>
              <p className="mt-1 text-xs leading-5 text-[#7b8784]">{t('fireLeverageNoEffectWhy')}</p>
            </div>
          : <>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-[#87918e]">
                    <th className="pb-2 font-medium">{nome}</th>
                    {!ferme.anni && <th className="pb-2 text-right font-medium">{t('fireLeverageYears')}</th>}
                    {!ferme.capitale && <th className="pb-2 text-right font-medium">{t('fireLeverageCapital')}</th>}
                    {risparmi && <th className="pb-2 text-right font-medium">{t('fireLeverageSavings')}</th>}
                  </tr>
                </thead>
                <tbody>
                  {scelta.rows.map((row) => (
                    <tr key={row.value} className={row.current ? 'bg-[#e5f3ed]' : 'border-t border-black/5'}>
                      <td className="py-2 pr-3 font-semibold tabular-nums">
                        {scrivi(row.value)} {row.current && <span className="ml-1 text-[10px] text-[#2d7b65]">· {t('fireLeverageYourRow')}</span>}
                      </td>
                      {!ferme.anni && <td className="py-2 text-right tabular-nums">{row.yearsLeft ?? '∞'}</td>}
                      {!ferme.capitale && <td className="py-2 text-right tabular-nums">{formatEuro(row.capitalNeeded)}</td>}
                      {risparmi && <td className="py-2 text-right text-xs text-[#87918e] tabular-nums">
                        {row.annualSavings === null ? '' : formatEuro(row.annualSavings)}
                      </td>}
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* Una colonna tolta senza una riga che lo dica si legge come una
                  colonna dimenticata. E la riga "si muove una cosa per volta"
                  vale solo finche' c'e' qualcosa che si muove. */}
              {ferme.anni || ferme.capitale
                ? <p className="mt-2 text-xs text-[#87918e]">{t('fireLeverageColumnFixed', {
                    column: t(ferme.anni ? 'fireLeverageYears' : 'fireLeverageCapital') })}</p>
                : <p className="mt-2 text-xs text-[#87918e]">{t('fireLeverageOnlyOne')}</p>}
            </>}
      </CardContent>
    </Card>
  );
}
