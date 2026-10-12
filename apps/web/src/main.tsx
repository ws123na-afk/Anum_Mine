import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { CircleDot, Search } from 'lucide-react';
import type { AgentRun, Approval, Favorite, Task } from '@anum/contracts';
import { hashTarget } from './lib/library';
import * as api from './lib/api';
import * as auth from './lib/auth';
import './styles.css';
import { ApprovalsView } from './ApprovalsView';
import { ApprovalPolicyView, BudgetExceededNotice, BudgetsView, MembersView } from './AdminViews';
import { getCurrentWorkspace } from './lib/adminApi';
import { PendingScreen, SignInScreen } from './SignInView';
import { TasksView } from './TasksView';
import type { MemoryTarget } from './SourcesUsed';
import { isActiveOwner } from './lib/capabilities';
import { CommandCenter } from './CommandCenter';
import { CommandPalette, NotificationsMenu, Sidebar, TopBar, UserMenu, usePaletteShortcut } from './Shell';
import { OWN_HEADER, displayName, navParent, viewFromHash, viewLabel, visibleViews, type PaletteItem, type View } from './lib/shell';
import type { ChatTarget } from './ChatCardView';
import { ModelGatewaySettings } from './ModelSettings';
import { ChatMemorySetting } from './ChatMemorySetting';
import { LanguageSettings } from './LanguageSettings';
import { NotificationSettings } from './NotificationSettings';
import { formatDateTime, initLocale, t, type MessageKey } from './lib/i18n';
import { statusLabel } from './lib/labels';
import { useLocale } from './useLocale';
import { AutomationAlerts, desktopNotify } from './AutomationAlerts';
import type { AutomationFocus } from './AutomationWorkbench';
import { automationAlert, type AutomationAlert } from './lib/automation';
import { CONNECT_STORAGE_KEY, readConnectReturn } from './lib/email';
import { useTeamNotifications } from './NotificationBell';
import { getBranding, logoObjectUrl } from './lib/teamApi';
// Views off the default path load on first visit, so the voice and workbench code stays out of the entry chunk.
const MeetingsView=lazy(()=>import('./MeetingsView').then(m=>({default:m.MeetingsView}))),TyreView=lazy(()=>import('./TyreView').then(m=>({default:m.TyreView}))),TeamView=lazy(()=>import('./TeamView').then(m=>({default:m.TeamView}))),AssistantsView=lazy(()=>import('./AssistantsView').then(m=>({default:m.AssistantsView}))),OrganisationsView=lazy(()=>import('./OrganisationsView').then(m=>({default:m.OrganisationsView}))),BrandingSettings=lazy(()=>import('./OrganisationsView').then(m=>({default:m.BrandingSettings}))),VoiceView=lazy(()=>import('./VoiceView').then(m=>({default:m.VoiceView}))),CallView=lazy(()=>import('./CallView').then(m=>({default:m.CallView}))),GovernanceView=lazy(()=>import('./EnterpriseViews').then(m=>({default:m.GovernanceView}))),EnterpriseView=lazy(()=>import('./EnterpriseViews').then(m=>({default:m.EnterpriseView}))),AutomationWorkbench=lazy(()=>import('./AutomationWorkbench').then(m=>({default:m.AutomationWorkbench}))),MemoryView=lazy(()=>import('./MemoryView').then(m=>({default:m.MemoryView}))),FavoritesView=lazy(()=>import('./Favorites').then(m=>({default:m.FavoritesView}))),KnowledgeView=lazy(()=>import('./KnowledgeView').then(m=>({default:m.KnowledgeView}))),ReportsView=lazy(()=>import('./ReportsView').then(m=>({default:m.ReportsView}))),SkillsView=lazy(()=>import('./SkillsView').then(m=>({default:m.SkillsView}))),AgentsView=lazy(()=>import('./AgentsView').then(m=>({default:m.AgentsView}))),IntegrationsView=lazy(()=>import('./IntegrationsView').then(m=>({default:m.IntegrationsView}))),ChatView=lazy(()=>import('./ChatView').then(m=>({default:m.ChatView}))),DeveloperView=lazy(()=>import('./DeveloperView').then(m=>({default:m.DeveloperView}))),MarketingView=lazy(()=>import('./MarketingView').then(m=>({default:m.MarketingView}))),FilesView=lazy(()=>import('./FilesView').then(m=>({default:m.FilesView}))),CommerceView=lazy(()=>import('./CommerceView').then(m=>({default:m.CommerceView}))),PrivacyView=lazy(()=>import('./PrivacyView').then(m=>({default:m.PrivacyView}))),LearningView=lazy(()=>import('./LearningView').then(m=>({default:m.LearningView}))),InboxView=lazy(()=>import('./InboxView').then(m=>({default:m.InboxView}))),WhatsAppView=lazy(()=>import('./WhatsAppView').then(m=>({default:m.WhatsAppView}))),ActivityView=lazy(()=>import('./AutonomyViews').then(m=>({default:m.ActivityView}))),AutonomySettingsView=lazy(()=>import('./AutonomyViews').then(m=>({default:m.AutonomySettingsView}))),ActionNotices=lazy(()=>import('./AutonomyViews').then(m=>({default:m.ActionNotices}))),AuditLogView=lazy(()=>import('./AuditLogView').then(m=>({default:m.AuditLogView}))),WorkspaceAdmin=lazy(()=>import('./WorkspaceAdmin').then(m=>({default:m.WorkspaceAdmin})));
import { budgetMessage, describeError, isBudgetExceeded, isPermissionDenied } from './lib/errors';
import { noticeFromEvent, type ActionNotice } from './lib/autonomy';

