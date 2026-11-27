import { Component, type ReactNode } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Empty } from './PageParts';
import { t } from './lib/i18n';

/**
 * Keeps one page's render error inside that page: the sidebar and header stay usable and the page
 * says what failed with a retry, instead of the whole app going blank. Key it by view so
 * navigating to another page starts clean (docs/web-design-system.md#page-errors).
 */
export class ViewErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <Empty
      className="viewError"
      icon={<TriangleAlert aria-hidden="true" />}
      title={t('shell.viewError')}
      text={t('shell.viewErrorText')}
      action={<>
        <button type="button" className="secondary" onClick={() => this.setState({ failed: false })}>{t('common.tryAgain')}</button>
        <button type="button" className="secondary" onClick={() => window.location.reload()}>{t('shell.reloadApp')}</button>
      </>}
    />;
  }
}
