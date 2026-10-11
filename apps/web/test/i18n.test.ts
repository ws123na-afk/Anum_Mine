// Unit tests for the web localization layer in src/lib/i18n.ts and the catalogs in
// src/lib/messages (docs/web-localization.md). Run with `pnpm --filter @anum/web test:unit`.
import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import {
  CATALOGS, LOCALES, LOCALE_STORAGE_KEY, browserLocale, chooseLocale, directionOf, formatNumber, getLocale, initLocale,
  joinList, placeholdersOf, resolveLocale, savedLocaleChoice, setLocale, subscribeLocale, t, translate, type MessageKey,
} from '../src/lib/i18n.ts';
import { indexStateLabel, riskLabel, statusLabel, stepLabel } from '../src/lib/labels.ts';
import { deletedMessage, chatMemoryStatus } from '../src/lib/chatMemory.ts';
import { deletedSentence } from '../src/lib/privacy.ts';
import { approvalProgress, expiryLabel, joinNames } from '../src/lib/approvals.ts';
import type { Approval } from '@anum/contracts';

afterEach(() => setLocale('en'));

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    removeItem: (key: string) => { data.delete(key); },
  };
}

describe('catalogs', () => {
  const english = Object.keys(CATALOGS.en).sort();

  test('every locale has exactly the English keys', () => {
    for (const locale of LOCALES) assert.deepEqual(Object.keys(CATALOGS[locale]).sort(), english, locale);
  });

  test('every translation uses the same placeholders as English', () => {
    for (const locale of LOCALES) {
      for (const key of english as MessageKey[]) {
        assert.deepEqual(placeholdersOf(CATALOGS[locale][key]), placeholdersOf(CATALOGS.en[key]), `${locale} ${key}`);
      }
    }
  });

  test('no message is empty, and plural messages have an "other" form', () => {
    for (const locale of LOCALES) {
      for (const [key, message] of Object.entries(CATALOGS[locale])) {
        const forms = typeof message === 'string' ? [message] : Object.values(message);
        assert.ok(forms.every((form) => typeof form === 'string' && form.trim().length > 0), `${locale} ${key}`);
        if (typeof message !== 'string') assert.ok(message.other, `${locale} ${key} other`);
      }
    }
  });

  test('Arabic plural messages cover the CLDR categories one, two, few and many', () => {
    for (const [key, message] of Object.entries(CATALOGS.ar)) {
      if (typeof message === 'string') continue;
      for (const form of ['one', 'two', 'few', 'many', 'other'] as const) assert.ok(message[form], `ar ${key} ${form}`);
    }
  });

  test('a plural message in English is plural in Arabic, and the reverse', () => {
    for (const key of english as MessageKey[]) {
      assert.equal(typeof CATALOGS.ar[key], typeof CATALOGS.en[key], key);
    }
  });
});

describe('translate', () => {
  test('fills placeholders and formats numbers for the locale', () => {
    assert.equal(translate('en', 'shell.crumb', { view: 'Tasks' }), 'Workspace / Tasks');
    assert.equal(translate('en', 'memory.index.indexed', { count: 12345 }), '12,345 indexed');
    // Arabic keeps Western digits and separators (the latn numbering system), so counts read like ids.
    assert.equal(translate('ar', 'memory.index.indexed', { count: 12345 }), '12,345 مفهرسة');
  });

  test('picks English plural forms, with an explicit zero form', () => {
    assert.equal(translate('en', 'chatMemory.deleted', { count: 0 }), 'There were no memories from your chats to delete.');
    assert.equal(translate('en', 'chatMemory.deleted', { count: 1 }), 'Deleted 1 memory from your chats.');
    assert.equal(translate('en', 'chatMemory.deleted', { count: 4 }), 'Deleted 4 memories from your chats.');
  });

  test('picks every Arabic plural category', () => {
    const steps = (count: number) => translate('ar', 'tasks.trace.count', { count });
    assert.equal(steps(0), 'لا خطوات');
    assert.equal(steps(1), 'خطوة واحدة');
    assert.equal(steps(2), 'خطوتان');
    assert.equal(steps(3), '3 خطوات');
    assert.equal(steps(11), '11 خطوة');
    assert.equal(steps(100), '100 خطوة');
  });

  test('isolates Latin-script values inside right-to-left sentences, never numbers or Arabic text', () => {
    assert.equal(translate('ar', 'approvals.decidedBy', { verb: 'رُفضت', who: 'user_owner' }), 'رُفضت بواسطة \u2068user_owner\u2069');
    assert.equal(translate('ar', 'privacy.days', { count: 3 }), '3 أيام');
    assert.equal(translate('ar', 'approvals.decidedBy', { verb: 'رُفضت', who: 'سارة' }), 'رُفضت بواسطة سارة');
    assert.equal(translate('ar', 'approvals.decidedBy', { verb: 'x', who: 'user_owner' }, { isolate: false }), 'x بواسطة user_owner');
    assert.equal(translate('en', 'approvals.decidedBy', { verb: 'Rejected', who: 'user_owner' }), 'Rejected by user_owner');
  });

  test('leaves an unknown placeholder visible rather than dropping it', () => {
    assert.equal(translate('en', 'shell.crumb'), 'Workspace / {view}');
  });

  test('follows the current locale', () => {
    assert.equal(t('nav.tasks'), 'Tasks');
    setLocale('ar');
    assert.equal(t('nav.tasks'), 'المهام');
  });
});

