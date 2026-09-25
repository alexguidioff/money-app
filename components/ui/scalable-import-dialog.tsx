'use client';

import { useState } from 'react';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useI18n } from '@/lib/i18n-context';
import { scalableImportPayload, scalablePreviewPayload, type ScalableDecisions } from '@/lib/payloads';
import { messaggioImportScalable, motivoRifiuto } from '@/lib/scalable-errors';

/** Una riga di banca che puo' essere lo stesso movimento di un versamento. */
export type ScalableBankRow = {
  id: number; date: string; amount: number; account: string | null; details: string | null; gapDays: number;
};

/** Un versamento o un prelievo, con la riga di banca che ha preso o che poteva prendere. */
export type ScalableBankLink = {
  row: number; date: string; kind: string; amount: number; matched: boolean;
  gapDays: number | null; bank: ScalableBankRow | null; candidates: ScalableBankRow[];
};

export type ScalableInstrument = {
  nome: string; grafie: string[]; quote: number; righe: number; prima: string; ultima: string;
  candidati: Array<{ symbol: string; name: string; exchange: string | null; type: string | null }>;
};

/**
 * L'anteprima di un elenco di Scalable, come la manda il server.
 *
 * E' il tipo con cui il frontend legge la risposta, e i test di contratto la
 * compilano contro quella vera: un campo rinominato nel backend si vede qui, e
 * non a schermo quando qualcuno importa la sua storia.
 */
export type ScalablePreview = {
  user: { id: number; username: string | null; displayName: string | null };
  counts: { read: number; importable: number; rejected: number; byType: Record<string, number> };
  deposits: { totali: number; conRiga: number; daFuori: number; importoFuori: number };
  bankLinks: ScalableBankLink[];
  rejected: Array<{ row: number; text: string; reason: string }>;
  instruments: ScalableInstrument[];
  accounts: Array<{ nome: string; broker: boolean; esiste: boolean; saldo: number }>;
  interest: { rows: number; amount: number; category: string; categories: string[] };
  totals: { contante: number; titoli: number; fuori: number; nonTracciato: number };
};

/** Lo strumento in anteprima piu' le due cose che si possono cambiare: nome e ticker. */
type StrumentoInAnteprima = ScalableInstrument & { ticker: string };

/**
 * L'import di un elenco copiato dall'interfaccia di Scalable.
 *
 * Si incolla il testo e si guarda cosa diventerebbe: le righe lette, quelle
 * scartate col motivo, gli strumenti con le loro grafie e le quote finali, i
 * versamenti con la riga di banca che hanno preso. Non si scrive niente fino
 * alla conferma, e quello che si scrive lo rilegge il server dallo stesso testo:
 * il browser manda le decisioni, non i movimenti.
 *
 * Gli acquisti non si elencano uno per uno: sono centinaia di righe uguali, e
 * una tabella cosi' non si legge. Restano raggruppati per strumento, e le scelte
 * a mano stanno dove servono davvero, cioe' sugli agganci di banca.
 */
