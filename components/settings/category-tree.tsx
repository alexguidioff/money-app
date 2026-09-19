'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';
import type { TranslationKey } from '@/lib/translations';

/** Una categoria come la manda l'API: dov'e' nell'albero, e chi la usa. */
export type CategoryRow = {
  id: number;
  name: string;
  parentId: number | null;
  position: number;
  active: boolean;
  movements: number;
  budgets: number;
  rules: number;
  children: number;
};

type Dettaglio = string | { code?: string; movements?: number; budgets?: number; rules?: number } | null;
type Traduttore = (key: TranslationKey, params?: Record<string, string | number>) => string;

/** Perche' la modifica non e' passata, detto come lo direbbe l'app. */
function messaggio(dettaglio: Dettaglio, t: Traduttore): string {
  const codice = typeof dettaglio === 'string' ? dettaglio : dettaglio?.code;
  if (codice === 'categoryExists') return t('catErrorExists');
  if (codice === 'categoryTooDeep' || codice === 'categoryOwnParent') return t('catErrorTooDeep');
  if (codice === 'categoryHasChildren') return t('catErrorHasChildren');
  if (codice === 'categoryNameRequired') return t('catErrorName');
  if (codice === 'categoryInUse' && dettaglio && typeof dettaglio === 'object') {
    return t('catErrorInUse', { movements: dettaglio.movements ?? 0, budgets: dettaglio.budgets ?? 0,
                                rules: dettaglio.rules ?? 0 });
  }
  return t('catErrorGeneric');
}

/**
 * L'albero delle categorie, dove si costruisce: una card nelle Impostazioni.
 *
 * Due livelli, con le frecce per l'ordine e un menu "sposta sotto" per
 * cambiare padre. Niente trascinamento: le frecce funzionano anche da telefono,
 * e un dito su uno schermo piccolo non distingue "sposta sopra" da "sposta
 * dentro".
 *
 * Un padre con figli non si sposta - sotto di lui ci sarebbe un terzo livello -
 * e non si cancella finche' i figli sono li'. Le categorie che qualcuno usa non
 * si cancellano: si mettono via, e restano nella storia dei movimenti che le
 * hanno gia' usate.
 */
