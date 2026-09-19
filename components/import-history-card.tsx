import { useEffect, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useI18n } from '@/lib/i18n-context';
import { translations, type TranslationKey } from '@/lib/translations';

export type ImportBatchRow = {
  id: number;
  /** 'statement' per un estratto conto, 'interchange' per il ripristino di un backup. */
  kind: string;
  sourceName: string;
  importedAt: string | null;
  accepted: number;
  rejected: number;
  /** I motivi di scarto contati per codice, come li ha scritti l'import. */
  reasons: Record<string, number>;
  transactionCount: number;
};

/**
 * Lo storico degli import, in fondo ai Movimenti.
 *
 * Si guarda dopo un import, non tutti i giorni: sta chiuso, e l'elenco si
 * chiede solo quando si apre. Sta accanto a dove si importa perche' e' li' che
 * viene la domanda a cui risponde - "questo import cos'ha fatto?".
 */
export function ImportHistoryCard({ apiUrl, versione }: { apiUrl: string; versione: number }) {
  const { t, formatDate } = useI18n();
  const [aperto, setAperto] = useState(false);
  const [righe, setRighe] = useState<ImportBatchRow[] | null>(null);
  // Si richiede quando cambia `versione`, cioe' dopo un import, ma solo a card
  // aperta: chiusa non la guarda nessuno, e sarebbe una richiesta per ogni
  // import fatta per niente.
  useEffect(() => {
    if (!aperto) return undefined;
    const controller = new AbortController();
    fetch(`${apiUrl}/api/import-batches?limit=20`, { signal: controller.signal })
      .then((risposta) => risposta.ok ? risposta.json() as Promise<{ items: ImportBatchRow[] }> : Promise.reject(new Error('storico-import')))
      .then((dati) => setRighe(dati.items))
      .catch(() => undefined);
    return () => controller.abort();
  }, [apiUrl, aperto, versione]);

  // I motivi sono codici, come quelli che l'anteprima mostra riga per riga: si
  // leggono con le stesse parole, invece di inventare una seconda traduzione
  // per la stessa cosa.
  const motivo = (codice: string) => t(Object.hasOwn(translations.it, codice) ? codice as TranslationKey : 'statementRowInvalid');

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <button type="button" aria-expanded={aperto} onClick={() => setAperto((corrente) => !corrente)} className="w-full text-left">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-[17px]">
            <ChevronDown className={`size-4 shrink-0 text-black/35 transition ${aperto ? 'rotate-180' : ''}`} />
            {t('importHistory')}
          </CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('importHistoryHint')}</p>
        </CardHeader>
      </button>
      {aperto && <CardContent className="px-3 sm:px-6">
        {righe === null ? <p className="py-8 text-center text-sm text-[#71807c]">{t('updating')}</p>
          : !righe.length ? <p className="py-8 text-center text-sm text-[#71807c]">{t('importHistoryEmpty')}</p>
            : <div className="divide-y divide-black/5">{righe.map((riga) => <div key={riga.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-3.5">
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                  <span className="rounded-full bg-[#f4f5f1] px-2 py-0.5 text-[10px] font-normal text-[#7b8784]">
                    {t(riga.kind === 'interchange' ? 'importHistoryInterchange' : 'importHistoryStatement')}
                  </span>
                  <span className="truncate">{riga.sourceName}</span>
                </span>
                <span className="mt-1 block text-xs text-[#87918e]">
                  {riga.importedAt ? formatDate(riga.importedAt, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                </span>
              </span>
              {/* Due import non si leggono allo stesso modo: un ripristino dice
                  quante righe sono tornate dentro, un estratto conto quante ne
                  sono entrate e quante no - con il motivo, che e' la parte su
                  cui si puo' ancora fare qualcosa. */}
              {riga.kind === 'interchange'
                ? <span className="text-sm tabular-nums text-[#52615d]">{t('importHistoryRestored', { count: riga.transactionCount })}</span>
                : <span className="min-w-0 flex-1 text-xs text-[#52615d]">
                  <span className="text-sm tabular-nums">{t('importHistoryAccepted', { count: riga.accepted })}</span>
                  {riga.rejected > 0 && <span className="text-sm tabular-nums text-[#bd5e46]"> · {t('importHistoryRejected', { count: riga.rejected })}</span>}
                  {Object.entries(riga.reasons).map(([codice, quante]) => <span key={codice} className="block text-[#87918e]">{motivo(codice)} ({quante})</span>)}
                </span>}
            </div>)}</div>}
      </CardContent>}
    </Card>
  );
}