export function ScalableImportDialog({ open, apiUrl, onClose, onDone }: {
  open: boolean;
  apiUrl: string;
  onClose: () => void;
  onDone: (esito: { ok: boolean; message: string }) => Promise<void> | void;
}) {
  const { t, formatEuro, formatDate } = useI18n();
  const [testo, setTesto] = useState('');
  const [anteprima, setAnteprima] = useState<ScalablePreview | null>(null);
  const [strumenti, setStrumenti] = useState<StrumentoInAnteprima[]>([]);
  // La riga di banca scelta per ogni versamento, per numero di riga del testo:
  // assente vuol dire "quella automatica", `null` vuol dire "nessuna".
  const [agganci, setAgganci] = useState<Record<number, number | null>>({});
  const [categoria, setCategoria] = useState('');
  const [busy, setBusy] = useState(false);
  const [errore, setErrore] = useState('');

  function azzera() {
    setTesto(''); setAnteprima(null); setStrumenti([]); setAgganci({}); setCategoria(''); setErrore('');
  }

  async function chiediAnteprima() {
    setBusy(true); setErrore('');
    try {
      const response = await fetch(`${apiUrl}/api/import/scalable/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scalablePreviewPayload(testo)),
      });
      if (!response.ok) throw new Error(await messaggioImportScalable(response, t));
      const dati = await response.json() as ScalablePreview;
      setAnteprima(dati);
      setStrumenti(dati.instruments.map((strumento) => ({ ...strumento, ticker: '' })));
      setAgganci(Object.fromEntries(dati.bankLinks.map((link) => [link.row, link.bank?.id ?? null])));
      // La categoria degli interessi si sceglie fra quelle che ci sono: quella
      // proposta dal server vale se esiste, altrimenti si parte dalla prima.
      setCategoria(dati.interest.categories.includes(dati.interest.category)
        ? dati.interest.category : (dati.interest.categories[0] ?? ''));
    } catch (caught) {
      setErrore(caught instanceof Error && caught.message ? caught.message : t('scalableFailed'));
    } finally { setBusy(false); }
  }

  async function conferma() {
    if (!anteprima) return;
    setBusy(true); setErrore('');
    const decisioni: ScalableDecisions = {
      testo, utente: anteprima.user.id, strumenti,
      agganci: Object.entries(agganci).map(([riga, transaction_id]) => ({ riga: Number(riga), transaction_id })),
      categoriaInteressi: categoria,
    };
    try {
      const response = await fetch(`${apiUrl}/api/import/scalable`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scalableImportPayload(decisioni)),
      });
      if (!response.ok) throw new Error(await messaggioImportScalable(response, t));
      const esito = await response.json() as { movements: number; rejected: number };
      await onDone({ ok: true, message: t('scalableSaved', { movements: esito.movements, rejected: esito.rejected }) });
      azzera(); onClose();
    } catch (caught) {
      setErrore(caught instanceof Error && caught.message ? caught.message : t('scalableFailed'));
    } finally { setBusy(false); }
  }

  const celle = 'px-3 py-2 text-left align-top';
  const celleNumero = 'px-3 py-2 text-right align-top tabular-nums';

  return <Dialog open={open} onOpenChange={(next) => { if (!next) { azzera(); onClose(); } }}>
    <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[95vw]">
      <DialogHeader>
        <DialogTitle>{t('scalableImport')}</DialogTitle>
        <DialogDescription>{t('scalableHint')}</DialogDescription>
      </DialogHeader>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto pr-1">
        {!anteprima ? (
          <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('scalableImport')}
            <Textarea value={testo} onChange={(event) => setTesto(event.target.value)} rows={14}
              spellCheck={false} className="bg-white font-mono text-xs" placeholder={t('scalableHint')} />
          </label>
        ) : <>
          <div className="space-y-1 rounded-lg border border-black/8 bg-[#fafaf8] px-4 py-3 text-sm">
            <p className="font-medium">{t('scalableCounts', { read: anteprima.counts.read, importable: anteprima.counts.importable, rejected: anteprima.counts.rejected })}</p>
            {/* Su chi si sta scrivendo: senza, un import fatto mentre si guarda
                lo schermo di un'altra persona finirebbe su quella persona. */}
            <p className="text-xs text-[#5e6c68]">{t('scalableOnUser', { user: anteprima.user.displayName || anteprima.user.username || String(anteprima.user.id) })}</p>
          </div>

          <div>
            <h3 className="mb-2 text-sm font-semibold">{t('scalableAccounts')}</h3>
            <table className="w-full text-sm"><tbody className="divide-y divide-black/5">
              {anteprima.accounts.map((conto) => <tr key={conto.nome}>
                <td className={celle}>{conto.nome}</td>
                <td className={celleNumero}>{formatEuro(conto.saldo)}</td>
                <td className={`${celle} text-xs text-[#5e6c68]`}>{conto.esiste ? t('scalableAccountExists') : ''}</td>
              </tr>)}
            </tbody></table>
          </div>

          <div>
            <h3 className="mb-2 text-sm font-semibold">{t('scalableInstruments')}</h3>
            <div className="overflow-x-auto rounded-lg border border-black/8">
              <table className="min-w-[820px] w-full text-sm">
                <thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr>
                  <th className={celle}>{t('instrument')}</th><th className={celle}>{t('scalableSpellings')}</th>
                  <th className={celle}>{t('scalableTicker')}</th><th className={celleNumero}>{t('scalableFinalUnits')}</th>
                </tr></thead>
                <tbody className="divide-y divide-black/5">
                  {strumenti.map((strumento, indice) => <tr key={strumento.grafie.join('|')}>
                    <td className={celle}>
                      {/* Due strumenti che qui prendono lo stesso nome diventano
                          uno solo: e' cosi' che si uniscono due grafie che il
                          lettore non ha riconosciuto come lo stesso fondo. */}
                      <Input value={strumento.nome} aria-label={t('instrument')}
                        onChange={(event) => setStrumenti((correnti) => correnti.map((altro, i) =>
                          i === indice ? { ...altro, nome: event.target.value } : altro))} />
                    </td>
                    <td className={`${celle} text-xs text-[#5e6c68]`}>
                      {strumento.grafie.map((grafia) => <p key={grafia}>{grafia}</p>)}
                      <p className="mt-1">{formatDate(`${strumento.prima}T12:00:00`)}–{formatDate(`${strumento.ultima}T12:00:00`)}</p>
                    </td>
                    <td className={celle}>
                      <Input value={strumento.ticker} aria-label={t('scalableTicker')} placeholder={t('optional')}
                        list={`scalable-ticker-${indice}`} className="w-40"
                        onChange={(event) => setStrumenti((correnti) => correnti.map((altro, i) =>
                          i === indice ? { ...altro, ticker: event.target.value } : altro))} />
                      <datalist id={`scalable-ticker-${indice}`}>
                        {strumento.candidati.map((candidato) => <option key={candidato.symbol} value={candidato.symbol}>{candidato.name}</option>)}
                      </datalist>
                      {strumento.candidati.length > 0 && <p className="mt-1 text-[10px] text-[#5e6c68]">{strumento.candidati.map((candidato) => candidato.symbol).join(' · ')}</p>}
                    </td>
                    <td className={celleNumero}>{strumento.quote}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-sm font-semibold">{t('scalableBankTitle')}</h3>
            <p className="mb-2 text-xs text-[#5e6c68]">{t('scalableCounts', { read: anteprima.deposits.totali, importable: anteprima.deposits.conRiga, rejected: anteprima.deposits.daFuori })}</p>
            <div className="overflow-x-auto rounded-lg border border-black/8">
              <table className="min-w-[820px] w-full text-sm">
                <thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr>
                  <th className={celle}>{t('date')}</th><th className={celle}>{t('type')}</th>
                  <th className={celleNumero}>{t('amount')}</th><th className={celle}>{t('account')}</th>
                </tr></thead>
                <tbody className="divide-y divide-black/5">
                  {anteprima.bankLinks.map((link) => {
                    const scelto = agganci[link.row];
                    const opzioni = [link.bank, ...link.candidates].filter((riga): riga is ScalableBankRow => riga !== null);
                    return <tr key={link.row}>
                      <td className={celle}>{formatDate(`${link.date}T12:00:00`)}</td>
                      <td className={celle}>{link.kind}</td>
                      <td className={celleNumero}>{formatEuro(link.amount)}</td>
                      <td className={celle}>
                        <select value={scelto === null || scelto === undefined ? '' : String(scelto)}
                          aria-label={t('scalableBankTitle')}
                          onChange={(event) => setAgganci((correnti) => ({
                            ...correnti, [link.row]: event.target.value ? Number(event.target.value) : null }))}
                          className="h-9 w-full rounded-lg border border-input bg-white px-2 text-xs">
                          <option value="">{t('scalableBankNone')}</option>
                          {opzioni.map((riga) => <option key={riga.id} value={riga.id}>
                            {`${formatDate(`${riga.date}T12:00:00`)} · ${riga.account ?? ''} · ${formatEuro(riga.amount)} · ${riga.gapDays === 0 ? t('scalableBankSameDay') : t('scalableBankGap', { count: riga.gapDays })}`}
                          </option>)}
                        </select>
                      </td>
                    </tr>;
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-black/8 bg-[#fafaf8] px-4 py-3 text-sm">
              <h3 className="mb-2 text-sm font-semibold">{t('scalableTotals')}</h3>
              <p className="flex justify-between"><span className="text-[#5e6c68]">{t('scalableCash')}</span><span className="tabular-nums">{formatEuro(anteprima.totals.contante)}</span></p>
              <p className="flex justify-between"><span className="text-[#5e6c68]">{t('scalableSecurities')}</span><span className="tabular-nums">{formatEuro(anteprima.totals.titoli)}</span></p>
              <p className="flex justify-between"><span className="text-[#5e6c68]">{t('scalableOutside')}</span><span className="tabular-nums">{formatEuro(anteprima.totals.nonTracciato)}</span></p>
              {/* Perche' il conto di fuori sta nel patrimonio: senza, lo stesso
                  denaro si conterebbe due volte. */}
              <p className="mt-2 text-[11px] leading-4 text-[#5e6c68]">{t('scalableOutsideHint')}</p>
            </div>
            <div className="rounded-lg border border-black/8 bg-[#fafaf8] px-4 py-3 text-sm">
              <h3 className="mb-2 text-sm font-semibold">{t('scalableInterestCategory')}</h3>
              {/* Un interesse e' un'entrata, e un'entrata ha bisogno di una
                  categoria: chi importa sceglie quale, invece di trovarsela. */}
              <p className="mb-2 flex justify-between"><span className="text-[#5e6c68]">{t('amount')}</span><span className="tabular-nums">{formatEuro(anteprima.interest.amount)}</span></p>
              <select value={categoria} aria-label={t('scalableInterestCategory')}
                onChange={(event) => setCategoria(event.target.value)}
                className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm">
                {anteprima.interest.categories.map((nome) => <option key={nome} value={nome}>{nome}</option>)}
              </select>
            </div>
          </div>

          {anteprima.rejected.length > 0 && <div>
            <h3 className="mb-2 text-sm font-semibold">{t('scalableRejectedTitle')}</h3>
            <div className="overflow-x-auto rounded-lg border border-black/8">
              <table className="min-w-[720px] w-full text-sm">
                <thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr>
                  {/* Il numero di riga del testo sta accanto a quello che c'era
                      scritto: e' quello che serve per ritrovarla nell'elenco. */}
                  <th className={celle}>{t('description')}</th><th className={celle}>{t('scalableReason')}</th>
                </tr></thead>
                <tbody className="divide-y divide-black/5">
                  {anteprima.rejected.map((rifiuto) => <tr key={rifiuto.row}>
                    <td className={`${celle} text-xs`}><span className="mr-2 tabular-nums text-[#5e6c68]">{rifiuto.row}</span>{rifiuto.text}</td>
                    <td className={celle}>{motivoRifiuto(rifiuto.reason, t)}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>
          </div>}
        </>}
        {errore && <p role="alert" className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a94f3a]">{errore}</p>}
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={() => { azzera(); onClose(); }}>{t('cancel')}</Button>
        {anteprima
          ? <Button disabled={busy} onClick={() => void conferma()} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
            <Upload className="size-4" />{t('statementConfirm', { count: anteprima.counts.importable })}
          </Button>
          : <Button disabled={busy || !testo.trim()} onClick={() => void chiediAnteprima()}>{t('statementPreview')}</Button>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
