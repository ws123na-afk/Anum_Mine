import { expect, test } from '@playwright/test';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { mockHq } from './support/hqCampusApi';
import { reportLooksAnonymous } from '../src/lib/hqGpuReport';

// scripts/hq-gpu-check.mjs, the owner-run check, driven against the mocked office here. In this sandbox and in CI the browser draws
// in software, so the script must say so, and what it saves is verified for shape and anonymity. A real GPU run is the owner's.
type Script = { runCheck(page: unknown, o: Record<string, unknown>, log: (line: string) => void): Promise<{ software: boolean; summary: Record<string, unknown> }>; parseArgs(argv: string[], env?: Record<string, string>): Record<string, unknown>; GPU_ARGS: string[] };
const load = async (): Promise<Script> => (await import(pathToFileURL(path.resolve(import.meta.dirname, '../../../scripts/hq-gpu-check.mjs')).href)) as Script;

test.use({ viewport: { width: 1440, height: 900 } });

test('the owner script parses its options and refuses a bad address', async () => {
  const { parseArgs, GPU_ARGS } = await load();
  expect(GPU_ARGS).toEqual(['--use-angle=default', '--enable-gpu-rasterization', '--ignore-gpu-blocklist']);
  expect(parseArgs(['--url', 'https://app.example.com', '--length', '2', '--login'], {})).toMatchObject({ origin: 'https://app.example.com', length: 2, login: true, channel: 'chrome' });
  expect(parseArgs([], { ANUM_HQ_URL: 'http://127.0.0.1:4173' })).toMatchObject({ origin: 'http://127.0.0.1:4173' });
  expect(() => parseArgs(['--url', 'ftp://example.com'])).toThrow(/http/);
  expect(() => parseArgs(['--url', 'nonsense'])).toThrow(/full address/);
  expect(() => parseArgs(['--length', '3'])).toThrow(/2, 5 or 10/);
  expect(() => parseArgs(['--password', 'x'])).toThrow(/Unknown option/);
});

test('the owner script runs the check, saves reports and screenshots, and says plainly that only software was found', async ({ page }) => {
  test.setTimeout(420_000);
  const out = mkdtempSync(path.join(tmpdir(), 'hq-gpu-check-'));
  try {
    await page.addInitScript(() => { try { localStorage.setItem('anum.locale', 'en'); localStorage.setItem('anum.hq.light', 'day'); } catch { /* storage blocked */ } });
    await mockHq(page, { campus: 'api', commerce: 'connected', meetings: 'live' });
    await page.goto('/#office-preview');
    const { runCheck } = await load();
    const lines: string[] = [];
    const { software, summary } = await runCheck(page, { out, length: 2 }, (line) => lines.push(line));
    expect(software).toBe(true);
    expect(lines.join('\n')).toContain('ONLY SOFTWARE GRAPHICS WERE FOUND');
    expect(lines.join('\n')).toContain('NOT your graphics card');
    const files = readdirSync(out).sort();
    for (const f of ['campus-day.png', 'campus-dusk.png', 'campus-night.png', 'report-campus.json', 'summary.json', 'panel-campus.png']) expect(files).toContain(f);
    const report = JSON.parse(readFileSync(path.join(out, 'report-campus.json'), 'utf8'));
    expect(report.schema).toBe('anum.hq.gpu-report/1');
    expect(report.tiers.length).toBeGreaterThanOrEqual(4);
    expect(report.verdict.kind).toBe('software');
    for (const f of files.filter((n) => n.endsWith('.json'))) expect(reportLooksAnonymous(readFileSync(path.join(out, f), 'utf8'))).toBe(true);
    expect(summary.softwareOnly).toBe(true);
    if (files.includes('building-cutaway-day.png')) expect(files).toContain('report-building.json');
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
});
