import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

/*
 * Le risposte vere del backend contro i tipi con cui il frontend le legge.
 *
 * Il file lo scrive `python -m tests.contratti risposte` nel gate di deploy.
 * Ogni risposta diventa un letterale assegnato al tipo del frontend e passa dal
 * compilatore: un campo che il frontend legge e il backend non manda piu', o
 * manda con un altro nome o un altro tipo, e' un errore di compilazione. E'
 * cosi' che `items` al posto di `streams`, o `Fase.nome` che non esisteva,
 * sarebbero stati fermati prima del deploy.
 *
 * I campi che il frontend non dichiara si tolgono prima del confronto,
 * seguendo il tipo: il backend puo' mandare dati che la pagina non usa, e il
 * compilatore, davanti a un campo in piu', segnala quello e smette di cercare i
 * campi mancanti. Lasciarli voleva dire non vedere proprio gli errori cercati.
 */

const RADICE = resolve(__dirname, '../..');
const FIXTURE = resolve(RADICE, 'tests/fixtures/contracts/risposte.json');
const FILE = resolve(RADICE, 'tests/contracts/__risposte__.ts');

const CONTRATTI: Array<{ risposta: string; tipo: string; da: string }> = [
  { risposta: 'fire', tipo: 'FireData', da: '@/components/fire-page' },
  { risposta: 'fireProfile', tipo: 'RetirementProfile', da: '@/components/settings/retirement-profile-form' },
  { risposta: 'fireStreams', tipo: '{ streams: IncomeStream[] }', da: '@/components/settings/income-streams-form' },
  { risposta: 'fireExpenseRules', tipo: 'ExpenseRulesData', da: '@/components/fire/expenses-by-category' },
  { risposta: 'firePensionShift', tipo: 'PensionShiftData', da: '@/components/fire/age-shift-slider' },
  { risposta: 'liabilities', tipo: 'LiabilityData', da: '@/components/money-dashboard' },
  { risposta: 'settings', tipo: 'SettingsData', da: '@/components/money-dashboard' },
  // Le categorie dell'albero, con il verso e il bisogno/piacere: la card che le
  // gestisce nel Budget legge proprio questi campi.
  { risposta: 'categories', tipo: '{ items: CategoryRow[] }', da: '@/components/settings/category-tree' },
  { risposta: 'netWorth', tipo: 'NetWorthData', da: '@/components/money-dashboard' },
  { risposta: 'accounts', tipo: 'AccountsResponse', da: '@/components/money-dashboard' },
  { risposta: 'transactions', tipo: 'TransactionsPage', da: '@/components/money-dashboard' },
  { risposta: 'events', tipo: 'EventsData', da: '@/components/money-dashboard' },
  { risposta: 'eventDetail', tipo: 'EventDetailData', da: '@/components/money-dashboard' },
  { risposta: 'summary', tipo: 'Summary', da: '@/components/money-dashboard' },
  { risposta: 'summaryBreakdownMonth', tipo: 'SummaryBreakdown', da: '@/components/money-dashboard' },
  { risposta: 'summaryBreakdownYear', tipo: 'SummaryBreakdown', da: '@/components/money-dashboard' },
  { risposta: 'analysis', tipo: 'AnalysisData', da: '@/components/money-dashboard' },
  { risposta: 'budgets', tipo: 'BudgetData', da: '@/components/money-dashboard' },
  { risposta: 'calculations', tipo: 'CalculationData', da: '@/components/money-dashboard' },
  { risposta: 'budgetAnnual', tipo: 'AnnualBudgetData', da: '@/components/money-dashboard' },
  { risposta: 'budgetDashboardMonth', tipo: 'BudgetDashboardData', da: '@/components/money-dashboard' },
  { risposta: 'budgetDashboardYear', tipo: 'BudgetDashboardData', da: '@/components/money-dashboard' },
  { risposta: 'budgetTrends', tipo: 'BudgetTrendsData', da: '@/components/money-dashboard' },
  { risposta: 'budgetSuggestions', tipo: 'BudgetSuggestionsData', da: '@/components/money-dashboard' },
  { risposta: 'goals', tipo: 'GoalsData', da: '@/components/money-dashboard' },
  { risposta: 'investmentsDashboard', tipo: 'InvestmentDashboardData', da: '@/components/money-dashboard' },
  { risposta: 'investmentsLedger', tipo: '{ items: InvestmentTransaction[] }', da: '@/components/money-dashboard' },
  { risposta: 'investmentsAllocation', tipo: 'InvestmentAllocationData', da: '@/components/money-dashboard' },
  { risposta: 'instrumentHistory', tipo: 'InstrumentHistory', da: '@/components/money-dashboard' },
  { risposta: 'balanceSheetSeries', tipo: 'BalanceSheetSeries', da: '@/components/money-dashboard' },
  { risposta: 'notes', tipo: '{ items: NoteData[] }', da: '@/components/money-dashboard' },
  { risposta: 'categorizationRules', tipo: 'CategorizationRulesData', da: '@/components/money-dashboard' },
  { risposta: 'categorizationSuggestions', tipo: 'CategorizationSuggestionsData', da: '@/components/money-dashboard' },
  { risposta: 'recurring', tipo: 'RecurringTransactionData[]', da: '@/components/money-dashboard' },
  { risposta: 'notifications', tipo: '{ items: Notification[] }', da: '@/components/notifications-panel' },
  { risposta: 'importBatches', tipo: '{ items: ImportBatchRow[] }', da: '@/components/import-history-card' },
  { risposta: 'backups', tipo: '{ items?: BackupItem[] }', da: '@/components/money-dashboard' },
];

