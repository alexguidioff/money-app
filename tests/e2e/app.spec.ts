import { expect, test, type Page } from '@playwright/test';

/*
 * Pochi flussi, quelli che si sono rotti davvero: pagine che restavano bianche,
 * moduli che non salvavano, pannelli che non comparivano. Tutto sullo stack di
 * test; ogni test lascia i dati coerenti per il successivo.
 */

const PAGINE = ['Panoramica', 'Budget', 'Movimenti', 'Obiettivi', 'Patrimonio', 'Debiti', 'Investimenti',
  'Insieme', 'Appunti', 'Pensionamento e FIRE', 'Report', 'Impostazioni'];

function raccogliErrori(page: Page): string[] {
  const errori: string[] = [];
  page.on('pageerror', (errore) => errori.push(`pagina: ${errore.message}`));
  page.on('console', (messaggio) => { if (messaggio.type() === 'error') errori.push(`console: ${messaggio.text()}`); });
  return errori;
}

async function apri(page: Page, voce: string) {
  // Il menu laterale: le sezioni stanno nel <nav>, Impostazioni sotto, fuori.
  await page.locator('aside').getByRole('button', { name: voce, exact: true }).click();
}

async function avvia(page: Page) {
  await page.goto('/');
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
}

test('ogni pagina del menu si apre senza errori', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  for (const voce of PAGINE) {
    await apri(page, voce);
    await expect(page.getByRole('heading', { level: 1 }).first(), voce).toBeVisible();
    await expect(page.getByText('Anteprima locale'), voce).toHaveCount(0);
  }
  expect(errori).toEqual([]);
});

test('FIRE: profilo salvato, flusso aggiunto, la pagina mostra il piano', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  // I dati del piano si compilano nella seconda scheda della pagina del piano,
  // non piu' in Impostazioni: gli ingressi stanno accanto all'uscita che
  // producono. La scheda si chiama "Profilo e flussi" perche' "Impostazioni" e'
  // gia' la voce della barra laterale.
  await apri(page, 'Pensionamento e FIRE');
  const schede = page.locator('#fire-tabs');
  await schede.getByRole('button', { name: 'Profilo e flussi' }).click();
  await page.getByLabel('Anno di nascita').fill('1998');
  await page.getByRole('button', { name: 'Salva profilo' }).click();
  await expect(page.getByText('Profilo salvato.')).toBeVisible();

  // Qui i flussi sono ancora zero, ed e' l'unico momento in cui la condizione
  // e' vera: l'eta' di ritiro non muove niente, e la pagina deve dirlo invece
  // di ripetere cinque volte lo stesso numero.
  await schede.getByRole('button', { name: 'Piano' }).click();
  await page.locator('#fire-lever').selectOption('retirementAge');
  await expect(page.getByText('Questa leva non muove il tuo piano')).toBeVisible();

  await schede.getByRole('button', { name: 'Profilo e flussi' }).click();
  await page.getByRole('button', { name: 'Aggiungi flusso' }).click();
  await page.getByLabel('Nome', { exact: true }).fill('AVS');
  await page.getByLabel('Importo annuo').fill('20000');
  await page.getByRole('button', { name: 'Salva flusso' }).click();
  await expect(page.getByText('Flusso salvato.')).toBeVisible();

  // Tornando al piano i numeri sono quelli nuovi, non quelli di prima: la
  // pagina non si smonta piu' a ogni cambio di sezione, e il ricarico lo fa la
  // scheda.
  await schede.getByRole('button', { name: 'Piano' }).click();
  await expect(page.getByText('Capitale necessario').first()).toBeVisible();
  expect(errori).toEqual([]);
});

