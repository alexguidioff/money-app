'use client';

import { useCallback, useEffect, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useI18n } from '@/lib/i18n-context';
import type { TranslationKey } from '@/lib/translations';

/**
 * Una categoria come la manda l'API: dov'e' nell'albero, e chi la usa.
 *
 * `scope` dice il verso - una categoria di spesa non compare fra le entrate - e
 * `essential` e' bisogno/piacere/nullo solo sulle spese: sulle entrate la
 * domanda non si pone. `essentialEffective` e' quello che vale davvero, cioe' il
 * dichiarato o quello del padre, e serve a mostrare in grigio l'ereditato senza
 * farlo sembrare una scelta di chi guarda.
 */
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
  scope: 'expense' | 'income';
  essential: 'needs' | 'wants' | null;
  essentialEffective: 'needs' | 'wants' | null;
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

/** I due alberi, nell'ordine in cui si mostrano: prima le spese. */
const VERSI = [
  { verso: 'expense' as const, titolo: 'catTreeExpenses' as const },
  { verso: 'income' as const, titolo: 'catTreeIncome' as const },
];

/**
 * L'albero delle categorie, dove si costruisce: una card nel Budget.
 *
 * Due alberi separati, uno per verso: una categoria di spesa non appartiene
 * all'albero delle entrate, e vederli mescolati faceva sembrare che la stessa
 * voce valesse per entrambi.
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
 *
 * Solo sulle spese, accanto al nome, c'e' bisogno / piacere / non detto: su
 * un'entrata la domanda non si pone. Una voce che non risponde eredita dal
 * padre, e l'ereditato si legge in grigio sotto: e' un valore che vale per lei,
 * ma non e' una risposta che ha dato.
 */
