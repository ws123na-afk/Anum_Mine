// Unit tests for the action browser's web helpers (src/lib/actionBrowser.ts, docs/action-browser.md).
import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import {
  WORDS, cardCodeProblem, credentialBody, credentialProblem, isActive, kindText, luhn, say, statusText, stepFacts, stepText, toPagePoint, usesActionBrowser,
  type BrowserSession, type SessionStep,
} from '../src/lib/actionBrowser.ts';

const session = (over: Partial<BrowserSession> = {}): BrowserSession => ({
  id: 'abs_1', task_id: 'task_1', run_id: 'run_1', status: 'running', goal: 'Book', start_url: 'https://shop.example/', sites: ['shop.example'],
  logins: ['shop'], cards: ['main'], step_count: 3, current_url: 'https://shop.example/pay', current_title: 'Pay', waiting: null, pending: null,
  outcome: null, message: '', live: true, can_watch: true, can_stop: true, created_at: '2026-11-04T08:00:00Z', updated_at: '2026-11-04T08:00:01Z',
  ended_at: null, steps: [], ...over,
});

describe('words', () => {
  test('English and Arabic have the same keys and no long dashes', () => {
    assert.deepEqual(Object.keys(WORDS.ar).sort(), Object.keys(WORDS.en).sort());
    for (const text of [...Object.values(WORDS.en), ...Object.values(WORDS.ar)]) assert.ok(!/[–—]/.test(text), text);
    for (const key of Object.keys(WORDS.en) as (keyof typeof WORDS.en)[]) {
      const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort().join();
      if (!WORDS.en[key].includes('{{')) assert.equal(holes(WORDS.ar[key]), holes(WORDS.en[key]), key);
    }
  });
  test('say fills placeholders in the chosen language', () => {
    assert.equal(say('saved', { handle: 'main' }, 'en'), 'Saved main.');
    assert.equal(say('saved', { handle: 'main' }, 'ar'), 'تم حفظ main.');
  });
});

describe('status and steps', () => {
  test('status follows what the session waits for', () => {
    assert.equal(statusText(session(), 'en'), 'Working');
    assert.equal(statusText(session({ status: 'waiting', waiting: 'approval' }), 'en'), 'Waiting for your approval');
    assert.equal(statusText(session({ status: 'waiting', waiting: 'handoff' }), 'en'), 'Needs you');
    assert.equal(statusText(session({ status: 'waiting', waiting: 'handoff' }), 'ar'), 'يحتاجك');
    assert.equal(statusText(session({ status: 'completed' }), 'en'), 'Done');
    assert.ok(isActive(session({ status: 'waiting' })) && !isActive(session({ status: 'stopped' })) && !isActive(null));
  });
  test('steps read in Arabic from their details, English from the stored sentence', () => {
    const step: SessionStep = { seq: 1, kind: 'opened', summary: 'Opened shop.example', url: null, detail: { host: 'shop.example' }, created_at: '' };
    assert.equal(stepText(step, 'en'), 'Opened shop.example');
    assert.equal(stepText(step, 'ar'), 'فتح shop.example');
    assert.equal(stepText({ ...step, kind: 'approval', summary: 'Waiting for your approval: press Pay (AED 1,240) on shop.example.' }, 'ar'),
      'بانتظار موافقتك: press Pay (AED 1,240) on shop.example.');
    assert.equal(stepText({ ...step, kind: 'mystery' }, 'ar'), 'Opened shop.example');
  });
  test('a task used the action browser when a step or an approval names web.act', () => {
    assert.ok(usesActionBrowser([{ metadata: { tool: 'web.act' } }]));
    assert.ok(usesActionBrowser([], [{ action: 'web.act' }]));
    assert.ok(!usesActionBrowser([{ metadata: { tool: 'web.browse_page' } }], [{ action: 'external.action' }]));
  });
});

describe('the picture', () => {
  test('a click on the scaled picture lands on the page', () => {
    assert.deepEqual(toPagePoint(110, 60, { left: 10, top: 10, width: 640, height: 400 }), { x: 200, y: 100 });
    assert.equal(toPagePoint(5, 5, { left: 10, top: 10, width: 640, height: 400 }), null);
    assert.equal(toPagePoint(5, 5, { left: 0, top: 0, width: 0, height: 0 }), null);
  });
});

