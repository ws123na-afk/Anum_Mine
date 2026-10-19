// Unit tests for invitation email and notification delivery states
// (src/lib/admin.ts, src/lib/push.ts, src/lib/desktopEvents.ts): docs/notifications.md, docs/identity.md.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { invitationBody, invitationEmailNotice, mapCreatedInvitation, mapInvitationEmail } from '../src/lib/admin.ts';
import { announcePreferences, desktopNoticeFor, onPreferences, showDesktopNotice, type DesktopEventContext } from '../src/lib/desktopEvents.ts';
import { desktopHint, emailHint, emailTestNotice, withDefaults, type NotificationChannels } from '../src/lib/push.ts';

const prefs = withDefaults({ task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: true, desktop_enabled: true });
const channels = (configured: boolean, addressKnown: boolean): NotificationChannels => ({ email: { configured, address_known: addressKnown }, push_configured: true });

describe('invitation email outcome', () => {
  test('maps every state and defaults an old server to not requested', () => {
    assert.equal(mapInvitationEmail(undefined).status, 'not_requested');
    assert.equal(mapInvitationEmail({ status: 'bogus' }).status, 'not_requested');
    assert.deepEqual(mapInvitationEmail({ status: 'failed', to: 'a@b.co', attempts: 3, error: 'down', link_included: true }), { status: 'failed', to: 'a@b.co', attempts: 3, error: 'down', linkIncluded: true });
    const created = mapCreatedInvitation({ invitation: { id: 'i', tenant_id: 't', workspace_id: 'w', role: 'member', invitee_user_id: null, invitee_email: 'a@b.co', status: 'pending', created_by_user_id: 'o', expires_at: '2030-01-01T00:00:00Z', accepted_by_user_id: null, accepted_at: null, revoked_at: null, created_at: '2030-01-01T00:00:00Z' }, token: 'anum_inv_x' });
    assert.equal(created.email.status, 'not_requested');
  });

  test('each state tells the owner what happened and what to do next', () => {
    const base = { to: 'sara@example.com', attempts: 1, error: null, linkIncluded: true };
    assert.equal(invitationEmailNotice({ ...base, status: 'sent' }).tone, 'success');
    assert.match(invitationEmailNotice({ ...base, status: 'sent' }).text, /Emailed to sara@example.com with a link/);
    assert.match(invitationEmailNotice({ ...base, status: 'sent', linkIncluded: false }).text, /token to paste/);
    const failed = invitationEmailNotice({ ...base, status: 'failed', attempts: 3, error: 'The mail server could not be reached' });
    assert.equal(failed.tone, 'warning');
    assert.match(failed.text, /after 3 tries/);
    assert.match(failed.text, /send it yourself/);
    const off = invitationEmailNotice({ ...base, status: 'not_configured' });
    assert.equal(off.tone, 'warning');
    assert.match(off.text, /no mail server/);
    assert.match(invitationEmailNotice({ ...base, status: 'not_requested', to: null }).text, /no email address/);
    assert.match(invitationEmailNotice({ ...base, status: 'not_requested' }).text, /as you chose/);
  });

  test('the request carries the app address and only sends send_email when turned off', () => {
    assert.deepEqual(invitationBody({ role: 'member', inviteeEmail: 'a@b.co', appUrl: 'https://app.example.com/' }), { role: 'member', invitee_email: 'a@b.co', app_url: 'https://app.example.com/' });
    assert.equal((invitationBody({ role: 'member', inviteeEmail: 'a@b.co', sendEmail: false }) as { send_email?: boolean }).send_email, false);
    assert.equal('send_email' in invitationBody({ role: 'member', inviteeEmail: 'a@b.co', sendEmail: true }), false);
  });
});

describe('channel hints', () => {
  test('email says why it delivers nothing', () => {
    assert.equal(emailHint(prefs, null), null);
    assert.match(emailHint(prefs, channels(false, true))?.text ?? '', /no mail server/);
    assert.equal(emailHint(prefs, channels(false, true))?.tone, 'warning');
    assert.match(emailHint(prefs, channels(true, false))?.text ?? '', /no email address for you/);
    assert.equal(emailHint({ email_enabled: true }, channels(true, true))?.tone, 'ok');
    assert.equal(emailHint({ email_enabled: false }, channels(true, true))?.tone, 'neutral');
  });

  test('desktop depends on the browser permission', () => {
    assert.match(desktopHint(prefs, 'unsupported').text, /cannot show/);
    assert.match(desktopHint(prefs, 'denied').text, /blocked/);
    assert.equal(desktopHint(prefs, 'default').tone, 'warning');
    assert.equal(desktopHint({ desktop_enabled: false }, 'default').tone, 'neutral');
    assert.equal(desktopHint(prefs, 'granted').tone, 'ok');
  });

  test('the test email notice covers sent, failed, not configured and no address', () => {
    assert.equal(emailTestNotice({ status: 'sent', to: 'a@b.co', attempts: 1, error: null }).tone, 'ok');
    assert.match(emailTestNotice({ status: 'failed', to: 'a@b.co', attempts: 3, error: 'down' }).text, /3 tries: down/);
    assert.match(emailTestNotice({ status: 'not_configured', to: null, attempts: 0, error: null }).text, /no mail server/);
    assert.match(emailTestNotice({ status: 'no_address', to: null, attempts: 0, error: null }).text, /no email address/);
  });
});