export function CategoryTreeCard({ apiUrl, onChanged }: { apiUrl: string; onChanged: () => Promise<void> }) {
  const { t } = useI18n();
  const [righe, setRighe] = useState<CategoryRow[]>([]);
  const [nuova, setNuova] = useState<Record<string, string>>({ expense: '', income: '' });
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

  async function aggiungi(nome: string, parentId: number | null, verso: 'expense' | 'income') {
    const pulito = nome.trim();
    if (!pulito) {
      setErrore(t('catErrorName'));
      return;
    }
    // Il verso si manda solo per una radice: un figlio sta nell'albero del
    // padre, e lo decide il server.
    const corpo = parentId === null ? { name: pulito, scope: verso } : { name: pulito, parentId };
    if (await chiama('/api/categories', 'POST', corpo)) {
      if (parentId === null) setNuova((corrente) => ({ ...corrente, [verso]: '' }));
      else { setFiglia(''); setSotto(null); }
    }
  }

  /** Bisogno, piacere, o non detto: tre parole, e "non detto" e' la terza. */
  async function classifica(riga: CategoryRow, gruppo: string) {
    await chiama('/api/category-groups', 'PUT', { categoryId: riga.id, category_group: gruppo });
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

  const usi = (riga: CategoryRow) => [
    riga.children ? (riga.children === 1 ? t('catChildCount_one') : t('catChildCount_other', { count: riga.children })) : '',
    riga.movements || riga.budgets || riga.rules
      ? t('catUsedBy', { movements: riga.movements, budgets: riga.budgets, rules: riga.rules }) : '',
  ].filter(Boolean).join(' · ');

  /**
   * Una riga dell'albero, radice o voce che sia.
   *
   * Il selettore bisogno/piacere sta solo sulle spese: su un'entrata la domanda
   * non si pone, e il server la rifiuterebbe. Quando la categoria non dichiara
   * niente, accanto compare in grigio quello che eredita dal padre: e' un
   * valore che vale per lei, ma non e' una sua risposta.
   */
  /** I bottoni di una riga: silenziosi finche' non la si guarda da vicino. */
  function azioni(riga: CategoryRow, radiciDelVerso: CategoryRow[]) {
    const puoSpostare = riga.parentId !== null || (riga.children === 0 && radiciDelVerso.some((r) => r.id !== riga.id));
    return <div className="flex shrink-0 items-center gap-0.5 opacity-45 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
      <Button type="button" variant="ghost" size="icon" className="size-7" aria-label={t('catMoveUp')} disabled={inCorso}
              onClick={() => void sposta(riga, -1)}><ArrowUp className="size-3.5" /></Button>
      <Button type="button" variant="ghost" size="icon" className="size-7" aria-label={t('catMoveDown')} disabled={inCorso}
              onClick={() => void sposta(riga, 1)}><ArrowDown className="size-3.5" /></Button>
      {puoSpostare && <select
        aria-label={t('catMoveUnder')} value="" disabled={inCorso}
        className="h-7 max-w-[7.5rem] rounded-md border border-transparent bg-transparent px-1 text-[11px] text-[#71807c] outline-none hover:border-input focus:border-ring"
        onChange={(evento) => void spostaSotto(riga, evento.target.value)}>
        <option value="">{t('catMoveUnder')}</option>
        {riga.parentId !== null && <option value="radice">{t('catMoveToRoot')}</option>}
        {/* Solo le radici dello stesso verso: un "sposta sotto" che porta in
            un altro albero cambierebbe il verso della categoria. */}
        {riga.children === 0 && radiciDelVerso.filter((radice) => radice.id !== riga.id)
          .map((radice) => <option key={radice.id} value={String(radice.id)}>{radice.name}</option>)}
      </select>}
      <Button type="button" variant="ghost" size="sm" className="h-7 px-2 text-[11px]" disabled={inCorso}
              onClick={() => void chiama(`/api/categories/${riga.id}`, 'PATCH', { active: !riga.active })}>
        {riga.active ? t('catInactiveOff') : t('catInactiveOn')}
      </Button>
      <Button type="button" variant="ghost" size="icon" className="size-7 text-[#a8837a] hover:text-[#bd5e46]"
              aria-label={t('catDelete')} disabled={inCorso} onClick={() => void cancella(riga)}><Trash2 className="size-3.5" /></Button>
    </div>;
  }

  /** Bisogno o piacere: solo sulle spese, e l'ereditato si vede che e' ereditato. */
  function classificazione(riga: CategoryRow) {
    if (riga.scope !== 'expense') return null;
    const ereditato = riga.essential === null && riga.essentialEffective;
    return <select
      aria-label={t('catEssentialOf', { name: riga.name })} value={riga.essential ?? ''} disabled={inCorso}
      className={`h-7 shrink-0 rounded-full border px-2 text-[11px] outline-none focus:border-ring ${
        riga.essential === 'needs' ? 'border-[#bcd8cd] bg-[#eef6f2] text-[#2d7b65]'
        : riga.essential === 'wants' ? 'border-[#e8d6bd] bg-[#fdf6ec] text-[#9a7b2f]'
        : ereditato ? 'border-dashed border-[#d7dcd9] bg-transparent text-[#9aa5a1]'
        : 'border-[#e4e8e6] bg-transparent text-[#9aa5a1]'}`}
      onChange={(evento) => void classifica(riga, evento.target.value)}>
      <option value="">{ereditato
        ? t('catEssentialInherited', { value: riga.essentialEffective === 'needs' ? t('groupNeeds') : t('groupWants') })
        : t('catEssentialUnset')}</option>
      <option value="Needs">{t('groupNeeds')}</option>
      <option value="Wants">{t('groupWants')}</option>
    </select>;
  }

  /**
   * Il nome si scrive dentro un campo che non sembra un campo: a riposo e' testo,
   * il bordo compare quando ci passi sopra o ci entri col tasto di tabulazione.
   * Con cinquanta categorie, cinquanta caselle disegnate sono il rumore che
   * rendeva illeggibile l'elenco.
   */
  function nome(riga: CategoryRow, grande: boolean) {
    return <Input key={`${riga.id}-${riga.name}`} defaultValue={riga.name} aria-label={t('catRenameOf', { name: riga.name })}
                  className={`h-7 min-w-0 flex-1 border-transparent bg-transparent px-1.5 shadow-none hover:border-input focus:border-ring ${
                    grande ? 'text-[14px] font-semibold text-[#2f3a37]' : 'text-[13px] text-[#4b5754]'} ${riga.active ? '' : 'line-through opacity-55'}`}
                  onBlur={(evento) => void rinomina(riga, evento.currentTarget.value)}
                  onKeyDown={(evento) => { if (evento.key === 'Enter') evento.currentTarget.blur(); }} />;
  }

  return <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
    <CardHeader>
      <CardTitle className="text-[17px]">{t('catSection')}</CardTitle>
      <p className="text-xs leading-5 text-[#7b8784]">{t('catSectionDesc')}</p>
    </CardHeader>
    <CardContent className="space-y-5">
      {errore && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{errore}</p>}
      {VERSI.map(({ verso, titolo }) => {
        const delVerso = righe.filter((riga) => riga.scope === verso);
        const radici = delVerso.filter((riga) => riga.parentId === null);
        return <div key={verso} className="space-y-3">
          <h3 className="text-sm font-semibold text-[#3d4a47]">{t(titolo)}</h3>
          <div className="flex gap-2">
            <Input value={nuova[verso] ?? ''} aria-label={t('catNewRoot')} placeholder={t('catNewRoot')}
                   onChange={(evento) => setNuova((corrente) => ({ ...corrente, [verso]: evento.target.value }))}
                   onKeyDown={(evento) => { if (evento.key === 'Enter') void aggiungi(nuova[verso] ?? '', null, verso); }} />
            <Button type="button" disabled={inCorso} onClick={() => void aggiungi(nuova[verso] ?? '', null, verso)}>{t('catAdd')}</Button>
          </div>
          {radici.length === 0 && <p className="text-xs text-[#71807c]">{t('catEmpty')}</p>}
          <ul className="space-y-2">
            {radici.map((radice) => {
              const figlie = delVerso.filter((figlio) => figlio.parentId === radice.id);
              return <li key={radice.id} className="overflow-hidden rounded-xl border border-black/[0.07]">
                {/* La radice ha lo sfondo e il nome in grassetto: si vede che e'
                    il contenitore, non una voce come le altre. */}
                <div className="group flex items-center gap-1.5 bg-[#f6f8f6] px-2.5 py-1.5">
                  {nome(radice, true)}
                  {usi(radice) && <span className="shrink-0 text-[11px] text-[#87918e]">{usi(radice)}</span>}
                  {classificazione(radice)}
                  {azioni(radice, radici)}
                </div>
                {figlie.length > 0 && <ul className="divide-y divide-black/[0.04]">
                  {figlie.map((sotto_voce) => <li key={sotto_voce.id}
                      className="group flex items-center gap-1.5 py-1.5 pr-2.5 pl-2.5">
                    {/* La linea verticale dice a colpo d'occhio che questa riga
                        appartiene a quella sopra: il rientro da solo non bastava. */}
                    <span aria-hidden className="ml-1 mr-1 h-5 w-px shrink-0 bg-[#dfe4e1]" />
                    {nome(sotto_voce, false)}
                    {usi(sotto_voce) && <span className="shrink-0 text-[11px] text-[#87918e]">{usi(sotto_voce)}</span>}
                    {classificazione(sotto_voce)}
                    {azioni(sotto_voce, radici)}
                  </li>)}
                </ul>}
                <div className="border-t border-black/[0.04] px-2.5 py-1">
                  {sotto === radice.id
                    ? <div className="flex gap-2 py-1">
                        <Input autoFocus value={figlia} aria-label={t('catChildPlaceholder')} placeholder={t('catChildPlaceholder')}
                               className="h-8" onChange={(evento) => setFiglia(evento.target.value)}
                               onKeyDown={(evento) => { if (evento.key === 'Enter') void aggiungi(figlia, radice.id, radice.scope); }} />
                        <Button type="button" size="sm" disabled={inCorso} onClick={() => void aggiungi(figlia, radice.id, radice.scope)}>{t('catAdd')}</Button>
                        <Button type="button" size="sm" variant="ghost" onClick={() => { setSotto(null); setFiglia(''); }}>{t('catCancel')}</Button>
                      </div>
                    : <Button type="button" variant="ghost" size="sm" className="h-7 px-1.5 text-[11px] text-[#71807c]"
                              onClick={() => { setSotto(radice.id); setFiglia(''); }}>
                        <Plus className="size-3.5" />{t('catAddChild')}
                      </Button>}
                </div>
              </li>;
            })}
          </ul>
        </div>;
      })}
    </CardContent>
  </Card>;
}
