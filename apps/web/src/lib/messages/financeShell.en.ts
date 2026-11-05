// The Finance shell (docs/finance.md#finance-shell): section groups, scope, period and delta chips.
// Spread into en.ts; every key has an Arabic twin in financeShell.ar.ts with the same {placeholders}.
import type { Message } from './types.ts';

export const financeShellEn = {
  'finance.tab.reports': 'Reports',
  'fshell.group.capture': 'Data capture',
  'fshell.group.invest': 'Investments',
  'fshell.group.trading': 'Trading',
  'fshell.nav.books': 'Business Expenses and P&L',
  'fshell.badge.new': 'NEW',
  'fshell.scope': 'Scope',
  'fshell.scope.personal': 'Personal',
  'fshell.scope.business': 'Business',
  'fshell.scope.all': 'All finance',
  'fshell.period': 'Period',
  'fshell.delta.up': 'Up',
  'fshell.delta.down': 'Down',
  'fshell.delta.flat': 'Flat',
  'fshell.delta.pct.up': 'Up {percent}%',
  'fshell.delta.pct.down': 'Down {percent}%',
  'fshell.delta.pct.flat': 'Flat',
} satisfies Record<string, Message>;
