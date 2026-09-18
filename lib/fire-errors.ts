import type { TranslationKey } from '@/lib/translations';

// I codici con cui il server rifiuta un profilo o un flusso che renderebbe il
// piano incoerente. Tradotti qui, in un posto solo, perche' li leggono due
// moduli: senza, entrambi dicevano solo "salvataggio non riuscito".
const MESSAGGI: Record<string, TranslationKey> = {
  fireStreamDuplicateName: 'fireErrorDuplicateName',
  fireEngine_stima_solo_per_rendita: 'fireErrorEarlyOnlyAnnuity',
  fireEngine_stime_pensione_invertite: 'fireErrorEstimatesInverted',
  fireEngine_intervallo_pensione: 'fireErrorPensionAge',
  fireEngine_orizzonte: 'fireErrorHorizon',
  fireEngine_tassi_fuori_intervallo: 'fireErrorRates',
};

/** Il codice del server tradotto in una frase, o il messaggio del modulo. */
export async function messaggioErrore(response: Response, mappa: Record<string, TranslationKey>,
                                      t: (key: TranslationKey) => string, predefinito: TranslationKey): Promise<string> {
  const corpo = await response.json().catch(() => null) as { detail?: unknown } | null;
  const chiave = typeof corpo?.detail === 'string' ? mappa[corpo.detail] : undefined;
  return t(chiave ?? predefinito);
}

export async function messaggioErroreFire(response: Response, t: (key: TranslationKey) => string,
                                          predefinito: TranslationKey): Promise<string> {
  return messaggioErrore(response, MESSAGGI, t, predefinito);
}