describe('approval facts', () => {
  test('a pay step shows the site, the address, the button and the amount', () => {
    const facts = stepFacts({ resume: 'step', kind: 'binding_button', site: 'shop.example', url: 'https://shop.example/pay', target: '“Pay AED 1,240”',
      amount_text: 'AED 1,240', fields: [{ label: 'Card number', type: 'text' }], shown_action: { type: 'click', ref: 'e2' } });
    assert.ok(facts);
    assert.equal(facts.amount, 'AED 1,240');
    assert.equal(facts.fields.length, 1);
    assert.equal(kindText(facts, 'en'), 'Press a button that commits');
  });
  test('the scope approval and other tools', () => {
    const scope = stepFacts({ goal: 'x', start_url: 'https://shop.example/', allowed_domains: ['shop.example'] });
    assert.equal(scope?.resume, 'scope');
    assert.equal(kindText(scope!, 'ar'), 'بدء مهمة المتصفح');
    assert.equal(stepFacts({ action: 'x' }), null);
    assert.equal(stepFacts(undefined), null);
  });
});

describe('saved logins and cards', () => {
  test('the form checks each kind', () => {
    assert.equal(credentialProblem({ kind: 'login', handle: 'Bad Name' }, false, 'en'), WORDS.en.handleRule);
    assert.equal(credentialProblem({ kind: 'login', handle: 'shop' }, false, 'en'), WORDS.en.siteNeeded);
    assert.equal(credentialProblem({ kind: 'login', handle: 'shop', site: 'shop.example', username: 'a', password: 'b' }, false, 'en'), null);
    assert.equal(credentialProblem({ kind: 'login', handle: 'shop' }, true, 'en'), null);
    assert.equal(credentialProblem({ kind: 'card', handle: 'main', number: '4242424242424241', expiry: '08/29', name: 'A' }, false, 'en'), WORDS.en.cardNumberBad);
    assert.equal(credentialProblem({ kind: 'card', handle: 'main', number: '4242 4242 4242 4242', expiry: '8/29', name: 'A' }, false, 'en'), WORDS.en.expiryBad);
    // A card is saved without a security code: ANUM never stores one.
    assert.equal(credentialProblem({ kind: 'card', handle: 'main', number: '4242424242424242', expiry: '08/29', name: 'A' }, false, 'en'), null);
    assert.equal(credentialProblem({ kind: 'card', handle: 'main' }, true, 'en'), null);
    assert.ok(luhn('4242424242424242') && !luhn('42'));
  });
  test('an edit sends only what was filled in', () => {
    assert.deepEqual(credentialBody({ kind: 'login', handle: ' Shop ', site: '', username: '', password: '' }), { kind: 'login', handle: 'shop' });
    assert.deepEqual(credentialBody({ kind: 'card', handle: 'main', expiry: ' 09/30 ' }), { kind: 'card', handle: 'main', expiry: '09/30' });
    // Even a stray code on the input object is never sent.
    const stray = { kind: 'card', handle: 'main', number: '4242424242424242', cvc: '739' } as unknown as Parameters<typeof credentialBody>[0];
    assert.ok(!('cvc' in credentialBody(stray)) && !JSON.stringify(credentialBody(stray)).includes('739'));
  });
});

describe('the card security code on the approval card', () => {
  test('3 or 4 digits, in English and Arabic', () => {
    assert.equal(cardCodeProblem('', 'en'), WORDS.en.codeNeeded);
    assert.equal(cardCodeProblem('12', 'en'), WORDS.en.codeBad);
    assert.equal(cardCodeProblem('12a', 'ar'), WORDS.ar.codeBad);
    assert.equal(cardCodeProblem('739', 'en'), null);
    assert.equal(cardCodeProblem('7391', 'ar'), null);
  });
  test('the step has its own heading', () => {
    const facts = stepFacts({ resume: 'step', kind: 'card_security_code', cvc: 'entered at approval', site: 'shop.example' })!;
    assert.equal(kindText(facts, 'en'), 'Enter the card security code');
    assert.equal(kindText(facts, 'ar'), WORDS.ar.kind_card_security_code);
  });
});
