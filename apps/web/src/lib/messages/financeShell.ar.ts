// غلاف المالية (docs/finance.md#finance-shell). Arabic twin of financeShell.en.ts.
import type { Message } from './types.ts';
import type { financeShellEn } from './financeShell.en.ts';

export const financeShellAr = {
  'finance.tab.reports': 'التقارير',
  'fshell.group.capture': 'جمع البيانات',
  'fshell.group.invest': 'الاستثمارات',
  'fshell.group.trading': 'التداول',
  'fshell.nav.books': 'مصروفات الأعمال والأرباح والخسائر',
  'fshell.nav.people': 'أموالي الشخصية',
  'fshell.badge.new': 'جديد',
  'fshell.scope': 'النطاق',
  'fshell.scope.personal': 'شخصي',
  'fshell.scope.business': 'الأعمال',
  'fshell.scope.all': 'كل المالية',
  'fshell.period': 'الفترة',
  'fshell.delta.up': 'ارتفاع',
  'fshell.delta.down': 'انخفاض',
  'fshell.delta.flat': 'دون تغيير',
  'fshell.delta.pct.up': 'ارتفاع {percent}%',
  'fshell.delta.pct.down': 'انخفاض {percent}%',
  'fshell.delta.pct.flat': 'دون تغيير',
} satisfies Record<keyof typeof financeShellEn, Message>;