test('Movimenti: una spesa si salva; un trasferimento al broker mostra il collegamento al ledger', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Movimenti');
  await page.getByRole('button', { name: 'Nuovo movimento' }).first().click();
  const dialogo = page.getByRole('dialog');
  // Gli id dei campi: il nome accessibile di una tendina include l'opzione scelta.
  await dialogo.locator('#movement-amount').fill('12.50');
  await dialogo.locator('#movement-account').selectOption('Banca');
  await dialogo.locator('#movement-details').fill('Spesa e2e');
  await dialogo.getByRole('button', { name: 'Salva movimento' }).click();
  await expect(dialogo).toBeHidden();
  await expect(page.getByText('Spesa e2e').first()).toBeVisible();
  // Una spesa ha il segno meno; un trasferimento fra conti propri no.
  await expect(page.getByText(/^−12,50\s*€$/).first()).toBeVisible();
  await expect(page.getByText(/^250,00\s*€$/).first()).toBeVisible();
  await expect(page.getByText(/^−250,00\s*€$/)).toHaveCount(0);

  await page.getByRole('button', { name: 'Modifica Versamento broker e2e' }).click();
  await dialogo.locator('#movement-type').selectOption('Investment');
  await expect(dialogo.getByText('Operazioni collegate')).toBeVisible();
  await expect(dialogo.getByText(/viene salvato come Investimento/)).toBeVisible();
  await dialogo.getByRole('button', { name: 'Annulla' }).click();

  // Il tipo scelto nel movimento precedente non deve decidere le categorie del
  // successivo: aprendo la spesa, restavano quelle (nessuna) dell'investimento.
  await page.getByRole('button', { name: 'Modifica Spesa e2e' }).click();
  await expect(dialogo.locator('#movement-type')).toHaveValue('Expenses');
  await expect(dialogo.locator('#movement-category')).toBeEnabled();
  await expect(dialogo.locator('#movement-category option', { hasText: 'Groceries' })).toHaveCount(1);
  await dialogo.getByRole('button', { name: 'Annulla' }).click();

  // Registrando anche nel ledger, lo strumento si sceglie fra quelli che ci sono.
  await page.getByRole('button', { name: 'Nuovo movimento' }).first().click();
  await dialogo.locator('#movement-type').selectOption('Investment');
  await dialogo.locator('#movement-account').selectOption('Banca');
  await dialogo.getByLabel('Registra anche nel ledger').check();
  const strumento = dialogo.locator('input[list="strumenti-esistenti"]');
  await expect(strumento).toBeVisible();
  await expect(dialogo.locator('#strumenti-esistenti option[value="ETF e2e"]')).toHaveCount(1);
  await strumento.fill('ETF e2e');
  await dialogo.getByRole('button', { name: 'Annulla' }).click();

  // Una spesa gia' salvata diventa un investimento creando l'operazione dal
  // modulo di modifica. Senza conto broker di destinazione non parte nessun
  // salvataggio (a video arrivava il codice "movementIncomplete"); con il conto
  // l'operazione si crea e si collega.
  await page.getByRole('button', { name: 'Modifica Spesa e2e' }).click();
  await dialogo.locator('#movement-type').selectOption('Investment');
  await dialogo.getByRole('button', { name: '+ Crea', exact: true }).click();
  const nomeOperazione = dialogo.getByLabel('Strumento', { exact: true });
  await expect(nomeOperazione).toHaveAttribute('list', 'strumenti-esistenti');
  await nomeOperazione.fill('ETF e2e');
  await dialogo.getByRole('button', { name: 'Crea e collega' }).click();
  expect(await dialogo.locator('#movement-destination').evaluate((campo) => campo.matches(':invalid'))).toBe(true);
  await expect(dialogo.getByText('movementIncomplete')).toHaveCount(0);
  await dialogo.locator('#movement-destination').selectOption('Broker');
  await dialogo.getByRole('button', { name: 'Crea e collega' }).click();
  await expect(dialogo.locator('li', { hasText: 'ETF e2e' })).toBeVisible();
  await dialogo.getByRole('button', { name: 'Annulla' }).click();
  expect(errori).toEqual([]);
});

test('Debiti: una linea di credito si configura e si salva', async ({ page }) => {
  // Il modulo di una linea non ha i campi del piano: arrivavano come la stringa
  // "null" e il salvataggio veniva rifiutato.
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Debiti');
  await page.getByRole('button', { name: 'Configura debito' }).click();
  const dialogo = page.getByRole('dialog');
  await dialogo.locator('select[name="kind"]').selectOption('credit_line');
  await dialogo.locator('input[name="credit_limit"]').fill('30000');
  await dialogo.locator('input[name="annual_rate"]').fill('1.5');
  await dialogo.getByRole('button', { name: 'Salva', exact: true }).click();
  await expect(dialogo).toBeHidden();
  await expect(page.getByText('Impossibile salvare le condizioni del debito.')).toHaveCount(0);
  await expect(page.getByText('Linee di credito')).toBeVisible();
  expect(errori).toEqual([]);
});

test('Impostazioni: una preferenza resta dopo aver ricaricato', async ({ page }) => {
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Impostazioni');
  const colore = page.getByLabel('Colore principale');
  await colore.selectOption('Green');
  await expect(page.getByText('Salvataggio…')).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
  await apri(page, 'Impostazioni');
  await expect(page.getByLabel('Colore principale')).toHaveValue('Green');
  expect(errori).toEqual([]);
});

