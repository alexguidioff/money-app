'use client';

import type { ReactNode } from 'react';
import { iconaCategoria } from '@/lib/category-icons';
import { posizioneNelRamo, ramiDelleCategorie, valoreDellaMadre, type CategoryNode } from '@/lib/category-picker';
import { useI18n } from '@/lib/i18n-context';

/** Un ramo dell'albero come lo manda il server: la radice e i nomi dei figli. */
export type { CategoryNode } from '@/lib/category-picker';

/**
 * Le voci di un elenco a tendina di categorie, con i figli indentati sotto il
 * padre.
 *
 * Una radice con figli si sceglie come qualunque altra categoria: e' dove
 * stanno i movimenti che nessuno ha ancora spostato nei figli, e spegnerla
 * vorrebbe dire non poter spaccare una categoria senza prima svuotarla - che e'
 * il contrario di come si fa: prima si spacca, poi si sposta quello che c'era.
 * Resta anche il titolo che dice a cosa appartengono le righe sotto.
 *
 * `names` e' quello che il server accetta per questo tipo di movimento, e da
 * solo decide cosa si vede: una categoria di entrate non compare in una tendina
 * di spese, e viceversa. Una categoria che l'albero non conosce - il vocabolario
 * di partenza puo' averne una - resta in fondo, dov'era, invece di sparire
 * dall'elenco.
 */
export function CategoryOptions({ names, tree }: { names: string[]; tree: CategoryNode[] }) {
  const { t } = useI18n();
  const ammessi = new Set(names);
  const voci: ReactNode[] = [];
  const mostrati = new Set<string>();
  for (const radice of tree) {
    const figli = radice.children.filter((figlio) => ammessi.has(figlio));
    const sceglibile = ammessi.has(radice.name);
    if (figli.length || sceglibile) {
      mostrati.add(radice.name);
      voci.push(
        // Spenta solo se il server non la offre qui - un padre messo via, o
        // l'albero dell'altro verso - con la spiegazione nel titolo: da sola
        // non direbbe perche' non si puo' scegliere.
        <option key={`padre-${radice.name}`} value={radice.name} disabled={!sceglibile}
                title={sceglibile ? undefined : t('catChooseChild', { name: radice.name })}>
          {/* L'icona davanti al nome, come nell'elenco del budget: e' la stessa
              categoria, e due elenchi che la mostrano in due modi diversi
              facevano sembrare due cose diverse. */}
          {iconaCategoria(radice.name) ? `${iconaCategoria(radice.name)} ${radice.name}` : radice.name}
        </option>,
        // Spazi fissi e non normali: in una tendina gli spazi normali vengono
        // compressi e l'indentazione sparisce.
        ...figli.map((figlio) => {
          mostrati.add(figlio);
          return <option key={`${radice.name}/${figlio}`} value={figlio}>{`   ${figlio}`}</option>;
        }),
      );
    }
  }
  for (const nome of names) {
    if (!mostrati.has(nome)) voci.push(<option key={`resto-${nome}`} value={nome}>{nome}</option>);
  }
  return <>{voci}</>;
}

/**
 * La stessa scelta in due passi: prima la categoria madre, poi la figlia.
 *
 * E' per dove l'elenco e' lungo e chi sceglie sa gia' dove vuole andare: una
 * tendina sola con sessanta voci in ordine alfabetico chiede di leggerle tutte
 * per trovarne una che si sa di avere sotto una madre. La prima tendina ha le
 * madri - quindici, non sessanta - e la seconda mostra solo le figlie di quella.
 *
 * La madre resta sceglibile da sola: e' dove stanno i movimenti che nessuno ha
 * ancora spostato nei figli, e lo dice l'opzione in cima alla seconda tendina.
 * Una categoria che l'albero non conosce resta sceglibile da sola, come in
 * `CategoryOptions`.
 *
 * `value` e' il nome che il server accetta e la madre si ricava da quello:
 * nessuno stato interno da tenere allineato, cosi' un suggerimento premuto o una
 * riga spostata lasciano le due tendine d'accordo senza doverlo ricordare.
 */
export function CategoryPicker({ names, tree, value, onChange, disabled = false }: {
  names: string[];
  tree: CategoryNode[];
  value: string;
  onChange: (category: string) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const rami = ramiDelleCategorie(names, tree);
  const { madre, figlia } = posizioneNelRamo(rami, value);
  const ramo = rami.find((r) => r.madre === madre);

  return <>
    <select aria-label={t('category')} required value={madre} disabled={disabled}
            onChange={(event) => onChange(valoreDellaMadre(rami, event.target.value))}
            className="h-10 min-w-0 rounded-md border border-input bg-[var(--money-superficie)] px-2 text-sm">
      <option value="">{t('budgetPickCategory')}</option>
      {rami.map((voce) => <option key={voce.madre} value={voce.madre}>
        {iconaCategoria(voce.madre) ? `${iconaCategoria(voce.madre)} ${voce.madre}` : voce.madre}
      </option>)}
    </select>
    <select aria-label={madre ? t('catChooseChild', { name: madre }) : t('category')}
            value={figlia} disabled={disabled || !madre}
            onChange={(event) => onChange(event.target.value || madre)}
            className="h-10 min-w-0 rounded-md border border-input bg-[var(--money-superficie)] px-2 text-sm disabled:bg-[var(--money-superficie-hover)] disabled:text-[var(--money-testo-spento)]">
      {ramo?.sceglibile && <option value="">{t('budgetOnlyParent', { name: madre })}</option>}
      {ramo?.figlie.map((nome) => <option key={nome} value={nome}>{nome}</option>)}
    </select>
  </>;
}