describe('locale choice', () => {
  test('a saved choice wins over the browser languages', () => {
    assert.equal(resolveLocale('ar', ['en-US']), 'ar');
    assert.equal(resolveLocale('en', ['ar-SA']), 'en');
  });

  test('without a choice, the first supported browser language is used', () => {
    assert.equal(resolveLocale(null, ['fr-FR', 'ar-EG', 'en']), 'ar');
    assert.equal(resolveLocale(undefined, ['AR_sa']), 'ar');
    assert.equal(resolveLocale('de', ['de-DE', 'fr']), 'en');
    assert.equal(browserLocale([]), 'en');
  });

  test('direction follows the locale', () => {
    assert.equal(directionOf('ar'), 'rtl');
    assert.equal(directionOf('en'), 'ltr');
  });

  test('initLocale sets <html lang dir>, and choosing saves, switches and notifies', () => {
    const storage = fakeStorage();
    const root = { lang: '', dir: '' };
    assert.equal(initLocale({ storage, languages: ['en-GB'], root }), 'en');
    assert.deepEqual(root, { lang: 'en', dir: 'ltr' });
    assert.equal(savedLocaleChoice(), null);

    const seen: string[] = [];
    const off = subscribeLocale((locale) => seen.push(locale));
    chooseLocale('ar');
    assert.equal(storage.data.get(LOCALE_STORAGE_KEY), 'ar');
    assert.equal(getLocale(), 'ar');
    assert.deepEqual(root, { lang: 'ar', dir: 'rtl' });
    assert.equal(savedLocaleChoice(), 'ar');

    // "Match my browser" forgets the choice and goes back to the browser's language.
    chooseLocale(null);
    assert.equal(storage.data.has(LOCALE_STORAGE_KEY), false);
    assert.equal(getLocale(), 'en');
    assert.deepEqual(root, { lang: 'en', dir: 'ltr' });
    off();
    assert.deepEqual(seen, ['ar', 'en']);
  });

  test('a saved choice is restored, and storage that throws does not stop a switch', () => {
    const root = { lang: '', dir: '' };
    assert.equal(initLocale({ storage: fakeStorage({ [LOCALE_STORAGE_KEY]: 'ar' }), languages: ['en'], root }), 'ar');
    assert.equal(root.dir, 'rtl');
    const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); }, removeItem: () => { throw new Error('blocked'); } };
    assert.equal(initLocale({ storage: broken, languages: ['ar'], root }), 'ar');
    assert.equal(chooseLocale('en'), 'en');
    assert.equal(root.dir, 'ltr');
  });
});

describe('localized helpers', () => {
  test('lists join with the locale\'s conjunction', () => {
    assert.equal(joinList(['a', 'b', 'c']), 'a, b and c');
    assert.equal(joinNames(['a', 'b']), 'a and b');
    setLocale('ar');
    assert.equal(joinList(['أ', 'ب', 'ج']), 'أ، ب وج');
  });

  test('codes from the API get labels, and unknown codes still read', () => {
    assert.equal(statusLabel('waiting_approval'), 'waiting approval');
    assert.equal(stepLabel('model_call'), 'model call');
    assert.equal(statusLabel('brand_new_status'), 'brand new status');
    assert.equal(riskLabel('HIGH'), 'high');
    assert.equal(indexStateLabel('not_indexed'), 'not indexed');
    setLocale('ar');
    assert.equal(statusLabel('completed'), 'مكتملة');
    assert.equal(riskLabel('critical'), 'حرجة');
    assert.equal(statusLabel('brand_new_status'), 'brand new status');
  });

  test('pure helpers speak the current locale', () => {
    setLocale('ar');
    assert.equal(deletedMessage(2), 'حُذفت ذكريان من محادثاتك.');
    assert.equal(chatMemoryStatus({ conversation_memory: 'on' }).badge, 'مفعّلة');
    const zero = { conversations: 0, conversation_turns: 0, private_memories: 0, retrieval_index_entries: 0, voice_sessions: 0, voice_transcript_segments: 0, notification_preferences: 0 };
    assert.equal(
      deletedSentence({ deleted: { ...zero, conversations: 3 }, kept: { tasks_created: 1, approvals_decided: 0, shared_memories_created: 0, files_uploaded: 0 }, account_note: '', shared_note: '' }),
      'حُذفت 3 عناصر خاصة. يبقى سجل واحد مشترك في مساحة العمل.',
    );
    const approval = { id: 'a', taskId: 't', action: 'x', reason: '', riskLevel: 'high', status: 'pending', createdAt: '2026-01-01T00:00:00Z', expiresAt: '2026-01-01T02:30:00Z', requiredApprovals: 3, approvers: [{ userId: 'u1' }] } as unknown as Approval;
    assert.equal(expiryLabel(approval, new Date('2026-01-01T00:00:00Z')), 'تنتهي خلال 2 ساعة');
    assert.equal(approvalProgress(approval)?.label, '1 من 3 موافقات');
  });

  test('numbers format per locale', () => {
    assert.equal(formatNumber(1234.5, 'en'), '1,234.5');
    assert.equal(formatNumber(1234.5, 'ar'), '1,234.5');
  });
});