// The shell's views, sidebar groups and hash routing live in lib/shell.ts; the chrome in Shell.tsx.
const err=(e:unknown)=>e instanceof Error?e.message:t('common.operationFailed');
type SettingsTarget='settings'|'language'|'notifications'|'privacy';

// Keyed by workspace in AuthGate: switching workspace remounts the app, so no state from the
// previous workspace survives and every request after the switch names the new one.
function App({onSwitchWorkspace,onSwitchOrganisation}:{onSwitchWorkspace:(workspaceId:string)=>Promise<void>;onSwitchOrganisation:(tenantId:string,workspaceId:string)=>Promise<void>}){
 // The caller's membership here: the owner-only entries and the role in the user menu.
 const [membership,setMembership]=useState<{role:string;active:boolean}|null>(null),[membershipLoaded,setMembershipLoaded]=useState(false);
 useEffect(()=>{void api.getCurrentMembership().then(m=>setMembership(m)).catch(()=>undefined).finally(()=>setMembershipLoaded(true))},[]);
 const owner=membershipLoaded?isActiveOwner(membership):null,views=visibleViews(owner,api.platformOperatorHint());
 // Team work: the work item opened from a notification, the last notification signal and a counter that refreshes the team lists on live events.
 const [workFocus,setWorkFocus]=useState<string|null>(null),[bellSignal,setBellSignal]=useState<{recipient:unknown;at:number}|null>(null),[teamLive,setTeamLive]=useState(0);
 const branding=useBranding();
 const [view,setView]=useState<View>(()=>location.pathname.endsWith('/meetings/connected')?'meetings':/[?&]state=mkt\./.test(location.search)?'marketing':viewFromHash(location.hash));
 const [settingsKey,setSettingsKey]=useState(0),[paletteOpen,setPaletteOpen]=useState(false);
 const [budgetStop,setBudgetStop]=useState<string|null>(null);
 // A report or collection opened by link (#reports/<id>, #knowledge/<id>), a favorite or a chat answer card.
 const [reportTarget,setReportTarget]=useState<string|null>(()=>hashTarget(location.hash,'reports')),[collectionTarget,setCollectionTarget]=useState<string|null>(()=>hashTarget(location.hash,'knowledge'));
 // An archived workspace is read-only (docs/identity.md#workspace-lifecycle): say so on every view.
 const [archived,setArchived]=useState(false);
 useEffect(()=>{void getCurrentWorkspace().then(w=>setArchived(w.archivedAt!==null)).catch(()=>undefined)},[]);
 // A conversation opened from the Memory view (a memory remembered from that chat).
 const [chatTarget,setChatTarget]=useState<string|null>(null);
 // Settings' "Show memories from my chats" opens the Memory view filtered to them.
 const [memoryChats,setMemoryChats]=useState(false);
 // "Sources used" on a run opens the memory (highlighted in the Memory view) or the file's detail; search opens an email.
 const [memoryFocus,setMemoryFocus]=useState<MemoryTarget|null>(null),[fileFocus,setFileFocus]=useState<string|null>(null),[emailFocus,setEmailFocus]=useState<string|null>(null),[waFocus,setWaFocus]=useState<string|null>(null);
 const [tasks,setTasks]=useState<Task[]>([]),[task,setTask]=useState<Task|null>(null),[run,setRun]=useState<AgentRun|null>(null),[approvals,setApprovals]=useState<Approval[]>([]);
 const [prompt,setPrompt]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState<string|null>(null),[message,setMessage]=useState<MessageKey>('shell.loadingState');
 const [stream,setStream]=useState<'connecting'|'live'|'offline'>('connecting'),[event,setEvent]=useState<string|null>(null);
 // Automations: a run opened from an alert, a counter that refreshes the page on automation events, and the alerts addressed to this user.
 const [automationFocus,setAutomationFocus]=useState<AutomationFocus|null>(null),[automationTick,setAutomationTick]=useState(0),[alerts,setAlerts]=useState<AutomationAlert[]>([]),desktopAlertsRef=useRef(false);
 // Desktop notifications follow the user's "desktop enabled" preference (Settings, Notifications).
 useEffect(()=>{void api.getNotificationPreferences().then(p=>{desktopAlertsRef.current=p.desktop_enabled}).catch(()=>undefined)},[]);
 // "ANUM acted" notifications (autonomy.action_taken events) with Undo while it is open; at most three, newest first.
 const [notices,setNotices]=useState<ActionNotice[]>([]);
 // Background refreshes (quiet) leave the loading state alone; each refresh fetches only the lists asked for.
 const load=useCallback(async({tasks:t=true,approvals:a=true,quiet=false}:{tasks?:boolean;approvals?:boolean;quiet?:boolean}={})=>{if(!quiet)setLoading(true);try{const [ts,as]=await Promise.all([t?api.getTasks():null,a?api.getApprovals():null]);if(as)setApprovals(as);if(ts){setTasks(ts);setTask(old=>ts.find(x=>x.id===old?.id)??ts[0]??null);setMessage(ts.length?'shell.stateCurrent':'shell.noTasksYet')}setError(null)}catch(e){setError(err(e))}finally{if(!quiet)setLoading(false)}},[]);
 useEffect(()=>{void api.ensureLocalSession().then(()=>load())},[load]);
 // Events arrive in bursts: coalesce them into one trailing refresh (300 ms) of what they touch (task.* and agent_run.* -> tasks, approval.* -> approvals, anything else -> both).
 useEffect(()=>{const c=new AbortController(),want={tasks:false,approvals:false};let timer:ReturnType<typeof setTimeout>|undefined;const off=()=>{if(!c.signal.aborted)setStream('offline')};api.streamEvents(e=>{setStream('live');setEvent(e.type);if(e.type==='notification.created'){setBellSignal({recipient:(e.payload as {recipient?:unknown}).recipient,at:Date.now()});return}if(/^(work_item|assistant)\./.test(e.type)){setTeamLive(n=>n+1);return}if(e.type==='commerce.alert.raised')void import('./lib/commerceNotify').then(m=>m.notifyCommerceAlert(e)).catch(()=>undefined);const notice=noticeFromEvent(e as {type:string;payload?:Record<string,unknown>});if(notice)setNotices(old=>[notice,...old.filter(n=>n.actionId!==notice.actionId)].slice(0,3));if(e.type.startsWith('automation'))setAutomationTick(t=>t+1);const alert=automationAlert(e,api.currentUserId());if(alert){setAlerts(list=>list.some(x=>x.id===alert.id)?list:[...list,alert]);desktopNotify(alert,desktopAlertsRef.current)}const t=e.type.startsWith('task.')||e.type.startsWith('agent_run.'),a=e.type.startsWith('approval.');want.tasks||=t||!a;want.approvals||=a||!t;clearTimeout(timer);timer=setTimeout(()=>{const next={...want,quiet:true};want.tasks=want.approvals=false;void load(next)},300)},c.signal,()=>setStream('live')).then(off,off);return()=>{c.abort();clearTimeout(timer)}},[load]);
 // A 402 from a run is not a failure to retry: show the budget message (it names the reset date) with a way to the budgets.
 async function doIt(fn:()=>Promise<void>){setBusy(true);setError(null);setBudgetStop(null);try{await fn()}catch(e){if(isBudgetExceeded(e)){setBudgetStop(budgetMessage(e));void load()}else setError(err(e))}finally{setBusy(false)}}
 // A 403 on a decision (e.g. the two-person rule refusing self-approval) belongs on the approval card, not the page.
 async function decide(a:Approval,d:'approve'|'reject',reason?:string):Promise<string|null>{let refusal:string|null=null;await doIt(async()=>{try{const r=d==='approve'?await api.approveTask(a,reason):await api.rejectTask(a,reason);setTask(r.task);setRun(r.run)}catch(e){if(!isPermissionDenied(e))throw e;refusal=describeError(e)}await load()});return refusal}
 const go=(next:View)=>{setView(next);history.replaceState(null,'',`#${next}`)};
 // A pick in the sidebar or the palette starts the view fresh (no focus carried over from a previous jump).
 const navigate=(next:View)=>{setChatTarget(null);setReportTarget(null);setCollectionTarget(null);setMemoryChats(false);setMemoryFocus(null);setFileFocus(null);setEmailFocus(null);setWaFocus(null);go(next)};
 // The caller's team notifications (docs/team-work.md#notifications), listed in the top bar's notifications menu.
 const teamNotifications=useTeamNotifications(bellSignal,id=>{setWorkFocus(id);navigate('team')});
 const openSettings=(target:SettingsTarget)=>{history.replaceState(null,'',`#${target}`);setSettingsKey(k=>k+1);setView('settings')};
 const openReport=(id:string)=>{setReportTarget(id);setView('reports');history.replaceState(null,'',`#reports/${encodeURIComponent(id)}`)};
 // Opening a favorite goes to its view, focused on the item where the view supports it (docs/knowledge-base.md#favorites).
 const openFavorite=(f:Favorite)=>{switch(f.item_type){case 'report':openReport(f.item_id);break;case 'collection':setCollectionTarget(f.item_id);setView('knowledge');history.replaceState(null,'',`#knowledge/${encodeURIComponent(f.item_id)}`);break;case 'conversation':setChatTarget(f.item_id);go('assistant');break;case 'file':setFileFocus(f.item_id);go('files');break;case 'memory':setMemoryChats(false);setMemoryFocus({id:f.item_id,title:f.label});go('memory');break;case 'task':go('tasks');break;case 'automation':go('automation');break;case 'agent':go('agents');break}};
 const openRun=(a:AutomationAlert)=>{setAlerts(list=>list.filter(x=>x.id!==a.id));setAutomationFocus({workflowId:a.workflowId,runId:a.runId});go('automation')};
 // A task an automation step created: load it (it may be newer than the list) and show it under Tasks.
 // Links on chat cards (docs/conversations.md#tools-in-chat) open the item in its own view.
 const openChatTarget=(target:ChatTarget)=>{switch(target.kind){case 'email':setEmailFocus(target.id);go('inbox');break;case 'inbox':setEmailFocus(null);go('inbox');break;case 'whatsapp':setWaFocus(target.id);go('whatsapp');break;case 'whatsappView':setWaFocus(null);go('whatsapp');break;case 'file':setFileFocus(target.id);go('files');break;case 'files':setFileFocus(null);go('files');break;case 'memory':setMemoryChats(false);setMemoryFocus({id:target.id,title:target.title});go('memory');break;case 'task':openTask(target.id);break;case 'approvals':go('approvals');break}};
 const openTask=(id:string)=>void doIt(async()=>{const t=await api.getTask(id);setTasks(ts=>ts.some(x=>x.id===t.id)?ts:[t,...ts]);setTask(t);setRun(null);go('tasks')});
 const openFile=(id:string)=>{setFileFocus(id);go('files')};
 const openPalette=useCallback(()=>setPaletteOpen(true),[]);
 usePaletteShortcut(openPalette);
 const pick=(item:PaletteItem)=>{setPaletteOpen(false);if(item.kind==='view')navigate(item.id as View);else if(item.kind==='task')openTask(item.id);else if(item.kind==='file')openFile(item.id);else{setEmailFocus(item.id);go('inbox')}};
 const pending=approvals.filter(x=>x.status==='pending'),scope=api.currentScope();
 const userName=displayName(auth.currentClaims());
 const label=viewLabel(navParent(view));
 return <div className="shell"><a className="skipLink" href="#main" onClick={e=>{e.preventDefault();document.getElementById('main')?.focus()}}>{t('shell.skipToContent')}</a>
 <Sidebar view={navParent(view)} views={views} pendingApprovals={pending.length} stream={stream} lastEvent={event} workspaceId={scope.workspaceId} onGo={navigate} onSwitchWorkspace={onSwitchWorkspace} branding={branding}/>
 <main className={`workspace view-${view}`} id="main" tabIndex={-1} aria-labelledby="view-title">
 <TopBar tenantId={scope.tenantId} workspaceId={scope.workspaceId} onSwitchWorkspace={onSwitchWorkspace} onSwitchOrganisation={onSwitchOrganisation} onRefresh={()=>void load()} onOpenPalette={openPalette}
  bell={<NotificationsMenu team={teamNotifications} approvals={approvals} alerts={alerts} notices={notices} onOpenApprovals={()=>navigate('approvals')} onOpenAlert={openRun} onOpenActivity={()=>navigate('activity')}/>}
  user={<UserMenu name={userName} userId={api.currentUserId()} role={membership?.role??null} signOut={auth.oidcEnabled?()=>void auth.signOut():null} onGo={openSettings}/>}/>
 {!OWN_HEADER.has(view)&&<div className="pageHead"><p className="eyebrow">{t('shell.crumb',{view:label})}</p><h1 id="view-title">{label}</h1></div>}{navParent(view)==='memory'&&<KnowledgeTabs view={view} onPick={navigate}/>}
 {archived&&<Notice>{t('workspaceAdmin.archivedBanner')}</Notice>}<AutomationAlerts alerts={alerts} onOpen={openRun} onDismiss={id=>setAlerts(list=>list.filter(x=>x.id!==id))}/>{error&&<Notice error>{error}</Notice>}{budgetStop&&<BudgetExceededNotice message={budgetStop} onOpenBudgets={()=>{setBudgetStop(null);go('budgets')}} onDismiss={()=>setBudgetStop(null)}/>}
 <Suspense fallback={<Notice>{t('shell.loadingView')}</Notice>}>{view==='team'&&<TeamView live={teamLive} focusItemId={workFocus} onFocusChange={setWorkFocus}/>}{view==='assistants'&&<AssistantsView onOpenWork={id=>{setWorkFocus(id);navigate('team')}}/>}{view==='organisations'&&<OrganisationsView/>}{(view==='home'||view==='command')&&<CommandCenter key={view} tasks={tasks} approvals={approvals} loading={loading} userName={userName} autoFocus={view==='command'} onGo={v=>navigate(v)} onOpenTask={openTask} onOpenFile={openFile} onOpenPalette={openPalette} onChanged={()=>void load({quiet:true})} onBudget={m=>setBudgetStop(m)}/>}{view==='assistant'&&<ChatView onOpenBudgets={()=>go('budgets')} onOpenDeveloper={owner?()=>go('developer'):undefined} onOpenTarget={openChatTarget} initialConversationId={chatTarget} onOpenReport={openReport} onOpenFavorite={openFavorite} onOpenFavorites={()=>go('favorites')}/>}{view==='favorites'&&<FavoritesView onOpen={openFavorite}/>}{view==='knowledge'&&<KnowledgeView key={collectionTarget??'all'} initialCollectionId={collectionTarget}/>}{view==='reports'&&<ReportsView key={reportTarget??'all'} initialReportId={reportTarget} onOpenConversation={id=>{setChatTarget(id);go('assistant')}} onOpenAutomation={()=>go('automation')} onOpenApprovals={()=>go('approvals')} onOpenInbox={()=>go('inbox')}/>}{view==='inbox'&&<InboxView key={emailFocus??'inbox'} initialMessageId={emailFocus} onOpenBudgets={()=>go('budgets')} onOpenApprovals={()=>go('approvals')}/>}{view==='whatsapp'&&<WhatsAppView key={waFocus??'whatsapp'} focusConversationId={waFocus} onOpenApprovals={()=>go('approvals')} onOpenBudgets={()=>go('budgets')}/>}{view==='developer'&&<DeveloperView onOpenBudgets={()=>go('budgets')}/>}{view==='marketing'&&<MarketingView onOpenBudgets={()=>go('budgets')} onOpenFiles={()=>go('files')}/>}{view==='tyre'&&<TyreView/>}{view==='meetings'&&<MeetingsView onOpenTasks={()=>go('tasks')} onOpenApprovals={()=>go('approvals')} onOpenSettings={()=>go('settings')}/>}{view==='tasks'&&<TasksView {...{tasks,task,run,approvals,prompt,setPrompt,busy,loading,setTask,stream}} message={t(message)} lastEvent={event} openApprovals={()=>go('approvals')} openMemory={target=>{setMemoryChats(false);setMemoryFocus(target);go('memory')}} openFile={openFile} runTask={()=>void doIt(async()=>{const r=await api.createAndRunTask(prompt);setTask(r.task);setRun(r.run);setPrompt('');setMessage(r.approval?'shell.taskPaused':'shell.taskCompleted');await load()})} cancel={()=>void doIt(async()=>{if(task)setTask(await api.cancelTask(task.id));await load()})} resume={()=>void doIt(async()=>{if(run){const r=await api.resumeRun(run.id);setTask(r.task);setRun(r.run)}await load()})}/>} {view==='automation'&&<AutomationWorkbench focus={automationFocus} onOpenTask={openTask} refreshSignal={automationTick}/>} {view==='agents'&&<AgentsView onOpenSkills={()=>go('skills')}/>} {view==='approvals'&&<ApprovalsView items={approvals} busy={busy} metric={(icon,label,value,detail)=><Metric icon={icon} label={label} value={value} detail={detail}/>} empty={<Empty title={t('approvals.queueClear')} text={t('approvals.queueClearText')}/>} decide={decide} userId={api.currentUserId()}/>} {view==='memory'&&<MemoryView key={memoryFocus?.id??'all'} tasks={tasks} initialOnlyChats={memoryChats} focus={memoryFocus} onOpenConversation={id=>{setChatTarget(id);go('assistant')}}/>} {view==='learning'&&<LearningView onOpenChatMemory={()=>go('settings')} onOpenWorkbench={()=>go('automation')}/>} {view==='files'&&<FilesView focusFileId={fileFocus} onFocusChange={setFileFocus}/>} {view==='skills'&&<SkillsView/>} {view==='commerce'&&<CommerceView onOpenApprovals={()=>go('approvals')}/>} {view==='integrations'&&<IntegrationsView/>} {view==='call'&&<CallView onOpenApprovals={()=>go('approvals')} onOpenConversation={id=>{setChatTarget(id);go('assistant')}}/>} {view==='voice'&&<VoiceView onOpenApprovals={()=>setView('approvals')} onOpenDeveloper={owner?()=>go('developer'):undefined}/>} {view==='governance'&&<GovernanceView/>} {view==='enterprise'&&<EnterpriseView/>} {view==='members'&&<MembersView workspaceId={scope.workspaceId} onSwitchWorkspace={onSwitchWorkspace} onSwitchOrganisation={onSwitchOrganisation}/>} {view==='audit'&&<AuditLogView onOpenMembers={()=>go('members')}/>} {view==='budgets'&&<BudgetsView/>} {view==='policy'&&<ApprovalPolicyView workspaceId={scope.workspaceId}/>} {view==='activity'&&<ActivityView onOpenSettings={()=>go('autonomy')}/>} {view==='autonomy'&&<AutonomySettingsView onOpenActivity={()=>go('activity')}/>} {view==='settings'&&<SettingsPage key={settingsKey} onSwitchWorkspace={onSwitchWorkspace} onArchivedChange={setArchived} onOpenChatMemories={()=>{setMemoryFocus(null);setMemoryChats(true);go('memory')}}/>}</Suspense>{notices.length>0&&<Suspense fallback={null}><ActionNotices notices={notices} onDismiss={id=>setNotices(old=>old.filter(n=>n.actionId!==id))} onOpenActivity={()=>go('activity')}/></Suspense>}</main>
 {paletteOpen&&<CommandPalette views={views} recentTasks={tasks} onClose={()=>setPaletteOpen(false)} onPick={pick}/>}</div>
}

