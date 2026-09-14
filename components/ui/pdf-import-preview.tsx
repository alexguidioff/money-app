import { useEffect, useState } from 'react';
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
  date: string | null;
  amount: number;
  type: 'income' | 'expense' | 'saving' | 'transfer';
  transactionType: 'Income' | 'Expenses' | 'Transfers';
  accountName: string | null;
  destinationName: string | null;
  goal: string | null;
  details: string;
  duplicate?: boolean;
  duplicateOf?: { id: number; date: string; amount: number; description: string } | null;
  errorCode?: string;
};

export function PDFImportPreview({ transactions, accounts, onConfirm, onCancel, feedback }: {
  transactions: PDFTransaction[];
  accounts: { name: string }[];
  onConfirm: (approvedTransactions: PDFTransaction[]) => Promise<void>;
  onCancel: () => void;
  feedback: { ok: boolean; message: string } | null;
}) {
  const { t, formatEuro, formatDate } = useI18n();
  const [rows, setRows] = useState(() => transactions.map(row => ({ ...row, selected: !row.duplicate })));
  const [isSaving, setIsSaving] = useState(false);
  const [allAccount, setAllAccount] = useState('');
  useEffect(() => {
    setRows(transactions.map(row => ({ ...row, selected: !row.duplicate })));
  }, [transactions]);
  const updateRow = (index: number, patch: Partial<(typeof rows)[number]>) =>
    setRows(current => current.map((row, i) => i === index ? { ...row, ...patch } : row));
  const selected = rows.filter(row => row.selected);
  const invalid = selected.some(row => !row.date || !row.accountName || row.amount <= 0 ||
    (row.transactionType === 'Transfers' && (!row.destinationName || row.destinationName === row.accountName)));
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
          <th className="p-2">{t('statementSelect')}</th><th>{t('date')}</th><th>{t('description')}</th>
          <th>{t('category')}</th><th>{t('amount')}</th><th>{t('type')}</th><th>{t('account')}</th><th>{t('fieldDestinationAccount')}</th>
        </tr></thead>
        <tbody>{rows.map((row, index) => {
          // Il risparmio non e' un tipo di movimento: si deriva da entrate e spese, e
          // una riga "Risparmi" veniva rifiutata al salvataggio.
          const outgoing = row.transactionType === 'Expenses' || row.transactionType === 'Transfers';
          return <tr key={index} className="border-b">
            <td className="p-2"><input type="checkbox" aria-label={t('statementSelectRow', { row: index + 1 })} checked={row.selected} disabled={isSaving}
              onChange={e => updateRow(index, { selected: e.target.checked })} /></td>
            <td className="p-2"><Input type="date" aria-label={t('date')} aria-invalid={row.selected && !row.date} value={row.date ?? ''} disabled={isSaving}
              onChange={e => updateRow(index, { date: e.target.value })} className="w-36" /></td>
            <td className="min-w-44 p-2">{row.description}{row.duplicate && <p className="text-xs text-amber-700">{t('statementDuplicate')}{row.duplicateOf && <> · #{row.duplicateOf.id} · {formatDate(row.duplicateOf.date)} · {formatEuro(row.duplicateOf.amount)} · {row.duplicateOf.description}</>}</p>}
              {row.errorCode && <p className="text-xs text-red-700">{t(Object.hasOwn(translations.it, row.errorCode) ? row.errorCode as TranslationKey : 'statementRowInvalid')}</p>}</td>
            <td className="p-2"><Input aria-label={t('category')} value={row.category} disabled={isSaving} onChange={e => updateRow(index, { category: e.target.value, categoryAutomatic: false })} className="w-40" /></td>
            <td className={`whitespace-nowrap p-2 tabular-nums ${outgoing ? 'text-[#c75f44]' : 'text-[#2d7b65]'}`}>{outgoing ? '−' : '+'}{formatEuro(row.amount)}</td>
            <td className="p-2"><select aria-label={t('type')} value={row.transactionType} disabled={isSaving} className="rounded border p-2"
              onChange={e => updateRow(index, { transactionType: e.target.value as PDFTransaction['transactionType'] })}>
              <option value="Income">{t('incomeType')}</option><option value="Expenses">{t('expensesType')}</option>
              <option value="Transfers">{t('transfersType')}</option>
            </select></td>
            <td className="p-2"><select aria-label={t('account')} aria-invalid={row.selected && !row.accountName} value={row.accountName ?? ''} disabled={isSaving} className="rounded border p-2"
              onChange={e => updateRow(index, { accountName: e.target.value || null })}>{accountOptions}</select></td>
            <td className="p-2">{row.transactionType === 'Transfers' && <select aria-label={t('fieldDestinationAccount')} value={row.destinationName ?? ''} disabled={isSaving} className="rounded border p-2"
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
