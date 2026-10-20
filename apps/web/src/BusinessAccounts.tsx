// Business, Accounts (docs/business-portal.md#giving-access): the client accounts, searched and paged, and for
// one account its facts, its contacts and the customer portal block (who holds a link, a new link shown once,
// turning one off). The portal block is the same one a lead's drawer shows; here it has a page of its own.
import { useCallback, useEffect, useId, useState } from 'react';
import { Search } from 'lucide-react';
import type { BusinessAccountDetail, BusinessSetup } from '@anum/contracts';
import { t } from './lib/i18n';
import { accountPlace } from './lib/accounts';
import * as b from './lib/businessApi';
import { Drawer } from './Dialog';
import { Badge, Empty, Note, Pager, failure } from './BusinessParts';
import { ListState, usePaged } from './BusinessDeals';
import { PortalAccessBlock } from './BusinessPortalAdmin';
import { PAGE_SIZE } from './lib/business';

export function AccountsTab({ setup, openId, onOpen }: { setup: BusinessSetup; openId: string | null; onOpen: (id: string | null) => void }) {
  const [typed, setTyped] = useState(''), [search, setSearch] = useState(''), [tick, setTick] = useState(0);
  const list = usePaged((offset) => b.listAccounts({ q: search || undefined, limit: PAGE_SIZE, offset }), [search, tick]);
  const searchId = useId();
  const filtered = search !== '';
  const empty = list.load.kind === 'ready' && list.items.length === 0;
  return <section className="bzSection" aria-label={t('biz.tab.accounts')}>
    <p className="muted bzHint">{t('acc.help')}</p>
    <div className="bzToolbar">
      <form className="bzSearch" role="search" onSubmit={(event) => { event.preventDefault(); setSearch(typed.trim()); setTick((value) => value + 1); }}>
        <label className="visuallyHidden" htmlFor={searchId}>{t('acc.search')}</label>
        <input id={searchId} type="search" value={typed} placeholder={t('acc.search')} dir="auto" onChange={(event) => { setTyped(event.target.value); if (!event.target.value) setSearch(''); }} />
        <button type="submit" className="secondary"><Search aria-hidden="true" />{t('biz.find')}</button>
      </form>
    </div>
    <ListState load={list.load} empty={list.items.length === 0} filtered={filtered} onRetry={() => void list.first()} onClear={() => { setTyped(''); setSearch(''); }} />
    {empty && !filtered && <Empty title={t('acc.emptyTitle')} text={setup.permissions.write ? t('acc.emptyText') : t('acc.emptyMember')} />}
    {list.load.kind === 'ready' && list.items.length > 0 && <>
      <p className="bzCount" aria-live="polite">{t('acc.count', { count: list.total })}</p>
      <div className="pane dataTableWrap"><table className="dataTable bzTable">
        <caption className="visuallyHidden">{t('acc.title')}</caption>
        <thead><tr><th scope="col">{t('acc.col.name')}</th><th scope="col" className="bzHideMd">{t('acc.col.place')}</th><th scope="col" className="num">{t('acc.col.contacts')}</th><th scope="col" className="num bzHideMd">{t('acc.col.leads')}</th></tr></thead>
        <tbody>{list.items.map((item) => <tr key={item.id} className={item.id === openId ? 'selected' : undefined}>
          <th scope="row"><button type="button" className="linkButton" onClick={() => onOpen(item.id)}><span dir="auto">{item.name}</span></button>
            {item.do_not_contact && <Badge tone="dangerTone">{t('acc.dnc')}</Badge>}<small dir="auto">{item.industry}</small></th>
          <td className="bzHideMd" dir="auto">{accountPlace(item)}</td>
          <td className="num">{item.contacts}</td>
          <td className="num bzHideMd">{item.open_leads}</td>
        </tr>)}</tbody></table></div>
      <Pager shown={list.items.length} total={list.total} busy={list.more.busy} error={list.more.error} onMore={() => void list.loadMore()} />
    </>}
    {openId && <AccountDrawer setup={setup} id={openId} onClose={() => onOpen(null)} />}
  </section>;
}

function AccountDrawer({ setup, id, onClose }: { setup: BusinessSetup; id: string; onClose: () => void }) {
  const [account, setAccount] = useState<BusinessAccountDetail | null>(null), [problem, setProblem] = useState<string | null>(null);
  const reload = useCallback(async () => {
    setProblem(null);
    try { setAccount(await b.getAccount(id)); } catch (error) { setProblem(failure(error, t('acc.load'))); }
  }, [id]);
  useEffect(() => { setAccount(null); void reload(); }, [reload]);
  if (!account) {
    return <Drawer title={t('acc.detail')} onClose={onClose}>
      {problem ? <><Note error>{problem}</Note><div className="bzInline"><button type="button" className="secondary" onClick={() => void reload()}>{t('common.tryAgain')}</button></div></> : <Note>{t('acc.load')}</Note>}
    </Drawer>;
  }
  return <Drawer title={account.name} onClose={onClose}>
    <div className="bzDrawer">
      <dl className="bzFacts">
        <div><dt>{t('acc.facts.website')}</dt><dd>{account.website ? <a href={account.website} target="_blank" rel="noopener noreferrer" dir="ltr">{account.domain ?? account.website}</a> : t('biz.unknown')}</dd></div>
        <div><dt>{t('acc.facts.industry')}</dt><dd dir="auto">{account.industry || t('biz.unknown')}</dd></div>
        <div><dt>{t('acc.facts.place')}</dt><dd dir="auto">{accountPlace(account) || t('biz.unknown')}</dd></div>
        {account.notes && <div><dt>{t('acc.facts.notes')}</dt><dd dir="auto" className="invAddress">{account.notes}</dd></div>}
      </dl>
      <section className="bzBlock" aria-labelledby={`ac-${account.id}`}>
        <h3 id={`ac-${account.id}`}>{t('acc.contacts')}</h3>
        {account.contacts.length === 0 ? <p className="muted">{t('acc.noContacts')}</p>
          : <ul className="bzTrail">{account.contacts.map((contact) => <li key={contact.id}>
            <div className="bzInline"><strong dir="auto">{contact.name}</strong>{contact.role_title && <span className="muted" dir="auto">{contact.role_title}</span>}
              {contact.do_not_contact && <Badge tone="dangerTone">{t('acc.dnc')}</Badge>}</div>
            <small dir="ltr">{[contact.email, contact.phone].filter(Boolean).join(' / ') || t('acc.noChannel')}</small></li>)}</ul>}
      </section>
      <PortalAccessBlock setup={setup} accountId={account.id} />
    </div>
  </Drawer>;
}
