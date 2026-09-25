import { responseError } from './download';
import { translations, type TranslationKey } from './translations';

type Traduci = (key: TranslationKey, params?: Record<string, string>) => string;

/**
 * Perche' una riga dell'elenco di Scalable e' stata scartata, tradotto.
 *
 * Il lettore manda il motivo come codice corto (`rigaSenzaImporto`), non come
 * chiave: gli stessi codici finiscono anche nello storico import come conteggi,
 * e li' una frase sarebbe fuori posto. I motivi dell'import sono codici della
 * stessa famiglia e passano di qui ugualmente; un codice che non conosciamo non
 * arriva a schermo, perche' una frase illeggibile in mezzo alle altre farebbe
 * sembrare rotto tutto l'import invece di quella riga sola.
 */
export function motivoRifiuto(codice: string, t: Traduci): string {
  // `scalableReason` da solo e' l'intestazione della colonna dei motivi: un
  // codice vuoto non deve pescarla.
  const composta = codice ? `scalableReason${codice.charAt(0).toUpperCase()}${codice.slice(1)}` : '';
  const chiave = composta && Object.hasOwn(translations.it, composta) ? composta
    : Object.hasOwn(translations.it, codice) ? codice : 'scalableReasonRigaSconosciuta';
  return t(chiave as TranslationKey);
}

/**
 * Il messaggio di un import rifiutato, con il numero della riga quando c'e'.
 *
 * Un elenco di quattrocento righe che non si importa e' un elenco da rileggere
 * tutto: le due risposte che nominano la riga si leggono qui, e tutto il resto
 * lo sa gia' leggere `responseError` come ogni altra rotta dell'app.
 */
export async function messaggioImportScalable(response: Response, t: Traduci): Promise<string> {
  // Il corpo si legge una volta sola: per delegare a `responseError` ne serve
  // una copia intatta.
  const copia = response.clone();
  const corpo = await response.json().catch(() => null) as { detail?: unknown } | null;
  const dettaglio = corpo?.detail && typeof corpo.detail === 'object' && !Array.isArray(corpo.detail)
    ? corpo.detail as { code?: string; row?: number; cause?: unknown } : null;
  if (dettaglio?.code === 'scalableRowFailed' || dettaglio?.code === 'scalableRowUnbalanced') {
    const causa = dettaglio.code === 'scalableRowUnbalanced' ? t('scalableReasonRowUnbalanced')
      : motivoRifiuto(typeof dettaglio.cause === 'string' ? dettaglio.cause : '', t);
    return t('scalableRowFailed', { row: String(dettaglio.row ?? ''), cause: causa });
  }
  return responseError(copia, t, 'scalableFailed');
}
