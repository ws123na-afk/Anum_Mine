// Type scale guard (docs/design/visual-identity.md#type): a font-size is written once, in
// src/typography.css, and everything else uses var(--text-*). Migrate a stylesheet with
// `node scripts/type-scale-codemod.mjs src/<file>.css`, then delete it from PENDING.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';

const src = new URL('../src/', import.meta.url);

// Stylesheets still on raw sizes. Only ever shrinks; a new stylesheet must use the tokens.
const PENDING = new Set([
  'adminPages.css',
  'answer.css',
  'appCalls.css',
  'audit.css',
  'autonomy.css',
  'brevo.css',
  'calls.css',
  'connections.css',
  'dialog.css',
  'features.css',
  'files.css',
  'financeEquity.css',
  'financeReports.css',
  'governance.css',
  'help.css',
  'integrations.css',
  'learning.css',
  'lifecycle.css',
  'mailAi.css',
  'mailAutomation.css',
  'mailCenter.css',
  'mailDirectory.css',
  'mailOutgoing.css',
  'meetings.css',
  'message.css',
  'model-settings.css',
  'privacy.css',
  'space.css',
  'suppression.css',
  'tasks.css',
  'voiceActions.css',
  'workbench.css',
]);

// Raw sizes that are justified: none yet. Format 'file.css:selector fragment', with a reason.
const EXCEPTIONS: string[] = [];

const RAW = /font-size:\s*([^;}]*(?:\d(?:px|rem)|clamp\()[^;}]*)/g;

function rawSizes(file: string): string[] {
  return [...readFileSync(new URL(file, src), 'utf8').matchAll(RAW)].map((m) => m[1].trim());
}

test('stylesheets use the type tokens instead of raw font sizes', () => {
  const sheets = readdirSync(src).filter((f) => f.endsWith('.css') && f !== 'typography.css');
  assert.ok(sheets.length > 40, 'the stylesheets were found');
  const offenders = sheets.filter((f) => !PENDING.has(f)).flatMap((f) => rawSizes(f).map((v) => `${f}: font-size: ${v}`));
  assert.deepEqual(offenders, [], 'use var(--text-*) from src/typography.css (docs/design/visual-identity.md#type)');
  assert.deepEqual(EXCEPTIONS, [], 'no exceptions are recorded yet');
});

test('PENDING lists only stylesheets that still have raw sizes', () => {
  const stale = [...PENDING].filter((f) => rawSizes(f).length === 0);
  assert.deepEqual(stale, [], 'delete migrated stylesheets from PENDING');
});

test('type tokens: every step the codemod emits is defined', () => {
  const tokens = readFileSync(new URL('typography.css', src), 'utf8');
  for (const step of ['2xs', 'xs', 'sm', 'md', 'base', 'lg', 'xl', '2xl', '3xl', '4xl', '5xl', '6xl']) {
    assert.match(tokens, new RegExp(`--text-${step}:`), step);
  }
});

test('no component sets a pixel fontSize inline', () => {
  const walk = (dir: URL): URL[] => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(new URL(`${e.name}/`, dir)) : /\.tsx?$/.test(e.name) ? [new URL(e.name, dir)] : []));
  const bad = walk(src).filter((f) => /fontSize:\s*['"`]?\d/.test(readFileSync(f, 'utf8'))).map((f) => f.pathname);
  assert.deepEqual(bad, [], 'use a class and var(--text-*)');
});
