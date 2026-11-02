// The Sales overview figures (docs/business.md#whatsapp-leads): counted from the one lead list,
// Business leads with the WhatsApp source, plus the enquiries still waiting to join it.
import type { SalesOverview } from './salesApi';

export type SalesFigureKey = 'leads' | 'qualified' | 'quoted' | 'won' | 'lost' | 'waiting';

export interface SalesFigure {
  key: SalesFigureKey;
  label: string;
  value: number;
}

const LABELS: Record<'en' | 'ar', Record<SalesFigureKey, string>> = {
  en: { leads: 'WhatsApp leads', qualified: 'Qualified', quoted: 'Offered', won: 'Won', lost: 'Lost', waiting: 'Waiting to join the list' },
  ar: { leads: 'عملاء واتساب', qualified: 'مؤهلون', quoted: 'عروض مقدمة', won: 'مكتمل', lost: 'مفقود', waiting: 'بانتظار الإضافة إلى القائمة' },
};

const ORDER: SalesFigureKey[] = ['leads', 'qualified', 'quoted', 'won', 'lost', 'waiting'];

/** The figures in order. With no overview yet (loading or failed) there are none: the page shows
 * its loading or error state instead of zeros it does not know. */
export function salesFigures(overview: SalesOverview | null, locale: 'en' | 'ar'): SalesFigure[] {
  if (!overview) return [];
  return ORDER.map((key) => ({ key, label: LABELS[locale][key], value: Number(overview[key] ?? 0) }));
}

export const FIGURES_NOTE: Record<'en' | 'ar', string> = {
  en: 'Counted from Business, Leads (WhatsApp source). Waiting: enquiries not in the lead list yet; add them there.',
  ar: 'محسوبة من الأعمال، العملاء المحتملون (مصدر واتساب). بانتظار الإضافة: استفسارات لم تُضف إلى القائمة بعد، أضفها هناك.',
};
