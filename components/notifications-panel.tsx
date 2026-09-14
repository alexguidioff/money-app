'use client';

import { X } from 'lucide-react';
import type { TranslationKey } from '@/lib/translations';
import { useI18n } from '@/lib/i18n-context';

export type Notification = {
  key: string;
  code: string;
  level: 'warning' | 'info';
  params: Record<string, string | number>;
};

// Il server manda un codice e i suoi valori, non una frase: il testo si compone
// qui, nella lingua scelta, come per gli errori della fonte quotazioni.
const TESTI: Record<string, TranslationKey> = {
  incompleteMovements: 'notifIncompleteMovements',
  budgetOverrun: 'notifBudgetOverrun',
  staleQuote: 'notifStaleQuote',
  missingQuote: 'notifMissingQuote',
  missingFx: 'notifMissingFx',
  instrumentsWithoutTicker: 'notifInstrumentsWithoutTicker',
  budgetEnding: 'notifBudgetEnding',
  emptyMonth: 'notifEmptyMonth',
  backupMissing: 'notifBackupMissing',
  backupOld: 'notifBackupOld',
  valuationStale: 'notifValuationStale',
};

export function NotificationsPanel({ items, onDismiss, onDismissAll }: {
  items: Notification[];
  onDismiss: (key: string) => Promise<void>;
  onDismissAll: () => Promise<void>;
}) {
  const { t, locale } = useI18n();

  // Il server manda i periodi come 2026-08: il nome del mese lo scriviamo qui,
  // nella lingua giusta.
  const conPeriodo = (params: Record<string, string | number>) => {
    const periodo = params.period;
    if (typeof periodo !== 'string' || !/^\d{4}-\d{2}$/.test(periodo)) return params;
    return {
      ...params,
      period: new Date(`${periodo}-01T12:00:00`).toLocaleDateString(locale, { month: 'long', year: 'numeric' }),
    };
  };

  return (
    <div className="absolute right-10 top-12 z-50 w-80 rounded-xl border border-black/7 bg-white p-4 shadow-xl">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-semibold">{t('notifications')}</p>
        {items.length > 0 && (
          <button onClick={() => void onDismissAll()} className="text-xs font-medium text-[#397867] hover:underline">
            {t('notifDismissAll')}
          </button>
        )}
      </div>

      {items.length === 0 && (
        <p className="mt-2 text-xs leading-5 text-[#71807c]">{t('notificationsUpToDate')}</p>
      )}

      <ul className="mt-2 max-h-80 space-y-1.5 overflow-y-auto">
        {items.map((avviso) => (
          <li key={avviso.key}
              className={`flex items-start gap-2 rounded-lg px-2.5 py-2 ${
                avviso.level === 'warning' ? 'bg-[#fff6f3]' : 'bg-[#f4f5f1]'}`}>
            <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
              avviso.level === 'warning' ? 'bg-[#bd5e46]' : 'bg-[#87918e]'}`} />
            <span className="flex-1 text-xs leading-5 text-[#3d4a47]">
              {TESTI[avviso.code] ? t(TESTI[avviso.code], conPeriodo(avviso.params)) : avviso.code}
            </span>
            <button onClick={() => void onDismiss(avviso.key)} title={t('notifDismiss')} aria-label={t('notifDismiss')}
                    className="mt-0.5 text-[#a3adaa] transition hover:text-[#3d4a47]">
              <X className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