function importazioni(): string {
  const perModulo = new Map<string, Set<string>>();
  for (const { tipo, da } of CONTRATTI) {
    const nomi = perModulo.get(da) ?? new Set<string>();
    (tipo.match(/\b[A-Z]\w+\b/g) ?? []).forEach((nome) => nomi.add(nome));
    perModulo.set(da, nomi);
  }
  return [...perModulo].map(([da, nomi]) => `import type { ${[...nomi].join(', ')} } from '${da}';`).join('\n');
}

/** Un compilatore che legge dal disco tutto tranne il file generato. */
function compilatore(sorgente: () => string) {
  const config = ts.parseJsonConfigFileContent(
    ts.readConfigFile(resolve(RADICE, 'tsconfig.json'), ts.sys.readFile).config, ts.sys, RADICE);
  const options = { ...config.options, incremental: false };
  const host = ts.createCompilerHost(options);
  const leggi = host.getSourceFile.bind(host);
  const esiste = host.fileExists.bind(host);
  host.getSourceFile = (nome, versione, ...resto) => nome === FILE
    ? ts.createSourceFile(nome, sorgente(), versione) : leggi(nome, versione, ...resto);
  host.fileExists = (nome) => nome === FILE || esiste(nome);
  let precedente: ts.Program | undefined;
  return () => (precedente = ts.createProgram({ rootNames: [FILE], options, host, oldProgram: precedente }));
}

/**
 * Tiene di `valore` solo quello che i tipi dichiarano, ricorsivamente.
 *
 * Si lavora su un elenco di candidati e non su un'unione: un campo che puo'
 * avere piu' forme (l'andamento di un prestito o di una linea di credito) si
 * pota con i campi di tutte, altrimenti si toglierebbero quelli dell'altra.
 */