test('Regole di categorizzazione: la regola scritta in Movimenti decide la categoria di un import', async ({ page }) => {
  // E' l'unico giro completo della funzione, e sta qui perche' e' l'unico posto
  // in cui regole e import si toccano: la regola si scrive dove si categorizza,
  // e si vede all'opera nell'anteprima subito dopo.
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Movimenti');
  // Le regole hanno una scheda loro, accanto a Movimenti e Ricorrenze.
  await page.getByRole('button', { name: 'Regole', exact: true }).click();
  const card = page.locator('[data-slot="card"]', { has: page.getByText('Regole di categorizzazione') });
  await card.getByLabel('Testo da cercare').fill('supermercato e2e regola');
  // Il nome accessibile di una tendina si porta dietro le sue opzioni, quindi
  // qui si sceglie per posizione: nel modulo la categoria e' la prima.
  await card.locator('form select').first().selectOption('Groceries');
  await card.getByRole('button', { name: 'Aggiungi', exact: true }).click();
  await expect(card.getByRole('cell', { name: 'supermercato e2e regola', exact: true })).toBeVisible();

  const scelta = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'Importa da CSV' }).click();
  // Una data lontana da quelle delle altre scene: un movimento uguale per
  // importo e giorno farebbe scattare il controllo dei doppioni nell'import che
  // viene dopo, e quella riga arriverebbe li' gia' deselezionata.
  await (await scelta).setFiles({
    name: 'estratto.csv', mimeType: 'text/csv',
    buffer: Buffer.from('data,descrizione,importo\n15/04/2026,Supermercato e2e regola,-45.20\n'),
  });
  const anteprima = page.getByRole('dialog');
  await anteprima.getByLabel('Conto per tutte le righe').selectOption('Banca');
  // La regola ha gia' deciso, e lo dice: la categoria si vede scritta, e sotto
  // c'e' il pattern da cui viene. Le maiuscole non contano.
  await expect(anteprima.getByLabel('Categoria', { exact: true }).first()).toHaveValue('Groceries');
  await expect(anteprima.getByText('da: supermercato e2e regola')).toBeVisible();
  await anteprima.getByRole('button', { name: /^Conferma e aggiungi/ }).click();
  await expect(anteprima).toBeHidden();

  // Ed e' la categoria che si salva: se il salvataggio la buttasse via perche'
  // nessuno l'ha scelta a mano, la regola non avrebbe deciso niente.
  const risposta = await page.request.get('/api/transactions?limit=500');
  const righe = ((await risposta.json()) as { items: Array<{ details: string | null; category: string }> }).items;
  expect(righe.find((riga) => riga.details === 'Supermercato e2e regola')?.category).toBe('Groceries');
  expect(errori).toEqual([]);
});

test('Ledger: uno split 2:1 raddoppia le quote e non tocca il costo', async ({ page }) => {
  // Uno split non aggiunge righe al portafoglio: cambia i numeri della
  // posizione, ed e' li' che si guarda. Percio' il giro e' acquisto, split dal
  // modulo, e di nuovo la tabella delle posizioni.
  const errori = raccogliErrori(page);
  const strumento = await page.request.post('/api/investments/instruments', { data: { name: 'Split e2e' } });
  expect(strumento.ok()).toBe(true);
  const acquisto = await page.request.post('/api/investments/ledger', { data: {
    occurred_on: '2026-02-10', name: 'Split e2e', transaction_type: 'Buy',
    amount: 1000, units: 10, price: 100, currency: 'EUR' } });
  expect(acquisto.ok(), await acquisto.text()).toBe(true);

  await avvia(page);
  await apri(page, 'Investimenti');
  const posizioni = page.locator('[data-slot="card"]', { has: page.getByText('Posizioni', { exact: true }) });
  const riga = posizioni.getByRole('row', { name: /Split e2e/ });
  await expect(riga.getByRole('cell').nth(1)).toHaveText('10');
  await expect(riga.getByRole('cell').nth(2)).toHaveText(/^1000,00\s*€$/);

  await page.getByRole('button', { name: 'Ledger', exact: true }).click();
  await page.getByRole('button', { name: 'Nuova operazione' }).click();
  const dialogo = page.getByRole('dialog');
  await dialogo.locator('select[name="transaction_type"]').selectOption('Split');
  await dialogo.locator('input[name="name"]').fill('Split e2e');
  // Col tipo Split il campo si chiama "Rapporto": 2 = due quote nuove per una
  // vecchia. L'importo non si scrive, uno split non si paga.
  await dialogo.locator('input[name="units"]').fill('2');
  await dialogo.getByRole('button', { name: 'Salva', exact: true }).click();
  // Il modulo si chiude solo se il salvataggio e' andato: un rapporto rifiutato
  // lo lascerebbe aperto con il messaggio sotto.
  await expect(dialogo).toBeHidden();

  await page.getByRole('button', { name: 'Portafoglio', exact: true }).click();
  await expect(riga.getByRole('cell').nth(1)).toHaveText('20');
  await expect(riga.getByRole('cell').nth(2)).toHaveText(/^1000,00\s*€$/);
  expect(errori).toEqual([]);
});

