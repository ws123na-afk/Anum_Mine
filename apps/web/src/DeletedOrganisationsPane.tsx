// Deleted organisations in the platform console (docs/organisation-lifecycle.md, Receipt): the
// receipts the operator's onboarded organisations left after deletion. Read only: dates, who asked,
// rows and files removed per table, and what happened to the sign-in accounts. No content.
import { useCallback, useEffect, useState } from 'react';
import { Archive } from 'lucide-react';
import { describeError } from './lib/errors';
import { formatDateTime, t } from './lib/i18n';
import { identityText, type DeletedOrganisation } from './lib/lifecycle';
import { getDeletedOrganisations } from './lib/lifecycleApi';
import { PaneProblem } from './PageParts';

type Load = { state: 'loading' } | { state: 'ready'; rows: DeletedOrganisation[] } | { state: 'error'; message: string };

const when = (value: string) => formatDateTime(value, { dateStyle: 'medium', timeStyle: 'short' });

export function DeletedOrganisationsPane() {
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const refresh = useCallback(async () => {
    setLoad({ state: 'loading' });
    try { setLoad({ state: 'ready', rows: await getDeletedOrganisations() }); }
    catch (error) { setLoad({ state: 'error', message: describeError(error) }); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  return <section className="pane deletedOrgsPane" aria-labelledby="deleted-orgs-title">
    <div className="paneHead"><h2 id="deleted-orgs-title">{t('lifecycle.deleted.title')}</h2></div>
    <p className="muted">{t('lifecycle.deleted.lead')}</p>
    {load.state === 'loading' && <p className="muted" role="status">{t('lifecycle.deleted.loading')}</p>}
    {load.state === 'error' && <PaneProblem text={t('lifecycle.deleted.loadError', { message: load.message })} onRetry={() => void refresh()} />}
    {load.state === 'ready' && !load.rows.length && <div className="paneEmpty"><Archive aria-hidden="true" /><h3>{t('lifecycle.deleted.empty')}</h3></div>}
    {load.state === 'ready' && load.rows.length > 0 && <div className="dataTableWrap"><table className="dataTable deletedOrgsTable">
      <thead><tr>
        <th scope="col">{t('lifecycle.deleted.col.organisation')}</th>
        <th scope="col" className="optional">{t('lifecycle.deleted.col.requested')}</th>
        <th scope="col">{t('lifecycle.deleted.col.deleted')}</th>
        <th scope="col">{t('lifecycle.deleted.col.removed')}</th>
        <th scope="col">{t('lifecycle.deleted.col.accounts')}</th>
      </tr></thead>
      <tbody>{load.rows.map((row) => <tr key={row.tenantId}>
        <th scope="row"><strong dir="auto">{row.name}</strong><small dir="ltr">{row.tenantId}</small></th>
        <td className="optional">{t('lifecycle.deleted.requestedBy', { when: when(row.requestedAt), who: row.requestedBy })}</td>
        <td className="nowrap">{row.deletedAt ? when(row.deletedAt) : <span className="statusBadge warning">{t('lifecycle.deleted.deleting')}</span>}</td>
        <td>
          <span>{t('lifecycle.deleted.rowsFiles', { rows: row.rows, files: row.files })}</span>
          {row.counts.length > 0
            ? <details className="deletedOrgCounts"><summary>{t('lifecycle.deleted.tables', { count: row.counts.length })}</summary>
              <ul>{row.counts.map((entry) => <li key={entry.table}><code>{entry.table}</code> <span className="num">{entry.rows}</span></li>)}</ul>
            </details>
            : <small className="muted">{t('lifecycle.deleted.noTables')}</small>}
        </td>
        <td>{identityText(row.identity)}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
