// "AI answers are placeholders" (docs/model-gateway.md#placeholder-answers): shown where answers appear
// while this workspace's model is the built-in placeholder. Driven only by GET /api/v1/model-status; when the
// status cannot be read nothing is shown, so an unknown state never claims the answers are fake.
import { useEffect, useState } from 'react';
import { TriangleAlert } from 'lucide-react';
import { getModelStatus } from './lib/api';
import { openHelp } from './lib/help';
import { t } from './lib/i18n';
import { placeholderBannerKey, type ModelStatus } from './lib/modelStatus';

export function ModelPlaceholderBanner() {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  useEffect(() => {
    let live = true;
    void getModelStatus().then((value) => { if (live) setStatus(value); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const key = placeholderBannerKey(status);
  if (!key) return null;
  return <div className="notice modelPlaceholderNotice" role="status" data-testid="model-placeholder-banner">
    <TriangleAlert aria-hidden="true" />
    <div>
      <p><strong>{t(key)}</strong> {t('model.placeholder.fix')}</p>
      <p className="modelPlaceholderActions">
        <button type="button" className="secondary" onClick={() => { window.location.hash = 'settings'; }}>{t('model.placeholder.openSettings')}</button>
        <button type="button" className="secondary" onClick={() => openHelp('settings')}>{t('model.placeholder.help')}</button>
      </p>
    </div>
  </div>;
}