// Settings has four sections; #notifications deep-links to Notifications and phone push (docs/notifications.md),
// #privacy to Privacy (the in-app data download and deletion path) and #language to the interface language
// (docs/web-localization.md).
type SettingsTab='workspace'|'notifications'|'language'|'privacy';
const settingsTabs:SettingsTab[]=['workspace','notifications','language','privacy'];
const settingsTabFromHash=():SettingsTab=>{const hash=location.hash.slice(1);return settingsTabs.includes(hash as SettingsTab)&&hash!=='workspace'?hash as SettingsTab:'workspace'};
function SettingsPage({onOpenChatMemories,onSwitchWorkspace,onArchivedChange}:{onOpenChatMemories:()=>void;onSwitchWorkspace:(workspaceId:string)=>Promise<void>;onArchivedChange:(archived:boolean)=>void}){const [tab,setTab]=useState<SettingsTab>(settingsTabFromHash),[focusChatMemory,setFocusChatMemory]=useState(false);const pick=(next:SettingsTab)=>{setTab(next);setFocusChatMemory(false);history.replaceState(null,'',next==='workspace'?'#settings':`#${next}`)};return <><nav className="settingsTabs" aria-label={t('settings.sections')}>{settingsTabs.map(id=><button key={id} type="button" aria-current={tab===id?'page':undefined} onClick={()=>pick(id)}>{t(`settings.tab.${id}`)}</button>)}</nav>{tab==='workspace'?<><WorkspaceAdmin onSwitchWorkspace={onSwitchWorkspace} onArchivedChange={onArchivedChange}/><WorkspaceSettings onOpenChatMemories={onOpenChatMemories} focusChatMemory={focusChatMemory}/></>:tab==='notifications'?<div className="settingsStack"><NotificationSettings/></div>:tab==='language'?<LanguageSettings/>:<PrivacyView onOpenChatMemory={()=>{pick('workspace');setFocusChatMemory(true)}}/>}</>}
function WorkspaceSettings({onOpenChatMemories,focusChatMemory=false}:{onOpenChatMemories:()=>void;focusChatMemory?:boolean}){const [onboard,setOnboard]=useState<api.OnboardingStatus|null>(null),[org,setOrg]=useState('ANUM'),[workspace,setWorkspace]=useState('Foundation'),[state,setState]=useState<string>(()=>t('settings.onboarding.loading'));const load=useCallback(()=>api.getOnboarding().then(o=>{setOnboard(o);setState('')}).catch(e=>setState(err(e))),[]);useEffect(()=>{void load()},[load]);return <div className="settingsStack"><section className="surface settingsForm"><div className="sectionHeader"><div><p className="eyebrow">{t('settings.onboarding.eyebrow')}</p><h2>{t('settings.onboarding.title')}</h2></div>{onboard&&<span className={`statusBadge ${onboard.complete?'success':'warning'}`}>{onboard.complete?t('settings.onboarding.complete'):t('settings.onboarding.required')}</span>}</div><div className="formGrid"><label className="field"><span>{t('settings.onboarding.orgName')}</span><input value={org} onChange={e=>setOrg(e.target.value)}/></label><label className="field"><span>{t('settings.onboarding.workspaceName')}</span><input value={workspace} onChange={e=>setWorkspace(e.target.value)}/></label></div><div className="actions"><button disabled={!onboard||onboard.complete} onClick={()=>void api.completeOnboarding(org,workspace).then(load).catch(e=>setState(err(e)))}>{onboard?.complete?t('settings.onboarding.done'):t('settings.onboarding.submit')}</button></div></section><BrandingSettings/><ModelGatewaySettings onboarded={onboard?onboard.complete:null}/><ChatMemorySetting reviewLabel={t('settings.chatMemoryReview')} onReview={onOpenChatMemories} focusOnLoad={focusChatMemory}/>{state&&<Notice>{state}</Notice>}</div>}