function pota(checker: ts.TypeChecker, tipi: readonly ts.Type[], valore: unknown, nodo: ts.Node): unknown {
  if (valore === null || typeof valore !== 'object') return valore;
  const candidati = tipi.flatMap((t) => (t.isUnion() ? t.types : [t]).map((u) => checker.getNonNullableType(u)));
  if (Array.isArray(valore)) {
    const elenchi = candidati.filter((t) => checker.isArrayType(t) || checker.isTupleType(t));
    if (elenchi.length === 0) return valore;
    return valore.map((v, i) => pota(checker, elenchi.map((t) => {
      const argomenti = checker.getTypeArguments(t as ts.TypeReference);
      return checker.isTupleType(t) ? argomenti[i] ?? argomenti[0] : argomenti[0];
    }), v, nodo));
  }
  const risultato: Record<string, unknown> = {};
  for (const [chiave, v] of Object.entries(valore as Record<string, unknown>)) {
    const dichiarati = candidati.flatMap((t) => {
      const proprieta = checker.getPropertyOfType(t, chiave);
      if (proprieta) return [checker.getTypeOfSymbolAtLocation(proprieta, nodo)];
      const indice = checker.getIndexInfoOfType(t, ts.IndexKind.String);
      return indice ? [indice.type] : [];
    });
    if (dichiarati.length > 0) risultato[chiave] = pota(checker, dichiarati, v, nodo);
  }
  return risultato;
}

describe.skipIf(!existsSync(FIXTURE))('contratti delle risposte', () => {
  const risposte = existsSync(FIXTURE) ? JSON.parse(readFileSync(FIXTURE, 'utf-8')) as Record<string, unknown> : {};

  // 1. Solo le dichiarazioni, per leggere i tipi.
  let sorgente = [importazioni(), ...CONTRATTI.map(({ risposta, tipo }) => `export declare const ${risposta}: ${tipo};`)].join('\n');
  const compila = compilatore(() => sorgente);
  const dichiarazioni = compila();
  const checker = dichiarazioni.getTypeChecker();
  const fileDichiarazioni = dichiarazioni.getSourceFile(FILE)!;
  const potate: Record<string, unknown> = {};
  for (const istruzione of fileDichiarazioni.statements) {
    if (!ts.isVariableStatement(istruzione)) continue;
    const dichiarazione = istruzione.declarationList.declarations[0];
    const nome = dichiarazione.name.getText(fileDichiarazioni);
    potate[nome] = pota(checker, [checker.getTypeAtLocation(dichiarazione.name)], risposte[nome], dichiarazione);
  }

  // 2. Le risposte potate, assegnate ai tipi.
  sorgente = [importazioni(), ...CONTRATTI.map(({ risposta, tipo }) =>
    `export const ${risposta}: ${tipo} = ${JSON.stringify(potate[risposta] ?? null)};`)].join('\n');
  const programma = compila();
  const righe = sorgente.split('\n');
  const diagnostiche = ts.getPreEmitDiagnostics(programma, programma.getSourceFile(FILE))
    .filter((d) => d.file?.fileName === FILE);
  const rigaDi = (d: ts.Diagnostic) => d.file!.getLineAndCharacterOfPosition(d.start ?? 0).line;
  // Il messaggio del compilatore non dice il percorso: il pezzo di risposta
  // attorno all'errore si'.
  const descrivi = (d: ts.Diagnostic) => `${ts.flattenDiagnosticMessageText(d.messageText, ' ').slice(0, 300)}  <-  …${
    sorgente.slice(Math.max(0, (d.start ?? 0) - 90), (d.start ?? 0) + 40)}…`;

  it('le importazioni dei tipi si compilano', () => {
    const importi = righe.findIndex((r) => r.startsWith('export const'));
    expect(diagnostiche.filter((d) => rigaDi(d) < importi).map(descrivi)).toEqual([]);
  });

  for (const { risposta, tipo } of CONTRATTI) {
    it(`${risposta} rispetta ${tipo}`, () => {
      expect(risposte[risposta], `manca "${risposta}" nel file delle risposte`).toBeDefined();
      const riga = righe.findIndex((r) => r.startsWith(`export const ${risposta}:`));
      expect(diagnostiche.filter((d) => rigaDi(d) === riga).map(descrivi)).toEqual([]);
    });
  }
}, 120_000);
