// Unit tests for the Email Center outgoing helpers (src/lib/mailOutgoing.ts, docs/email.md#attachments
// and #scheduled-send): the send-later picker's times and checks, and the early file checks.
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { approvalSendAt, fileProblems, fromLocalInput, quickTimes, scheduleProblem, scheduledFor, toLocalInput, totalBytes } from '../src/lib/mailOutgoing.ts';
import type { MailDraft, MailSetup } from '../src/lib/mail.ts';

const setup = {
  attachment_max_bytes: 1000, attachments_per_message: 3, attachments_total_bytes: 1500, attachment_extensions: ['.pdf', '.png', '.txt'],
} as MailSetup;

const draft = (status: MailDraft['status'], send_at: string | null): MailDraft => ({ status, send_at } as MailDraft);

describe('send-later times', () => {
  test('a local input value round-trips in the person\'s zone', () => {
    const at = new Date(2026, 9, 20, 8, 5);
    assert.equal(toLocalInput(at), '2026-10-20T08:05');
    assert.equal(fromLocalInput('2026-10-20T08:05')?.getTime(), at.getTime());
    assert.equal(fromLocalInput(''), null);
    assert.equal(fromLocalInput('tomorrow'), null);
  });

  test('a time must be a minute ahead and within the limit', () => {
    const now = new Date(2026, 9, 18, 10, 0);
    assert.equal(scheduleProblem(null, now, 30), 'empty');
    assert.equal(scheduleProblem(new Date(now.getTime() + 30_000), now, 30), 'tooSoon');
    assert.equal(scheduleProblem(new Date(now.getTime() + 31 * 86_400_000), now, 30), 'tooFar');
    assert.equal(scheduleProblem(new Date(now.getTime() + 3600_000), now, 30), null);
  });

  test('quick picks are tomorrow and next Monday at 08:00', () => {
    const saturday = new Date(2026, 9, 17, 15, 30);
    const [tomorrow, monday] = quickTimes(saturday);
    assert.equal(toLocalInput(tomorrow.when), '2026-10-18T08:00');
    assert.equal(toLocalInput(monday.when), '2026-10-19T08:00');
    const onMonday = quickTimes(new Date(2026, 9, 19, 7, 0))[1];
    assert.equal(toLocalInput(onMonday.when), '2026-10-26T08:00');
  });

  test('a draft shows its time only while scheduled or waiting for that approval', () => {
    assert.equal(scheduledFor(draft('scheduled', '2026-10-20T04:00:00+00:00')), '2026-10-20T04:00:00+00:00');
    assert.equal(scheduledFor(draft('pending_approval', '2026-10-20T04:00:00+00:00')), '2026-10-20T04:00:00+00:00');
    assert.equal(scheduledFor(draft('draft', '2026-10-20T04:00:00+00:00')), null);
    assert.equal(scheduledFor(draft('pending_approval', null)), null);
  });

  test('the approval\'s bound time is read from its arguments', () => {
    assert.equal(approvalSendAt({ send_at: '2026-10-20T04:00:00+00:00' }), '2026-10-20T04:00:00+00:00');
    assert.equal(approvalSendAt({ send_at: null }), null);
    assert.equal(approvalSendAt(undefined), null);
  });
});

describe('early file checks', () => {
  test('type, empty, size, count and total, in order', () => {
    const { ok, problems } = fileProblems(
      [{ name: 'tool.exe', size: 10 }, { name: 'empty.txt', size: 0 }, { name: 'big.pdf', size: 2000 }, { name: 'a.PDF', size: 700 }, { name: 'b.png', size: 800 }, { name: 'c.txt', size: 200 }],
      [{ size: 100 }], setup,
    );
    assert.deepEqual(ok.map((f) => f.name), ['a.PDF', 'c.txt']);
    assert.deepEqual(problems.map((p) => [p.key, p.name]), [['type', 'tool.exe'], ['empty', 'empty.txt'], ['size', 'big.pdf'], ['total', 'b.png']]);
    const full = fileProblems([{ name: 'd.txt', size: 1 }], [{ size: 1 }, { size: 1 }, { size: 1 }], setup);
    assert.deepEqual(full.problems.map((p) => p.key), ['count']);
  });

  test('an older API without limits lets the server decide', () => {
    const { ok, problems } = fileProblems([{ name: 'any.bin', size: 5 }], [], {} as MailSetup);
    assert.equal(ok.length, 1);
    assert.equal(problems.length, 0);
    assert.equal(totalBytes([{ size: 2 }, { size: 3 }]), 5);
  });
});