// The Knowledge Base entry's two tabs (docs/knowledge-base.md): memories ANUM keeps, and collections of files and notes to search and ask.
function KnowledgeTabs({view,onPick}:{view:View;onPick:(view:View)=>void}){return <nav className="settingsTabs" aria-label={t('knowledge.tabs.label')}>{(['memory','knowledge'] as const).map(id=><button key={id} type="button" aria-current={view===id?'page':undefined} onClick={()=>onPick(id)}>{t(id==='memory'?'knowledge.tabs.memories':'knowledge.tabs.collections')}</button>)}</nav>}
function Metric({icon,label,value,detail}:{icon:ReactNode;label:string;value:string;detail:string}){return <article className="metric"><div className="metricTop"><span>{label}</span><div className="metricIcon">{icon}</div></div><strong>{value}</strong><small>{detail}</small></article>}
function Status({value}:{value:string}){return <span className={`statusBadge ${value==='completed'?'success':value==='failed'||value==='cancelled'?'dangerTone':'warning'}`}>{statusLabel(value)}</span>}
function Notice({children,error}:{children:ReactNode;error?:boolean}){return <p className={`notice ${error?'errorNotice':''}`} role={error?'alert':'status'} aria-live={error?undefined:'polite'}><CircleDot aria-hidden="true"/>{children}</p>}
function Empty({title,text}:{title:string;text:string}){return <div className="emptyState"><Search aria-hidden="true"/><h3>{title}</h3><p>{text}</p></div>}
const date=(v:string)=>formatDateTime(v);const time=(v:string)=>formatDateTime(v,{hour:'2-digit',minute:'2-digit',second:'2-digit'});
// Signs in through Keycloak when VITE_ANUM_OIDC_ISSUER is set; otherwise renders the app on the local development session.
function AuthGate(){const [status,setStatus]=useState<auth.AuthStatus|null>(null);useEffect(()=>{let live=true;const off=auth.onAuthChange(s=>{if(live)setStatus(s)});void auth.initAuth().then(s=>{if(live)setStatus(s)});return()=>{live=false;off()}},[]);
 if(!status)return <main className="signInShell" aria-busy="true"><section className="surface signInCard"><p className="eyebrow">ANUM</p><h1>{t('auth.restoring')}</h1></section></main>;
 // Google, Apple, email and create-account all start the same code + PKCE sign-in (SignInView.tsx).
 if(status.kind==='signedOut')return <SignInScreen message={status.message} onSignIn={request=>auth.signIn(request)}/>;
 // Signed in without an organization: nothing in the app would work, so say what happens next.
 if(status.kind==='pending')return <PendingScreen account={status.account} onCheckAgain={async()=>{const next=await auth.checkAgain();if(next.kind!=='pending')setStatus(next);return next.kind==='pending'}} onSignOut={()=>void auth.signOut()}/>;
 return <SignedInApp/>}
