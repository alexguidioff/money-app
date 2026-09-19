'use client';

import { campiMancanti, downloadFile, responseError } from '@/lib/download';
import { previewEffectiveDate } from '@/lib/effective-date';
import { LEDGER_SENZA_QUOTE, nettoOperazioni } from '@/lib/ledger-preview';
import { splitPayload, accountPayload, budgetCreatePayload, budgetUpdatePayload, categorizationBulkPayload, categorizationRulePayload, goalPayload, ledgerOperationPayload, liabilityTermsPayload, notePayload, recurringPayload, transactionPayload } from '@/lib/payloads';
import { messaggioErroreRegola } from '@/lib/rule-errors';
import { SyntheticEvent, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Flame,
  AlertCircle,
  ArrowDownRight,
  ArrowRightLeft,
  ArrowUpRight,
  BadgeEuro,
  BarChart3,
  Bell,
  Calendar,
  CheckCircle2,
  ChartLine,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Link2,
  CircleDollarSign,
  CreditCard,
  Copy,
  Database,
  Download,
  FileSpreadsheet,
  FileText,
  Filter,
  Landmark,
  LayoutDashboard,
  LineChart as LineChartIcon,
  Menu,
  Gauge,
  Pencil,
  PiggyBank,
  Plus,
  ReceiptText,
  RefreshCw,
  Search,
  Save,
  Settings,
  Split,
  StickyNote,
  Users,
  LogOut,
  Target,
  Trash2,
  TrendingDown,
  TrendingUp,
  WalletCards,
  X,
  Unlink,
} from 'lucide-react';
import { Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, LineChart, Pie, PieChart, ReferenceLine, Treemap, XAxis, YAxis } from 'recharts';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { PDFImportPreview, type PDFTransaction } from '@/components/ui/pdf-import-preview';
import { RefundPicker } from '@/components/ui/refund-picker';
import {
  ChartConfig,
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart';
import { I18nProvider, useI18n } from '@/lib/i18n-context';
import { LoginScreen, type AccountSummary } from '@/components/login-screen';
import { SharedTotalsView } from '@/components/shared-totals';
import { FirePage } from '@/components/fire-page';
import { FireSettingsSection } from '@/components/settings/fire-settings-section';
import { AccountSettings, type AccountState } from '@/components/account-settings';
import { NotificationsPanel, type Notification } from '@/components/notifications-panel';
import { PageHelp } from '@/components/page-help';
import { LANG_LABELS, translations, type Lang, type TranslationKey } from '@/lib/translations';

export type Summary = {
  income: number;
  expenses: number;
  savings: number;
  netWorthDetail: {
    total: number;
    marketValue: number;
    investedCapital: number;
    gain: number;
    gainPercent: number | null;
    otherBalance: number;
    snapshotPeriod: string | null;
  };
  netWorthComparison: { period: string; periodYear: number; periodMonth: number | null; total: number; totalDelta: number } | null;
  budgetUsed: number;
  control: { level: 'ok' | 'watch' | 'over'; spentPercent: number; timePercent: number; overBudgetCategories: number };
  projection: { state: 'closed' | 'current' | 'future'; estimate: number | null; planned: number; spentSoFar: number } | null;
  goalCoverage: { savedThisPeriod: number; monthlyNeeded: number; coverage: number | null };
  plannedExpenses: number;
  actualExpenses: number;
  daysInPeriod: number;
  daysPassed: number;
  periodCompletion: number;
  savingsRate: number | null;
  comparison: {
    period: string;
    periodYear: number;
    periodMonth: number | null;
    income: number;
    expenses: number;
    savings: number;
    incomeDelta: number;
    expensesDelta: number;
    savingsDelta: number;
  } | null;
};

type BreakdownSection = {
  categories: Array<{ name: string; tracked: number; budget: number; completion: number | null; remaining: number; excess: number }>;
  plannedTotal: number;
  actualTotal: number;
};

type PieBreakdown = { items: Array<{ name: string; value: number; color: string }>; total: number };

export type SummaryBreakdown = {
  period: string;
  sections: { income: BreakdownSection; expenses: BreakdownSection; savings: BreakdownSection };
  pie: { income: PieBreakdown; expenses: PieBreakdown; savings: PieBreakdown };
};

type MonthlyBudgetPoint = { month: string; inBudget: number; remaining: number; excess: number; isCurrentMonth: boolean };

export type AnalysisData = {
  year: number;
  monthlyBudget: { income: MonthlyBudgetPoint[]; expenses: MonthlyBudgetPoint[]; savings: MonthlyBudgetPoint[] };
  topExpenseCategories: Array<{ name: string; value: number; color: string }>;
  savingsByMonth: Array<{ month: string; amount: number }>;
  investedByMonth: Array<{ month: string; amount: number }>;
  categoryTransactions: Array<{ date: string; amount: number; description: string }>;
  categoryOptions: string[];
};

export type Transaction = {
  countsInBudget?: boolean;
  refundOfId?: number | null;
  refundedById?: number | null;
  incomplete?: boolean;
  missingFields?: string[];
  id: string;
  description: string;
  category: string;
  amount: number;

  transactionType: 'Income' | 'Expenses' | 'Investment' | 'Transfers' | 'Debt';

  occurredOn: string;
  effectiveOn: string;
  accountName: string | null;
  destinationName: string | null;
  goal: string | null;
  details: string | null;
  linkedLedger?: LinkedLedgerSummary[];
  liabilitySplit?: { id: number; kind: 'repayment' | 'drawdown' | 'charge'; principal: number; interest: number; classified: boolean } | null;
};

/* La quadratura di un gruppo collegato: un'operazione puo' essere finanziata da
   due bonifici e un bonifico da piu' operazioni, quindi il conto si fa sul
   gruppo intero, non sulla riga aperta. La calcola il backend. */
type LedgerGroupBalance = {
  transfers: number; operations: number; difference: number; balanced: boolean;
  transactionCount: number; operationCount: number;
};

type LinkedLedgerSummary = {
  linkId: number;
  id: number;
  occurredOn: string;
  name: string;
  transactionType: string;
  amount: number;
  units: number | null;
  price: number | null;
  currency: string;
  fee: number;
  notes: string | null;
};

type LinkedLedgerDraft = {
  name: string;
  transactionType: LedgerTypeDaMovimento;
  // Solo per i tipi senza quote: un dividendo muove il suo importo, non
  // quote per prezzo.
  amount: string;
  units: string;
  price: string;
  fee: string;
  notes: string;
};

type AccountValuation = { id: number; observedOn: string; value: number; notes: string | null };

export type TransactionsPage = { items: Transaction[]; total: number; offset: number; years: string[]; goals: string[] };
export type AccountsResponse = { items: Account[] };

export type Account = {
  isActive?: boolean;
  countsInNetWorth?: boolean;
  isLiquid?: boolean;
  // Il conto dove stanno i titoli: e' li' che puo' puntare un Investment.
  isBroker?: boolean;
  notes?: string | null;
  needsManualValuation?: boolean;
  // Vero quando i movimenti di questo conto risultano collegati al ledger
  // investimenti: allora `value` e' il valore di mercato e `calculatedBalance`
  // il costo versato. Non e' una casella che si spunta, e' un fatto dedotto.
  valuedByLedger?: boolean;
  valuations?: AccountValuation[];
  id: number;
  name: string;
  group: 'bank' | 'asset' | 'liability' | 'financial';
  // Quanto vale oggi: lo stesso numero della pagina Patrimonio, debiti
  // positivi compresi. `calculatedBalance` risponde a un'altra domanda -
  // cosa dicono i movimenti - e serve solo alla riconciliazione.
  value: number;
  startingBalance: number;
  calculatedBalance: number;
};


type BalanceSheetLevel = 'networth' | 'side' | 'component' | 'instruments';
type BalanceSheetKey = { key: string; label: string; drillTo: BalanceSheetLevel | null };
export type BalanceSheetSeries = {
  level: BalanceSheetLevel;
  breadcrumb: Array<{ level: BalanceSheetLevel; side: string | null; label: string }>;
  side?: string | null;
  component?: string | null;
  series: Array<{ period: string; label: string; values: Record<string, number>; total: number }>;
  keys: BalanceSheetKey[];
  // Linee ferme a zero, non disegnate: vanno dette, se no sembra che manchino
  // dei dati invece che essere una scelta.
  hidden: number;
};


type BudgetItem = {
  id: number;
  category: string;
  categoryLabel: string;
  categoryGroup: string | null;
  amount: number;
  actual: number;
  // Solo informativo: quanto era avanzato (o mancato) il mese prima.
  previousLeftover: number;
};

export type BudgetData = {
  items: BudgetItem[];
  plannedTotal: number;
  actualTotal: number;
  balance: BudgetBalance;
};

type BudgetSuggestion = { category: string; median: number; average: number; max: number; monthsWithSpending: number; monthsConsidered: number };
export type BudgetSuggestionsData = { period: string; monthsBack: number; budgetType: string; items: BudgetSuggestion[] };
type BudgetBalance = { income: number; expenses: number; savings: number; storedSavings: number; hasIncomePlan: boolean };
const BUDGET_VUOTO: BudgetData = { items: [], plannedTotal: 0, actualTotal: 0,
  balance: { income: 0, expenses: 0, savings: 0, storedSavings: 0, hasIncomePlan: false } };
type BudgetGroupSplit = { group: 'Needs' | 'Wants' | 'Other'; planned: number; actual: number; plannedShare: number; actualShare: number };

export type AnnualBudgetData = {
  year: number;
  items: Array<{
    category: string;
    categoryLabel: string;
    categoryGroup: string | null;
    months: Array<{ month: number; id: number | null; amount: number; actual: number }>;
    plannedTotal: number;
    actualTotal: number;
  }>;
  monthTotals: Array<{ month: number; label: string; planned: number; actual: number }>;
  balance: BudgetBalance;
};

export type BudgetDashboardData = {
  period: string;
  plannedTotal: number;
  actualTotal: number;
  remaining: number;
  usage: number;
  periodYear: number;
  periodMonth: number | null;
  balance: BudgetBalance;
  groups: BudgetGroupSplit[];
  overBudgetCategories: number;
  categories: Array<{ category: string; categoryLabel: string; categoryGroup: string | null; planned: number; previousLeftover: number; actual: number; variance: number; usage: number | null }>;
  months: Array<{ month: number; label: string; planned: number; actual: number }>;
  topTransactions: Transaction[];
};

export type BudgetTrendsData = {
  years: Array<{ year: number; plannedTotal: number; actualTotal: number; months: Array<{ month: number; label: string; planned: number; actual: number }> }>;
  comparison: Array<Record<string, string | number>>;
};

type BudgetAlertData = {
  alert_count: number;
};

export type RecurringTransactionData = {
  id: number;
  description: string;
  category: string;
  amount: number;
  type: string;
  transactionType: string;
  recurrence_rule: string | null;
  recurrence_end_date: string | null;
  is_recurring_template: boolean;
  next_occurrence: string | null;
};

export type CategorizationRuleData = {
  id: number;
  position: number;
  pattern: string;
  isRegex: boolean;
  category: string;
  transactionType: 'Expenses' | 'Income' | null;
  minAmount: number | null;
  maxAmount: number | null;
  active: boolean;
};

export type CategorizationRulesData = {
  items: CategorizationRuleData[];
};

export type RuleProposalData = {
  pattern: string;
  category: string;
  transactionType: 'Expenses' | 'Income' | null;
  occorrenze: number;
  quota: number;
  fiducia: 'sicura' | 'incerta';
  altre: Array<{ category: string; count: number }>;
};

// Le descrizioni che nessuna categoria tiene insieme: si mostrano e basta,
// non diventano proposte, perche' qualunque scelta sarebbe giusta a meta'.
export type RuleIncoherentData = {
  pattern: string;
  occorrenze: number;
  categorie: Array<{ category: string; count: number }>;
};

export type CategorizationSuggestionsData = {
  proposte: RuleProposalData[];
  incoerenti: RuleIncoherentData[];
};

type GoalData = {
  id: number;
  name: string;
  startingAmount: number;
  targetAmount: number;
  startDate: string | null;
  targetDate: string | null;
  kind: 'contributions' | 'net_worth' | 'portfolio';
  targetAccount: string | null;
  monthlyNeeded: number | null;
  weeklyNeeded: number | null;
  monthsLeft: number | null;
  overdue: boolean;
  status: 'on_track' | 'slightly_behind' | 'behind' | 'completed' | null;
  timeProgress: number | null;
  completedAt: string | null;
  linkedAmount: number;
  linkedMovements: number;
  currentAmount: number;
  remainingAmount: number;
  progress: number;
  completed: boolean;
  history: Array<{ label: string; amount: number }>;
};

export type GoalsData = { items: GoalData[]; active: number; completed: number; targetTotal: number; currentTotal: number; monthlyNeededTotal: number; plannedSavings: number; hasPlannedSavings: boolean; monthlyGap: number };

export type NetWorthData = {
  requestedPeriod: string;
  dataPeriod: string | null;
  totals: { bank: number; asset: number; liability: number; financial: number; liquid: number; netWorth: number };
  currencies: Array<{
    code: string;
    available: boolean;
    inverted: boolean;
    total: number | null;
    change: number | null;
    changePercent: number | null;
    rate: number | null;
  }>;
};

export type CalculationData = {
  period: string;
  income: number;
  expenses: number;
  savings: number;
  trackingBalance: number;
  savingsRate: number | null;
  daysInPeriod: number;
  daysPassed: number;
  budgetDelta: Record<string, number>;
  accountDifferences: number;
};

export type SettingsData = {
  settings: Record<string, string>;
  labels: Record<string, string>;
  options: Record<string, string[]>;
  categoriesByType: Record<string, string[]>;
  budgetYearsByType: Record<'Expenses' | 'Income' | 'Savings', string[]>;
};

// I tipi che il ledger conosce: acquisto, vendita, i movimenti di solo
// contante (dividendi e interessi, commissioni, versamenti) e lo split, che
// non muove denaro ma moltiplica le quote.
export type LedgerOperationType = 'Buy' | 'Sell' | 'Dividend' | 'Fee' | 'Split';

// Il tipo di una riga si legge per esteso: chiamare "Vendi" un dividendo e' il
// modo piu' veloce per correggere la riga sbagliata.
const LEDGER_TYPE_LABEL: Record<LedgerOperationType, TranslationKey> = {
  Buy: 'buy', Sell: 'sell', Dividend: 'ledgerDividend', Fee: 'ledgerFee', Split: 'ledgerSplit',
};

// I tipi che si possono creare partendo da un movimento. Lo split non c'e':
// non muove un centesimo e vuole importo zero, mentre un movimento un importo
// ce l'ha sempre. Gli altri sei si collegano, perche' la regola del
// collegamento guarda il tipo del *movimento* (dev'essere Investment), non
// quello dell'operazione.
export const LEDGER_TYPES_DA_MOVIMENTO = (Object.keys(LEDGER_TYPE_LABEL) as LedgerOperationType[])
  .filter((tipo) => tipo !== 'Split');
export type LedgerTypeDaMovimento = Exclude<LedgerOperationType, 'Split'>;
// Senza quote: si digita l'importo, non quote e prezzo. L'elenco sta in
// `lib/ledger-preview`, insieme al calcolo che lo usa: scritto in due posti,
// prima o poi uno dei due resta indietro.
const SOLO_CONTANTE_DA_MOVIMENTO: readonly string[] = LEDGER_SENZA_QUOTE;

export type InvestmentTransaction = { id: number; occurredOn: string; name: string; transactionType: LedgerOperationType; amount: number; signedAmount: number; units: number; price: number; currency: string; fee: number; notes: string | null; runningUnits: number;
  // Agganciata a un movimento dei conti: e' il collegamento che dice a quale
  // conto attribuire il guadagno di questa operazione.
  linked: boolean };
type LedgerCsvItem = { row: number; occurred_on?: string; name?: string; transaction_type?: 'Buy' | 'Sell'; amount?: number; units?: number; price?: number; currency?: string; duplicate: boolean; duplicateOf?: { id: number | null; occurredOn: string; sourceRow?: number } | null; error?: string | null };
type InvestmentPosition = { name: string; isOpen: boolean; units: number; costBasis: number; netContributed: number; price: number; hasQuote: boolean; marketValue: number; realizedGain: number; unrealizedGain: number; totalGain: number; returnRate: number | null; incomeReceived: number; feesPaid: number; currency: string; instrumentId: number | null; providerSymbol: string | null; assetClass: string; area: string; sector: string; targetWeight: number | null };
// `rebalance.amount` e' firmato: positivo vuol dire sopra il peso obiettivo,
// cioe' da vendere. Chi legge deve poterlo vedere anche nella tabella, dove
// l'importo viene da comprare o da vendere secondo quel segno.
// Il rendimento del portafoglio: il valore oppure il motivo per cui non c'e'.
// `reason` e' un codice, la frase la sceglie la traduzione. Mai zero al posto
// di un motivo: uno zero si legge "non ho guadagnato niente", che e'
// un'affermazione, e spesso falsa.
export type InvestmentReturns = { twr: { value: number | null; reason: string | null }; xirr: { value: number | null; reason: string | null }; months: number; since: string | null; asOf: string | null };
// Il confronto: il simbolo scelto e il primo mese in cui le due storie si
// sovrappongono. `from` nullo vuol dire che non c'e' niente da confrontare -
// nessun indice configurato, o troppo poca storia in comune - e non e' un
// guasto: e' una funzione che non e' stata accesa.
type InvestmentBenchmark = { symbol: string | null; from: string | null; months: number };
export type InvestmentDashboardData = { snapshot: { period: string | null; marketValue: number; investedCapital: number; gain: number; returnRate: number }; ledger: { marketValue: number; costBasis: number; gain: number; quotedPositions: number; activePositions: number }; positions: InvestmentPosition[]; history: Array<{ period: string; label: string; marketValue: number; investedCapital: number; gain: number; returnRate: number | null; twrCurve: number | null; benchmarkCurve: number | null }>; contributions: Array<{ period: string; label: string; amount: number }>; rebalance: { total: number; declaredWeight: number; warnings: string[]; rows: Array<{ name: string; currentWeight: number; targetWeight: number; drift: number; amount: number }> }; returns: InvestmentReturns; benchmark: InvestmentBenchmark };
export type InvestmentAllocationData = {
  total: number;
  allocations: Record<'instrument' | 'sector' | 'assetType' | 'holdings' | 'currency', Array<{ label: string; value: number; weight: number }>>;
  coverage: {
    covered: number; uncovered: number; coveredPercent: number;
    missing: Array<{ instrument: string; value: number; reason: string; code: string | null }>;
    lastFetch: string | null;
    sourceErrors: Array<{ symbol: string; code: string }>;
  };
};
type LiabilityDrawdown = { occurredOn: string; amount: number };
type LiabilityProfileData = { debtType: string; originalPrincipal: number; annualRate: number; rateType: string; paymentFrequency: string; paymentStructure: string;
  graceInterest?: string; startDate: string; repaymentStartDate: string; endDate: string; plannedDrawdowns: LiabilityDrawdown[]; status: string; notes: string | null;
  kind: 'term_loan' | 'credit_line';
  creditLimit: number | null };
type LiabilityScheduleRow = { number: number; dueOn: string; payment: number; principal: number; interest: number; remaining: number };
type LiabilityComparison = { plannedDebt: number; actualDebt: number; plannedPrincipal: number; actualPrincipal: number; plannedInterest: number; actualInterest: number; plannedTotal: number; actualTotal: number };
export type LiabilityData = { orphaned: Array<{ accountId: number; kind: 'term_loan' | 'credit_line'; notes: string | null }>; asOf: string; summary: { totalDebt: number; weightedRate: number | null; monthlyService: number; configured: number; total: number; unclassifiedCount: number; unclassifiedAmount: number }; items: LiabilityItem[] };

type LiabilityPayment = { id: number; occurredOn: string; kind: 'repayment' | 'drawdown' | 'charge'; principal: number; interest: number; total: number; classified: boolean; description: string; sourceAccount: string | null; destinationAccount: string | null; transactionIds: number[] };
type LiabilityMovement = { id: number; occurredOn: string; type: string; amount: number; effect: number; description: string; accountName: string | null; destinationName: string | null };

/** Un prestito a rate: capitale, piano, scadenze, confronto col piano. */
type TermLoanItem = { kind: 'term_loan'; accountId: number; name: string; outstanding: number; startingBalance: number; accountNotes: string | null; profile: LiabilityProfileData | null; payment: number | null; monthlyService: number; nextPayment: LiabilityScheduleRow | null; totalInterest: number; theoreticalRemaining: number | null; difference: number | null; drawnPrincipal: number; principalRepaid: number; interestCharged: number | null; interestPaid: number; interestOutstanding: number | null; actualTotalDebt: number | null; unclassified: { count: number; amount: number }; suggestedDrawdowns: LiabilityDrawdown[]; trend: Array<{ period: string; label: string; plannedDebt: number; actualDebt: number | null; actualDrawn: number | null; actualRepaid: number | null; plannedInterest: number; actualInterestCharged: number | null; actualInterestPaid: number | null; plannedPayment: number }>;
  // Perche' il piano non si puo' costruire: prima ogni rifiuto era una lista
  // vuota e la pagina restava bianca.
  reconciliationDifference: number; scheduleIssue: string | null; comparison: LiabilityComparison | null; schedule: LiabilityScheduleRow[]; payments: LiabilityPayment[]; movements: LiabilityMovement[] };

/**
 * Una linea di credito: un saldo che galleggia, non un piano.
 *
 * Niente tranche, scadenze ne' residuo teorico - uno scoperto non li ha, e
 * il compilatore ora impedisce di cercarglieli. Il limite e' facoltativo:
 * `null` vuol dire illimitato, e allora l'utilizzo non si puo' calcolare.
 */
type CreditLineItem = {
  kind: 'credit_line';
  accountId: number;
  name: string;
  accountNotes: string | null;
  profile: LiabilityProfileData;
  exposure: number;
  creditLimit: number | null;
  utilisation: number | null;
  peakExposure: number;
  interestThisYear: number;
  rate: number;
  unclassified: { count: number; amount: number };
  trend: Array<{ period: string; label: string; exposure: number }>;
  payments: LiabilityPayment[];
  movements: LiabilityMovement[];
};

type LiabilityItem = TermLoanItem | CreditLineItem;
export type NoteData = { id: number; section: string; title: string; body: string; status: string | null; updatedAt: string | null };
// Il mese di partenza e' oggi, non una data scritta nel codice: con il 2026
// cablato un anno nuovo si sarebbe aperto su un mese vecchio per sempre.
const MESE_CORRENTE = { anno: new Date().getFullYear(), mese: new Date().getMonth() + 1 };
// Singolo Date() per componente, calcolato una volta sola: anche se la pagina
// ri-renderizza, non rifacciamo l'allocazione a ogni tasto premuto.
const OGGI = new Date();
type PeriodSelection = { year: number; month: number; scope: 'month' | 'year' };

// Dove eravamo rimasti. Sta nel browser, non nel database: e' una comodita' di
// questo schermo, non un dato dell'account.
const CHIAVE_VISTA = 'money.ultima-vista';

type Section = 'Panoramica' | 'Movimenti' | 'Budget' | 'Obiettivi' | 'Patrimonio' | 'Debiti' | 'Investimenti' | 'FIRE' | 'Insieme' | 'Appunti' | 'Report' | 'Impostazioni';



const fallbackSummary: Summary = {
  income: 1544.74,
  expenses: 441.31,
  savings: 988.98,
  netWorthDetail: {
    total: 80346.18,
    marketValue: 65000,
    investedCapital: 55000,
    gain: 10000,
    gainPercent: 18.18,
    otherBalance: 15346.18,
    snapshotPeriod: null,
  },
  netWorthComparison: null,
  budgetUsed: 62,
  control: { level: 'ok', spentPercent: 62, timePercent: 60, overBudgetCategories: 0 },
  projection: null,
  goalCoverage: { savedThisPeriod: 0, monthlyNeeded: 0, coverage: null },
  plannedExpenses: 820,
  actualExpenses: 441.31,
  daysInPeriod: 31,
  daysPassed: 31,
  periodCompletion: 1,
  savingsRate: 0.64,
  comparison: null,
};

const fallbackTransactions: Transaction[] = [
  { id: '1', description: 'Stipendio', category: 'Entrate', occurredOn: '2026-07-27', effectiveOn: '2026-07-27', amount: 1544.74, transactionType: 'Income', accountName: null, destinationName: null, goal: null, details: 'Stipendio' },
];

const fallbackSettings: SettingsData = {
  settings: {
    // Vuoto finche' le impostazioni non arrivano: il tema lo decide l'ultimo
    // scelto su questo browser, non un giallo di ripiego.
    header_color: '',
    late_income_shift: 'Inactive',
    late_income_day: '20',
  },
  labels: {},
  options: {
    years: [String(MESE_CORRENTE.anno)],
    overviewYears: [String(MESE_CORRENTE.anno)],
    colors: ['Blue', 'Orange', 'Green', 'Yellow', 'Purple', 'Light Blue'],
    categories: [],
  },
  categoriesByType: { Expenses: [], Income: [], Savings: [], Transfers: [] },
  budgetYearsByType: { Expenses: [String(MESE_CORRENTE.anno)], Income: [String(MESE_CORRENTE.anno)], Savings: [String(MESE_CORRENTE.anno)] },
};

// Colore principale (Settings E16 nel workbook): tinge logo, avatar e barra del
// budget. Tonalita' chiare, perche' ci va sopra testo scuro.
type Theme = {
  sidebar: string;   // fondo del menu laterale
  primary: string;   // pulsanti e voce di menu attiva
  hover: string;     // stato hover dei pulsanti pieni
  deep: string;      // card scure (budget, tab attivi)
  accent: string;    // logo, avatar, barre di avanzamento
  onAccent: string;  // testo sopra l'accento
  page: string;      // sfondo della pagina
};

// Colore principale (Settings E16 nel workbook): tinge menu, sfondo, pulsanti e
// accenti. Ogni tema tiene lo stesso contrasto dell'originale verde, cambiando
// solo la tinta, cosi' il testo bianco sul menu resta sempre leggibile.
const THEMES: Record<string, Theme> = {
  Yellow: { sidebar: '#332c0f', primary: '#51461a', hover: '#3f3612', deep: '#5c501f', accent: '#d9f373', onAccent: '#342d14', page: '#f9f8f3' },
  Orange: { sidebar: '#331a0f', primary: '#512a1a', hover: '#3f2012', deep: '#5c311f', accent: '#f7bc8a', onAccent: '#341d14', page: '#f9f5f3' },
  Green: { sidebar: '#0f331e', primary: '#1a5131', hover: '#123f25', deep: '#1f5c38', accent: '#8fdcae', onAccent: '#143421', page: '#f3f9f6' },
  'Light Blue': { sidebar: '#0f2f33', primary: '#1a4a51', hover: '#12393f', deep: '#1f545c', accent: '#a3e3ea', onAccent: '#142f34', page: '#f3f8f9' },
  Blue: { sidebar: '#0f1c33', primary: '#1a2e51', hover: '#12233f', deep: '#1f355c', accent: '#9cc7fb', onAccent: '#141f34', page: '#f3f5f9' },
  Purple: { sidebar: '#220f33', primary: '#371a51', hover: '#2a123f', deep: '#3f1f5c', accent: '#c9b6f5', onAccent: '#251434', page: '#f6f3f9' },
};

// L'ultimo tema applicato, per ridipingere subito al prossimo avvio: senza, chi
// non usa il giallo lo vedeva lampeggiare finche' le impostazioni non arrivavano.
const CHIAVE_TEMA = 'money-tema';

// Le variabili vanno sull'elemento radice: i dialoghi sono montati in un
// portale fuori dall'albero della pagina e altrimenti non le erediterebbero.
function applyTheme(colorName: string): void {
  try { window.localStorage.setItem(CHIAVE_TEMA, colorName); } catch { /* solo un di piu' */ }
  const theme = THEMES[colorName] ?? THEMES.Yellow;
  const root = document.documentElement;
  root.style.setProperty('--money-sidebar', theme.sidebar);
  root.style.setProperty('--money-primary', theme.primary);
  root.style.setProperty('--money-primary-hover', theme.hover);
  root.style.setProperty('--money-deep', theme.deep);
  root.style.setProperty('--money-accent', theme.accent);
  root.style.setProperty('--money-on-accent', theme.onAccent);
  root.style.setProperty('--money-page', theme.page);
}


// Il periodo arriva in numeri e diventa testo qui, nella lingua scelta: il
// server mandava "Luglio 2025" e finiva tale e quale in una pagina in inglese.
// Aspetta che si smetta di scrivere prima di dare retta al testo: senza, ogni
// lettera digitata nella ricerca diventerebbe una chiamata al server.
function useRitardato<T>(valore: T, millisecondi = 300): T {
  const [ritardato, setRitardato] = useState(valore);
  useEffect(() => {
    const timer = window.setTimeout(() => setRitardato(valore), millisecondi);
    return () => window.clearTimeout(timer);
  }, [valore, millisecondi]);
  return ritardato;
}

// Gli ambiti di ricarica: una modifica dichiara cosa ha toccato.
type LoadScope = 'settings' | 'budget' | 'trends' | 'networth' | 'overview' | 'analysis' | 'ledger' | 'investments' | 'notes' | 'goals' | 'rules' | 'categoryRules';

// Cosa non dipende da nessun selettore: si carica una volta e resta.
const SCOPE_FISSI: LoadScope[] = ['settings', 'ledger', 'notes', 'goals', 'rules'];

// Serve solo a uscire dal blocco health senza passare per il catch di rete.
class SkipGroup extends Error {}

/* Il salvataggio e' fallito e il motivo e' gia' a video: chi cattura non deve
   sostituirlo col messaggio generico. Sta in un tipo invece che in un flag
   perche' il flag andava letto dallo stato, che qui e' ancora quello vecchio. */
class SaveFailed extends Error {
  constructor(readonly spiegato: boolean) { super('save'); }
}

function ControlRow({ control, onOpenBudget }: { control: Summary['control']; onOpenBudget: () => void }) {
  const { t } = useI18n();
  // Nessun budget nel periodo: non c'e' niente da tenere sotto controllo.
  if (!control.spentPercent && !control.overBudgetCategories) return null;
  const stile = {
    ok: { classe: 'border-[#cfe6dc] bg-[#e5f3ed] text-[#2d7b65]', titolo: t('controlOk') },
    watch: { classe: 'border-[#f0e2c2] bg-[#fbf3e2] text-[#9a7b2f]', titolo: t('controlWatch') },
    over: { classe: 'border-[#f4d8ce] bg-[#fce9e3] text-[#bd5e46]', titolo: t('controlOver') },
  }[control.level];
  const dettaglio = control.overBudgetCategories > 0
    ? t('controlDetailWithCategories', { spent: control.spentPercent, time: control.timePercent, count: control.overBudgetCategories })
    : t('controlDetail', { spent: control.spentPercent, time: control.timePercent });
  return <button type="button" onClick={onOpenBudget} className={`mb-5 flex w-full items-center justify-between gap-3 rounded-2xl border px-4 py-3 text-left transition hover:brightness-[0.98] ${stile.classe}`}>
    <span className="flex items-center gap-3">
      <span className="size-2.5 shrink-0 rounded-full bg-current" />
      <span><span className="text-sm font-semibold">{stile.titolo}</span> <span className="text-xs opacity-80">{dettaglio}</span></span>
    </span>
    <ChevronRight className="size-4 shrink-0 opacity-70" />
  </button>;
}

function formatPeriodRef(monthNames: string[], year: number | null | undefined, month: number | null | undefined): string {
  if (year === null || year === undefined) return '';
  return month ? `${monthNames[month - 1]} ${year}` : String(year);
}

function formatComparisonChange(
  t: (key: TranslationKey, params?: Record<string, string | number>) => string,
  formatEuro: (value: number) => string,
  monthNames: string[],
  delta: number | undefined,
  comparePeriod: { periodYear: number; periodMonth: number | null } | null | undefined,
): string {
  if (delta === undefined || !comparePeriod) return t('noComparisonSelected');
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
  return `${sign}${formatEuro(Math.abs(delta))} vs ${formatPeriodRef(monthNames, comparePeriod.periodYear, comparePeriod.periodMonth)}`;
}

// Regola della colonna N del foglio Transactions: un'entrata incassata dal
// giorno configurato in poi pesa sul mese successivo. Qui serve solo per
// l'anteprima nel form: il valore salvato lo calcola sempre il backend.
// Per le Spese, avere ancora budget disponibile e' un bene (variance = planned-actual >= 0);
// per Entrate/Risparmi e' l'opposto: superare l'obiettivo (actual >= planned, quindi variance <= 0) e' il buon esito.
function budgetVarianceIsGood(budgetType: 'Expenses' | 'Income' | 'Savings', variance: number): boolean {
  return budgetType === 'Expenses' ? variance >= 0 : variance <= 0;
}

// Accetta importi positivi o zero. Rifiuta stringa vuota, NaN e numeri negativi.
// `Number('')` fa 0 e `Number('abc')` fa NaN: senza questo helper il pulsante
// "Salva" partiva anche con campo vuoto o con testo non numerico.
function isPositiveNumber(value: string): boolean {
  if (value.trim() === '') return false;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0;
}

const navItems: { label: Section; labelKey: TranslationKey; icon: typeof LayoutDashboard }[] = [
  { label: 'Panoramica', labelKey: 'navPanoramica', icon: LayoutDashboard },
  { label: 'Budget', labelKey: 'navBudget', icon: BarChart3 },
  { label: 'Movimenti', labelKey: 'navMovimenti', icon: ReceiptText },
  { label: 'Obiettivi', labelKey: 'navObiettivi', icon: Target },
  { label: 'Patrimonio', labelKey: 'navPatrimonio', icon: TrendingUp },
  { label: 'Debiti', labelKey: 'navDebiti', icon: CreditCard },
  { label: 'Investimenti', labelKey: 'navInvestimenti', icon: BarChart3 },
  { label: 'Insieme', labelKey: 'navInsieme', icon: Users },
  { label: 'Appunti', labelKey: 'navAppunti', icon: StickyNote },
  { label: 'FIRE', labelKey: 'fireSection', icon: Flame },
  { label: 'Report', labelKey: 'navReport', icon: FileText },
];

const SECTION_LABEL_KEYS: Record<Section, TranslationKey> = {
  Panoramica: 'navPanoramica',
  Movimenti: 'navMovimenti',
  Budget: 'navBudget',
  Obiettivi: 'navObiettivi',
  Patrimonio: 'navPatrimonio',
  Debiti: 'navDebiti',
  Investimenti: 'navInvestimenti',
  FIRE: 'fireSection',
  Insieme: 'navInsieme',
  Appunti: 'navAppunti',
  Report: 'navReport',
  Impostazioni: 'navImpostazioni',
};

const SECTION_DESC_KEYS: Record<Exclude<Section, 'Panoramica'>, TranslationKey> = {
  Movimenti: 'sectionMovimentiDesc',
  Budget: 'sectionBudgetDesc',
  Obiettivi: 'sectionObiettiviDesc',
  Patrimonio: 'sectionPatrimonioDesc',
  Debiti: 'sectionDebitiDesc',
  Investimenti: 'sectionInvestimentiDesc',
  // La pagina ha la sua descrizione: quella della sezione in Impostazioni
  // parla dei moduli da compilare, non di cosa mostra il piano.
  FIRE: 'firePageDesc',
  Insieme: 'sectionInsiemeDesc',
  Appunti: 'sectionAppuntiDesc',
  Report: 'sectionReportDesc',
  Impostazioni: 'sectionImpostazioniDesc',
};

/**
 * La spiegazione di ogni pagina, dietro il "?" accanto al titolo.
 *
 * Il secondo elemento e' la dipendenza: da cosa deve esserci perche' la pagina
 * mostri qualcosa. Gli Appunti non ne hanno, non dipendono da nulla.
 */
const SECTION_HELP_KEYS: Record<Section, [TranslationKey, TranslationKey?]> = {
  Panoramica: ['helpPanoramica', 'helpPanoramicaDep'],
  Movimenti: ['helpMovimenti', 'helpMovimentiDep'],
  Budget: ['helpBudget', 'helpBudgetDep'],
  Obiettivi: ['helpObiettivi', 'helpObiettiviDep'],
  Patrimonio: ['helpPatrimonio', 'helpPatrimonioDep'],
  Debiti: ['helpDebiti', 'helpDebitiDep'],
  Investimenti: ['helpInvestimenti', 'helpInvestimentiDep'],
  Insieme: ['helpInsieme', 'helpInsiemeDep'],
  Appunti: ['helpAppunti'],
  Report: ['helpReport', 'helpReportDep'],
  FIRE: ['fireSectionHelp', 'fireSectionHelpDep'],
  Impostazioni: ['helpImpostazioni', 'helpImpostazioniDep'],
};

export function MoneyDashboard() {
  return (
    <I18nProvider>
      <MoneyDashboardInner />
    </I18nProvider>
  );
}

function MoneyDashboardInner() {
  const { t, lang, setLang, locale, formatEuro, formatCompactEuro, formatDate, monthNames } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState<Section>('Panoramica');
  const [period, setPeriod] = useState<PeriodSelection>({ year: MESE_CORRENTE.anno, month: MESE_CORRENTE.mese, scope: 'month' });
  const selectedYear = period.year;
  const selectedMonth = period.month;
  const [budgetType, setBudgetType] = useState<'Expenses' | 'Income' | 'Savings'>('Expenses');
  const [effectivePreview, setEffectivePreview] = useState<{ occurred: string; type: string; amount: number; origine: string }>(MODULO_VUOTO);
  const overviewYear = period.year;
  const overviewMonth = period.scope === 'year' ? null : period.month;
  const [overviewCompareTo, setOverviewCompareTo] = useState<'none' | 'prior_period' | 'prior_year'>('prior_year');
  const [overviewView, setOverviewView] = useState<'panoramica' | 'analisi'>('panoramica');
  const analysisYear = period.year;
  const [analysisCategoryType, setAnalysisCategoryType] = useState<'Income' | 'Expenses' | 'Savings'>('Expenses');
  const [analysisCategory, setAnalysisCategory] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [summary, setSummary] = useState(fallbackSummary);
  const [summaryLoaded, setSummaryLoaded] = useState(false);
  // La pagina Movimenti si ricarica da sola la sua pagina di dati: da qui le si
  // dice soltanto che qualcosa e' cambiato.
  const [movimentiVersione, setMovimentiVersione] = useState(0);
  const [daDividere, setDaDividere] = useState<Transaction | null>(null);
  // Quanti caricamenti sono in volo: serve a dire che si sta aggiornando senza
  // far sparire quello che c'e' gia' a schermo.
  const [inCorso, setInCorso] = useState(0);
  const [summaryBreakdown, setSummaryBreakdown] = useState<SummaryBreakdown | null>(null);
  const [analysisData, setAnalysisData] = useState<AnalysisData | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [budgetData, setBudgetData] = useState<BudgetData>(BUDGET_VUOTO);
  const [budgetSuggestions, setBudgetSuggestions] = useState<BudgetSuggestion[]>([]);
  const [annualBudgetData, setAnnualBudgetData] = useState<AnnualBudgetData>({ year: 2026, items: [], monthTotals: [], balance: { income: 0, expenses: 0, savings: 0, storedSavings: 0, hasIncomePlan: false } });
  const [budgetDashboardData, setBudgetDashboardData] = useState<BudgetDashboardData | null>(null);
  const [budgetTrendsData, setBudgetTrendsData] = useState<BudgetTrendsData>({ years: [], comparison: [] });
  const [trendYearsAvailable, setTrendYearsAvailable] = useState<number[]>([]);
  // L'utente sceglie quali anni mettere a confronto in Trends. Default: gli
  // ultimi 3 anni disponibili, una volta che la lista arriva dal backend.
  const [trendYears, setTrendYears] = useState<number[]>([]);
  const [budgetView, setBudgetView] = useState<'dashboard' | 'trends' | 'plan'>('dashboard');
  const [budgetLoadFailed, setBudgetLoadFailed] = useState(false);
  const [trendsLoadFailed, setTrendsLoadFailed] = useState(false);
  const [budgetAlerts, setBudgetAlerts] = useState<BudgetAlertData | null>(null);
  const [recurringTransactions, setRecurringTransactions] = useState<RecurringTransactionData[]>([]);
  const [categorizationRules, setCategorizationRules] = useState<CategorizationRuleData[]>([]);
  const [goalsData, setGoalsData] = useState<GoalsData>({ items: [], active: 0, completed: 0, targetTotal: 0, currentTotal: 0, monthlyNeededTotal: 0, plannedSavings: 0, hasPlannedSavings: false, monthlyGap: 0 });
  const [netWorthData, setNetWorthData] = useState<NetWorthData>({ requestedPeriod: '', dataPeriod: null, totals: { bank: 0, asset: 0, liability: 0, financial: 0, liquid: 0, netWorth: 0 }, currencies: [] });
  const [investmentDashboardData, setInvestmentDashboardData] = useState<InvestmentDashboardData>({ snapshot: { period: null, marketValue: 0, investedCapital: 0, gain: 0, returnRate: 0 }, ledger: { marketValue: 0, costBasis: 0, gain: 0, quotedPositions: 0, activePositions: 0 }, positions: [], history: [], contributions: [], rebalance: { total: 0, declaredWeight: 0, warnings: [], rows: [] }, returns: { twr: { value: null, reason: null }, xirr: { value: null, reason: null }, months: 0, since: null, asOf: null }, benchmark: { symbol: null, from: null, months: 0 } });
  const [investmentLedger, setInvestmentLedger] = useState<InvestmentTransaction[]>([]);
    const [investmentAllocationData, setInvestmentAllocationData] = useState<InvestmentAllocationData>({ total: 0, allocations: { instrument: [], sector: [], assetType: [], holdings: [], currency: [] }, coverage: { covered: 0, uncovered: 0, coveredPercent: 0, missing: [], lastFetch: null, sourceErrors: [] } });
  const [notesData, setNotesData] = useState<NoteData[]>([]);
  const [calculationData, setCalculationData] = useState<CalculationData | null>(null);
  const [settingsData, setSettingsData] = useState<SettingsData>(fallbackSettings);
  const [connected, setConnected] = useState(false);
  const [connectionError, setConnectionError] = useState('');
  // Il bottone "Riprova" del banner d'errore della Panoramica incrementa
  // questo contatore: e' una dipendenza dell'effect di caricamento, quindi
  // React abortisce il fetch in corso e ne fa ripartire uno pulito.
  const [panoramicaRetryKey, setPanoramicaRetryKey] = useState(0);
  const [dbSummary, setDbSummary] = useState<{ counts: { transactions: number; goals: number; notes: number; accounts: number; budgets: number; investments: number; instruments: number; categories: number }; isEmpty: boolean } | null>(null);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [profileOpen, setProfileOpen] = useState(false);
  const [newTransactionOpen, setNewTransactionOpen] = useState(false);
  const [movementAccount, setMovementAccount] = useState('');
  const [editingTransaction, setEditingTransaction] = useState<Transaction | null>(null);
  const [duplicatingTransaction, setDuplicatingTransaction] = useState<Transaction | null>(null);
  const [linkLedgerOpen, setLinkLedgerOpen] = useState(false);
  const movementFormRef = useRef<HTMLFormElement>(null);
  const [linkedLedgerRows, setLinkedLedgerRows] = useState<LinkedLedgerDraft[]>([]);
  const [linkedLedgerItems, setLinkedLedgerItems] = useState<LinkedLedgerSummary[]>([]);
  const [linkedBalance, setLinkedBalance] = useState<LedgerGroupBalance | null>(null);
  const [linkPickerOpen, setLinkPickerOpen] = useState(false);
  const [linkPickerLedger, setLinkPickerLedger] = useState<InvestmentTransaction[]>([]);
  const [linkPickerBusy, setLinkPickerBusy] = useState(false);
  const [linkPickerQuery, setLinkPickerQuery] = useState('');
  const [nuovaOpAperta, setNuovaOpAperta] = useState(false);
  const [nuovaOpNome, setNuovaOpNome] = useState('');
  const [nuovaOpTipo, setNuovaOpTipo] = useState<LedgerTypeDaMovimento>('Buy');
  const [nuovaOpImporto, setNuovaOpImporto] = useState('');
  const [nuovaOpQuote, setNuovaOpQuote] = useState('');
  const [linkEditingBusy, setLinkEditingBusy] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferSource, setTransferSource] = useState('');
  const [transferDestination, setTransferDestination] = useState('');
  const [transferAmount, setTransferAmount] = useState('');
  const [transferPrincipal, setTransferPrincipal] = useState('');
  const [transferInterest, setTransferInterest] = useState('0');
  // Un dialog solo per creare e per modificare: i campi sono gli stessi, cambia
  // solo dove finisce il salvataggio. 'new' = creazione, un conto = modifica.
  const [accountDialog, setAccountDialog] = useState<Account | 'new' | null>(null);
  const [valuationForId, setValuationForId] = useState<number | null>(null);
  const [accountGroup, setAccountGroup] = useState<Account['group']>('bank');
  const [accountSaving, setAccountSaving] = useState(false);
  const [accountError, setAccountError] = useState('');
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pdfImporting, setPdfImporting] = useState(false);
  const [importFeedback, setImportFeedback] = useState<{ ok: boolean; message: string } | null>(null);
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [pdfPreviewTransactions, setPdfPreviewTransactions] = useState<PDFTransaction[]>([]);
  const [showPdfPreview, setShowPdfPreview] = useState(false);
  const [settingSaving, setSettingSaving] = useState('');
  const [settingError, setSettingError] = useState('');
  const [auth, setAuth] = useState<{ user: AccountSummary & { sharesTotals: boolean } | null; users: AccountSummary[]; loginRequired: boolean; canManageBackups: boolean } | null>(null);
  // Il selettore serve anche quando nessuno ha una password: senza, con piu'
  // profili non ci sarebbe modo di passare da uno all'altro.
  const [sceltaProfilo, setSceltaProfilo] = useState(false);
  const [saveError, setSaveError] = useState('');
  const configuredApiUrl = process.env.NEXT_PUBLIC_API_URL;
  const apiUrl = configuredApiUrl ?? (typeof window === 'undefined'
    ? 'http://api:8000'
    : '');
  // I nomi degli strumenti da proporre nelle operazioni collegate: quelli
  // configurati e quelli gia' usati nel ledger. Si chiedono a ogni apertura del
  // modulo, cosi' uno strumento appena creato c'e' gia'.
  const [nomiStrumenti, setNomiStrumenti] = useState<string[]>([]);
  useEffect(() => {
    if (!newTransactionOpen) return;
    const controller = new AbortController();
    fetch(`${apiUrl}/api/investments/instruments`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<{ items: Array<{ name: string }> }> : { items: [] })
      .then((data) => setNomiStrumenti(Array.from(new Set([...data.items.map((i) => i.name), ...investmentLedger.map((op) => op.name)]))
        .filter(Boolean).sort((a, b) => a.localeCompare(b))))
      .catch(() => undefined);
    return () => controller.abort();
  }, [newTransactionOpen, apiUrl, investmentLedger]);

  // Di settingsData a loadData serve un solo numero. Tenere nelle dipendenze
  // l'intero oggetto - che loadData stesso riscrive a ogni giro, con una
  // identita' nuova - faceva ripartire il caricamento all'infinito: la pagina
  // non era lenta, non smetteva mai di caricare.
  // L'elenco degli anni per Trends e' ora esplicito (trendYears): niente piu'
  // "fino al 2050" automatico, niente finestra che mostra tutti i precedenti.
  const trendYearsKey = trendYears.join(',');

  // Cosa ricaricare. Senza elenco si ricarica tutto (primo avvio, cambio
  // periodo, ripristino di un backup); una modifica dice invece cosa ha
  // toccato, e il resto della pagina non viene riscaricato per niente.
  const loadData = useCallback(async (signal?: AbortSignal, scope?: LoadScope[]) => {
    // I saldi dei conti seguono il periodo scelto, come la serie del patrimonio:
    // sono lo stesso numero e devono restare tali anche guardando indietro.
    const ultimoGiorno = new Date(selectedYear, selectedMonth, 0).getDate();
    const fineMesePeriodo = `${selectedYear}-${String(selectedMonth).padStart(2, '0')}-${String(ultimoGiorno).padStart(2, '0')}`;
    const serve = (gruppo: LoadScope) => scope === undefined || scope.includes(gruppo);
    if (serve('overview')) setBudgetAlerts(null);
    if (serve('budget')) setBudgetLoadFailed(false);
    setInCorso((quanti) => quanti + 1);
    try {
    // Health summary per primo, separato: se fallisce possiamo ancora mostrare
    // un errore di connessione; se riesce sappiamo subito se il DB è vuoto o
    // pieno e possiamo differenziare "primo avvio" da "problema di rete".
    try {
      if (!serve('ledger')) throw new SkipGroup();
      const healthResponse = await fetch(`${apiUrl}/api/health/summary`, { signal });
      if (healthResponse.ok) {
        const healthData = await healthResponse.json() as { counts: { transactions: number; goals: number; notes: number; accounts: number; budgets: number; investments: number; instruments: number; categories: number }; isEmpty: boolean };
        setDbSummary(healthData);
      }
    } catch (error) {
      // Non bloccare il caricamento: il riepilogo è informativo.
      if (!(error instanceof SkipGroup) && !(error instanceof DOMException && error.name === 'AbortError')) {
        // silenzioso: connectionError verrà mostrato dal fallimento complessivo
      }
    }
    // Helper: fetch un singolo endpoint; se fallisce, logga e ritorna null invece
    // di buttare giù l'intero loadData. Ogni sezione della dashboard può quindi
    // mostrare i dati che ha e segnalare quelli mancanti senza bloccare le altre.
    const fetchOptional = async <T,>(url: string, label: string): Promise<T | null> => {
      try {
        const response = await fetch(url, { signal });
        if (!response.ok) {
          console.warn(`[dashboard] ${label} → ${response.status} (${response.statusText})`);
          return null;
        }
        return await response.json() as T;
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') throw error;
        console.warn(`[dashboard] ${label} errore di rete:`, error);
        return null;
      }
    };

    // Endpoint core (devono riuscire perché l'app sia utile): summary, transactions, accounts.
    // Il riepilogo segue il periodo; l'elenco movimenti e i conti no, e
    // ricaricarli a ogni cambio di mese voleva dire rispedire un megabyte
    // e mezzo di JSON per niente.
    const [summaryData, accountData] = await Promise.all([
      serve('overview') ? fetchOptional<Summary>(`${apiUrl}/api/summary?year=${overviewYear}${overviewMonth !== null ? `&month=${overviewMonth}` : ''}${overviewCompareTo !== 'none' ? `&compare_to=${overviewCompareTo}` : ''}`, 'summary') : null,
      serve('ledger') ? fetchOptional<AccountsResponse>(`${apiUrl}/api/accounts?at=${fineMesePeriodo}`, 'accounts') : null,
    ]);
    if ((serve('overview') && !summaryData) || (serve('ledger') && !accountData)) {
      throw new Error('api');
    }
    if (summaryData) {
      setSummary(summaryData);
      setSummaryLoaded(true);
    }
    if (accountData) setAccounts(accountData.items);

    // Endpoint opzionali: nessuno di questi blocca la UI se manca.
    const [
      importedSettings, importedBudgets, importedCalculations, importedAnnualBudget,
      importedBudgetDashboard, importedBudgetTrends, importedGoals, importedNetWorth,
      importedInvestmentDashboard, importedInvestmentLedger, importedInvestmentAllocation,
      importedNotes,
      importedSummaryBreakdown, importedAnalysisData, importedBudgetSuggestions,
    ] = await Promise.all([
      serve('settings') ? fetchOptional<SettingsData>(`${apiUrl}/api/settings`, 'settings') : null,
      serve('budget') && period.scope === 'month' ? fetchOptional<BudgetData>(`${apiUrl}/api/budgets?year=${selectedYear}&month=${selectedMonth}&budget_type=${budgetType}`, 'budgets') : null,
      serve('budget') && period.scope === 'month' ? fetchOptional<CalculationData>(`${apiUrl}/api/calculations?year=${selectedYear}&month=${selectedMonth}`, 'calculations') : null,
      serve('budget') ? fetchOptional<AnnualBudgetData>(`${apiUrl}/api/budget-annual?year=${selectedYear}&budget_type=${budgetType}`, 'budget-annual') : null,
      serve('budget') ? fetchOptional<BudgetDashboardData>(`${apiUrl}/api/budget-dashboard?year=${selectedYear}${period.scope === 'month' ? `&month=${selectedMonth}` : ''}&budget_type=${budgetType}`, 'budget-dashboard') : null,
      serve('trends') ? fetchOptional<BudgetTrendsData>(`${apiUrl}/api/budget-trends?years=${trendYearsKey || selectedYear}&budget_type=${budgetType}`, 'budget-trends') : null,
      serve('goals') ? fetchOptional<GoalsData>(`${apiUrl}/api/goals`, 'goals') : null,
      serve('networth') ? fetchOptional<NetWorthData>(`${apiUrl}/api/net-worth?year=${selectedYear}&month=${selectedMonth}&months=12`, 'net-worth') : null,
      serve('investments') ? fetchOptional<InvestmentDashboardData>(`${apiUrl}/api/investments/dashboard`, 'investment-dashboard') : null,
      serve('investments') ? fetchOptional<{ items: InvestmentTransaction[] }>(`${apiUrl}/api/investments/ledger`, 'investment-ledger') : null,
      serve('investments') ? fetchOptional<InvestmentAllocationData>(`${apiUrl}/api/investments/allocation`, 'investment-allocation') : null,
      serve('notes') ? fetchOptional<{ items: NoteData[] }>(`${apiUrl}/api/notes`, 'notes') : null,
      serve('overview') ? fetchOptional<SummaryBreakdown>(`${apiUrl}/api/summary-breakdown?year=${overviewYear}${overviewMonth !== null ? `&month=${overviewMonth}` : ''}`, 'summary-breakdown') : null,
      serve('analysis') ? fetchOptional<AnalysisData>(`${apiUrl}/api/analysis?year=${analysisYear}&category_type=${analysisCategoryType}${analysisCategory ? `&category=${encodeURIComponent(analysisCategory)}` : ''}`, 'analysis') : null,
      serve('budget') && period.scope === 'month' ? fetchOptional<BudgetSuggestionsData>(`${apiUrl}/api/budget-suggestions?year=${selectedYear}&month=${selectedMonth}&budget_type=${budgetType}`, 'budget-suggestions') : null,
    ]);
    const budgetFailed = serve('budget') && [importedAnnualBudget, importedBudgetDashboard, ...(period.scope === 'month' ? [importedBudgets, importedCalculations] : [])].some((value) => value === null);
    if (serve('budget')) {
      setBudgetLoadFailed(budgetFailed);
      setBudgetSuggestions(importedBudgetSuggestions?.items ?? []);
    }
    if (importedSettings) setSettingsData(importedSettings);
    if (!budgetFailed && importedBudgets) setBudgetData(importedBudgets);
    if (!budgetFailed && importedCalculations) setCalculationData(importedCalculations);
    if (!budgetFailed && importedAnnualBudget) setAnnualBudgetData(importedAnnualBudget);
    if (serve('budget')) setBudgetDashboardData(!budgetFailed && importedBudgetDashboard ? importedBudgetDashboard : null);
    if (serve('trends')) setTrendsLoadFailed(importedBudgetTrends === null);
    if (importedBudgetTrends) setBudgetTrendsData(importedBudgetTrends);
    if (importedGoals) setGoalsData(importedGoals);
    if (importedNetWorth) setNetWorthData(importedNetWorth);
    if (importedInvestmentDashboard) setInvestmentDashboardData(importedInvestmentDashboard);
    if (importedInvestmentLedger) setInvestmentLedger(importedInvestmentLedger.items);
    if (importedInvestmentAllocation) setInvestmentAllocationData(importedInvestmentAllocation);
    if (importedNotes) setNotesData(importedNotes.items);
    if (serve('overview')) {
      setSummaryBreakdown(importedSummaryBreakdown);
      const expenses = importedSummaryBreakdown?.sections.expenses;
      setBudgetAlerts(expenses ? {
        alert_count: expenses.categories.filter((category) => category.budget > 0 && category.tracked > category.budget).length,
      } : null);
    }
    if (serve('analysis')) setAnalysisData(importedAnalysisData);
    setConnected(true);
    setConnectionError('');

    // Ricorrenze: caricamento non bloccante.
    if (serve('rules')) try {
      const recurringResponse = await fetch(`${apiUrl}/api/recurring-transactions`, { signal });
      // La rotta ha restituito nel tempo sia una lista sia { items }.
      // Accettarle entrambe evita che una differenza di forma faccia finire
      // undefined nello stato e mandi in errore l'intera pagina.
      const elenco = <T,>(payload: unknown): T[] =>
        Array.isArray(payload) ? payload as T[] : ((payload as { items?: T[] } | null)?.items ?? []);
      if (recurringResponse.ok) setRecurringTransactions(elenco<RecurringTransactionData>(await recurringResponse.json()));
    } catch {
      // Ignora: l'app funziona anche senza i pannelli ricorrenze/regole.
    }
    // Regole di categorizzazione: stesso trattamento delle ricorrenze. Lo
    // scope e' a parte da 'rules', che sono le ricorrenze: si caricano quando
    // serve l'uno o l'altro, e aprire Movimenti non deve pesare per entrambi.
    if (serve('categoryRules')) try {
      const rulesResponse = await fetch(`${apiUrl}/api/categorization-rules`, { signal });
      if (rulesResponse.ok) setCategorizationRules((await rulesResponse.json() as CategorizationRulesData).items ?? []);
    } catch {
      // Ignora: le regole non servono al resto della pagina.
    }
    } finally {
      setInCorso((quanti) => Math.max(0, quanti - 1));
    }
  }, [apiUrl, selectedMonth, selectedYear, period.scope, budgetType, overviewYear, overviewMonth, overviewCompareTo, analysisYear, analysisCategoryType, analysisCategory, trendYearsKey]);

  // `loadData` cambia identita' a ogni cambio di periodo: il caricamento dei
  // dati fissi deve poterlo chiamare senza per questo ripartire.
  const loadDataRef = useRef(loadData);
  loadDataRef.current = loadData;
  // Anche `t` passa da un ref. La lingua viene rilevata dopo il primo render,
  // e `t` cambia identita' con lei: averlo fra le dipendenze faceva ripartire
  // tutti i caricamenti una seconda volta, a ogni apertura della pagina.
  const tRef = useRef(t);
  tRef.current = t;

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void loadDataRef.current(controller.signal, SCOPE_FISSI).catch(() => undefined);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [apiUrl]);

  // Un effetto per ogni selettore, cosi' cambiare il mese della Panoramica non
  // ricarica il budget e viceversa. Passano tutti dal ref: `loadData` cambia
  // identita' quando cambia un qualunque periodo, e metterlo fra le dipendenze
  // rimetterebbe insieme quello che qui si sta separando.
  const caricaAmbito = useCallback((scope: LoadScope[]) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      loadDataRef.current(controller.signal, scope).catch((error: unknown) => {
        // Un caricamento annullato (cambio periodo mentre e' in corso) non e'
        // un errore di rete: segnalarlo mostrerebbe un falso "app offline".
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setConnected(false);
        setConnectionError(error instanceof Error ? error.message : tRef.current('networkErrorGeneric'));
      });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, []);

  // Anni proposti per il confronto Trends: solo anni con transazioni reali o
  // piani di budget. Vengono caricati una volta; la selezione dell'utente vive
  // in `trendYears`.
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/budget-trends/available-years`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<{ years: number[] }> : Promise.reject(new Error('trend-years')))
      .then((data) => {
        const anni = data.years ?? [];
        setTrendYearsAvailable(anni);
        setTrendYears((current) => {
          if (current.length) {
            // Tieni solo anni che esistono ancora nella lista aggiornata.
            const validi = current.filter((y) => anni.includes(y));
            if (validi.length) return validi;
          }
          // Prima volta: ultime 3 selezionate, in modo che il grafico non parta
          // vuoto quando l'utente apre Trends.
          const defaultYears = anni.slice(-3);
          return defaultYears.length ? defaultYears : anni.slice();
        });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setTrendYearsAvailable([]);
      });
    return () => controller.abort();
  }, [apiUrl]);
  // Il periodo e' condiviso, i caricamenti no: reagisce soltanto la pagina
  // aperta, cosi' cambiare mese non risveglia tre sezioni invisibili.
  useEffect(() => activeSection === 'Budget' && budgetView !== 'trends' ? caricaAmbito(['budget']) : undefined,
    [activeSection, budgetView, budgetType, caricaAmbito, selectedYear, selectedMonth, period.scope]);
  // Gli anni del confronto sono un selettore a se': cambiarli non deve
  // riscaricare budget, suggerimenti e calcoli del mese.
  useEffect(() => activeSection === 'Budget' && budgetView === 'trends' ? caricaAmbito(['trends']) : undefined,
    [activeSection, budgetView, budgetType, caricaAmbito, trendYearsKey]);
  // Cambio di tipo di budget: i dati in stato appartengono ancora al vecchio
  // tipo, quindi azzerarli subito per non mostrare le categorie delle Spese
  // mentre il fetch di quelle delle Entrate (o Risparmi) e' in volo.
  useEffect(() => {
    setBudgetData(BUDGET_VUOTO);
    setBudgetDashboardData(null);
    setBudgetTrendsData({ years: [], comparison: [] });
    setAnnualBudgetData({ year: selectedYear, items: [], monthTotals: [], balance: { income: 0, expenses: 0, savings: 0, storedSavings: 0, hasIncomePlan: false } });
    setBudgetSuggestions([]);
  }, [budgetType, selectedYear]);
  useEffect(() => activeSection === 'Patrimonio' ? caricaAmbito(['networth', 'ledger']) : undefined,
    [activeSection, caricaAmbito, selectedYear, selectedMonth]);
  useEffect(() => activeSection === 'Investimenti' ? caricaAmbito(['investments']) : undefined,
    [activeSection, caricaAmbito]);
  // Le regole vivono in fondo alla pagina Movimenti, quindi si caricano
  // aprendo Movimenti. Non entrano in SCOPE_FISSI: chi non le guarda non le
  // scarica.
  useEffect(() => activeSection === 'Movimenti' ? caricaAmbito(['categoryRules']) : undefined,
    [activeSection, caricaAmbito]);
  // La Panoramica ha il suo, e anche il confronto con il periodo precedente.
  useEffect(() => activeSection === 'Panoramica' && overviewView === 'panoramica' ? caricaAmbito(['overview']) : undefined,
    [activeSection, overviewView, caricaAmbito, overviewYear, overviewMonth, overviewCompareTo, panoramicaRetryKey]);
  // L'analisi pure.
  useEffect(() => activeSection === 'Panoramica' && overviewView === 'analisi' ? caricaAmbito(['analysis']) : undefined,
    [activeSection, overviewView, caricaAmbito, analysisYear, analysisCategoryType, analysisCategory]);

  // Il salvataggio deve aspettare il ripristino: partendo subito scriverebbe i
  // valori di partenza sopra quelli appena letti, e non ricorderebbe niente.
  const [vistaRipristinata, setVistaRipristinata] = useState(false);

  // Ripristino dopo il primo render, non durante: leggere localStorage mentre
  // il server ha gia' disegnato la pagina farebbe litigare le due versioni.
  useEffect(() => {
    try {
      const salvato = JSON.parse(window.localStorage.getItem(CHIAVE_VISTA) ?? 'null') as
        { section?: string; period?: Partial<PeriodSelection>; year?: number; month?: number;
          overviewYear?: number; overviewMonth?: number | null; overviewCompareTo?: 'none' | 'prior_period' | 'prior_year';
          trendYears?: number[]; budgetView?: 'dashboard' | 'trends' | 'plan' } | null;
      if (!salvato) return;
      if (salvato.section && salvato.section in SECTION_LABEL_KEYS) setActiveSection(salvato.section as Section);
      // Il vecchio formato aveva due periodi: durante la migrazione vince
      // quello della pagina che l'utente aveva lasciato aperta.
      const dallaPanoramica = salvato.section === 'Panoramica';
      const year = salvato.period?.year ?? (dallaPanoramica ? salvato.overviewYear : salvato.year);
      const month = salvato.period?.month ?? (dallaPanoramica ? salvato.overviewMonth : salvato.month);
      const restoredMonth = month === null ? MESE_CORRENTE.mese : month;
      if (Number.isInteger(year) && Number.isInteger(restoredMonth) && Number(restoredMonth) >= 1 && Number(restoredMonth) <= 12) {
        setPeriod({ year: Number(year), month: Number(restoredMonth), scope: salvato.period?.scope === 'year' || (dallaPanoramica && salvato.overviewMonth === null) ? 'year' : 'month' });
      }
      if (salvato.overviewCompareTo) setOverviewCompareTo(salvato.overviewCompareTo);
      if (Array.isArray(salvato.trendYears)) setTrendYears(salvato.trendYears.filter(Number.isInteger).slice(0, MAX_ANNI_CONFRONTO));
      if (salvato.budgetView) setBudgetView(salvato.budgetView);
    } catch {
      // Un localStorage non disponibile o con dentro spazzatura non deve
      // impedire di aprire l'app: si riparte dai valori di partenza.
    } finally {
      setVistaRipristinata(true);
    }
  }, []);

  useEffect(() => {
    if (!vistaRipristinata) return;
    try {
      window.localStorage.setItem(CHIAVE_VISTA, JSON.stringify({
        section: activeSection, period, overviewCompareTo, trendYears, budgetView,
      }));
    } catch {
      // Salvare dove si era rimasti e' un di piu': se non si puo', pazienza.
    }
  }, [vistaRipristinata, activeSection, period, overviewCompareTo, trendYears, budgetView]);

  useEffect(() => {
    let tema = settingsData.settings.header_color;
    if (!tema) {
      try { tema = window.localStorage.getItem(CHIAVE_TEMA) ?? ''; } catch { tema = ''; }
    }
    if (tema) applyTheme(tema);
  }, [settingsData.settings.header_color]);

  async function handleSaveTransaction(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    await salvaMovimento(new FormData(event.currentTarget), true);
  }

  /* Salva il movimento del modulo. `chiudi` falso lo lascia aperto: serve a
     collegare il ledger subito dopo aver cambiato il tipo in Investimento,
     senza chiudere e riaprire. Restituisce se il salvataggio e' riuscito. */
  async function salvaMovimento(form: FormData, chiudi: boolean): Promise<boolean> {
    setSaving(true);
    setSaveError('');
    const txType = String(form.get('transaction_type') ?? 'Expenses');
    const txAmount = Math.abs(Number(form.get('amount') || 0));
    const debtPrincipal = Number(form.get('debt_principal') || 0);
    const debtInterest = Number(form.get('debt_interest') || 0);
    if (txType === 'Debt' && Math.abs(debtPrincipal + debtInterest - txAmount) > 0.005) {
      setSaving(false); setSaveError(t('debtSplitMustMatch')); return false;
    }
    try {
      const basePayload = transactionPayload(form);
      // Solo in creazione: il tick puo' generare N righe ledger in modo atomico.
      // In modifica si usano gli endpoint dedicati di gestione link.
      // Anche il tipo: spuntata la casella su un Investimento e poi cambiato
      // tipo, le righe restavano nello stato e il salvataggio veniva rifiutato.
      const useLinkedEndpoint = linkLedgerOpen && !editingTransaction && txType === 'Investment' && linkedLedgerRows.length > 0;
      const endpoint = useLinkedEndpoint
        ? `${apiUrl}/api/transactions/with-ledger`
        : `${apiUrl}/api/transactions${editingTransaction ? `/${editingTransaction.id}` : ''}`;
      const method = editingTransaction && !useLinkedEndpoint ? 'PATCH' : 'POST';
      const payload = useLinkedEndpoint
        ? {
            ...basePayload,
            linked_ledger: linkedLedgerRows.map((row) => ({
              name: row.name.trim(),
              transaction_type: row.transactionType,
              amount: SOLO_CONTANTE_DA_MOVIMENTO.includes(row.transactionType) ? Number(row.amount || 0) : undefined,
              units: Number(row.units || 0),
              price: Number(row.price || 0),
              currency: 'EUR',
              fee: Number(row.fee || 0),
              notes: row.notes || null,
            })),
          }
        : basePayload;
      const response = await fetch(endpoint, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        // L'API restituisce {detail: ...} per i 422, e detail ha tre forme:
        // una stringa, l'elenco di errori di validazione di FastAPI, o un
        // oggetto con un codice nostro. Erano gestite solo le prime due, e con
        // la terza il motivo vero spariva.
        let detail = '';
        try {
          const errBody = await response.json() as { detail?: string | { msg?: string }[] | { code?: string; fields?: string[] } };
          detail = typeof errBody.detail === 'string' ? errBody.detail
            : Array.isArray(errBody.detail) ? errBody.detail.map((item) => item.msg).join('; ')
            : errBody.detail?.code === 'movementIncomplete' ? campiMancanti(errBody.detail.fields, t)
            : errBody.detail?.code && Object.hasOwn(translations.it, errBody.detail.code) ? t(errBody.detail.code as TranslationKey)
            : errBody.detail?.code ?? '';
        } catch { /* ignore */ }
        if (detail) setSaveError(detail);
        // `saveError` qui e' il valore del render precedente: leggerlo per
        // decidere se sovrascrivere faceva sparire il messaggio appena
        // impostato, e al suo posto compariva sempre quello generico.
        throw new SaveFailed(Boolean(detail));
      }
      if (chiudi) {
        // Chiudo prima di ricaricare: il movimento e' gia' salvato, e aspettare
        // il giro completo delle chiamate lasciava il dialogo fermo per secondi.
        setNewTransactionOpen(false);
        setEditingTransaction(null);
        setDuplicatingTransaction(null);
        setLinkLedgerOpen(false);
        setLinkedLedgerRows([]);
      }
      await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
      setMovimentiVersione((versione) => versione + 1);
      return true;
    } catch (error) {
      // Solo se non abbiamo gia' un motivo preciso da mostrare.
      if (!(error instanceof SaveFailed && error.spiegato)) {
        setSaveError(t('cannotSaveGeneric'));
      }
      return false;
    } finally {
      setSaving(false);
    }
  }

  /* Il collegamento lavora sul movimento salvato, e il server lo accetta solo
     se e' salvato come Investimento. Chi ha appena cambiato il tipo nel modulo
     non deve salvare, chiudere e riaprire: lo salviamo qui, con tutto quello
     che ha scritto, e il collegamento parte subito dopo. */
  async function investimentoSalvato(): Promise<string | null> {
    if (!editingTransaction) return null;
    if (editingTransaction.transactionType === 'Investment') return editingTransaction.id;
    // Il modulo non passa dal submit: senza questo i campi obbligatori (il conto
    // broker di destinazione) arrivavano vuoti e il server rispondeva con un codice.
    if (!movementFormRef.current?.reportValidity() || !await salvaMovimento(new FormData(movementFormRef.current), false)) return null;
    setEditingTransaction((corrente) => corrente && { ...corrente, transactionType: 'Investment' });
    return editingTransaction.id;
  }

  // --- Gestione link Transazione <-> Ledger (in modifica) ---
  const loadLinkedLedgerItems = useCallback(async (transactionId: string): Promise<LinkedLedgerSummary[]> => {
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${transactionId}/ledger-links`);
      if (!response.ok) return [];
      const data = await response.json() as { items: LinkedLedgerSummary[]; balance: LedgerGroupBalance };
      setLinkedBalance(data.balance ?? null);
      return data.items ?? [];
    } catch {
      setLinkedBalance(null);
      return [];
    }
  }, [apiUrl]);

  async function unlinkLedgerRow(linkId: number) {
    if (!editingTransaction) return;
    if (!window.confirm(t('confirmUnlinkOperation'))) return;
    setLinkEditingBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${editingTransaction.id}/ledger-links/${linkId}`, { method: 'DELETE' });
      if (!response.ok) throw new Error('unlink');
      setLinkedLedgerItems(await loadLinkedLedgerItems(editingTransaction.id));
      await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
      setMovimentiVersione((versione) => versione + 1);
    } catch {
      setSaveError(t('cannotUnlinkGeneric'));
    } finally {
      setLinkEditingBusy(false);
    }
  }

  async function openLinkPicker() {
    setLinkPickerOpen(true);
    setLinkPickerQuery('');
    setLinkPickerBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/investments/ledger`);
      if (response.ok) {
        const data = await response.json() as { items: InvestmentTransaction[] };
        // Le operazioni gia' agganciate non si offrono: sceglierne una qui
        // vorrebbe dire attribuirla due volte, e l'elenco e' lungo abbastanza
        // senza le righe che non si devono toccare.
        setLinkPickerLedger((data.items ?? []).filter((item) => !item.linked));
      } else {
        setLinkPickerLedger([]);
      }
    } catch {
      setLinkPickerLedger([]);
    } finally {
      setLinkPickerBusy(false);
    }
  }

  /* Crea un'operazione del ledger e la aggancia subito al movimento aperto.
     Serve quando il bonifico e le operazioni non tornano: la differenza e' una
     compravendita che nel ledger non e' mai stata scritta, e si chiude qui
     invece che andandola a cercare in un'altra pagina. */
  // Un dividendo, una commissione, un versamento non hanno quote: il campo
  // sparisce dal modulo e non entra nel salvataggio.
  const contanteNuovaOp = SOLO_CONTANTE_DA_MOVIMENTO.includes(nuovaOpTipo);

  async function creaEcollegaOperazione() {
    const id = await investimentoSalvato();
    if (!id) return;
    setLinkEditingBusy(true);
    try {
      const creata = await fetch(`${apiUrl}/api/investments/ledger`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          occurred_on: formTransaction?.occurredOn ?? new Date().toISOString().slice(0, 10),
          name: nuovaOpNome.trim(), transaction_type: nuovaOpTipo,
          amount: Number(nuovaOpImporto),
          // Le quote scritte prima di cambiare tipo non si portano dietro: un
          // dividendo con "10 quote" rimaste nel campo diventerebbe un acquisto
          // travestito, con tanto di prezzo calcolato.
          units: contanteNuovaOp ? 0 : Number(nuovaOpQuote) || 0,
          price: !contanteNuovaOp && Number(nuovaOpQuote) ? Number(nuovaOpImporto) / Number(nuovaOpQuote) : 0,
        }),
      });
      if (!creata.ok) throw new Error('ledger');
      const operazione = await creata.json() as { id: number };
      const collegata = await fetch(`${apiUrl}/api/transactions/${id}/ledger-links`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ledger_id: operazione.id }),
      });
      if (!collegata.ok) throw new Error('link');
      setNuovaOpAperta(false); setNuovaOpNome(''); setNuovaOpImporto(''); setNuovaOpQuote('');
      setLinkedLedgerItems(await loadLinkedLedgerItems(id));
      await loadData(undefined, ['investments']);
    } catch {
      setSaveError(t('cannotSaveOperation'));
    } finally {
      setLinkEditingBusy(false);
    }
  }

  async function linkExistingLedger(ledgerId: number) {
    const id = await investimentoSalvato();
    if (!id) return;
    setLinkEditingBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${id}/ledger-links`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ledger_id: ledgerId }),
      });
      if (!response.ok) {
        let detail = '';
        try {
          const errBody = await response.json() as { detail?: string };
          detail = errBody.detail ?? '';
        } catch { /* ignore */ }
        setSaveError(detail || t('cannotLinkGeneric'));
        throw new Error('link');
      }
      setLinkedLedgerItems(await loadLinkedLedgerItems(id));
      setLinkPickerOpen(false);
      await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
      setMovimentiVersione((versione) => versione + 1);
    } catch (error) {
      if (error instanceof Error && error.message === 'link' && !saveError) {
        setSaveError(t('cannotLinkGeneric'));
      }
    } finally {
      setLinkEditingBusy(false);
    }
  }

  const handleDeleteTransaction = useCallback(async (transaction: Transaction) => {
    if (!window.confirm(tRef.current(transaction.liabilitySplit ? 'confirmDeleteDebtPayment' : 'confirmDeleteMovement', { name: transaction.description }))) return;
    const response = await fetch(`${apiUrl}/api/transactions/${transaction.id}`, { method: 'DELETE' });
    if (!response.ok) return;
    await loadDataRef.current(undefined, ['ledger', 'overview', 'budget', 'goals']).catch(() => undefined);
    setMovimentiVersione((versione) => versione + 1);
  }, [apiUrl]);

  async function handleCreateTransfer(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaveError('');
    const form = new FormData(event.currentTarget);
    const source = String(form.get('source_account'));
    const destination = String(form.get('destination_account'));
    const sourceIsDebt = accounts.find((account) => account.name === source)?.group === 'liability';
    const destinationIsDebt = accounts.find((account) => account.name === destination)?.group === 'liability';
    const debtMode = sourceIsDebt || destinationIsDebt;
    try {
      const response = await fetch(`${apiUrl}${debtMode ? '/api/liabilities/transfers' : '/api/transfers'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(debtMode ? {
          occurred_on: form.get('occurred_on'), source_account: source, destination_account: destination,
          principal_amount: Number(sourceIsDebt ? form.get('amount') : form.get('principal_amount')),
          interest_amount: Number(sourceIsDebt ? 0 : form.get('interest_amount')),
          details: form.get('details') || null,
        } : {
          occurred_on: form.get('occurred_on'), amount: Number(form.get('amount')),
          source_account: source, destination_account: destination, details: form.get('details') || null,
        }),
      });
      if (!response.ok) throw new Error('transfer');
      await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
      setMovimentiVersione((versione) => versione + 1);
      setTransferOpen(false);
    } catch {
      setSaveError(t('cannotRegisterTransfer'));
    } finally {
      setSaving(false);
    }
  }

  async function handleBudgetUpdate(id: number, payload: { category?: string; amount?: number }) {
    const response = await fetch(`${apiUrl}/api/budgets/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('budget-update');
    await loadData(undefined, ['budget', 'settings']);
  }

  async function handleBudgetCreate(category: string, amount: number) {
    const response = await fetch(`${apiUrl}/api/budgets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(budgetCreatePayload(selectedYear, selectedMonth, budgetType, category, amount)),
    });
    if (!response.ok) throw new Error('budget-create');
    await loadData(undefined, ['budget', 'settings']);
  }

  // Il gruppo appartiene alla categoria, non al mese: il backend lo scrive su
  // tutti i periodi in una volta.
  async function handleCategoryGroupChange(category: string, categoryGroup: string) {
    const response = await fetch(`${apiUrl}/api/category-groups`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category, category_group: categoryGroup || null }),
    });
    if (!response.ok) throw new Error('category-group');
    await loadData(undefined, ['budget']);
  }

  async function handleBudgetDelete(id: number) {
    if (!window.confirm(t('confirmDeleteBudgetCategory'))) return;
    const response = await fetch(`${apiUrl}/api/budgets/${id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('budget-delete');
    await loadData(undefined, ['budget', 'settings']);
  }

  async function handleBudgetCopy(mode: 'month' | 'year') {
    if (!window.confirm(t('confirmReplaceBudget', { period: mode === 'month' ? t('monthWord') : t('yearWord') }))) return;
    const source = new Date(selectedYear, selectedMonth - 1, 1);
    if (mode === 'month') source.setMonth(source.getMonth() - 1);
    else source.setFullYear(source.getFullYear() - 1);
    const anni = (settingsData?.budgetYearsByType?.[budgetType] ?? []).map(Number).filter(Number.isFinite);
    if (anni.length && !anni.includes(source.getFullYear())) return;
    const response = await fetch(`${apiUrl}/api/budgets/copy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source_year: source.getFullYear(),
        source_month: source.getMonth() + 1,
        target_year: selectedYear,
        target_month: selectedMonth,
        budget_type: budgetType,
      }),
    });
    if (!response.ok) throw new Error('budget-copy');
    await loadData(undefined, ['budget', 'settings']);
  }

  async function handleAnnualBudgetApply(category: string, months: number[], amount: number, categoryGroup?: string | null) {
    const response = await fetch(`${apiUrl}/api/budget-bulk`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year: selectedYear, budget_type: budgetType, category, category_group: categoryGroup ?? null, months, amount }),
    });
    if (!response.ok) throw new Error('budget-bulk');
    await loadData(undefined, ['budget', 'settings']).catch(() => undefined);
  }

  async function handleGoalSave(goalId: number | null, payload: Record<string, string | number | null>) {
    const response = await fetch(`${apiUrl}/api/goals${goalId ? `/${goalId}` : ''}`, {
      method: goalId ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('goal-save');
    await loadData(undefined, ['goals']).catch(() => undefined);
  }

  async function handleGoalDelete(goal: GoalData) {
    if (!window.confirm(t('confirmDeleteGoal', { name: goal.name }))) return;
    const response = await fetch(`${apiUrl}/api/goals/${goal.id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('goal-delete');
    await loadData(undefined, ['goals']);
  }

  async function handleInvestmentSave(transactionId: number | null, payload: Record<string, string | number | boolean>) {
    const { force_duplicate: forceDuplicate, ...body } = payload;
    const response = await fetch(`${apiUrl}/api/investments/ledger${transactionId ? `/${transactionId}` : ''}${!transactionId && forceDuplicate ? '?force=true' : ''}`, {
      method: transactionId ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => null) as { detail?: string | { code?: string; duplicate?: { occurredOn?: string } } } | null;
      if (response.status === 409 && typeof result?.detail === 'object' && result.detail.code === 'ledgerDuplicate') {
        throw new Error(t('ledgerDuplicateFound', { date: result.detail.duplicate?.occurredOn ?? '—' }));
      }
      // Un codice del server che ha il suo testo si mostra com'e': un rapporto
      // di split fuori scala deve dire cosa non andava, non "salvataggio non
      // riuscito".
      if (typeof result?.detail === 'object' && result.detail?.code && Object.hasOwn(translations.it, result.detail.code)) {
        throw new Error(t(result.detail.code as TranslationKey));
      }
      throw new Error('investment-save');
    }
    // Il ricaricamento puo' essere annullato da un altro fetch in corso: il
    // salvataggio e' comunque riuscito, non va segnalato come errore.
    await loadData(undefined, ['investments', 'overview']).catch(() => undefined);
  }

  async function handleInvestmentDelete(transaction: InvestmentTransaction) {
    if (!window.confirm(t('confirmDeleteOperation', { name: transaction.name }))) return;
    const response = await fetch(`${apiUrl}/api/investments/ledger/${transaction.id}`, { method: 'DELETE' });
    if (response.ok) await loadData(undefined, ['investments', 'overview']);
  }

  async function handleInstrumentSave(instrumentId: number, payload: Record<string, string | number | null>) {
    const response = await fetch(`${apiUrl}/api/investments/instruments/${instrumentId}/classification`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('instrument-save');
    await loadData(undefined, ['investments']).catch(() => undefined);
  }

  async function handleMarketRefresh(): Promise<{ updated: number; errors: Array<{ code?: string; error?: string }> }> {
    // Aggiorna le quotazioni degli strumenti che hanno un ticker, cambi inclusi.
    const response = await fetch(`${apiUrl}/api/investments/refresh-quotes`, { method: 'POST' });
    if (!response.ok) throw new Error('market-refresh');
    const result = await response.json() as { updated: unknown[]; errors: Array<{ code?: string; error?: string }> };
    await loadData(undefined, ['investments', 'overview']);
    return { updated: result.updated?.length ?? 0, errors: result.errors ?? [] };
  }

  async function handleNoteSave(noteId: number | null, payload: Record<string, string | null>) {
    const response = await fetch(`${apiUrl}/api/notes${noteId ? `/${noteId}` : ''}`, {
      method: noteId ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('note-save');
    await loadData(undefined, ['notes']).catch(() => undefined);
  }

  async function handleNoteDelete(note: NoteData) {
    if (!window.confirm(t('confirmDeleteNote', { name: note.title }))) return;
    const response = await fetch(`${apiUrl}/api/notes/${note.id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('note-delete');
    await loadData(undefined, ['notes']);
  }

  function openNewAccount(group: Account['group'] = 'bank') {
    setAccountError('');
    setAccountGroup(group);
    setAccountDialog('new');
  }

  function openEditAccount(account: Account) {
    setAccountError('');
    setAccountGroup(account.group);
    setAccountDialog(account);
  }

  async function handleValuationSave(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    // Il conto si rilegge da `accounts` a ogni uso: la copia catturata quando il
    // dialog si e' aperto avrebbe la lista di valutazioni di prima del salvataggio.
    const conto = accounts.find((c) => c.id === valuationForId);
    if (!conto) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setAccountSaving(true); setAccountError('');
    try {
      const response = await fetch(`${apiUrl}/api/accounts/${conto.id}/valuations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          observed_on: String(data.get('observed_on') || ''),
          value: Number(data.get('value') || 0),
          notes: String(data.get('notes') || '') || null,
        }),
      });
      if (!response.ok) throw new Error('valuation-save');
      form.reset();
      await loadData(undefined, ['ledger', 'overview', 'settings']);
    } catch (error) {
      setAccountError(error instanceof Error ? error.message : 'valuation-save');
    } finally { setAccountSaving(false); }
  }

  async function handleValuationDelete(valuationId: number) {
    const response = await fetch(`${apiUrl}/api/accounts/${valuationForId}/valuations/${valuationId}`, { method: 'DELETE' });
    if (response.ok) await loadData(undefined, ['ledger', 'overview', 'settings']);
  }

  async function handleAccountSave(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const modifica = accountDialog !== 'new' ? accountDialog : null;
    const payload = accountPayload(data, modifica !== null);
    if (!payload.name || !payload.source_group) return;
    setAccountSaving(true);
    setAccountError('');
    try {
      const response = await fetch(
        modifica ? `${apiUrl}/api/accounts/${modifica.id}` : `${apiUrl}/api/accounts`,
        {
          method: modifica ? 'PATCH' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      if (!response.ok) {
        const detail = await response.json().catch(() => null) as { detail?: string } | null;
        throw new Error(detail?.detail || 'account-save');
      }
      form.reset();
      setAccountDialog(null);
      await loadData(undefined, ['ledger', 'overview', 'settings']);
      setMovimentiVersione((versione) => versione + 1);
    } catch (error) {
      setAccountError(error instanceof Error ? error.message : 'account-save');
    } finally {
      setAccountSaving(false);
    }
  }

  async function handleAccountDelete(account: Account) {
    if (!window.confirm(t(account.group === 'liability' ? 'debtDeleteAccountPrompt' : 'confirmDeleteAccount', { name: account.name }))) return;
    try {
      const query = account.group === 'liability' ? `?confirm_liability_account=${encodeURIComponent(account.name)}` : '';
      const response = await fetch(`${apiUrl}/api/accounts/${account.id}${query}`, { method: 'DELETE' });
      if (!response.ok) {
        const result = await response.json().catch(() => null) as { detail?: string } | null;
        setConnectionError(t(result?.detail === 'debtDeleteBackupFailed' ? 'debtDeleteBackupFailed' : 'debtDeleteFailed'));
        return;
      }
      await loadData(undefined, ['ledger', 'overview', 'settings', 'networth']);
      setMovimentiVersione((versione) => versione + 1);
    } catch { setConnectionError(t('debtDeleteFailed')); }
  }

  // Cambiando persona i dati della precedente devono sparire subito: restare a
  // schermo mentre arrivano i nuovi significherebbe mostrare a qualcuno numeri
  // che non sono suoi.
  function svuotaDati() {
    setSummary(fallbackSummary);
    setSummaryLoaded(false);
    setSettingsData(fallbackSettings);
    setConnected(false);
  }

  const loadNotifications = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/api/notifications`);
      if (response.ok) setNotifications((await response.json() as { items: Notification[] }).items ?? []);
    } catch {
      // Gli avvisi sono un di piu': se non arrivano, l'app resta usabile.
    }
  }, [apiUrl]);

  useEffect(() => { void loadNotifications(); }, [loadNotifications]);

  async function dismissNotification(key: string) {
    await fetch(`${apiUrl}/api/notifications/dismiss`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }),
    }).catch(() => undefined);
    await loadNotifications();
  }

  async function dismissAllNotifications() {
    await fetch(`${apiUrl}/api/notifications/dismiss-all`, { method: 'POST' }).catch(() => undefined);
    await loadNotifications();
  }

  const loadAuth = useCallback(async () => {
    try {
      const response = await fetch(`${apiUrl}/api/auth/me`);
      if (response.ok) setAuth(await response.json());
    } catch {
      // Un problema qui non deve impedire di usare l'app: si resta come prima.
    }
  }, [apiUrl]);

  useEffect(() => { void loadAuth(); }, [loadAuth]);

  async function handleLogin(username: string, password?: string) {
    const response = await fetch(`${apiUrl}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password: password ?? null }),
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'loginTooManyAttempts' : 'loginFailed');
    setSceltaProfilo(false);
    // Prima si svuota, poi si carica: altrimenti per qualche secondo la nuova
    // persona vedrebbe a schermo i numeri di quella precedente.
    svuotaDati();
    setNotifications([]);
    await loadAuth();
    await loadData();
    setMovimentiVersione((versione) => versione + 1);
    // Gli avvisi vanno richiesti di nuovo: la prima richiesta e' partita mentre
    // eravamo sulla schermata di accesso ed e' stata respinta, e sono di chi
    // e' entrato adesso, non di chi c'era prima.
    await loadNotifications();
  }

  async function handleCreateAccount(name: string) {
    const username = name.toLowerCase().normalize('NFD').replace(/[^a-z0-9]+/g, '').slice(0, 40);
    const response = await fetch(`${apiUrl}/api/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, display_name: name }),
    });
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    if (!response.ok) throw new Error(payload?.detail === 'username_taken' ? t('personExists') : t('cannotSaveGeneric'));
    // Il primo account entra da solo: il server ha gia' aperto la sessione.
    await loadAuth();
    await loadData();
    setMovimentiVersione((versione) => versione + 1);
    await loadNotifications();
  }

  async function handleLogout() {
    await fetch(`${apiUrl}/api/auth/logout`, { method: 'POST' }).catch(() => undefined);
    await loadAuth();
  }

  async function download(kind: 'excel' | 'pdf' | 'data') {
    setDownloadBusy(true);
    setDownloadError('');
    try {
      const url = kind === 'data' ? `${apiUrl}/api/export/data` : `${apiUrl}/api/reports/${kind}?year=${selectedYear}&month=${selectedMonth}`;
      const filename = kind === 'data' ? 'money-dati.xlsx' : `Money-report-${selectedYear}-${String(selectedMonth).padStart(2, '0')}.${kind === 'excel' ? 'xlsx' : 'pdf'}`;
      await downloadFile(url, filename, t);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : t('importExportFailed'));
    } finally { setDownloadBusy(false); }
  }
  const downloadReport = (kind: 'excel' | 'pdf') => { void download(kind); };
  const downloadData = () => { void download('data'); };

  function openNewTransaction() {
    setEffectivePreview(MODULO_VUOTO);
    setEditingTransaction(null);
    setDuplicatingTransaction(null);
    setSaveError('');
    setNewTransactionOpen(true);
  }

  function openTransfer(destination = '', suggestion?: { principal: number; interest: number }) {
    setSaveError('');
    setTransferSource('');
    setTransferDestination(destination);
    setTransferAmount('');
    setTransferPrincipal(suggestion ? suggestion.principal.toFixed(2) : '');
    setTransferInterest(suggestion ? suggestion.interest.toFixed(2) : '0');
    setTransferOpen(true);
  }

  const openEditTransaction = useCallback((transaction: Transaction) => {
    setEffectivePreview(MODULO_VUOTO);
    setEditingTransaction(transaction);
    setDuplicatingTransaction(null);
    setSaveError('');
    setLinkLedgerOpen(false);
    setLinkedLedgerRows([]);
    setLinkPickerOpen(false);
    setNewTransactionOpen(true);
    // Carica i link in background: la sezione "Operazioni collegate" si popola
    // appena la risposta arriva. Niente loading visibile: l'utente intanto
    // modifica gli altri campi senza essere bloccato.
    void loadLinkedLedgerItems(transaction.id).then(setLinkedLedgerItems);
  }, [loadLinkedLedgerItems]);

  const openDuplicateTransaction = useCallback((transaction: Transaction) => {
    setEffectivePreview(MODULO_VUOTO);
    setEditingTransaction(null);
    setDuplicatingTransaction(transaction);
    setSaveError('');
    setNewTransactionOpen(true);
  }, []);

  // Sostituisce i dati con quelli di un file di scambio. Il backend valida
  // tutto prima di scrivere: se il file non va bene, il database resta com'e'.
  async function handleImportData(file: File): Promise<string> {
    setImporting(true);
    try {
      const body = new FormData();
      body.append('file', file);
      const response = await fetch(`${apiUrl}/api/import/data`, { method: 'POST', body });
      if (!response.ok) throw new Error(await responseError(response, t));
      const payload = await response.json() as { rows?: Record<string, number> };
      await loadData();
      setMovimentiVersione((versione) => versione + 1);
      const total = Object.values(payload?.rows ?? {}).reduce((sum, count) => sum + count, 0);
      return t('importDataDone', { count: total });
    } finally {
      setImporting(false);
    }
  }

  async function importStatement(kind: 'pdf' | 'csv') {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = `.${kind}`;
    input.style.display = 'none';
    document.body.appendChild(input);
    input.oncancel = () => input.remove();
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setPdfImporting(true);
      setImportFeedback(null);
      try {
        const body = new FormData();
        body.append('file', file);
        const response = await fetch(`${apiUrl}/api/import/${kind}`, { method: 'POST', body });
        if (!response.ok) throw new Error(await responseError(response, t));
        const result = await response.json() as { transactions: PDFTransaction[]; rulesDiscarded?: string[] };
        if (!result.transactions.length) throw new Error(t('statementEmpty'));
        setPdfPreviewTransactions(result.transactions);
        // Regole scartate perche' non compilabili: senza dirle resterebbero
        // regole che non fanno niente, in silenzio.
        setImportFeedback(result.rulesDiscarded?.length
          ? { ok: false, message: t('statementRulesDiscarded', { rules: result.rulesDiscarded.join(', ') }) }
          : null);
        setShowPdfPreview(true);
      } catch (error) {
        setImportFeedback({ ok: false, message: error instanceof Error ? error.message : t('statementParseFailed') });
      } finally { setPdfImporting(false); input.remove(); }
    };
    input.click();
  }
  const handlePdfImport = () => importStatement('pdf');
  const handleCsvImport = () => importStatement('csv');

  async function handleConfirmPdfImport(approvedTransactions: PDFTransaction[]) {
    setPdfImporting(true);
    setImportFeedback(null);
    try {
      const response = await fetch(`${apiUrl}/api/transactions/pdf-import`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(approvedTransactions)
      });
      if (!response.ok) throw new Error(await responseError(response, t));
      const result = await response.json() as { saved: number; errors: { index: number; code: string }[] };
      setImportFeedback({ ok: !result.errors.length, message: t('statementSaved', { saved: result.saved, errors: result.errors.length }) });
      if (result.errors.length) {
        setPdfPreviewTransactions(result.errors.map(error => ({ ...approvedTransactions[error.index], duplicate: false, errorCode: error.code })));
      } else {
        setShowPdfPreview(false);
        setPdfPreviewTransactions([]);
      }
      await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
      setMovimentiVersione(versione => versione + 1);
    } catch (error) {
      setImportFeedback({ ok: false, message: error instanceof Error ? error.message : t('importExportFailed') });
    } finally { setPdfImporting(false); }
  }

  async function handleCreateRecurring(payload: Record<string, string | number | boolean | null>) {
    const response = await fetch(`${apiUrl}/api/recurring-transactions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error('Impossibile salvare la ricorrenza');
    const created = await response.json() as RecurringTransactionData;
    setRecurringTransactions((current) => [...current, created]);
  }

  async function handleGenerateRecurring(upTo: string): Promise<number> {
    const response = await fetch(`${apiUrl}/api/recurring-transactions/generate?up_to_date=${upTo}`, { method: 'POST' });
    if (!response.ok) throw new Error('recurring-generate');
    const result = await response.json() as { generated: number };
    await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']);
    setMovimentiVersione((versione) => versione + 1);
    return result.generated;
  }

  async function handleDeleteRecurring(id: number) {
    const response = await fetch(`${apiUrl}/api/recurring-transactions/${id}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('Impossibile eliminare la ricorrenza');
    setRecurringTransactions((current) => current.filter((rule) => rule.id !== id));
  }

  async function handleSettingChange(key: string, value: string) {
    // Lo spostamento delle entrate tardive riassegna il mese di competenza a
    // tutto lo storico, non solo ai movimenti futuri: va chiesto.
    const ricalcola = key === 'late_income_shift' || key === 'late_income_day';
    if (ricalcola && !window.confirm(t('shiftLateIncomeConfirm'))) return;
    setSettingSaving(key);
    setSettingError('');
    try {
      const response = await fetch(`${apiUrl}/api/settings/${key}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      });
      if (!response.ok) throw new Error('setting');
      setSettingsData((current) => ({
        ...current,
        settings: { ...current.settings, [key]: value },
      }));
      // Il server ha gia' spostato i movimenti: senza ricaricare, budget e
      // panoramica restavano sui mesi di prima fino al prossimo aggiornamento.
      if (ricalcola) {
        await loadData(undefined, ['ledger', 'overview', 'budget', 'goals', 'trends', 'analysis']);
        setMovimentiVersione((versione) => versione + 1);
      }
      // Il simbolo di confronto da solo non disegna niente: la sua storia di
      // fine mese arriva con le quotazioni, come quella di ogni strumento, e il
      // grafico legge i dati, non l'impostazione. Senza questo aggiornamento il
      // confronto comparirebbe solo al prossimo refresh a mano, cioe' mai.
      if (key === 'benchmark_symbol') {
        const aggiornato = await handleMarketRefresh().catch(() => null);
        if (!aggiornato) await loadData(undefined, ['investments']);
      }
    } catch {
      // L'app e' connessa: il salvataggio di questa singola preferenza non
      // ha funzionato. Mostriamo un errore circoscritto invece di far credere
      // che tutta l'app sia offline.
      setSettingError(t('settingSaveError'));
    } finally {
      setSettingSaving('');
    }
  }

  // Margine del periodo: pianificato meno speso, entrambi gia' coperti da ogni
  // categoria (non solo le prime sei). Usato nella card "Budget of period".
  const remainingBudget = summary.plannedExpenses - summary.actualExpenses;

  const displayName = auth?.user?.displayName || '';
  const initials = displayName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase();
  const formTransaction = editingTransaction ?? duplicatingTransaction;
  // Il tipo scelto nel form decide quali categorie hanno senso e se il conto di
  // destinazione serve: un trasferimento ha una destinazione e nessuna
  // categoria, tutto il resto il contrario.
  const movementType = effectivePreview.type || formTransaction?.transactionType || 'Expenses';
  // Il conto segue il movimento aperto nel dialogo: in creazione parte vuoto,
  // in modifica dal conto del movimento. Un effetto solo invece di ricordarsene
  // nei tre punti da cui il dialogo si apre.
  useEffect(() => {
    if (newTransactionOpen) setMovementAccount(formTransaction?.accountName ?? '');
  }, [newTransactionOpen, formTransaction?.accountName]);
  // Il conto scelto serve a decidere se mostrare il campo dell'addebito: una
  // spesa su un conto di debito puo' essere un interesse, su un conto normale no.
  const contoDebito = accounts.some((account) => account.name === movementAccount && account.group === 'liability');
  // Un Investment sposta denaro come un giroconto: stessa forma nel modulo -
  // niente categoria, destinazione obbligatoria - e un vincolo in piu': un
  // lato dev'essere un conto broker.
  const isSpostamento = movementType === 'Transfers' || movementType === 'Investment' || movementType === 'Debt';
  const isTransfer = isSpostamento;
  // Se il broker e' gia' l'origine, il movimento e' un PRELIEVO e la
  // destinazione e' una banca qualunque. Filtrare la destinazione sui soli
  // broker valeva solo per i versamenti, e faceva sparire dalla tendina la
  // banca di arrivo di ogni vendita.
  const origineForm = effectivePreview.origine || formTransaction?.accountName || '';
  const prelievoDaBroker = accounts.some((account) => account.isBroker && account.name === origineForm);
  const origineDaDebito = accounts.find((account) => account.name === origineForm)?.group === 'liability';
  const movementCategories = settingsData.categoriesByType[movementType] ?? [];
  // Spese, entrate e risparmi: gli stessi gruppi fra cui il server accetta una
  // categoria di regola. I trasferimenti restano fuori perche' una regola non
  // li tocca mai, quindi proporli sarebbe una scelta senza effetto.
  const categorieRegola = useMemo(() => Array.from(new Set(
    ['Expenses', 'Income', 'Savings'].flatMap((gruppo) => settingsData.categoriesByType[gruppo] ?? []))).sort(),
    [settingsData.categoriesByType]);
  const transferSourceIsDebt = accounts.some((account) => account.name === transferSource && account.group === 'liability');
  const transferDestinationIsDebt = accounts.some((account) => account.name === transferDestination && account.group === 'liability');
  const debtTransferMode = transferSourceIsDebt ? 'drawdown' : transferDestinationIsDebt ? 'repayment' : null;

  function navigate(section: Section) {
    setActiveSection(section);
    setMenuOpen(false);
    setNotificationsOpen(false);
    setProfileOpen(false);
  }

  // Si entra quando si sa chi sei. Il server risponde con un utente anche
  // quando nessuno ha una password - li' l'app resta aperta come e' sempre
  // stata - ma se non risponde con nessuno, o perche' serve l'accesso o
  // perche' l'installazione e' appena nata, si passa da questa schermata.
  if (auth && (!auth.user || sceltaProfilo)) {
    return <LoginScreen users={auth.users} onLogin={handleLogin} onCreate={handleCreateAccount} />;
  }

  return (
    <main className="min-h-screen bg-[var(--money-page)] text-[#17211f]" >
      <div className="min-h-screen lg:grid lg:grid-cols-[244px_1fr]">
        <aside className={`fixed inset-y-0 left-0 z-40 flex w-[244px] flex-col bg-[var(--money-sidebar)] px-4 py-5 text-white transition-transform lg:visible lg:translate-x-0 ${menuOpen ? 'visible translate-x-0' : 'invisible -translate-x-full'}`}>
          <div className="mb-8 flex items-center justify-between px-2">
            <div className="flex items-center gap-3">
              <div className="grid size-10 place-items-center rounded-xl bg-[var(--money-accent)] text-[var(--money-on-accent)]">
                <BadgeEuro className="size-6" strokeWidth={2.4} />
              </div>
              <div>
                <p className="text-lg font-semibold tracking-tight">{t('appName')}</p>
                <p className="text-xs text-white/45">{t('appTagline')}</p>
              </div>
            </div>
            <Button variant="ghost" size="icon" className="text-white hover:bg-white/10 hover:text-white lg:hidden" onClick={() => setMenuOpen(false)} aria-label={t('closeMenu')}>
              <X className="size-5" />
            </Button>
          </div>
          <nav className="space-y-1">
            {navItems.map(({ label, labelKey, icon: Icon }) => (
              <button key={label} onClick={() => navigate(label as Section)} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition ${activeSection === label ? 'bg-white/12 font-medium text-white' : 'text-white/58 hover:bg-white/7 hover:text-white'}`}>
                <Icon className="size-[18px]" />{t(labelKey)}
              </button>
            ))}
          </nav>
          <div className="mt-auto rounded-2xl border border-white/8 bg-white/5 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium">
              <span className={`size-2 rounded-full ${connected ? 'bg-[#87db9b]' : 'bg-[#f1c96b]'}`} />
              {connected ? t('dataUpdated') : t('localPreview')}
            </div>
            <p className="text-xs leading-5 text-white/48">
              {connected
                ? connectionError ? t('statusReadyExcelIssue', { error: connectionError }) : t('statusReadyConnected')
                : dbSummary
                  ? dbSummary.isEmpty
                    ? t('statusEmptyDb')
                    : t('statusPopulatedButUnreachable', { count: dbSummary.counts.transactions.toLocaleString(locale), accounts: dbSummary.counts.accounts, error: connectionError || t('networkErrorGeneric') })
                  : connectionError ? t('statusCheckConnection', { error: connectionError }) : t('statusConnecting')}
            </p>
            {dbSummary && !dbSummary.isEmpty && (
              <p className="mt-2 text-[10px] leading-4 text-white/35">
                {t('statsSummaryLine', { transactions: dbSummary.counts.transactions.toLocaleString(locale), accounts: dbSummary.counts.accounts, budgets: dbSummary.counts.budgets, goals: dbSummary.counts.goals, investments: dbSummary.counts.investments })}
              </p>
            )}
          </div>
          {auth?.user && (
            <div className="mt-3 flex items-center gap-2.5 rounded-xl bg-white/6 px-3 py-2.5">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[var(--money-accent)] text-xs font-semibold text-[var(--money-on-accent)]">
                {auth.user.displayName.slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-medium text-white/80">{auth.user.displayName}</span>
              {auth.users.length > 1 && (
                <button onClick={() => setSceltaProfilo(true)} title={t('switchProfile')} aria-label={t('switchProfile')}
                  className="text-white/45 transition hover:text-white">
                  <Users className="size-4" />
                </button>
              )}
              {auth.loginRequired && (
                <button onClick={() => void handleLogout()} title={t('logout')} aria-label={t('logout')}
                  className="text-white/45 transition hover:text-white">
                  <LogOut className="size-4" />
                </button>
              )}
            </div>
          )}
          <button onClick={() => navigate('Impostazioni')} className={`mt-3 flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm hover:bg-white/7 hover:text-white ${activeSection === 'Impostazioni' ? 'bg-white/12 font-medium text-white' : 'text-white/58'}`}>
            <Settings className="size-[18px]" />{t('navImpostazioni')}
          </button>
        </aside>

        {menuOpen && <button className="fixed inset-0 z-30 bg-black/35 lg:hidden" aria-label={t('closeMenu')} onClick={() => setMenuOpen(false)} />}

        <section className="min-w-0 lg:col-start-2">
          <header className="sticky top-0 z-20 flex h-[72px] items-center justify-between border-b border-black/5 bg-[var(--money-page)]/90 px-4 backdrop-blur-xl sm:px-7 lg:px-9">
            <div className="flex items-center gap-3">
              <Button variant="outline" size="icon" className="border-black/8 bg-white lg:hidden" onClick={() => setMenuOpen(true)} aria-label={t('openMenu')}><Menu className="size-5" /></Button>
              <div className="relative hidden sm:block">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-black/35" />
                <input value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') navigate('Movimenti'); }} className="h-10 w-64 rounded-xl border border-black/7 bg-white/70 pl-10 pr-3 text-sm outline-none transition placeholder:text-black/35 focus:border-[#5c8f82] focus:bg-white" placeholder={t('searchPlaceholder')} />
              </div>
            </div>
            <div className="relative flex items-center gap-2">
              {inCorso > 0 && <span role="status" className="mr-1 hidden items-center gap-1.5 rounded-lg bg-white/70 px-2.5 py-1.5 text-xs text-[#71807c] sm:flex">
                <RefreshCw className="size-3.5 animate-spin" />{t('updating')}
              </span>}
              <Button aria-label={t('notifications')} aria-expanded={notificationsOpen} onClick={() => { setNotificationsOpen((value) => !value); setProfileOpen(false); }} variant="outline" size="icon" className="relative border-black/7 bg-white/70"><Bell className="size-[18px]" />{notifications.length > 0 && <span className="absolute -right-1 -top-1 grid min-w-4 place-items-center rounded-full bg-[#bd5e46] px-1 text-[10px] font-semibold leading-4 text-white">{notifications.length}</span>}</Button>
              <button aria-label={t('profile')} aria-expanded={profileOpen} onClick={() => { setProfileOpen((value) => !value); setNotificationsOpen(false); }} className="ml-1 flex items-center gap-2 rounded-xl p-1.5 pr-2 hover:bg-black/5"><span className="grid size-8 place-items-center rounded-lg bg-[var(--money-accent)] text-xs font-bold text-[#18342e]">{initials}</span><ChevronDown className="size-4 text-black/45" /></button>
              {notificationsOpen && <NotificationsPanel items={notifications} onDismiss={dismissNotification} onDismissAll={dismissAllNotifications} />}
              {profileOpen && <div className="absolute right-0 top-12 w-56 rounded-xl border border-black/7 bg-white p-2 shadow-xl">
                <div className="px-3 py-2"><p className="text-sm font-semibold">{displayName}</p><p className="text-xs text-[#71807c]">{t('personalArchive')}</p></div>
                <div className="px-3 py-2">
                  <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-[#87918e]">{t('language')}</p>
                  <div className="flex gap-1 rounded-lg bg-[#f3f5f1] p-1">
                    {(['it', 'en', 'de', 'es', 'fr'] as Lang[]).map((code) => (
                      <button key={code} type="button" title={LANG_LABELS[code]} onClick={() => setLang(code)} className={`flex-1 rounded-md py-1 text-xs font-semibold uppercase transition ${lang === code ? 'bg-[var(--money-primary)] text-white' : 'text-[#52615d] hover:bg-white'}`}>
                        {code}
                      </button>
                    ))}
                  </div>
                </div>
                <button onClick={() => navigate('Impostazioni')} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-black/5"><Settings className="size-4" />{t('navImpostazioni')}</button>
              </div>}
            </div>
          </header>

          <div className="mx-auto max-w-[1450px] px-4 py-7 sm:px-7 lg:px-9 lg:py-9">
            {activeSection === 'Panoramica' ? <>
            <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
              <div><p className="mb-1 text-sm font-medium text-[#71807c]">{t('panoramicaSubtitle')}</p><h1 className="flex items-center text-2xl font-semibold tracking-[-0.03em] sm:text-[30px]">{t('panoramicaGreeting', { name: displayName.split(/\s+/)[0] })}<PageHelp titolo="helpTitle" testo="helpPanoramica" dipendenza="helpPanoramicaDep" /></h1></div>
              <div className="flex flex-wrap items-center justify-end gap-2">
                {overviewView === 'panoramica' && <PeriodSelector
                  value={period}
                  years={settingsData.options.overviewYears}
                  onChange={setPeriod}
                  compareTo={overviewCompareTo}
                  onCompareToChange={setOverviewCompareTo}
                />}
                <div className="flex shrink-0 items-center gap-2">
                <div className="flex overflow-hidden rounded-xl border border-black/7 bg-white shadow-sm shadow-black/[0.02]">
                  <button type="button" onClick={() => setOverviewView('panoramica')} className={`h-10 px-3.5 text-sm font-medium transition ${overviewView === 'panoramica' ? 'bg-[var(--money-primary)] text-white' : 'text-[#52615d] hover:bg-[#f4f5f1]'}`}>{t('overviewToggle')}</button>
                  <button type="button" onClick={() => setOverviewView('analisi')} className={`h-10 px-3.5 text-sm font-medium transition ${overviewView === 'analisi' ? 'bg-[var(--money-primary)] text-white' : 'text-[#52615d] hover:bg-[#f4f5f1]'}`}>{t('analysisToggle')}</button>
                </div>
                {overviewView === 'panoramica' && <Button className="h-10 rounded-xl bg-[var(--money-primary)] px-4 text-white hover:bg-[var(--money-primary-hover)]" onClick={openNewTransaction}><Plus className="size-4" /><span className="hidden sm:inline">{t('newTransaction')}</span></Button>}
                </div>
              </div>
            </div>

            {overviewView === 'analisi' ? (
              <AnnualAnalysisView
                data={analysisData}
                period={period}
                years={settingsData.options.overviewYears}
                categoryType={analysisCategoryType}
                category={analysisCategory}
                onPeriodChange={setPeriod}
                onCategoryTypeChange={(value) => { setAnalysisCategoryType(value); setAnalysisCategory(null); }}
                onCategoryChange={setAnalysisCategory}
              />
            ) : <>

            {/*
              Banner d'errore Panoramica. `loadData` lancia se `/api/summary`
              fallisce, e senza un segnale esplicito la griglia restava
              skeleton per sempre. Il banner dice cosa manca e rilancia la
              fetch pulita (AbortController incluso) tramite panoramicaRetryKey.
            */}
            {!summaryLoaded && connectionError && (
              <div className="mb-5 rounded-2xl border border-[#efc4b8] bg-[#fff6f3] p-4 shadow-sm">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-start gap-3">
                    <span className="mt-0.5 grid size-9 place-items-center rounded-xl bg-[#fce9e3]">
                      <AlertCircle className="size-5 text-[#bd5e46]" />
                    </span>
                    <div>
                      <p className="text-sm font-semibold">{t('overviewLoadFailed')}</p>
                      <p className="mt-1 text-xs leading-5 text-[#52615d]">{t('overviewLoadFailedHint')}</p>
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => setPanoramicaRetryKey((k) => k + 1)}>{t('retry')}</Button>
                </div>
              </div>
            )}

            {summaryLoaded && <ControlRow control={summary.control} onOpenBudget={() => navigate('Budget')} />}
            <div className="mb-4 grid gap-4 sm:grid-cols-3">
              {!summaryLoaded ? <>
                <SkeletonMetricCard />
                <SkeletonMetricCard />
                <SkeletonMetricCard />
              </> : <>
                <MetricCard title={t('income')} value={summary.income} change={formatComparisonChange(t, formatEuro, monthNames, summary.comparison?.incomeDelta, summary.comparison)} delta={summary.comparison?.incomeDelta} icon={ArrowDownRight} tone="income" />
                <MetricCard title={t('expenses')} value={summary.expenses} change={formatComparisonChange(t, formatEuro, monthNames, summary.comparison?.expensesDelta, summary.comparison)} delta={summary.comparison?.expensesDelta} icon={ArrowUpRight} tone="expense" />
                <MetricCard title={t('savings')} value={summary.savings} change={formatComparisonChange(t, formatEuro, monthNames, summary.comparison?.savingsDelta, summary.comparison)} delta={summary.comparison?.savingsDelta} icon={PiggyBank} tone="saving" />
              </>}
            </div>

            <div className="mb-5 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
              {!summaryLoaded ? <SkeletonNetWorthCard /> : <NetWorthCard detail={summary.netWorthDetail} comparison={summary.netWorthComparison} />}
              <DebitCard apiUrl={apiUrl} version={movimentiVersione} onOpen={() => navigate('Debiti')} />
            </div>

            <div className="mb-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between"><span className="text-sm font-medium text-[#71807c]">{t('periodCompletion')}</span><span className="text-xs text-[#87918e]">{t('daysOf', { passed: summary.daysPassed, total: summary.daysInPeriod })}</span></div>
                  <p className="mt-2 text-2xl font-semibold tracking-tight">{Math.round(summary.periodCompletion * 100)}%</p>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#eef0ec]"><div className="h-full rounded-full bg-[#6d8ff4]" style={{ width: `${summary.periodCompletion * 100}%` }} /></div>
                </CardContent>
              </Card>
              <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
                <CardContent className="p-5">
                  <div className="flex items-center justify-between"><span className="text-sm font-medium text-[#71807c]">{t('periodSavingsRate')}</span></div>
                  <p className="mt-2 text-2xl font-semibold tracking-tight">{summary.savingsRate !== null ? `${(summary.savingsRate * 100).toLocaleString(locale, { maximumFractionDigits: 1 })}%` : '—'}</p>
                  <p className="mt-3 text-xs text-[#87918e]">{summary.savingsRate !== null ? t('onPeriodIncome') : t('noIncomeInPeriod')}</p>
                </CardContent>
              </Card>
              {/* Su un mese chiuso la card resta - togliendola ballerebbe la
                  griglia - ma dice che non c'e' niente da stimare. */}
              {summary.projection && <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
                <CardContent className="p-5">
                  <span className="text-sm font-medium text-[#71807c]">{t('endOfMonthEstimate')}</span>
                  {summary.projection.state === 'closed' ? <>
                    <p className="mt-2 text-2xl font-semibold tracking-tight text-[#a0a8a5]">—</p>
                    <p className="mt-3 text-xs text-[#87918e]">{t('estimateClosedMonth')}</p>
                    <p className="mt-1 text-[11px] leading-4 text-[#a0a8a5]">{t('estimateClosedTotal', { spent: formatCompactEuro(summary.projection.spentSoFar) })}</p>
                  </> : <>
                    <p className="mt-2 text-2xl font-semibold tracking-tight">{formatEuro(summary.projection.estimate ?? 0)}</p>
                    <p className="mt-3 text-xs text-[#87918e]">{summary.projection.planned > 0
                      ? t('estimateVsPlanned', { spent: formatCompactEuro(summary.projection.spentSoFar), planned: formatCompactEuro(summary.projection.planned) })
                      : t('estimateSoFar', { spent: formatCompactEuro(summary.projection.spentSoFar) })}</p>
                    <p className="mt-1 text-[11px] leading-4 text-[#a0a8a5]">{t('estimateRule')}</p>
                  </>}
                </CardContent>
              </Card>}
              {summary.goalCoverage.monthlyNeeded > 0 && <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
                <CardContent className="p-5">
                  <span className="text-sm font-medium text-[#71807c]">{t('goalCoverage')}</span>
                  <p className="mt-2 text-2xl font-semibold tracking-tight">{(summary.goalCoverage.coverage ?? 0).toLocaleString(locale)}%</p>
                  <p className="mt-3 text-xs text-[#87918e]">{t('goalCoverageDetail', { saved: formatCompactEuro(summary.goalCoverage.savedThisPeriod), needed: formatCompactEuro(summary.goalCoverage.monthlyNeeded) })}</p>
                </CardContent>
              </Card>}
            </div>

            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(310px,0.85fr)]">
              <PeriodBreakdownCard breakdown={summaryBreakdown} isWholeYear={overviewMonth === null} />

              <Card className="flex flex-col border-black/6 bg-[var(--money-deep)] text-white shadow-sm shadow-black/[0.04]">
                <CardHeader className="pb-2"><div className="flex items-center justify-between"><CardTitle className="text-[17px]">{t('budgetOfPeriod')}</CardTitle><CircleDollarSign className="size-5 text-[var(--money-accent)]" /></div><p className="text-xs text-white/50">{t('howMuchLeftToSpend')}</p></CardHeader>
                <CardContent className="flex min-h-0 flex-1 flex-col">
                  <p className="mt-1 text-3xl font-semibold tracking-tight">{formatEuro(remainingBudget)}</p>
                  <div className="mt-6 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-[var(--money-accent)]" style={{ width: `${Math.min(summary.budgetUsed, 100)}%` }} /></div>
                  <div className="mt-2 flex justify-between text-xs text-white/48"><span>{t('usedPercent', { percent: summary.budgetUsed })}</span><span>{t('availablePercent', { percent: Math.max(0, 100 - summary.budgetUsed) })}</span></div>
                  {budgetAlerts && budgetAlerts.alert_count > 0 && <p className="mt-3 text-xs font-medium text-[#efb09e]">{t('categoriesOverBudgetCount', { count: budgetAlerts.alert_count })}</p>}
                  {(() => {
                    const categorie = (summaryBreakdown?.sections.expenses.categories ?? [])
                      .map((categoria) => ({
                        nome: categoria.name,
                        budget: categoria.budget,
                        speso: categoria.tracked,
                      }))
                      // Anche le categorie senza budget ma con spesa: sono
                      // sforamenti a tutti gli effetti, e prima sparivano.
                      .filter((categoria) => categoria.budget > 0 || categoria.speso > 0)
                      // In cima chi e' piu' in rosso, in euro. Ordinare per
                      // percentuale non regge con budget zero (percentuale
                      // infinita) e mette 12 su 7 davanti a 98 su 20.
                      .sort((a, b) => (a.budget - a.speso) - (b.budget - b.speso));
                    const inEvidenza = categorie.slice(0, 5);
                    const altre = categorie.slice(5);
                    return <>
                      <div className="mt-6 space-y-2.5">{inEvidenza.map((categoria) => {
                        return <div key={categoria.nome} className="space-y-1">
                          <div className="flex items-baseline justify-between gap-2 text-sm">
                            <span className="truncate text-white/72">{categoria.nome}</span>
                            <span className="shrink-0 font-medium">{formatCompactEuro(categoria.speso)} <span className="font-normal text-white/35">/ {categoria.budget > 0 ? formatCompactEuro(categoria.budget) : '—'}</span></span>
                          </div>
                          {categoria.budget > 0 && <div className="h-1 overflow-hidden rounded-full bg-white/12">
                            <div className="h-full rounded-full" style={{ width: `${Math.min((categoria.speso / categoria.budget) * 100, 100)}%`, background: categoria.speso > categoria.budget ? '#ef8e72' : 'var(--money-accent)' }} />
                          </div>}
                        </div>;
                      })}</div>
                      {/* Le altre non spariscono: di loro serve sapere solo
                          quanto margine resta, e da qui si va a vederle tutte. */}
                      <button type="button" onClick={() => navigate('Budget')} className="mt-4 flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-xs text-white/55 transition hover:bg-white/8 hover:text-white/80">
                        {/* Niente cifra qui: il margine delle altre categorie
                            non e' spendibile - una parte copre lo sforamento di
                            quelle sopra - e messo accanto al totale in cima
                            sembrava una seconda risposta alla stessa domanda. */}
                        <span>{altre.length ? t('otherCategories', { count: altre.length }) : t('openBudget')}</span>
                        <ChevronRight className="size-4 shrink-0" />
                      </button>
                    </>;
                  })()}
                </CardContent>
              </Card>
            </div>

            <div className="mt-5">
              <PeriodBreakdownTable breakdown={summaryBreakdown} />
            </div>
            </>}
            </> : <SectionView
              section={activeSection}
              summary={summary}
              movimentiVersione={movimentiVersione}
              inCorso={inCorso}
              accounts={accounts}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              budgetData={budgetData}
              budgetSuggestions={budgetSuggestions}
              onCategoryGroupChange={handleCategoryGroupChange}
              annualBudgetData={annualBudgetData}
              budgetDashboardData={budgetDashboardData}
              budgetTrendsData={budgetTrendsData}
              budgetLoadFailed={budgetLoadFailed}
              trendsLoadFailed={trendsLoadFailed}
              goalsData={goalsData}
              netWorthData={netWorthData}
              calculationData={calculationData}
              onNewTransaction={openNewTransaction}
              onTransfer={openTransfer}
              onEditTransaction={openEditTransaction}
              onDuplicateTransaction={openDuplicateTransaction}
              onDeleteTransaction={handleDeleteTransaction}
              onSplitTransaction={setDaDividere}
              onBudgetUpdate={handleBudgetUpdate}
              onBudgetCreate={handleBudgetCreate}
              onBudgetDelete={handleBudgetDelete}
              onBudgetCopy={handleBudgetCopy}
              onAnnualBudgetApply={handleAnnualBudgetApply}
              onGoalSave={handleGoalSave}
              onGoalDelete={handleGoalDelete}
              investmentDashboardData={investmentDashboardData}
              investmentLedger={investmentLedger}
              investmentAllocationData={investmentAllocationData}
              onInvestmentSave={handleInvestmentSave}
              onInvestmentDelete={handleInvestmentDelete}
              onInstrumentSave={handleInstrumentSave}
              onMarketRefresh={handleMarketRefresh}
              notesData={notesData}
              onNoteSave={handleNoteSave}
              onNoteDelete={handleNoteDelete}
              onNewAccount={openNewAccount}
              onNavigate={navigate}
              onAccountEdit={openEditAccount}
              onAccountValuations={(conto) => { setAccountError(''); setValuationForId(conto.id); }}
              onAccountDelete={handleAccountDelete}
              recurringTransactions={recurringTransactions}
              onCreateRecurring={handleCreateRecurring}
              onDeleteRecurring={handleDeleteRecurring}
              onGenerateRecurring={handleGenerateRecurring}
              categorizationRules={categorizationRules}
              categorieRegola={categorieRegola}
              onCategoryRulesChanged={() => caricaAmbito(['categoryRules'])}
              onDownloadReport={downloadReport}
              onDownloadData={downloadData}
              apiUrl={apiUrl}
              onReloadData={loadData}
              selectedYear={selectedYear}
              selectedMonth={selectedMonth}
              period={period}
              onPeriodChange={setPeriod}
              years={settingsData.budgetYearsByType?.[budgetType] || settingsData.options.years}
              budgetType={budgetType}
              onBudgetTypeChange={setBudgetType}
              budgetView={budgetView}
              onBudgetViewChange={setBudgetView}
              settingsData={settingsData}
              onSettingChange={handleSettingChange}
              settingSaving={settingSaving}
              settingError={settingError}
              importing={importing}
              onImportData={handleImportData}
              onReload={async () => { await loadData(undefined, ['ledger', 'overview', 'budget', 'goals']); setMovimentiVersione(v => v + 1); }}
              account={auth?.user ?? null}
              onAccountChanged={loadAuth}
              importFeedback={importFeedback}
              downloadBusy={downloadBusy}
              downloadError={downloadError}
              canManageBackups={auth?.canManageBackups ?? false}
              pdfImporting={pdfImporting}
              onPdfImport={handlePdfImport}
              onCsvImport={handleCsvImport}
              showPdfPreview={showPdfPreview}
              pdfPreviewTransactions={pdfPreviewTransactions}
              onPdfImportConfirm={handleConfirmPdfImport}
              onPdfImportCancel={() => {
                setShowPdfPreview(false);
                setPdfPreviewTransactions([]);
              }}
              connected={connected}
              trendYearsAvailable={trendYearsAvailable}
              trendYears={trendYears}
              onTrendYearsChange={setTrendYears}
            />}
          </div>
        </section>
      </div>

      <Dialog open={newTransactionOpen} onOpenChange={(open) => { setNewTransactionOpen(open); if (!open) { setEditingTransaction(null); setDuplicatingTransaction(null); } }}>
        <DialogContent className="max-w-md gap-5 p-6">
          <DialogHeader>
            <DialogTitle className="text-lg">{editingTransaction ? t('editMovement') : duplicatingTransaction ? t('duplicateMovement') : t('newMovement')}</DialogTitle>
            <DialogDescription>{editingTransaction ? t('editMovementDesc') : duplicatingTransaction ? t('duplicateMovementDesc') : t('newMovementDesc')}</DialogDescription>
          </DialogHeader>
          <form ref={movementFormRef} key={editingTransaction ? `edit-${editingTransaction.id}` : duplicatingTransaction ? `duplicate-${duplicatingTransaction.id}` : 'new'} className="space-y-4" onSubmit={handleSaveTransaction} onChange={(event) => { const form = new FormData(event.currentTarget); setEffectivePreview({ occurred: String(form.get('occurred_on') ?? ''), type: String(form.get('transaction_type') ?? ''), amount: Math.abs(Number(form.get('amount') || 0)), origine: String(form.get('account_name') ?? '') }); }}>
            <datalist id="strumenti-esistenti">{nomiStrumenti.map((nome) => <option key={nome} value={nome} />)}</datalist>
            <div className="grid grid-cols-2 gap-3">
              <label htmlFor="movement-date" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDate')}<Input id="movement-date" required name="occurred_on" type="date" defaultValue={formTransaction?.occurredOn ?? new Date().toISOString().slice(0, 10)} className="h-10 bg-white" /></label>
              <label htmlFor="movement-type" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldType')}<select id="movement-type" required name="transaction_type" defaultValue={formTransaction?.transactionType ?? 'Expenses'} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"><option value="Expenses">{t('typeExpense')}</option><option value="Income">{t('typeIncome')}</option><option value="Transfers">{t('typeTransfer')}</option><option value="Investment">{t('typeInvestment')}</option><option value="Debt">{t('typeDebt')}</option></select></label>
            </div>
            {(() => {
              const occurred = effectivePreview.occurred || formTransaction?.occurredOn || new Date().toISOString().slice(0, 10);
              const type = effectivePreview.type || formTransaction?.transactionType || 'Expenses';
              const shift = settingsData.settings.late_income_shift ?? 'Inactive';
              const day = Number(settingsData.settings.late_income_day ?? 20) || 20;
              const effective = previewEffectiveDate(occurred, type, shift, day);
              if (effective === occurred) return null;
              return <p className="rounded-lg bg-[#f0f8f4] px-3 py-2 text-xs text-[#3b6a5b]">{t('effectiveDateHint', { date: formatDate(`${effective}T12:00:00`, { day: 'numeric', month: 'long', year: 'numeric' }) })}</p>;
            })()}
            <div className="grid grid-cols-2 gap-3">
              <label htmlFor="movement-category" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldCategory')}<select id="movement-category" required={!isTransfer && Boolean(editingTransaction)} disabled={isTransfer} name="category" defaultValue={formTransaction?.category ?? ''} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 disabled:bg-[#f4f5f1] disabled:text-[#a3adaa]"><option value="" disabled={!isTransfer && Boolean(editingTransaction)}>{isTransfer ? t('categoryNotApplicable') : editingTransaction ? t('selectPlaceholder') : t('categoryAutomatic')}</option>{!isTransfer && uniqueOptions(formTransaction?.category ?? '', movementCategories).map((category) => <option key={category} value={category}>{category}</option>)}</select></label>
              <label htmlFor="movement-amount" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldAmount')}<Input id="movement-amount" required min="0.01" step="0.01" name="amount" type="number" defaultValue={formTransaction ? Math.abs(formTransaction.amount).toFixed(2) : undefined} placeholder="0,00" className="h-10 bg-white" /></label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <label htmlFor="movement-account" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldAccount')}<select id="movement-account" name="account_name" required value={movementAccount} onChange={(event) => setMovementAccount(event.target.value)} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"><option value="">{t('noAccount')}</option>{accounts.filter(a => a.isActive !== false || (!!editingTransaction && a.name === formTransaction?.accountName)).map((account) => <option key={account.id} value={account.name}>{account.name}</option>)}</select></label>
              <label htmlFor="movement-destination" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDestinationAccount')}<select id="movement-destination" name="destination_name" required={isTransfer} disabled={!isTransfer} defaultValue={formTransaction?.destinationName ?? ''} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 disabled:bg-[#f4f5f1] disabled:text-[#a3adaa]"><option value="">{isTransfer ? t('none') : t('destinationOnlyForTransfers')}</option>{isTransfer && accounts.filter(a => (movementType !== 'Investment' || prelievoDaBroker || a.isBroker === true
              // Il valore gia' salvato resta sempre in elenco: una tendina che
              // non contiene il proprio valore lo perde al primo salvataggio.
              || a.name === formTransaction?.destinationName)
              && (a.isActive !== false || (!!editingTransaction && a.name === formTransaction?.destinationName))).map((account) => <option key={account.id} value={account.name}>{account.name}</option>)}</select></label>
            </div>
            {movementType === 'Expenses' && contoDebito && <div className="space-y-1.5 rounded-xl border border-[#bd5e46]/20 bg-[#fff9f6] p-3">
              {/* Una spesa su un conto di debito puo' essere un interesse, e
                  finche' non lo si dice non entra nei totali del debito: il
                  banner "da classificare" la segnala. Prima si deduceva dal
                  tipo e dal conto, e qualunque spesa finita li' diventava un
                  interesse senza che nulla lo segnalasse. */}
              <label htmlFor="movement-debt-interest" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDebtCharge')}
                <Input id="movement-debt-interest" name="debt_interest" type="number" min="0" step="0.01" placeholder={t('fieldDebtChargeNone')} defaultValue={formTransaction?.liabilitySplit?.interest ?? ''} />
              </label>
              <span className="block text-[11px] leading-4 text-[#7b8784]">{t('fieldDebtChargeHint')}</span>
            </div>}
            {movementType === 'Debt' && <div className="space-y-3 rounded-xl border border-[#bd5e46]/20 bg-[#fff9f6] p-3"><div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('principalShare')}<Input required min="0" step="0.01" name="debt_principal" type="number" defaultValue={formTransaction?.liabilitySplit?.principal ?? (origineDaDebito && formTransaction ? Math.abs(formTransaction.amount) : undefined)} /></label><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('interestShare')}<Input required min="0" step="0.01" name="debt_interest" type="number" defaultValue={formTransaction?.liabilitySplit?.interest ?? 0} /></label></div></div>}
            {!isSpostamento && <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="exclude_budget" defaultChecked={formTransaction?.countsInBudget === false} />{t('excludeBudget')}</label>}
            {(movementType === 'Income' || movementType === 'Expenses') && <RefundPicker apiUrl={apiUrl} initialId={editingTransaction?.refundOfId ?? null} transactionType={movementType} />}
            <label htmlFor="movement-goal" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldGoal')}<select id="movement-goal" name="goal" defaultValue={formTransaction?.goal ?? ''} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring focus:ring-3 focus:ring-ring/20"><option value="">{t('noGoal')}</option>{uniqueOptions(formTransaction?.goal ?? '', goalsData.items.map((goal) => goal.name)).map((goal) => <option key={goal} value={goal}>{goal}</option>)}</select></label>
            <label htmlFor="movement-details" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDescription')}<Input id="movement-details" name="details" defaultValue={formTransaction?.details ?? ''} placeholder={t('optionalNote')} className="h-10 bg-white" /></label>
            {/* Solo un Investimento si collega al ledger, e conta il tipo scelto
                ora nel modulo: la casella compariva anche su spese ed entrate,
                che il backend rifiuta. */}
            {!editingTransaction && movementType === 'Investment' && (
              <div className="space-y-2 rounded-lg border border-[#5c8f82]/20 bg-[#f6f9f7] p-3">
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-[#3b6a5b]">
                  <input
                    type="checkbox"
                    checked={linkLedgerOpen}
                    onChange={(event) => {
                      const next = event.target.checked;
                      setLinkLedgerOpen(next);
                      if (!next) setLinkedLedgerRows([]);
                      else if (linkedLedgerRows.length === 0) setLinkedLedgerRows([{ name: '', transactionType: 'Buy', amount: '', units: '', price: '', fee: '0', notes: '' }]);
                    }}
                    className="size-4 accent-[var(--money-primary)]"
                  />
                  {t('linkToLedger')}
                </label>
                {linkLedgerOpen && (
                  <div className="space-y-2">
                    <p className="text-[11px] text-[#52615d]">{t('linkToLedgerDesc')}</p>
                    {linkedLedgerRows.map((row, index) => (
                      <div key={index} className="space-y-2 rounded-md border border-black/8 bg-white p-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[11px] font-semibold text-[#52615d]">#{index + 1}</span>
                          <button
                            type="button"
                            onClick={() => setLinkedLedgerRows(linkedLedgerRows.filter((_, i) => i !== index))}
                            className="text-[11px] text-[#bd5e46] hover:underline"
                            aria-label={t('removeRow')}
                          >
                            {t('removeRow')}
                          </button>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                          <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('instrument')}
                            <Input list="strumenti-esistenti" autoComplete="off" value={row.name} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} className="h-8 bg-white text-xs" />
                          </label>
                          <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('operation')}
                            <select value={row.transactionType} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, transactionType: event.target.value as LedgerTypeDaMovimento } : item))} className="h-8 w-full rounded-md border border-input bg-white px-2 text-xs">
                              {LEDGER_TYPES_DA_MOVIMENTO.map((tipo) => <option key={tipo} value={tipo}>{t(LEDGER_TYPE_LABEL[tipo])}</option>)}
                            </select>
                          </label>
                        </div>
                        {SOLO_CONTANTE_DA_MOVIMENTO.includes(row.transactionType) ? (
                          // Quote e prezzo non vogliono dire niente per un dividendo
                          // o un versamento: al loro posto l'importo, come fa il
                          // modulo del ledger.
                          <div className="grid grid-cols-2 gap-2">
                            <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fieldAmount')}
                              <Input type="number" step="0.01" value={row.amount} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, amount: event.target.value } : item))} className="h-8 bg-white text-xs" />
                            </label>
                            <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fee')}
                              <Input type="number" step="0.01" value={row.fee} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, fee: event.target.value } : item))} className="h-8 bg-white text-xs" />
                            </label>
                          </div>
                        ) : (
                        <div className="grid grid-cols-3 gap-2">
                          <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('units')}
                            <Input type="number" step="0.00000001" value={row.units} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, units: event.target.value } : item))} className="h-8 bg-white text-xs" />
                          </label>
                          <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('priceLabel')}
                            <Input type="number" step="0.0001" value={row.price} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, price: event.target.value } : item))} className="h-8 bg-white text-xs" />
                          </label>
                          <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fee')}
                            <Input type="number" step="0.01" value={row.fee} onChange={(event) => setLinkedLedgerRows(linkedLedgerRows.map((item, i) => i === index ? { ...item, fee: event.target.value } : item))} className="h-8 bg-white text-xs" />
                          </label>
                        </div>
                        )}
                      </div>
                    ))}
                    <Button type="button" variant="outline" size="sm" onClick={() => setLinkedLedgerRows([...linkedLedgerRows, { name: '', transactionType: 'Buy', amount: '', units: '', price: '', fee: '0', notes: '' }])} className="h-7 text-xs">+ {t('addInstrument')}</Button>
                    {linkedLedgerRows.length > 0 && (() => {
                      // Il lordo di una riga di solo contante e' il suo importo: quote
                      // per prezzo darebbe zero e il riepilogo direbbe "0,00" sotto un
                      // dividendo da cento euro.
                      const total = linkedLedgerRows.reduce((acc, r) => acc + (SOLO_CONTANTE_DA_MOVIMENTO.includes(r.transactionType)
                        ? Number(r.amount || 0) : Number(r.units || 0) * Number(r.price || 0)), 0);
                      const fees = linkedLedgerRows.reduce((acc, r) => acc + Number(r.fee || 0), 0);
                      // Il preview della somma si appoggia sullo stato del form
                      // tenuto aggiornato dal onChange del form stesso.
                      const expected = effectivePreview.amount;
                      // Il denaro che le operazioni muovono: un acquisto costa
                      // prezzo piu' commissione, una vendita incassa prezzo meno
                      // commissione. Solo un'indicazione: il contante puo' restare
                      // sul broker, e il backend non impone la quadratura.
                      const netto = nettoOperazioni(linkedLedgerRows);
                      const mismatch = Math.abs(netto - expected) > 0.01;
                      return (
                        <p className={`text-[11px] ${mismatch ? 'text-[#a94f3a]' : 'text-[#3b6a5b]'}`}>
                          {t('ledgerTotalPreview', {
                            sum: total.toFixed(2),
                            fees: fees.toFixed(2),
                            amount: expected.toFixed(2),
                          })}
                        </p>
                      );
                    })()}
                  </div>
                )}
              </div>
            )}
            {/* Solo un Investment puo' essere agganciato al ledger: mostrare il
                pannello sugli altri tipi offriva un'azione che il backend
                rifiuta, e non c'e' niente di piu' inutile di un pulsante che
                dice sempre di no. */}
            {editingTransaction && movementType === 'Investment' && (() => {
              // La quadratura la calcola il backend sul gruppo connesso: qui si
              // legge e basta, perche' rifarla lato client con i soli dati di
              // questo movimento darebbe un falso scarto quando un'operazione
              // e' finanziata da due bonifici.
              const atteso = linkedBalance?.transfers ?? 0;
              const nettoOperazioni = linkedBalance?.operations ?? 0;
              const scarto = linkedBalance?.difference ?? 0;
              return (
              <div className="space-y-2 rounded-lg border border-[#5c8f82]/20 bg-[#f6f9f7] p-3">
                {/* Titolo e pulsanti su due righe: il dialogo e' largo 448px e
                    su una riga sola i due pulsanti uscivano dal riquadro. */}
                <p className="text-xs font-semibold text-[#3b6a5b]">{t('linkedLedgerTitle')}</p>
                {editingTransaction.transactionType !== 'Investment' && <p className="text-[11px] leading-4 text-[#52615d]">{t('linkSavesAsInvestment')}</p>}
                <div className="flex flex-wrap gap-1.5">
                  <Button type="button" variant="outline" size="sm" disabled={linkEditingBusy} onClick={() => { setNuovaOpAperta(true); setNuovaOpImporto(Math.abs(scarto).toFixed(2)); }} className="h-7 flex-1 px-2 text-xs">+ {t('createLedgerShort')}</Button>
                  <Button type="button" variant="outline" size="sm" disabled={linkEditingBusy} onClick={openLinkPicker} className="h-7 flex-1 px-2 text-xs">+ {t('linkExistingShort')}</Button>
                </div>
                {/* La quadratura, sempre a video: un bonifico e le operazioni che
                    ha finanziato devono dire la stessa cifra, e quando non lo
                    fanno si deve vedere subito di quanto. */}
                <div className={`grid grid-cols-3 gap-2 rounded-md px-2 py-1.5 text-[10px] ${Math.abs(scarto) < 0.005 ? 'bg-[#e5f3ed] text-[#2d7b65]' : 'bg-[#fdf3e7] text-[#9a6320]'}`}>
                  <span className="min-w-0">{t('linkTransferAmount')}<br /><b className="tabular-nums text-xs">{formatEuro(atteso)}</b></span>
                  <span className="min-w-0">{t('linkOperationsTotal')}<br /><b className="tabular-nums text-xs">{formatEuro(nettoOperazioni)}</b></span>
                  <span className="min-w-0">{Math.abs(scarto) < 0.005
                    ? <><br /><b className="text-xs">{t('linkBalanced')}</b></>
                    : <>{t('linkDifference')}<br /><b className="tabular-nums text-xs">{scarto > 0 ? '+' : ''}{formatEuro(scarto)}</b></>}</span>
                </div>
                {nuovaOpAperta && (
                  <div className="space-y-2 rounded-md border border-[#5c8f82]/30 bg-white p-2">
                    <p className="text-[11px] font-semibold text-[#3b6a5b]">{t('createLedgerOperation')}</p>
                    <p className="text-[10px] leading-4 text-[#87918e]">{t('createLedgerOperationHint')}</p>
                    <div className="grid grid-cols-2 gap-2">
                      <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('instrument')}
                        <Input list="strumenti-esistenti" autoComplete="off" value={nuovaOpNome} onChange={(e) => setNuovaOpNome(e.target.value)} className="h-8 bg-white text-xs" /></label>
                      <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('operation')}
                        <select value={nuovaOpTipo} onChange={(e) => setNuovaOpTipo(e.target.value as LedgerTypeDaMovimento)} className="h-8 w-full rounded-md border border-input bg-white px-2 text-xs">{LEDGER_TYPES_DA_MOVIMENTO.map((tipo) => <option key={tipo} value={tipo}>{t(LEDGER_TYPE_LABEL[tipo])}</option>)}</select></label>
                      <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fieldAmount')}
                        <Input type="number" step="0.01" value={nuovaOpImporto} onChange={(e) => setNuovaOpImporto(e.target.value)} className="h-8 bg-white text-xs" /></label>
                      {!SOLO_CONTANTE_DA_MOVIMENTO.includes(nuovaOpTipo) && (
                        <label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('units')}
                          <Input type="number" step="0.00000001" value={nuovaOpQuote} onChange={(e) => setNuovaOpQuote(e.target.value)} className="h-8 bg-white text-xs" /></label>
                      )}
                    </div>
                    <div className="flex justify-end gap-1.5">
                      <Button type="button" variant="ghost" size="sm" onClick={() => setNuovaOpAperta(false)} className="h-7 text-xs">{t('cancel')}</Button>
                      <Button type="button" size="sm" disabled={linkEditingBusy || !nuovaOpNome.trim() || !Number(nuovaOpImporto)} onClick={() => void creaEcollegaOperazione()} className="h-7 bg-[var(--money-primary)] text-xs text-white">{t('createAndLink')}</Button>
                    </div>
                  </div>
                )}
                {linkedLedgerItems.length === 0 ? (
                  <p className="text-[11px] text-[#52615d]">{t('noLinkedOperations')}</p>
                ) : (
                  <ul className="space-y-1.5">
                    {linkedLedgerItems.map((item) => (
                      <li key={item.linkId} className="flex items-start justify-between gap-2 rounded-md border border-black/8 bg-white p-2">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs font-medium">{item.name} <span className="ml-1 rounded-full bg-[#edf0ed] px-1.5 py-0.5 text-[10px] font-normal text-[#5d716b]">{item.transactionType}</span></p>
                          <p className="mt-0.5 text-[10px] text-[#87918e]">{item.occurredOn} · <b className="font-medium tabular-nums text-[#52615d]">{formatEuro(item.transactionType === 'Sell' ? -Math.abs(item.amount) : Math.abs(item.amount))}</b>{item.units != null ? ` · ${item.units} @ ${item.price}` : ''}</p>
                        </div>
                        <Button type="button" variant="ghost" size="icon" title={t('unlinkOperation')} aria-label={`${t('unlinkOperation')} ${item.name}`} disabled={linkEditingBusy} onClick={() => void unlinkLedgerRow(item.linkId)} className="size-7 shrink-0 text-[#bd5e46]"><Unlink className="size-3.5" /></Button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              );
            })()}
            {linkPickerOpen && (
              <div className="rounded-lg border border-[#5c8f82]/30 bg-white p-3">
                <div className="mb-2 flex items-center justify-between">
                  <p className="text-xs font-semibold text-[#3b6a5b]">{t('linkExistingOperation')}</p>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setLinkPickerOpen(false)} className="h-7 text-xs">{t('cancel')}</Button>
                </div>
                {linkPickerBusy ? (
                  <p className="text-[11px] text-[#52615d]">{t('loadingEllipsis')}</p>
                ) : linkPickerLedger.length === 0 ? (
                  <p className="text-[11px] text-[#52615d]">{t('allOperationsLinked')}</p>
                ) : (() => {
                  // Cerca su nome, data e importo insieme: quello che uno ha
                  // sott'occhio guardando un bonifico e' la cifra o il giorno,
                  // non sempre il nome dello strumento.
                  const cercato = linkPickerQuery.trim().toLowerCase();
                  const trovati = cercato
                    ? linkPickerLedger.filter((item) => `${item.name} ${item.occurredOn} ${item.amount} ${item.transactionType}`.toLowerCase().includes(cercato))
                    : linkPickerLedger;
                  return <>
                    <div className="relative mb-2">
                      <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-black/35" />
                      <Input aria-label={t('searchOperation')} value={linkPickerQuery} onChange={(event) => setLinkPickerQuery(event.target.value)} className="h-8 bg-[#fafaf8] pl-8 text-xs" placeholder={t('searchOperationPlaceholder')} />
                    </div>
                    {trovati.length === 0 ? <p className="text-[11px] text-[#52615d]">{t('noOperationsMatch')}</p> : <>
                      <ul className="max-h-48 space-y-1 overflow-auto">
                        {trovati.slice(0, 50).map((item) => (
                          <li key={item.id}>
                            <Button type="button" variant="ghost" disabled={linkEditingBusy} onClick={() => void linkExistingLedger(item.id)} className="h-auto w-full justify-between whitespace-normal rounded-md border border-black/5 bg-white px-2 py-1.5 text-left text-xs hover:bg-[#f4f5f1]">
                              <span className="min-w-0 flex-1 truncate">{item.name} <span className="ml-1 text-[10px] text-[#87918e]">{item.transactionType}</span></span>
                              <span className="ml-2 shrink-0 text-[10px] text-[#87918e]">{item.occurredOn} · {item.amount} {item.currency}</span>
                            </Button>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1.5 text-[10px] text-[#87918e]">{trovati.length > 50 ? t('operationsShownOfMatching', { shown: 50, total: trovati.length }) : t('operationsToLink', { count: trovati.length })}</p>
                    </>}
                  </>;
                })()}
              </div>
            )}
            {saveError && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{saveError}</p>}
            <DialogFooter className="mx-0 mb-0 mt-5 border-0 bg-transparent p-0">
              <Button type="button" variant="outline" onClick={() => setNewTransactionOpen(false)}>{t('cancel')}</Button>
              <Button type="submit" disabled={saving} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{saving ? t('savingEllipsis') : editingTransaction ? t('saveChanges') : duplicatingTransaction ? t('createCopy') : t('saveMovement')}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={transferOpen} onOpenChange={setTransferOpen}>
        <DialogContent className="max-w-md gap-5 p-6">
          <DialogHeader><DialogTitle className="text-lg">{t(debtTransferMode === 'repayment' ? 'registerDebtPayment' : debtTransferMode === 'drawdown' ? 'registerDebtDrawdown' : 'transferBetweenAccounts')}</DialogTitle><DialogDescription>{t(debtTransferMode ? 'debtTransferDesc' : 'transferDesc')}</DialogDescription></DialogHeader>
          <form className="space-y-4" onSubmit={handleCreateTransfer}>
            <label htmlFor="transfer-date" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDate')}<Input id="transfer-date" required name="occurred_on" type="date" defaultValue={new Date().toISOString().slice(0, 10)} className="h-10 bg-white" /></label>
            <label htmlFor="transfer-source" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldFrom')}<select id="transfer-source" required name="source_account" value={transferSource} onChange={(event) => setTransferSource(event.target.value)} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm"><option value="" disabled>{t('selectAccount')}</option>{accounts.filter(a => a.isActive !== false).map((account) => <option key={account.id} value={account.name}>{account.name}</option>)}</select></label>
            <label htmlFor="transfer-destination" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldTo')}<select id="transfer-destination" required name="destination_account" value={transferDestination} onChange={(event) => setTransferDestination(event.target.value)} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm"><option value="" disabled>{t('selectAccount')}</option>{accounts.filter(a => a.isActive !== false).map((account) => <option key={account.id} value={account.name}>{account.name}</option>)}</select></label>
            {debtTransferMode === 'repayment' ? <><div className="grid grid-cols-2 gap-3"><label htmlFor="transfer-principal" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('principalShare')}<Input id="transfer-principal" required min="0" step="0.01" name="principal_amount" type="number" value={transferPrincipal} onChange={(event) => setTransferPrincipal(event.target.value)} placeholder="0,00" /></label><label htmlFor="transfer-interest" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('interestShare')}<Input id="transfer-interest" required min="0" step="0.01" name="interest_amount" type="number" value={transferInterest} onChange={(event) => setTransferInterest(event.target.value)} /></label></div><p className="rounded-lg bg-[#f4f5f1] px-3 py-2 text-xs text-[#52615d]">{t('debtPaymentTotal', { amount: formatEuro(Number(transferPrincipal || 0) + Number(transferInterest || 0)) })}</p></> : <label htmlFor="transfer-amount" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t(debtTransferMode === 'drawdown' ? 'principalShare' : 'fieldAmount')}<Input id="transfer-amount" required min="0.01" step="0.01" name="amount" type="number" value={transferAmount} onChange={(event) => setTransferAmount(event.target.value)} placeholder="0,00" className="h-10 bg-white" /></label>}
            <label htmlFor="transfer-details" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDescription')}<Input id="transfer-details" name="details" placeholder={t('optionalNote')} className="h-10 bg-white" /></label>
            {saveError && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{saveError}</p>}
            <DialogFooter className="mx-0 mb-0 mt-5 border-0 bg-transparent p-0"><Button type="button" variant="outline" onClick={() => setTransferOpen(false)}>{t('cancel')}</Button><Button type="submit" disabled={saving} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{saving ? t('savingEllipsis') : t(debtTransferMode === 'repayment' ? 'registerDebtPayment' : debtTransferMode === 'drawdown' ? 'registerDebtDrawdown' : 'registerTransfer')}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={valuationForId !== null} onOpenChange={(open) => { if (!open) { setValuationForId(null); setAccountError(''); } }}>
        <DialogContent className="max-w-lg gap-5 p-6">
          {(() => {
            const conto = accounts.find((c) => c.id === valuationForId);
            if (!conto) return null;
            const stime = conto.valuations ?? [];
            return <>
              <DialogHeader>
                <DialogTitle className="text-lg">{t('valuationsTitle')} · {conto.name}</DialogTitle>
                <DialogDescription>{t('valuationsDialogDesc')}</DialogDescription>
              </DialogHeader>
              {stime.length === 0
                ? <p className="rounded-lg bg-[#fafaf8] px-3 py-4 text-center text-xs text-[#87918e]">{t('valuationsEmpty')}</p>
                : <ul className="divide-y divide-black/5 rounded-lg border border-black/6">
                    {stime.map((stima) => <li key={stima.id} className="flex items-center gap-3 px-3 py-2.5">
                      <span className="w-24 shrink-0 text-xs tabular-nums text-[#71807c]">{stima.observedOn}</span>
                      <span className="flex-1 text-sm font-semibold tabular-nums">{formatEuro(stima.value)}</span>
                      {stima.notes && <span className="truncate text-xs text-[#87918e]">{stima.notes}</span>}
                      <Button size="icon" variant="ghost" aria-label={`${t('delete')} ${stima.observedOn}`} onClick={() => void handleValuationDelete(stima.id)} className="text-[#bd5e46] hover:text-[#a04f3a]"><Trash2 className="size-4" /></Button>
                    </li>)}
                  </ul>}
              <form className="space-y-3" onSubmit={handleValuationSave}>
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDate')}<Input required name="observed_on" type="date" defaultValue={new Date().toISOString().slice(0, 10)} className="h-10 bg-white" /></label>
                  <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('value')}<Input required name="value" type="number" step="0.01" className="h-10 bg-white" /></label>
                </div>
                <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('note')}<Input name="notes" placeholder={t('optional')} className="h-10 bg-white" /></label>
                {accountError && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{accountError}</p>}
                <DialogFooter className="mx-0 mb-0 mt-4 border-0 bg-transparent p-0">
                  <Button type="button" variant="outline" onClick={() => setValuationForId(null)}>{t('close')}</Button>
                  <Button type="submit" disabled={accountSaving} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{accountSaving ? t('savingEllipsis') : t('addValuation')}</Button>
                </DialogFooter>
              </form>
            </>;
          })()}
        </DialogContent>
      </Dialog>
      <Dialog open={accountDialog !== null} onOpenChange={(open) => { if (!open) { setAccountDialog(null); setAccountError(''); } }}>
        <DialogContent className="max-w-md gap-5 p-6">
          {(() => {
            const conto = accountDialog !== null && accountDialog !== 'new' ? accountDialog : null;
            return <>
              <DialogHeader>
                <DialogTitle className="text-lg">{conto ? t('editAccountTitle') : t('newAccountTitle')}</DialogTitle>
                <DialogDescription>{conto ? t('editAccountDesc') : t('accountDialogDesc')}</DialogDescription>
              </DialogHeader>
              {/* La key rimonta il form quando si passa da un conto all'altro:
                  senza, i defaultValue resterebbero quelli del conto di prima. */}
              <form key={conto?.id ?? 'new'} className="space-y-4" onSubmit={handleAccountSave}>
                <label htmlFor="account-name" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldName')}<Input id="account-name" required name="name" defaultValue={conto?.name ?? ''} className="h-10 bg-white" /></label>
                <label htmlFor="account-group" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldGroup')}<select id="account-group" required name="source_group" defaultValue={conto?.group ?? 'bank'} onChange={(event) => setAccountGroup(event.target.value as Account['group'])} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm">
                  <option value="bank">{t('groupBank')}</option>
                  <option value="asset">{t('groupAsset')}</option>
                  <option value="liability">{t('groupLiability')}</option>
                </select></label>
                <label htmlFor="account-balance" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldInitialBalance')}<Input id="account-balance" name="starting_balance" type="number" step="0.01" defaultValue={conto ? String(conto.startingBalance) : '0'} className="h-10 bg-white" /></label>
                <label htmlFor="account-notes" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('note')}<textarea id="account-notes" name="notes" defaultValue={conto?.notes ?? ''} className="min-h-20 w-full rounded-lg border border-input bg-white p-2.5 text-sm" /></label>
                <label className="flex items-center gap-2.5 text-xs font-medium text-[#52615d]"><input type="checkbox" name="counts_in_net_worth" defaultChecked={conto ? conto.countsInNetWorth !== false : true} className="size-4 accent-[var(--money-primary)]" />{t('fieldCountsInNetWorth')}</label>
                {accountGroup === 'asset' && <label title={t('liquidityExplanation')} className="flex items-center gap-2.5 text-xs font-medium text-[#52615d]"><input type="checkbox" name="is_liquid" defaultChecked={conto?.isLiquid === true} className="size-4 accent-[var(--money-primary)]" />{t('fieldLiquid')}</label>}
                {accountGroup === 'asset' && <label className="block space-y-1 text-xs font-medium text-[#52615d]"><span className="flex items-center gap-2.5"><input type="checkbox" name="is_broker" defaultChecked={conto?.isBroker === true} className="size-4 accent-[var(--money-primary)]" />{t('fieldBroker')}</span><span className="block pl-6.5 pt-1 font-normal leading-5 text-[#7b8784]">{t('brokerHint')}</span></label>}
                {accountGroup === 'asset' && <label className="block space-y-1 text-xs font-medium text-[#52615d]"><span className="flex items-center gap-2.5"><input type="checkbox" name="needs_manual_valuation" defaultChecked={conto?.needsManualValuation === true} className="size-4 accent-[var(--money-primary)]" />{t('fieldManualValuation')}</span><span className="block pl-6.5 pt-1 font-normal leading-5 text-[#7b8784]">{t('manualValuationHint')}</span></label>}
                {conto && <label className="flex items-center gap-2.5 text-xs font-medium text-[#52615d]"><input type="checkbox" name="is_archived" defaultChecked={conto.isActive === false} className="size-4 accent-[var(--money-primary)]" />{t('fieldArchived')}</label>}
                {accountError && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{accountError}</p>}
                <DialogFooter className="mx-0 mb-0 mt-5 border-0 bg-transparent p-0">
                  <Button type="button" variant="outline" onClick={() => setAccountDialog(null)}>{t('cancel')}</Button>
                  <Button type="submit" disabled={accountSaving} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{accountSaving ? t('savingEllipsis') : t('save')}</Button>
                </DialogFooter>
              </form>
            </>;
          })()}
        </DialogContent>
      </Dialog>
      {daDividere && <SplitTransactionDialog transaction={daDividere} accounts={accounts} apiUrl={apiUrl}
        categoriesByType={settingsData.categoriesByType} onClose={() => setDaDividere(null)}
        onDone={async () => {
          setDaDividere(null);
          await loadDataRef.current(undefined, ['ledger', 'overview', 'budget', 'goals']).catch(() => undefined);
          setMovimentiVersione((versione) => versione + 1);
        }} />}
    </main>
  );
}

function PeriodSelector({ value, years, onChange, allowMonth = true, allowYear = true, compareTo, onCompareToChange }: {
  value: PeriodSelection;
  years: string[];
  onChange: (period: PeriodSelection) => void;
  allowMonth?: boolean;
  allowYear?: boolean;
  compareTo?: 'none' | 'prior_period' | 'prior_year';
  onCompareToChange?: (value: 'none' | 'prior_period' | 'prior_year') => void;
}) {
  const { t, monthNames } = useI18n();
  const availableYears = [...new Set([...years.map(Number).filter(Number.isFinite), value.year, OGGI.getFullYear()])].sort((a, b) => a - b);
  const scope = allowMonth ? (allowYear ? value.scope : 'month') : 'year';
  const previousYear = [...availableYears].reverse().find((year) => year < value.year);
  const nextYear = availableYears.find((year) => year > value.year);
  const atMin = scope === 'year' ? previousYear === undefined : value.month > 1 ? false : previousYear === undefined;
  const atMax = scope === 'year' ? nextYear === undefined : value.month < 12 ? false : nextYear === undefined;
  const periodLabel = scope === 'year' ? String(value.year) : formatPeriodRef(monthNames, value.year, value.month);
  const move = (direction: -1 | 1) => {
    if (scope === 'year') {
      const year = direction < 0 ? previousYear : nextYear;
      if (year !== undefined) onChange({ ...value, year });
    } else if (direction < 0 && value.month === 1 && previousYear !== undefined) {
      onChange({ ...value, year: previousYear, month: 12 });
    } else if (direction > 0 && value.month === 12 && nextYear !== undefined) {
      onChange({ ...value, year: nextYear, month: 1 });
    } else if ((direction < 0 && value.month > 1) || (direction > 0 && value.month < 12)) {
      onChange({ ...value, month: value.month + direction });
    }
  };
  return <div role="toolbar" aria-label={t('period')} tabIndex={0} onKeyDown={(event) => { if (event.target !== event.currentTarget) return; if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1); } }} className="flex flex-wrap items-center gap-1 rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-[var(--money-primary)]/40">
    <div className="flex flex-nowrap items-center gap-1">
    <button type="button" aria-label={t('previousPeriod')} onClick={() => move(-1)} disabled={atMin} className="grid size-10 shrink-0 place-items-center rounded-xl border border-black/7 bg-white text-[#52615d] shadow-sm shadow-black/[0.02] transition hover:bg-[#f4f5f1] disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft className="size-4" /></button>
    <label className="relative"><span className="sr-only">{t('year')}</span><select aria-label={t('year')} value={value.year} onChange={(event) => onChange({ ...value, year: Number(event.target.value) })} className="h-10 appearance-none rounded-xl border border-black/7 bg-white py-0 pl-3.5 pr-9 text-sm font-medium shadow-sm outline-none focus:border-[#5c8f82]">{availableYears.map((year) => <option key={year} value={year}>{year}</option>)}</select><ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-black/45" /></label>
    {allowMonth && <label className="relative"><span className="sr-only">{t('period')}</span><select aria-label={t('period')} value={scope === 'year' ? 'year' : value.month} onChange={(event) => { const annual = event.target.value === 'year'; onChange({ ...value, scope: annual ? 'year' : 'month', month: annual ? value.month : Number(event.target.value) }); if (annual && compareTo === 'prior_period') onCompareToChange?.('prior_year'); }} className="h-10 appearance-none rounded-xl border border-black/7 bg-white py-0 pl-3.5 pr-9 text-sm font-medium shadow-sm outline-none focus:border-[#5c8f82]">{allowYear && <option value="year">{t('wholeYear')}</option>}{monthNames.map((name, index) => <option key={name} value={index + 1}>{name}</option>)}</select><ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-black/45" /></label>}
    <button type="button" aria-label={t('nextPeriod')} onClick={() => move(1)} disabled={atMax} className="grid size-10 shrink-0 place-items-center rounded-xl border border-black/7 bg-white text-[#52615d] shadow-sm shadow-black/[0.02] transition hover:bg-[#f4f5f1] disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight className="size-4" /></button>
    <button type="button" onClick={() => onChange({ year: OGGI.getFullYear(), month: OGGI.getMonth() + 1, scope: allowMonth ? 'month' : value.scope })} disabled={allowMonth ? scope === 'month' && value.year === OGGI.getFullYear() && value.month === OGGI.getMonth() + 1 : value.year === OGGI.getFullYear()} className="h-10 rounded-xl border border-black/7 bg-white px-3 text-sm font-medium text-[#52615d] shadow-sm hover:bg-[#f4f5f1] disabled:opacity-40">{t('today')}</button>
    </div>
    {compareTo !== undefined && onCompareToChange && <label className="relative ml-1"><span className="sr-only">{t('compareWith')}</span><select aria-label={t('compareWith')} value={compareTo} onChange={(event) => onCompareToChange(event.target.value as 'none' | 'prior_period' | 'prior_year')} className="h-10 appearance-none rounded-xl border border-black/7 bg-white py-0 pl-3.5 pr-9 text-sm font-medium shadow-sm outline-none focus:border-[#5c8f82]"><option value="prior_year">{scope === 'year' ? t('priorYear') : t('vsSamePeriodPriorYear', { unit: t('monthUnit') })}</option>{scope === 'month' && <option value="prior_period">{t('vsPriorPeriod', { label: t('priorMonth').toLowerCase() })}</option>}<option value="none">{t('noComparisonOption')}</option></select><ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-black/45" /></label>}
    <output className="sr-only" aria-live="polite">{t('selectedPeriodAnnouncement', { period: periodLabel })}</output>
  </div>;
}

const TREND_COLORS = ['#ef8e72', '#6d8ff4', '#47a889', '#9479d1'];

// Il tetto non e' un numero scelto a caso: e' quanti colori distinti ha la
// tavolozza. Oltre il quarto anno il quinto riprendeva il colore del primo e
// due linee diventavano indistinguibili, legenda compresa.
const MAX_ANNI_CONFRONTO = TREND_COLORS.length;

function YearComparisonSelector({ availableYears, selected, onChange }: { availableYears: number[]; selected: number[]; onChange: (years: number[]) => void }) {
  const { t } = useI18n();
  if (!availableYears.length) {
    return <p className="text-xs text-[#71807c]">{t('trendsNoYears')}</p>;
  }
  const alTetto = selected.length >= MAX_ANNI_CONFRONTO;
  const toggle = (year: number) => {
    if (selected.includes(year)) {
      // Almeno un anno deve restare: un grafico senza serie non dice niente.
      if (selected.length > 1) onChange(selected.filter((y) => y !== year));
    } else if (!alTetto) {
      onChange([...selected, year].sort((a, b) => a - b));
    }
  };
  // Gli anni disponibili crescono di uno ogni anno: a vista occuperebbero
  // sempre piu' intestazione. Dietro un pannello lo spazio resta quello.
  // <details> e' l'elemento del browser per questo: niente libreria, e la
  // tastiera lo apre e lo chiude da sola.
  return (
    <details className="group relative">
      <summary className="flex h-10 cursor-pointer list-none items-center gap-2 rounded-xl border border-black/7 bg-white px-3.5 text-sm font-medium shadow-sm shadow-black/[0.02] transition hover:bg-[#f8f9f6] [&::-webkit-details-marker]:hidden">
        <span className="text-[#61706c]">{t('trendsCompareYears')}:</span>
        <span className="tabular-nums">{selected.join(', ')}</span>
        <ChevronDown className="size-4 shrink-0 text-black/45 transition group-open:rotate-180" />
      </summary>
      <div className="absolute right-0 z-20 mt-1.5 w-max max-w-[20rem] rounded-xl border border-black/7 bg-white p-2.5 shadow-lg shadow-black/[0.08]">
        <div className="flex flex-wrap gap-1.5">
          {availableYears.map((year) => {
            const active = selected.includes(year);
            // Al tetto restano premibili solo quelli gia' scelti, per poterli
            // togliere: gli altri si spengono invece di rifiutare il clic.
            const bloccato = !active && alTetto;
            return <button key={year} type="button" aria-pressed={active} disabled={bloccato} onClick={() => toggle(year)} className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${active ? 'border-[var(--money-deep)] bg-[var(--money-deep)] text-white' : bloccato ? 'cursor-not-allowed border-black/5 bg-[#fafaf8] text-[#b6bdba]' : 'border-black/8 bg-white text-[#61706c] hover:bg-[#f0f2ee]'}`}>{year}</button>;
          })}
        </div>
        <p className="mt-2 px-0.5 text-[11px] leading-4 text-[#87918e]">{t('trendsMaxYears', { max: MAX_ANNI_CONFRONTO })}</p>
      </div>
      <output className="sr-only" aria-live="polite">{t('selectedYearsAnnouncement', { years: selected.join(', ') })}</output>
    </details>
  );
}

function SectionView({
  section,
  summary,
  movimentiVersione,
  inCorso,
  accounts,
  budgetData,
  budgetSuggestions,
  onCategoryGroupChange,
  annualBudgetData,
  budgetDashboardData,
  budgetTrendsData,
  budgetLoadFailed,
  trendsLoadFailed,
  goalsData,
  netWorthData,
  investmentDashboardData,
  investmentLedger,
  investmentAllocationData,
  calculationData,
  searchQuery,
  onSearchChange,
  onNewTransaction,
  onTransfer,
  onEditTransaction,
  onDuplicateTransaction,
  onDeleteTransaction,
  onSplitTransaction,
  onBudgetUpdate,
  onBudgetCreate,
  onBudgetDelete,
  onBudgetCopy,
  onAnnualBudgetApply,
  onGoalSave,
  onGoalDelete,
  onInvestmentSave,
  onInvestmentDelete,
  onInstrumentSave,
  onMarketRefresh,
  notesData,
  onNoteSave,
  onNoteDelete,
  onNewAccount,
  onNavigate,
  onAccountEdit,
  onAccountValuations,
  onAccountDelete,
  recurringTransactions,
  onCreateRecurring,
  onDeleteRecurring,
  onGenerateRecurring,
  categorizationRules,
  categorieRegola,
  onCategoryRulesChanged,
  onDownloadReport,
  onDownloadData,
  apiUrl,
  onReloadData,
  selectedYear,
  selectedMonth,
  period,
  years,
  onPeriodChange,
  budgetType,
  onBudgetTypeChange,
  budgetView,
  onBudgetViewChange,
  settingsData,
  onSettingChange,
  settingSaving,
  settingError,
  onReload,
  account,
  onAccountChanged,
  importing,
  onImportData,
  importFeedback,
  downloadBusy,
  downloadError,
  canManageBackups,
  pdfImporting,
  onPdfImport,
  onCsvImport,
  showPdfPreview,
  pdfPreviewTransactions,
  onPdfImportConfirm,
  onPdfImportCancel,
  connected,
  trendYearsAvailable,
  trendYears,
  onTrendYearsChange,
}: {
  section: Exclude<Section, 'Panoramica'>;
  onNavigate: (section: Section) => void;
  summary: Summary;
  movimentiVersione: number;
  inCorso: number;
  accounts: Account[];
  budgetData: BudgetData;
  budgetSuggestions: BudgetSuggestion[];
  onCategoryGroupChange: (category: string, categoryGroup: string) => Promise<void>;
  annualBudgetData: AnnualBudgetData;
  budgetDashboardData: BudgetDashboardData | null;
  budgetTrendsData: BudgetTrendsData;
  budgetLoadFailed: boolean;
  trendsLoadFailed: boolean;
  goalsData: GoalsData;
  netWorthData: NetWorthData;
  investmentDashboardData: InvestmentDashboardData;
  investmentLedger: InvestmentTransaction[];
  investmentAllocationData: InvestmentAllocationData;
  calculationData: CalculationData | null;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  onNewTransaction: () => void;
  onTransfer: (destination?: string) => void;
  onEditTransaction: (transaction: Transaction) => void;
  onDuplicateTransaction: (transaction: Transaction) => void;
  onDeleteTransaction: (transaction: Transaction) => Promise<void>;
  onSplitTransaction: (transaction: Transaction) => void;
  onBudgetUpdate: (id: number, payload: { category?: string; amount?: number }) => Promise<void>;
  onBudgetCreate: (category: string, amount: number) => Promise<void>;
  onBudgetDelete: (id: number) => Promise<void>;
  onBudgetCopy: (mode: 'month' | 'year') => Promise<void>;
  onAnnualBudgetApply: (category: string, months: number[], amount: number, categoryGroup?: string | null) => Promise<void>;
  onGoalSave: (goalId: number | null, payload: Record<string, string | number | null>) => Promise<void>;
  onGoalDelete: (goal: GoalData) => Promise<void>;
  onInvestmentSave: (transactionId: number | null, payload: Record<string, string | number | boolean>) => Promise<void>;
  onInvestmentDelete: (transaction: InvestmentTransaction) => Promise<void>;
  onInstrumentSave: (instrumentId: number, payload: Record<string, string | number | null>) => Promise<void>;
  onMarketRefresh: () => Promise<{ updated: number; errors: Array<{ code?: string; error?: string }> }>;
  notesData: NoteData[];
  onNoteSave: (noteId: number | null, payload: Record<string, string | null>) => Promise<void>;
  onNoteDelete: (note: NoteData) => Promise<void>;
  onNewAccount: (group?: Account['group']) => void;
  onAccountEdit: (account: Account) => void;
  onAccountValuations: (account: Account) => void;
  onAccountDelete: (account: Account) => Promise<void>;
  recurringTransactions: RecurringTransactionData[];
  onCreateRecurring: (payload: Record<string, string | number | boolean | null>) => Promise<void>;
  onDeleteRecurring: (id: number) => Promise<void>;
  onGenerateRecurring: (upTo: string) => Promise<number>;
  categorizationRules: CategorizationRuleData[];
  categorieRegola: string[];
  // Ricarica solo le regole: un salvataggio riuscito non deve riportare a
  // casa movimenti e conti, e la pagina non deve lampeggiare.
  onCategoryRulesChanged: () => void;
  onDownloadReport: (kind: 'excel' | 'pdf') => void;
  onDownloadData: () => void;
  apiUrl: string;
  onReloadData: () => Promise<void>;
  selectedYear: number;
  selectedMonth: number;
  period: PeriodSelection;
  years: string[];
  onPeriodChange: (value: PeriodSelection) => void;
  budgetType: 'Expenses' | 'Income' | 'Savings';
  onBudgetTypeChange: (value: 'Expenses' | 'Income' | 'Savings') => void;
  budgetView: 'dashboard' | 'trends' | 'plan';
  onBudgetViewChange: (value: 'dashboard' | 'trends' | 'plan') => void;
  settingsData: SettingsData;
  onSettingChange: (key: string, value: string) => Promise<void>;
  settingSaving: string;
  settingError: string;
  onReload: () => Promise<void>;
  account: AccountState | null;
  onAccountChanged: () => Promise<void>;
  importing: boolean;
  onImportData: (file: File) => Promise<string>;
  importFeedback: { ok: boolean; message: string } | null;
  downloadBusy: boolean;
  downloadError: string;
  canManageBackups: boolean;
  pdfImporting: boolean;
  onPdfImport: () => Promise<void>;
  onCsvImport: () => Promise<void>;
  showPdfPreview: boolean;
  pdfPreviewTransactions: PDFTransaction[];
  onPdfImportConfirm: (transactions: PDFTransaction[]) => Promise<void>;
  onPdfImportCancel: () => void;
  connected: boolean;
  trendYearsAvailable: number[];
  trendYears: number[];
  onTrendYearsChange: (years: number[]) => void;
}) {
  const { t, lang, locale, formatEuro, monthNames } = useI18n();
  const [transactionTypeFilter, setTransactionTypeFilter] = useState('all');
  const [incompleteOnly, setIncompleteOnly] = useState(false);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [bulkField, setBulkField] = useState('category');
  const [bulkValue, setBulkValue] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkError, setBulkError] = useState('');
  // Quanti ne ha trovati il filtro quando la selezione e' stata troncata dal
  // tetto del server. Zero quando li ha presi tutti.
  const [selezioneTroncata, setSelezioneTroncata] = useState(0);
  const [refundError, setRefundError] = useState('');
  const filteredParams = () => {
    const params = new URLSearchParams();
    if (ricerca.trim()) params.set('search', ricerca.trim());
    if (transactionTypeFilter !== 'all') params.set('transaction_type', transactionTypeFilter);
    if (accountFilter !== 'all') params.set('account_name', accountFilter);
    if (goalFilter !== 'all') params.set('goal', goalFilter);
    if (yearFilter !== 'all') params.set('year', yearFilter);
    if (monthFilter !== 'all') params.set('month', String(Number(monthFilter)));
    if (incompleteOnly) params.set('incomplete', 'true');
    return params;
  };
  const selectAll = async () => {
    setBulkBusy(true); setBulkError('');
    try {
      const params = filteredParams(); params.set('ids_only', 'true');
      const response = await fetch(`${apiUrl}/api/transactions?${params}`);
      if (!response.ok) throw new Error();
      const data = await response.json() as { ids: number[]; total: number };
      setSelection(new Set(data.ids.map(String)));
      setSelezioneTroncata(data.total > data.ids.length ? data.total : 0);
    } catch { setBulkError(t('bulkFailed')); } finally { setBulkBusy(false); }
  };
  const openRefundedTransaction = useCallback(async (transaction: Transaction) => {
    setRefundError('');
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${transaction.refundedById}`);
      if (!response.ok) throw new Error('refund');
      onEditTransaction(await response.json());
    } catch {
      // Errore dedicato: non passa per bulkError perche' l'azione di massa
      // non c'entra, e l'utente vede la notifica anche se la selezione e' vuota.
      setRefundError(t('refundLookupFailed'));
    }
  }, [apiUrl, onEditTransaction, t]);
  const applyBulk = async () => {
    setBulkBusy(true); setBulkError('');
    try {
      const response = await fetch(`${apiUrl}/api/transactions/bulk`, { method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: [...selection].map(Number), changes: { [bulkField]: bulkField === 'counts_in_budget' || bulkField === 'incomplete_accepted' ? bulkValue === 'true' : bulkValue } }) });
      if (!response.ok) throw new Error();
      await onReload(); setSelection(new Set()); setSelezioneTroncata(0);
    } catch { setBulkError(t('bulkFailed')); } finally { setBulkBusy(false); }
  };
  const [accountFilter, setAccountFilter] = useState('all');
  // '-' e' il filtro per i movimenti senza obiettivo: e' l'unico modo di
  // trovarli, visto che il campo e' vuoto e la ricerca testuale non li vede.
  const [goalFilter, setGoalFilter] = useState('all');
  const [transactionGoals, setTransactionGoals] = useState<string[]>([]);
  const [yearFilter, setYearFilter] = useState('all');
  const [monthFilter, setMonthFilter] = useState('all');
  // I movimenti non stanno piu' tutti in memoria: si chiede al server la pagina
  // che serve, con i filtri gia' applicati. L'elenco intero erano quasi
  // quattromila righe scaricate a ogni apertura per mostrarne cento.
  const PAGINA_MOVIMENTI = 100;
  const [movimenti, setMovimenti] = useState<Transaction[]>([]);
  const [totaleMovimenti, setTotaleMovimenti] = useState(0);
  const [transactionYears, setTransactionYears] = useState<string[]>([]);
  const [pagina, setPagina] = useState(0);
  const [caricandoMovimenti, setCaricandoMovimenti] = useState(false);
  const ricerca = useRitardato(searchQuery);

  // Cambiando un filtro si riparte dalla prima pagina: la richiesta in volo
  // viene annullata dal cleanup, quindi non puo' arrivare in coda a quella nuova.
  useEffect(() => {
    setPagina(0); setSelection(new Set()); setSelezioneTroncata(0);
  }, [ricerca, transactionTypeFilter, accountFilter, goalFilter, yearFilter, monthFilter, incompleteOnly, movimentiVersione]);

  useEffect(() => {
    const controller = new AbortController();
    const parametri = filteredParams();
    parametri.set('limit', String(PAGINA_MOVIMENTI));
    parametri.set('offset', String(pagina * PAGINA_MOVIMENTI));
    setCaricandoMovimenti(true);
    fetch(`${apiUrl}/api/transactions?${parametri.toString()}`, { signal: controller.signal })
      .then((risposta) => risposta.ok ? risposta.json() as Promise<TransactionsPage> : Promise.reject(new Error('movimenti')))
      .then((dati) => {
        // Sostituire o accodare lo decide la risposta, non lo stato locale:
        // cosi' non si accoda mai il risultato di un filtro a quello di un altro.
        setMovimenti((precedenti) => dati.offset === 0 ? dati.items : [...precedenti, ...dati.items]);
        setTotaleMovimenti(dati.total);
        setTransactionYears(dati.years);
        setTransactionGoals(dati.goals ?? []);
      })
      .catch(() => undefined)
      .finally(() => setCaricandoMovimenti(false));
    return () => controller.abort();
  }, [apiUrl, ricerca, transactionTypeFilter, accountFilter, goalFilter, yearFilter, monthFilter, incompleteOnly, pagina, movimentiVersione]);
  const [movementsView, setMovementsView] = useState<'list' | 'recurring' | 'rules'>('list');
  // Il periodo scelto e' il mese corrente? Solo li' le azioni sui conti hanno
  // senso: piu' indietro i saldi sono quelli di allora e non si modificano.
  const alPresente = selectedYear === OGGI.getFullYear() && selectedMonth === OGGI.getMonth() + 1;
  // Gli anni su cui il budget e' editabile li decide l'utente in Impostazioni.
  // Derivarne l'intervallo da li' evita di tenere hard-coded il 2024..2027 in
  // due posti diversi e di dimenticarselo quando la finestra cambia.
  const editableBudgetYears = (settingsData?.budgetYearsByType?.[budgetType] ?? []).map(Number).filter(Number.isFinite);
  const canEditBudgetYear = editableBudgetYears.includes(selectedYear);

  return (
    <>
      <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div><p className="mb-1 text-sm font-medium text-[#71807c]">{t(SECTION_DESC_KEYS[section])}</p><h1 className="flex items-center text-2xl font-semibold tracking-[-0.03em] sm:text-[30px]">{t(SECTION_LABEL_KEYS[section])}<PageHelp titolo="helpTitle" testo={SECTION_HELP_KEYS[section][0]} dipendenza={SECTION_HELP_KEYS[section][1]} /></h1></div>
        {section === 'Movimenti' && <div className="flex flex-wrap gap-2"><div className="flex flex-wrap gap-2"><Button disabled={pdfImporting} onClick={() => void onPdfImport()} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><FileText className={`size-4 ${pdfImporting ? 'animate-spin' : ''}`} />{pdfImporting ? t('importingEllipsis') : t('importFromPdf')}</Button><Button disabled={pdfImporting} onClick={() => void onCsvImport()} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><FileSpreadsheet className={`size-4 ${pdfImporting ? 'animate-spin' : ''}`} />{pdfImporting ? t('importingEllipsis') : t('importFromCsv')}</Button></div><Button variant="outline" className="h-10 rounded-xl bg-white" onClick={() => onTransfer()}><ArrowRightLeft className="size-4" />{t('transfer')}</Button><Button className="h-10 rounded-xl bg-[var(--money-primary)] px-4 text-white hover:bg-[var(--money-primary-hover)]" onClick={onNewTransaction}><Plus className="size-4" />{t('newTransaction')}</Button></div>}
        {section === 'Budget' && (budgetView === 'trends' ? <YearComparisonSelector availableYears={trendYearsAvailable} selected={trendYears} onChange={onTrendYearsChange} /> : <PeriodSelector value={period} years={years} onChange={onPeriodChange} />)}
        {section === 'Patrimonio' && <PeriodSelector value={period} years={years} onChange={onPeriodChange} allowYear={false} />}
        {/* Insieme seguiva il periodo condiviso senza offrire il modo di
            cambiarlo: per spostarsi di mese bisognava passare da Budget o
            Patrimonio e tornare indietro. Qui l'anno intero ha senso - i
            totali di una famiglia si guardano anche sull'anno - quindi a
            differenza di Patrimonio la scelta resta disponibile. */}
        {section === 'Insieme' && <PeriodSelector value={period} years={years} onChange={onPeriodChange} />}
        {section === 'Report' && <PeriodSelector value={period} years={years} onChange={onPeriodChange} allowYear={false} />}
      </div>

      {section === 'Movimenti' && <div className="space-y-4"><p className="text-xs text-[#7b8784]">{t('statementAllDates')}</p>
        {importFeedback && <p role="status" className="rounded-xl border border-black/6 bg-white px-4 py-2 text-sm text-[#3a4a46] shadow-sm shadow-black/[0.02]">{importFeedback.message}</p>}
        {refundError && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{refundError}</p>}
        <div className="flex flex-wrap gap-2 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm">
          {([['list', t('movementsTabList')], ['recurring', t('movementsTabRecurring')], ['rules', t('movementsTabRules')]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => setMovementsView(value)} className={`rounded-lg px-3.5 py-2 text-sm font-medium transition ${movementsView === value ? 'bg-[var(--money-deep)] text-white' : 'text-[#61706c] hover:bg-[#f0f2ee]'}`}>{label}</button>)}
        </div>
        {movementsView === 'rules'
          ? <CategoryRulesCard rules={categorizationRules} categories={categorieRegola} apiUrl={apiUrl}
            onChanged={onCategoryRulesChanged} />
          : movementsView === 'recurring' ? <RecurringTransactionsView accounts={accounts} data={recurringTransactions} categoriesByType={settingsData.categoriesByType} onCreate={onCreateRecurring} onDelete={onDeleteRecurring} onGenerate={onGenerateRecurring} /> : <>
        <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
          <CardHeader className="gap-4">
            <div><CardTitle className="text-[17px]">{t('allMovements')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('resultsOfTotal', { count: movimenti.length, total: totaleMovimenti })}</p></div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-[minmax(190px,1fr)_150px_180px_130px_150px]">
              <div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-black/35" /><Input aria-label={t('searchInMovements')} value={searchQuery} onChange={(event) => onSearchChange(event.target.value)} className="h-10 bg-[#fafaf8] pl-9" placeholder={t('searchInMovementsPlaceholder')} />{searchQuery && <button aria-label={t('clearSearch')} onClick={() => onSearchChange('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-black/40 hover:text-black"><X className="size-4" /></button>}</div>
              <FilterSelect label={t('filterByType')} value={transactionTypeFilter} onChange={setTransactionTypeFilter} options={[["all", t('allTypes')], ["Income", t('incomeType')], ["Expenses", t('expensesType')], ["Transfers", t('transfersType')], ["Investment", t('investmentType')], ["Debt", t('debtTypeMovement')]]} />
              <FilterSelect label={t('filterByAccount')} value={accountFilter} onChange={setAccountFilter} options={[["all", t('allAccounts')], ...accounts.map((account) => [account.name, account.name] as [string, string])]} />
              {transactionGoals.length > 0 && <FilterSelect label={t('filterByGoal')} value={goalFilter} onChange={setGoalFilter} options={[["all", t('allGoals')], ["-", t('withoutGoal')], ...transactionGoals.map((nome) => [nome, nome] as [string, string])]} />}
              <FilterSelect label={t('year')} value={yearFilter} onChange={setYearFilter} options={[["all", t('allYears')], ...transactionYears.map((year) => [year, year] as [string, string])]} />
              <FilterSelect label={t('filterByPeriod')} value={monthFilter} onChange={setMonthFilter} options={[["all", t('allMonths')], ...monthNames.map((name, index) => [String(index + 1).padStart(2, '0'), name] as [string, string])]} />
            </div>
          </CardHeader>
          {/* Fra i filtri e l'elenco: un filo sopra la separa dall'intestazione,
              e il bottone va a destra perche' agisce su cio' che sta sotto,
              non sul filtro accanto. */}
          <div className="flex flex-wrap items-center gap-3 border-t border-black/5 px-3 py-2.5 sm:px-6">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-[#52615d] transition hover:bg-[#f4f5f1]">
              <input type="checkbox" checked={incompleteOnly} onChange={(event) => setIncompleteOnly(event.target.checked)} className="size-4 shrink-0 cursor-pointer accent-[var(--money-primary)]" />
              {t('incompleteMovements')}
            </label>
            <Button type="button" variant="outline" size="sm" className="ml-auto h-9 rounded-lg bg-white" disabled={bulkBusy || totaleMovimenti === 0} onClick={() => void selectAll()}>{bulkBusy ? t('updating') : t('selectFiltered')}</Button>
          </div>
          {bulkError && <p role="alert" className="px-6 text-[#bd5e46]">{bulkError}</p>}
          <CardContent className="px-3 sm:px-6">{movimenti.length ? <><div className={`divide-y divide-black/5 ${caricandoMovimenti ? 'opacity-60' : ''}`}>{movimenti.map((transaction) => <div key={transaction.id} className="flex items-center gap-2"><input type="checkbox" aria-label={t('selectMovement', { description: transaction.description })} checked={selection.has(String(transaction.id))} onChange={e => setSelection(old => { const next = new Set(old); e.target.checked ? next.add(String(transaction.id)) : next.delete(String(transaction.id)); return next; })} /><div className="min-w-0 flex-1"><TransactionRow transaction={transaction} onRefund={openRefundedTransaction} onEdit={onEditTransaction} onDuplicate={onDuplicateTransaction} onDelete={onDeleteTransaction} onSplit={onSplitTransaction} /></div></div>)}</div>{movimenti.length < totaleMovimenti && <div className="border-t border-black/5 py-4 text-center"><Button type="button" variant="outline" disabled={caricandoMovimenti} onClick={() => setPagina((corrente) => corrente + 1)}>{caricandoMovimenti ? t('updating') : t('showMore100')}</Button></div>}</> : <p className="py-12 text-center text-sm text-[#71807c]">{caricandoMovimenti ? t('updating') : t('noMovementsMatchFilters')}</p>}</CardContent>
        </Card>
        {selection.size > 0 && <div className="sticky bottom-4 flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4 shadow-lg">
          <span>{t('selectedCount', { count: selection.size })}</span>
          {selezioneTroncata > 0 && <span className="rounded-lg bg-[#f4f5f1] px-2 py-1 text-xs text-[#52615d]">{t('bulkSelectionCapped', { total: selezioneTroncata })}</span>}
          <select aria-label={t('bulkField')} value={bulkField} onChange={e => { setBulkField(e.target.value); setBulkValue(''); }} className="h-10 min-w-0 rounded-lg border border-input bg-[#fafaf8] px-2.5 text-sm outline-none focus:border-ring">
            <option value="category">{t('category')}</option><option value="account_name">{t('account')}</option><option value="transaction_type">{t('type')}</option><option value="counts_in_budget">{t('excludeBudget')}</option><option value="incomplete_accepted">{t('bulkFieldIncompleteAccepted')}</option>
          </select>
          {bulkField === 'category' ? <Input aria-label={t('category')} value={bulkValue} onChange={e => setBulkValue(e.target.value)} className="w-48" /> :
            <select aria-label={t('bulkValue')} value={bulkValue} onChange={e => setBulkValue(e.target.value)} className="h-10 min-w-0 rounded-lg border border-input bg-[#fafaf8] px-2.5 text-sm outline-none focus:border-ring"><option value="">{t('bulkValue')}</option>
              {bulkField === 'account_name' ? accounts.filter(a => a.isActive !== false).map(a => <option key={a.id} value={a.name}>{a.name}</option>) :
              bulkField === 'transaction_type' ? (['Income','Expenses','Transfers','Investment','Debt'] as const).map(type => <option key={type} value={type}>{t(type === 'Income' ? 'incomeType' : type === 'Expenses' ? 'expensesType' : type === 'Transfers' ? 'transfersType' : type === 'Investment' ? 'investmentType' : 'debtTypeMovement')}</option>) :
              bulkField === 'incomplete_accepted' ? <><option value="true">{t('leaveAsIs')}</option><option value="false">{t('backToFix')}</option></> :
              <><option value="false">{t('bulkValueExcludeBudget')}</option><option value="true">{t('bulkValueIncludeBudget')}</option></>}
            </select>}
          <Button disabled={bulkBusy || !bulkValue} onClick={() => void applyBulk()}>{t('applySelected')}</Button>
          <Button variant="outline" onClick={() => { setSelection(new Set()); setSelezioneTroncata(0); }}>{t('cancel')}</Button>
        </div>}
        </>}
      </div>}

      {section === 'FIRE' && <FirePage apiUrl={apiUrl} onOpenSettings={() => onNavigate('Impostazioni')} />}
      {section === 'Insieme' && <SharedTotalsView apiUrl={apiUrl} year={selectedYear} month={period.scope === 'year' ? null : selectedMonth} />}
      {section === 'Appunti' && <NotesView notes={notesData} onSave={onNoteSave} onDelete={onNoteDelete} />}



      {section === 'Report' && <ReportsView year={selectedYear} month={selectedMonth} downloadBusy={downloadBusy} downloadError={downloadError} canManageBackups={canManageBackups} onDownload={onDownloadReport} onDownloadData={onDownloadData} onImportData={onImportData} importing={importing} apiUrl={apiUrl} onReload={onReload} />}

      {section === 'Budget' && <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label={t('type')} className="flex gap-1.5 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm">
            {([['Expenses', t('expensesType')], ['Income', t('incomeType')], ['Savings', t('savingsType')]] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={budgetType === value} onClick={() => onBudgetTypeChange(value)} className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${budgetType === value ? 'bg-[var(--money-primary)] text-white' : 'text-[#66736f] hover:bg-[#f4f5f1]'}`}>{label}</button>)}
          </div>
          <div role="group" aria-label={t('budget')} className="flex flex-wrap gap-1 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm">
            {([['dashboard', t('budgetTabDashboard')], ['trends', t('budgetTabTrends')], ['plan', t('budgetTabPlan')]] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={budgetView === value} onClick={() => onBudgetViewChange(value)} className={`rounded-lg px-3.5 py-2 text-sm font-medium transition ${budgetView === value ? 'bg-[var(--money-deep)] text-white' : 'text-[#61706c] hover:bg-[#f0f2ee]'}`}>{label}</button>)}
          </div>
        </div>
        {budgetLoadFailed ? <Card className="border-[#efc4b8] bg-[#fff6f3] shadow-sm"><CardContent className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-sm font-semibold">{t('budgetLoadFailed')}</p><p className="mt-1 text-xs text-[#52615d]">{t('budgetLoadFailedHint')}</p></div><Button variant="outline" size="sm" onClick={() => void onReload()}>{t('retry')}</Button></CardContent></Card> : <>
        {budgetView === 'dashboard' && (budgetDashboardData ? <BudgetDashboardView data={budgetDashboardData} budgetType={budgetType} /> : <BudgetTabEmpty loading={inCorso > 0} />)}
        {budgetView === 'trends' && (trendsLoadFailed
          ? <Card className="border-[#efc4b8] bg-[#fff6f3] shadow-sm"><CardContent className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-sm font-semibold">{t('budgetLoadFailed')}</p><p className="mt-1 text-xs text-[#52615d]">{t('budgetLoadFailedHint')}</p></div><Button variant="outline" size="sm" onClick={() => void onReload()}>{t('retry')}</Button></CardContent></Card>
          : <BudgetTrendsView data={budgetTrendsData} budgetType={budgetType} loading={inCorso > 0} />)}
        {budgetView === 'plan' && <div className="space-y-5">
          {period.scope === 'month' ? <>
            <BudgetPlanMonthTotals data={budgetData} budgetType={budgetType} calculations={calculationData} year={selectedYear} month={selectedMonth} />
            {budgetType === 'Savings' ? <BudgetBalanceCard balance={budgetData.balance} scope="month" /> : <BudgetEditor data={budgetData} canEdit={canEditBudgetYear} editableYears={editableBudgetYears} budgetType={budgetType} suggestions={budgetSuggestions} onUpdate={onBudgetUpdate} onCreate={onBudgetCreate} onDelete={onBudgetDelete} onCopy={onBudgetCopy} onCategoryGroupChange={onCategoryGroupChange} />}
          </> : budgetType === 'Savings' ? <BudgetBalanceCard balance={annualBudgetData.balance} scope="year" /> : <AnnualBudgetEditor data={annualBudgetData} onApply={onAnnualBudgetApply} />}
        </div>}
        </>}
      </div>}

      {section === 'Obiettivi' && <GoalsView data={goalsData} accounts={accounts} onSave={onGoalSave} onDelete={onGoalDelete} />}

      {section === 'Patrimonio' && <NetWorthView apiUrl={apiUrl} data={netWorthData} primoAnno={Number(years[0]) || selectedYear} accounts={accounts} alPresente={alPresente} onNewAccount={onNewAccount} onAccountEdit={onAccountEdit} onAccountValuations={onAccountValuations} onAccountDelete={onAccountDelete} />}

      {section === 'Debiti' && <LiabilitiesView apiUrl={apiUrl} onDeleted={onReloadData} accounts={accounts} version={movimentiVersione} onPayment={onTransfer} onNewAccount={() => onNewAccount('liability')} onEditAccount={onAccountEdit} onEditTransaction={onEditTransaction} />}

      {section === 'Investimenti' && <InvestmentsView apiUrl={apiUrl} onQuotesChanged={onReloadData} dashboard={investmentDashboardData} ledger={investmentLedger} allocation={investmentAllocationData} onSave={onInvestmentSave} onDelete={onInvestmentDelete} onInstrumentSave={onInstrumentSave} onRefresh={onMarketRefresh} accounts={accounts} />}

      {section === 'Impostazioni' && <div className="space-y-5">
        {/* Due colonne: a sinistra chi sei e cosa entra, a destra come l'app
            si comporta. Le regole stanno sotto perche' sono una tabella. */}
        <div className="grid items-start gap-5 xl:grid-cols-2">
          <div className="space-y-5">
            {account && <AccountSettings apiUrl={apiUrl} account={account} onChanged={onAccountChanged} />}
       </div>
          <div className="space-y-5">
          <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]"><CardHeader><CardTitle className="text-[17px]">{t('preferences')}</CardTitle><p className="text-xs leading-5 text-[#7b8784]">{t('preferencesSubtitle')}</p></CardHeader><CardContent className="space-y-4">
            {settingError && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-2 text-sm text-[#bd5e46]">{settingError}</p>}
            <SettingSelect label={t('mainColor')} value={settingsData.settings.header_color} options={settingsData.options.colors} saving={settingSaving === 'header_color'} labels={{ Blue: t('colorBlue'), Orange: t('colorOrange'), Green: t('colorGreen'), Yellow: t('colorYellow'), Purple: t('colorPurple'), 'Light Blue': t('colorLightBlue') }} onChange={(value) => void onSettingChange('header_color', value)} />
            <SettingCurrencies label={t('netWorthCurrenciesSetting')} value={settingsData.settings.net_worth_currencies ?? 'USD,CHF,BTC'} saving={settingSaving === 'net_worth_currencies'} onChange={(value) => void onSettingChange('net_worth_currencies', value)} />
            <SettingSelect label={t('shiftLateIncome')} value={settingsData.settings.late_income_shift} options={uniqueOptions(settingsData.settings.late_income_shift, ['Active', 'Inactive'])} saving={settingSaving === 'late_income_shift'} hint={t('shiftLateIncomeHint')} labels={{ Active: t('toggleActive'), Inactive: t('toggleInactive') }} onChange={(value) => void onSettingChange('late_income_shift', value)} />
            <SettingSelect label={t('fromDay')} value={settingsData.settings.late_income_day} options={Array.from({ length: 28 }, (_, index) => String(index + 1))} saving={settingSaving === 'late_income_day'} disabled={settingsData.settings.late_income_shift !== 'Active'} hint={settingsData.settings.late_income_shift === 'Active' ? t('fromDayHintActive') : t('fromDayHintInactive')} onChange={(value) => void onSettingChange('late_income_day', value)} />
            <SettingText label={t('benchmarkSymbol')} value={settingsData.settings.benchmark_symbol ?? ''} saving={settingSaving === 'benchmark_symbol'} placeholder="es. ^GSPC" hint={t('benchmarkSymbolHint')} onChange={(value) => void onSettingChange('benchmark_symbol', value)} />
          </CardContent></Card>
          </div>
        </div>
        <FireSettingsSection apiUrl={apiUrl} />
      </div>}
      <Dialog open={showPdfPreview} onOpenChange={open => { if (!open && !pdfImporting) onPdfImportCancel(); }}>
        {showPdfPreview && <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[95vw]" showCloseButton={false}>
          <PDFImportPreview transactions={pdfPreviewTransactions} accounts={accounts.filter(a => a.isActive !== false)} categoriesByType={settingsData.categoriesByType} feedback={importFeedback}
            onConfirm={onPdfImportConfirm} onCancel={onPdfImportCancel} />
        </DialogContent>}
      </Dialog>
    </>
  );
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: [string, string][]; onChange: (value: string) => void }) {
  return <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} className="h-10 min-w-0 rounded-lg border border-input bg-[#fafaf8] px-2.5 text-sm outline-none focus:border-ring">{options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}</select>;
}

function BudgetBalanceCard({ balance, scope }: { balance: BudgetBalance; scope: 'month' | 'year' }) {
  const { t, formatEuro } = useI18n();
  // Il risparmio pianificato non e' un numero a se': e' quello che avanza. Il
  // caso da segnalare non e' piu' "non quadra" - quadra sempre - ma "quello che
  // avanza e' negativo", cioe' il piano spende piu' di quanto incassa.
  const negativo = balance.savings < 0;
  // Senza piano entrate, un savings negativo vuol dire che le spese superano
  // quello che si pensava di incassare: e' comunque un segnale da segnalare.
  const tono = negativo ? 'text-[#bd5e46]' : !balance.hasIncomePlan ? 'text-[#71807c]' : 'text-[#2d7b65]';
  const messaggio = negativo
    ? t('budgetBalanceOverplanned', { amount: formatEuro(Math.abs(balance.savings)) })
    : !balance.hasIncomePlan
      ? t('budgetBalanceNoIncome')
      : t('budgetBalanceSaves', { amount: formatEuro(balance.savings) });
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader><CardTitle className="text-[17px]">{scope === 'year' ? t('budgetBalanceTitleYear') : t('budgetBalanceTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{scope === 'year' ? t('budgetBalanceSubtitleYear') : t('budgetBalanceSubtitle')}</p></CardHeader>
    <CardContent className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm tabular-nums">
        <span className="font-semibold">{formatEuro(balance.income)}</span>
        <span className="text-[#87918e]">{t('budgetBalanceIncome')}</span>
        <span className="text-[#87918e]">−</span>
        <span className="font-semibold">{formatEuro(balance.expenses)}</span>
        <span className="text-[#87918e]">{t('budgetBalanceExpenses')}</span>
        <span className="text-[#87918e]">=</span>
        <span className="text-[#87918e]">{t('budgetBalanceSavings')}</span>
      </div>
      <p className={`text-2xl font-semibold tabular-nums ${tono}`}>{formatEuro(balance.savings)}</p>
      <p className={`text-xs ${tono}`}>{messaggio}</p>
    </CardContent>
  </Card>;
}

const GROUP_COLORS: Record<string, string> = { Needs: '#4d7f70', Wants: '#e0a458', Other: '#b7bfbb' };

function NeedsWantsCard({ groups }: { groups: BudgetGroupSplit[] }) {
  const { t, formatEuro } = useI18n();
  const totale = groups.reduce((sum, group) => sum + group.actual, 0);
  const etichetta = (group: string) => group === 'Needs' ? t('groupNeeds') : group === 'Wants' ? t('groupWants') : t('groupOther');
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader><CardTitle className="text-[17px]">{t('needsWantsTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('needsWantsSubtitle')}</p></CardHeader>
    <CardContent className="space-y-3">
      {totale > 0 ? <>
        <div className="flex h-3 overflow-hidden rounded-full bg-[#f0f2ee]">
          {groups.filter((group) => group.actual > 0).map((group) => <div key={group.group} style={{ width: `${group.actualShare}%`, backgroundColor: GROUP_COLORS[group.group] }} title={`${etichetta(group.group)} ${group.actualShare}%`} />)}
        </div>
        <ul className="space-y-2">
          {groups.map((group) => <li key={group.group} className="flex items-center justify-between gap-3 text-xs">
            <span className="flex items-center gap-2"><span className="size-2.5 rounded-full" style={{ backgroundColor: GROUP_COLORS[group.group] }} />{etichetta(group.group)}</span>
            <span className="tabular-nums text-[#52615d]">{formatEuro(group.actual)} · {group.actualShare}%{group.planned > 0 && <span className="text-[#87918e]"> ({t('plannedShort', { amount: formatEuro(group.planned) })})</span>}</span>
          </li>)}
        </ul>
        {(groups.find((group) => group.group === 'Other')?.actual ?? 0) > 0 && <p className="rounded-lg bg-[#f4f5f1] px-3 py-2 text-[11px] leading-4 text-[#71807c]">{t('needsWantsUnclassified')}</p>}
      </> : <p className="text-xs text-[#71807c]">{t('needsWantsEmpty')}</p>}
    </CardContent>
  </Card>;
}

function BudgetDashboardView({ data, budgetType }: { data: BudgetDashboardData; budgetType: 'Expenses' | 'Income' | 'Savings' }) {
  const { t, locale, formatEuro, formatCompactEuro, monthNames, formatPeriodLabel } = useI18n();
 const isExpense = budgetType === 'Expenses';
  const isSavings = budgetType === 'Savings';
  const actualTitle = budgetType === 'Expenses' ? t('spentMetric') : budgetType === 'Income' ? t('receivedMetric') : t('savedMetric');
  const plannedTitle = budgetType === 'Expenses' ? t('budgetMetric') : budgetType === 'Income' ? t('incomeTargetMetric') : t('plannedSavingsMetric');
  const varianceTitle = budgetType === 'Expenses' ? t('availableMetric') : budgetType === 'Income' ? t('vsTargetMetric') : t('vsPlanMetric');
  const totalGood = budgetVarianceIsGood(budgetType, data.remaining);
  const varianceStatus = data.plannedTotal === 0 && data.actualTotal === 0 ? t('budgetNotPlanned')
    : data.remaining === 0
    ? t(budgetType === 'Expenses' ? 'budgetFullyUsed' : budgetType === 'Income' ? 'onTarget' : 'onPlan')
    : isExpense
      ? t(data.remaining > 0 ? 'stillAvailable' : 'budgetExceeded')
      : budgetType === 'Income'
        ? t(data.remaining < 0 ? 'targetExceeded' : 'belowTarget')
        : t(data.remaining < 0 ? 'abovePlan' : 'belowPlan');
  const concerningCategories = data.categories.filter((item) => !budgetVarianceIsGood(budgetType, item.variance)).length;
  const budgetChartConfig = {
    planned: { label: plannedTitle, color: '#6d8ff4' },
    actual: { label: actualTitle, color: '#ef8e72' },
  } satisfies ChartConfig;
  return <div className="space-y-5">
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard title={plannedTitle} value={data.plannedTotal} change={formatPeriodRef(monthNames, data.periodYear, data.periodMonth)} icon={CircleDollarSign} tone="worth" />
      <MetricCard title={actualTitle} value={data.actualTotal} change={isExpense ? t('spentPercentUsed', { percent: data.usage.toLocaleString(locale) }) : t(isSavings ? 'percentOfPlanValue' : 'percentOfTarget', { percent: data.usage.toLocaleString(locale) })} icon={CreditCard} tone={isExpense ? 'expense' : 'worth'} />
      <MetricCard title={varianceTitle} value={isExpense ? data.remaining : Math.abs(data.remaining)} change={varianceStatus} icon={PiggyBank} tone={totalGood ? 'saving' : 'expense'} />
      {isSavings
        ? <MetricCard title={t('progressMetric')} value={data.usage} valueLabel={`${data.usage.toLocaleString(locale)}%`} change={t('percentOfPlan')} icon={AlertCircle} tone="income" />
        : <MetricCard title={isExpense ? t('categoriesOverBudget') : t('categoriesBelowTarget')} value={concerningCategories} valueLabel={String(concerningCategories)} change={t('inSelectedPeriod')} icon={AlertCircle} tone="income" />}
    </div>
    {isExpense && <NeedsWantsCard groups={data.groups} />}
    <div className={`grid gap-5 ${isSavings ? '' : 'xl:grid-cols-[1.3fr_1fr]'}`}>
      <Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('budgetAndActualByType', { 0: budgetType })}</CardTitle><p className="text-xs text-[#7b8784]">{t('budgetAndActualByTypeSubtitle', { 0: budgetType })}</p></CardHeader><CardContent><ChartContainer config={budgetChartConfig} className="h-[300px] w-full"><BarChart accessibilityLayer data={data.months}><CartesianGrid vertical={false} strokeDasharray="3 5" /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} /><YAxis tickLine={false} axisLine={false} tickFormatter={(value) => formatCompactEuro(Number(value))} width={70} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} />} /><ChartLegend content={<ChartLegendContent />} /><Bar dataKey="planned" fill="var(--color-planned)" radius={[4, 4, 0, 0]} maxBarSize={22} /><Bar dataKey="actual" fill="var(--color-actual)" radius={[4, 4, 0, 0]} maxBarSize={22} /></BarChart></ChartContainer></CardContent></Card>
      {!isSavings && <Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('periodCategories')}</CardTitle><p className="text-xs text-[#7b8784]">{t('monthCategoriesSubtitle')}</p></CardHeader><CardContent className="divide-y divide-black/5">{[...data.categories].sort((a, b) => (b.actual - a.actual) || (b.planned - a.planned)).slice(0, 10).map((item) => { const good = budgetVarianceIsGood(budgetType, item.variance); const message = isExpense ? (item.variance < 0 ? t('overBudgetBy', { amount: formatEuro(Math.abs(item.variance)) }) : t('availableAmount', { amount: formatEuro(item.variance) })) : (item.variance < 0 ? t('exceededTargetBy', { amount: formatEuro(Math.abs(item.variance)) }) : t('belowTargetBy', { amount: formatEuro(item.variance) })); return <div key={item.category} className="grid grid-cols-[1fr_auto] gap-3 py-3"><div><p className="text-sm font-medium">{item.categoryLabel}</p><p className={`mt-1 text-xs ${good ? 'text-[#71807c]' : 'text-[#bd5e46]'}`}>{message}</p>{item.previousLeftover !== 0 && <p className="mt-0.5 text-[11px] text-[#87918e]">{item.previousLeftover > 0 ? t('budgetPreviousLeft', { amount: formatEuro(item.previousLeftover) }) : t('budgetPreviousOver', { amount: formatEuro(Math.abs(item.previousLeftover)) })}</p>}</div><div className="text-right text-xs"><p className="font-semibold">{formatEuro(item.actual)}</p><p className="mt-1 text-[#87918e]">{t('ofPlannedShort', { amount: formatEuro(item.planned) })}</p></div></div>; })}</CardContent></Card>}
    </div>
  </div>;
}

function BudgetPlanMonthTotals({ data, budgetType, calculations, year, month }: {
  data: BudgetData;
  budgetType: 'Expenses' | 'Income' | 'Savings';
  calculations: CalculationData | null;
  year: number;
  month: number;
}) {
  const { t, locale, formatEuro, monthNames } = useI18n();
  const planned = data.plannedTotal;
  const actual = data.actualTotal;
  const variance = planned - actual;
  const actualLabel = budgetType === 'Expenses' ? t('spentMetric') : budgetType === 'Income' ? t('receivedMetric') : t('savedMetric');
  const plannedLabel = budgetType === 'Expenses' ? t('budgetMetric') : budgetType === 'Income' ? t('incomeTargetMetric') : t('plannedSavingsMetric');
  const varianceLabel = budgetType === 'Expenses' ? t('budgetAvailable') : budgetType === 'Income' ? t('vsTargetMetric') : t('vsPlanMetric');
  const varianceStatus = planned === 0 && actual === 0 ? t('budgetNotPlanned')
    : variance === 0
    ? t(budgetType === 'Expenses' ? 'budgetFullyUsed' : budgetType === 'Savings' ? 'onPlan' : 'onTarget')
    : budgetType === 'Expenses'
      ? t(variance > 0 ? 'stillAvailable' : 'budgetExceeded')
      : budgetType === 'Income'
        ? t(variance < 0 ? 'targetExceeded' : 'belowTarget')
        : t(variance < 0 ? 'abovePlan' : 'belowPlan');
  const periodRef = formatPeriodRef(monthNames, year, month);
  return <div className="grid gap-5 md:grid-cols-3">
    <Card className="border-0 bg-[var(--money-deep)] text-white shadow-sm"><CardContent className="p-6"><p className="text-sm text-white/55">{varianceLabel}</p><p className="mt-2 text-3xl font-semibold">{formatEuro(budgetType === 'Expenses' ? variance : Math.abs(variance))}</p><p className="mt-2 text-xs text-white/65">{varianceStatus}</p><div className="mt-7 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-[var(--money-accent)]" style={{ width: `${planned ? Math.min(actual / planned * 100, 100) : 0}%` }} /></div></CardContent></Card>
    <MetricCard title={actualLabel} value={actual} change={periodRef} icon={CreditCard} tone={budgetType === 'Expenses' ? 'expense' : 'worth'} />
    {budgetType === 'Savings'
      ? <MetricCard title={t('savingsRateLabel')} value={calculations?.savingsRate ?? 0} valueLabel={calculations?.savingsRate == null ? '—' : `${(calculations.savingsRate * 100).toLocaleString(locale, { maximumFractionDigits: 1 })}%`} change={calculations ? t('daysOfDays', { passed: calculations.daysPassed, total: calculations.daysInPeriod }) : t('calculationInProgress')} icon={PiggyBank} tone="saving" />
      : <MetricCard title={plannedLabel} value={planned} change={periodRef} icon={CircleDollarSign} tone="worth" />}
  </div>;
}

function BudgetTabEmpty({ loading }: { loading: boolean }) {
  const { t } = useI18n();
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardContent className="flex h-40 items-center justify-center text-sm text-[#71807c]">
      {loading ? t('loading') : t('budgetTabEmpty')}
    </CardContent>
  </Card>;
}

function BudgetTrendsView({ data, budgetType, loading }: { data: BudgetTrendsData; budgetType: 'Expenses' | 'Income' | 'Savings'; loading: boolean }) {
  const { t, formatEuro, formatCompactEuro, formatPeriodLabel } = useI18n();
  // Il mapping anno→colore dipende solo da `data.years`: memoizzarlo evita di
  // rifare la stessa `Object.fromEntries` a ogni render del componente.
  const trendConfig = useMemo(() => Object.fromEntries(data.years.map((item, index) => [String(item.year), { label: String(item.year), color: TREND_COLORS[index % TREND_COLORS.length] }])) as ChartConfig, [data.years]);
  const yearTitle = (year: number) => budgetType === 'Expenses' ? t('spentYear', { year }) : budgetType === 'Income' ? t('receivedYear', { year }) : t('savedYear', { year });
  if (!data.years.length) return <BudgetTabEmpty loading={loading} />;
  return <div className="space-y-5">
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{data.years.map((item) => { const good = budgetVarianceIsGood(budgetType, item.plannedTotal - item.actualTotal); return <Card key={item.year} className="border-black/6 bg-white shadow-sm"><CardContent className="p-5"><p className="text-sm font-medium text-[#71807c]">{yearTitle(item.year)}</p><p className="mt-3 text-2xl font-semibold">{formatEuro(item.actualTotal)}</p><p className={`mt-2 text-xs ${good ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>{t('budgetMetric')} {formatEuro(item.plannedTotal)}</p></CardContent></Card>; })}</div>
    <Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('monthlyComparisonAcrossYears')}</CardTitle><p className="text-xs text-[#7b8784]">{t('monthlyComparisonAcrossYearsSubtitle')}</p></CardHeader><CardContent><ChartContainer config={trendConfig} className="h-[360px] w-full"><LineChart accessibilityLayer data={data.comparison}><CartesianGrid vertical={false} strokeDasharray="3 5" /><XAxis dataKey="month" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} /><YAxis tickLine={false} axisLine={false} width={72} tickFormatter={(value) => formatCompactEuro(Number(value))} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} />} /><ChartLegend content={<ChartLegendContent />} />{data.years.map((item, index) => <Line key={item.year} type="monotone" dataKey={String(item.year)} stroke={TREND_COLORS[index % TREND_COLORS.length]} strokeWidth={2.5} dot={false} connectNulls />)}</LineChart></ChartContainer></CardContent></Card>
  </div>;
}

function AnnualBudgetEditor({ data, onApply }: { data: AnnualBudgetData; onApply: (category: string, months: number[], amount: number, categoryGroup?: string | null) => Promise<void> }) {
  const { t, formatEuro, formatCompactEuro, monthNamesShort } = useI18n();
  const [category, setCategory] = useState('');
  const [amount, setAmount] = useState('');
  const [months, setMonths] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const effectiveCategory = category || data.items[0]?.category || '';
  const selectedCategory = data.items.find((item) => item.category === effectiveCategory);

  async function applyMany(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!effectiveCategory || !months.length || !isPositiveNumber(amount)) {
      setError(t('budgetRowInvalid'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      await onApply(effectiveCategory, months, Number(amount), selectedCategory?.categoryGroup);
      setAmount('');
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy(false);
    }
  }

  async function applyCell(event: SyntheticEvent<HTMLInputElement>, itemCategory: string, monthIndex: number, previousAmount: number, categoryGroup: string | null | undefined) {
    const input = event.currentTarget;
    const raw = input.value;
    const value = Number(raw);
    const formatted = previousAmount.toFixed(2);
    if (!Number.isFinite(value) || value < 0) {
      input.value = formatted;
      return;
    }
    if (value === previousAmount) {
      if (raw !== formatted) input.value = formatted;
      return;
    }
    setError('');
    try {
      await onApply(itemCategory, [monthIndex], value, categoryGroup);
    } catch {
      setError(t('cannotSaveBudgetRow'));
      // Dopo l'await React ha azzerato `event.currentTarget`: salvarsi il
      // riferimento all'input prima dell'attesa per poter ripristinare il valore.
      input.value = formatted;
    }
  }

  return <div className="space-y-5">
    <Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('applySameValueToMultipleMonths')}</CardTitle><p className="text-xs text-[#7b8784]">{t('applySameValueToMultipleMonthsSubtitle')}</p></CardHeader><CardContent><form onSubmit={applyMany} className="space-y-4"><div className="grid gap-3 lg:grid-cols-[minmax(220px,1fr)_180px_auto]"><select aria-label={t('category')} value={effectiveCategory} onChange={(event) => setCategory(event.target.value)} className="h-10 rounded-lg border border-input bg-white px-3 text-sm">{data.items.map((item) => <option key={item.category} value={item.category}>{item.categoryLabel}</option>)}</select><Input required min="0" step="0.01" type="number" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder={t('monthlyAmountPlaceholder')} /><Button type="submit" disabled={busy || !months.length} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><Save className="size-4" />{busy ? t('savingEllipsis') : t('applyToMonths', { count: months.length || 0 })}</Button></div><div className="flex flex-wrap gap-2">{monthNamesShort.map((name, index) => { const month = index + 1; const selected = months.includes(month); return <button key={name} type="button" onClick={() => setMonths((current) => selected ? current.filter((value) => value !== month) : [...current, month].sort((a, b) => a - b))} className={`rounded-lg border px-3 py-2 text-xs font-medium ${selected ? 'border-[var(--money-deep)] bg-[var(--money-deep)] text-white' : 'border-black/8 bg-[#fafaf8] text-[#61706c]'}`}>{name}</button>; })}<button type="button" onClick={() => setMonths(months.length === 12 ? [] : Array.from({ length: 12 }, (_, index) => index + 1))} className="rounded-lg px-3 py-2 text-xs font-semibold text-[#397867]">{months.length === 12 ? t('deselectAll') : t('wholeYearAction')}</button></div></form></CardContent></Card>
    {error && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{error}</p>}
    <Card className="overflow-hidden border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('annualPlan', { year: data.year })}</CardTitle><p className="text-xs text-[#7b8784]">{t('annualPlanSubtitle')}</p></CardHeader><CardContent className="p-0"><div className="overflow-x-auto"><table className="min-w-[1320px] w-full text-xs"><thead className="sticky top-0 bg-[#f4f5f1] text-[#52615d]"><tr><th className="sticky left-0 z-10 bg-[#f4f5f1] px-4 py-3 text-left">{t('category')}</th>{monthNamesShort.map((month) => <th key={month} className="px-2 py-3 text-right">{month}</th>)}<th className="px-4 py-3 text-right">{t('total')}</th></tr></thead><tbody className="divide-y divide-black/5">{data.items.map((item) => <tr key={item.category}><td className="sticky left-0 z-10 bg-white px-4 py-2 font-medium">{item.categoryLabel}</td>{item.months.map((month) => <td key={month.month} className="px-1.5 py-1.5"><input key={`${data.year}-${item.category}-${month.month}`} aria-label={`${item.categoryLabel} ${monthNamesShort[month.month - 1]}`} type="number" min="0" step="0.01" defaultValue={month.amount.toFixed(2)} onBlur={(event) => void applyCell(event, item.category, month.month, month.amount, item.categoryGroup)} className="h-8 w-full rounded-md border border-transparent bg-[#fafaf8] px-2 text-right tabular-nums outline-none hover:border-black/10 focus:border-[#5c8f82]" /></td>)}<td className="px-4 py-2 text-right font-semibold">{formatEuro(item.plannedTotal)}</td></tr>)}</tbody><tfoot className="border-t border-black/8 bg-[#f4f5f1] font-semibold"><tr><td className="sticky left-0 bg-[#f4f5f1] px-4 py-3">{t('total')}</td>{data.monthTotals.map((month) => <td key={month.month} className="px-2 py-3 text-right">{formatCompactEuro(month.planned)}</td>)}<td className="px-4 py-3 text-right">{formatEuro(data.monthTotals.reduce((total, month) => total + month.planned, 0))}</td></tr></tfoot></table></div></CardContent></Card>
  </div>;
}

function GoalStatusBadge({ status }: { status: GoalData['status'] }) {
  const { t } = useI18n();
  if (!status || status === 'completed') return null;
  const stile = {
    on_track: { testo: t('goalOnTrack'), classe: 'bg-[#e5f3ed] text-[#2d7b65]' },
    slightly_behind: { testo: t('goalSlightlyBehind'), classe: 'bg-[#fbf3e2] text-[#9a7b2f]' },
    behind: { testo: t('goalBehind'), classe: 'bg-[#fce9e3] text-[#bd5e46]' },
  }[status];
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${stile.classe}`}>{stile.testo}</span>;
}

function GoalsBalanceRow({ data }: { data: GoalsData }) {
  const { t, formatEuro } = useI18n();
  // Nessun goal con una scadenza: non c'e' niente da far quadrare.
  if (!data.monthlyNeededTotal) return null;
  const senzaPiano = !data.hasPlannedSavings;
  const ciStanno = data.monthlyGap >= 0;
  const tono = senzaPiano ? 'text-[#71807c]' : ciStanno ? 'text-[#2d7b65]' : 'text-[#bd5e46]';
  const messaggio = senzaPiano
    ? t('goalsNoPlan', { amount: formatEuro(data.monthlyNeededTotal) })
    : ciStanno
      ? t('goalsFit', { needed: formatEuro(data.monthlyNeededTotal), saved: formatEuro(data.plannedSavings) })
      : t('goalsShort', { needed: formatEuro(data.monthlyNeededTotal), saved: formatEuro(data.plannedSavings), missing: formatEuro(Math.abs(data.monthlyGap)) });
  return <Card className="border-black/6 bg-white shadow-sm"><CardContent className="p-4"><p className={`text-sm ${tono}`}>{messaggio}</p></CardContent></Card>;
}

function GoalsView({ data, accounts, onSave, onDelete }: { data: GoalsData; accounts: Account[]; onSave: (goalId: number | null, payload: Record<string, string | number | null>) => Promise<void>; onDelete: (goal: GoalData) => Promise<void> }) {
  const { t, locale, formatEuro, formatDate, formatPeriodLabel } = useI18n();
  const [editing, setEditing] = useState<GoalData | null | undefined>(undefined);
  // Il tipo va tenuto in stato e non lasciato al form: il conto d'accumulo ha
  // senso solo per i goal `contributions`, e deve sparire appena scegli un
  // altro tipo invece di restare li' a chiedere una risposta che non serve.
  const [kindScelto, setKindScelto] = useState<GoalData['kind']>('contributions');
  const apriGoal = (goal: GoalData | null) => { setEditing(goal); setKindScelto(goal?.kind ?? 'contributions'); setError(''); };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const [goalFilter, setGoalFilter] = useState<'all' | 'active'>('all');
  const visibleGoals = goalFilter === 'active' ? data.items.filter((goal) => !goal.completed) : data.items;

  async function save(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    try {
      await onSave(editing?.id ?? null, goalPayload(form));
      setEditing(undefined);
    } catch { setError(t('cannotSaveGoal')); } finally { setBusy(false); }
  }

  async function remove(goal: GoalData) {
    setDeleteError('');
    try { await onDelete(goal); }
    catch { setDeleteError(t('cannotDeleteGoal')); }
  }

  const historyConfig = { amount: { label: t('goalCurrentAmount'), color: '#6d8ff4' }, target: { label: t('goalTargetLabel'), color: '#a8b3af' } } satisfies ChartConfig;

  return <div className="space-y-5">
    <div className="grid gap-4 sm:grid-cols-2">
      <Card className="border-0 bg-[var(--money-deep)] text-white"><CardContent className="p-6"><p className="text-sm text-white/55">{t('allocatedTotal')}</p><p className="mt-2 text-3xl font-semibold">{formatEuro(data.currentTotal)}</p><p className="mt-3 text-xs text-white/48">{t('ofAllGoals', { amount: formatEuro(data.targetTotal) })}</p></CardContent></Card>
      <Card className="border-black/6 bg-white"><CardContent className="p-6"><p className="text-sm text-[#71807c]">{t('activeGoals')}</p><p className="mt-2 text-3xl font-semibold">{data.active}</p><p className="mt-3 text-xs text-[#87918e]">{t('goalsAchievedOfTotal', { completed: data.completed, total: data.items.length })}</p></CardContent></Card>
    </div>
    <GoalsBalanceRow data={data} />
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-medium text-[#52615d]">{visibleGoals.length === data.items.length
        ? t('goalListTitleAll', { total: data.items.length })
        : t('goalListTitle', { visible: visibleGoals.length, total: data.items.length })}</p>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 rounded-xl border border-black/6 bg-white p-1 shadow-sm">
        {([['all', t('goalFilterAll')], ['active', t('goalFilterActive')]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => setGoalFilter(value)} className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${goalFilter === value ? 'bg-[var(--money-deep)] text-white' : 'text-[#66736f] hover:bg-[#f4f5f1]'}`}>{label}</button>)}
        </div>
        <Button className="h-10 rounded-xl bg-[var(--money-primary)] px-4 text-white hover:bg-[var(--money-primary-hover)]" onClick={() => apriGoal(null)}><Plus className="size-4" />{t('newGoal')}</Button>
      </div>
    </div>
    {deleteError && <p role="alert" className="rounded-lg bg-[#fce9e3] px-3 py-2 text-sm text-[#a94f3a]">{deleteError}</p>}
    {visibleGoals.length === 0 ? <Card><CardContent className="p-10 text-center text-sm text-[#71807c]">{t('noGoalsMatchFilter')}</CardContent></Card> : <div className="grid gap-5 lg:grid-cols-2">{visibleGoals.map((goal) => {
      const historyData = goal.history.map((point) => ({ ...point, target: goal.targetAmount }));
      const subtitleParts: string[] = [];
      if (goal.completed) {
        if (goal.completedAt) subtitleParts.push(t('goalAchievedOn', { date: formatDate(`${goal.completedAt}T12:00:00`) }));
        if (goal.targetDate) subtitleParts.push(t('goalTargetWas', { date: formatDate(`${goal.targetDate}T12:00:00`) }));
        if (subtitleParts.length === 0) subtitleParts.push(t('goalAchieved'));
      } else if (goal.targetDate) {
        subtitleParts.push(t('goalDeadline', { date: formatDate(`${goal.targetDate}T12:00:00`) }));
      } else {
        subtitleParts.push(t('noDeadline'));
      }
      return <Card key={goal.id} className={`border-black/6 bg-white shadow-sm ${goal.completed ? 'opacity-75' : ''}`}><CardHeader className="flex-row items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><CardTitle className="text-[17px]">{goal.name}</CardTitle><GoalStatusBadge status={goal.status} /></div><p className="mt-1 text-xs text-[#7b8784]">{subtitleParts.join(' · ')}</p></div><div className="flex"><Button size="icon" variant="ghost" aria-label={`${t('edit')} ${goal.name}`} onClick={() => apriGoal(goal)}><Pencil className="size-4" /></Button><Button size="icon" variant="ghost" aria-label={`${t('delete')} ${goal.name}`} onClick={() => void remove(goal)} className="text-[#bd5e46]"><Trash2 className="size-4" /></Button></div></CardHeader><CardContent><div className="flex items-end justify-between"><p className="text-2xl font-semibold">{formatEuro(goal.currentAmount)}</p><p className="text-sm font-semibold text-[#397867]">{goal.progress.toLocaleString(locale)}%</p></div><div className="mt-4 h-2 overflow-hidden rounded-full bg-[#eef0ec]"><div className="h-full rounded-full bg-[#6d8ff4]" style={{ width: `${goal.progress}%` }} /></div>{goal.history.length > 1 ? <div className="mt-4"><ChartContainer config={historyConfig} className="h-[88px] w-full"><LineChart accessibilityLayer data={historyData} margin={{ top: 6, right: 6, left: 6, bottom: 0 }}><YAxis hide domain={[(min: number) => Math.min(min, 0), (max: number) => Math.max(max, goal.targetAmount)]} /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} hide /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} nameKey="amount" formatter={(value) => formatEuro(Number(value))} />} /><Line type="monotone" dataKey="target" stroke="var(--color-target)" strokeWidth={1.5} strokeDasharray="3 4" dot={false} /><Line type="monotone" dataKey="amount" stroke="var(--color-amount)" strokeWidth={2.4} dot={false} /></LineChart></ChartContainer></div> : <p className="mt-4 text-xs text-[#87918e]">{t('goalHistoryUnavailable')}</p>}{goal.monthlyNeeded !== null && <p className="mt-3 text-sm"><span className="font-semibold text-[var(--money-deep)]">{t('goalMonthlyNeeded', { amount: formatEuro(goal.monthlyNeeded) })}</span> <span className="text-xs text-[#7b8784]">{t('goalWeeklyNeeded', { amount: formatEuro(goal.weeklyNeeded ?? 0) })}</span></p>}
      {goal.overdue && !goal.completed && <p className="mt-3 text-sm font-medium text-[#bd5e46]">{t('goalOverdue', { amount: formatEuro(goal.remainingAmount) })}</p>}
      <div className="mt-3 flex justify-between text-xs text-[#7b8784]"><span>{goal.kind === 'contributions' ? `${t('movementsAndAmount', { count: goal.linkedMovements, amount: formatEuro(goal.linkedAmount) })}${goal.targetAccount ? ` · ${t('goalTargetAccountOn', { account: goal.targetAccount })}` : ''}` : t(goal.kind === 'portfolio' ? 'goalKindPortfolio' : 'goalKindNetWorth')}</span><span>{t('goalTarget', { amount: formatEuro(goal.targetAmount) })}</span></div></CardContent></Card>;
    })}</div>}
    <Dialog open={editing !== undefined} onOpenChange={(open) => { if (!open) setEditing(undefined); }}><DialogContent className="max-w-md"><DialogHeader><DialogTitle>{editing ? t('editGoal') : t('newGoalTitle')}</DialogTitle><DialogDescription>{t('goalDialogDesc')}</DialogDescription></DialogHeader><form key={editing?.id ?? 'new-goal'} onSubmit={save} className="space-y-4"><label htmlFor="goal-name" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldName')}<Input id="goal-name" required name="name" defaultValue={editing?.name ?? ''} /></label><div className="grid grid-cols-2 gap-3"><label htmlFor="goal-start-amount" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('startingAmount')}<Input id="goal-start-amount" required name="starting_amount" type="number" min="0" step="0.01" defaultValue={editing?.startingAmount ?? 0} /></label><label htmlFor="goal-target-amount" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldTarget')}<Input id="goal-target-amount" required name="target_amount" type="number" min="0.01" step="0.01" defaultValue={editing?.targetAmount ?? ''} /></label></div><div className="grid grid-cols-2 gap-3"><label htmlFor="goal-start-date" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('startDate')}<Input id="goal-start-date" name="start_date" type="date" defaultValue={editing?.startDate ?? ''} /></label><label htmlFor="goal-target-date" className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fieldDeadline')}<Input id="goal-target-date" name="target_date" type="date" defaultValue={editing?.targetDate ?? ''} /></label></div><label htmlFor="goal-kind" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('goalKindLabel')}<select id="goal-kind" name="kind" value={kindScelto} onChange={(event) => setKindScelto(event.target.value as GoalData['kind'])} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring"><option value="contributions">{t('goalKindContributionsOption')}</option><option value="portfolio">{t('goalKindPortfolioOption')}</option><option value="net_worth">{t('goalKindNetWorthOption')}</option></select></label>{kindScelto === 'contributions' && <label htmlFor="goal-target-account" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('goalTargetAccountLabel')}<select id="goal-target-account" name="target_account" defaultValue={editing?.targetAccount ?? ''} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring"><option value="">{t('goalTargetAccountNone')}</option>{accounts.map((conto) => <option key={conto.id} value={conto.name}>{conto.name}</option>)}</select><span className="block pt-1 font-normal leading-5 text-[#7b8784]">{t('goalTargetAccountHint')}</span></label>}<label htmlFor="goal-completed-date" className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('achievedDate')}<Input id="goal-completed-date" name="completed_at" type="date" defaultValue={editing?.completedAt ?? ''} /></label>{error && <p className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{error}</p>}<DialogFooter><Button type="button" variant="outline" onClick={() => setEditing(undefined)}>{t('cancel')}</Button><Button type="submit" disabled={busy} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{busy ? t('savingEllipsis') : t('saveGoal')}</Button></DialogFooter></form></DialogContent></Dialog>
  </div>;
}

type InstrumentRow = { id: number; name: string; providerSymbol: string | null; assetClass: string; area: string; sector: string; currency: string };

// I messaggi della fonte arrivano come codici: la frase la costruisce qui
// l'interfaccia, nella lingua scelta dall'utente.
function sourceErrorLabel(
  t: (key: TranslationKey, params?: Record<string, string | number>) => string,
  code: string | null | undefined,
  retryMinutes?: number | null,
): string {
  switch (code) {
    case 'rate_limited':
      return retryMinutes ? t('sourceRateLimitedIn', { minutes: retryMinutes }) : t('sourceRateLimited');
    case 'handshake_failed':
      return t('sourceHandshakeFailed');
    case 'unreachable':
      return t('sourceUnreachable');
    case 'no_data':
      return t('sourceNoData');
    case 'invalid_symbol':
      return t('sourceInvalidSymbol');
    case 'query_too_short':
      return t('sourceQueryTooShort');
    case null:
    case undefined:
      return t('allocationNoProfile');
    default:
      return t('sourceUnexpected');
  }
}

export type InstrumentHistory = {
  name: string;
  symbol: string;
  currency: string | null;
  prices: Array<{ date: string; price: number; currency: string | null }>;
  trades: Array<{ date: string; type: string; units: number; amount: number; price: number }>;
};

// Chi ha prodotto il guadagno, e cosa e' successo al singolo strumento.
// I prezzi sono gli stessi gia' scaricati per valorizzare il portafoglio:
// dieci anni di chiusure mensili che finora non si vedevano da nessuna parte.
function InstrumentAnalysisView({ apiUrl, positions }: { apiUrl: string; positions: InvestmentPosition[] }) {
  const { t, formatEuro, formatCompactEuro, formatDate, formatPeriodLabel } = useI18n();
  const ranked = positions.slice().sort((a, b) => b.totalGain - a.totalGain);
  const [selected, setSelected] = useState<string | null>(ranked[0]?.name ?? null);
  const [history, setHistory] = useState<InstrumentHistory | null>(null);
  const [loading, setLoading] = useState(false);
  const worst = Math.max(...ranked.map((p) => Math.abs(p.totalGain)), 1);

  useEffect(() => {
    if (!selected) return;
    let annullato = false;
    setLoading(true);
    fetch(`${apiUrl}/api/investments/instrument-history?name=${encodeURIComponent(selected)}`)
      .then((response) => (response.ok ? response.json() as Promise<InstrumentHistory> : null))
      .then((payload) => { if (!annullato) setHistory(payload); })
      .catch(() => { if (!annullato) setHistory(null); })
      .finally(() => { if (!annullato) setLoading(false); });
    return () => { annullato = true; };
  }, [apiUrl, selected]);

  // Prezzo mese per mese, con sopra il prezzo medio delle proprie operazioni:
  // due serie separate, cosi' i punti restano leggibili anche dove si affollano.
  const chartData = (history?.prices ?? []).map((point) => {
    const sameMonth = (history?.trades ?? []).filter((trade) => trade.date.slice(0, 7) === point.date.slice(0, 7));
    const buys = sameMonth.filter((trade) => trade.type === 'Buy');
    const sells = sameMonth.filter((trade) => trade.type === 'Sell');
    const media = (rows: typeof sameMonth) => (rows.length ? rows.reduce((sum, r) => sum + r.price, 0) / rows.length : null);
    return {
      label: formatDate(`${point.date}T12:00:00`, { month: 'short', year: '2-digit' }),
      price: point.price,
      buy: media(buys),
      sell: media(sells),
    };
  });
  const chartConfig = {
    price: { label: t('price'), color: '#6d8ff4' },
    buy: { label: t('buy'), color: '#2d7b65' },
    sell: { label: t('sell'), color: '#bd5e46' },
  } satisfies ChartConfig;

  return (
    <div className="space-y-5">
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-[17px]">{t('gainByInstrument')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('gainByInstrumentSubtitle')}</p>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {ranked.map((position) => (
            <button
              key={position.name}
              type="button"
              onClick={() => setSelected(position.name)}
              className={`flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition hover:bg-black/[0.03] ${selected === position.name ? 'bg-black/[0.04]' : ''}`}
            >
              <span className="w-44 shrink-0 truncate text-sm">
                {position.name}
                {!position.isOpen && <span className="ml-2 rounded-full bg-black/6 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[#a3adaa]">{t('closed')}</span>}
              </span>
              <span className="relative flex h-4 flex-1 items-center">
                <span className="absolute left-1/2 h-full w-px bg-black/10" />
                <span
                  className="absolute h-2.5 rounded-sm"
                  style={{
                    backgroundColor: position.totalGain >= 0 ? '#47a889' : '#bd5e46',
                    width: `${Math.abs(position.totalGain) / worst * 50}%`,
                    left: position.totalGain >= 0 ? '50%' : undefined,
                    right: position.totalGain < 0 ? '50%' : undefined,
                  }}
                />
              </span>
              <span className={`w-28 shrink-0 text-right text-sm font-semibold tabular-nums ${position.totalGain >= 0 ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>
                {formatEuro(position.totalGain)}
              </span>
            </button>
          ))}
        </CardContent>
      </Card>

      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-[17px]">{selected ?? t('instrument')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">
            {history?.symbol ? t('instrumentHistorySubtitle', { symbol: history.symbol }) : t('instrumentHistoryNoTicker')}
          </p>
        </CardHeader>
        <CardContent>
          {loading && <div className="flex h-[300px] items-center justify-center text-sm text-[#87918e]">{t('loading')}</div>}
          {!loading && chartData.length === 0 && <div className="flex h-[300px] items-center justify-center text-sm text-[#87918e]">{t('instrumentHistoryEmpty')}</div>}
          {!loading && chartData.length > 0 && (
            <ChartContainer config={chartConfig} className="h-[300px] w-full">
              <ComposedChart accessibilityLayer data={chartData}>
                <CartesianGrid vertical={false} strokeDasharray="3 5" />
                <XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={26} />
                <YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(value) => formatCompactEuro(Number(value))} />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => formatEuro(Number(value))} />} />
                <ChartLegend content={<ChartLegendContent />} />
                <Line type="monotone" dataKey="price" stroke="var(--color-price)" strokeWidth={2} dot={false} />
                <Line dataKey="buy" stroke="none" dot={{ r: 4, fill: '#2d7b65' }} legendType="circle" />
                <Line dataKey="sell" stroke="none" dot={{ r: 4, fill: '#bd5e46' }} legendType="circle" />
              </ComposedChart>
            </ChartContainer>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function InstrumentQuotesView({ apiUrl, rows, reload, onSaved, onRefresh }: { apiUrl: string; rows: InstrumentRow[]; reload: () => Promise<void>; onSaved: () => Promise<void>; onRefresh: () => Promise<{ updated: number; errors: Array<{ code?: string; error?: string }> }> }) {
  const { t } = useI18n();
  const [newName, setNewName] = useState('');
  const [newSymbol, setNewSymbol] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [checks, setChecks] = useState<Record<number, { ok: boolean; text: string }>>({});
  const [busy, setBusy] = useState<number | 'refresh' | null>(null);
  const [summary, setSummary] = useState('');
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [searchFor, setSearchFor] = useState<number | null>(null);
  const [searchText, setSearchText] = useState('');
  const [results, setResults] = useState<Array<{ symbol: string; name: string; exchange: string; type: string }>>([]);
  const [searching, setSearching] = useState(false);

  async function runSearch(instrumentId: number, query: string) {
    if (query.trim().length < 2) return;
    setSearching(true);
    setResults([]);
    try {
      const response = await fetch(`${apiUrl}/api/market-data/search?q=${encodeURIComponent(query.trim())}`);
      if (response.ok) setResults(((await response.json()) as { items: typeof results }).items);
    } finally { setSearching(false); }
  }

  function pick(instrumentId: number, symbol: string) {
    setDrafts((d) => ({ ...d, [instrumentId]: symbol }));
    setChecks((c) => { const next = { ...c }; delete next[instrumentId]; return next; });
    setSearchFor(null);
    setResults([]);
  }

  async function createInstrument() {
    if (!newName.trim()) return;
    setCreating(true); setCreateError('');
    try {
      const response = await fetch(`${apiUrl}/api/investments/instruments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName.trim(), provider_symbol: newSymbol.trim() || null }),
      });
      if (!response.ok) {
        setCreateError(response.status === 409 ? t('instrumentAlreadyExists') : t('instrumentCreateFailed'));
        return;
      }
      setNewName(''); setNewSymbol('');
      await reload(); await onSaved();
    } finally { setCreating(false); }
  }

  async function verify(row: InstrumentRow) {
    const symbol = (drafts[row.id] ?? row.providerSymbol ?? '').trim();
    if (!symbol) return;
    setBusy(row.id);
    try {
      const response = await fetch(`${apiUrl}/api/market-data/verify?symbol=${encodeURIComponent(symbol)}`);
      if (!response.ok) {
        setChecks((c) => ({ ...c, [row.id]: { ok: false, text: t('tickerNotFound') } }));
        return;
      }
      const quote = await response.json() as { price: number; currency: string; observedOn: string };
      setChecks((c) => ({ ...c, [row.id]: { ok: true, text: `${quote.price.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${quote.currency} · ${quote.observedOn}` } }));
    } finally { setBusy(null); }
  }

  async function save(row: InstrumentRow) {
    const symbol = (drafts[row.id] ?? row.providerSymbol ?? '').trim();
    setBusy(row.id);
    try {
      const response = await fetch(`${apiUrl}/api/investments/instruments/${row.id}/classification`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        // la valuta non si invia: la ricava il backend dalla quotazione del ticker
        body: JSON.stringify({ provider_symbol: symbol || null, asset_class: row.assetClass, area: row.area, sector: row.sector }),
      });
      if (response.ok) { await reload(); await onSaved(); }
    } finally { setBusy(null); }
  }

  async function refreshAll() {
    setBusy('refresh'); setSummary(''); setRefreshFailed(false);
    try {
      const result = await onRefresh();
      setSummary(t('quotesRefreshed', { updated: result.updated, errors: result.errors.length }));
    } catch {
      setRefreshFailed(true);
      setSummary(t('cannotReachQuoteSource'));
    } finally { setBusy(null); }
  }

  const configured = rows.filter((row) => (row.providerSymbol || '').trim()).length;

  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <CardTitle className="text-[17px]">{t('instrumentTickers')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('instrumentTickersSubtitle', { configured, total: rows.length })}</p>
      </div>
      <div className="flex flex-col items-end gap-1">
        <Button type="button" variant="outline" disabled={busy === 'refresh'} onClick={() => void refreshAll()}>
          <RefreshCw className={`size-4 ${busy === 'refresh' ? 'animate-spin' : ''}`} />{t('refreshQuotes')}
        </Button>
        {summary && <span role={refreshFailed ? 'alert' : undefined} className={`text-xs ${refreshFailed ? 'text-[#bd5e46]' : 'text-[#397867]'}`}>{summary}</span>}
      </div>
    </CardHeader>
    <CardContent className="p-0">
      <div className="mx-(--card-spacing) mb-3 rounded-xl bg-[#f4f5f1] p-3">
        <p className="mb-2 text-xs font-medium text-[#52615d]">{t('addInstrument')}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={t('instrumentNamePlaceholder')} className="h-9 w-[240px] bg-white" />
          <Input value={newSymbol} onChange={(event) => setNewSymbol(event.target.value)} placeholder={t('tickerPlaceholder')} className="h-9 w-[150px] bg-white" />
          <Button type="button" size="sm" disabled={creating || !newName.trim()} onClick={() => void createInstrument()} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><Plus className="size-4" />{t('add')}</Button>
          {createError && <span className="text-xs text-[#bd5e46]">{createError}</span>}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-[#f4f5f1] text-xs text-[#52615d]">
            <tr>
              <th className="px-5 py-3 text-left">{t('instrument')}</th>
              <th className="px-3 py-3 text-left">{t('currencyAuto')}</th>
              <th className="px-3 py-3 text-left">{t('tickerSymbol')}</th>
              <th className="px-3 py-3 text-left">{t('tickerCheck')}</th>
              <th className="px-5 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-black/5">
            {rows.map((row) => {
              const value = drafts[row.id] ?? row.providerSymbol ?? '';
              const changed = value.trim() !== (row.providerSymbol ?? '').trim();
              const check = checks[row.id];
              return <tr key={row.id}>
                <td className="px-5 py-2.5 font-medium">{row.name}</td>
                <td className="px-3 py-2.5 text-xs text-[#71807c]">{row.currency}</td>
                <td className="px-3 py-2.5">
                  <Input value={value} placeholder={t('tickerPlaceholder')} onChange={(event) => setDrafts((d) => ({ ...d, [row.id]: event.target.value }))} className="h-9 w-[150px] bg-[#fafaf8]" />
                </td>
                <td className={`px-3 py-2.5 text-xs ${check ? (check.ok ? 'text-[#2d7b65]' : 'text-[#bd5e46]') : 'text-[#87918e]'}`}>{check ? check.text : '—'}</td>
                <td className="px-5 py-2.5">
                  <div className="flex justify-end gap-1">
                    <Button type="button" size="sm" variant="ghost" onClick={() => { setSearchFor(searchFor === row.id ? null : row.id); setSearchText(row.name); setResults([]); }}><Search className="size-4" />{t('tickerSearch')}</Button>
                    <Button type="button" size="sm" variant="ghost" disabled={busy === row.id || !value.trim()} onClick={() => void verify(row)}>{t('tickerVerify')}</Button>
                    <Button type="button" size="sm" variant="outline" disabled={busy === row.id || !changed} onClick={() => void save(row)}><Save className="size-4" />{t('save')}</Button>
                  </div>
                </td>
              </tr>;
            }).flatMap((rowNode, index) => {
              const row = rows[index];
              if (searchFor !== row.id) return [rowNode];
              return [rowNode, <tr key={`search-${row.id}`}><td colSpan={5} className="bg-[#fafaf8] px-5 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Input autoFocus value={searchText} onChange={(event) => setSearchText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void runSearch(row.id, searchText); } }} placeholder={t('tickerSearchPlaceholder')} className="h-9 w-[280px] bg-white" />
                  <Button type="button" size="sm" variant="outline" disabled={searching} onClick={() => void runSearch(row.id, searchText)}>{searching ? t('searchingEllipsis') : t('tickerSearch')}</Button>
                </div>
                {results.length > 0 && <div className="mt-2 divide-y divide-black/5 rounded-lg border border-black/8 bg-white">
                  {results.map((item) => <button key={item.symbol} type="button" onClick={() => pick(row.id, item.symbol)} className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs hover:bg-[#f4f5f1]">
                    <span><span className="font-semibold">{item.symbol}</span> <span className="text-[#71807c]">{item.name}</span></span>
                    <span className="shrink-0 text-[#87918e]">{item.exchange} · {item.type}</span>
                  </button>)}
                </div>}
                {!searching && results.length === 0 && <p className="mt-2 text-xs text-[#87918e]">{t('tickerSearchHint')}</p>}
              </td></tr>];
            })}
          </tbody>
        </table>
      </div>
    </CardContent>
  </Card>;
}

function CreditLineCard({ item, onEdit, onDelete }: { item: CreditLineItem; onEdit: () => void; onDelete: () => void }) {
  const { t, locale, formatEuro, formatCompactEuro, formatDate, formatPeriodLabel } = useI18n();
  const [aperto, setAperto] = useState(false);
  // Quattro voci, piu' limite e utilizzo se un limite esiste. Di uno scoperto
  // non interessa il piano - non ce l'ha - ma quanto devi adesso, quanto hai
  // dovuto al massimo, quanto ti e' costato e a che tasso.
  const voci: Array<[string, string]> = [
    [t('debtExposure'), formatEuro(item.exposure)],
    [t('debtPeakExposure'), formatEuro(item.peakExposure)],
    [t('debtInterestThisYear'), formatEuro(item.interestThisYear)],
    [t('debtRate'), `${item.rate.toLocaleString(locale)}%`],
  ];
  if (item.creditLimit !== null) {
    voci.splice(1, 0, [t('debtCreditLimit'), formatEuro(item.creditLimit)]);
    if (item.utilisation !== null) voci.splice(2, 0, [t('debtUtilisation'), `${item.utilisation.toLocaleString(locale)}%`]);
  }
  const esposizioneConfig = { exposure: { label: t('debtExposure'), color: '#bd5e46' } } satisfies ChartConfig;
  // Un movimento puo' avere una riga di dettaglio: serve a marcare in tabella
  // quelli ancora da classificare, come fa la scheda del prestito.
  const dettaglioPerMovimento = new Map(item.payments.flatMap((riga) => riga.transactionIds.map((id) => [id, riga] as const)));
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
      <div>
        <CardTitle className="text-[17px]">{item.name}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{item.creditLimit === null ? t('debtNoCreditLimit') : t('debtCreditLineLabel')}</p>
        {item.unclassified.count > 0 && <p className="mt-1 text-xs font-medium text-[#a05f4e]">{t('debtUnclassifiedSummary', { count: item.unclassified.count, amount: formatEuro(item.unclassified.amount) })}</p>}
      </div>
      <div className="flex gap-2"><Button variant="outline" size="sm" onClick={onEdit}>{t('edit')}</Button><Button variant="ghost" size="icon" aria-label={`${t('delete')} ${item.name}`} onClick={onDelete}><Trash2 className="size-4" /></Button></div>
    </CardHeader>
    <CardContent>
      <div className="divide-y divide-black/5">
        {voci.map(([etichetta, valore]) => (
          <div key={etichetta} className="flex items-baseline justify-between gap-3 py-2">
            <span className="text-xs text-[#7b8784]">{etichetta}</span>
            <span className="text-sm font-semibold tabular-nums">{valore}</span>
          </div>
        ))}
      </div>
      <Button variant="outline" size="sm" className="mt-3" aria-expanded={aperto} onClick={() => setAperto((prima) => !prima)}>
        {aperto ? t('debtHideDetail') : t('debtShowDetail')}
      </Button>
      {aperto && <div className="mt-4 space-y-5 border-t border-black/6 pt-4">
        {/* L'equivalente del grafico del saldo di un prestito: come si e'
            mosso lo scoperto nel tempo. Un punto per mese, il valore di fine
            mese - il picco infra-mese sta fra le voci qui sopra. */}
        <div>
          <p className="mb-2 text-xs font-semibold text-[#52615d]">{t('debtExposureChart')}</p>
          {item.trend.length > 0
            ? <ChartContainer config={esposizioneConfig} className="h-[200px] w-full">
                <LineChart accessibilityLayer data={item.trend}>
                  <CartesianGrid vertical={false} strokeDasharray="3 5" />
                  <XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={35} />
                  <YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(valore) => formatCompactEuro(Number(valore))} />
                  <ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(valore) => formatEuro(Number(valore))} />} />
                  <Line dataKey="exposure" stroke="var(--color-exposure)" strokeWidth={2.5} dot={item.trend.length === 1} />
                </LineChart>
              </ChartContainer>
            : <p className="text-sm text-[#71807c]">{t('noTransactions')}</p>}
        </div>
        <div>
          <p className="mb-3 text-sm font-semibold">{t('debtMovements')}</p>
          {item.movements.length ? <div className="max-h-[28rem] overflow-auto rounded-lg border border-black/8">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-[#f4f5f1]"><tr>
                <th className="px-3 py-2 text-left">{t('date')}</th>
                <th className="px-3 py-2 text-left">{t('description')}</th>
                <th className="px-3 py-2 text-right">{t('amount')}</th>
              </tr></thead>
              <tbody>{item.movements.map((movimento) => {
                const dettaglio = dettaglioPerMovimento.get(movimento.id);
                // In uscita dal conto il debito cresce, in entrata scende.
                const effetto = movimento.effect;
                return <tr key={movimento.id} className={`border-t border-black/5 ${dettaglio && !dettaglio.classified ? 'bg-[#fce9e3]' : ''}`}>
                  <td className="whitespace-nowrap px-3 py-2">{formatDate(`${movimento.occurredOn}T12:00:00`)}</td>
                  <td className="px-3 py-2">{movimento.description}{dettaglio && !dettaglio.classified && <span className="ml-2 text-[11px] font-medium text-[#a05f4e]">{t('debtUnclassified')}</span>}</td>
                  <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${effetto >= 0 ? 'text-[#bd5e46]' : 'text-[#2d7b65]'}`}>{effetto >= 0 ? '+' : '−'}{formatEuro(Math.abs(effetto))}</td>
                </tr>;
              })}</tbody>
            </table>
          </div> : <p className="text-sm text-[#71807c]">{t('noTransactions')}</p>}
        </div>
      </div>}
    </CardContent>
  </Card>;
}

function LiabilitiesView({ apiUrl, accounts, version, onDeleted, onNewAccount, onEditAccount, onEditTransaction, onPayment }: { apiUrl: string; accounts: Account[]; version: number; onDeleted: () => Promise<void>; onNewAccount: () => void; onEditAccount: (account: Account) => void; onEditTransaction: (transaction: Transaction) => void; onPayment: (destination?: string, suggestion?: { principal: number; interest: number }) => void }) {
  const { t, locale, formatEuro, formatCompactEuro, formatDate, formatPeriodLabel } = useI18n();
  const [data, setData] = useState<LiabilityData | null>(null);
  const [editing, setEditing] = useState<LiabilityData['items'][number] | null>(null);
  const [drawdowns, setDrawdowns] = useState<LiabilityDrawdown[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState<{ accountId: number; name: string; accountName: string | null } | null>(null);
  const [confirmAccount, setConfirmAccount] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  function requestDelete(accountId: number, name: string, accountName: string | null) {
    setDeleting({ accountId, name, accountName }); setConfirmAccount(false); setDeleteError('');
  }
  async function removeDebt() {
    if (!deleting || deleteBusy || (deleting.accountName !== null && !confirmAccount)) return;
    setDeleteBusy(true); setDeleteError('');
    try {
      const response = await fetch(`${apiUrl}/api/liabilities/${deleting.accountId}`, {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_name: deleting.accountName }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => null) as { detail?: string } | null;
        setDeleteError(t(result?.detail === 'debtDeleteBackupFailed' ? 'debtDeleteBackupFailed' : result?.detail === 'debtDeleteAccountConfirmation' ? 'debtDeleteAccountConfirmation' : 'debtDeleteFailed'));
        return;
      }
      setDeleting(null);
      try { await load(); await onDeleted(); } catch { setError(t('networkErrorGeneric')); }
    } catch { setDeleteError(t('debtDeleteFailed')); }
    finally { setDeleteBusy(false); }
  }
  // Il tipo guida il modulo: con una linea i campi del piano non compaiono.
  const [tipoProfilo, setTipoProfilo] = useState<'term_loan' | 'credit_line'>('term_loan');
  const load = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/liabilities`);
    if (!response.ok) throw new Error();
    setData(await response.json() as LiabilityData);
    setError('');
  }, [apiUrl, version]);
  useEffect(() => { void load().catch(() => setError(t('networkErrorGeneric'))); }, [load, t]);
  const today = new Date().toISOString().slice(0, 10);
  const defaultEnd = `${new Date().getFullYear() + 1}-${today.slice(5)}`;

  function edit(item: LiabilityItem) {
    // Le tranche sono cosa da prestito: una linea di credito non le ha, e i
    // campi del piano nel modulo restano nascosti.
    setDrawdowns(item.kind === 'credit_line' ? []
      : item.profile?.plannedDrawdowns.length ? item.profile.plannedDrawdowns
        : item.suggestedDrawdowns.length ? item.suggestedDrawdowns
          : [{ occurredOn: today, amount: item.outstanding || 1 }]);
    setTipoProfilo(item.kind === 'credit_line' ? 'credit_line' : item.profile?.kind ?? 'term_loan');
    setEditing(item); setError('');
  }

  async function editMovement(id: number) {
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${id}`);
      if (!response.ok) throw new Error();
      onEditTransaction(await response.json() as Transaction);
    } catch {
      setError(t('networkErrorGeneric'));
    }
  }

  async function save(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault(); if (!editing) return;
    setBusy(true); setError(''); const form = new FormData(event.currentTarget);
    const payload = liabilityTermsPayload(form, drawdowns);
    try {
      if (!payload) { setError(t('liabilityNeedsDrawdown')); return; }
      const response = await fetch(`${apiUrl}/api/liabilities/${editing.accountId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!response.ok) {
        // Il server dice quale regola non torna: il modulo lo ripete invece
        // del generico "impossibile salvare".
        const corpo = await response.json().catch(() => null) as { detail?: unknown } | null;
        const codice = typeof corpo?.detail === 'string' ? corpo.detail : '';
        setError(t(Object.hasOwn(translations.it, codice) ? codice as TranslationKey : 'debtSaveFailed'));
        return;
      }
      await load(); setEditing(null);
    } catch { setError(t('debtSaveFailed')); } finally { setBusy(false); }
  }

  if (!data) return <Card><CardContent className="p-10 text-center text-sm text-[#71807c]">{error || t('updating')}</CardContent></Card>;
  const metriche = [
    [t('debtTotal'), formatEuro(data.summary.totalDebt)],
    [t('debtAverageRate'), data.summary.weightedRate == null ? '—' : `${data.summary.weightedRate.toLocaleString(locale)}%`],
    [t('debtMonthlyService'), formatEuro(data.summary.monthlyService)],
    [t('configureDebt'), t('debtConfigured', { configured: data.summary.configured, total: data.summary.total })],
  ];
  // Il tipo separa i due strumenti: il compilatore non lascia leggere il piano
  // di una linea ne' l'esposizione di un prestito.
  const linee = data.items.filter((item): item is CreditLineItem => item.kind === 'credit_line');
  const prestiti = data.items.filter((item): item is TermLoanItem => item.kind === 'term_loan');

  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><p role="status" className="text-sm text-[#71807c]">{t('debtAsOf', { date: formatDate(`${data.asOf}T12:00:00`) })}</p><Button onClick={onNewAccount} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><Plus className="size-4" />{t('newLiability')}</Button></div>
    {error && <p role="alert" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-3 text-sm text-[#bd5e46]">{error}</p>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{metriche.map(([label, value], index) => <Card key={label} className={index === 0 ? 'border-0 bg-[var(--money-deep)] text-white' : 'border-black/6 bg-white'}><CardContent className="p-5"><p className={`text-xs ${index === 0 ? 'text-white/60' : 'text-[#71807c]'}`}>{label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p></CardContent></Card>)}</div>
    {data.summary.unclassifiedCount > 0 && <div role="status" className="rounded-xl border border-[#f4d8ce] bg-[#fce9e3] px-4 py-3 text-sm text-[#a05f4e]">{t('debtUnclassifiedSummary', { count: data.summary.unclassifiedCount, amount: formatEuro(data.summary.unclassifiedAmount) })}</div>}
    {!data.items.length && <Card><CardContent className="p-10 text-center"><CreditCard className="mx-auto mb-3 size-8 text-[#87918e]" /><p className="text-sm text-[#71807c]">{t('debtNoAccounts')}</p><Button className="mt-4" onClick={onNewAccount}>{t('newLiability')}</Button></CardContent></Card>}
    {linee.length > 0 && <>
      <p className="text-xs font-semibold uppercase tracking-wide text-[#87918e]">{t('debtCreditLines')}</p>
      <div className="grid gap-4 xl:grid-cols-2">{linee.map((linea) => <CreditLineCard key={linea.accountId} item={linea} onEdit={() => edit(linea)} onDelete={() => requestDelete(linea.accountId, linea.name, linea.name)} />)}</div>
    </>}
    {prestiti.length > 0 && linee.length > 0 && <p className="mt-2 text-xs font-semibold uppercase tracking-wide text-[#87918e]">{t('debtTermLoans')}</p>}
    {prestiti.map((item) => <Card key={item.accountId} className="border-black/6 bg-white shadow-sm">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3"><div><CardTitle className="text-[17px]">{item.name}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{item.profile ? `${item.profile.annualRate.toLocaleString(locale)}% · ${t(`debtType_${item.profile.debtType}` as TranslationKey)} · ${t(`debtStatus_${item.profile.status}` as TranslationKey)}` : t('debtScheduleEmpty')}</p>{item.unclassified.count > 0 && <p className="mt-1 text-xs font-medium text-[#a05f4e]">{t('debtUnclassifiedSummary', { count: item.unclassified.count, amount: formatEuro(item.unclassified.amount) })}</p>}</div><div className="flex flex-wrap gap-2"><Button size="sm" onClick={() => { const due = item.nextPayment ? Math.abs((new Date(`${item.nextPayment.dueOn}T12:00:00`).getTime() - Date.now()) / 86400000) : Infinity; onPayment(item.name, due <= 10 && item.nextPayment ? { principal: item.nextPayment.principal, interest: item.nextPayment.interest } : undefined); }}><ArrowRightLeft className="size-4" />{t('registerDebtPayment')}</Button><Button size="sm" variant="outline" onClick={() => edit(item)}><Pencil className="size-4" />{t('configureDebt')}</Button><Button size="sm" variant="ghost" onClick={() => { const account = accounts.find((row) => row.id === item.accountId); if (account) onEditAccount(account); }}>{t('account')}</Button><Button variant="ghost" size="icon" aria-label={`${t('delete')} ${item.name}`} onClick={() => requestDelete(item.accountId, item.name, item.name)}><Trash2 className="size-4" /></Button></div></CardHeader>
      <CardContent className="space-y-4">
      <div className="grid gap-3 lg:grid-cols-3">
        <div className="rounded-xl border border-[#bd5e46]/15 bg-[#fff9f6] p-4"><p className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#9b513e]">{t('debtPlanSection')}</p><div className="space-y-2">{[
          [t('plannedPrincipal'), item.profile ? formatEuro(item.profile.plannedDrawdowns.length ? item.profile.plannedDrawdowns.reduce((sum, row) => sum + row.amount, 0) : item.profile.originalPrincipal) : '—'],
          [t('theoreticalRemaining'), item.theoreticalRemaining == null ? '—' : formatEuro(item.theoreticalRemaining)],
          [t('interestDueToDate'), item.comparison ? formatEuro(item.comparison.plannedInterest) : '—'],
        ].map(([label, value]) => <div key={label} className="flex items-baseline justify-between gap-3"><span className="text-xs text-[#71807c]">{label}</span><span className="text-sm font-semibold tabular-nums">{value}</span></div>)}</div>{!item.profile && <p className="mt-3 text-xs text-[#9b513e]">{t('unconfiguredInterest')}</p>}</div>
        <div className="rounded-xl border border-[#2d7b65]/15 bg-[#f3faf7] p-4"><p className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#2d7b65]">{t('debtAccruedSection')}</p><div className="space-y-2">{[
          [t('drawnPrincipal'), formatEuro(item.drawnPrincipal)],
          [t('chargedInterest'), item.interestCharged == null ? '—' : formatEuro(item.interestCharged)],
        ].map(([label, value]) => <div key={label} className="flex items-baseline justify-between gap-3"><span className="text-xs text-[#71807c]">{label}</span><span className="text-sm font-semibold tabular-nums">{value}</span></div>)}</div><p className="mt-3 text-[11px] leading-4 text-[#71807c]">{item.profile ? t('chargedInterestHint') : t('unconfiguredInterest')}</p></div>
        <div className="rounded-xl border border-black/8 bg-[#f7f8f5] p-4"><p className="mb-3 text-xs font-semibold uppercase tracking-wide text-[#52615d]">{t('debtPaymentsSection')}</p><div className="space-y-2">{[
          [t('actualRepaid'), formatEuro(item.principalRepaid)],
          [t('interestPaid'), formatEuro(item.interestPaid)],
        ].map(([label, value]) => <div key={label} className="flex items-baseline justify-between gap-3"><span className="text-xs text-[#71807c]">{label}</span><span className="text-sm font-semibold tabular-nums">{value}</span></div>)}</div></div>
      </div>
      <div className="rounded-xl bg-[var(--money-deep)] p-4 text-white"><p className="mb-3 text-xs font-semibold uppercase tracking-wide text-white/60">{t('remainingToPay')}</p><div className="grid gap-3 sm:grid-cols-3">{[
        [t('remainingDebt'), formatEuro(item.outstanding)],
        [t('interestOutstanding'), item.interestOutstanding == null ? '—' : formatEuro(item.interestOutstanding)],
        [t('actualTotalDebt'), item.actualTotalDebt == null ? '—' : formatEuro(item.actualTotalDebt)],
      ].map(([label, value], index) => <div key={label} className={index === 2 ? 'sm:border-l sm:border-white/15 sm:pl-3' : ''}><p className="text-[11px] text-white/60">{label}</p><p className={`mt-1 tabular-nums ${index === 2 ? 'text-xl font-semibold' : 'text-sm font-medium'}`}>{value}</p></div>)}</div></div>
      {Math.abs(item.reconciliationDifference) >= 0.01 && <p role="status" className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{t('debtReconciliation', { amount: formatEuro(item.reconciliationDifference) })}</p>}
      {item.nextPayment && <p className="text-xs text-[#52615d]"><span className="font-medium">{t('theoreticalNextPayment')}:</span> {formatEuro(item.nextPayment.payment)} · {formatDate(`${item.nextPayment.dueOn}T12:00:00`)}</p>}
      <Button variant="outline" size="sm" aria-expanded={expanded === item.accountId} onClick={() => setExpanded(expanded === item.accountId ? null : item.accountId)}>{expanded === item.accountId ? t('close') : t('details')}</Button>
      {expanded === item.accountId && <div className="grid gap-5 border-t border-black/6 pt-4 xl:grid-cols-2">
        <div><p className="mb-3 text-sm font-semibold">{t('debtPlanVsActual')}</p>{item.schedule.length ? <>
          {item.comparison && <p className={`mb-4 rounded-lg px-3 py-2 text-xs font-medium ${item.difference != null && item.difference <= 0 ? 'bg-[#e5f3ed] text-[#2d7b65]' : 'bg-[#fce9e3] text-[#bd5e46]'}`}>{t((item.difference ?? 0) <= 0 ? 'debtAheadOfPlan' : 'debtBehindPlan', { amount: formatEuro(Math.abs(item.difference ?? 0)) })}</p>}
          <p className="mb-2 text-xs font-semibold text-[#52615d]">{t('debtBalanceChart')}</p><ChartContainer config={{ plannedDebt: { label: t('theoreticalRemaining'), color: '#bd5e46' }, actualDebt: { label: t('actualTotalDebt'), color: '#2d7b65' } }} className="h-[200px] w-full"><LineChart accessibilityLayer data={item.trend}><CartesianGrid vertical={false} /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={35} /><YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(value) => formatCompactEuro(Number(value))} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => formatEuro(Number(value))} />} /><ChartLegend content={<ChartLegendContent />} /><Line dataKey="plannedDebt" stroke="var(--color-plannedDebt)" strokeWidth={2.5} dot={false} /><Line dataKey="actualDebt" stroke="var(--color-actualDebt)" strokeWidth={2.5} dot={false} connectNulls={false} /></LineChart></ChartContainer>
          <p className="mb-2 mt-5 text-xs font-semibold text-[#52615d]">{t('debtCapitalFlowsChart')}</p><ChartContainer config={{ actualDrawn: { label: t('drawnPrincipal'), color: '#457b9d' }, actualRepaid: { label: t('actualRepaid'), color: '#2d7b65' } }} className="h-[200px] w-full"><LineChart accessibilityLayer data={item.trend}><CartesianGrid vertical={false} /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={35} /><YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(value) => formatCompactEuro(Number(value))} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => formatEuro(Number(value))} />} /><ChartLegend content={<ChartLegendContent />} /><Line dataKey="actualDrawn" stroke="var(--color-actualDrawn)" strokeWidth={2.5} dot={false} connectNulls={false} /><Line dataKey="actualRepaid" stroke="var(--color-actualRepaid)" strokeWidth={2.5} dot={false} connectNulls={false} /></LineChart></ChartContainer>
          <p className="mb-2 mt-5 text-xs font-semibold text-[#52615d]">{t('debtInterestChart')}</p><ChartContainer config={{ plannedInterest: { label: t('plannedInterestLine'), color: '#d49a3a' }, actualInterestCharged: { label: t('actualChargedInterestLine'), color: '#bd5e46' }, actualInterestPaid: { label: t('actualInterestLine'), color: '#6473b8' } }} className="h-[200px] w-full"><LineChart accessibilityLayer data={item.trend}><CartesianGrid vertical={false} /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={35} /><YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(value) => formatCompactEuro(Number(value))} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => formatEuro(Number(value))} />} /><ChartLegend content={<ChartLegendContent />} /><Line dataKey="plannedInterest" stroke="var(--color-plannedInterest)" strokeWidth={2} strokeDasharray="5 4" dot={false} /><Line dataKey="actualInterestCharged" stroke="var(--color-actualInterestCharged)" strokeWidth={2.5} dot={false} connectNulls={false} /><Line dataKey="actualInterestPaid" stroke="var(--color-actualInterestPaid)" strokeWidth={2.5} dot={false} connectNulls={false} /></LineChart></ChartContainer>
          <p className="mb-3 mt-5 text-sm font-semibold">{t('amortizationPlan')}</p><div className="max-h-72 overflow-auto rounded-lg border border-black/8"><table className="w-full text-xs"><thead className="sticky top-0 bg-[#f4f5f1]"><tr><th className="px-3 py-2 text-left">#</th><th className="px-3 py-2 text-left">{t('date')}</th><th className="px-3 py-2 text-right">{t('amount')}</th><th className="px-3 py-2 text-right">{t('totalInterest')}</th><th className="px-3 py-2 text-right">{t('theoreticalRemaining')}</th></tr></thead><tbody>{item.schedule.map((row) => <tr key={row.number} className="border-t border-black/5"><td className="px-3 py-2">{row.number}</td><td className="px-3 py-2">{formatDate(`${row.dueOn}T12:00:00`)}</td><td className="px-3 py-2 text-right">{formatEuro(row.payment)}</td><td className="px-3 py-2 text-right">{formatEuro(row.interest)}</td><td className="px-3 py-2 text-right">{formatEuro(row.remaining)}</td></tr>)}</tbody></table></div>
        </> : <p className="text-sm text-[#71807c]">{item.scheduleIssue ? t(`debtIssue_${item.scheduleIssue}` as TranslationKey) : t('debtScheduleEmpty')}</p>}</div>
        <div><p className="mb-3 text-sm font-semibold">{t('debtRegister')}</p>{item.movements.length ? (() => {
          const paymentByTransaction = new Map(item.payments.flatMap((payment) => payment.transactionIds.map((id) => [id, payment] as const)));
          return <div className="max-h-[34rem] overflow-auto rounded-lg border border-black/8"><table className="w-full text-xs"><thead className="sticky top-0 bg-[#f4f5f1]"><tr><th className="px-3 py-2 text-left">{t('date')}</th><th className="px-3 py-2 text-left">{t('fieldDescription')}</th><th className="px-3 py-2 text-right">{t('principalShare')}</th><th className="px-3 py-2 text-right">{t('debtInterestCharges')}</th><th className="px-3 py-2 text-right">{t('debtEffect')}</th></tr></thead><tbody>{item.movements.map((movement) => {
            const payment = paymentByTransaction.get(movement.id);
            const label = payment ? t(payment.kind === 'drawdown' ? 'debtDrawdown' : payment.kind === 'charge' ? 'debtCharge' : 'debtRepayment') : t(({ Income: 'incomeType', Expenses: 'expensesType', Transfers: 'transfersType', Investment: 'investmentType', Debt: 'debtTypeMovement' } as Record<string, TranslationKey>)[movement.type] ?? 'relatedMovements');
            const effect = movement.effect;
            return <tr key={movement.id} className={`border-t border-black/5 ${payment && !payment.classified ? 'bg-[#fce9e3]' : ''}`}><td className="whitespace-nowrap px-3 py-2">{formatDate(`${movement.occurredOn}T12:00:00`)}</td><td className="px-3 py-2"><p className="font-medium">{label}</p><p className="text-[#87918e]">{movement.description}</p>{payment && !payment.classified && <p className="font-medium text-[#a05f4e]">{t('debtUnclassified')}</p>}<Button type="button" size="sm" variant="ghost" className="mt-1 h-7 px-2" onClick={() => void editMovement(movement.id)}><Pencil className="size-3.5" />{t('edit')}</Button></td><td className="px-3 py-2 text-right tabular-nums">{payment ? formatEuro(payment.principal) : '—'}</td><td className="px-3 py-2 text-right tabular-nums">{payment ? formatEuro(payment.interest) : '—'}</td><td className={`px-3 py-2 text-right font-semibold tabular-nums ${effect > 0 ? 'text-[#bd5e46]' : 'text-[#2d7b65]'}`}>{effect > 0 ? '+' : ''}{formatEuro(effect)}</td></tr>;
          })}</tbody></table></div>;
        })() : <p className="text-sm text-[#71807c]">{t('noTransactions')}</p>}</div>
      </div>}
      </CardContent>
    </Card>)}
    {data.orphaned.map((item) => <Card key={`orphan-${item.accountId}`}><CardContent className="flex items-center justify-between gap-3 p-5"><div><p className="font-medium">{t(item.kind === 'credit_line' ? 'debtKindCreditLine' : 'debtKindTermLoan')} · {item.accountId}</p><p className="text-sm text-[#71807c]">{t('debtWithoutAccount')}</p>{item.notes && <p className="text-sm">{item.notes}</p>}</div><Button variant="outline" onClick={() => requestDelete(item.accountId, t(item.kind === 'credit_line' ? 'debtKindCreditLine' : 'debtKindTermLoan'), null)}>{t('delete')}</Button></CardContent></Card>)}
    <Dialog open={Boolean(deleting)} onOpenChange={(open) => { if (!open && !deleteBusy) setDeleting(null); }}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t('delete')} · {deleting?.name}</DialogTitle><DialogDescription>{t(deleting?.accountName != null ? 'debtDeleteLinkedDesc' : 'debtDeleteOrphanDesc')}</DialogDescription></DialogHeader>
        {deleting?.accountName != null && <label className="flex items-start gap-3 text-sm"><input type="checkbox" checked={confirmAccount} disabled={deleteBusy} onChange={(event) => setConfirmAccount(event.target.checked)} />{t('debtDeleteAlsoAccount', { name: deleting.accountName })}</label>}
        {deleteBusy && <p role="status" className="text-sm">{t('debtDeleteBusy')}</p>}
        {deleteError && <p role="alert" className="text-sm text-[#bd5e46]">{deleteError}</p>}
        <DialogFooter><Button variant="outline" disabled={deleteBusy} onClick={() => setDeleting(null)}>{t('cancel')}</Button><Button variant="destructive" disabled={deleteBusy || (deleting?.accountName != null && !confirmAccount)} onClick={() => void removeDebt()}>{t('delete')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
    <Dialog open={Boolean(editing)} onOpenChange={(open) => { if (!open) setEditing(null); }}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader><DialogTitle>{t('configureDebt')} · {editing?.name}</DialogTitle><DialogDescription>{t('helpDebitiDep')}</DialogDescription></DialogHeader>
        {editing && <form key={editing.accountId} onSubmit={save} className="space-y-4">
          <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('debtKind')}
            <select name="kind" value={tipoProfilo} onChange={(event) => { setTipoProfilo(event.target.value as 'term_loan' | 'credit_line'); if (event.target.value === 'term_loan' && !drawdowns.length) setDrawdowns([{ occurredOn: today, amount: editing.kind === 'credit_line' ? Math.max(editing.exposure, 1) : Math.max(editing.outstanding, 1) }]); }} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring">
              <option value="term_loan">{t('debtKindTermLoan')}</option>
              <option value="credit_line">{t('debtKindCreditLine')}</option>
            </select>
            <span className="block pt-1 font-normal leading-5 text-[#7b8784]">{t('debtKindHint')}</span>
          </label>
          {tipoProfilo === 'credit_line' && <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('debtCreditLimit')}
              <Input name="credit_limit" type="number" min="0" step="0.01" placeholder={t('debtNoLimitPlaceholder')} defaultValue={editing.profile?.creditLimit ?? ''} />
            </label>
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('startDate')}
              <Input required name="start_date" type="date" defaultValue={editing.profile?.startDate ?? today} />
            </label>
          </div>}
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('debtType')}<select name="debt_type" defaultValue={editing.profile?.debtType ?? 'other'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="mortgage">{t('debtType_mortgage')}</option><option value="personal">{t('debtType_personal')}</option><option value="leasing">{t('debtType_leasing')}</option><option value="revolving">{t('debtType_revolving')}</option><option value="margin">{t('debtType_margin')}</option><option value="informal">{t('debtType_informal')}</option><option value="other">{t('debtType_other')}</option></select></label>
            <div className="rounded-lg bg-[#f7f8f5] p-3 text-xs text-[#52615d]"><p>{t(tipoProfilo === 'credit_line' ? 'debtExposure' : 'startingAmount')}</p><p className="mt-1 text-lg font-semibold text-[#17231f]">{formatEuro(tipoProfilo === 'credit_line' ? (editing.kind === 'credit_line' ? editing.exposure : editing.actualTotalDebt ?? 0) : drawdowns.reduce((sum, row) => sum + Number(row.amount || 0), 0))}</p></div>
          </div>
          {tipoProfilo === 'term_loan' && <div className="space-y-2 rounded-xl border border-black/8 p-3">
            <div><p className="text-xs font-semibold text-[#52615d]">{t('plannedDrawdowns')}</p><p className="text-[11px] text-[#87918e]">{t('plannedDrawdownsHint')}</p></div>
            {drawdowns.map((row, index) => <div key={index} className="grid grid-cols-[1fr_1fr_auto] gap-2"><Input aria-label={t('date')} required type="date" value={row.occurredOn} onChange={(event) => setDrawdowns((values) => values.map((value, position) => position === index ? { ...value, occurredOn: event.target.value } : value))} /><Input aria-label={t('amount')} required min="0.01" step="0.01" type="number" value={row.amount} onChange={(event) => setDrawdowns((values) => values.map((value, position) => position === index ? { ...value, amount: Number(event.target.value) } : value))} /><Button aria-label={t('delete')} type="button" variant="ghost" size="icon" disabled={drawdowns.length === 1} onClick={() => setDrawdowns((values) => values.filter((_, position) => position !== index))}><Trash2 className="size-4" /></Button></div>)}
            <Button type="button" size="sm" variant="outline" onClick={() => setDrawdowns((values) => [...values, { occurredOn: values.at(-1)?.occurredOn ?? today, amount: 0 }])}><Plus className="size-4" />{t('addDrawdown')}</Button>
          </div>}
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('annualRate')} %<Input required min="0" step="0.0001" type="number" name="annual_rate" defaultValue={editing.profile?.annualRate ?? 0} /></label>
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('rateType')}<select name="rate_type" defaultValue={editing.profile?.rateType ?? 'fixed'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="fixed">{t('debtRate_fixed')}</option><option value="variable">{t('debtRate_variable')}</option><option value="mixed">{t('debtRate_mixed')}</option></select></label>
          </div>
          {tipoProfilo === 'term_loan' && <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('frequency')}<select name="payment_frequency" defaultValue={editing.profile?.paymentFrequency ?? 'monthly'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="monthly">{t('debtFrequency_monthly')}</option><option value="quarterly">{t('debtFrequency_quarterly')}</option><option value="annual">{t('debtFrequency_annual')}</option></select></label>
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('paymentStructure')}<select name="payment_structure" defaultValue={editing.profile?.paymentStructure ?? 'amortizing'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="amortizing">{t('debtPlanFixedPayment')}</option><option value="constant_principal">{t('debtPlanConstantPrincipal')}</option><option value="interest_only">{t('debtPlanInterestOnly')}</option><option value="bullet">{t('debtPlanBullet')}</option></select></label>
            {/* Durante il preammortamento gli interessi si pagano ogni scadenza
                oppure si sommano al debito. I contratti fanno entrambe le cose
                e non si deduce da nient'altro: va scelto. */}
            <label className="col-span-2 space-y-1 text-xs font-medium text-[#52615d]">{t('graceInterestLabel')}<select name="grace_interest" defaultValue={editing.profile?.graceInterest ?? 'paid'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="paid">{t('graceInterestPaid')}</option><option value="capitalised">{t('graceInterestCapitalised')}</option></select><span className="block font-normal leading-4 text-[#87918e]">{t('graceInterestHint')}</span></label>
          </div>}
          <div className="grid grid-cols-2 gap-3">
            {tipoProfilo === 'term_loan' && <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('repaymentStart')}<Input required type="date" name="repayment_start_date" defaultValue={editing.profile?.repaymentStartDate ?? drawdowns.at(-1)?.occurredOn ?? today} /></label>}
            <label className="space-y-1 text-xs font-medium text-[#52615d]">{t('fieldDeadline')}<Input required type="date" name="end_date" defaultValue={editing.profile?.endDate ?? defaultEnd} /></label>
          </div>
          <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('status')}<select name="status" defaultValue={editing.profile?.status ?? 'active'} className="h-10 w-full rounded-lg border border-input bg-white px-2 text-sm"><option value="active">{t('debtStatus_active')}</option><option value="paid">{t('debtStatus_paid')}</option><option value="suspended">{t('debtStatus_suspended')}</option></select></label>
          <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('note')}<Input name="notes" defaultValue={editing.profile?.notes ?? ''} /></label>
          {error && <p className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{error}</p>}
          <DialogFooter><Button type="button" variant="outline" onClick={() => setEditing(null)}>{t('cancel')}</Button><Button type="submit" disabled={busy}>{busy ? t('savingEllipsis') : t('save')}</Button></DialogFooter>
        </form>}
      </DialogContent>
    </Dialog>
  </div>;
}

// Il riequilibrio non e' un ordine da eseguire: e' la distanza fra dove sei e
// dove hai detto di voler stare. Il verso lo decide il segno dell'importo, che
// arriva dal server; qui si sceglie solo il colore. Verde sotto peso (da
// comprare), ambra sopra (da vendere).
/** I pesi obiettivo di tutti gli strumenti, modificabili insieme.
 *
 * Uno alla volta dentro il dialogo dello strumento non si capiva a che somma
 * si stesse arrivando: il totale di una ripartizione e' l'unica cosa che conta
 * guardare mentre la si scrive, e si vede solo mettendo le righe una sotto
 * l'altra. Si scrive in percentuale e si salva in frazione, come nel dialogo.
 */
function TargetWeightsEditor({ posizioni, onInstrumentSave }: {
  posizioni: InvestmentPosition[];
  onInstrumentSave: (instrumentId: number, payload: Record<string, string | number | null>) => Promise<void>;
}) {
  const { t, formatNumber } = useI18n();
  const modificabili = posizioni.filter((p) => p.instrumentId !== null && p.isOpen);
  const pesoIniziale = (p: InvestmentPosition) => p.targetWeight === null ? '' : String(Math.round(p.targetWeight * 1000) / 10);
  const [bozza, setBozza] = useState<Record<number, string>>({});
  const [salvando, setSalvando] = useState(false);
  const [errore, setErrore] = useState('');
  const valore = (p: InvestmentPosition) => bozza[p.instrumentId!] ?? pesoIniziale(p);
  const numero = (testo: string) => { const n = Number(testo.replace(',', '.')); return testo.trim() === '' || !Number.isFinite(n) ? 0 : n; };
  const totale = modificabili.reduce((somma, p) => somma + numero(valore(p)), 0);
  const cambiate = modificabili.filter((p) => valore(p) !== pesoIniziale(p));
  // Cento esatto e' irraggiungibile scrivendo a mano con un decimale: 99,9 e
  // 100,1 sono configurazioni sane, e bloccare il salvataggio sarebbe una
  // pedanteria. Si segnala il verde solo quando ci si sta davvero dentro.
  const inLinea = Math.abs(totale - 100) < 0.05;

  async function salva() {
    setSalvando(true); setErrore('');
    try {
      for (const p of cambiate) {
        const scritto = valore(p).replace(',', '.').trim();
        await onInstrumentSave(p.instrumentId!, { target_weight: scritto === '' ? null : Number(scritto) / 100 });
      }
      setBozza({});
    } catch { setErrore(t('allocWeightsSaveFailed')); }
    finally { setSalvando(false); }
  }

  if (!modificabili.length) return null;
  // La riga apribile si porta il triangolo del browser e un fondo che cambia
  // al passaggio: senza, non si capisce che sotto la tabella delle proposte
  // comincia un'altra cosa, non un secondo pezzo della stessa.
  return <details className="group border-t border-black/6">
    <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-3 text-sm font-medium text-[#52615d] transition hover:bg-[#f8f9f6] [&::-webkit-details-marker]:hidden">
      {t('allocWeightsEditor')}
      <ChevronDown className="size-4 shrink-0 text-black/45 transition group-open:rotate-180" />
    </summary>
    <div className="overflow-x-auto"><table className="w-full text-sm">
      <thead className="text-xs text-[#87918e]"><tr>
        <th className="px-5 py-2 text-left">{t('instrument')}</th>
        <th className="px-3 py-2 text-right">{t('allocTargetWeight')}</th>
      </tr></thead>
      <tbody className="divide-y divide-black/5">{modificabili.map((p) => <tr key={p.instrumentId}>
        <td className="px-5 py-2">{p.name}</td>
        <td className="px-3 py-2 text-right">
          <Input type="number" step="0.1" min={0} max={100} value={valore(p)} placeholder="—"
            onChange={(event) => setBozza((corrente) => ({ ...corrente, [p.instrumentId!]: event.target.value }))}
            className="ml-auto h-8 w-24 text-right" />
        </td>
      </tr>)}</tbody>
      <tfoot><tr className="border-t border-black/10">
        <td className="px-5 py-3 text-xs font-medium text-[#52615d]">{t('allocWeightsTotal')}</td>
        <td className={`px-3 py-3 text-right font-semibold tabular-nums ${inLinea ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>
          {formatNumber(totale / 100, { style: 'percent', maximumFractionDigits: 1 })}
        </td>
      </tr></tfoot>
    </table></div>
    {errore && <p className="mx-5 mb-3 rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{errore}</p>}
    <div className="flex items-center justify-end gap-3 px-5 pb-4">
      {cambiate.length > 0 && <span className="text-xs text-[#7b8784]">{t('allocWeightsChanged', { count: cambiate.length })}</span>}
      <Button size="sm" disabled={salvando || cambiate.length === 0} onClick={salva}
        className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{t('save')}</Button>
    </div>
  </details>;
}

function RebalanceCard({ riordino, posizioni, onInstrumentSave }: {
  riordino: InvestmentDashboardData['rebalance'];
  posizioni: InvestmentPosition[];
  onInstrumentSave: (instrumentId: number, payload: Record<string, string | number | null>) => Promise<void>;
}) {
  const { t, formatEuro, formatNumber } = useI18n();
  const percentuale = (valore: number) => formatNumber(valore, { style: 'percent', maximumFractionDigits: 1 });
  const deriva = (valore: number) => formatNumber(valore, { style: 'percent', maximumFractionDigits: 1, signDisplay: 'always' });
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader className="pb-2"><CardTitle className="text-[17px]">{t('allocRebalance')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('allocRebalanceSubtitle')}</p></CardHeader>
    <CardContent className="p-0">
      {/* L'avviso sta sopra la tabella perche' e' l'unica cosa che puo' rendere
          sbagliati tutti i numeri sotto: se i pesi non tornano, la colpa non e'
          del calcolo. Senza pesi obiettivo, pero', non c'e' nessuna somma da
          correggere: li' l'avviso direbbe "sommano a 0%, non al 100%" sopra la
          riga che spiega che non c'e' niente da riequilibrare. */}
      {riordino.total > 0 && riordino.warnings.includes('pesi_non_sommano_a_cento') && <p role="alert" className="mx-5 mt-3 rounded-lg border border-[#f2d7cb] bg-[#fdf1ec] px-3 py-2 text-xs text-[#a05f4e]">{t('allocTargetSum', { sum: percentuale(riordino.declaredWeight) })}</p>}
      {riordino.total <= 0
        ? <p className="px-5 py-6 text-sm text-[#71807c]">{t('allocNoTargets')}</p>
        : riordino.rows.length === 0
          ? <p className="px-5 py-6 text-sm font-medium text-[#2d7b65]">{t('allocInLine')}</p>
          : <div className="overflow-x-auto"><table className="min-w-[640px] w-full text-sm">
            <thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr><th className="px-5 py-3 text-left">{t('instrument')}</th><th className="px-3 py-3 text-right">{t('allocCurrentWeight')}</th><th className="px-3 py-3 text-right">{t('allocTargetWeight')}</th><th className="px-3 py-3 text-right">{t('allocDrift')}</th><th className="px-5 py-3 text-right">{t('allocAmount')}</th></tr></thead>
            <tbody className="divide-y divide-black/5">{riordino.rows.map((riga) => {
              const comprare = riga.amount < 0;
              const tono = comprare ? 'text-[#2d7b65]' : 'text-[#9a7b2f]';
              return <tr key={riga.name}>
                <td className="px-5 py-3 font-medium">{riga.name}</td>
                <td className="px-3 py-3 text-right tabular-nums">{percentuale(riga.currentWeight)}</td>
                <td className="px-3 py-3 text-right tabular-nums">{percentuale(riga.targetWeight)}</td>
                <td className={`px-3 py-3 text-right tabular-nums ${tono}`}>{deriva(riga.drift)}</td>
                <td className={`px-5 py-3 text-right font-semibold tabular-nums ${tono}`}>{t(comprare ? 'allocBuy' : 'allocSell', { amount: formatEuro(Math.abs(riga.amount)) })}</td>
              </tr>;
            })}</tbody>
          </table></div>}
      <TargetWeightsEditor posizioni={posizioni} onInstrumentSave={onInstrumentSave} />
    </CardContent>
  </Card>;
}

// I motivi arrivano dal server come codici: la frase la sceglie la traduzione.
// Un codice che qui non compare non fa sparire la riga, dice solo che il
// rendimento non e' calcolabile.
const MOTIVI_RENDIMENTO: Record<string, TranslationKey> = {
  prezzo_mancante: 'returnReasonMissingPrice',
  storia_troppo_corta: 'returnReasonShortHistory',
  nessun_flusso: 'returnReasonNoFlows',
  flussi_senza_cambio_di_segno: 'returnReasonNoSignChange',
  xirr_non_converge: 'returnReasonNoConvergence',
};

function RendimentoCard({ titolo, spiegazione, esito }: {
  titolo: string;
  spiegazione: string;
  esito: InvestmentReturns['twr'];
}) {
  const { t, formatNumber } = useI18n();
  return <Card className="border-black/6 bg-white shadow-sm">
    <CardContent className="p-5">
      <p className="mb-4 text-sm font-medium text-[#71807c]">{titolo}</p>
      {/* Il motivo sta al posto del numero, in grigio: non un trattino e non
          uno zero, perche' "non ho guadagnato niente" e' un'altra cosa da
          "non si puo' sapere". */}
      {esito.value === null
        ? <p className="text-base font-medium text-[#71807c]">{t(MOTIVI_RENDIMENTO[esito.reason ?? ''] ?? 'returnReasonUnknown')}</p>
        : <p className={`text-[25px] font-semibold tracking-[-0.03em] tabular-nums ${esito.value >= 0 ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>{formatNumber(esito.value, { style: 'percent', maximumFractionDigits: 2, signDisplay: 'always' })}</p>}
      <p className="mt-2 text-xs text-[#618078]">{spiegazione}</p>
    </CardContent>
  </Card>;
}

function InvestmentsView({ dashboard, ledger, allocation, apiUrl, onQuotesChanged, onSave, onDelete, onInstrumentSave, onRefresh, accounts }: { apiUrl: string; onQuotesChanged: () => Promise<void>; dashboard: InvestmentDashboardData; ledger: InvestmentTransaction[]; allocation: InvestmentAllocationData; onSave: (transactionId: number | null, payload: Record<string, string | number | boolean>) => Promise<void>; onDelete: (transaction: InvestmentTransaction) => Promise<void>; onInstrumentSave: (instrumentId: number, payload: Record<string, string | number | null>) => Promise<void>; onRefresh: () => Promise<{ updated: number; errors: Array<{ code?: string; error?: string }> }>; accounts: Account[] }) {
  const { t, lang, locale, formatEuro, formatCompactEuro, formatDate, monthNames, formatPeriodLabel } = useI18n();
  const [tab, setTab] = useState<'portfolio' | 'ledger' | 'instruments' | 'allocation' | 'quotes'>('portfolio');
  const [showClosedPositions, setShowClosedPositions] = useState(false);
  // "Chiusa" vuol dire zero quote. Ma gli interessi del broker, una
  // commissione, un versamento nascono a zero quote: una riga cosi' non e'
  // una posizione finita, e' un conto, e nasconderla vorrebbe dire nascondere
  // i proventi proprio dove si leggono. Resta in tabella finche' ha qualcosa
  // da dire.
  const viva = (position: InvestmentPosition) => position.isOpen || position.incomeReceived !== 0 || position.feesPaid !== 0;
  const [instruments, setInstruments] = useState<InstrumentRow[]>([]);
  const [instrumentQuery, setInstrumentQuery] = useState('');
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const loadInstruments = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/investments/instruments`);
    if (response.ok) setInstruments(((await response.json()) as { items: InstrumentRow[] }).items);
  }, [apiUrl]);
  useEffect(() => { void loadInstruments(); }, [loadInstruments]);
  const [allocationDimension, setAllocationDimension] = useState<'instrument' | 'sector' | 'assetType' | 'holdings' | 'currency'>('instrument');
  const [refreshingProfiles, setRefreshingProfiles] = useState(false);
  const [profileMessage, setProfileMessage] = useState('');

  async function refreshProfiles() {
    setRefreshingProfiles(true); setProfileMessage('');
    try {
      const response = await fetch(`${apiUrl}/api/investments/refresh-profiles`, { method: 'POST' });
      if (!response.ok) throw new Error('profile-refresh');
      const result = await response.json() as { updated?: unknown[]; errors?: Array<{ code: string; retryMinutes: number | null }>; blocked?: boolean };
      if (!Array.isArray(result.updated) || !Array.isArray(result.errors) || typeof result.blocked !== 'boolean') throw new Error('profile-response');
      if (result.blocked) setProfileMessage(sourceErrorLabel(t, result.errors[0]?.code, result.errors[0]?.retryMinutes));
      else if (result.errors.length) setProfileMessage(t('allocationPartial', { ok: result.updated.length, ko: result.errors.length }));
      await onQuotesChanged();
    } catch {
      setProfileMessage(t('cannotRefreshComposition'));
    } finally { setRefreshingProfiles(false); }
  }
  const [editing, setEditing] = useState<InvestmentTransaction | null | undefined>(undefined);
  const [amountInput, setAmountInput] = useState('');
  const [unitsInput, setUnitsInput] = useState('');
  const [ledgerTypeInput, setLedgerTypeInput] = useState<LedgerOperationType>('Buy');
  const [linkTransactionOpen, setLinkTransactionOpen] = useState(false);
  useEffect(() => {
    setInstrumentQuery(editing?.name ?? '');
    setSuggestionsOpen(false);
    setAmountInput(editing ? String(editing.amount) : '');
    setUnitsInput(editing ? String(editing.units) : '');
    setLedgerTypeInput(editing?.transactionType ?? 'Buy');
    setLinkTransactionOpen(false);
  }, [editing]);
  // Lo split non si paga - l'importo e' zero - e quello che si digita e' il
  // rapporto, che sta nelle quote. Gli altri movimenti di solo contante non
  // hanno quote: chiedergliele vorrebbe dire far inventare un numero.
  // Cambiare tipo azzera i campi che il tipo nuovo non usa. Senza, le 10 quote
  // di un acquisto restavano nel campo che per lo split si chiama "Rapporto", e
  // salvando moltiplicavano la posizione per dieci.
  function cambiaTipoLedger(tipo: LedgerOperationType) {
    const eraSenzaQuote = tipo === 'Split' || SOLO_CONTANTE_DA_MOVIMENTO.includes(tipo);
    if (eraSenzaQuote !== (ledgerTypeInput === 'Split' || SOLO_CONTANTE_DA_MOVIMENTO.includes(ledgerTypeInput))
        || tipo === 'Split' || ledgerTypeInput === 'Split') setUnitsInput('');
    if (tipo === 'Split') setAmountInput('');
    setLedgerTypeInput(tipo);
  }

  const tipoSplit = ledgerTypeInput === 'Split';
  const tipoContante = tipoSplit || SOLO_CONTANTE_DA_MOVIMENTO.includes(ledgerTypeInput);
  // Il prezzo non si digita: e' importo diviso quantita', cosi' le tre cifre
  // non possono contraddirsi.
  const derivedPrice = (() => {
    const amount = Number(amountInput.replace(',', '.'));
    const units = Number(unitsInput.replace(',', '.'));
    if (!Number.isFinite(amount) || !Number.isFinite(units) || units === 0) return '';
    return (Math.abs(amount) / Math.abs(units)).toFixed(6);
  })();
  const instrumentNeedle = instrumentQuery.trim().toLowerCase();
  const instrumentSuggestions = instruments
    .filter((item) => !instrumentNeedle || item.name.toLowerCase().includes(instrumentNeedle))
    .slice(0, 8);
  const [configuring, setConfiguring] = useState<InvestmentPosition | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [ledgerSearchQuery, setLedgerSearchQuery] = useState('');
  const [ledgerTypeFilter, setLedgerTypeFilter] = useState('all');
  const [ledgerInstrumentFilter, setLedgerInstrumentFilter] = useState('all');
  const [ledgerYearFilter, setLedgerYearFilter] = useState('all');
  const [ledgerMonthFilter, setLedgerMonthFilter] = useState('all');
  const [ledgerVisibleLimit, setLedgerVisibleLimit] = useState(100);
  const [ledgerSelection, setLedgerSelection] = useState<Set<number>>(new Set());
  const [ledgerBulkField, setLedgerBulkField] = useState<'name' | 'transaction_type' | 'currency' | 'link'>('name');
  const [ledgerBulkValue, setLedgerBulkValue] = useState('');
  const [ledgerBulkBusy, setLedgerBulkBusy] = useState(false);
  const [ledgerBulkError, setLedgerBulkError] = useState('');
  const [ledgerLinkQuery, setLedgerLinkQuery] = useState('');
  const [ledgerLinkTransactions, setLedgerLinkTransactions] = useState<Transaction[]>([]);
  const [ledgerCsvOpen, setLedgerCsvOpen] = useState(false);
  const [ledgerCsvFile, setLedgerCsvFile] = useState<File | null>(null);
  const [ledgerCsvHeaders, setLedgerCsvHeaders] = useState<string[]>([]);
  const [ledgerCsvSample, setLedgerCsvSample] = useState<Array<Record<string, string>>>([]);
  const [ledgerCsvCount, setLedgerCsvCount] = useState(0);
  const [ledgerCsvMapping, setLedgerCsvMapping] = useState<Record<string, string>>({});
  const [ledgerCsvPreview, setLedgerCsvPreview] = useState<LedgerCsvItem[]>([]);
  const [ledgerCsvSelection, setLedgerCsvSelection] = useState<Set<number>>(new Set());
  const [ledgerCsvBusy, setLedgerCsvBusy] = useState(false);
  const [ledgerCsvError, setLedgerCsvError] = useState('');
  // Cambiando cio' che si vede, una selezione precedente non deve poter
  // modificare righe ormai nascoste e l'elenco riparte dalla prima pagina.
  useEffect(() => {
    setLedgerSelection(new Set());
    setLedgerVisibleLimit(100);
  }, [ledgerSearchQuery, ledgerTypeFilter, ledgerInstrumentFilter, ledgerYearFilter, ledgerMonthFilter]);
  const ledgerInstruments = useMemo(
    () => Array.from(new Set(ledger.map((row) => row.name))).sort((a, b) => a.localeCompare(b, locale)),
    [ledger, locale],
  );
  const ledgerYears = useMemo(
    () => Array.from(new Set(ledger.map((row) => row.occurredOn.slice(0, 4)))).sort().reverse(),
    [ledger],
  );
  const filteredLedger = useMemo(() => {
    const needle = ledgerSearchQuery.trim().toLocaleLowerCase(lang);
    return ledger.filter((row) => {
      const matchesSearch = !needle || `${row.name} ${row.notes ?? ''}`.toLocaleLowerCase(lang).includes(needle);
      const matchesType = ledgerTypeFilter === 'all' || row.transactionType === (ledgerTypeFilter as LedgerOperationType);
      const matchesInstrument = ledgerInstrumentFilter === 'all' || row.name === ledgerInstrumentFilter;
      const matchesPeriod = (ledgerYearFilter === 'all' || row.occurredOn.slice(0, 4) === ledgerYearFilter)
        && (ledgerMonthFilter === 'all' || row.occurredOn.slice(5, 7) === ledgerMonthFilter);
      return matchesSearch && matchesType && matchesInstrument && matchesPeriod;
    });
  }, [ledger, ledgerSearchQuery, ledgerTypeFilter, ledgerInstrumentFilter, ledgerYearFilter, ledgerMonthFilter, lang]);
  const visibleLedger = useMemo(() => filteredLedger.slice(0, ledgerVisibleLimit), [filteredLedger, ledgerVisibleLimit]);
  const ledgerLinkCandidates = useMemo(() => {
    const needle = ledgerLinkQuery.trim().toLocaleLowerCase(lang);
    return ledgerLinkTransactions.filter((transaction) => {
      const linked = new Set(transaction.linkedLedger?.map((item) => item.id) ?? []);
      if ([...ledgerSelection].every((id) => linked.has(id))) return false;
      return !needle || `${transaction.occurredOn} ${transaction.description} ${transaction.accountName ?? ''}`.toLocaleLowerCase(lang).includes(needle);
    });
  }, [ledgerLinkQuery, ledgerLinkTransactions, ledgerSelection, lang]);
  const historyConfig = useMemo(() => ({ marketValue: { label: t('value'), color: '#47a889' }, investedCapital: { label: t('investedCapital'), color: '#6d8ff4' } }) satisfies ChartConfig, [t]);
  // Il grafico in euro sale anche quando versi e il portafoglio non rende: la
  // lettura in percentuale separa le due cose.
  const returnConfig = useMemo(() => ({ returnRate: { label: t('returnRate'), color: '#9479d1' } }) satisfies ChartConfig, [t]);
  // Col confronto acceso la lettura in percentuale cambia numero, non solo
  // linea: `returnRate` e' il guadagno sul versato punto per punto, e accanto a
  // due curve che partono da 100 sarebbe un'altra unita' di misura. Le due
  // curve cumulate dicono la stessa cosa - chi ha reso di piu' - e si leggono
  // una sopra l'altra.
  const indice = dashboard.benchmark.from && dashboard.benchmark.symbol ? dashboard.benchmark.symbol : null;
  const confrontoConfig = useMemo(() => ({ twrCurve: { label: t('investTabPortfolio'), color: '#9479d1' }, benchmarkCurve: { label: indice ?? '', color: '#6d8ff4' } }) satisfies ChartConfig, [t, indice]);
  // Il confronto parte dal primo mese in comune: i mesi prima non sono
  // "piatti", sono mesi che l'indice non ha, e disegnarli a zero sarebbe una
  // bugia. Si mostrano solo quelli che il confronto lo hanno davvero.
  const puntiConfronto = useMemo(() => {
    const da = dashboard.benchmark.from;
    return indice && da ? dashboard.history.filter((punto) => punto.period >= da) : [];
  }, [dashboard.history, dashboard.benchmark.from, indice]);
  const [historyMode, setHistoryMode] = useState<'amount' | 'return'>('amount');
  const dimensionLabels: Record<'class' | 'area' | 'sector' | 'currency', string> = { class: t('dimClass'), area: t('dimArea'), sector: t('dimSector'), currency: t('dimCurrency') };

  async function saveLedger(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    const ledgerPayload = ledgerOperationPayload(form);
    const forceDuplicate = form.get('force_duplicate') !== null;
    try {
      if (!editing && linkTransactionOpen) {
        // Crea l'operazione di portafoglio + un movimento bank collegato in modo atomico.
        const response = await fetch(`${apiUrl}/api/investments/ledger/with-transaction${forceDuplicate ? '?force=true' : ''}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...ledgerPayload,
            linked_transaction: {
              occurred_on: String(form.get('tx_occurred_on') || form.get('occurred_on')),
              transaction_type: ledgerPayload.transaction_type === 'Sell' || ledgerPayload.transaction_type === 'Dividend' ? 'Income' : 'Expenses',
              category: String(form.get('tx_category') || 'Investimenti'),
              // L'importo di un movimento e' sempre positivo: il verso lo dice il
              // tipo. Un negativo qui veniva rifiutato con 422, e il rifiuto
              // annullava anche la riga di ledger gia' creata.
              amount: Math.abs(ledgerPayload.amount),
              account_name: String(form.get('tx_account_name') || '') || null,
              destination_name: null,
              goal: null,
              details: String(form.get('tx_details') || '') || null,
            },
          }),
        });
        if (!response.ok) {
          let detail = '';
          try {
            const errBody = await response.json() as { detail?: string | { code?: string; duplicate?: { occurredOn?: string } } | { msg?: string }[] };
            detail = typeof errBody.detail === 'string'
              ? errBody.detail
              : Array.isArray(errBody.detail)
                ? errBody.detail.map((item) => item.msg).join('; ')
                : errBody.detail?.code === 'ledgerDuplicate'
                  ? t('ledgerDuplicateFound', { date: errBody.detail.duplicate?.occurredOn ?? '—' })
                  : errBody.detail?.code && Object.hasOwn(translations.it, errBody.detail.code)
                    ? t(errBody.detail.code as TranslationKey)
                    : '';
          } catch { /* ignore */ }
          if (detail) setError(detail);
          throw new Error('save');
        }
      } else {
        await onSave(editing?.id ?? null, { ...ledgerPayload, force_duplicate: forceDuplicate });
      }
      setEditing(undefined);
      setLinkTransactionOpen(false);
    } catch (caught) {
      if (!(caught instanceof Error && caught.message === 'save')) {
        setError(caught instanceof Error && caught.message !== 'investment-save' ? caught.message : t('cannotSaveOperation'));
      }
    } finally { setBusy(false); }
  }

  async function saveInstrument(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault(); if (!configuring?.instrumentId) return;
    setBusy(true); setError(''); const form = new FormData(event.currentTarget);
    try {
      // Il peso si digita in percentuale e si salva come frazione: nel database
      // sta fra 0 e 1, ma scrivere "17,5" e' piu' naturale che "0,175". Campo
      // vuoto vuol dire "nessun obiettivo", ed e' l'unico modo per toglierlo.
      const pesoScritto = String(form.get('target_weight') || '').replace(',', '.').trim();
      await onInstrumentSave(configuring.instrumentId, { provider_symbol: String(form.get('provider_symbol') || '').trim() || null, asset_class: String(form.get('asset_class')).trim(), area: String(form.get('area')).trim(), sector: String(form.get('sector')).trim(), currency: String(form.get('currency')).trim().toUpperCase(), target_weight: pesoScritto === '' ? null : Number(pesoScritto) / 100 });
      setConfiguring(null);
    } catch { setError(t('cannotSaveInstrumentConfig')); } finally { setBusy(false); }
  }

  async function loadLedgerLinkTransactions() {
    if (ledgerLinkTransactions.length) return;
    const response = await fetch(`${apiUrl}/api/transactions?transaction_type=Investment&limit=10000`);
    if (!response.ok) throw new Error('movements');
    setLedgerLinkTransactions(((await response.json()) as { items: Transaction[] }).items);
  }

  async function applyLedgerBulk() {
    setLedgerBulkBusy(true); setLedgerBulkError('');
    try {
      const response = await fetch(`${apiUrl}/api/investments/ledger/${ledgerBulkField === 'link' ? 'bulk-link' : 'bulk'}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ledgerBulkField === 'link'
          ? { ids: [...ledgerSelection], transaction_id: Number(ledgerBulkValue) }
          : { ids: [...ledgerSelection], changes: { [ledgerBulkField]: ledgerBulkValue } }),
      });
      if (!response.ok) {
        const body = await response.json() as { detail?: string | { code?: string } };
        const code = typeof body.detail === 'string' ? body.detail : body.detail?.code;
        throw new Error(code === 'ledgerGroupUnbalanced' ? t('ledgerGroupUnbalanced') : t('bulkFailed'));
      }
      await onQuotesChanged();
      setLedgerSelection(new Set()); setLedgerBulkValue(''); setLedgerLinkQuery('');
    } catch (caught) {
      setLedgerBulkError(caught instanceof Error ? caught.message : t('bulkFailed'));
    } finally { setLedgerBulkBusy(false); }
  }

  async function chooseLedgerCsv() {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = '.csv,text/csv';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      setLedgerCsvOpen(true); setLedgerCsvFile(file); setLedgerCsvBusy(true); setLedgerCsvError(''); setLedgerCsvPreview([]);
      try {
        const form = new FormData(); form.append('file', file);
        const response = await fetch(`${apiUrl}/api/investments/ledger/import/csv/columns`, { method: 'POST', body: form });
        if (!response.ok) throw new Error();
        const data = await response.json() as { headers: string[]; sample: Array<Record<string, string>>; count: number };
        const normalized = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z]/g, '');
        const aliases: Record<string, string[]> = {
          date: ['date', 'data'], name: ['name', 'nome', 'strumento', 'instrument'], type: ['type', 'tipo', 'operazione', 'operation'],
          amount: ['amount', 'importo', 'total', 'totale'], units: ['units', 'quote', 'quantity', 'quantita'],
          price: ['price', 'prezzo'], currency: ['currency', 'valuta'],
        };
        setLedgerCsvHeaders(data.headers); setLedgerCsvSample(data.sample); setLedgerCsvCount(data.count);
        setLedgerCsvMapping(Object.fromEntries(Object.entries(aliases).map(([field, names]) => [field, data.headers.find((header) => names.includes(normalized(header))) ?? ''])));
      } catch { setLedgerCsvError(t('statementEmpty')); } finally { setLedgerCsvBusy(false); }
    };
    input.click();
  }

  async function previewLedgerCsv() {
    if (!ledgerCsvFile) return;
    setLedgerCsvBusy(true); setLedgerCsvError('');
    try {
      const form = new FormData(); form.append('file', ledgerCsvFile); form.append('mapping', JSON.stringify(ledgerCsvMapping));
      const response = await fetch(`${apiUrl}/api/investments/ledger/import/csv/preview`, { method: 'POST', body: form });
      if (!response.ok) throw new Error();
      const data = await response.json() as { items: LedgerCsvItem[] };
      setLedgerCsvPreview(data.items);
      setLedgerCsvSelection(new Set(data.items.filter((item) => !item.duplicate && !item.error).map((item) => item.row)));
    } catch { setLedgerCsvError(t('statementRequiredFields')); } finally { setLedgerCsvBusy(false); }
  }

  async function importLedgerCsv() {
    const payload = ledgerCsvPreview.filter((item) => ledgerCsvSelection.has(item.row)).map((item) => ({
      occurred_on: item.occurred_on, name: item.name, transaction_type: item.transaction_type,
      amount: item.amount, units: item.units, price: item.price, currency: item.currency,
    }));
    if (!payload.length) return;
    setLedgerCsvBusy(true); setLedgerCsvError('');
    try {
      const response = await fetch(`${apiUrl}/api/investments/ledger/batch`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error();
      await onQuotesChanged(); setLedgerCsvOpen(false); setLedgerCsvFile(null); setLedgerCsvPreview([]);
    } catch { setLedgerCsvError(t('bulkFailed')); } finally { setLedgerCsvBusy(false); }
  }

  return <div className="space-y-5">
    <div className="flex flex-wrap gap-2 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm">
      {([['portfolio', t('investTabPortfolio')], ['ledger', t('investTabLedger')], ['instruments', t('investTabInstruments')], ['allocation', t('investTabAllocation')], ['quotes', t('investTabQuotes')]] as const).map(([value, label]) => <button key={value} type="button" onClick={() => setTab(value)} className={`rounded-lg px-3.5 py-2 text-sm font-medium transition ${tab === value ? 'bg-[var(--money-deep)] text-white' : 'text-[#61706c] hover:bg-[#f0f2ee]'}`}>{label}</button>)}
    </div>

    {tab === 'portfolio' && <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4"><MetricCard title={t('portfolioValue')} value={dashboard.snapshot.marketValue} change={dashboard.snapshot.period ? t('excelHistory', { period: formatDate(`${dashboard.snapshot.period}T12:00:00`, { month: 'long', year: 'numeric' }) }) : t('noHistory')} icon={Landmark} tone="worth" /><MetricCard title={t('investedCapital')} value={dashboard.snapshot.investedCapital} change={t('netContributionsOverTime')} icon={CircleDollarSign} tone="saving" /><MetricCard title={t('gainLoss')} value={dashboard.snapshot.gain} change={t('percentOnCapital', { percent: dashboard.snapshot.returnRate.toLocaleString(locale) })} icon={TrendingUp} tone={dashboard.snapshot.gain >= 0 ? 'income' : 'expense'} /><MetricCard title={t('connectedQuotes')} value={dashboard.ledger.quotedPositions} valueLabel={`${dashboard.ledger.quotedPositions}/${dashboard.ledger.activePositions}`} change={t('withLocalCache')} icon={RefreshCw} tone="worth" /><RendimentoCard titolo={t('twrReturn')} spiegazione={t('twrHint')} esito={dashboard.returns.twr} /><RendimentoCard titolo={t('xirrReturn')} spiegazione={t('xirrHint')} esito={dashboard.returns.xirr} /></div>
      {/* Il metodo si dichiara: chi legge un rendimento ha diritto di sapere su
          cosa e' calcolato, e da quando. */}
      {dashboard.returns.since && <p className="text-xs text-[#7b8784]">{t('returnsMethod', { from: formatDate(`${dashboard.returns.since}T12:00:00`, { month: 'long', year: 'numeric' }), to: formatDate(`${dashboard.returns.asOf}T12:00:00`, { month: 'long', year: 'numeric' }) })}</p>}
      <Card className="border-black/6 bg-white shadow-sm"><CardHeader className="flex-row items-start justify-between gap-4"><div><CardTitle className="text-[17px]">{t('valueAndInvestedCapital')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('valueAndInvestedCapitalSubtitle')}</p></div><Button type="button" variant="outline" disabled={busy} onClick={async () => { setBusy(true); setError(''); try { const outcome = await onRefresh(); if (outcome.errors.length) setError(t('quotesRefreshedWithErrors', { ok: outcome.updated, ko: outcome.errors.length })); else if (outcome.updated === 0) setError(t('noTickersConfigured')); } catch { setError(t('cannotReachQuoteSource')); } finally { setBusy(false); } }}><RefreshCw className={`size-4 ${busy ? 'animate-spin' : ''}`} />{t('refreshQuotes')}</Button></CardHeader><CardContent>
        <div className="mb-3 flex w-fit gap-1 rounded-lg border border-black/6 bg-[#f4f5f1] p-1 text-xs">
          {([['amount', t('inEuro')], ['return', t('inPercent')]] as const).map(([value, label]) => (
            <button key={value} type="button" onClick={() => setHistoryMode(value)}
              className={`rounded-md px-2.5 py-1 font-medium transition ${historyMode === value ? 'bg-white text-[#173b33] shadow-sm' : 'text-[#71807c] hover:text-[#173b33]'}`}>
              {label}
            </button>
          ))}
        </div>
        {historyMode === 'amount' ? (
          <ChartContainer config={historyConfig} className="h-[310px] w-full"><LineChart accessibilityLayer data={dashboard.history}><CartesianGrid vertical={false} strokeDasharray="3 5" /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={26} /><YAxis tickLine={false} axisLine={false} width={78} tickFormatter={(value) => formatCompactEuro(Number(value))} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} />} /><ChartLegend content={<ChartLegendContent />} /><Line type="monotone" dataKey="marketValue" stroke="var(--color-marketValue)" strokeWidth={2.5} dot={false} /><Line type="monotone" dataKey="investedCapital" stroke="var(--color-investedCapital)" strokeWidth={2.25} dot={false} /></LineChart></ChartContainer>
        ) : indice ? (
          /* Con un indice configurato: le due curve cumulate, entrambe da 100
             nel primo mese in comune, e in legenda chi e' chi. I numeri sull'asse
             sono punti, non percentuali: 105 vuol dire cinque in piu' del punto
             di partenza, non il 105%. */
          <ChartContainer config={confrontoConfig} className="h-[310px] w-full"><LineChart accessibilityLayer data={puntiConfronto}><CartesianGrid vertical={false} strokeDasharray="3 5" /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={26} /><YAxis tickLine={false} axisLine={false} width={56} tickFormatter={(value) => Number(value).toFixed(0)} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => Number(value).toFixed(2)} />} /><ChartLegend content={<ChartLegendContent />} /><Line type="monotone" dataKey="twrCurve" stroke="var(--color-twrCurve)" strokeWidth={2.5} dot={false} /><Line type="monotone" dataKey="benchmarkCurve" stroke="var(--color-benchmarkCurve)" strokeWidth={2.25} dot={false} /></LineChart></ChartContainer>
        ) : (
          <ChartContainer config={returnConfig} className="h-[310px] w-full"><LineChart accessibilityLayer data={dashboard.history}><CartesianGrid vertical={false} strokeDasharray="3 5" /><XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={26} /><YAxis tickLine={false} axisLine={false} width={56} tickFormatter={(value) => `${Number(value).toFixed(0)}%`} /><ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => `${Number(value).toFixed(2)}%`} />} /><Line type="monotone" dataKey="returnRate" stroke="var(--color-returnRate)" strokeWidth={2.5} dot={false} /></LineChart></ChartContainer>
        )}
        {/* Il confronto parte dove le due storie si sovrappongono, e lo dice:
            altrimenti sembra che il grafico abbia perso dei mesi. */}
        {historyMode === 'return' && indice && dashboard.benchmark.from && <p className="mt-3 text-xs text-[#7b8784]">{t('benchmarkSince', { symbol: indice, from: formatDate(`${dashboard.benchmark.from}T12:00:00`, { month: 'long', year: 'numeric' }) })}</p>}
        {/* Configurato ma senza niente da confrontare non e' la stessa cosa di
            non configurato, e tacerlo lascerebbe credere che il campo non
            serva a niente. */}
        {historyMode === 'return' && dashboard.benchmark.symbol && !indice && <p className="mt-3 text-xs text-[#7b8784]">{t('benchmarkNoComparison', { symbol: dashboard.benchmark.symbol })}</p>}
        {error && <p className="mt-3 rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{error}</p>}
      </CardContent>
      </Card>
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="pb-2"><CardTitle className="text-[17px]">{t('monthlyContributions')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('monthlyContributionsSubtitle')}</p></CardHeader>
        <CardContent>
          <ChartContainer config={{ amount: { label: t('monthlyContributions'), color: '#6d8ff4' } }} className="h-[220px] w-full">
            <BarChart accessibilityLayer data={dashboard.contributions}>
              <CartesianGrid vertical={false} strokeDasharray="3 5" />
              <XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={26} />
              <YAxis tickLine={false} axisLine={false} width={70} tickFormatter={(value) => formatCompactEuro(Number(value))} />
              <ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value) => formatEuro(Number(value))} />} />
              <Bar dataKey="amount" radius={[3, 3, 1, 1]} maxBarSize={14}>
                {dashboard.contributions.map((point) => <Cell key={point.period} fill={point.amount >= 0 ? '#6d8ff4' : '#bd5e46'} />)}
              </Bar>
            </BarChart>
          </ChartContainer>
        </CardContent>
      </Card>
      <Card className="border-black/6 bg-white shadow-sm"><CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0"><div><CardTitle className="text-[17px]">{t('positions')}</CardTitle><p className="text-xs text-[#7b8784]">{showClosedPositions ? t('positionsSubtitleAll') : t('positionsSubtitle')}</p></div><label className="flex items-center gap-2 text-xs font-medium text-[#52615d]"><input type="checkbox" checked={showClosedPositions} onChange={(event) => setShowClosedPositions(event.target.checked)} className="size-4 accent-[var(--money-primary)]" />{t('showClosedPositions', { count: dashboard.positions.filter((p) => !viva(p)).length })}</label></CardHeader><CardContent className="p-0"><div className="overflow-x-auto"><table className="min-w-[1060px] w-full text-sm"><thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr><th className="px-5 py-3 text-left">{t('instrument')}</th><th className="px-3 py-3 text-right">{t('quantity')}</th><th className="px-3 py-3 text-right">{t('cost')}</th><th className="px-3 py-3 text-right">{t('value')}</th><th className="px-3 py-3 text-right">{t('profitLoss')}</th><th className="px-3 py-3 text-right">{t('ledgerIncome')}</th><th className="px-3 py-3 text-right">{t('ledgerFees')}</th><th className="px-3 py-3 text-right">{t('source')}</th><th className="px-5 py-3 text-right">{t('settings')}</th></tr></thead><tbody className="divide-y divide-black/5">{dashboard.positions.filter((position) => showClosedPositions || viva(position)).map((position) => <tr key={position.name} className={viva(position) ? undefined : 'bg-[#fafaf8] text-[#71807c]'}><td className="px-5 py-3"><p className="font-medium">{position.name}{!viva(position) && <span className="ml-2 rounded-full bg-black/6 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#71807c]">{t('closed')}</span>}</p><p className="mt-0.5 text-xs text-[#87918e]">{position.assetClass} · {position.area}</p></td><td className="px-3 py-3 text-right tabular-nums">{position.units.toLocaleString(locale, { maximumFractionDigits: 4 })}</td><td className="px-3 py-3 text-right tabular-nums">{formatEuro(position.costBasis)}</td><td className="px-3 py-3 text-right font-semibold tabular-nums">{formatEuro(position.marketValue)}</td><td className={`px-3 py-3 text-right font-semibold tabular-nums ${position.totalGain >= 0 ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>{formatEuro(position.totalGain)}{position.returnRate !== null && <span className="ml-1 text-xs">({(position.returnRate * 100).toLocaleString(locale, { maximumFractionDigits: 1 })}%)</span>}</td><td className="px-3 py-3 text-right tabular-nums text-[#2d7b65]">{position.incomeReceived !== 0 ? formatEuro(position.incomeReceived) : '—'}</td><td className="px-3 py-3 text-right tabular-nums text-[#bd5e46]">{position.feesPaid !== 0 ? formatEuro(position.feesPaid) : '—'}</td><td className="px-3 py-3 text-right text-xs">{position.isOpen ? (position.hasQuote ? t('cachedQuote') : t('lastLedgerPrice')) : t('realizedResult')}</td><td className="px-5 py-3 text-right">{position.instrumentId ? <Button size="sm" variant="ghost" onClick={() => { setConfiguring(position); setError(''); }}><Pencil className="size-4" />{t('configure')}</Button> : '—'}</td></tr>)}</tbody></table></div>{/* I proventi si leggono in fondo, tutti insieme: sono la parte che il guadagno non mostra, perche' un dividendo non e' una plusvalenza. */}<div className="flex items-center justify-between border-t border-black/6 px-5 py-3"><span className="text-xs text-[#52615d]">{t('ledgerIncomeTotal')}</span><span className="font-semibold tabular-nums">{formatEuro(dashboard.positions.reduce((somma, position) => somma + position.incomeReceived, 0))}</span></div></CardContent></Card>
      <RebalanceCard riordino={dashboard.rebalance} posizioni={dashboard.positions} onInstrumentSave={onInstrumentSave} />
    </div>}

    {tab === 'ledger' && (
      <div className="space-y-5">
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => void chooseLedgerCsv()}><FileSpreadsheet className="size-4" />{t('importFromCsv')}</Button>
          <Button className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]" onClick={() => { setEditing(null); setError(''); }}>
            <Plus className="size-4" />{t('newOperation')}
          </Button>
        </div>
        <Card className="border-black/6 bg-white shadow-sm">
          <CardHeader className="gap-4">
            <div>
              <CardTitle className="text-[17px]">{t('portfolioOperations')}</CardTitle>
              <p className="mt-1 text-xs text-[#7b8784]">{t('resultsOfTotal', { count: filteredLedger.length, total: ledger.length })}</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-[minmax(190px,1fr)_150px_180px_130px_150px]">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-black/35" />
                <Input aria-label={t('searchInLedger')} value={ledgerSearchQuery} onChange={(event) => setLedgerSearchQuery(event.target.value)} className="h-10 bg-[#fafaf8] pl-9" placeholder={t('searchInLedgerPlaceholder')} />
                {ledgerSearchQuery && <button aria-label={t('clearSearch')} onClick={() => setLedgerSearchQuery('')} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-black/40 hover:text-black"><X className="size-4" /></button>}
              </div>
              <FilterSelect label={t('filterByType')} value={ledgerTypeFilter} onChange={setLedgerTypeFilter} options={[['all', t('allTypes')], ...(Object.keys(LEDGER_TYPE_LABEL) as LedgerOperationType[]).map((tipo) => [tipo, t(LEDGER_TYPE_LABEL[tipo])] as [string, string])]} />
              <FilterSelect label={t('filterByInstrument')} value={ledgerInstrumentFilter} onChange={setLedgerInstrumentFilter} options={[['all', t('allInstruments')], ...ledgerInstruments.map((name) => [name, name] as [string, string])]} />
              <FilterSelect label={t('year')} value={ledgerYearFilter} onChange={setLedgerYearFilter} options={[['all', t('allYears')], ...ledgerYears.map((year) => [year, year] as [string, string])]} />
              <FilterSelect label={t('filterByPeriod')} value={ledgerMonthFilter} onChange={setLedgerMonthFilter} options={[['all', t('allMonths')], ...monthNames.map((name, index) => [String(index + 1).padStart(2, '0'), name] as [string, string])]} />
            </div>
          </CardHeader>
          <CardContent className="p-0">
            {filteredLedger.length ? (
              <>
                <div className="overflow-x-auto">
                  <table className="min-w-[980px] w-full text-sm">
                    <thead className="bg-[#f4f5f1] text-xs text-[#52615d]">
                      <tr>
                        <th className="w-10 px-3 py-3"><input type="checkbox" aria-label={t('selectFiltered')} checked={visibleLedger.length > 0 && visibleLedger.every((row) => ledgerSelection.has(row.id))} onChange={(event) => setLedgerSelection((current) => { const next = new Set(current); visibleLedger.forEach((row) => event.target.checked ? next.add(row.id) : next.delete(row.id)); return next; })} className="size-4 accent-[var(--money-primary)]" /></th>
                        <th className="px-5 py-3 text-left">{t('date')}</th>
                        <th className="px-3 py-3 text-left">{t('instrument')}</th>
                        <th className="px-3 py-3 text-left">{t('type')}</th>
                        <th className="px-3 py-3 text-right">{t('amount')}</th>
                        <th className="px-3 py-3 text-right">{t('quantity')}</th>
                        <th className="px-3 py-3 text-right">{t('ledgerPosition')}</th>
                        <th className="px-3 py-3 text-right">{t('price')}</th>
                        <th className="px-3 py-3 text-right">{t('fee')}</th>
                        <th className="px-5 py-3" />
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-black/5">
                      {visibleLedger.map((row) => (
                        <tr key={row.id}>
                          <td className="px-3 py-3 text-center"><input type="checkbox" aria-label={`${t('selectMovement', { description: row.name })}`} checked={ledgerSelection.has(row.id)} onChange={(event) => setLedgerSelection((current) => { const next = new Set(current); event.target.checked ? next.add(row.id) : next.delete(row.id); return next; })} className="size-4 accent-[var(--money-primary)]" /></td>
                          <td className="px-5 py-3 text-xs">{formatDate(`${row.occurredOn}T12:00:00`)}</td>
                          <td className="px-3 py-3 font-medium"><span className="flex items-center gap-1.5"><span title={row.linked ? t('ledgerLinkedHint') : t('ledgerUnlinkedHint')} aria-label={row.linked ? t('ledgerLinked') : t('ledgerUnlinked')} className="flex shrink-0">{row.linked
                            ? <Link2 className="size-3.5 text-[#2d7b65]" />
                            : <Unlink className="size-3.5 text-[#c3ccc8]" />}</span>{row.name}</span>{row.notes && <p className="mt-0.5 text-xs font-normal text-[#87918e]">{row.notes}</p>}</td>
                          <td className={`px-3 py-3 text-xs font-semibold ${row.transactionType === 'Buy' ? 'text-[#2d7b65]' : row.transactionType === 'Sell' ? 'text-[#bd5e46]' : 'text-[#52615d]'}`}>{t(LEDGER_TYPE_LABEL[row.transactionType])}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{formatEuro(row.amount)}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{row.units.toLocaleString(locale, { maximumFractionDigits: 5 })}</td>
                          <td className="px-3 py-3 text-right tabular-nums font-semibold">{row.runningUnits.toLocaleString(locale, { maximumFractionDigits: 5 })}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{row.price.toLocaleString(locale, { maximumFractionDigits: 4 })} {row.currency}</td>
                          <td className="px-3 py-3 text-right tabular-nums">{formatEuro(row.fee)}</td>
                          <td className="px-5 py-3">
                            <div className="flex justify-end">
                              <Button size="icon" variant="ghost" aria-label={`${t('edit')} ${row.name}`} onClick={() => { setEditing(row); setError(''); }}><Pencil className="size-4" /></Button>
                              <Button size="icon" variant="ghost" aria-label={`${t('delete')} ${row.name}`} className="text-[#bd5e46]" onClick={() => void onDelete(row)}><Trash2 className="size-4" /></Button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {visibleLedger.length < filteredLedger.length && (
                  <div className="border-t border-black/5 py-4 text-center">
                    <Button type="button" variant="outline" onClick={() => setLedgerVisibleLimit((current) => current + 100)}>{t('showMore100')}</Button>
                  </div>
                )}
              </>
            ) : (
              <p className="py-12 text-center text-sm text-[#71807c]">{t('noOperationsMatchFilters')}</p>
            )}
          </CardContent>
        </Card>
        {ledgerBulkError && <p role="alert" className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{ledgerBulkError}</p>}
        {ledgerSelection.size > 0 && <div className="sticky bottom-4 z-20 flex flex-wrap items-center gap-3 rounded-xl border bg-white p-4 shadow-lg">
          <span className="text-sm font-medium">{t('selectedCount', { count: ledgerSelection.size })}</span>
          <select aria-label={t('bulkField')} value={ledgerBulkField} onChange={(event) => { const value = event.target.value as typeof ledgerBulkField; setLedgerBulkField(value); setLedgerBulkValue(''); setLedgerBulkError(''); if (value === 'link') void loadLedgerLinkTransactions().catch(() => setLedgerBulkError(t('bulkFailed'))); }} className="h-10 rounded-lg border border-input bg-white px-2.5 text-sm">
            <option value="name">{t('fieldName')}</option><option value="transaction_type">{t('type')}</option><option value="currency">{t('currency')}</option><option value="link">{t('linkToTransaction')}</option>
          </select>
          {ledgerBulkField === 'transaction_type' ? <select aria-label={t('bulkValue')} value={ledgerBulkValue} onChange={(event) => setLedgerBulkValue(event.target.value)} className="h-10 rounded-lg border border-input bg-white px-2.5 text-sm"><option value="">{t('bulkValue')}</option><option value="Buy">{t('buy')}</option><option value="Sell">{t('sell')}</option></select>
            : ledgerBulkField === 'link' ? <><Input aria-label={t('searchInMovements')} value={ledgerLinkQuery} onChange={(event) => setLedgerLinkQuery(event.target.value)} placeholder={t('searchInMovementsPlaceholder')} className="w-52" /><select aria-label={t('linkToTransaction')} value={ledgerBulkValue} onChange={(event) => setLedgerBulkValue(event.target.value)} className="h-10 max-w-[360px] rounded-lg border border-input bg-white px-2.5 text-sm"><option value="">{t('bulkValue')}</option>{ledgerLinkCandidates.map((transaction) => <option key={transaction.id} value={transaction.id}>{formatDate(`${transaction.occurredOn}T12:00:00`)} · {transaction.description} · {formatEuro(Math.abs(transaction.amount))}</option>)}</select></>
            : <Input aria-label={ledgerBulkField === 'name' ? t('fieldName') : t('currency')} value={ledgerBulkValue} onChange={(event) => setLedgerBulkValue(event.target.value)} className="w-48" />}
          <Button disabled={ledgerBulkBusy || !ledgerBulkValue} onClick={() => void applyLedgerBulk()}>{t('applySelected')}</Button>
          <Button variant="outline" onClick={() => { setLedgerSelection(new Set()); setLedgerBulkError(''); }}>{t('cancel')}</Button>
        </div>}
      </div>
    )}

    {tab === 'instruments' && <InstrumentAnalysisView apiUrl={apiUrl} positions={dashboard.positions} />}

    {tab === 'allocation' && <div className="space-y-5">
      <Card className={`border shadow-sm ${allocation.coverage.coveredPercent >= 99 ? 'border-[#b9ddce] bg-[#f0f8f4]' : 'border-[#efc4b8] bg-[#fff6f3]'}`}>
        <CardContent className="flex flex-col gap-3 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              {allocation.coverage.coveredPercent >= 99 ? <CheckCircle2 className="mt-0.5 size-5 text-[#2d7b65]" /> : <AlertCircle className="mt-0.5 size-5 text-[#bd5e46]" />}
              <div>
                <p className="text-sm font-semibold">{t('allocationCoverage', { percent: allocation.coverage.coveredPercent.toLocaleString(locale) })}</p>
                <p className="mt-1 text-xs leading-5 text-[#52615d]">{allocation.coverage.lastFetch ? t('allocationLastFetch', { date: new Date(allocation.coverage.lastFetch).toLocaleString(locale) }) : t('allocationNeverFetched')}</p>
              </div>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Button type="button" variant="outline" disabled={refreshingProfiles} onClick={() => void refreshProfiles()}>
                <RefreshCw className={`size-4 ${refreshingProfiles ? 'animate-spin' : ''}`} />{t('refreshComposition')}
              </Button>
              {profileMessage && <span className="max-w-[320px] text-right text-xs text-[#a05f4e]">{profileMessage}</span>}
            </div>
          </div>
          {allocation.coverage.missing.length > 0 && <ul className="space-y-1 text-xs">
            {allocation.coverage.missing.slice(0, 5).map((item) => <li key={item.instrument} className="flex justify-between gap-3 rounded-lg bg-white/70 px-3 py-1.5">
              <span className="font-medium">{item.instrument}</span>
              <span className="text-[#71807c]">{formatEuro(item.value)} · {item.reason === 'no_ticker' ? t('allocationNoTicker') : sourceErrorLabel(t, item.code)}</span>
            </li>)}
          </ul>}
        </CardContent>
      </Card>
      <div className="flex flex-wrap gap-2">{([['instrument', t('dimInstrument')], ['sector', t('dimSector')], ['assetType', t('dimAssetType')], ['holdings', t('dimHoldings')], ['currency', t('dimCurrency')]] as const).map(([value, label]) => <Button key={value} type="button" variant={allocationDimension === value ? 'default' : 'outline'} className={allocationDimension === value ? 'bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]' : 'bg-white'} onClick={() => setAllocationDimension(value)}>{label}</Button>)}</div>
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader><CardTitle className="text-[17px]">{t('allocationForDimension', { dimension: ({ instrument: t('dimInstrument'), sector: t('dimSector'), assetType: t('dimAssetType'), holdings: t('dimHoldings'), currency: t('dimCurrency') })[allocationDimension] })}</CardTitle><p className="text-xs text-[#7b8784]">{t('estimatedValueFromLedger', { amount: formatEuro(allocation.total) })}</p></CardHeader>
        <CardContent className="space-y-4">
          {allocation.allocations[allocationDimension].length === 0
            ? <p className="py-6 text-center text-sm text-[#87918e]">{t('allocationNoData')}</p>
            : allocation.allocations[allocationDimension].map((item) => <div key={item.label}>
                <div className="mb-2 flex justify-between gap-4 text-sm"><span className="font-medium">{item.label === '__unavailable__' ? t('allocationUnavailable') : item.label}</span><span className="tabular-nums">{item.weight.toLocaleString(locale)}%</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-[#edf0ed]"><div className={`h-full rounded-full ${item.label === '__unavailable__' ? 'bg-[#c9a99b]' : 'bg-[#6d8ff4]'}`} style={{ width: `${Math.min(item.weight, 100)}%` }} /></div>
                <div className="mt-1.5 text-xs text-[#7b8784]">{formatEuro(item.value)}</div>
              </div>)}
        </CardContent>
      </Card>
    </div>}

    {tab === 'quotes' && <InstrumentQuotesView apiUrl={apiUrl} rows={instruments} reload={loadInstruments} onSaved={onQuotesChanged} onRefresh={onRefresh} />}

    <Dialog open={ledgerCsvOpen} onOpenChange={(open) => { setLedgerCsvOpen(open); if (!open) { setLedgerCsvFile(null); setLedgerCsvPreview([]); setLedgerCsvError(''); } }}>
      <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-[95vw]">
        <DialogHeader><DialogTitle>{t('importFromCsv')} · {t('portfolioOperations')}</DialogTitle><DialogDescription>{t('statementPreviewHint', { count: ledgerCsvCount })}</DialogDescription></DialogHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-auto pr-1">
          {!ledgerCsvPreview.length ? <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {([['date', t('date')], ['name', t('instrument')], ['type', t('type')], ['amount', t('amount')], ['units', t('units')], ['price', t('price')], ['currency', t('currency')]] as const).map(([field, label]) => <label key={field} className="space-y-1 text-xs font-medium text-[#52615d]">{label}<select value={ledgerCsvMapping[field] ?? ''} onChange={(event) => setLedgerCsvMapping((current) => ({ ...current, [field]: event.target.value }))} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm"><option value="">{t('bulkValue')}</option>{ledgerCsvHeaders.map((header) => <option key={header} value={header}>{header}</option>)}</select></label>)}
            </div>
            {ledgerCsvSample.length > 0 && <div className="overflow-x-auto rounded-lg border border-black/8"><table className="min-w-full text-xs"><thead className="bg-[#f4f5f1]"><tr>{ledgerCsvHeaders.map((header) => <th key={header} className="whitespace-nowrap px-3 py-2 text-left">{header}</th>)}</tr></thead><tbody>{ledgerCsvSample.map((row, index) => <tr key={index} className="border-t border-black/5">{ledgerCsvHeaders.map((header) => <td key={header} className="whitespace-nowrap px-3 py-2">{row[header]}</td>)}</tr>)}</tbody></table></div>}
          </> : <div className="overflow-x-auto rounded-lg border border-black/8"><table className="min-w-[900px] w-full text-sm"><thead className="bg-[#f4f5f1] text-xs text-[#52615d]"><tr><th className="px-3 py-3">{t('statementSelect')}</th><th className="px-3 py-3 text-left">{t('date')}</th><th className="px-3 py-3 text-left">{t('instrument')}</th><th className="px-3 py-3 text-left">{t('type')}</th><th className="px-3 py-3 text-right">{t('amount')}</th><th className="px-3 py-3 text-right">{t('units')}</th><th className="px-3 py-3 text-right">{t('price')}</th><th className="px-3 py-3 text-left">{t('currency')}</th><th className="px-3 py-3 text-left" /></tr></thead><tbody className="divide-y divide-black/5">{ledgerCsvPreview.map((item) => <tr key={item.row} className={item.duplicate || item.error ? 'bg-[#fafaf8] text-[#87918e]' : ''}><td className="px-3 py-3 text-center"><input type="checkbox" disabled={item.duplicate || Boolean(item.error)} checked={ledgerCsvSelection.has(item.row)} aria-label={t('statementSelectRow', { row: item.row })} onChange={(event) => setLedgerCsvSelection((current) => { const next = new Set(current); event.target.checked ? next.add(item.row) : next.delete(item.row); return next; })} className="size-4 accent-[var(--money-primary)]" /></td><td className="px-3 py-3">{item.occurred_on ?? '—'}</td><td className="px-3 py-3 font-medium">{item.name ?? '—'}</td><td className="px-3 py-3">{item.transaction_type ?? '—'}</td><td className="px-3 py-3 text-right tabular-nums">{item.amount == null ? '—' : formatEuro(item.amount)}</td><td className="px-3 py-3 text-right tabular-nums">{item.units ?? '—'}</td><td className="px-3 py-3 text-right tabular-nums">{item.price ?? '—'}</td><td className="px-3 py-3">{item.currency ?? '—'}</td><td className="px-3 py-3 text-xs text-[#a05f4e]">{item.duplicate ? t('statementDuplicate') : item.error ? t('statementRequiredFields') : ''}</td></tr>)}</tbody></table></div>}
          {ledgerCsvError && <p role="alert" className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{ledgerCsvError}</p>}
        </div>
        <DialogFooter><Button type="button" variant="outline" onClick={() => setLedgerCsvOpen(false)}>{t('cancel')}</Button>{ledgerCsvPreview.length ? <Button disabled={ledgerCsvBusy || !ledgerCsvSelection.size} onClick={() => void importLedgerCsv()}>{t('statementConfirm', { count: ledgerCsvSelection.size })}</Button> : <Button disabled={ledgerCsvBusy || Object.values(ledgerCsvMapping).length !== 7 || Object.values(ledgerCsvMapping).some((value) => !value) || new Set(Object.values(ledgerCsvMapping)).size !== 7} onClick={() => void previewLedgerCsv()}>{t('statementPreview')}</Button>}</DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog open={editing !== undefined} onOpenChange={(open) => { if (!open) setEditing(undefined); }}><DialogContent className="max-w-lg"><DialogHeader><DialogTitle>{editing ? t('editOperation') : t('newInvestmentOperation')}</DialogTitle><DialogDescription>{t('investmentDialogDesc')}</DialogDescription></DialogHeader><form key={editing?.id ?? 'new-investment'} onSubmit={saveLedger} className="space-y-4"><div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('date')}<Input required name="occurred_on" type="date" defaultValue={editing?.occurredOn ?? new Date().toISOString().slice(0, 10)} /></label><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('operation')}<select required name="transaction_type" value={ledgerTypeInput} onChange={(event) => cambiaTipoLedger(event.target.value as LedgerOperationType)} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm">{(Object.keys(LEDGER_TYPE_LABEL) as LedgerOperationType[]).map((tipo) => <option key={tipo} value={tipo}>{t(LEDGER_TYPE_LABEL[tipo])}</option>)}</select></label></div><label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('instrument')}<div className="relative">
      <Input required name="name" autoComplete="off" value={instrumentQuery}
        onChange={(event) => { setInstrumentQuery(event.target.value); setSuggestionsOpen(true); }}
        onFocus={() => setSuggestionsOpen(true)}
        onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 150)}
        placeholder={t('instrumentPlaceholder')} />
      {suggestionsOpen && instrumentSuggestions.length > 0 && <div className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-black/10 bg-white shadow-lg">
        {instrumentSuggestions.map((item) => <button key={item.id} type="button"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => { setInstrumentQuery(item.name); setSuggestionsOpen(false); }}
          className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-xs font-normal hover:bg-[#f4f5f1]">
          <span>{item.name}</span><span className="shrink-0 text-[#87918e]">{item.providerSymbol || '—'}</span>
        </button>)}
      </div>}
      {instruments.length === 0 && <p className="mt-1 font-normal text-[#a05f4e]">{t('noInstrumentsHint')}</p>}
      {instruments.length > 0 && suggestionsOpen && instrumentSuggestions.length === 0 && <p className="mt-1 font-normal text-[#87918e]">{t('noInstrumentMatch')}</p>}
    </div></label><div className="grid grid-cols-3 gap-3">{tipoSplit ? <input type="hidden" name="amount" value="0" /> : <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('amount')}<Input required name="amount" min="0.01" step="0.01" type="number" value={amountInput} onChange={(event) => setAmountInput(event.target.value)} /></label>}{tipoContante ? null : <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('units')}<Input required name="units" min="0.00000001" step="0.00000001" type="number" value={unitsInput} onChange={(event) => setUnitsInput(event.target.value)} /></label>}{tipoSplit ? <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('ledgerSplitRatio')}<Input required name="units" min="0.00000001" step="0.00000001" type="number" value={unitsInput} onChange={(event) => setUnitsInput(event.target.value)} /></label> : null}{tipoContante ? null : <label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('priceComputed')}<Input readOnly required name="price" type="number" value={derivedPrice} tabIndex={-1} className="bg-[#f4f5f1] text-[#52615d]" /></label>}</div>{tipoSplit && <p className="text-[11px] leading-4 text-[#87918e]">{t('ledgerSplitHint')}</p>}<div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('currency')}<select name="currency" defaultValue={editing?.currency ?? 'EUR'} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm"><option>EUR</option><option>USD</option></select></label><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('fee')}<Input name="fee" min="0" step="0.01" type="number" defaultValue={editing?.fee ?? 0} /></label></div><label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('note')}<Input name="notes" defaultValue={editing?.notes ?? ''} placeholder={t('optional')} /></label>{!editing && !tipoSplit && <><div className="space-y-2 rounded-lg border border-[#5c8f82]/20 bg-[#f6f9f7] p-3"><label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-[#3b6a5b]"><input type="checkbox" checked={linkTransactionOpen} onChange={(event) => setLinkTransactionOpen(event.target.checked)} className="size-4 accent-[var(--money-primary)]" />{t('linkToTransaction')}</label>{linkTransactionOpen && <div className="grid grid-cols-2 gap-2 pt-1"><label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fieldDate')}<Input name="tx_occurred_on" type="date" defaultValue={new Date().toISOString().slice(0, 10)} className="h-8 bg-white text-xs" /></label><label className="space-y-1 text-[10px] font-medium text-[#52615d]">{t('fieldAccount')}<select required name="tx_account_name" defaultValue="" className="h-8 w-full rounded-md border border-input bg-white px-2 text-xs"><option value="">{t('noAccount')}</option>{accounts.filter(a => a.isActive !== false).map((account) => <option key={account.id} value={account.name}>{account.name}</option>)}</select></label><label className="space-y-1 text-[10px] font-medium text-[#52615d] col-span-2">{t('fieldCategory')}<Input name="tx_category" defaultValue="Investimenti" className="h-8 bg-white text-xs" /></label><label className="space-y-1 text-[10px] font-medium text-[#52615d] col-span-2">{t('fieldDescription')}<Input name="tx_details" defaultValue="" placeholder={t('optionalNote')} className="h-8 bg-white text-xs" /></label></div>}</div><label className="flex items-center gap-2 text-xs text-[#71807c]"><input type="checkbox" name="force_duplicate" className="size-4 accent-[var(--money-primary)]" />{t('allowLedgerDuplicate')}</label></>}{error && <p className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{error}</p>}<DialogFooter><Button type="button" variant="outline" onClick={() => setEditing(undefined)}>{t('cancel')}</Button><Button type="submit" disabled={busy} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{busy ? t('savingEllipsis') : t('save')}</Button></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={Boolean(configuring)} onOpenChange={(open) => { if (!open) setConfiguring(null); }}><DialogContent className="max-w-md"><DialogHeader><DialogTitle>{t('configureQuoteAndClassification')}</DialogTitle><DialogDescription>{t('configureQuoteAndClassificationDesc')}</DialogDescription></DialogHeader>{configuring && <form key={configuring.instrumentId} onSubmit={saveInstrument} className="space-y-4"><label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('sourceSymbol')}<Input name="provider_symbol" defaultValue={configuring.providerSymbol ?? ''} placeholder="es. VUSA.AS" /></label><div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('assetClassLabel')}<Input name="asset_class" defaultValue={configuring.assetClass} /></label><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('area')}<Input name="area" defaultValue={configuring.area} /></label></div><div className="grid grid-cols-2 gap-3"><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('sector')}<Input name="sector" defaultValue={configuring.sector} /></label><label className="space-y-1.5 text-xs font-medium text-[#52615d]">{t('currency')}<Input name="currency" defaultValue={configuring.currency} /></label></div><label className="block space-y-1.5 text-xs font-medium text-[#52615d]">{t('allocTargetWeightField')}<Input name="target_weight" type="number" step="0.1" min={0} max={100} defaultValue={configuring.targetWeight === null ? '' : String(Math.round(configuring.targetWeight * 1000) / 10)} placeholder="es. 17,5" /><span className="block text-[11px] font-normal text-[#87918e]">{t('allocTargetWeightHelp')}</span></label>{error && <p className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{error}</p>}<DialogFooter><Button type="button" variant="outline" onClick={() => setConfiguring(null)}>{t('cancel')}</Button><Button type="submit" disabled={busy} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{t('saveConfiguration')}</Button></DialogFooter></form>}</DialogContent></Dialog>
  </div>;
}

function NetWorthView({ apiUrl, data, primoAnno, accounts, alPresente, onNewAccount, onAccountEdit,
                       onAccountValuations, onAccountDelete }: {
  apiUrl: string; data: NetWorthData; primoAnno: number; accounts: Account[];
  // Vero quando il periodo scelto e' il mese corrente: solo li' modificare un
  // conto significa qualcosa.
  alPresente: boolean;
  onNewAccount: () => void;
  onAccountEdit: (account: Account) => void;
  onAccountValuations: (account: Account) => void;
  onAccountDelete: (account: Account) => Promise<void>;
}) {
  const { t, locale, formatEuro, formatDate } = useI18n();
  // Due schede sulla stessa pagina: "Patrimonio" risponde a quanto vali e come
  // ci sei arrivato, "Conti" a quali conti lo compongono e se tornano. Stesso
  // periodo, stessi numeri, due domande diverse.
  const [scheda, setScheda] = useState<'patrimonio' | 'conti'>('patrimonio');
  const [accountOrder, setAccountOrder] = useState<'balance' | 'name' | 'added'>('balance');
  const [accountOrderDirection, setAccountOrderDirection] = useState<'asc' | 'desc'>('desc');
  type BreakdownGroup = Account['group'];
  const [hideZeroBalances, setHideZeroBalances] = useState(true);
  const [expandedGroupOverride, setExpandedGroupOverride] = useState<Partial<Record<BreakdownGroup, boolean>>>({});

  // L'elenco viene dai conti veri, non dal breakdown della serie: sono lo stesso
  // numero (un test lo sorveglia) ma i conti portano anche nome, gruppo,
  // riconciliazione e i pulsanti. Il breakdown restava una copia poverissima
  // degli stessi dati, ed e' per questo che i due componenti card divergevano.
  const groupLabel: Record<Account['group'], string> = useMemo(() => ({ bank: t('groupBank'), asset: t('groupAsset'), liability: t('groupLiability'), financial: t('groupFinancial') }), [t]);
  const isZeroBalance = (account: Account) => Math.abs(account.value) < 0.005;
  const compareAccounts = (a: Account, b: Account) => {
    const esito = accountOrder === 'name' ? a.name.localeCompare(b.name, locale)
      : accountOrder === 'added' ? a.id - b.id : Math.abs(a.value) - Math.abs(b.value);
    return accountOrderDirection === 'asc' ? esito : -esito;
  };
  const perGruppo = (Object.keys(groupLabel) as Account['group'][]).map((group) => {
    const all = accounts.filter((account) => account.group === group);
    const items = (hideZeroBalances ? all.filter((account) => !isZeroBalance(account)) : all).sort(compareAccounts);
    return { group, items, totalCount: all.length,
             total: all.reduce((sum, account) => account.countsInNetWorth === false ? sum : sum + account.value, 0) };
  }).filter((voce) => voce.totalCount > 0);
  const colonnaAttivo = perGruppo.filter((voce) => voce.group !== 'liability');
  const colonnaPassivo = perGruppo.filter((voce) => voce.group === 'liability');
  const totali = useMemo(() => {
    const gruppo: Record<Account['group'], number> = { bank: 0, asset: 0, liability: 0, financial: 0 };
    let investimenti = 0;
    let versati = 0;
    for (const account of accounts) {
      if (account.countsInNetWorth === false) continue;
      gruppo[account.group] += account.value;
      if (account.valuedByLedger) {
        investimenti += account.value;
        versati += account.calculatedBalance;
      }
    }
    // `financial` non e' un gruppo che un conto possa avere - il modulo offre
    // solo banca, attivita' e passivita' - ma se un giorno lo diventasse
    // l'intestazione della colonna continuerebbe a quadrare con le sue card.
    const totaleAttivo = gruppo.bank + gruppo.asset + gruppo.financial;
    return { gruppo, investimenti, versati, totaleAttivo, passivita: gruppo.liability,
             restoAttivo: totaleAttivo - investimenti,
             capitaleProprio: totaleAttivo - gruppo.liability };
  }, [accounts]);
  const { investimenti, versati, totaleAttivo, passivita, restoAttivo, capitaleProprio } = totali;
  const dataAsOfLabel = data.dataPeriod ? formatDate(`${data.dataPeriod}T12:00:00`, { month: 'long', year: 'numeric' }) : t('noData');

  return <div className="space-y-5">
    <div className="flex flex-wrap gap-2 rounded-xl border border-black/6 bg-white p-1.5 shadow-sm">
      {([['patrimonio', t('tabNetWorth')], ['conti', t('tabAccounts')]] as const).map(([valore, etichetta]) => (
        <button key={valore} type="button" onClick={() => setScheda(valore)}
          className={`rounded-lg px-3.5 py-2 text-sm font-medium transition ${scheda === valore ? 'bg-[var(--money-deep)] text-white' : 'text-[#61706c] hover:bg-[#f0f2ee]'}`}>
          {etichetta}
        </button>
      ))}
    </div>

    {scheda === 'patrimonio' && <>
    <div className="grid gap-4 lg:grid-cols-2">
      <MetricCard featured title={t('netWorthNet')} value={data.totals.netWorth} change={data.dataPeriod ? t('dataAsOf', { date: dataAsOfLabel }) : t('noData')} icon={Landmark} tone="worth" />
      <MetricCard featured title={t('liquidityMetric')} titleHint={t('liquidityExplanation')} value={data.totals.liquid} change={t('liquidityAndCreditsLabel')} icon={WalletCards} tone="saving" />
    </div>
    {/* I quattro totali di gruppo stanno gia' in testa alle due colonne del
        bilancio: quattro card grandi occupavano mezzo schermo per ripeterli.
        Patrimonio netto e liquidita' restano grandi perche' sono le uniche due
        cifre che non compaiono altrove. */}
    <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-xl border border-black/7 bg-white px-4 py-3 text-xs shadow-sm shadow-black/[0.02]">
      {([[t('groupBank'), totali.gruppo.bank], [t('bsOtherAssets'), totali.gruppo.asset - investimenti],
         [t('bsInvestments'), investimenti], [t('balanceSheetLiabilitiesOnly'), passivita]] as const)
        .map(([etichetta, valore]) => (
          <span key={etichetta} className="flex items-baseline gap-1.5">
            <span className="text-[#87918e]">{etichetta}</span>
            <b className="tabular-nums text-[13px] text-[#1f2c28]">{formatEuro(valore)}</b>
          </span>
        ))}
    </div>

    {(data.currencies ?? []).length > 0 && (
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="pb-2">
          <CardTitle className="text-[17px]">{t('netWorthInCurrencies')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('netWorthInCurrenciesSubtitle')}</p>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {(data.currencies ?? []).map((entry) => (
            <div key={entry.code} className="rounded-xl border border-black/6 px-3 py-2.5">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-[#87918e]">{entry.code}</p>
              {entry.available && entry.total !== null ? (
                <>
                  <p className="mt-0.5 text-lg font-semibold tabular-nums text-[#173b33]">
                    {entry.total.toLocaleString(locale, { minimumFractionDigits: entry.inverted ? 4 : 2, maximumFractionDigits: entry.inverted ? 4 : 2 })}
                  </p>
                  {entry.change !== null && (
                    <p className={`text-xs tabular-nums ${entry.change >= 0 ? 'text-[#2d7b65]' : 'text-[#bd5e46]'}`}>
                      {entry.change >= 0 ? '+' : ''}{entry.change.toLocaleString(locale, { maximumFractionDigits: entry.inverted ? 4 : 2 })}
                      {entry.changePercent !== null && ` (${entry.changePercent >= 0 ? '+' : ''}${entry.changePercent.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%)`}
                    </p>
                  )}
                  {entry.rate !== null && (
                    <p className="mt-1 text-[11px] text-[#87918e]">
                      {entry.inverted
                        ? `1 ${entry.code} = ${entry.rate.toLocaleString(locale, { maximumFractionDigits: 2 })} EUR`
                        : `1 EUR = ${entry.rate.toLocaleString(locale, { maximumFractionDigits: 4 })} ${entry.code}`}
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-xs text-[#a3adaa]">{t('netWorthRateMissing')}</p>
              )}
            </div>
          ))}
        </CardContent>
      </Card>
    )}

    <BalanceSheetChart apiUrl={apiUrl} primoAnno={primoAnno} />
    </>}

    {scheda === 'conti' && <>
    {/* Da qui in giu' e' quella che era la pagina Conti: stessi conti, stesso
        numero, e adesso anche lo stesso posto. Le azioni valgono solo al
        periodo corrente - non esiste un saldo di giugno da correggere. */}
    {/* Una riga sola: stato a sinistra, controlli in mezzo, l'azione a destra.
        Lo stato era una Card grande con dentro la formula, affiancata a un
        pulsante: due elementi di peso diverso che si contendevano la riga. La
        formula e' una spiegazione, non un titolo, e sta nel tooltip. */}
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-xl border border-black/7 bg-white px-4 py-3 shadow-sm shadow-black/[0.02]">
      <label className="flex items-center gap-2 text-xs font-medium text-[#52615d]">
        <input type="checkbox" checked={hideZeroBalances} onChange={(event) => setHideZeroBalances(event.target.checked)} className="size-4 accent-[var(--money-primary)]" />
        {t('hideZeroBalanceAccounts')}
      </label>
      <label className="flex items-center gap-2 text-xs font-medium text-[#52615d]">{t('accountOrder')}
        <select value={accountOrder} onChange={(event) => setAccountOrder(event.target.value as 'balance' | 'name' | 'added')} className="h-8 rounded-lg border border-black/7 bg-white px-2 text-xs"><option value="balance">{t('accountOrderBalance')}</option><option value="name">{t('accountOrderName')}</option><option value="added">{t('accountOrderAdded')}</option></select>
        <label className="sr-only" htmlFor="account-order-direction">{t('accountOrderDirection')}</label>
        <select id="account-order-direction" value={accountOrderDirection} onChange={(event) => setAccountOrderDirection(event.target.value as 'asc' | 'desc')} className="h-8 rounded-lg border border-black/7 bg-white px-2 text-xs"><option value="asc">{t('ascending')}</option><option value="desc">{t('descending')}</option></select>
      </label>
      {alPresente && <Button onClick={onNewAccount} size="sm" className="ml-auto h-8 rounded-lg bg-[var(--money-primary)] px-3 text-xs text-white hover:bg-[var(--money-primary-hover)]"><Plus className="size-3.5" />{t('newAccount')}</Button>}
    </div>
    {!alPresente && <p className="rounded-xl border border-black/7 bg-[#fafaf8] px-4 py-2.5 text-xs text-[#71807c]">{t('pastPeriodReadOnly')}</p>}

    <div className="grid gap-5 xl:grid-cols-2">
      <div className="space-y-5">
        <div className="flex items-baseline justify-between gap-3 border-b border-black/8 pb-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-[#87918e]">{t('balanceSheetAssets')}</p>
          <p className="text-sm font-semibold tabular-nums">{formatEuro(totaleAttivo)}</p>
        </div>
        {colonnaAttivo.map(({ group, items, totalCount, total }) => <AccountGroupCard key={group} group={group} label={groupLabel[group]} items={items} totalCount={totalCount} total={total} netWorth={data.totals.netWorth} azioni={alPresente} expanded={expandedGroupOverride[group] ?? items.length <= 6} onToggleExpand={() => setExpandedGroupOverride((prev) => ({ ...prev, [group]: !(prev[group] ?? items.length <= 6) }))} onEdit={onAccountEdit} onValuations={onAccountValuations} onDelete={(account) => void onAccountDelete(account)} />)}
      </div>
      <div className="space-y-5">
        <div className="flex items-baseline justify-between gap-3 border-b border-black/8 pb-1.5">
          <p className="text-xs font-semibold uppercase tracking-wide text-[#87918e]">{t('balanceSheetLiabilitiesEquity')}</p>
          <p className="text-sm font-semibold tabular-nums">{formatEuro(passivita + capitaleProprio)}</p>
        </div>
        {colonnaPassivo.map(({ group, items, totalCount, total }) => <AccountGroupCard key={group} group={group} label={groupLabel[group]} items={items} totalCount={totalCount} total={total} netWorth={data.totals.netWorth} azioni={alPresente} expanded={expandedGroupOverride[group] ?? items.length <= 6} onToggleExpand={() => setExpandedGroupOverride((prev) => ({ ...prev, [group]: !(prev[group] ?? items.length <= 6) }))} onEdit={onAccountEdit} onValuations={onAccountValuations} onDelete={(account) => void onAccountDelete(account)} />)}
        <EquityCard versati={versati} rivalutazione={investimenti - versati} resto={restoAttivo} passivita={passivita} haPortafoglio={investimenti !== 0} />
      </div>
    </div>
    </>}
  </div>;
}

function BudgetEditor({ data, canEdit, editableYears, budgetType, suggestions, onUpdate, onCreate, onDelete, onCopy, onCategoryGroupChange }: {
  data: BudgetData;
  canEdit: boolean;
  editableYears: number[];
  budgetType: 'Expenses' | 'Income';
  suggestions: BudgetSuggestion[];
  onUpdate: (id: number, payload: { category?: string; amount?: number }) => Promise<void>;
  onCreate: (category: string, amount: number) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onCopy: (mode: 'month' | 'year') => Promise<void>;
  onCategoryGroupChange: (category: string, categoryGroup: string) => Promise<void>;
}) {
  const { t, formatEuro } = useI18n();
  // Il suggerimento e' quello della categoria scritta ora nella riga, non di
  // quella salvata: se stai rinominando, cambia con te.
  const suggestionFor = (category: string) => suggestions.find((item) => item.category.trim().toLowerCase() === category.trim().toLowerCase());
  const actualLabel = budgetType === 'Expenses' ? t('spentMetric') : t('receivedMetric');
  const [drafts, setDrafts] = useState<Record<number, { category: string; amount: string }>>({});
  const [newCategory, setNewCategory] = useState('');
  const [newAmount, setNewAmount] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function save(item: BudgetItem) {
    const draft = drafts[item.id];
    if (!draft || !draft.category.trim() || !isPositiveNumber(draft.amount)) {
      setError(t('budgetRowInvalid'));
      return;
    }
    setBusy(`save-${item.id}`);
    setError('');
    try {
      await onUpdate(item.id, budgetUpdatePayload(draft.category, draft.amount));
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy('');
    }
  }

  async function create(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newCategory.trim() || !isPositiveNumber(newAmount)) {
      setError(t('budgetRowInvalid'));
      return;
    }
    setBusy('create');
    setError('');
    try {
      await onCreate(newCategory.trim(), Number(newAmount));
      setNewCategory('');
      setNewAmount('');
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy('');
    }
  }

  async function copyBudget(mode: 'month' | 'year') {
    setBusy(`copy-${mode}`);
    setError('');
    try {
      await onCopy(mode);
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy('');
    }
  }

  async function changeGroup(category: string, categoryGroup: string) {
    setBusy(`group-${category}`);
    setError('');
    try {
      await onCategoryGroupChange(category, categoryGroup);
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy('');
    }
  }

  async function remove(item: BudgetItem) {
    setBusy(`delete-${item.id}`);
    setError('');
    try {
      await onDelete(item.id);
    } catch {
      setError(t('cannotSaveBudgetRow'));
    } finally {
      setBusy('');
    }
  }

  return <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
    <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between"><div><CardTitle className="text-[17px]">{t('monthlyBudgetEditable')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('monthlyBudgetEditableSubtitle')}</p></div><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={!canEdit || Boolean(busy)} onClick={() => void copyBudget('month')}><Copy className="size-4" />{t('priorMonthAction')}</Button><Button type="button" variant="outline" disabled={!canEdit || Boolean(busy)} onClick={() => void copyBudget('year')}><Copy className="size-4" />{t('priorYearAction')}</Button></div></CardHeader>
    <CardContent>
      {!canEdit && <p className="mb-4 rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a05f4e]">{t('budgetEditRestricted', { years: editableYears.join(', ') })}</p>}
      {error && <p className="mb-3 rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{error}</p>}
      <div className="hidden gap-2 pb-2 text-[11px] font-medium uppercase tracking-wide text-[#87918e] sm:grid sm:grid-cols-[minmax(170px,1fr)_130px_120px_130px_auto]">
        <span>{t('category')}</span>
        <span>{t('categoryGroup')}</span>
        <span>{t('budget')}</span>
        <span className="text-right">{actualLabel}</span>
        <span className="w-[72px]" />
      </div>
      <div className="divide-y divide-black/5">{data.items.map((item) => {
        const draft = drafts[item.id] ?? { category: item.category, amount: item.amount.toFixed(2) };
        const changed = draft.category !== item.category || Number(draft.amount) !== item.amount;
        const suggestion = suggestionFor(draft.category);
        // Mediana zero vuol dire che nella maggior parte dei mesi non hai speso
        // niente li' dentro: non e' un suggerimento, e' rumore.
        const usefulSuggestion = suggestion && suggestion.median > 0 ? suggestion : undefined;
        return <div key={item.id} className="grid gap-2 py-3 sm:items-start sm:grid-cols-[minmax(170px,1fr)_130px_120px_130px_auto]">
          <div className="min-w-0">
            <Input aria-label={`${t('category')} ${item.categoryLabel}`} disabled={!canEdit} value={draft.category} onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, category: event.target.value } }))} className="h-9 bg-[#fafaf8]" />
            {usefulSuggestion && <button type="button" disabled={!canEdit} title={t('budgetSuggestionTitle', { average: formatEuro(usefulSuggestion.average), max: formatEuro(usefulSuggestion.max) })} onClick={() => setDrafts((current) => ({ ...current, [item.id]: { ...draft, amount: usefulSuggestion.median.toFixed(2) } }))} className="mt-1 text-left text-[11px] leading-4 text-[#397867] hover:underline disabled:cursor-default disabled:text-[#9aa5a2] disabled:no-underline">
              {t('budgetSuggestion', { amount: formatEuro(usefulSuggestion.median), months: usefulSuggestion.monthsWithSpending, total: usefulSuggestion.monthsConsidered })}
            </button>}
          </div>
          <select aria-label={`${t('categoryGroup')} ${item.categoryLabel}`} disabled={!canEdit || Boolean(busy)} value={item.categoryGroup ?? ''} onChange={(event) => void changeGroup(item.category, event.target.value)} className="h-9 rounded-lg border border-input bg-[#fafaf8] px-2 text-xs outline-none focus:border-ring">
            <option value="">{t('groupUnset')}</option>
            <option value="Needs">{t('groupNeeds')}</option>
            <option value="Wants">{t('groupWants')}</option>
            <option value="Other">{t('groupOther')}</option>
          </select>
          <Input aria-label={`${t('budget')} ${item.categoryLabel}`} disabled={!canEdit} min="0" step="0.01" type="number" value={draft.amount} onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: { ...draft, amount: event.target.value } }))} className="h-9 bg-[#fafaf8]" />
          <div className="text-right text-xs leading-4 text-[#71807c] sm:pt-2">
            <span className="sm:hidden">{actualLabel}: </span>{formatEuro(item.actual)}
            {item.previousLeftover !== 0 && <span className="block text-[#87918e]">{item.previousLeftover > 0 ? t('budgetPreviousLeft', { amount: formatEuro(item.previousLeftover) }) : t('budgetPreviousOver', { amount: formatEuro(Math.abs(item.previousLeftover)) })}</span>}
          </div>
          <div className="flex justify-end gap-1">
            <Button type="button" size="icon" variant="ghost" aria-label={`${t('save')} ${item.categoryLabel}`} disabled={!canEdit || !changed || Boolean(busy)} onClick={() => void save(item)}><Save className="size-4" /></Button>
            <Button type="button" size="icon" variant="ghost" aria-label={`${t('delete')} ${item.categoryLabel}`} disabled={!canEdit || Boolean(busy)} onClick={() => void remove(item)} className="text-[#bd5e46]"><Trash2 className="size-4" /></Button>
          </div>
        </div>;
      })}</div>
      {canEdit && <form onSubmit={create} className="mt-4 grid gap-2 rounded-xl bg-[#f4f5f1] p-3 sm:grid-cols-[1fr_150px_auto]"><Input required value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder={t('newCategoryPlaceholder')} className="h-10 bg-white" /><Input required min="0" step="0.01" type="number" value={newAmount} onChange={(event) => setNewAmount(event.target.value)} placeholder={t('budgetPlaceholder')} className="h-10 bg-white" /><Button type="submit" disabled={Boolean(busy)} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><Plus className="size-4" />{t('add')}</Button></form>}
    </CardContent>
  </Card>;
}

function NotesView({ notes, onSave, onDelete }: { notes: NoteData[]; onSave: (noteId: number | null, payload: Record<string, string | null>) => Promise<void>; onDelete: (note: NoteData) => Promise<void> }) {
  const { t, lang } = useI18n();
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<NoteData | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [deleteError, setDeleteError] = useState('');
  const filtered = useMemo(() => {
    const needle = query.toLocaleLowerCase(lang);
    return notes.filter((note) => `${note.title} ${note.body} ${note.section}`.toLocaleLowerCase(lang).includes(needle));
  }, [notes, query, lang]);
  const sections = useMemo(() => Array.from(new Set(['Appunti', ...notes.map((note) => note.section)])).sort((a, b) => a.localeCompare(b, lang)), [notes, lang]);
  async function save(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setSaveError('');
    try {
      await onSave(editing?.id ?? null, notePayload(form));
      setEditing(null); setCreating(false);
    } catch {
      setSaveError(t('cannotSaveNote'));
    } finally { setBusy(false); }
  }
  async function remove(note: NoteData) {
    setDeleteError('');
    try { await onDelete(note); }
    catch { setDeleteError(t('cannotDeleteNote')); }
  }
  return <div className="space-y-5"><Card className="border-black/6 bg-white shadow-sm"><CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between"><div><CardTitle className="text-[17px]">{t('notesTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('notesSubtitle', { count: filtered.length, total: notes.length })}</p></div><Button onClick={() => { setEditing(null); setCreating(true); setSaveError(''); }} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><Plus className="size-4" />{t('newNote')}</Button></CardHeader><CardContent><div className="relative"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-black/35" /><Input aria-label={t('searchInTitlesAndText')} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('searchInTitlesAndText')} className="h-10 bg-[#fafaf8] pl-9" /></div></CardContent></Card>{deleteError && <p role="alert" className="rounded-lg bg-[#fce9e3] px-3 py-2 text-sm text-[#a94f3a]">{deleteError}</p>}<div className="grid gap-4 lg:grid-cols-2">{filtered.map((note) => <Card key={note.id} className="border-black/6 bg-white shadow-sm"><CardContent className="p-5"><div className="flex gap-3"><StickyNote className="mt-0.5 size-4 shrink-0 text-[#4d7f70]" /><div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-2"><div><p className="text-sm font-semibold">{note.title}</p><p className="mt-0.5 text-xs text-[#7b8784]">{note.section}{note.status ? ` · ${note.status}` : ''}</p></div><div className="flex"><Button size="icon" variant="ghost" aria-label={`${t('edit')} ${note.title}`} onClick={() => { setEditing(note); setCreating(false); setSaveError(''); }}><Pencil className="size-4" /></Button><Button size="icon" variant="ghost" aria-label={`${t('delete')} ${note.title}`} onClick={() => void remove(note)} className="text-[#bd5e46]"><Trash2 className="size-4" /></Button></div></div><p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-[#52615d]">{note.body}</p></div></div></CardContent></Card>)}</div>{!filtered.length && <Card><CardContent className="p-10 text-center text-sm text-[#71807c]">{t('noNotesFound')}</CardContent></Card>}<Dialog open={creating || editing !== null} onOpenChange={(open) => { if (!open) { setCreating(false); setEditing(null); setSaveError(''); } }}><DialogContent className="max-w-lg"><DialogHeader><DialogTitle>{editing ? t('editNote') : t('newNoteTitle')}</DialogTitle><DialogDescription>{t('noteDialogDesc')}</DialogDescription></DialogHeader><form key={editing?.id ?? 'new-note'} onSubmit={save} className="space-y-3"><label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('section')}<select name="section" defaultValue={editing?.section ?? 'Appunti'} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm">{sections.map((section) => <option key={section} value={section}>{section}</option>)}</select></label><label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('title')}<Input required name="title" defaultValue={editing?.title ?? ''} /></label><label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('text')}<textarea name="body" defaultValue={editing?.body ?? ''} className="min-h-28 w-full rounded-lg border border-input bg-white p-2.5 text-sm" /></label><label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('status')}<Input name="status" defaultValue={editing?.status ?? ''} placeholder={t('statusPlaceholder')} /></label>{saveError && <p role="alert" className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{saveError}</p>}<DialogFooter><Button type="button" variant="outline" onClick={() => { setCreating(false); setEditing(null); setSaveError(''); }}>{t('cancel')}</Button><Button type="submit" disabled={busy} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{busy ? t('savingEllipsis') : t('save')}</Button></DialogFooter></form></DialogContent></Dialog></div>;
}


export type BackupItem = { filename: string; size_bytes: number; created_at: string };

function BackupsCard({ apiUrl, onRestored }: { apiUrl: string; onRestored: () => Promise<void> }) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<BackupItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<{ filename: string; azione: 'restore' | 'delete' } | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);

  const load = useCallback(async () => {
    const response = await fetch(`${apiUrl}/api/backups`);
    if (!response.ok) return;
    const payload = await response.json() as { items?: BackupItem[] };
    setItems(payload.items ?? []);
  }, [apiUrl]);

  useEffect(() => { void load(); }, [load]);

  const when = (value: string) => new Date(value.endsWith('Z') ? value : `${value}Z`)
    .toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });

  async function run(action: () => Promise<Response>, done: (item?: BackupItem) => string) {
    setBusy(true);
    setOutcome(null);
    try {
      const response = await action();
      if (!response.ok) throw new Error(t('backupFailed'));
      setOutcome({ ok: true, message: done() });
      await load();
      return true;
    } catch (error) {
      setOutcome({ ok: false, message: error instanceof Error ? error.message : t('backupFailed') });
      return false;
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  return (
    <Card className="border-black/6 bg-white shadow-sm">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('backups')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('backupsHint')} {t('backupGlobalScope')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void run(
            () => fetch(`${apiUrl}/api/backups?label=manual`, { method: 'POST' }),
            () => t('backupCreated'),
          )}
        >
          <Database className={`size-4 ${busy ? 'animate-pulse' : ''}`} />{t('backupCreate')}
        </Button>
        {items.length === 0 && <p className="text-xs text-[#71807c]">{t('backupNone')}</p>}
        {items.length > 0 && (
          <ul className="divide-y divide-black/5 rounded-xl border border-black/6">
            {items.map((item) => (
              <li key={item.filename} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <span className="text-xs text-[#52615d]">
                  {when(item.created_at)} · {(item.size_bytes / 1024).toFixed(0)} KB
                </span>
                {confirming?.filename === item.filename ? (
                  <>
                  <Button
                    disabled={busy}
                    onClick={() => void (confirming.azione === 'delete'
                      ? run(
                          () => fetch(`${apiUrl}/api/backups/${encodeURIComponent(item.filename)}`, { method: 'DELETE' }),
                          () => t('backupDeleted', { date: when(item.created_at) }),
                        ).then(() => undefined)
                      : run(
                          () => fetch(`${apiUrl}/api/backups/${encodeURIComponent(item.filename)}/restore`, { method: 'POST' }),
                          () => t('backupRestored', { date: when(item.created_at) }),
                        ).then(ok => { if (ok) void onRestored(); }))}
                    className="bg-[#bd5e46] text-white hover:bg-[#a24f3b]"
                  >
                    {confirming.azione === 'delete' ? t('backupDeleteConfirm') : t('backupRestoreConfirm')}
                  </Button>
                  <Button variant="outline" disabled={busy} onClick={() => setConfirming(null)}>{t('cancel')}</Button>
                  <p className="w-full text-xs text-[#a65b49]">{confirming.azione === 'delete'
                    ? t('backupDeleteWarning', { date: when(item.created_at) })
                    : t('backupGlobalConfirm', { date: when(item.created_at) })}</p>
                  </>
                ) : (
                  <span className="flex items-center gap-3">
                    <button
                      onClick={() => { setConfirming({ filename: item.filename, azione: 'restore' }); setOutcome(null); }}
                      className="text-xs font-medium text-[#397867] hover:underline"
                    >
                      {t('backupRestore')}
                    </button>
                    {/* La conservazione automatica tocca solo `auto` e
                        `pre-import`: senza questo, ogni altro dump restava per
                        sempre e si poteva togliere solo dal container. */}
                    <button
                      aria-label={`${t('delete')} ${when(item.created_at)}`}
                      onClick={() => { setConfirming({ filename: item.filename, azione: 'delete' }); setOutcome(null); }}
                      className="text-xs font-medium text-[#bd5e46] hover:underline"
                    >
                      {t('delete')}
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {outcome && <p className={`text-xs ${outcome.ok ? 'text-[#2d7b65]' : 'text-[#a65b49]'}`}>{outcome.message}</p>}
      </CardContent>
    </Card>
  );
}

function ImportDataCard({ onImportData, importing }: { onImportData: (file: File) => Promise<string>; importing: boolean }) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null);
  return (
    <Card className="border-black/6 bg-white shadow-sm">
      <CardHeader>
        <CardTitle className="text-[17px]">{t('importData')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('importDataHint')}</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="rounded-lg bg-[#fff6f3] px-3 py-2 text-xs text-[#a65b49]">{t('importDataReplace')}</p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="cursor-pointer rounded-lg border border-black/10 px-3 py-2 text-xs font-medium text-[#52615d] hover:bg-black/[0.03]">
            {file ? file.name : t('importDataChoose')}
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(event) => { setFile(event.target.files?.[0] ?? null); setOutcome(null); }}
            />
          </label>
          <Button
            disabled={!file || importing}
            onClick={async () => {
              if (!file) return;
              if (!window.confirm(t('confirmReplaceData'))) return;
              try {
                setOutcome({ ok: true, message: await onImportData(file) });
                setFile(null);
                if (inputRef.current) inputRef.current.value = '';
              } catch (error) {
                setOutcome({ ok: false, message: error instanceof Error ? error.message : t('importDataFailed') });
              }
            }}
            className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"
          >
            <RefreshCw className={`size-4 ${importing ? 'animate-spin' : ''}`} />
            {importing ? t('importingEllipsis') : t('importDataConfirm')}
          </Button>
        </div>
        {outcome && <p className={`text-xs ${outcome.ok ? 'text-[#2d7b65]' : 'text-[#a65b49]'}`}>{outcome.message}</p>}
      </CardContent>
    </Card>
  );
}

function ReportsView({ year, month, downloadBusy, downloadError, canManageBackups, onDownload, onDownloadData, onImportData, importing, apiUrl, onReload }: { year: number; month: number; downloadBusy: boolean; downloadError: string; canManageBackups: boolean; onDownloadData: () => void; onImportData: (file: File) => Promise<string>; importing: boolean; apiUrl: string; onReload: () => Promise<void>; onDownload: (kind: 'excel' | 'pdf') => void }) {
  const { t, monthNames } = useI18n();
  return <div className="space-y-5">{downloadBusy && <p role="status">{t('downloadPreparing')}</p>}{downloadError && <p role="alert" className="text-sm text-[#a65b49]">{downloadError}</p>}<Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('exportReport')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('exportReportForPeriod', { period: formatPeriodRef(monthNames, year, month) })} · {t('exportReportSubtitle')}</p></CardHeader><CardContent className="flex flex-wrap gap-3"><Button disabled={downloadBusy} onClick={() => onDownload('excel')} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]"><FileSpreadsheet className="size-4" />{t('downloadExcel')}</Button><Button disabled={downloadBusy} variant="outline" onClick={() => onDownload('pdf')}><Download className="size-4" />{t('downloadPdf')}</Button></CardContent></Card><Card className="border-black/6 bg-white shadow-sm"><CardHeader><CardTitle className="text-[17px]">{t('dataExchangeTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('downloadDataHint')}</p></CardHeader><CardContent><Button disabled={downloadBusy} variant="outline" onClick={onDownloadData}><Download className="size-4" />{t('downloadData')}</Button></CardContent></Card><ImportDataCard onImportData={onImportData} importing={importing} />{canManageBackups && <BackupsCard apiUrl={apiUrl} onRestored={onReload} />}</div>;
}

// Quello che il modulo del movimento ha letto dai campi mentre li si compila.
// Va svuotato a ogni apertura: rimasto dal movimento precedente, il suo tipo
// decideva le categorie offerte per quello nuovo.
const MODULO_VUOTO = { occurred: '', type: '', amount: 0, origine: '' };

function uniqueOptions(current: string, options: string[]) {
  return Array.from(new Set([current, ...options].filter(Boolean)));
}

// Valute in cui rileggere il patrimonio. Non e' un elenco chiuso: qualunque
// codice quotato dalla fonte funziona, questi sono solo i piu' comuni.
const COMMON_CURRENCIES = ['USD', 'CHF', 'GBP', 'JPY', 'CAD', 'AUD', 'SEK', 'NOK', 'BTC', 'ETH'];

function SettingCurrencies({ label, value, saving, onChange }: {
  label: string; value: string; saving: boolean; onChange: (value: string) => void;
}) {
  const { t } = useI18n();
  const codes = value.split(',').map((code) => code.trim().toUpperCase()).filter(Boolean);
  const save = (next: string[]) => onChange(Array.from(new Set(next)).join(','));
  return (
    <div className="space-y-1.5 text-xs font-medium text-[#52615d]">
      <span className="flex items-center justify-between">
        <span>{label}</span>
        {saving && <span className="font-normal text-[#87918e]">{t('savingEllipsis')}</span>}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        {codes.map((code) => (
          <button
            key={code}
            type="button"
            onClick={() => save(codes.filter((item) => item !== code))}
            className="flex items-center gap-1 rounded-full bg-[var(--money-primary)] px-2.5 py-1 text-xs font-medium text-white"
          >
            {code}<X className="size-3" />
          </button>
        ))}
        <select
          value=""
          onChange={(event) => { if (event.target.value) save([...codes, event.target.value]); }}
          className="h-8 rounded-lg border border-input bg-white px-2 text-xs outline-none focus:border-ring"
        >
          <option value="">{t('addCurrency')}</option>
          {COMMON_CURRENCIES.filter((code) => !codes.includes(code)).map((code) => (
            <option key={code} value={code}>{code}</option>
          ))}
        </select>
      </div>
    </div>
  );
}

function SettingSelect({ label, value, options, saving, onChange, disabled, hint, labels }: { label: string; value: string; options: string[]; saving: boolean; onChange: (value: string) => void; disabled?: boolean; hint?: string; labels?: Record<string, string> }) {
  const { t } = useI18n();
  return <label className="block space-y-1.5 text-xs font-medium text-[#52615d]"><span className="flex items-center justify-between"><span>{label}</span>{saving && <span className="font-normal text-[#71807c]">{t('savingEllipsis')}</span>}</span><select value={value} onChange={(event) => onChange(event.target.value)} disabled={saving || disabled} className="h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm text-[#17211f] outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 disabled:cursor-not-allowed disabled:bg-[#f4f5f1] disabled:opacity-60">{uniqueOptions(value, options).map((option) => <option key={option} value={option}>{labels?.[option] ?? option}</option>)}</select>{hint && <p className="font-normal leading-4 text-[#87918e]">{hint}</p>}</label>;
}

// Un'impostazione che si scrive invece di sceglierla: il simbolo di un indice
// non e' un elenco chiuso. Salva uscendo dal campo o premendo Invio, non a ogni
// lettera: il simbolo si digita una volta, e ogni tasto sarebbe un salvataggio.
function SettingText({ label, value, saving, onChange, hint, placeholder }: { label: string; value: string; saving: boolean; onChange: (value: string) => void; hint?: string; placeholder?: string }) {
  const { t } = useI18n();
  const [bozza, setBozza] = useState(value);
  // Il valore vero e' quello del server: quando arriva (o cambia altrove), la
  // bozza si allinea invece di restare quella digitata.
  useEffect(() => setBozza(value), [value]);
  const salva = () => { const pulito = bozza.trim(); if (pulito !== value) onChange(pulito); };
  return <label className="block space-y-1.5 text-xs font-medium text-[#52615d]">
    <span className="flex items-center justify-between"><span>{label}</span>{saving && <span className="font-normal text-[#71807c]">{t('savingEllipsis')}</span>}</span>
    <Input value={bozza} placeholder={placeholder} onChange={(event) => setBozza(event.target.value)} onBlur={salva}
      onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); salva(); } }} />
    {hint && <p className="font-normal leading-4 text-[#87918e]">{hint}</p>}
  </label>;
}

function formatCheckValue(key: string, value: number, locale: string) {
  return ['income', 'expenses', 'savings'].includes(key)
    ? new Intl.NumberFormat(locale, { style: 'currency', currency: 'EUR', minimumFractionDigits: 2 }).format(value)
    : new Intl.NumberFormat(locale).format(value);
}

/* Il capitale proprio non e' un gruppo di conti da compilare: e' quello che
   resta delle attivita' tolti i debiti. Scomporlo serve a rispondere alla
   domanda "da dove viene questa cifra": tanto l'hai versato, tanto e' cresciuto
   da solo, tanto sta fermo sui conti. Le tre voci sommano al totale per
   costruzione, quindi il bilancio non puo' non quadrare. */
function EquityCard({ versati, rivalutazione, resto, passivita, haPortafoglio }: {
  versati: number;
  rivalutazione: number;
  resto: number;
  // Positiva: e' il debito, e va sottratta. `/api/accounts` gira gia' il segno.
  passivita: number;
  haPortafoglio: boolean;
}) {
  const { t, formatEuro } = useI18n();
  const voci: Array<[string, number, string | null]> = haPortafoglio
    ? [[t('equityContributed'), versati, t('equityContributedHint')],
       [t('equityRevaluation'), rivalutazione, t('equityRevaluationHint')],
       [t('equityRest'), resto, t('equityRestHint')]]
    : [[t('equityRest'), resto, t('equityRestHint')]];
  // Senza questa riga le voci sommavano il solo attivo, mentre il totale
  // sopra era gia' al netto dei debiti: tre numeri che non facevano il quarto.
  if (Math.abs(passivita) > 0.005) voci.push([t('balanceSheetLiabilitiesOnly'), -passivita, null]);
  // Ricavato da cio' che la card elenca, non passato da fuori: cosi' il numero
  // in cima e le voci sotto non possono piu' raccontare cose diverse.
  const totale = voci.reduce((somma, [, valore]) => somma + valore, 0);
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader className="flex-row items-center justify-between">
        <div>
          <CardTitle className="text-[17px]">{t('equityTitle')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('equityFormula')}</p>
        </div>
        <p className="font-semibold tabular-nums">{formatEuro(totale)}</p>
      </CardHeader>
      <CardContent className="divide-y divide-black/5 border-t border-black/5 pt-2">
        {voci.map(([etichetta, valore, spiegazione]) => (
          <div key={etichetta} className="flex items-start justify-between gap-3 py-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{etichetta}</p>
              {spiegazione && <p className="mt-0.5 text-xs leading-5 text-[#87918e]">{spiegazione}</p>}
            </div>
            <p className={`shrink-0 text-sm font-semibold tabular-nums ${valore < 0 ? 'text-[#bd5e46]' : ''}`}>{valore > 0 && etichetta === t('equityRevaluation') ? '+' : ''}{formatEuro(valore)}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AccountGroupCard({ group, label, items, totalCount, total, netWorth, expanded, azioni = true,
                            onToggleExpand, onEdit, onValuations, onDelete }: {
  group: Account['group'];
  label: string;
  items: Account[];
  totalCount: number;
  total: number;
  netWorth: number;
  expanded: boolean;
  // Modificare, valutare, eliminare sono operazioni su oggi: guardando un mese
  // passato si spengono, perche' non esiste un saldo di giugno da correggere -
  // esistono il saldo iniziale e i movimenti.
  azioni?: boolean;
  onToggleExpand: () => void;
  onEdit: (account: Account) => void;
  onValuations: (account: Account) => void;
  onDelete: (account: Account) => void;
}) {
  const { t, formatEuro } = useI18n();
  const hiddenCount = totalCount - items.length;
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader onClick={onToggleExpand} className="flex-row cursor-pointer items-center justify-between select-none">
        <div>
          <CardTitle className="text-[17px]">{label}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{hiddenCount > 0 ? t('accountsVisibleOfTotal', { visible: items.length, total: totalCount }) : t('accountsCount', { count: totalCount })}</p>
        </div>
        <div className="flex items-center gap-2">
          <p className="mr-1 font-semibold tabular-nums">{formatEuro(total)}</p>
          <ChevronDown className={`size-4 shrink-0 text-black/40 transition-transform ${expanded ? 'rotate-180' : ''}`} />
        </div>
      </CardHeader>
      {expanded && (items.length === 0 ? (
        <p className="border-t border-black/5 px-5 pt-3 text-xs text-[#87918e]">{t('accountsAllHiddenZero')}</p>
      ) : (
        <CardContent className="divide-y divide-black/5 border-t border-black/5 pt-2">{items.map((account) => { const valore = account.value; const share = account.countsInNetWorth === false || !netWorth ? null : Math.abs(valore) / Math.abs(netWorth) * 100; return <div key={account.id} className="py-3.5"><div className="flex items-center gap-3"><span className="grid size-9 place-items-center rounded-xl bg-[#edf0ed] text-[#4e6c64]"><Landmark className="size-4" /></span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{account.name}</p>{account.notes && <p className="mt-0.5 line-clamp-2 text-xs text-[#71807c]">{account.notes}</p>}</div><div className="text-right"><p className="text-sm font-semibold tabular-nums">{formatEuro(valore)}</p><p title={t('netWorthShareExplanation')} className="text-[11px] text-[#87918e]">{share !== null ? t('netWorthShare', { percent: share.toFixed(1) })
                    : account.countsInNetWorth === false ? t('accountOutsideNetWorth') : '—'}</p></div>{azioni && <>{account.needsManualValuation && <Button size="icon" variant="ghost" aria-label={`${t('valuationsTitle')} ${account.name}`} onClick={() => onValuations(account)} className="text-[#52615d] hover:text-[#173b33]"><Gauge className="size-4" /></Button>}<Button size="icon" variant="ghost" aria-label={`${t('edit')} ${account.name}`} onClick={() => onEdit(account)} className="text-[#52615d] hover:text-[#173b33]"><Pencil className="size-4" /></Button><Button size="icon" variant="ghost" aria-label={`${t('delete')} ${account.name}`} onClick={() => onDelete(account)} className="text-[#bd5e46] hover:text-[#a04f3a]"><Trash2 className="size-4" /></Button></>}</div>{/* Costo e rivalutazione non compaiono qui: sono le prime due voci della card
    del capitale proprio, dove hanno anche la spiegazione. La riconciliazione
    invece riguarda solo questo conto e vale per tutti, broker compresi: il
    saldo iniziale e la differenza col dichiarato sono la sua storia, non il
    valore di mercato. */}
{/* Resta il solo saldo iniziale: e' l'unico dei tre che si puo' cambiare.
    Il "dichiarato" era la cifra importata dall'Excel, e da quando l'app non
    lo legge piu' nessuno lo aggiorna: confrontarcisi produceva una differenza
    che non si poteva ne' spiegare ne' correggere. */}
{azioni && Math.abs(account.startingBalance) > 0.005 && <p className="ml-12 mt-1.5 text-[11px] text-[#87918e]">{t('initial')} <b className="font-medium text-[#52615d]">{formatEuro(account.startingBalance)}</b></p>}</div>; })}</CardContent>
      ))}
    </Card>
  );
}

/* Il bilancio nel tempo, a profondita' variabile.
   Sostituisce tre grafici che non si parlavano: il trend a sei linee, lo
   storico per gruppo della pagina Conti e il dettaglio. Avevano tre periodi
   diversi, quindi non si potevano nemmeno sovrapporre a occhio.
   Qui il periodo e' uno e non si azzera scendendo: scendere serve a capire
   quello che hai appena visto, e cambiargli sotto l'asse x lo renderebbe un
   altro grafico. */
const BALANCE_SHEET_RANGES = [6, 12, 36, 60, 0] as const;
const BALANCE_SHEET_COLORS = ['#3f7d68', '#9479d1', '#d8964a', '#4f8fce', '#c4677f', '#6aa06f',
                              '#a8794c', '#7b6bb5', '#4aa3a3', '#cf7f4f', '#8c8f4a', '#b06a9c'];

function meseISO(scostamento: number): string {
  const quando = new Date(OGGI.getFullYear(), OGGI.getMonth() - scostamento, 1);
  return `${quando.getFullYear()}-${String(quando.getMonth() + 1).padStart(2, '0')}`;
}

function BalanceSheetChart({ apiUrl, primoAnno }: { apiUrl: string; primoAnno: number }) {
  const { t, formatEuro, formatCompactEuro, formatPeriodLabel } = useI18n();
  const [mesi, setMesi] = useState<number>(12);
  const [percorso, setPercorso] = useState<{ level: BalanceSheetLevel; side?: string; component?: string }>({ level: 'networth' });
  const [dati, setDati] = useState<BalanceSheetSeries | null>(null);
  const [caricamento, setCaricamento] = useState(false);
  const [errore, setErrore] = useState(false);

  // Oltre i tre anni si passa al trimestre: sessanta punti su una card larga
  // seicento pixel sono rumore, e chi guarda cinque anni non cerca il mese.
  const grain = mesi === 0 || mesi > 36 ? 'quarter' : 'month';
  const da = mesi === 0 ? `${primoAnno}-01` : meseISO(mesi - 1);
  const a = meseISO(0);

  useEffect(() => {
    let annullato = false;
    setCaricamento(true); setErrore(false);
    const query = new URLSearchParams({ from: da, to: a, grain, level: percorso.level });
    if (percorso.side) query.set('side', percorso.side);
    if (percorso.component) query.set('component', percorso.component);
    fetch(`${apiUrl}/api/balance-sheet/series?${query}`)
      .then((risposta) => { if (!risposta.ok) throw new Error('serie'); return risposta.json() as Promise<BalanceSheetSeries>; })
      .then((risposta) => { if (!annullato) setDati(risposta); })
      .catch(() => { if (!annullato) { setDati(null); setErrore(true); } })
      .finally(() => { if (!annullato) setCaricamento(false); });
    return () => { annullato = true; };
  }, [apiUrl, da, a, grain, percorso]);

  // Le chiavi note hanno un nome tradotto; quelle per conto portano gia' il
  // nome del conto e restano come sono.
  const nomeVoce = (chiave: BalanceSheetKey) => {
    const noti: Record<string, string> = {
      assets: t('balanceSheetAssets'), liabilities: t('balanceSheetLiabilitiesOnly'),
      liquidity: t('bsLiquidity'), investments: t('bsInvestments'), other: t('bsOtherAssets'),
      cost: t('portfolioCost'), market: t('bsMarketValue'),
    };
    return noti[chiave.label] ?? chiave.label;
  };

  const righe = (dati?.series ?? []).map((punto) => ({ label: punto.label, total: punto.total, ...punto.values }));
  const chiavi = dati?.keys ?? [];
  const config: ChartConfig = Object.fromEntries(chiavi.map((chiave, indice) =>
    [chiave.key, { label: nomeVoce(chiave), color: BALANCE_SHEET_COLORS[indice % BALANCE_SHEET_COLORS.length] }]));

  const scendi = (chiave: BalanceSheetKey) => {
    if (!chiave.drillTo) return;
    if (chiave.drillTo === 'side') setPercorso({ level: 'side', side: chiave.key });
    else if (chiave.drillTo === 'instruments') setPercorso({ level: 'instruments' });
    else setPercorso({ level: 'component', side: dati?.side ?? 'assets', component: chiave.key });
  };
  const risali = (livello: BalanceSheetLevel, lato: string | null) =>
    setPercorso(livello === 'networth' ? { level: 'networth' } : { level: 'side', side: lato ?? 'assets' });

  const titoloLivello = dati?.level === 'networth' ? t('bsLevelNetWorth')
    : dati?.level === 'instruments' ? t('bsLevelInstruments')
    : dati?.component ? nomeVoce({ key: dati.component, label: dati.component, drillTo: null })
    : dati?.side === 'liabilities' ? t('balanceSheetLiabilitiesOnly') : t('balanceSheetAssets');

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-[17px]">{t('bsChartTitle')}</CardTitle>
            {/* La briciola nasce dai click che hai gia' fatto: un menu' ti
                chiederebbe di scegliere fra livelli che non sai se ti interessano. */}
            <nav aria-label={t('bsBreadcrumb')} className="mt-1 flex flex-wrap items-center gap-1 text-xs text-[#7b8784]">
              {(dati?.breadcrumb ?? []).map((passo) => (
                <span key={`${passo.level}-${passo.side ?? ''}`} className="flex items-center gap-1">
                  <button type="button" onClick={() => risali(passo.level, passo.side)} className="rounded font-medium text-[#3f7d68] underline-offset-2 hover:underline">
                    {passo.label === 'networth' ? t('bsLevelNetWorth') : passo.label === 'assets' ? t('balanceSheetAssets') : t('balanceSheetLiabilitiesOnly')}
                  </button>
                  <ChevronRight className="size-3 text-[#b3bcb8]" />
                </span>
              ))}
              <span className="font-medium text-[#1f2c28]">{titoloLivello}</span>
            </nav>
          </div>
          <div className="flex gap-1 rounded-lg border border-black/6 bg-[#f4f5f1] p-1 text-xs">
            {BALANCE_SHEET_RANGES.map((valore) => (
              <button key={valore} type="button" onClick={() => setMesi(valore)}
                className={`rounded-md px-2.5 py-1 font-medium transition ${mesi === valore ? 'bg-white text-[#173b33] shadow-sm' : 'text-[#66736f] hover:text-[#173b33]'}`}>
                {valore === 0 ? t('bsRangeAll') : t('bsRangeMonths', { count: valore })}
              </button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {errore ? <p role="alert" className="py-12 text-center text-sm text-[#bd5e46]">{t('bsChartFailed')}</p>
          : righe.length === 0 ? <p className="py-12 text-center text-sm text-[#71807c]">{caricamento ? t('updating') : t('bsChartEmpty')}</p>
          : <>
            <div className="mb-3 flex flex-wrap gap-1.5">
              {chiavi.map((chiave, indice) => (
                <button key={chiave.key} type="button" onClick={() => scendi(chiave)} disabled={!chiave.drillTo}
                  title={chiave.drillTo ? t('bsDrillHint') : undefined}
                  className={`flex items-center gap-2 rounded-full border border-[#cfd6d2] bg-white px-3 py-1 text-xs font-medium text-[#1f2c28] transition ${chiave.drillTo ? 'cursor-pointer hover:border-[#3f7d68] hover:bg-[#f2f7f4]' : 'cursor-default'}`}>
                  <span className="size-2.5 rounded-full" style={{ backgroundColor: BALANCE_SHEET_COLORS[indice % BALANCE_SHEET_COLORS.length] }} />
                  {nomeVoce(chiave)}
                  {chiave.drillTo && <ChevronRight className="size-3 text-[#8c9a95]" />}
                </button>
              ))}
            </div>
            <ChartContainer config={config} className={`h-[340px] w-full ${caricamento ? 'opacity-60' : ''}`}>
              <ComposedChart accessibilityLayer data={righe}>
                <CartesianGrid vertical={false} strokeDasharray="3 5" />
                <XAxis dataKey="label" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} minTickGap={20} />
                <YAxis tickLine={false} axisLine={false} width={64} tickFormatter={(valore) => formatCompactEuro(Number(valore))} />
                <ReferenceLine y={0} stroke="#c9d1cd" />
                <ChartTooltip content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(valore) => formatEuro(Number(valore))} />} />
                {/* Il livello 0 impila attivita' e passivita' attorno allo zero
                    e ci sovrappone il patrimonio: due aree e una linea dicono
                    quel che prima dicevano sei linee sovrapposte. */}
                {/* Costo e mercato non si impilano: uno sta dentro l'altro.
                    Il costo e' l'area piena, il mercato la linea che ci corre
                    sopra, e lo spazio fra le due E' la rivalutazione - che e'
                    l'unica cosa che questo livello ha da dire. */}
                {dati?.level === 'instruments' ? <>
                  <Area type="monotone" dataKey="cost" name={t('portfolioCost')} fill="#9479d1" fillOpacity={0.28} stroke="#9479d1" strokeWidth={1.5} />
                  <Line type="monotone" dataKey="market" name={t('bsMarketValue')} stroke="#173b33" strokeWidth={2.4} dot={false} />
                </> : <>
                  {chiavi.map((chiave, indice) => (
                    <Area key={chiave.key} type="monotone" dataKey={chiave.key} name={nomeVoce(chiave)}
                      stackId="bilancio"
                      fill={BALANCE_SHEET_COLORS[indice % BALANCE_SHEET_COLORS.length]} fillOpacity={0.5}
                      stroke={BALANCE_SHEET_COLORS[indice % BALANCE_SHEET_COLORS.length]} strokeWidth={1.5} />
                  ))}
                  <Line type="monotone" dataKey="total" name={t('bsTotalLine')} stroke="#173b33" strokeWidth={2} dot={false} />
                </>}
              </ComposedChart>
            </ChartContainer>
            {dati && dati.hidden > 0 && <p className="mt-2 text-xs text-[#87918e]">{t('accountHistoryHidden', { count: dati.hidden })}</p>}
          </>}
      </CardContent>
    </Card>
  );
}

function MetricCard({ title, titleHint, value, valueLabel, change, delta, icon: Icon, tone, featured = false }: { title: string; titleHint?: string; value: number; valueLabel?: string; change: string; delta?: number; icon: typeof ArrowDownRight; tone: 'income' | 'expense' | 'saving' | 'worth'; featured?: boolean }) {
  const { formatEuro } = useI18n();
  const styles = { income: 'bg-[#e5f3ed] text-[#2d7b65]', expense: 'bg-[#fce9e3] text-[#c75f44]', saving: 'bg-[#edf0ff] text-[#536fc1]', worth: 'bg-[#f2efdb] text-[#7d7135]' };
  // Sulle spese un delta negativo e' una buona notizia; sulle altre voci il
  // delta positivo. Senza `delta` (es. card di Budget/Investimenti dove `change`
  // e' un'etichetta, non un confronto) si cade sul default storico: rosso per
  // le spese, verde-neutro per il resto.
  const changeColor = delta !== undefined
    ? (tone === 'expense'
        ? (delta <= 0 ? 'text-[#2d7b65]' : 'text-[#bd6c58]')
        : (delta >= 0 ? 'text-[#2d7b65]' : 'text-[#bd6c58]'))
    : (tone === 'expense' ? 'text-[#bd6c58]' : 'text-[#618078]');
  return <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]"><CardContent className={featured ? 'p-6' : 'p-5'}><div className="mb-4 flex items-center justify-between"><span title={titleHint} className={`${featured ? 'text-base' : 'text-sm'} font-medium text-[#71807c]`}>{title}</span><span className={`grid ${featured ? 'size-10' : 'size-8'} place-items-center rounded-lg ${styles[tone]}`}><Icon className={featured ? 'size-5' : 'size-4'} /></span></div><p className={`${featured ? 'text-[32px] sm:text-[36px]' : 'text-[25px]'} font-semibold tracking-[-0.03em]`}>{valueLabel ?? formatEuro(value)}</p><p className={`mt-2 text-xs ${changeColor}`}>{change}</p></CardContent></Card>;
}

function NetWorthCard({ detail, comparison }: { detail: Summary['netWorthDetail']; comparison: Summary['netWorthComparison'] }) {
  const { t, formatEuro, locale, monthNames } = useI18n();
  const gainPositive = detail.gain >= 0;
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center">
        <div className="flex flex-1 items-start gap-4">
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-[#f2efdb] text-[#7d7135]"><Landmark className="size-5" /></span>
          <div>
            <p className="text-sm font-medium text-[#71807c]">{t('netWorth')}</p>
            <p className="mt-1 text-[25px] font-semibold tracking-[-0.03em]">{formatEuro(detail.total)}</p>
            <p className="mt-1 text-xs text-[#618078]">{formatComparisonChange(t, formatEuro, monthNames, comparison?.totalDelta, comparison)}</p>
          </div>
        </div>
        <div className="flex flex-col gap-2 border-t border-black/6 pt-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
          <p className="text-[11px] font-medium uppercase tracking-wide text-[#87918e]">{t('investmentPortfolioLabel')}</p>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div>
              <p className="text-[11px] text-[#87918e]">{t('marketValue')}</p>
              <p className="text-sm font-semibold">{formatEuro(detail.marketValue)}</p>
            </div>
            <div>
              <p className="text-[11px] text-[#87918e]">{t('investedCapital')}</p>
              <p className="text-sm font-semibold">{formatEuro(detail.investedCapital)}</p>
            </div>
            <div>
              <p className="text-[11px] text-[#87918e]">{t('gainLoss')}</p>
              <p className={`flex items-center gap-1 text-sm font-semibold ${gainPositive ? 'text-[#2d7b65]' : 'text-[#bd6c58]'}`}>
                {gainPositive ? <TrendingUp className="size-3.5" /> : <TrendingDown className="size-3.5" />}
                {gainPositive ? '+' : '−'}{formatEuro(Math.abs(detail.gain))}
                {detail.gainPercent !== null && <span className="text-xs font-normal text-[#87918e]">{t('percentOnCapital', { percent: detail.gainPercent.toLocaleString(locale) })}</span>}
              </p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonNetWorthCard() {
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="flex flex-col gap-5 p-5 lg:flex-row lg:items-center">
        <div className="flex flex-1 items-start gap-4">
          <SkeletonBlock className="size-10 shrink-0 rounded-lg" />
          <div>
            <SkeletonBlock className="h-4 w-28" />
            <SkeletonBlock className="mt-2.5 h-7 w-36" />
            <SkeletonBlock className="mt-2.5 h-3 w-40" />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-6 border-t border-black/6 pt-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
          <SkeletonBlock className="h-10 w-24" />
          <SkeletonBlock className="h-10 w-24" />
          <SkeletonBlock className="h-10 w-24" />
        </div>
      </CardContent>
    </Card>
  );
}

/* I debiti in Panoramica: il riepilogo della pagina Debiti, non un conto
   rifatto qui. Vale a oggi e non al periodo scelto, come la pagina da cui
   viene, e lo dice. Si carica da sola: la Panoramica non aspetta i piani di
   ammortamento per mostrare entrate e spese. */
function DebitCard({ apiUrl, version, onOpen }: { apiUrl: string; version: number; onOpen: () => void }) {
  const { t, formatEuro, formatDate, locale } = useI18n();
  const [data, setData] = useState<LiabilityData | null>(null);
  const [errore, setErrore] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/api/liabilities`, { signal: controller.signal })
      .then((r) => r.ok ? r.json() as Promise<LiabilityData> : Promise.reject(new Error('liabilities')))
      .then((payload) => { setData(payload); setErrore(false); })
      .catch((err: unknown) => { if (!(err instanceof DOMException && err.name === 'AbortError')) setErrore(true); });
    return () => controller.abort();
  }, [apiUrl, version]);

  if (!data && !errore) return <SkeletonDebitCard />;
  // La rata piu' vicina fra tutti i prestiti: e' la domanda che si fa aprendo
  // l'app, "quando mi tocca pagare e quanto".
  const prossima = (data?.items ?? [])
    .flatMap((item) => item.kind === 'term_loan' && item.nextPayment ? [{ name: item.name, ...item.nextPayment }] : [])
    .sort((x, y) => x.dueOn.localeCompare(y.dueOn))[0];
  const summary = data?.summary;
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="flex items-start gap-4 p-5">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-[#f2efdb] text-[#7d7135]"><CreditCard className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <p className="text-sm font-medium text-[#71807c]">{t('debitCard')}</p>
            {data && <p className="text-[11px] text-[#a0a8a5]">{t('debtAsOf', { date: formatDate(`${data.asOf}T12:00:00`) })}</p>}
          </div>
          {errore || !summary ? <p className="mt-2 text-sm text-[#87918e]">{t('debitCardError')}</p>
            : summary.total === 0 ? <p className="mt-2 text-sm text-[#87918e]">{t('debitCardNone')}</p>
            : <>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{formatEuro(summary.totalDebt)}</p>
              <p className="mt-1 text-xs text-[#87918e]">
                {t('debtMonthlyService')}: {formatEuro(summary.monthlyService)}
                {summary.weightedRate != null && ` · ${t('debtAverageRate')}: ${summary.weightedRate.toLocaleString(locale)}%`}
              </p>
              {prossima && <p className="mt-2 text-xs text-[#52615d]">
                {t('theoreticalNextPayment')}: <b className="font-semibold tabular-nums">{formatEuro(prossima.payment)}</b> · {formatDate(`${prossima.dueOn}T12:00:00`)} · {prossima.name}
              </p>}
              {summary.unclassifiedCount > 0 && <p className="mt-1 text-xs text-[#bd5e46]">
                {t('debtUnclassifiedSummary', { count: summary.unclassifiedCount, amount: formatEuro(summary.unclassifiedAmount) })}
              </p>}
            </>}
          <button type="button" onClick={onOpen} className="mt-2 text-xs font-medium text-[#2d7b65] hover:underline">{t('debitCardOpen')}</button>
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonDebitCard() {
  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardContent className="flex items-start gap-4 p-5">
        <SkeletonBlock className="size-10 shrink-0 rounded-lg" />
        <div>
          <SkeletonBlock className="h-4 w-24" />
          <SkeletonBlock className="mt-2.5 h-3 w-32" />
        </div>
      </CardContent>
    </Card>
  );
}

function SkeletonBlock({ className }: { className: string }) {
  return <div className={`animate-pulse rounded-md bg-black/[0.06] ${className}`} />;
}

function SkeletonMetricCard() {
  return <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]"><CardContent className="p-5"><div className="mb-4 flex items-center justify-between"><SkeletonBlock className="h-4 w-16" /><SkeletonBlock className="size-8 rounded-lg" /></div><SkeletonBlock className="h-7 w-28" /><SkeletonBlock className="mt-2.5 h-3 w-32" /></CardContent></Card>;
}


function PeriodBreakdownCard({ breakdown, isWholeYear }: { breakdown: SummaryBreakdown | null; isWholeYear: boolean }) {
  const { t, formatEuro, formatCompactEuro, formatPeriodLabel } = useI18n();
  const [tab, setTab] = useState<'expenses' | 'income' | 'savings'>('expenses');
  const BREAKDOWN_TABS = [['expenses', t('expensesType')], ['income', t('incomeType')], ['savings', t('savingsType')]] as const;
  const pieConfig = { value: { label: t('amount') } } satisfies ChartConfig;
  const pie = breakdown?.pie[tab];

  return (
    <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader className="flex-row items-start justify-between pb-2">
        <div><CardTitle className="text-[17px]">{t('categoryBreakdown')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{isWholeYear ? t('categoryBreakdownWholeYear') : t('categoryBreakdownPeriod')}</p></div>
        <div className="flex gap-1.5">{BREAKDOWN_TABS.map(([value, label]) => <button key={value} type="button" onClick={() => setTab(value)} className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${tab === value ? 'bg-[var(--money-primary)] text-white' : 'bg-[#f4f5f1] text-[#66736f] hover:bg-[#eceee8]'}`}>{label}</button>)}</div>
      </CardHeader>
      <CardContent>
        {!pie || pie.items.length === 0 ? (
          <div className="flex h-[260px] items-center justify-center text-sm text-[#87918e]">{t('noMovementsInPeriod')}</div>
        ) : (
          <div className={isWholeYear ? 'grid gap-6 lg:grid-cols-[280px_1fr]' : 'grid gap-6 sm:grid-cols-[280px_1fr]'}>
            <ChartContainer config={pieConfig} className="mx-auto aspect-square h-[220px]">
              <PieChart>
                <ChartTooltip content={<ChartTooltipContent formatter={(value) => <span className="font-mono font-medium tabular-nums">{formatEuro(Number(value))}</span>} />} />
                <Pie data={pie.items} dataKey="value" nameKey="name" innerRadius={50} outerRadius={90} paddingAngle={2}>
                  {pie.items.map((item) => <Cell key={item.name} fill={item.color} />)}
                </Pie>
              </PieChart>
            </ChartContainer>
            <div className="space-y-2.5 self-center">
              {pie.items.map((item) => <div key={item.name} className="flex items-center justify-between gap-3 text-sm"><span className="flex min-w-0 items-center gap-2 text-[#52615d]"><i className="size-2.5 shrink-0 rounded-full" style={{ background: item.color }} /><span className="truncate">{item.name}</span></span><span className="flex shrink-0 items-baseline gap-2 tabular-nums"><span className="font-medium">{formatCompactEuro(item.value)}</span><span className="font-normal text-[#87918e]">{pie.total ? Math.round((item.value / pie.total) * 100) : 0}%</span></span></div>)}
              <div className="flex items-center justify-between border-t border-black/8 pt-2.5 text-sm font-semibold"><span>{t('total')}</span><span className="tabular-nums">{formatCompactEuro(pie.total)}</span></div>
            </div>
          </div>
        )}

      </CardContent>
    </Card>
  );
}

function PeriodBreakdownTable({ breakdown }: { breakdown: SummaryBreakdown | null }) {
  const { t, formatCompactEuro } = useI18n();
  if (!breakdown) return null;
  const sections: Array<{ key: 'income' | 'expenses' | 'savings'; label: string }> = [
    { key: 'income', label: t('income') },
    { key: 'expenses', label: t('expenses') },
    { key: 'savings', label: t('savings') },
  ];
  // Gli stessi colori che entrate, uscite e risparmi hanno gia' nelle card e
  // nelle icone dei movimenti: qui servono a far leggere le tre tabelle come
  // tre blocchi distinti invece che come un unico elenco lungo.
  const stiliSezione = {
    income: { accento: '#2d7b65', tinta: '#e5f3ed', bordo: '#cfe6dc', icona: BadgeEuro },
    expenses: { accento: '#c75f44', tinta: '#fce9e3', bordo: '#f4d8ce', icona: CreditCard },
    savings: { accento: '#536fc1', tinta: '#edf0ff', bordo: '#d9dff7', icona: PiggyBank },
  } as const;
  // overflow-visible sulla Card: la sua classe base ha overflow-hidden, che
  // annulla la testata appiccicata delle sezioni. Qui non deve clippare niente,
  // i blocchi hanno i loro bordi arrotondati.
  return (
    <Card className="overflow-visible border-black/6 bg-white shadow-sm shadow-black/[0.025]">
      <CardHeader className="pb-3"><CardTitle className="text-[17px]">{t('categoryDetail')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('categoryDetailSubtitle')}</p></CardHeader>
      <CardContent className="space-y-4">
        {sections.map(({ key, label }) => {
          const section = breakdown.sections[key];
          if (!section.categories.length) return null;
          const stile = stiliSezione[key];
          const IconaSezione = stile.icona;
          return (
            <div key={key} className="rounded-xl border" style={{ borderColor: stile.bordo }}>
              <div className="sticky top-[72px] z-10 flex flex-wrap items-center justify-between gap-2 rounded-t-xl px-4 py-2.5" style={{ backgroundColor: stile.tinta }}>
                <h3 className="flex items-center gap-2 text-sm font-semibold" style={{ color: stile.accento }}><IconaSezione className="size-4" />{label}</h3>
                <span className="text-xs font-medium tabular-nums" style={{ color: stile.accento }}>{t('trackedOfPlanned', { tracked: formatCompactEuro(section.actualTotal), planned: formatCompactEuro(section.plannedTotal) })}</span>
              </div>
              <div className="overflow-x-auto px-4 pb-1">
                <table className="w-full min-w-[640px] table-fixed text-sm">
                  <colgroup><col className="w-[26%]" /><col className="w-[15%]" /><col className="w-[15%]" /><col className="w-[22%]" /><col className="w-[11%]" /><col className="w-[11%]" /></colgroup>
                  <thead className="text-xs text-[#87918e]"><tr><th className="py-1.5 text-left font-medium">{t('category')}</th><th className="py-1.5 text-right font-medium">{t('tracked')}</th><th className="py-1.5 text-right font-medium">{t('budget')}</th><th className="py-1.5 text-left font-medium pl-4">{t('completion')}</th><th className="py-1.5 text-right font-medium">{t('remaining')}</th><th className="py-1.5 text-right font-medium">{t('excess')}</th></tr></thead>
                  <tbody className="divide-y divide-black/5">
                    {section.categories.map((category) => {
                      const percentage = category.completion !== null ? Math.min(category.completion * 100, 100) : 0;
                      // Superare il pianificato e' un guaio solo per le uscite:
                      // incassare o mettere da parte piu' del previsto non va
                      // dipinto di rosso come uno sforamento.
                      const over = category.completion !== null && category.completion > 1;
                      const coloreBarra = key === 'expenses' ? (over ? '#bd5e46' : '#47a889') : (over ? stile.accento : '#8aa8a0');
                      return <tr key={category.name}>
                        <td className="py-2.5 truncate font-medium">{category.name}</td>
                        <td className="py-2.5 text-right tabular-nums">{formatCompactEuro(category.tracked)}</td>
                        <td className="py-2.5 text-right tabular-nums text-[#71807c]">{formatCompactEuro(category.budget)}</td>
                        <td className="py-2.5 pl-4">
                          {category.completion !== null ? (
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#eef0ec]"><div className="h-full rounded-full" style={{ width: `${percentage}%`, background: coloreBarra }} /></div>
                              <span className="w-9 shrink-0 text-right text-xs tabular-nums text-[#71807c]">{Math.round(category.completion * 100)}%</span>
                            </div>
                          ) : <span className="text-xs text-[#87918e]">—</span>}
                        </td>
                        {/* Verde e rosso hanno un senso solo sulle uscite: li'
                            "rimanente" e' margine e "eccedenza" e' uno sforamento.
                            Su entrate e risparmi i due significati si invertono,
                            quindi l'eccedenza prende il colore della sezione e il
                            rimanente resta neutro. */}
                        <td className="py-2.5 text-right tabular-nums" style={{ color: key === 'expenses' ? '#397867' : '#71807c' }}>{category.remaining ? formatCompactEuro(category.remaining) : '—'}</td>
                        <td className="py-2.5 text-right tabular-nums" style={{ color: key === 'expenses' ? '#bd5e46' : stile.accento }}>{category.excess ? formatCompactEuro(category.excess) : '—'}</td>
                      </tr>;
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

const ANALYSIS_TYPE_TABS = [['expenses', 'expensesType', 'Expenses'], ['income', 'incomeType', 'Income'], ['savings', 'savingsType', 'Savings']] as const satisfies readonly (readonly [string, TranslationKey, string])[];

function MonthlyStackedBarChart({ data, budgetType }: { data: MonthlyBudgetPoint[]; budgetType: 'Expenses' | 'Income' | 'Savings' }) {
  const { t, formatCompactEuro, formatPeriodLabel, formatNumber } = useI18n();
  // Superare il budget e' un guaio solo per le spese. Per entrate e risparmio
  // e' il contrario, e dipingerlo di rosso dava una brutta notizia a chi aveva
  // messo da parte piu' del previsto. Cambiano il colore e la parola, non la
  // barra: e' sempre la stessa quantita'.
  // Una varianza negativa vuol dire "sforato": `budgetVarianceIsGood` risponde
  // gia' se sforare va bene per questo tipo. Negarla capovolgeva tutti e due i
  // casi - rosso al risparmio superato, verde alle spese sforate.
  const superareEBene = budgetVarianceIsGood(budgetType, -1);
  const config = {
    inBudget: { label: t('inBudget'), color: '#47a889' },
    remaining: { label: superareEBene ? t('belowTarget') : t('remaining'), color: '#c9c9c9' },
    excess: { label: superareEBene ? t('targetExceeded') : t('excess'), color: superareEBene ? '#2d7b65' : '#bd5e46' },
  } satisfies ChartConfig;
  return (
    <ChartContainer config={config} className="h-[220px] w-full">
      <BarChart accessibilityLayer data={data} barGap={2}>
        <CartesianGrid vertical={false} strokeDasharray="3 5" />
        <XAxis dataKey="month" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis tickLine={false} axisLine={false} tickMargin={6} width={40} tickFormatter={(value) => formatNumber(Number(value), { notation: 'compact', maximumFractionDigits: 1 })} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value, name) => <><span className="text-muted-foreground">{config[name as keyof typeof config]?.label}</span><span className="ml-auto font-mono font-medium tabular-nums">{formatCompactEuro(Number(value))}</span></>} />} />
        {/* Tre colori impilati non si leggono senza dire cosa sono: in alto a
            destra, dentro il grafico, con gli stessi colori delle barre. */}
        <ChartLegend verticalAlign="top" align="right" height={16} wrapperStyle={{ top: -6 }} content={<ChartLegendContent className="justify-end pb-0" />} />
        <Bar dataKey="inBudget" stackId="a" fill="var(--color-inBudget)" radius={[0, 0, 0, 0]} maxBarSize={22}>
          {data.map((point) => <Cell key={point.month} fillOpacity={point.isCurrentMonth ? 1 : 0.55} />)}
        </Bar>
        <Bar dataKey="remaining" stackId="a" fill="var(--color-remaining)" radius={[3, 3, 0, 0]} maxBarSize={22}>
          {data.map((point) => <Cell key={point.month} fillOpacity={point.isCurrentMonth ? 1 : 0.55} />)}
        </Bar>
        <Bar dataKey="excess" stackId="a" fill="var(--color-excess)" radius={[3, 3, 0, 0]} maxBarSize={22}>
          {data.map((point) => <Cell key={point.month} fillOpacity={point.isCurrentMonth ? 1 : 0.55} />)}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}

function AnnualAnalysisView({ data, period, years, categoryType, category, onPeriodChange, onCategoryTypeChange, onCategoryChange }: {
  data: AnalysisData | null;
  period: PeriodSelection;
  years: string[];
  categoryType: 'Income' | 'Expenses' | 'Savings';
  category: string | null;
  onPeriodChange: (period: PeriodSelection) => void;
  onCategoryTypeChange: (value: 'Income' | 'Expenses' | 'Savings') => void;
  onCategoryChange: (value: string | null) => void;
}) {
  const { t, formatEuro, formatCompactEuro, formatDate, formatPeriodLabel, formatNumber } = useI18n();
  const [budgetTab, setBudgetTab] = useState<'expenses' | 'income' | 'savings'>('expenses');
  const savingsConfig = {
    amount: { label: t('saved'), color: '#6d8ff4' },
    invested: { label: t('investedInMonth'), color: '#e0b04f' },
  } satisfies ChartConfig;
  // Risparmiato e investito nello stesso mese, uno accanto all'altro.
  const savingsChartData = (data?.savingsByMonth ?? []).map((point, index) => ({
    ...point,
    invested: data?.investedByMonth[index]?.amount ?? 0,
  }));
  const treemapData = data?.topExpenseCategories.map((c) => ({ name: c.name, size: c.value, fill: c.color })) ?? [];

  return (
    <div className="space-y-5">
      <div className="flex justify-end"><PeriodSelector value={period} years={years} onChange={onPeriodChange} allowMonth={false} /></div>

      <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
        <CardHeader className="flex-row items-start justify-between pb-2">
          <div><CardTitle className="text-[17px]">{t('monthlyBudgetVsTracked')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('monthlyBudgetVsTrackedSubtitle')}</p></div>
          <div className="flex gap-1.5">{ANALYSIS_TYPE_TABS.map(([value, labelKey]) => <button key={value} type="button" onClick={() => setBudgetTab(value)} className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${budgetTab === value ? 'bg-[var(--money-primary)] text-white' : 'bg-[#f4f5f1] text-[#66736f] hover:bg-[#eceee8]'}`}>{t(labelKey)}</button>)}</div>
        </CardHeader>
        <CardContent>
          {data ? <MonthlyStackedBarChart data={data.monthlyBudget[budgetTab]} budgetType={budgetTab === 'expenses' ? 'Expenses' : budgetTab === 'income' ? 'Income' : 'Savings'} /> : <div className="flex h-[220px] items-center justify-center text-sm text-[#87918e]">{t('loading')}</div>}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
          <CardHeader className="pb-2"><CardTitle className="text-[17px]">{t('topExpenseCategoriesTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('topExpenseCategoriesSubtitle')}</p></CardHeader>
          <CardContent>
            {data && treemapData.length ? (
              <ChartContainer config={{}} className="h-[280px] w-full">
                {/* Un riquadro piccolo non ha spazio per il suo nome, e senza
                    nome non dice niente: il tooltip e' l'unico modo di leggere
                    le categorie minori, che sono poi quelle che uno cerca. */}
                <Treemap data={treemapData} dataKey="size" nameKey="name" stroke="#fff" isAnimationActive={false} content={<CategoryTreemapCell />}>
                  <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel formatter={(valore, nome) => (
                    <span className="flex w-full items-baseline justify-between gap-4">
                      <span className="text-muted-foreground">{nome}</span>
                      <b className="font-mono font-medium tabular-nums">{formatEuro(Number(valore))}</b>
                    </span>
                  )} />} />
                </Treemap>
              </ChartContainer>
            ) : <div className="flex h-[280px] items-center justify-center text-sm text-[#87918e]">{t('noExpensesInYear')}</div>}
          </CardContent>
        </Card>

        <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
          <CardHeader className="pb-2"><CardTitle className="text-[17px]">{t('savingsByMonthTitle')}</CardTitle><p className="mt-1 text-xs text-[#7b8784]">{t('savingsByMonthSubtitle')}</p></CardHeader>
          <CardContent>
            {data ? (
              <ChartContainer config={savingsConfig} className="h-[280px] w-full">
                <ComposedChart accessibilityLayer data={savingsChartData} barGap={4}>
                  <CartesianGrid vertical={false} strokeDasharray="3 5" />
                  <XAxis dataKey="month" tickFormatter={formatPeriodLabel} tickLine={false} axisLine={false} tickMargin={8} />
                  <YAxis yAxisId="saved" tickLine={false} axisLine={false} tickMargin={6} width={40} tickFormatter={(value) => formatNumber(Number(value), { notation: 'compact', maximumFractionDigits: 1 })} />
                  {/* Un mese di ribilanciamento vale decine di migliaia: sulla
                      stessa scala le barre del risparmio sparirebbero. */}
                  <YAxis yAxisId="invested" orientation="right" tickLine={false} axisLine={false} tickMargin={6} width={44} tickFormatter={(value) => formatNumber(Number(value), { notation: 'compact', maximumFractionDigits: 1 })} />
                  <ChartTooltip cursor={false} content={<ChartTooltipContent labelFormatter={(etichetta) => formatPeriodLabel(etichetta)} formatter={(value, name) => <><span className="text-muted-foreground">{savingsConfig[name as keyof typeof savingsConfig]?.label}</span><span className="ml-auto font-mono font-medium tabular-nums">{formatCompactEuro(Number(value))}</span></>} />} />
                  <Bar yAxisId="saved" dataKey="amount" radius={[4, 4, 1, 1]} maxBarSize={18}>
                    {savingsChartData.map((point) => <Cell key={point.month} fill={point.amount >= 0 ? '#47a889' : '#bd5e46'} />)}
                  </Bar>
                  <Line yAxisId="invested" type="monotone" dataKey="invested" stroke="var(--color-invested)" strokeWidth={2} dot={{ r: 2.5 }} />
                  <ChartLegend content={<ChartLegendContent />} />
                </ComposedChart>
              </ChartContainer>
            ) : <div className="flex h-[280px] items-center justify-center text-sm text-[#87918e]">{t('loading')}</div>}
          </CardContent>
        </Card>
      </div>

      <Card className="border-black/6 bg-white shadow-sm shadow-black/[0.025]">
        <CardHeader className="pb-3">
          <CardTitle className="text-[17px]">{t('categoryAnalysisTitle')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('categoryAnalysisSubtitle')}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <label className="relative">
              <span className="sr-only">{t('type')}</span>
              <select aria-label={t('type')} value={categoryType} onChange={(event) => onCategoryTypeChange(event.target.value as 'Income' | 'Expenses' | 'Savings')} className="h-9 appearance-none rounded-lg border border-black/7 bg-white py-0 pl-3 pr-8 text-sm outline-none focus:border-[#5c8f82]">
                <option value="Expenses">{t('expensesType')}</option>
                <option value="Income">{t('incomeType')}</option>
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-black/45" />
            </label>
            <label className="relative">
              <span className="sr-only">{t('category')}</span>
              <select aria-label={t('category')} value={category ?? ''} onChange={(event) => onCategoryChange(event.target.value || null)} className="h-9 appearance-none rounded-lg border border-black/7 bg-white py-0 pl-3 pr-8 text-sm outline-none focus:border-[#5c8f82]">
                <option value="">{t('selectCategory')}</option>
                {(data?.categoryOptions ?? []).map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-black/45" />
            </label>
          </div>
        </CardHeader>
        <CardContent>
          {!category ? (
            <p className="py-8 text-center text-sm text-[#87918e]">{t('selectCategoryPrompt')}</p>
          ) : !data || data.categoryTransactions.length === 0 ? (
            <p className="py-8 text-center text-sm text-[#87918e]">{t('noTransactionsForCategory')}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] table-fixed text-sm">
                <colgroup><col className="w-[20%]" /><col className="w-[55%]" /><col className="w-[25%]" /></colgroup>
                <thead className="text-xs text-[#87918e]"><tr><th className="py-1.5 text-left font-medium">{t('date')}</th><th className="py-1.5 text-left font-medium">{t('description')}</th><th className="py-1.5 text-right font-medium">{t('amount')}</th></tr></thead>
                <tbody className="divide-y divide-black/5">
                  {data.categoryTransactions.map((tx, index) => <tr key={`${tx.date}-${index}`}>
                    <td className="py-2 text-[#71807c]">{formatDate(`${tx.date}T12:00:00`)}</td>
                    <td className="py-2 truncate font-medium">{tx.description}</td>
                    <td className="py-2 text-right tabular-nums">{formatEuro(tx.amount)}</td>
                  </tr>)}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CategoryTreemapCell(props: { x?: number; y?: number; width?: number; height?: number; name?: string; size?: number; fill?: string }) {
  const { formatCompactEuro } = useI18n();
  const { x = 0, y = 0, width = 0, height = 0, name, size, fill } = props;
  // Una soglia sola: o nome e importo, o niente e ci pensa il tooltip.
  const mostraNome = width > 60 && height > 32;
  const mostraImporto = mostraNome;
  return (
    <g role="img" aria-label={`${name ?? ''}${size !== undefined ? `: ${formatCompactEuro(size)}` : ''}`}>
      <rect x={x} y={y} width={width} height={height} fill={fill} stroke="#fff" strokeWidth={2} rx={4} />
      {mostraNome && (
        <text x={x + 8} y={y + 20} fill="#fff" fontSize={12} fontWeight={600}>{name}</text>
      )}
      {mostraImporto && (
        <text x={x + 8} y={y + 38} fill="#fff" fontSize={11} fillOpacity={0.85}>{size !== undefined ? formatCompactEuro(size) : ''}</text>
      )}
    </g>
  );
}

const TRANSACTION_ICON_STYLES = {
  Income: { icon: BadgeEuro, className: 'bg-[#e5f3ed] text-[#2d7b65]' },
  Expenses: { icon: CreditCard, className: 'bg-[#fce9e3] text-[#c75f44]' },
  Transfers: { icon: Landmark, className: 'bg-[#edf0ed] text-[#5d716b]' },
  Investment: { icon: LineChartIcon, className: 'bg-[#efeaf9] text-[#6b57a8]' },
  Debt: { icon: CreditCard, className: 'bg-[#fff0e8] text-[#a95e3f]' },
};

// Le callback ricevono il movimento invece di chiuderci sopra: cosi' il
// genitore puo' passarne una sola, stabile, e `memo` qui sotto ha senso.
// Con cento righe in pagina, un carattere digitato nella ricerca ne
// ridisegnava cento; adesso nessuna.
// Si dividono solo i movimenti la cui cifra non e' legata ad altro: il server
// applica la stessa regola, qui serve a non mostrare un pulsante che fallirebbe.
function divisibile(transaction: Transaction) {
  return ['Expenses', 'Income', 'Transfers'].includes(transaction.transactionType) && !transaction.refundOfId
    && !transaction.refundedById && !transaction.linkedLedger?.length && !transaction.liabilitySplit;
}

function SplitTransactionDialog({ transaction, accounts, apiUrl, categoriesByType, onClose, onDone }: {
  transaction: Transaction; accounts: Account[]; apiUrl: string; categoriesByType: Record<string, string[]>;
  onClose: () => void; onDone: () => Promise<void>;
}) {
  const { t, formatEuro } = useI18n();
  // La lista manda le uscite con il segno meno: la divisione ragiona sull'importo.
  const totale = Math.abs(transaction.amount);
  const meta = Math.round((totale - Math.ceil(totale * 100 / 2) / 100) * 100) / 100;
  const [parte, setParte] = useState({ amount: String(meta), type: transaction.transactionType === 'Income' ? 'Income' : 'Transfers',
    category: '', destination: '', details: transaction.details ?? '' });
  const [errore, setErrore] = useState('');
  const [salvando, setSalvando] = useState(false);
  const importo = Number(parte.amount);
  const resta = Math.round((totale - importo) * 100) / 100;
  const spostamento = parte.type === 'Transfers';
  const valido = importo > 0 && resta > 0 && (spostamento ? Boolean(parte.destination) : Boolean(parte.category));

  async function conferma(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    setSalvando(true);
    setErrore('');
    try {
      const response = await fetch(`${apiUrl}/api/transactions/${transaction.id}/split`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(splitPayload(parte)),
      });
      if (!response.ok) throw new Error(await responseError(response, t));
      await onDone();
    } catch (error) {
      setErrore(error instanceof Error ? error.message : t('cannotSaveGeneric'));
    } finally {
      setSalvando(false);
    }
  }

  const campo = 'h-10 w-full rounded-lg border border-input bg-white px-2.5 text-sm outline-none focus:border-ring disabled:bg-[#f4f5f1] disabled:text-[#a3adaa]';
  return <Dialog open onOpenChange={(open) => { if (!open && !salvando) onClose(); }}>
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>{t('splitMovementTitle')}</DialogTitle>
        <DialogDescription>{transaction.description} · {t('splitMovementDesc', { total: formatEuro(totale) })}</DialogDescription>
      </DialogHeader>
      <form className="space-y-3" onSubmit={conferma}>
        <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('splitNewPart')}
          <Input type="number" inputMode="decimal" min="0.01" step="0.01" required value={parte.amount}
            onChange={(e) => setParte((p) => ({ ...p, amount: e.target.value }))} />
          <span className={`block text-[11px] font-normal ${resta > 0 ? 'text-[#71807c]' : 'text-[#a94f3a]'}`}>{resta > 0 ? t('splitRemaining', { amount: formatEuro(resta) }) : t('splitInvalidAmount')}</span>
        </label>
        <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('fieldType')}
          <select className={campo} value={parte.type} onChange={(e) => setParte((p) => ({ ...p, type: e.target.value, category: '' }))}>
            <option value="Expenses">{t('typeExpense')}</option><option value="Income">{t('typeIncome')}</option><option value="Transfers">{t('typeTransfer')}</option>
          </select>
        </label>
        {spostamento
          ? <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('fieldDestinationAccount')}
            <select className={campo} required value={parte.destination} onChange={(e) => setParte((p) => ({ ...p, destination: e.target.value }))}>
              <option value="">{t('selectAccount')}</option>
              {accounts.filter((a) => a.isActive !== false && a.name !== transaction.accountName).map((a) => <option key={a.id} value={a.name}>{a.name}</option>)}
            </select></label>
          : <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('fieldCategory')}
            <select className={campo} required value={parte.category} onChange={(e) => setParte((p) => ({ ...p, category: e.target.value }))}>
              <option value="">{t('selectPlaceholder')}</option>
              {(categoriesByType[parte.type] ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
            </select></label>}
        <label className="block space-y-1 text-xs font-medium text-[#52615d]">{t('fieldDescription')}
          <Input value={parte.details} onChange={(e) => setParte((p) => ({ ...p, details: e.target.value }))} />
        </label>
        {errore && <p role="alert" className="rounded-lg bg-[#fce9e3] px-3 py-2 text-xs text-[#a94f3a]">{errore}</p>}
        <DialogFooter className="mx-0 mb-0 mt-5 border-0 bg-transparent p-0">
          <Button type="button" variant="outline" disabled={salvando} onClick={onClose}>{t('cancel')}</Button>
          <Button type="submit" disabled={salvando || !valido} className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{salvando ? t('savingEllipsis') : t('splitRow')}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}

const TransactionRow = memo(function TransactionRow({ transaction, onEdit, onDuplicate, onDelete, onRefund, onSplit }: { transaction: Transaction; onEdit?: (transaction: Transaction) => void; onDuplicate?: (transaction: Transaction) => void; onDelete?: (transaction: Transaction) => void; onRefund?: (transaction: Transaction) => void; onSplit?: (transaction: Transaction) => void }) {
  const { t, formatEuro, formatDate } = useI18n();
  // Un tipo che l'API conosce e questa tabella no farebbe esplodere la riga, e
  // con lei tutta la pagina: meglio l'icona del giroconto che una schermata bianca.
  const style = TRANSACTION_ICON_STYLES[transaction.transactionType] ?? TRANSACTION_ICON_STYLES.Transfers;
  const Icon = style.icon;
  // Trasferimenti, investimenti e rate spostano denaro fra due conti propri: non
  // e' ne' un'entrata ne' un'uscita, e il segno meno li faceva sembrare spese.
  const spostamento = ['Transfers', 'Investment', 'Debt'].includes(transaction.transactionType);
  const linkedCount = transaction.linkedLedger?.length ?? 0;
  const linkedTooltip = linkedCount === 0
    ? ''
    : linkedCount === 1
      ? `${transaction.linkedLedger?.[0]?.name ?? ''} (${transaction.linkedLedger?.[0]?.transactionType ?? ''})`
      : transaction.linkedLedger?.map((item) => `${item.name} (${item.transactionType})`).join(', ');
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3.5 sm:flex-nowrap"><span className={`grid size-9 shrink-0 place-items-center rounded-xl ${style.className}`}><Icon className="size-4" /></span><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{transaction.description}</p><div className="flex gap-2 text-[11px]">{transaction.incomplete && <span className="text-[#a05f4e]">{t('incompleteMovements')}</span>}{transaction.countsInBudget === false && !['Transfers', 'Investment', 'Debt'].includes(transaction.transactionType) && <span className="text-[#87918e]">{t('excludeBudget')}</span>}{transaction.refundedById && <button type="button" className="text-[#2d7b65] underline" onClick={() => onRefund?.(transaction)}>{t('refunded')}</button>}{transaction.liabilitySplit && <span className={transaction.liabilitySplit.classified ? 'text-[#8a5a46]' : 'text-[#a05f4e]'}>{transaction.liabilitySplit.classified ? t('debtSplitSummary', { principal: formatEuro(transaction.liabilitySplit.principal), interest: formatEuro(transaction.liabilitySplit.interest) }) : t('debtUnclassified')}</span>}</div><p className="mt-0.5 truncate text-xs text-[#87918e]">{transaction.category}{transaction.accountName ? ` · ${transaction.accountName}` : ''}{transaction.destinationName ? ` → ${transaction.destinationName}` : ''}</p></div><div className="hidden text-right text-xs text-[#87918e] sm:block"><p>{formatDate(`${transaction.effectiveOn}T12:00:00`, { day: 'numeric', month: 'short' })}</p>{transaction.effectiveOn !== transaction.occurredOn && <p className="mt-0.5 text-[11px] text-[#a0a8a5]">{t('occurredOnNote', { date: formatDate(`${transaction.occurredOn}T12:00:00`, { day: 'numeric', month: 'short' }) })}</p>}</div><p className={`w-24 text-right text-sm font-semibold tabular-nums ${!spostamento && transaction.amount > 0 ? 'text-[#2d7b65]' : 'text-[#28312f]'}`}>{spostamento ? '' : transaction.amount > 0 ? '+' : '−'}{formatEuro(Math.abs(transaction.amount))}</p>{linkedCount > 0 && <span title={linkedTooltip} aria-label={linkedCount === 1 ? t('ledgerLinkedCount_one') : t('ledgerLinkedCount_other', { count: linkedCount })} className="grid size-7 shrink-0 place-items-center rounded-full bg-[#e5f3ed] text-[#2d7b65]"><LineChartIcon className="size-3.5" /></span>}{onEdit && onDuplicate && onDelete && <div className="flex shrink-0 basis-full justify-end sm:basis-auto"><Button type="button" size="icon" variant="ghost" title={t('edit')} aria-label={`${t('edit')} ${transaction.description}`} onClick={() => onEdit(transaction)}><Pencil className="size-4" /></Button><Button type="button" size="icon" variant="ghost" title={t('duplicate')} aria-label={`${t('duplicate')} ${transaction.description}`} onClick={() => onDuplicate(transaction)}><Copy className="size-4" /></Button>{onSplit && divisibile(transaction) && <Button type="button" size="icon" variant="ghost" title={t('splitRow')} aria-label={`${t('splitRow')} ${transaction.description}`} onClick={() => onSplit(transaction)}><Split className="size-4" /></Button>}<Button type="button" size="icon" variant="ghost" title={t('delete')} aria-label={`${t('delete')} ${transaction.description}`} onClick={() => onDelete(transaction)} className="text-[#bd5e46]"><Trash2 className="size-4" /></Button></div>}</div>;
})
function CategoryRulesCard({ rules, categories, apiUrl, onChanged }: {
  rules: CategorizationRuleData[]; categories: string[]; apiUrl: string; onChanged: () => void;
}) {
  const { t, formatEuro } = useI18n();
  const vuoto = { pattern: '', category: '', type: '' as '' | 'Expenses' | 'Income', isRegex: false, minAmount: '', maxAmount: '' };
  const [form, setForm] = useState(vuoto);
  const [editId, setEditId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [errore, setErrore] = useState('');
  const [proposte, setProposte] = useState<CategorizationSuggestionsData | null>(null);
  // Le spuntate, per pattern: e' quello che distingue una proposta accettata da
  // una solo mostrata, ed e' l'unica cosa che il lotto scrive.
  const [spuntate, setSpuntate] = useState<Set<string>>(new Set());

  const chiama = async (percorso: string, metodo: string, corpo?: unknown): Promise<boolean> => {
    const risposta = await fetch(`${apiUrl}/api/categorization-rules${percorso}`, {
      method: metodo, headers: { 'Content-Type': 'application/json' },
      ...(corpo === undefined ? {} : { body: JSON.stringify(corpo) }),
    });
    if (risposta.ok) return true;
    setErrore(await messaggioErroreRegola(risposta, t));
    return false;
  };

  // Un'azione per volta: due salvataggi in volo scriverebbero l'ordine due
  // volte, e vincerebbe l'ultima risposta arrivata, non l'ultima scelta.
  const esegui = async (azione: () => Promise<boolean>) => {
    setBusy(true); setErrore('');
    try {
      if (await azione()) onChanged();
    } catch {
      setErrore(t('ruleSaveError'));
    } finally { setBusy(false); }
  };

  const salva = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    void esegui(async () => {
      const esito = await chiama(editId ? `/${editId}` : '', editId ? 'PUT' : 'POST',
        categorizationRulePayload({ ...form, transactionType: form.type || null }));
      if (esito) { setForm(vuoto); setEditId(null); }
      return esito;
    });
  };

  // L'ordine e' la priorita': si riscrive tutto, cosi' il server non deve
  // indovinare cosa volesse dire uno scambio di due righe.
  const sposta = (indice: number, verso: -1 | 1) => void esegui(() => {
    const ids = rules.map((riga) => riga.id);
    [ids[indice], ids[indice + verso]] = [ids[indice + verso], ids[indice]];
    return chiama('/order', 'PUT', { ids });
  });

  const modifica = (riga: CategorizationRuleData) => {
    setEditId(riga.id); setErrore('');
    setForm({ pattern: riga.pattern, category: riga.category, type: riga.transactionType ?? '',
      isRegex: riga.isRegex, minAmount: riga.minAmount === null ? '' : String(riga.minAmount),
      maxAmount: riga.maxAmount === null ? '' : String(riga.maxAmount) });
  };

  const intervallo = (riga: CategorizationRuleData) => riga.minAmount === null && riga.maxAmount === null ? '—'
    : `${riga.minAmount === null ? '' : formatEuro(riga.minAmount)} – ${riga.maxAmount === null ? '' : formatEuro(riga.maxAmount)}`;

  // Le sicure arrivano spuntate, le incerte no: la spunta di partenza e' il
  // giudizio dell'app, e resta un giudizio da cui si puo' dissentire.
  const impara = () => void esegui(async () => {
    const risposta = await fetch(`${apiUrl}/api/categorization-rules/suggest`, { method: 'POST' });
    if (!risposta.ok) { setErrore(await messaggioErroreRegola(risposta, t)); return false; }
    const trovate = await risposta.json() as CategorizationSuggestionsData;
    setProposte(trovate);
    setSpuntate(new Set(trovate.proposte.filter((p) => p.fiducia === 'sicura').map((p) => p.pattern)));
    return false;
  });

  const accetta = () => void esegui(async () => {
    const scelte = (proposte?.proposte ?? []).filter((p) => spuntate.has(p.pattern));
    const esito = await chiama('/bulk', 'POST', categorizationBulkPayload(scelte));
    if (esito) setProposte(null);
    return esito;
  });

  // "114 volte, 79% Groceries, 24 volte Other": quante righe sono, quanto e'
  // decisa la scelta, e cosa dice la minoranza.
  const dettaglio = (proposta: RuleProposalData) => [
    t('ruleOccurrences', { count: proposta.occorrenze }),
    `${Math.round(proposta.quota * 100)}% ${proposta.category}`,
    ...proposta.altre.map((altra) => `${t('ruleOccurrences', { count: altra.count })} ${altra.category}`),
  ].join(', ');

  return <Card className="border-black/6 bg-white shadow-sm">
    <CardHeader className="gap-3">
      <div><CardTitle className="text-[17px]">{t('categoryRules')}</CardTitle>
        <p className="mt-1 text-xs text-[#7b8784]">{t('categoryRulesHint')}</p></div>
      <div><Button type="button" variant="outline" className="h-10 bg-white" disabled={busy} onClick={impara}>{t('learnFromHistory')}</Button></div>
    </CardHeader>
    <CardContent className="space-y-4">
      {rules.length === 0
        ? <p className="text-sm text-[#71807c]">{t('ruleEmpty')}</p>
        : <div className="overflow-x-auto"><table className="w-full text-left text-sm">
          <thead className="text-xs text-[#71807c]"><tr>
            <th className="py-2 pr-2 font-medium">{t('rulePattern')}</th>
            <th className="py-2 pr-2 font-medium">{t('category')}</th>
            <th className="py-2 pr-2 font-medium">{t('type')}</th>
            <th className="py-2 pr-2 font-medium">{t('amount')}</th>
            <th className="py-2 pr-2 font-medium">{t('active')}</th>
            <th className="py-2 font-medium"><span className="sr-only">{t('edit')}</span></th>
          </tr></thead>
          <tbody className="divide-y divide-black/5">{rules.map((riga, indice) => <tr key={riga.id}>
            <td className="py-2 pr-2">{riga.isRegex ? <code className="rounded bg-[#f0f2ee] px-1.5 py-0.5 text-[13px]">{riga.pattern}</code> : riga.pattern}</td>
            <td className="py-2 pr-2">{riga.category}</td>
            <td className="py-2 pr-2">{riga.transactionType ? t(riga.transactionType === 'Expenses' ? 'typeExpense' : 'typeIncome') : t('none')}</td>
            <td className="py-2 pr-2 tabular-nums">{intervallo(riga)}</td>
            <td className="py-2 pr-2">{t(riga.active ? 'active' : 'toggleInactive')}</td>
            <td className="py-2"><div className="flex justify-end">
              <Button type="button" size="icon" variant="ghost" disabled={busy || indice === 0} title={t('ruleMoveUp')} aria-label={t('ruleMoveUp')} onClick={() => sposta(indice, -1)}><ChevronUp className="size-4" /></Button>
              <Button type="button" size="icon" variant="ghost" disabled={busy || indice === rules.length - 1} title={t('ruleMoveDown')} aria-label={t('ruleMoveDown')} onClick={() => sposta(indice, 1)}><ChevronDown className="size-4" /></Button>
              <Button type="button" size="icon" variant="ghost" title={t('edit')} aria-label={`${t('edit')} ${riga.pattern}`} onClick={() => modifica(riga)}><Pencil className="size-4" /></Button>
              <Button type="button" size="icon" variant="ghost" title={t('delete')} aria-label={`${t('delete')} ${riga.pattern}`} className="text-[#bd5e46]" disabled={busy} onClick={() => void esegui(() => chiama(`/${riga.id}`, 'DELETE'))}><Trash2 className="size-4" /></Button>
            </div></td>
          </tr>)}</tbody>
        </table></div>}

      <form className="grid gap-3 border-t border-black/5 pt-4 sm:grid-cols-[2fr_1fr_1fr_1fr_1fr_auto]" onSubmit={salva}>
        <label className="text-xs text-[#52615d]">{t('rulePattern')}
          <Input required value={form.pattern} onChange={(e) => setForm((c) => ({ ...c, pattern: e.target.value }))} className="mt-1 h-10 bg-white" /></label>
        <label className="text-xs text-[#52615d]">{t('category')}
          <select required value={form.category} onChange={(e) => setForm((c) => ({ ...c, category: e.target.value }))} className="mt-1 h-10 w-full rounded-lg border border-input bg-white px-2 text-sm">
            <option value="">{t('categoryPlaceholder')}</option>
            {categories.map((categoria) => <option key={categoria} value={categoria}>{categoria}</option>)}
          </select></label>
        <label className="text-xs text-[#52615d]">{t('type')}
          <select value={form.type} onChange={(e) => setForm((c) => ({ ...c, type: e.target.value as typeof c.type }))} className="mt-1 h-10 w-full rounded-lg border border-input bg-white px-2 text-sm">
            <option value="">{t('none')}</option>
            <option value="Expenses">{t('typeExpense')}</option>
            <option value="Income">{t('typeIncome')}</option>
          </select></label>
        <label className="text-xs text-[#52615d]">{t('ruleAmountFrom')}
          <Input type="number" min="0" step="0.01" value={form.minAmount} onChange={(e) => setForm((c) => ({ ...c, minAmount: e.target.value }))} className="mt-1 h-10 bg-white" /></label>
        <label className="text-xs text-[#52615d]">{t('ruleAmountTo')}
          <Input type="number" min="0" step="0.01" value={form.maxAmount} onChange={(e) => setForm((c) => ({ ...c, maxAmount: e.target.value }))} className="mt-1 h-10 bg-white" /></label>
        <div className="flex items-end gap-2">
          <label className="inline-flex items-center gap-2 pb-2.5 text-xs text-[#52615d]">
            <input type="checkbox" checked={form.isRegex} onChange={(e) => setForm((c) => ({ ...c, isRegex: e.target.checked }))} className="size-4 shrink-0 cursor-pointer accent-[var(--money-primary)]" />
            {t('ruleIsRegex')}</label>
          <Button type="submit" disabled={busy} className="h-10 bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{busy ? t('savingEllipsis') : editId ? t('save') : t('add')}</Button>
          {editId !== null && <Button type="button" variant="outline" className="h-10" disabled={busy} onClick={() => { setEditId(null); setForm(vuoto); setErrore(''); }}>{t('cancel')}</Button>}
        </div>
      </form>
      {errore && <p role="alert" className="text-xs text-[#bd5e46]">{errore}</p>}
    </CardContent>
    {/* Le proposte non si applicano da sole: la spunta e' il passaggio in cui
        si decide, e chi non spunta niente non scrive niente. */}
    {proposte && <Dialog open onOpenChange={(aperto) => { if (!aperto) setProposte(null); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('ruleProposals')}</DialogTitle>
          <DialogDescription>{t('categoryRulesHint')}</DialogDescription>
        </DialogHeader>
        {proposte.proposte.length === 0
          ? <p className="text-sm text-[#71807c]">{t('ruleNoneFound')}</p>
          : <div className="max-h-80 space-y-1 overflow-y-auto">
            {proposte.proposte.map((proposta) => <label key={proposta.pattern}
              className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-[#f7f8f5]">
              <input type="checkbox" className="mt-0.5 size-4 shrink-0 cursor-pointer accent-[var(--money-primary)]"
                checked={spuntate.has(proposta.pattern)}
                onChange={(e) => setSpuntate((vecchie) => {
                  const nuove = new Set(vecchie);
                  e.target.checked ? nuove.add(proposta.pattern) : nuove.delete(proposta.pattern);
                  return nuove;
                })} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{proposta.pattern}</span>
                <span className="block text-xs text-[#7b8784]">{dettaglio(proposta)}</span>
              </span>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${proposta.fiducia === 'sicura' ? 'bg-[#e5f3ed] text-[#2d7b65]' : 'bg-[#f4f5f1] text-[#61706c]'}`}>
                {t(proposta.fiducia === 'sicura' ? 'ruleConfidenceSure' : 'ruleConfidenceUnsure')}</span>
            </label>)}
          </div>}
        {/* Di sola lettura: sono le descrizioni che nessuna categoria tiene
            insieme, e riscriverle cambierebbe budget e report gia' chiusi. */}
        {proposte.incoerenti.length > 0 && <details className="border-t border-black/5 pt-3">
          <summary className="cursor-pointer text-xs font-medium text-[#52615d]">{t('ruleInconsistent')}</summary>
          <ul className="mt-2 space-y-1">
            {proposte.incoerenti.map((riga) => <li key={riga.pattern} className="text-xs text-[#7b8784]">
              <span className="text-[#28312f]">{riga.pattern}</span>
              {` · ${t('ruleOccurrences', { count: riga.occorrenze })} · `}
              {riga.categorie.map((c) => `${c.category} ${c.count}`).join(', ')}</li>)}
          </ul>
        </details>}
        <DialogFooter className="mx-0 mb-0 border-0 bg-transparent p-0">
          <Button type="button" variant="outline" disabled={busy} onClick={() => setProposte(null)}>{t('cancel')}</Button>
          <Button type="button" disabled={busy || spuntate.size === 0} onClick={accetta}
            className="bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">
            {t('ruleAcceptChecked', { count: spuntate.size })}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>}
  </Card>;
}

function RecurringTransactionsView({ accounts, data, categoriesByType, onCreate, onDelete, onGenerate }: { accounts: Account[]; data: RecurringTransactionData[]; categoriesByType: Record<string, string[]>; onCreate: (payload: Record<string, string | number | null>) => Promise<void>; onDelete: (id: number) => Promise<void>; onGenerate: (until: string) => Promise<number> }) {
  const { t, locale } = useI18n();
  const [form, setForm] = useState({ description: '', amount: '', category: '', recurrence: 'FREQ=MONTHLY;BYMONTHDAY=1', startDate: new Date().toISOString().slice(0, 10), endDate: '', type: 'Expenses' });
  const [busy, setBusy] = useState(false);
  const [generateUpTo, setGenerateUpTo] = useState(new Date().toISOString().slice(0, 10));
  const [generating, setGenerating] = useState(false);
  const [generatedInfo, setGeneratedInfo] = useState<number | null>(null);

  async function generate() {
    setGenerating(true);
    setGeneratedInfo(null);
    try {
      setGeneratedInfo(await onGenerate(generateUpTo));
    } finally {
      setGenerating(false);
    }
  }

  async function submit(event: SyntheticEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.amount || (form.type !== 'Transfers' && !form.category)) return;
    const values = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await onCreate(recurringPayload(form, values));
      setForm((current) => ({ ...current, description: '', amount: '' }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-[17px]"><Calendar className="size-5 text-[#397867]" />{t('newRecurrence')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{t('newRecurrenceDesc')}</p>
        </CardHeader>
        <CardContent>
          <form className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr_1fr_auto]" onSubmit={submit}>
            <label>{t('account')}<select required name="accountName" className="h-10 w-full rounded border"><option value="">{t('selectAccount')}</option>{accounts.filter(a => a.isActive !== false).map(a => <option key={a.id}>{a.name}</option>)}</select></label>
            {form.type === 'Transfers' && <label>{t('fieldDestinationAccount')}<select required name="destinationName" className="h-10 w-full rounded border"><option value="">{t('selectAccount')}</option>{accounts.filter(a => a.isActive !== false).map(a => <option key={a.id}>{a.name}</option>)}</select></label>}
            <Input placeholder={t('descriptionPlaceholder')} value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} className="h-10 bg-white" />
            <select aria-label={t('categoryPlaceholder')} required={form.type !== 'Transfers'} disabled={form.type === 'Transfers'} value={form.type === 'Transfers' ? '' : form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))} className="h-10 rounded-lg border border-input bg-white px-2 text-sm disabled:bg-[#f4f5f1] disabled:text-[#a3adaa]">
              <option value="">{form.type === 'Transfers' ? t('categoryNotApplicable') : t('categoryPlaceholder')}</option>
              {form.type !== 'Transfers' && (categoriesByType[form.type] ?? []).map((categoria) => <option key={categoria} value={categoria}>{categoria}</option>)}
            </select>
            <Input type="number" min="0.01" step="0.01" placeholder={t('amountPlaceholder')} value={form.amount} onChange={(event) => setForm((current) => ({ ...current, amount: event.target.value }))} className="h-10 bg-white" />
            {/* Una categoria di spesa non vale per un'entrata: cambiando tipo si riparte. */}
            <select aria-label={t('type')} value={form.type} onChange={(event) => setForm((current) => ({ ...current, type: event.target.value, category: (categoriesByType[event.target.value] ?? []).includes(current.category) ? current.category : '' }))} className="h-10 rounded-lg border border-input bg-white px-2 text-sm">
              <option value="Expenses">{t('typeExpense')}</option>
              <option value="Income">{t('typeIncome')}</option>
              <option value="Transfers">{t('typeTransfer')}</option>
            </select>
            <Button type="submit" disabled={busy} className="h-10 bg-[var(--money-primary)] text-white hover:bg-[var(--money-primary-hover)]">{busy ? t('savingEllipsis') : t('add')}</Button>
            <div className="sm:col-span-3 grid grid-cols-3 gap-3">
              <label htmlFor="recurring-frequency" className="text-xs text-[#52615d]">{t('frequency')}<select id="recurring-frequency" value={form.recurrence} onChange={(event) => setForm((current) => ({ ...current, recurrence: event.target.value }))} className="mt-1 h-10 w-full rounded-lg border border-input bg-white px-2 text-sm">
                <option value="FREQ=DAILY">{t('everyDay')}</option>
                <option value="FREQ=WEEKLY">{t('everyWeek')}</option>
                <option value="FREQ=MONTHLY;BYMONTHDAY=1">{t('everyMonthDay1')}</option>
                <option value="FREQ=MONTHLY;BYMONTHDAY=15">{t('everyMonthDay15')}</option>
                <option value="FREQ=YEARLY">{t('everyYear')}</option>
              </select></label>
              <label htmlFor="recurring-start-date" className="text-xs text-[#52615d]">{t('startDateField')}<Input id="recurring-start-date" type="date" value={form.startDate} onChange={(event) => setForm((current) => ({ ...current, startDate: event.target.value }))} className="mt-1 h-10 bg-white" /></label>
              <label htmlFor="recurring-end-date" className="text-xs text-[#52615d]">{t('endDateOptional')}<Input id="recurring-end-date" type="date" value={form.endDate} onChange={(event) => setForm((current) => ({ ...current, endDate: event.target.value }))} className="mt-1 h-10 bg-white" /></label>
            </div>
            <p className="sm:col-span-2 text-[11px] text-[#71807c]">{t('currentRule')} <code className="rounded bg-[#f0f2ee] px-1.5 py-0.5">{form.recurrence}</code></p>
          </form>
        </CardContent>
      </Card>
      <Card className="border-black/6 bg-white shadow-sm">
        <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
          <CardTitle className="text-[17px]">{t('activeRecurrences')}</CardTitle>
          <p className="mt-1 text-xs text-[#7b8784]">{data.length === 1 ? t('ruleRegisteredCount', { count: data.length }) : t('rulesRegisteredCount', { count: data.length })}</p>
          </div>
          {data.length > 0 && <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs font-medium text-[#52615d]">{t('generateUpTo')}<Input type="date" value={generateUpTo} onChange={(event) => setGenerateUpTo(event.target.value)} className="mt-1 h-9 w-[160px] bg-white" /></label>
            <Button type="button" variant="outline" disabled={generating} onClick={() => void generate()} className="mt-5"><RefreshCw className={`size-4 ${generating ? 'animate-spin' : ''}`} />{generating ? t('generatingEllipsis') : t('generateOccurrences')}</Button>
          </div>}
        </CardHeader>
        {generatedInfo !== null && <p className="px-(--card-spacing) text-xs text-[#397867]">{t('occurrencesGenerated', { count: generatedInfo })}</p>}
        <CardContent className="divide-y divide-black/5">
          {data.length === 0 ? <p className="py-6 text-center text-sm text-[#71807c]">{t('noRecurrences')}</p> :
            data.map((rule) => (
              <div key={rule.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{rule.description}</p>
                  <p className="mt-0.5 text-xs text-[#87918e]">{rule.category} · {rule.recurrence_rule ?? t('withoutRule')}{rule.next_occurrence ? ` · ${t('nextOccurrence', { date: rule.next_occurrence })}` : ''}</p>
                </div>
                <div className="flex items-center gap-2">
                  <span className="rounded-full bg-[#edf0ed] px-2.5 py-1 text-xs font-medium">{rule.transactionType}</span>
                  <span className="text-sm font-semibold tabular-nums">{rule.amount.toLocaleString(locale, { style: 'currency', currency: 'EUR' })}</span>
                  <Button type="button" size="icon" variant="ghost" aria-label={t('deleteRecurrence')} onClick={() => void onDelete(rule.id)} className="text-[#bd5e46]"><Trash2 className="size-4" /></Button>
                </div>
              </div>
            ))}
        </CardContent>
      </Card>
    </div>
  );
}
