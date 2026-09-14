import { useEffect, useState } from 'react';
import { useI18n } from '@/lib/i18n-context';
import { Input } from './input';
import { Button } from './button';

export function RefundPicker({ apiUrl, initialId, transactionType }: { apiUrl: string; initialId: number | null; transactionType: 'Income' | 'Expenses' }) {
  const { t, formatEuro, formatDate } = useI18n();
  const [selected, setSelected] = useState(initialId);
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<{ id: string; description: string; occurredOn: string; amount: number }[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    if (!query.trim()) { setItems([]); return; }
    const timer = setTimeout(() => {
      setError(false);
      fetch(`${apiUrl}/api/transactions?${new URLSearchParams({ search: query, transaction_type: transactionType === 'Income' ? 'Expenses' : 'Income', limit: '20' })}`, { signal: controller.signal })
        .then(response => { if (!response.ok) throw new Error(); return response.json() as Promise<{ items: typeof items }>; })
        .then(data => setItems(data.items))
        .catch(() => { if (!controller.signal.aborted) setError(true); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [apiUrl, query, transactionType]);
  return <div className="space-y-2 text-sm">
    <input type="hidden" name="refund_of_id" value={selected ?? ''} />
    <label>{t('refundOf')}<Input value={query} onChange={e => setQuery(e.target.value)} placeholder={t('searchInMovements')} /></label>
    {selected && <p>{t('refundSelected', { id: selected })} <Button type="button" variant="outline" onClick={() => setSelected(null)}>{t('unlinkRefund')}</Button></p>}
    {error && <p role="alert">{t('bulkFailed')}</p>}
    <div className="max-h-48 overflow-auto">{items.map(item => <button type="button" key={item.id} className="block w-full border-b p-2 text-left hover:bg-slate-50"
      onClick={() => { setSelected(Number(item.id)); setQuery(''); }}>{formatDate(item.occurredOn)} · {item.description} · {formatEuro(Math.abs(item.amount))}</button>)}</div>
  </div>;
}