// The workspace is part of the app's identity: a confirmed switch remounts everything under the new one.
function SignedInApp(){const [scopeKey,setScopeKey]=useState(()=>{const s=api.currentScope();return `${s.tenantId}/${s.workspaceId}`});return <App key={scopeKey} onSwitchWorkspace={async target=>{const workspaceId=await api.switchWorkspace(target);setScopeKey(`${api.currentScope().tenantId}/${workspaceId}`)}} onSwitchOrganisation={async (tenantId,workspaceId)=>{const next=await api.switchOrganisation(tenantId,workspaceId);history.replaceState(null,'','#team');setScopeKey(`${next.tenantId}/${next.workspaceId}`)}}/>}

// The organisation's name and logo for the sidebar (docs/customer-organisations.md); silent when unavailable.
function useBranding(){const [state,setState]=useState<{name:string|null;logo:string|null}>({name:null,logo:null});useEffect(()=>{let live=true,url:string|null=null;void getBranding().then(async b=>{url=b.has_logo?await logoObjectUrl().catch(()=>null):null;if(live)setState({name:b.display_name,logo:url});else if(url)URL.revokeObjectURL(url)}).catch(()=>undefined);return()=>{live=false;if(url)URL.revokeObjectURL(url)}},[]);return state}
// Browser error reporting (docs/observability.md#error-reporting): only a bundle built with VITE_SENTRY_DSN
// contains it; without the DSN this branch and the SDK chunk are dropped at build time.
const sentryDsn:string|undefined=import.meta.env.VITE_SENTRY_DSN;
if(sentryDsn)void import('./lib/errorReportingStart').then(m=>m.start(sentryDsn,import.meta.env.VITE_SENTRY_ENVIRONMENT||'production',()=>api.currentScope().tenantId)).catch(()=>undefined);
// The interface language: a choice saved in this browser, else the browser's languages (docs/web-localization.md).
// Set before the first render so <html lang dir> are right from the first paint.
const storage=(()=>{try{return window.localStorage}catch{return null}})();
// A mailbox connection the provider just sent the browser back with (docs/email.md): kept for the
// Inbox view, which finishes it as the signed-in person, and removed from the address bar before
// sign-in runs (it never carries an OAuth code, only ANUM's connection id).
const emailReturn=readConnectReturn(window.location.href);
if(emailReturn){try{sessionStorage.setItem(CONNECT_STORAGE_KEY,JSON.stringify({connection:emailReturn.connection,error:emailReturn.error}))}catch{/* the Inbox then shows nothing to finish */}history.replaceState(null,'',emailReturn.cleanUrl)}
initLocale({storage,languages:navigator.languages?.length?navigator.languages:[navigator.language],root:document.documentElement});
// Re-renders the whole tree when the language changes, so every string and layout direction follows.
function Root(){useLocale();return <AuthGate/>}
createRoot(document.getElementById('root')!).render(<Root/>);