test('Budget: la card del risparmio del mese guarda solo il mese', async ({ page }) => {
  // Mostrava entrate e spese dell'anno fino a quel mese sotto il titolo "il mese".
  const errori = raccogliErrori(page);
  const oggi = new Date();
  const anno = oggi.getFullYear(), mese = oggi.getMonth() + 1, altro = mese > 1 ? mese - 1 : mese + 1;
  for (const [m, tipo, categoria, importo] of [[mese, 'Income', 'Salary', 250], [mese, 'Expenses', 'Housing', 147.11],
    [altro, 'Income', 'Salary', 1736], [altro, 'Expenses', 'Housing', 1287.44]] as const) {
    const risposta = await page.request.post('/api/budgets', { data: { year: anno, month: m, budget_type: tipo, category: categoria, amount: importo } });
    expect(risposta.ok()).toBe(true);
  }
  await avvia(page);
  await apri(page, 'Budget');
  await page.getByRole('button', { name: 'Risparmi', exact: true }).click();
  await page.getByRole('button', { name: 'Piano', exact: true }).click();
  const card = page.locator('[data-slot="card"]', { has: page.getByText('Quanto mette da parte il mese') });
  await expect(card.getByText(/^250,00\s*€$/)).toBeVisible();
  await expect(card.getByText(/^147,11\s*€$/)).toBeVisible();
  await expect(card.getByText(/102,89/).first()).toBeVisible();
  await expect(card.getByText(/1\.986,00/)).toHaveCount(0);
  expect(errori).toEqual([]);
});

test('Obiettivi: una tappa si aggiunge dall\'elenco, resta dopo il ricarico e si toglie', async ({ page }) => {
  // Il corpo del modulo e' l'unica cosa che nessun test di contratto vede: il
  // contratto costruisce il FormData da solo, quindi un campo `name` scritto
  // male nella card passerebbe. Qui si passa dal browser vero.
  const errori = raccogliErrori(page);
  await avvia(page);
  await apri(page, 'Obiettivi');
  await page.getByRole('button', { name: 'Nuovo obiettivo' }).click();
  const modulo = page.getByRole('dialog');
  await modulo.getByLabel('Nome', { exact: true }).fill('Vacanza e2e');
  await modulo.getByLabel('Importo iniziale').fill('0');
  await modulo.getByLabel('Obiettivo', { exact: true }).fill('10000');
  await modulo.getByRole('button', { name: 'Salva obiettivo' }).click();
  const card = page.locator('[data-slot="card"]', { has: page.getByText('Vacanza e2e') });
  await expect(card).toBeVisible();

  await card.getByRole('button', { name: 'Aggiungi una tappa' }).click();
  await card.getByLabel('Nome della tappa').fill('Biglietti e2e');
  await card.getByLabel('Importo della tappa').fill('2500');
  await card.getByRole('button', { name: 'Aggiungi una tappa' }).click();
  await expect(card.getByText('Biglietti e2e')).toBeVisible();

  // Dopo il ricarico c'e' ancora: e' stato salvato, non solo disegnato.
  await page.reload();
  await expect(page.getByText('Dati aggiornati')).toBeVisible();
  await apri(page, 'Obiettivi');
  const dopo = page.locator('[data-slot="card"]', { has: page.getByText('Vacanza e2e') });
  await expect(dopo.getByText('Biglietti e2e')).toBeVisible();

  await dopo.getByRole('button', { name: 'Elimina Biglietti e2e' }).click();
  await expect(dopo.getByText('Biglietti e2e')).toHaveCount(0);
  expect(errori).toEqual([]);

  // Una tappa piu' grande dell'obiettivo non e' una tappa: l'app dice perche'.
  // Questo passo sta in fondo apposta: il 422 e' voluto, ma il browser lo
  // registra come errore di rete, e sporcherebbe il controllo qui sopra.
  await dopo.getByRole('button', { name: 'Aggiungi una tappa' }).click();
  await dopo.getByLabel('Nome della tappa').fill('Troppo grande e2e');
  await dopo.getByLabel('Importo della tappa').fill('20000');
  await dopo.getByRole('button', { name: 'Aggiungi una tappa' }).click();
  await expect(dopo.getByText('Una tappa non può valere più dell’obiettivo.')).toBeVisible();
  await expect(dopo.getByText('Troppo grande e2e')).toHaveCount(0);
});

