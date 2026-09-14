'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';
import { expenseRulesPayload } from '@/lib/payloads';

// Cosa succede alle spese quando si va in pensione? Ogni categoria resta,
// sparisce o cambia importo. Importi e totale li calcola il server sugli
// stessi anni della spesa di riferimento, cosi' le categorie sommano al
// centesimo; qui si sceglie la regola e si salva.

type Mode = 'stay' | 'drop' | 'change';
type Row = { category: string; amount: number; mode: Mode; newAmount: number | null };
export type ExpenseRulesData = { configured: boolean; applies: boolean; referenceExpenses: number; retirementExpenses: number; categories: Row[] };

export function ExpensesByCategory({ apiUrl }: { apiUrl: string }) {
  const { t, formatEuro } = useI18n();
  const [data, setData] = useState<ExpenseRulesData | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [stato, setStato] = useState<'loading' | 'ready' | 'noProfile' | 'error'>('loading');
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/fire/expense-rules`, { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw new Error('rules');
        const payload = await r.json() as ExpenseRulesData;
        if (!payload.configured) { setStato('noProfile'); return; }
        setData(payload); setRows(payload.categories); setStato('ready');
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setStato('error');
      });
    return () => controller.abort();
  }, [apiUrl]);

  const update = (category: string, patch: Partial<Row>) =>
    setRows((current) => current.map((r) => r.category === category ? { ...r, ...patch } : r));

  // Anteprima del totale mentre si modifica: stessa regola del server, che
  // resta l'unico a salvarlo e a usarlo nel piano.
  const anteprima = data ? Math.max(0, data.referenceExpenses + rows.reduce((delta, r) =>
    delta + (r.mode === 'drop' ? -r.amount : r.mode === 'change' ? (r.newAmount ?? r.amount) - r.amount : 0), 0)) : 0;
  const incompleta = rows.some((r) => r.mode === 'change' && r.newAmount === null);

  async function save() {
    setBusy(true); setOutcome(null);
    try {
      const response = await fetch(`${apiUrl}/api/fire/expense-rules`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(expenseRulesPayload(rows)),
      });
      if (!response.ok) throw new Error('save');
      const payload = await response.json() as ExpenseRulesData;
      setData(payload); setRows(payload.categories);
      setOutcome({ ok: true, message: t('fireExpensesCategorySaved') });
    } catch {
      setOutcome({ ok: false, message: t('fireProfileSaveError') });
    } finally { setBusy(false); }
  }

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('fireExpensesByCategoryTitle')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('fireExpensesByCategorySubtitle')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        {stato === 'loading' && <p className="text-sm text-[#7b8784]">{t('loading')}</p>}
        {stato === 'noProfile' && <p className="text-sm text-[#7b8784]">{t('fireExpensesCategoryNoProfile')}</p>}
        {stato === 'error' && <p role="alert" className="text-sm text-[#bd5e46]">{t('fireExpensesCategoryLoadError')}</p>}
        {stato === 'ready' && data && !data.applies && <p className="text-sm text-[#7b8784]">{t('fireExpensesCategoryCustom')}</p>}
        {stato === 'ready' && data?.applies && (rows.length === 0
          ? <p className="text-sm text-[#7b8784]">{t('fireExpensesCategoryEmpty')}</p>
          : <>
            <ul className="divide-y divide-black/5 rounded-xl border border-black/6 bg-white">
              {rows.map((r) => (
                <li key={r.category} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <span className="min-w-[120px] flex-1">
                    <span className="block text-sm font-medium text-[#3a4a46]">{r.category}</span>
                    <span className="block text-xs tabular-nums text-[#87918e]">{t('fireExpensesCategoryPerYear', { amount: formatEuro(r.amount) })}</span>
                  </span>
                  <select value={r.mode} aria-label={r.category}
                    onChange={(e) => update(r.category, { mode: e.target.value as Mode })}
                    className="h-9 rounded-lg border border-input bg-white px-2 text-sm outline-none focus:border-ring">
                    <option value="stay">{t('fireExpensesCategoryStay')}</option>
                    <option value="drop">{t('fireExpensesCategoryDrop')}</option>
                    <option value="change">{t('fireExpensesCategoryChange')}</option>
                  </select>
                  {r.mode === 'change' && (
                    <Input type="number" min={0} step="0.01" aria-label={t('fireExpensesCategoryNewAmount')}
                      placeholder={t('fireExpensesCategoryNewAmount')} value={r.newAmount ?? ''}
                      onChange={(e) => update(r.category, { newAmount: e.target.value === '' ? null : Number(e.target.value) })}
                      className="h-9 w-36 bg-white" />
                  )}
                </li>
              ))}
            </ul>
            <p className="text-sm font-medium tabular-nums text-[#3a4a46]">
              {t('fireExpensesRetirementTotal', { retirement: formatEuro(anteprima), today: formatEuro(data.referenceExpenses) })}
            </p>
            {outcome && (
              <p role="status" className={`rounded-xl border px-4 py-2 text-sm ${outcome.ok ? 'border-[#cfe6dc] bg-[#e5f3ed] text-[#2d7b65]' : 'border-[#f4d8ce] bg-[#fce9e3] text-[#bd5e46]'}`}>
                {outcome.message}
              </p>
            )}
            <div className="flex justify-end">
              <Button onClick={() => void save()} disabled={busy || incompleta}
                className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
                {busy ? '…' : t('fireExpensesCategorySave')}
              </Button>
            </div>
          </>)}
      </CardContent>
    </Card>
  );
}