export function CategoryTreeCard({ apiUrl, onChanged }: { apiUrl: string; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [righe, setRighe] = useState<CategoryRow[]>([]);
  const [nuova, setNuova] = useState('');
  const [sotto, setSotto] = useState<number | null>(null);
  const [figlia, setFiglia] = useState('');
  const [errore, setErrore] = useState('');
  const [inCorso, setInCorso] = useState(false);

  const carica = useCallback(async (signal?: AbortSignal) => {
    const risposta = await fetch(`${apiUrl}/api/categories`, { signal });
    if (!risposta.ok) throw new Error('categories');
    const dati = await risposta.json() as { items: CategoryRow[] };
    setRighe(dati.items ?? []);
  }, [apiUrl]);

  useEffect(() => {
    const controller = new AbortController();
    carica(controller.signal).catch((err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      setErrore(t('catErrorGeneric'));
    });
    return () => controller.abort();
  }, [carica, t]);

  /**
   * Manda la modifica e rilegge l'albero: l'ordine e i legami li decide il
   * server, e rifarli qui vorrebbe dire due idee diverse di com'e' fatto.
   */
  async function chiama(percorso: string, metodo: string, corpo?: unknown): Promise<boolean> {
    setInCorso(true);
    setErrore('');
    try {
      const risposta = await fetch(`${apiUrl}${percorso}`, {
        method: metodo,
        headers: corpo === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: corpo === undefined ? undefined : JSON.stringify(corpo),
      });
      if (!risposta.ok) {
        const dettaglio = await risposta.json().catch(() => null) as { detail?: Dettaglio } | null;
        setErrore(messaggio(dettaglio?.detail ?? null, t));
        return false;
      }
      await carica();
      // Le tendine dei movimenti si costruiscono sull'albero: senza ricaricare,
      // una categoria appena creata non comparirebbe dove serve.
      await onChanged();
      return true;
    } catch {
      setErrore(t('catErrorGeneric'));
      return false;
    } finally {
      setInCorso(false);
    }
  }

  async function aggiungi(nome: string, parentId: number | null) {
    const pulito = nome.trim();
    if (!pulito) {
      setErrore(t('catErrorName'));
      return;
    }
    if (await chiama('/api/categories', 'POST', { name: pulito, parentId })) {
      if (parentId === null) setNuova('');
      else { setFiglia(''); setSotto(null); }
    }
  }

  async function rinomina(riga: CategoryRow, nome: string) {
    const pulito = nome.trim();
    if (!pulito || pulito === riga.name) return;
    await chiama(`/api/categories/${riga.id}`, 'PATCH', { name: pulito });
  }

  /** Un gradino oltre la categoria vicina: cosi' l'ordine cambia anche quando
   * due categorie hanno la stessa posizione, che e' il caso di quelle appena
   * create. */
  async function sposta(riga: CategoryRow, passo: -1 | 1) {
    const gruppo = righe.filter((altra) => altra.parentId === riga.parentId);
    const vicina = gruppo[gruppo.findIndex((altra) => altra.id === riga.id) + passo];
    if (!vicina) return;
    await chiama(`/api/categories/${riga.id}`, 'PATCH', { position: vicina.position + passo });
  }

  async function spostaSotto(riga: CategoryRow, valore: string) {
    if (!valore) return;
    const padre = valore === 'radice' ? null : Number(valore);
    await chiama(`/api/categories/${riga.id}`, 'PATCH', { parentId: padre });
  }

  async function cancella(riga: CategoryRow) {
    if (!window.confirm(t('catConfirmDelete', { name: riga.name }))) return;
    await chiama(`/api/categories/${riga.id}`, 'DELETE');
  }

  const radici = righe.filter((riga) => riga.parentId === null);
  const usi = (riga: CategoryRow) => [
    riga.children ? (riga.children === 1 ? t('catChildCount_one') : t('catChildCount_other', { count: riga.children })) : '',
    riga.movements || riga.budgets || riga.rules
      ? t('catUsedBy', { movements: riga.movements, budgets: riga.budgets, rules: riga.rules }) : '',
  ].filter(Boolean).join(' · ');

  return <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
    <CardHeader>
      <CardTitle className="text-[17px]">{t('catSection')}</CardTitle>
      <p className="text-xs leading-5 text-[#7b8784]">{t('catSectionDesc')}</p>
    </CardHeader>
    <CardContent className="space-y-3">
      {errore && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{errore}</p>}
      <div className="flex gap-2">
        <Input value={nuova} aria-label={t('catNewRoot')} placeholder={t('catNewRoot')} onChange={(evento) => setNuova(evento.target.value)}
               onKeyDown={(evento) => { if (evento.key === 'Enter') void aggiungi(nuova, null); }} />
        <Button type="button" disabled={inCorso} onClick={() => void aggiungi(nuova, null)}>{t('catAdd')}</Button>
      </div>
      {righe.length === 0 && <p className="text-xs text-[#71807c]">{t('catEmpty')}</p>}
      <ul className="space-y-1.5">
        {righe.map((riga) => {
          const opzioni = radici.filter((radice) => radice.id !== riga.id).length;
          return <li key={riga.id} className={riga.parentId === null ? '' : 'pl-6'}>
            <div className="flex flex-wrap items-center gap-1.5">
              <Input key={`${riga.id}-${riga.name}`} defaultValue={riga.name} aria-label={t('catRenameOf', { name: riga.name })}
                     className="h-9 min-w-0 flex-1" onBlur={(evento) => void rinomina(riga, evento.currentTarget.value)}
                     onKeyDown={(evento) => { if (evento.key === 'Enter') evento.currentTarget.blur(); }} />
              <Button type="button" variant="ghost" size="icon" aria-label={t('catMoveUp')} disabled={inCorso}
                      onClick={() => void sposta(riga, -1)}><ArrowUp className="size-4" /></Button>
              <Button type="button" variant="ghost" size="icon" aria-label={t('catMoveDown')} disabled={inCorso}
                      onClick={() => void sposta(riga, 1)}><ArrowDown className="size-4" /></Button>
              {(riga.parentId !== null || (riga.children === 0 && opzioni)) && <select
                aria-label={t('catMoveUnder')} value="" disabled={inCorso}
                className="h-9 rounded-lg border border-input bg-[#fafaf8] px-2 text-xs outline-none focus:border-ring"
                onChange={(evento) => void spostaSotto(riga, evento.target.value)}>
                <option value="">{t('catMoveUnder')}</option>
                {riga.parentId !== null && <option value="radice">{t('catMoveToRoot')}</option>}
                {riga.children === 0 && radici.filter((radice) => radice.id !== riga.id)
                  .map((radice) => <option key={radice.id} value={String(radice.id)}>{radice.name}</option>)}
              </select>}
              <Button type="button" variant="ghost" size="sm" disabled={inCorso}
                      onClick={() => void chiama(`/api/categories/${riga.id}`, 'PATCH', { active: !riga.active })}>
                {riga.active ? t('catInactiveOff') : t('catInactiveOn')}
              </Button>
              <Button type="button" variant="ghost" size="icon" aria-label={t('catDelete')} disabled={inCorso}
                      onClick={() => void cancella(riga)}><Trash2 className="size-4" /></Button>
            </div>
            {usi(riga) && <p className="mt-0.5 text-[11px] text-[#87918e]">{usi(riga)}</p>}
            {riga.parentId === null && (sotto === riga.id
              ? <div className="mt-1.5 flex gap-2">
                  <Input autoFocus value={figlia} aria-label={t('catChildPlaceholder')} placeholder={t('catChildPlaceholder')}
                         onChange={(evento) => setFiglia(evento.target.value)}
                         onKeyDown={(evento) => { if (evento.key === 'Enter') void aggiungi(figlia, riga.id); }} />
                  <Button type="button" disabled={inCorso} onClick={() => void aggiungi(figlia, riga.id)}>{t('catAdd')}</Button>
                  <Button type="button" variant="ghost" onClick={() => { setSotto(null); setFiglia(''); }}>{t('catCancel')}</Button>
                </div>
              : <Button type="button" variant="ghost" size="sm" className="mt-0.5 text-[#71807c]"
                        onClick={() => { setSotto(riga.id); setFiglia(''); }}>
                  <Plus className="size-4" />{t('catAddChild')}
                </Button>)}
          </li>;
        })}
      </ul>
    </CardContent>
  </Card>;
}
