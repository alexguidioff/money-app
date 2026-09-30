/**
 * L'icona di una categoria, per nome.
 *
 * Sono le radici - le "categorie generali" - e non le voci dentro: l'elenco si
 * apre chiuso, quindi le radici sono le uniche righe che si vedono sempre, ed e'
 * li' che l'icona serve a riconoscere una riga a colpo d'occhio invece di
 * leggerla. Un figlio sta gia' sotto l'icona del padre che lo contiene.
 *
 * I nomi sono quelli con cui le categorie nascono (`ALBERO_SPESE` e
 * `ALBERO_ENTRATE` in `backend/app/migrations.py`), confrontati senza badare
 * alle maiuscole. Una categoria rinominata perde l'icona e non e' un guasto:
 * una riga senza icona si legge come prima, e il nome non e' piu' quello che
 * abbiamo scritto noi - legargli un'icona sarebbe indovinare.
 *
 * Emoji e non le icone vettoriali del resto dell'app perche' le voci di una
 * tendina sono testo: in un `<option>` non entra un disegno, entra un carattere.
 * Con le emoji la stessa icona si vede nell'elenco del budget e nella tendina
 * del modulo, che altrimenti direbbero due cose diverse.
 *
 * ponytail: mappa per nome, non una colonna sulla categoria. Reggerebbe una
 * rinomina, ma costa una migrazione su un database vivo; quando le categorie
 * saranno rinominabili davvero, l'icona diventa un campo che si sceglie.
 */
const ICONE: Record<string, string> = {
  // Spese
  'housing': '🏠',
  'groceries': '🛒',
  'food & dining': '🍽️',
  'transportation': '🚗',
  'entertainment': '🎬',
  'health & wellness': '🩺',
  'bills & utilities': '🧾',
  'shopping': '🛍️',
  'fees & charges': '🏦',
  'gifts & donations': '🎁',
  'travels': '✈️',
  'study': '🎓',
  'personal care': '💅',
  'other expenses': '📦',
  'savings': '🐖',
  // Entrate
  'employment': '💼',
  'self-employment': '💻',
  'investment income': '📈',
  'other income': '💰',
};

/** L'icona di quella categoria, o niente: una riga senza icona si legge lo stesso. */
export function iconaCategoria(nome: string): string | undefined {
  return ICONE[nome.trim().toLowerCase()];
}