test('Analisi: il periodo si dichiara, e cambiandolo i totali cambiano', async ({ page }) => {
  // La scena che tiene insieme il periodo nuovo: la pagina si apre sugli ultimi
  // dodici mesi, dice quali sono, e cambiando periodo i numeri cambiano davvero.
  // Per vederlo serve una spesa che cade dentro una finestra e non nell'altra:
  // qui sono due, una nel mese di oggi e una nello stesso mese dell'anno scorso.
  // La prima sta in tutte e due le finestre; la seconda sta nell'anno e mai
  // negli ultimi dodici mesi, che sono dodici esatti - dicembre compreso, il
  // mese in cui le due finestre si toccano. I numeri sono tondi e inventati.
  const errori = raccogliErrori(page);
  const oggi = new Date();
  const anno = oggi.getFullYear();
  const mese = String(oggi.getMonth() + 1).padStart(2, '0');
  // La riga vecchia serve solo a dire che i dati cominciano prima del periodo
  // precedente: senza, il confronto non si fa e la tabella mostra dei trattini.
  const righe = [[`${anno - 3}-01-10`, 1], [`${anno - 1}-${mese}-05`, 500], [`${anno}-${mese}-05`, 300]] as const;
  for (const [giorno, importo] of righe) {
    const risposta = await page.request.post('/api/transactions', { data: {
      occurred_on: giorno, transaction_type: 'Expenses', category: 'Housing', amount: importo, account_name: 'Banca' } });
    expect(risposta.ok(), await risposta.text()).toBe(true);
  }

  await avvia(page);
  await page.getByRole('button', { name: 'Andamento annuale' }).click();
  const anni = page.getByLabel('Anno', { exact: true });
  await expect(anni).toHaveValue('last12');
  // E la finestra la dichiara con le sue due date: dodici mesi che finiscono con
  // il mese di oggi, e non l'anno solare in corso. E' la riga che il test
  // controlla con un conto suo, senza chiedere al backend quali date fossero.
  const giorno = (data: Date) => `${String(data.getDate()).padStart(2, '0')}/${String(data.getMonth() + 1).padStart(2, '0')}/${data.getFullYear()}`;
  const inizio = new Date(anno, oggi.getMonth() - 11, 1);
  const fine = new Date(anno, oggi.getMonth() + 1, 0);
  await expect(page.getByText(`dal ${giorno(inizio)} al ${giorno(fine)}`)).toBeVisible();
  const confronto = page.locator('[data-slot="card"]', { has: page.getByText('Le categorie, confrontate') });
  const casa = confronto.getByRole('row', { name: /Housing/ });
  await expect(casa.getByRole('cell').nth(1)).toHaveText(/^300\s*€$/);
  // La mediana e' quella degli ultimi dodici mesi, e viaggia con quanti mesi
  // l'hanno formata: un mese su dodici lo si legge invece di dedurlo.
  await expect(casa).toContainText('mesi con movimenti: 1/12');

  await anni.selectOption(String(anno - 1));
  await expect(page.getByText(`dal 01/01/${anno - 1} al 31/12/${anno - 1}`)).toBeVisible();
  await expect(casa.getByRole('cell').nth(1)).toHaveText(/^500\s*€$/);
  // Le schede annuali sotto non seguono la finestra: seguono un anno solare, e
  // adesso lo dicono. Se l'anno scelto qui restasse attaccato anche agli ultimi
  // dodici mesi, la pagina dichiarerebbe una finestra e disegnerebbe un altro
  // anno - che e' quello che si vedeva: i grafici fermi sull'anno prima.
  await expect(page.getByText(`su ciascun mese del ${anno - 1}`)).toBeVisible();

  await anni.selectOption('last12');
  await expect(casa.getByRole('cell').nth(1)).toHaveText(/^300\s*€$/);
  await expect(page.getByText(`su ciascun mese del ${anno}`)).toBeVisible();
  await expect(page.getByText(`su ciascun mese del ${anno - 1}`)).toHaveCount(0);
  expect(errori).toEqual([]);
});
