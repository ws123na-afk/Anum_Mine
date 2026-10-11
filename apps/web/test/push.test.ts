// Unit tests for phone push in Settings › Notifications (src/lib/push.ts).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  MY_DEVICES_PATH,
  notificationSwitches,
  PUSH_STATUS_PATH,
  deviceDeletePath,
  deviceTitle,
  lastSentText,
  pushSummary,
  sortDevices,
  withDefaults,
  type PushDevice,
  type PushStatus,
} from '../src/lib/push.ts';

const on: PushStatus = { available: true, provider: 'fcm', project_id: 'anum-example', message: 'Phone push is on.' };
const off: PushStatus = { available: false, provider: 'fcm', project_id: null, message: 'Phone push is off on this server: the operator has not set ANUM_FCM_SERVICE_ACCOUNT_JSON.' };
const device = (id: string, updated: string, extra: Partial<PushDevice> = {}): PushDevice => ({
  id, platform: 'android', label: null, token_hint: 'ab12', created_at: updated, updated_at: updated, last_sent_at: null, ...extra,
});
const prefs = { task_completed: true, approval_required: true, run_failed: true, automation_failed: true, email_enabled: false, desktop_enabled: true };

describe('paths', () => {
  test('status, list and delete', () => {
    assert.equal(PUSH_STATUS_PATH, '/api/v1/push/status');
    assert.equal(MY_DEVICES_PATH, '/api/v1/me/devices');
    assert.equal(deviceDeletePath('device/1 2'), '/api/v1/me/devices/device%2F1%202');
  });
});

describe('pushSummary', () => {
  test('off on the server says who can turn it on', () => {
    const summary = pushSummary(off, [device('d1', '2026-10-11T09:00:00Z')]);
    assert.equal(summary.tone, 'warning');
    assert.equal(summary.badge, 'Off on this server');
    assert.match(summary.text, /ANUM_FCM_SERVICE_ACCOUNT_JSON/);
    assert.match(summary.text, /person who runs this ANUM server/);
  });
  test('no phone yet gives the next step in the app', () => {
    const summary = pushSummary(on, [], prefs);
    assert.equal(summary.badge, 'No phone yet');
    assert.match(summary.text, /Settings › Notifications/);
  });
  test('turned off by the user keeps the phones', () => {
    const summary = pushSummary(on, [device('d1', '2026-10-11T09:00:00Z')], { ...prefs, push_enabled: false });
    assert.equal(summary.badge, 'Off');
    assert.match(summary.text, /stay registered/);
  });
  test('on counts the phones and promises no content', () => {
    assert.match(pushSummary(on, [device('d1', 'x')], prefs).text, /^One phone gets/);
    const two = pushSummary(on, [device('d1', 'x'), device('d2', 'y')], { ...prefs, push_enabled: true });
    assert.equal(two.tone, 'success');
    assert.match(two.text, /^2 phones get/);
    assert.match(two.text, /Approval needed/);
  });
});

describe('devices', () => {
  test('titles tell phones apart without the token', () => {
    assert.equal(deviceTitle(device('d1', 'x')), 'Android phone (Android, ends in ab12)');
    assert.equal(deviceTitle(device('d2', 'x', { platform: 'ios', label: 'Work iPhone' })), 'Work iPhone (iPhone or iPad, ends in ab12)');
  });
  test('last notified', () => {
    const now = new Date('2026-10-11T12:00:00Z');
    assert.equal(lastSentText({ last_sent_at: null }, now), 'Not notified yet');
    assert.equal(lastSentText({ last_sent_at: '2026-10-11T11:59:30Z' }, now), 'Last notified just now');
    assert.equal(lastSentText({ last_sent_at: '2026-10-11T11:59:00Z' }, now), 'Last notified 1 minute ago');
    assert.equal(lastSentText({ last_sent_at: '2026-10-11T09:00:00Z' }, now), 'Last notified 3 hours ago');
    assert.equal(lastSentText({ last_sent_at: '2026-10-01T12:00:00Z' }, now), 'Last notified 10 days ago');
  });
  test('newest registration first', () => {
    const sorted = sortDevices([device('a', '2026-10-01T00:00:00Z'), device('b', '2026-10-11T00:00:00Z')]);
    assert.deepEqual(sorted.map((item) => item.id), ['b', 'a']);
  });
});

describe('notification switches', () => {
  test('every switch is labelled and phone push defaults on for older servers', () => {
    const keys = notificationSwitches().map((item) => item.key);
    assert.deepEqual([...keys].sort(), ['approval_required', 'automation_failed', 'desktop_enabled', 'email_enabled', 'push_enabled', 'run_failed', 'task_completed']);
    for (const item of notificationSwitches()) assert.ok(item.label && item.description.length > 10, item.key);
    assert.equal(withDefaults(prefs).push_enabled, true);
    assert.equal(withDefaults({ ...prefs, push_enabled: false }).push_enabled, false);
  });
});
