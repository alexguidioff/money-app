import { messaggioErrore } from '@/lib/fire-errors';
import type { TranslationKey } from '@/lib/translations';

// I rifiuti del server su una regola di categorizzazione. Tradotti in un posto
// solo perche' li leggono due moduli: il modulo di inserimento e il dialogo
// delle proposte. Senza, entrambi direbbero solo "non riuscito".
const MESSAGGI: Record<string, TranslationKey> = {
  rulePatternRequired: 'rulePatternRequired',
  ruleRegexInvalid: 'ruleRegexInvalid',
  ruleCategoryUnknown: 'ruleCategoryUnknown',
  ruleAmountRange: 'ruleAmountRange',
  ruleLimitReached: 'ruleLimitReached',
  ruleDuplicate: 'ruleDuplicate',
};

export function messaggioErroreRegola(response: Response, t: (key: TranslationKey) => string): Promise<string> {
  return messaggioErrore(response, MESSAGGI, t, 'ruleSaveError');
}
