import { t } from '../lib/i18n.ts';
import type { StripStop } from './navModel.ts';

export function stopName(stop: StripStop): string {
  return stop.kind === 'campus' ? t('hqNav.strip.campus') : stop.kind === 'building' ? stop.name : stop.label;
}
