/**
 * La scelta di una categoria in due passi - prima la madre, poi la figlia -
 * ridotta a quello che serve al selettore: quali madri si offrono, dove sta un
 * nome dentro la gerarchia, e cosa diventa la scelta quando si preme una madre.
 *
 * Sta qui e non dentro il componente perche' e' l'unico pezzo che puo' sbagliare
 * in silenzio: una madre che si sceglie da sola quando il server non la accetta,
 * o una figlia che non ritrova la madre nell'elenco, si vedono solo in due
 * tendine che si contraddicono.
 */

/** Un ramo dell'albero come lo manda il server: la radice e i nomi dei figli. */
export type CategoryNode = { name: string; children: string[] };

export type Ramo = {
  /** Il nome della madre come lo mostra la prima tendina. */
  madre: string;
  /** Se il server accetta la madre da sola fra i nomi di questo verso. */
  sceglibile: boolean;
  /** Le figlie che il server accetta qui, nell'ordine dell'albero. */
  figlie: string[];
};

/**
 * I rami da mostrare nella prima tendina, dati i nomi che il server accetta.
 *
 * Una radice i cui figli sono tutti di un altro verso si salta: non c'e' niente
 * da scegliere sotto. Una radice spenta con le figlie accese resta, ma non
 * sceglibile - e' il caso di un padre messo via senza spostarne i movimenti.
 *
 * Una categoria che nessun ramo conosce - il vocabolario di partenza puo'
 * averne - diventa un ramo suo, sceglibile da sola: sparire dall'elenco
 * sarebbe peggio che comparire senza gerarchia.
 */
export function ramiDelleCategorie(names: string[], albero: CategoryNode[]): Ramo[] {
  const accettato = (nome: string) => names.find((n) => stessoNome(n, nome));
  const rami: Ramo[] = [];
  const visti = new Set<string>();
  for (const radice of albero) {
    const madre = accettato(radice.name);
    const figlie = radice.children.map(accettato).filter((nome): nome is string => Boolean(nome));
    if (!madre && !figlie.length) continue;
    visti.add(chiave(radice.name));
    for (const figlia of figlie) visti.add(chiave(figlia));
    rami.push({ madre: madre ?? radice.name, sceglibile: Boolean(madre), figlie });
  }
  for (const nome of names) {
    if (visti.has(chiave(nome))) continue;
    visti.add(chiave(nome));
    rami.push({ madre: nome, sceglibile: true, figlie: [] });
  }
  return rami;
}

/**
 * Dove sta un nome dentro i rami: la madre a cui appartiene - se stessa, se non
 * sta sotto nessuna - e la figlia, se e' una figlia. Tutt'e due i nomi come li
 * ha il server, non come li ha scritti chi chiama.
 */
export function posizioneNelRamo(rami: Ramo[], valore: string): { madre: string; figlia: string } {
  if (!valore) return { madre: '', figlia: '' };
  for (const ramo of rami) {
    if (stessoNome(ramo.madre, valore)) return { madre: ramo.madre, figlia: '' };
    const figlia = ramo.figlie.find((nome) => stessoNome(nome, valore));
    if (figlia) return { madre: ramo.madre, figlia };
  }
  // Un nome che non sta in nessun ramo: resta se stesso, cosi' la seconda
  // tendina non lo perde e la prima non si svuota da sola.
  return { madre: valore, figlia: '' };
}

/**
 * Cosa diventa la scelta quando si preme una madre nella prima tendina: il nome
 * della madre, se si puo' scegliere da sola; la sua prima figlia, se il server
 * la accetta solo attraverso di loro.
 */
export function valoreDellaMadre(rami: Ramo[], madre: string): string {
  if (!madre) return '';
  const ramo = rami.find((r) => stessoNome(r.madre, madre));
  if (!ramo) return madre;
  return ramo.sceglibile ? ramo.madre : ramo.figlie[0] ?? ramo.madre;
}

function chiave(nome: string): string {
  return nome.trim().toLowerCase();
}

function stessoNome(a: string, b: string): boolean {
  return chiave(a) === chiave(b);
}
