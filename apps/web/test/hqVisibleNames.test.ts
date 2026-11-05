// WCAG 2.5.3 (Label in Name, axe rule label-content-name-mismatch): a control's spoken name holds every word it shows, in the
// order it shows them (docs/web-localization.md#visible-text-in-the-spoken-name). The HQ cards, rail and people list, the label
// messages of short verb buttons in English and Arabic, and the rows named by their own text are checked here.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { afterEach, test } from 'node:test';
import { floorNameParts } from '../src/lib/hqLabelModel.ts';
import { setLocale, t, type Locale } from '../src/lib/i18n.ts';

const read = (file: string) => readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');

/** Words as axe compares them: letters and numbers only, parentheses dropped, lower case. */
function words(text: string): string[] {
  const plain = text.normalize('NFKD').replace(/\([^()]*\)/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').toLowerCase();
  return [...new Intl.Segmenter(undefined, { granularity: 'word' }).segment(plain)].filter((s) => s.isWordLike).map((s) => s.segment);
}

function nameHoldsText(name: string, shown: string): boolean {
  const n = words(name), s = words(shown);
  for (let i = 0; i + s.length <= n.length; i++) if (s.every((w, j) => n[i + j] === w)) return true;
  return false;
}

afterEach(() => setLocale('en'));

for (const locale of ['en', 'ar'] as const satisfies readonly Locale[]) {
  test(`${locale}: the floor tag says its room count, as the tag shows it`, () => {
    setLocale(locale);
    for (const [name, rooms] of [['Ground floor', 6], ['Level 2: Operations', 1], ['Rooftop', 2]] as const) {
      const parts = floorNameParts(name);
      const roomsText = t('hqLabels.floorTagRooms', { count: rooms });
      const shown = `${parts.short} ${parts.rest ? `${parts.rest} · ` : ''}${roomsText}`;
      const spoken = t('hqLabels.floorTagAria', { name, rooms: roomsText });
      assert.ok(nameHoldsText(spoken, shown), `${spoken} / ${shown}`);
    }
  });

  test(`${locale}: the whole-floor switch starts with the words on it`, () => {
    setLocale(locale);
    assert.ok(nameHoldsText(`${t('hqFloor.rail')}. ${t('hqFloor.railHint')}.`, t('hqFloor.rail')));
  });
}

test('the floor tag message names the floor, then its rooms, in both languages', () => {
  for (const locale of ['en', 'ar'] as const) {
    setLocale(locale);
    const spoken = t('hqLabels.floorTagAria', { name: 'Ground floor', rooms: t('hqLabels.floorTagRooms', { count: 3 }) });
    assert.ok(words(spoken).indexOf('ground') < words(spoken).indexOf('3'), spoken);
  }
});

test('counts and initials inside named HQ buttons are drawn from data attributes, not text', () => {
  const cards = read('HqLabelCards.tsx');
  assert.match(cards, /className="hqRoomWorking"[^>]*data-count=\{props\.working\} \/>/);
  assert.match(cards, /className="hqAvatar" aria-hidden="true" data-initials=/);
  assert.match(read('hq-nav/SidePanel.tsx'), /className="hqAvatar" aria-hidden="true" data-initials=/);
  assert.match(read('hqLabels.css'), /\.hqRoomWorking\[data-count\]::before \{ content: attr\(data-count\); \}/);
  assert.match(read('headquartersLive.css'), /\.hqAvatar\[data-initials\]::before \{ content: attr\(data-initials\); \}/);
});

test('the people list names the role and room it shows, and floor list items take their name from their text', () => {
  const panel = read('hq-nav/SidePanel.tsx');
  assert.match(panel, /aria-label=\{`\$\{t\('hqNav\.people\.open', \{ name \}\)\}\. \$\{where \? `\$\{where\}\. ` : ''\}\$\{state\}\.`\}/);
  const rail = read('hq-nav/FloorRail.tsx');
  const item = rail.slice(rail.indexOf('className="hqRailItem"'), rail.indexOf('</button>', rail.indexOf('className="hqRailItem"')));
  assert.doesNotMatch(item.split('>')[0], /aria-label=/);
  assert.match(item, /className="hqSpokenStop"/);
  assert.match(item, /slim \? 'hqRailText visuallyHidden' : 'hqRailText'/);
});

// Buttons and links whose aria-label names one item while their text is a short verb: the label starts from the words shown.
const LABELLED_VERBS: ReadonlyArray<readonly [label: string, shown: string, vars: Record<string, string>]> = [
  ['activity.tightenLabel', 'activity.tighten', { pattern: 'sending email' }],
  ['brevo.campaigns.reportOf', 'brevo.campaigns.report', { name: 'Spring offer' }],
  ['setup.guideFor', 'setup.guide', { name: 'Gmail' }],
  ['files.ver.keepOf', 'files.ver.keep', { version: '3' }],
  ['files.keepOf', 'files.keep', { name: 'notes.txt' }],
  ['hqFlow.showRoom', 'hqFlow.showInOffice', { room: 'Reception' }],
  ['mails.contacts.writeTo', 'mails.contacts.write', { name: 'Sara Example' }],
  ['pricing.startNamed', 'pricing.start', { plan: 'Business' }],
  ['hqProv.addLabel', 'hqProv.add', { role: 'Orders Desk' }],
  ['hqProv.reviewLabel', 'hqProv.review', { role: 'Orders Desk' }],
  ['emailHub.connections.settingsLabel', 'emailHub.connections.settings', { address: 'shop@example.com' }],
  ['emailHub.connections.openLabel', 'emailHub.connections.open', { address: 'shop@example.com' }],
  ['meetings.actions.createTaskOf', 'meetings.actions.createTask', { text: 'Send the report' }],
];

for (const locale of ['en', 'ar'] as const satisfies readonly Locale[]) {
  test(`${locale}: a labelled verb button's name holds the verb it shows`, () => {
    setLocale(locale);
    for (const [label, shown, vars] of LABELLED_VERBS) {
      const spoken = t(label as Parameters<typeof t>[0], vars);
      assert.ok(nameHoldsText(spoken, t(shown as Parameters<typeof t>[0])), `${label}: ${spoken}`);
    }
  });
}

test('initials and counts in named rows and chips are drawn from data attributes', () => {
  assert.match(read('AvatarStyleEditor.tsx'), /<span aria-hidden="true" data-initials=\{initialsOf\(name\)\} \/>/);
  assert.match(read('avatarStyle.css'), /\.avatarChip \[data-initials\]::before \{ content: attr\(data-initials\); \}/);
  assert.match(read('CallsLists.tsx'), /className="callsAvatar" aria-hidden="true" data-initials=/);
  assert.match(read('calls.css'), /\.callsAvatar\[data-initials\]::before \{ content: attr\(data-initials\); \}/);
  assert.match(read('EmailHub.tsx'), /className="hubCount" aria-hidden="true" data-count=/);
  assert.match(read('emailHub.css'), /\.hubCount\[data-count\]::before \{ content: attr\(data-count\); \}/);
});

test('the command orb and the call rows are named by their own text after a hidden verb', () => {
  const orb = read('CommandCenter.tsx');
  const button = orb.slice(orb.indexOf('className={`commandOrb'), orb.indexOf('</button>', orb.indexOf('className={`commandOrb')));
  assert.doesNotMatch(button.split('>')[0], /aria-label=/);
  assert.match(button, /<span className="visuallyHidden">\{listening \? t\('command\.voice\.stop'\) : t\('command\.voice\.start'\)\}\. <\/span>/);
  const calls = read('CallsLists.tsx');
  assert.equal((calls.match(/className="callsRow"[^>]*aria-label=/g) ?? []).length, 0);
  assert.match(calls, /\{t\('calls\.recents\.openHint'\)\} /);
  assert.match(calls, /\{t\('calls\.contacts\.openHint'\)\} /);
});

test('call controls start their name with the word under the icon, and the WhatsApp chip names its number', () => {
  const call = read('CallView.tsx');
  assert.match(call, /return shown\.every\(\(word\) => said\.includes\(word\)\) \? label : `\$\{short\}: \$\{label\}`;/);
  assert.match(call, /aria-label=\{controlName\(short, label\)\}/);
  assert.match(read('WhatsAppView.tsx'), /account\.display_phone_number \? ` \\u00B7 \$\{account\.display_phone_number\}` : ''/);
});

test('meeting and file rows are named by their text after a hidden verb, and calendar chips start with the time they show', () => {
  const meetings = read('MeetingsView.tsx');
  assert.doesNotMatch(meetings, /className="meetingRow"[^>]*aria-label=/);
  assert.match(meetings, /\{t\('meetings\.openHint'\)\} /);
  const files = read('FilesView.tsx');
  assert.doesNotMatch(files, /className="cellButton" aria-label=\{t\('files\.showDetails/);
  assert.match(files, /\{t\('files\.showDetailsHint'\)\} /);
  const marketing = read('MarketingView.tsx');
  assert.match(marketing, /aria-label=\{`\$\{chipTime\(post\)\}\. \$\{t\('marketing\.calendar\.chip'/);
  assert.doesNotMatch(marketing, /role="gridcell"[^>]*aria-label=/);
});
