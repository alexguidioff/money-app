import { mkdirSync } from 'node:fs';
import { request, type FullConfig } from '@playwright/test';

/*
 * Prepara lo stack di test: il primo utente (che entra subito, come chi apre
 * l'app per la prima volta), la lingua italiana e i conti su cui lavorano i
 * test. Tutto via API: i test provano i flussi, non la preparazione.
 */
export default async function prepara(config: FullConfig) {
  const { baseURL, storageState } = config.projects[0].use;
  if (!baseURL?.includes(':3011')) throw new Error(`I test end-to-end girano solo sullo stack di test, non su ${baseURL}`);
  const api = await request.newContext({ baseURL });
  const esito = await api.post('/api/users', { data: { username: 'prova', display_name: 'Prova' } });
  if (!esito.ok()) throw new Error(`Utente di prova non creato (${esito.status()}): lo stack non e' vuoto?`);
  for (const conto of [
    { name: 'Banca', source_group: 'bank', starting_balance: 5000 },
    { name: 'Broker', source_group: 'asset', starting_balance: 0, is_broker: true, is_liquid: false },
    { name: 'Fido', source_group: 'liability', starting_balance: 0 },
  ]) {
    const risposta = await api.post('/api/accounts', { data: conto });
    if (!risposta.ok()) throw new Error(`Conto ${conto.name}: ${risposta.status()} ${await risposta.text()}`);
  }
  const trasferimento = await api.post('/api/transactions', { data: {
    occurred_on: new Date().toISOString().slice(0, 10), transaction_type: 'Transfers', amount: 250,
    account_name: 'Banca', destination_name: 'Broker', details: 'Versamento broker e2e' } });
  if (!trasferimento.ok()) throw new Error(`Trasferimento: ${trasferimento.status()} ${await trasferimento.text()}`);

  const stato = await api.storageState();
  mkdirSync('test-results', { recursive: true });
  stato.origins = [{ origin: baseURL, localStorage: [{ name: 'money-app-lang', value: 'it' }] }];
  const { writeFileSync } = await import('node:fs');
  writeFileSync(storageState as string, JSON.stringify(stato));
  await api.dispose();
}
