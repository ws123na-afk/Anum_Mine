import assert from 'node:assert/strict';
import { afterEach, describe, test } from 'node:test';
import { setLocale } from '../src/lib/i18n.ts';
import {
  checkDraft, composeText, dayKey, fromLocalInput, monthGrid, moveToDay, oauthCallback, parseHashtags, postActions, postsByDay, rate, change,
  postsPath, slotLabel, statusName, statusTone, toLocalInput, withUtm, xWeightedLength, type PlatformLimits,
} from '../src/lib/marketing.ts';

afterEach(() => setLocale('en'));

const instagram: PlatformLimits = {
  platform: 'instagram', name: 'Instagram', text_limit: 2200, counting: 'chars', hashtag_limit: 30, hashtag_recommended: 10, requires_media: true,
  requires_video: false, requires_title: false, title_limit: null, max_images: 10, max_videos: 1, image_types: ['image/jpeg'], video_types: ['video/mp4'],
  image_ratio: [0.8, 1.91], video_ratio: null, video_seconds: null, notes: [], review: '',
};
const x: PlatformLimits = { ...instagram, platform: 'x', name: 'X', text_limit: 280, counting: 'x_weighted', hashtag_limit: null, hashtag_recommended: 2, requires_media: false, max_images: 4, image_types: ['image/jpeg', 'image/png'] };

describe('composing and limits', () => {
  test('hashtags are parsed once each, with or without #, in English and Arabic', () => {
    assert.deepEqual(parseHashtags('#Tyres tyres, KSA،#إطارات'), ['Tyres', 'KSA', 'إطارات']);
  });

  test('the composed text adds missing hashtags and the link, like the API', () => {
    assert.equal(composeText('New stock #Tyres', ['tyres', 'KSA'], 'https://shop.example'), 'New stock #Tyres\n\n#KSA\nhttps://shop.example');
    assert.equal(composeText('', ['A'], null), '#A');
  });

  test('X counts links as 23 and wide characters as 2', () => {
    assert.equal(xWeightedLength('hi https://example.com/a/very/long/path'), 3 + 23);
    assert.equal(xWeightedLength('مرحبا'), 5);
    assert.equal(xWeightedLength('中文'), 4);
  });

  test('draft checks name what to fix for each platform', () => {
    const needsMedia = checkDraft({ text: 'Hello', title: '', hashtags: [], link: null, media: [] }, instagram);
    assert.deepEqual(needsMedia.issues.map((issue) => issue.code), ['media_required']);
    const png = checkDraft({ text: 'Hello', title: '', hashtags: [], link: null, media: [{ kind: 'image', content_type: 'image/png' }] }, instagram);
    assert.match(png.issues[0].message, /JPEG/);
    const long = checkDraft({ text: 'a'.repeat(270), title: '', hashtags: ['one', 'two', 'three'], link: null, media: [] }, x);
    assert.equal(long.length, 270 + 2 + 'one #two #three'.length + 1);
    assert.deepEqual(long.issues.map((issue) => issue.code), ['too_long', 'many_hashtags']);
  });
});

describe('links', () => {
  test('UTM tracking for websites, Google Play and the App Store', () => {
    assert.equal(withUtm('https://tyrepulse.example/?a=1', 'instagram', 'summer'), 'https://tyrepulse.example/?a=1&utm_source=instagram&utm_medium=social&utm_campaign=summer');
    const play = new URL(withUtm('https://play.google.com/store/apps/details?id=com.tyrepulse', 'x', 'launch'));
    assert.equal(play.searchParams.get('referrer'), 'utm_source=x&utm_medium=social&utm_campaign=launch');
    assert.equal(new URL(withUtm('https://apps.apple.com/sa/app/id1', 'tiktok', 'launch')).searchParams.get('ct'), 'launch-tiktok');
    assert.throws(() => withUtm('ftp://example.com', 'x'));
  });

  test('the OAuth callback is recognised only with an ANUM state', () => {
    assert.deepEqual(oauthCallback('?code=abc&state=mkt.xyz'), { code: 'abc', state: 'mkt.xyz' });
    assert.deepEqual(oauthCallback('?error=access_denied&state=mkt.xyz'), { error: 'access_denied' });
    assert.equal(oauthCallback('?code=abc&state=other'), null);
    assert.equal(oauthCallback(''), null);
  });

  test('post list paths carry filters and the cursor', () => {
    assert.equal(postsPath({ status: 'failed', platform: 'x', query: ' tyres ', before: 'mkpost_1', limit: 20 }), '/api/v1/marketing/posts?limit=20&status=failed&platform=x&q=tyres&before=mkpost_1');
  });
});

describe('calendar', () => {
  test('a month starts on Sunday and covers every day', () => {
    const weeks = monthGrid(2026, 9); // October 2026 starts on a Thursday
    assert.equal(weeks[0][0].getDay(), 0);
    assert.equal(dayKey(weeks[0][4]), '2026-10-01');
    assert.ok(weeks.flat().some((day) => dayKey(day) === '2026-10-31'));
  });

  test('moving a post keeps its time of day and never lands in the past', () => {
    const moved = moveToDay(new Date(2026, 9, 12, 21, 30).toISOString(), new Date(2026, 9, 15), new Date(2026, 9, 1));
    assert.equal(dayKey(moved), '2026-10-15');
    assert.equal(moved.getHours(), 21);
    const now = new Date(2026, 9, 20, 12);
    assert.ok(moveToDay(new Date(2026, 9, 12, 9).toISOString(), new Date(2026, 9, 20), now).getTime() > now.getTime());
  });

  test('posts group by their local day; datetime-local values round-trip', () => {
    const at = new Date(2026, 9, 12, 20, 0).toISOString();
    assert.deepEqual([...postsByDay([{ scheduled_at: at, published_at: null }, { scheduled_at: null, published_at: null }])], [['2026-10-12', [0]]]);
    assert.equal(fromLocalInput(toLocalInput(at)), at);
    assert.equal(fromLocalInput(''), null);
  });
});

describe('labels and actions', () => {
  test('statuses read in both languages and have tones', () => {
    assert.equal(statusName('pending_approval'), 'waiting for approval');
    assert.equal(statusTone('failed'), 'dangerTone');
    setLocale('ar');
    assert.equal(statusName('published'), 'منشور');
    assert.equal(slotLabel({ weekday: 6, hour: 21 }), 'الأحد 21:00');
  });

  test('what can be done with a post follows its status and the caller', () => {
    assert.deepEqual(postActions({ status: 'scheduled', approved_hash: 'h' }, true), { edit: true, schedule: false, cancel: true, publishNow: true, retry: false, remove: true });
    assert.equal(postActions({ status: 'failed', approved_hash: null }, true).retry, false);
    assert.deepEqual(Object.values(postActions({ status: 'draft', approved_hash: null }, false)), [false, false, false, false, false, false]);
  });

  test('rates and changes', () => {
    assert.equal(rate(0.0812), '8.1%');
    assert.equal(rate(null), '–');
    assert.equal(change(0.1, 0.08), '+2.0');
    assert.equal(change(0.05, 0.08), '−3.0');
  });
});
