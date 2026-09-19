'use client';

import type { ReactNode } from 'react';
import { useI18n } from '@/lib/i18n-context';

/** Un ramo dell'albero come lo manda il server: la radice e i nomi dei figli. */
export type CategoryNode = { name: string; children: string[] };

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
          {radice.name}
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
