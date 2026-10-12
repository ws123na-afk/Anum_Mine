// Unit tests for the Calls & Meetings helpers in src/lib/meetings.ts.
// Run with `pnpm --filter @anum/web test:unit` (Node's test runner with type stripping).
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import {
  clock, draftText, durationText, mailtoLink, meetingsListPath, oauthCallback, pageNumbers, parseRecipients, recentActivity,
  statusLabel, statusTone, textDirection, transcriptPath, uploadProblem, uploadQuery, type ApiMeeting, type ApiMeetingSetup,
} from '../src/lib/meetings.ts';

afterEach(() => setLocale('en'));

const setup: ApiMeetingSetup = {
  transcription: { configured: true, provider: 'openai-compatible', model: 'whisper-1', target: 'whisper.local', problem: null, max_recording_bytes: 2 * 1024 * 1024, accepted_extensions: ['.mp3', '.wav'] },
  transcript_extensions: ['.vtt', '.srt', '.txt', '.docx'], max_transcript_bytes: 1024 * 1024, connectors: [],
  model_ready: true, email_accounts: [], can_create: true, can_manage_all: false,
};

const meeting = (id: string, status: ApiMeeting['status'], updated: string, summary: string | null = 'Done.'): ApiMeeting => ({
  id, tenant_id: 't', workspace_id: 'w', created_by: 'u', title: `Call ${id}`, source: 'transcript', status, language: 'en', requested_language: 'auto',
  occurred_at: null, duration_seconds: null, agent_skill_id: id === 'a' ? 'pmv.planner' : null, recording_file_id: null, source_file_name: null, external_id: null,
  segment_count: 3, error_code: null, error: null, summary, decisions: [], action_items: [], follow_up: null, provenance: null,
  created_at: updated, updated_at: updated,
});

describe('paths', () => {
  test('list path carries paging, a trimmed search and the status', () => {
    assert.equal(meetingsListPath(), '/api/v1/meetings?limit=20&offset=0');
    assert.equal(meetingsListPath({ query: '  PMV call ', status: 'failed', offset: 40 }), '/api/v1/meetings?limit=20&offset=40&q=PMV+call&status=failed');
  });
  test('transcript path encodes the id, query and speaker', () => {
    assert.equal(transcriptPath('m/1', { query: 'stock', speaker: 'Omar Ali', offset: 100 }), '/api/v1/meetings/m%2F1/transcript?limit=100&offset=100&q=stock&speaker=Omar+Ali');
  });
  test('upload query URL-encodes Arabic names and skips auto language', () => {
    const query = new URLSearchParams(uploadQuery('اجتماع.vtt', { title: ' Weekly ', language: 'auto', agentSkillId: 'pmv.planner' }));
    assert.equal(query.get('name'), 'اجتماع.vtt');
    assert.equal(query.get('title'), 'Weekly');
    assert.equal(query.get('language'), null);
    assert.equal(query.get('agent_skill_id'), 'pmv.planner');
    assert.equal(new URLSearchParams(uploadQuery('a.vtt', { language: 'ar' })).get('language'), 'ar');
  });
});

describe('labels and formats', () => {
  test('clock and duration', () => {
    assert.equal(clock(62_000), '01:02');
    assert.equal(clock(3_723_000), '1:02:03');
    assert.equal(clock(null), '');
    assert.equal(durationText(2700), '45 min');
    assert.equal(durationText(3900), '1 h 5 min');
    assert.equal(durationText(null), '');
  });
  test('status labels and tones in English and Arabic', () => {
    assert.equal(statusLabel('needs_model'), 'needs a model');
    assert.equal(statusTone('ready'), 'success');
    assert.equal(statusTone('failed'), 'dangerTone');
    assert.equal(statusTone('analyzing'), 'warning');
    setLocale('ar');
    assert.equal(statusLabel('ready'), 'جاهز');
    assert.equal(durationText(120), 'دقيقتان');
  });
  test('Arabic meetings read right to left', () => {
    assert.equal(textDirection('ar'), 'rtl');
    assert.equal(textDirection('en'), 'ltr');
    assert.equal(textDirection('auto'), 'auto');
  });
});

describe('follow-up email', () => {
  test('recipients split on commas, semicolons and lines, deduplicated', () => {
    assert.deepEqual(parseRecipients('a@example.com; b@example.com,\n a@example.com, nope'), { valid: ['a@example.com', 'b@example.com'], invalid: ['nope'] });
  });
  test('mailto link and clipboard text', () => {
    const draft = { to: ['a@example.com'], subject: 'Recap & next', body: 'Hi all\nThanks' };
    assert.equal(mailtoLink(draft), 'mailto:a%40example.com?subject=Recap%20%26%20next&body=Hi%20all%0AThanks');
    assert.equal(mailtoLink({ ...draft, body: 'x'.repeat(9000) }), null);
    assert.equal(draftText(draft), 'To: a@example.com\nSubject: Recap & next\n\nHi all\nThanks');
  });
});

describe('uploads', () => {
  test('checks the kind and the server limits', () => {
    assert.equal(uploadProblem({ name: 'a.vtt', size: 10 }, 'transcript', setup), null);
    assert.match(uploadProblem({ name: 'a.pdf', size: 10 }, 'transcript', setup) ?? '', /not a supported file/);
    assert.match(uploadProblem({ name: 'call.MP3', size: 3 * 1024 * 1024 }, 'recording', setup) ?? '', /2 MB/);
    assert.match(uploadProblem({ name: 'a.vtt', size: 0 }, 'transcript', setup) ?? '', /empty/);
  });
});

describe('OAuth callback page', () => {
  test('reads code and state only on the meetings callback path', () => {
    assert.deepEqual(oauthCallback({ pathname: '/meetings/connected', search: '?code=c1&state=s1' }), { code: 'c1', state: 's1' });
    assert.deepEqual(oauthCallback({ pathname: '/meetings/connected', search: '?error=access_denied&error_description=User+said+no' }), { error: 'User said no' });
    assert.equal(oauthCallback({ pathname: '/', search: '?code=c1&state=s1' }), null);
  });
});

describe('recent activity and pager', () => {
  test('call summary entries for the newest ready meetings', () => {
    const entries = recentActivity([meeting('a', 'ready', '2026-10-10T10:00:00Z'), meeting('b', 'failed', '2026-10-11T10:00:00Z'), meeting('c', 'ready', '2026-10-11T09:00:00Z'), meeting('d', 'ready', '2026-10-11T11:00:00Z', null)]);
    assert.deepEqual(entries.map((entry) => [entry.title, entry.agent]), [['Call summary: Call c', null], ['Call summary: Call a', 'pmv.planner']]);
    setLocale('ar');
    assert.equal(recentActivity([meeting('a', 'ready', '2026-10-10T10:00:00Z')])[0].title, 'ملخص مكالمة: \u2068Call a\u2069');
  });
  test('page numbers collapse with gaps', () => {
    assert.deepEqual(pageNumbers(1, 3), [1, 2, 3]);
    assert.deepEqual(pageNumbers(6, 12), [1, null, 5, 6, 7, null, 12]);
    assert.deepEqual(pageNumbers(1, 12), [1, 2, null, 12]);
  });
});