describe('desktop notices for approvals and task results', () => {
  const event = (type: string, payload: Record<string, unknown> = {}) => ({ id: 'e1', type, subject: 's1', payload });
  const context = (overrides: Partial<DesktopEventContext> = {}): DesktopEventContext => ({
    preferences: prefs, userId: 'me', isOwner: true,
    taskCreator: async (id) => (id === 'task_mine' ? 'me' : 'someone'),
    approval: async () => ({ riskLevel: 'high', requestedBy: 'someone', taskId: 'task_other' }),
    twoPersonRule: async () => false,
    ...overrides,
  });

  test('approvals reach owners who kept the switches on', async () => {
    assert.deepEqual(await desktopNoticeFor(event('approval.requested'), context()), { title: 'Approval needed', body: 'An action is waiting for your decision.', tag: 's1' });
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ isOwner: false })), null);
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ preferences: { ...prefs, approval_required: false } })), null);
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ preferences: { ...prefs, desktop_enabled: false } })), null);
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ preferences: null })), null);
  });

  test('under the two-person rule the requester and the task creator are not told', async () => {
    const rule = { twoPersonRule: async () => true };
    // I requested it.
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ ...rule, approval: async () => ({ riskLevel: 'high', requestedBy: 'me', taskId: 'task_other' }) })), null);
    // I created its task (a task outside the loaded list is looked up).
    assert.equal(await desktopNoticeFor(event('approval.requested'), context({ ...rule, approval: async () => ({ riskLevel: 'high', requestedBy: 'someone', taskId: 'task_mine' }) })), null);
    // Another owner is still told.
    assert.ok(await desktopNoticeFor(event('approval.requested'), context({ ...rule })));
    // The rule covers high risk only, and only while it is on.
    assert.ok(await desktopNoticeFor(event('approval.requested'), context({ ...rule, approval: async () => ({ riskLevel: 'medium', requestedBy: 'me', taskId: 'task_mine' }) })));
    assert.ok(await desktopNoticeFor(event('approval.requested'), context({ approval: async () => ({ riskLevel: 'high', requestedBy: 'me', taskId: 'task_mine' }) })));
    // An approval that cannot be read still notifies; the server decides who may act.
    assert.ok(await desktopNoticeFor(event('approval.requested'), context({ ...rule, approval: async () => null })));
  });

  test('task results reach the task creator only, each with its own switch', async () => {
    assert.equal((await desktopNoticeFor(event('agent_run.completed', { task_id: 'task_mine' }), context()))?.title, 'Task finished');
    assert.equal((await desktopNoticeFor(event('agent_run.failed', { task_id: 'task_mine' }), context()))?.title, 'Task failed');
    assert.equal(await desktopNoticeFor(event('agent_run.completed', { task_id: 'task_other' }), context()), null);
    assert.equal(await desktopNoticeFor(event('agent_run.completed', { task_id: 'task_mine' }), context({ preferences: { ...prefs, task_completed: false } })), null);
    assert.equal(await desktopNoticeFor(event('agent_run.failed', { task_id: 'task_mine' }), context({ preferences: { ...prefs, run_failed: false } })), null);
    assert.equal(await desktopNoticeFor(event('agent_run.completed'), context()), null);
    assert.equal(await desktopNoticeFor(event('task.created'), context()), null);
  });

  test('a task outside the loaded list is resolved, not treated as someone else\'s', async () => {
    const looked: string[] = [];
    const fetched = context({ taskCreator: async (id) => { looked.push(id); return 'me'; } });
    assert.equal((await desktopNoticeFor(event('agent_run.completed', { task_id: 'task_old' }), fetched))?.title, 'Task finished');
    assert.deepEqual(looked, ['task_old']);
    // Not found anywhere: quiet.
    assert.equal(await desktopNoticeFor(event('agent_run.failed', { task_id: 'task_gone' }), context({ taskCreator: async () => undefined })), null);
  });

  test('the notice text is fixed: it never carries a task title', async () => {
    const notice = await desktopNoticeFor(event('agent_run.completed', { task_id: 'task_mine', title: 'Secret board deck' }), context());
    assert.ok(notice && !JSON.stringify(notice).includes('Secret'));
  });

  test('nothing is shown in the foreground, without permission, or when unsupported', () => {
    const notice = { title: 't', body: 'b', tag: 'x' };
    assert.equal(showDesktopNotice(notice, { hidden: false, permission: 'granted' }), false);
    assert.equal(showDesktopNotice(notice, { hidden: true, permission: 'default' }), false);
    assert.equal(showDesktopNotice(notice, { hidden: true, permission: 'denied' }), false);
    assert.equal(showDesktopNotice(notice, { hidden: true, permission: 'granted' }), false); // no Notification in node
  });

  test('saved preferences reach listeners until they stop listening', () => {
    const scope = new EventTarget();
    const seen: boolean[] = [];
    const stop = onPreferences((value) => seen.push(value.desktop_enabled), scope);
    announcePreferences({ ...prefs, desktop_enabled: false }, scope);
    stop();
    announcePreferences({ ...prefs, desktop_enabled: true }, scope);
    assert.deepEqual(seen, [false]);
  });
});
