import { useEffect, useState } from 'react';
import { Split, Undo2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { useI18n } from '@/lib/i18n-context';
import { translations, type TranslationKey } from '@/lib/translations';

export type PDFTransaction = {
  id: number | null;
  description: string;
  category: string;
  categoryAutomatic?: boolean;
  // Il testo della regola che ha deciso la categoria. C'e' solo quando la
  // decisione e' sua: una categoria portata dal file non e' una proposta.
  categoryRule?: string | null;
  date: string | null;
  amount: number;
  type: 'income' | 'expense' | 'saving' | 'transfer';
  transactionType: TipoMovimento;
  accountName: string | null;
  destinationName: string | null;
  goal: string | null;
  details: string;
  duplicate?: boolean;
  duplicateOf?: { id: number; date: string; amount: number; description: string } | null;
  errorCode?: string;
};

type Riga = PDFTransaction & { selected: boolean; chiave: number; divisa?: { gruppo: number; totale: number } };

const centesimi = (valore: number) => Math.round(valore * 100) / 100;

// Gli stessi tipi, nello stesso ordine, del modulo "Nuovo movimento". Il
// risparmio non c'e': non e' un movimento, e' quello che resta di entrate e spese.
const TIPI = [['Expenses', 'typeExpense'], ['Income', 'typeIncome'], ['Transfers', 'typeTransfer'],
  ['Investment', 'typeInvestment'], ['Debt', 'typeDebt']] as const satisfies readonly (readonly [string, TranslationKey])[];
type TipoMovimento = (typeof TIPI)[number][0];
// Spostano denaro fra due conti: vogliono una destinazione e non hanno categoria.
const SPOSTAMENTI: readonly TipoMovimento[] = ['Transfers', 'Investment', 'Debt'];

export function PDFImportPreview({ transactions, accounts, categoriesByType, onConfirm, onCancel, feedback }: {
  transactions: PDFTransaction[];
  accounts: { name: string }[];
  categoriesByType: Record<string, string[]>;
  onConfirm: (approvedTransactions: PDFTransaction[]) => Promise<void>;
  onCancel: () => void;
  feedback: { ok: boolean; message: string } | null;
}) {
  const { t, formatEuro, formatDate } = useI18n();
  // La chiave resta con la riga anche quando se ne inseriscono altre: con
  // l'indice, dividere una riga sposterebbe i valori digitati su quella sotto.
  const [contatore] = useState(() => ({ valore: 0 }));
  const iniziali = () => transactions.map((row): Riga => ({ ...row, selected: !row.duplicate, chiave: contatore.valore++ }));
  const [rows, setRows] = useState(iniziali);
  const [isSaving, setIsSaving] = useState(false);
  const [allAccount, setAllAccount] = useState('');
  useEffect(() => {
    setRows(iniziali());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transactions]);
  const updateRow = (index: number, patch: Partial<Riga>) =>
    setRows(current => current.map((row, i) => i === index ? { ...row, ...patch } : row));

  // Una spesa pagata per intero ma solo in parte propria (l'altra meta' da farsi
  // restituire) diventa due righe che si sistemano ciascuna per conto suo. La
  // somma resta quella dell'estratto conto: cambiando una parte, l'altra segue.
  const dividi = (index: number) => setRows(current => {
    const riga = current[index];
    const gruppo = contatore.valore++;
    const prima = centesimi(Math.ceil(riga.amount * 100 / 2) / 100);
    const divisa = { gruppo, totale: riga.amount };
    const seconda: Riga = { ...riga, chiave: contatore.valore++, amount: centesimi(riga.amount - prima), divisa,
      selected: true, duplicate: false, duplicateOf: null, errorCode: undefined };
    return [...current.slice(0, index), { ...riga, amount: prima, divisa }, seconda, ...current.slice(index + 1)];
  });
  const riunisci = (gruppo: number) => setRows(current => {
    const prima = current.findIndex(row => row.divisa?.gruppo === gruppo);
    const unita = { ...current[prima], amount: current[prima].divisa!.totale, divisa: undefined };
    return current.flatMap((row, i) => i === prima ? [unita] : row.divisa?.gruppo === gruppo ? [] : [row]);
  });
  const cambiaImporto = (index: number, valore: number) => setRows(current => {
    const riga = current[index];
    return current.map((row, i) => {
      if (i === index) return { ...row, amount: valore };
      const resto = riga.divisa ? centesimi(riga.divisa.totale - valore) : 0;
      return riga.divisa && row.divisa?.gruppo === riga.divisa.gruppo && resto > 0 ? { ...row, amount: resto } : row;
    });
  });
  const sommaSbagliata = (row: Riga) => Boolean(row.divisa) && centesimi(rows
    .filter(altra => altra.divisa?.gruppo === row.divisa!.gruppo).reduce((somma, altra) => somma + (altra.amount || 0), 0)) !== row.divisa!.totale;

  const selected = rows.filter(row => row.selected);
  const invalid = selected.some(row => !row.date || !row.accountName || !(row.amount > 0) || sommaSbagliata(row) ||
    (SPOSTAMENTI.includes(row.transactionType) && (!row.destinationName || row.destinationName === row.accountName)));
  const accountOptions = <><option value="">{t('statementChooseAccount')}</option>{accounts.map(a => <option key={a.name} value={a.name}>{a.name}</option>)}</>;

  return <div className="flex min-h-0 flex-col gap-4">
    <DialogTitle>{t('statementPreview')}</DialogTitle>
    <DialogDescription>{t('statementPreviewHint', { count: rows.length })}</DialogDescription>
    <p className="text-sm">{t('duplicateSummary', { count: rows.length, duplicates: rows.filter(row => row.duplicate).length })}</p>
    <label className="text-sm">{t('statementAccountAll')}
      <select aria-label={t('statementAccountAll')} disabled={isSaving} value={allAccount} className="ml-3 rounded border p-2"
        onChange={e => { setAllAccount(e.target.value); setRows(current => current.map(row => ({ ...row, accountName: e.target.value || null }))); }}>
        {accountOptions}
      </select>
    </label>
    <div className="min-h-0 overflow-auto">
      <table className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-white"><tr>
          <th className="p-2">{t('statementSelect')}</th><th><span className="sr-only">{t('splitRow')}</span></th><th>{t('date')}</th><th>{t('description')}</th>
          <th>{t('category')}</th><th>{t('amount')}</th><th>{t('type')}</th><th>{t('account')}</th><th>{t('fieldDestinationAccount')}</th>
        </tr></thead>
        <tbody>{rows.map((row, index) => {
          // Il risparmio non e' un tipo di movimento: si deriva da entrate e spese, e
          // una riga "Risparmi" veniva rifiutata al salvataggio.
          const outgoing = row.transactionType !== 'Income';
          const spostamento = SPOSTAMENTI.includes(row.transactionType);
          const categorie = categoriesByType[row.transactionType] ?? [];
          // Una categoria decisa da una regola e' automatica - non l'ha scelta
          // nessuno - ma non e' vuota: si vede, e la riga sotto dice da dove
          // viene. Solo chi non ne ha proprio una resta "Da categorizzare".
          const senzaCategoria = row.categoryAutomatic && !row.categoryRule;
          // La categoria letta dal file resta fra le scelte anche se non e' nel
          // vocabolario; "Da categorizzare" e' la voce vuota.
          const scelte = Array.from(new Set([senzaCategoria ? '' : row.category, ...categorie].filter(Boolean)));
          const primaDelGruppo = row.divisa && rows.findIndex(altra => altra.divisa?.gruppo === row.divisa!.gruppo) === index;
          return <tr key={row.chiave} className={`border-b ${row.divisa ? 'bg-[#f7f9f6]' : ''}`}>
            <td className="p-2"><input type="checkbox" aria-label={t('statementSelectRow', { row: index + 1 })} checked={row.selected} disabled={isSaving}
              onChange={e => updateRow(index, { selected: e.target.checked })} /></td>
            <td className="p-2">{!row.divisa
              ? <Button type="button" variant="ghost" size="icon" title={t('splitRow')} aria-label={t('splitRowAria', { row: index + 1 })} disabled={isSaving || !(row.amount >= 0.02)} onClick={() => dividi(index)}><Split className="size-4" /></Button>
              : primaDelGruppo && <Button type="button" variant="ghost" size="icon" title={t('splitUndo')} aria-label={t('splitUndoAria', { row: index + 1 })} disabled={isSaving} onClick={() => riunisci(row.divisa!.gruppo)}><Undo2 className="size-4" /></Button>}</td>
            <td className="p-2"><Input type="date" aria-label={t('date')} aria-invalid={row.selected && !row.date} value={row.date ?? ''} disabled={isSaving}
              onChange={e => updateRow(index, { date: e.target.value })} className="w-36" /></td>
            <td className="min-w-44 p-2">{row.description}{row.duplicate && <p className="text-xs text-amber-700">{t('statementDuplicate')}{row.duplicateOf && <> · #{row.duplicateOf.id} · {formatDate(row.duplicateOf.date)} · {formatEuro(row.duplicateOf.amount)} · {row.duplicateOf.description}</>}</p>}
              {row.errorCode && <p className="text-xs text-red-700">{t(Object.hasOwn(translations.it, row.errorCode) ? row.errorCode as TranslationKey : 'statementRowInvalid')}</p>}</td>
            <td className="p-2"><select aria-label={t('category')} value={spostamento || senzaCategoria ? '' : row.category} disabled={isSaving || spostamento} className="w-40 rounded border p-2 disabled:bg-[#f4f5f1] disabled:text-[#a3adaa]"
              onChange={e => updateRow(index, { category: e.target.value, categoryAutomatic: !e.target.value, categoryRule: null })}>
              <option value="">{spostamento ? t('categoryNotApplicable') : t('categoryAutomatic')}</option>
              {!spostamento && scelte.map(categoria => <option key={categoria} value={categoria}>{categoria}</option>)}
            </select>
            {/* Da dove viene la categoria: senza, una casella gia' piena sembra
                una lettura del file. La × la riporta a "Da categorizzare". */}
            {row.categoryRule && !spostamento && <p className="mt-1 flex items-center gap-1 text-xs text-[#7b8784]">
              <span>{t('categoryFromRule', { rule: row.categoryRule })}</span>
              <button type="button" disabled={isSaving} aria-label={t('categoryRuleClear')}
                onClick={() => updateRow(index, { categoryRule: null, category: '', categoryAutomatic: true })}
                className="text-[#7b8784] hover:text-[#28312f]"><X className="size-3" /></button>
            </p>}</td>
            {/* La lettura del PDF puo' sbagliare una cifra: l'importo si corregge qui.
                Resta sempre positivo, il verso lo dice il tipo. */}
            <td className={`whitespace-nowrap p-2 tabular-nums ${spostamento ? 'text-[#28312f]' : outgoing ? 'text-[#c75f44]' : 'text-[#2d7b65]'}`}>
              <span aria-hidden>{spostamento ? '' : outgoing ? '−' : '+'}</span>
              <Input type="number" inputMode="decimal" min="0.01" step="0.01" aria-label={t('amount')} aria-invalid={row.selected && !(row.amount > 0)}
                value={Number.isNaN(row.amount) ? '' : row.amount} disabled={isSaving} className="ml-1 inline-block w-28 text-right"
                onChange={e => cambiaImporto(index, e.target.value === '' ? Number.NaN : Math.abs(Number(e.target.value)))} />
              {row.divisa && <p className={`text-[11px] ${sommaSbagliata(row) ? 'text-red-700' : 'text-[#71807c]'}`}>{t(sommaSbagliata(row) ? 'splitSumMismatch' : 'splitPartOf', { total: formatEuro(row.divisa.totale) })}</p>}
            </td>
            <td className="p-2"><select aria-label={t('type')} value={row.transactionType} disabled={isSaving} className="rounded border p-2"
              onChange={e => {
                const tipo = e.target.value as TipoMovimento;
                // Una categoria di spesa non vale per un'entrata: cambiando tipo si
                // tiene solo se esiste anche fra quelle del tipo nuovo.
                const tiene = !senzaCategoria && (categoriesByType[tipo] ?? []).includes(row.category);
                // Cambiando tipo la categoria puo' non valere piu': cade anche la
                // regola, altrimenti resterebbe scritto da dove veniva una
                // categoria che non c'e' piu'.
                updateRow(index, { transactionType: tipo, ...(tiene ? {} : { categoryAutomatic: true, category: '', categoryRule: null }) });
              }}>
              {TIPI.map(([tipo, etichetta]) => <option key={tipo} value={tipo}>{t(etichetta)}</option>)}
            </select></td>
            <td className="p-2"><select aria-label={t('account')} aria-invalid={row.selected && !row.accountName} value={row.accountName ?? ''} disabled={isSaving} className="rounded border p-2"
              onChange={e => updateRow(index, { accountName: e.target.value || null })}>{accountOptions}</select></td>
            <td className="p-2">{spostamento && <select aria-label={t('fieldDestinationAccount')} value={row.destinationName ?? ''} disabled={isSaving} className="rounded border p-2"
              onChange={e => updateRow(index, { destinationName: e.target.value || null })}>{accountOptions}</select>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
    {invalid && <p role="alert" className="text-xs text-[#a65b49]">{t('statementRequiredFields')}</p>}
    {feedback && <p role="status" className={`text-xs ${feedback.ok ? 'text-[#2d7b65]' : 'text-[#a65b49]'}`}>{feedback.message}</p>}
    <div className="flex shrink-0 justify-end gap-3">
      <Button variant="outline" disabled={isSaving} onClick={onCancel}>{t('cancel')}</Button>
      <Button disabled={isSaving || invalid || !selected.length} onClick={async () => {
        setIsSaving(true);
        try { await onConfirm(selected); } finally { setIsSaving(false); }
      }}>{isSaving ? t('savingEllipsis') : t('statementConfirm', { count: selected.length })}</Button>
    </div>
  </div>;
}
