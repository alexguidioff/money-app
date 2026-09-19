'use client';

import type { ReactNode } from 'react';
import { useI18n } from '@/lib/i18n-context';

/** Un ramo dell'albero come lo manda il server: la radice e i nomi dei figli. */
export type CategoryNode = { name: string; children: string[] };

/**
 * Le voci di un elenco a tendina di categorie, con i figli indentati sotto il
 * padre.
 *
 * Una radice che ha figli non e' una scelta: se hai spaccato Alimentari in
 * Supermercato e Mensa, un movimento sta in uno dei due, non nel contenitore.
 * Resta nell'elenco, spenta, perche' e' l'unico modo di dire a cosa
 * appartengono le due righe sotto. Una radice senza figli invece si sceglie
 * come qualunque altra categoria.
 *
 * `names` e' quello che il server accetta per questo tipo di movimento. Una
 * categoria che l'albero non conosce - il vocabolario di partenza puo' averne
 * una - resta in fondo, dov'era, invece di sparire dall'elenco.
 */
export function CategoryOptions({ names, tree }: { names: string[]; tree: CategoryNode[] }) {
  const { t } = useI18n();
  const ammessi = new Set(names);
  const voci: ReactNode[] = [];
  const mostrati = new Set<string>();
  for (const radice of tree) {
    const figli = radice.children.filter((figlio) => ammessi.has(figlio));
    if (figli.length) {
      mostrati.add(radice.name);
      voci.push(
        // Spenta e con la spiegazione nel titolo: da sola non direbbe perche'
        // non si puo' scegliere.
        <option key={`padre-${radice.name}`} value={radice.name} disabled title={t('catChooseChild', { name: radice.name })}>
          {radice.name}
        </option>,
        // Spazi fissi e non normali: in una tendina gli spazi normali vengono
        // compressi e l'indentazione sparisce.
        ...figli.map((figlio) => {
          mostrati.add(figlio);
          return <option key={`${radice.name}/${figlio}`} value={figlio}>{`   ${figlio}`}</option>;
        }),
      );
    } else if (radice.children.length === 0 && ammessi.has(radice.name)) {
      mostrati.add(radice.name);
      voci.push(<option key={`radice-${radice.name}`} value={radice.name}>{radice.name}</option>);
    }
  }
  for (const nome of names) {
    if (!mostrati.has(nome)) voci.push(<option key={`resto-${nome}`} value={nome}>{nome}</option>);
  }
  return <>{voci}</>;
}
