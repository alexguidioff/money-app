// Le valute che l'app offre in due punti: il menu della valuta di un conto e le
// pillole della Panoramica. Sono due elenchi diversi dello stesso elenco
// maestro - quello scritto in Patrimonio > Valute - e la differenza e' il
// motivo per cui stanno qui invece che dentro il componente: sbagliarla non fa
// rumore, fa comparire un numero in un'altra moneta.

/**
 * Le valute che un conto puo' scegliere: quelle dell'elenco maestro, piu' la
 * sua.
 *
 * La sua c'e' sempre, anche se non e' piu' nell'elenco: un conto tiene la
 * valuta che ha, e aprire il conto per correggere una nota non deve
 * cambiargliela. Senza, il `<select>` non avrebbe una voce da selezionare e
 * mostrerebbe la prima - salvando, il conto cambierebbe moneta in silenzio.
 */
export function valuteDelConto(scelte: string[] | undefined, corrente: string | undefined): string[] {
  return [...new Set([...(scelte ?? ['EUR']), ...(corrente ? [corrente.toUpperCase()] : [])])];
}

/**
 * Le valute in cui la Panoramica si puo' leggere adesso.
 *
 * L'euro c'e' sempre e sta primo: e' la valuta di partenza della pagina. Le
 * altre si offrono solo se l'app ne conosce il cambio (`pronte`): una valuta
 * appena aggiunta a mano non ha ancora storico, e chiederne la Panoramica e'
 * un errore - non un numero in euro spacciato per franchi. Senza `pronte`, che
 * e' il caso di chi non ha ancora caricato il patrimonio, si offrono tutte:
 * meglio una pillola che puo' fallire di una pillola che manca.
 */
export function valuteDellaPanoramica(scelte: string[] | undefined, pronte: string[] | undefined): string[] {
  const altre = (scelte ?? ['EUR']).filter((codice) => codice !== 'EUR');
  return ['EUR', ...(pronte ? altre.filter((codice) => pronte.includes(codice)) : altre)];
}
