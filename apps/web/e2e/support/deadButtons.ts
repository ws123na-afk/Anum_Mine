import type { Page } from '@playwright/test';
import { settle } from './gatePages';

// The dead-button audit for e2e/journey.spec.ts (CLAUDE.md, definition of done: no dead buttons).
// Every visible, enabled button in the page's main region is clicked; it must do something a person
// can see or that reaches the API: change the DOM (open a panel, dialog, menu, message or field
// error), change the URL, fire a request, open a file picker, a download, a popup or a native dialog,
// copy to the clipboard, or ask for the microphone or camera. A button that does none of these is a
// dead end and is reported by name. Rows of the same kind are sampled (three per class) so a long
// list does not dominate the run.

declare global {
  interface Window { __anumAudit?: { mutations: number; effects: number } }
}

/** Counts DOM changes and side effects that leave no DOM trace (clipboard, window.open, media). */
function instrument() {
  const audit = { mutations: 0, effects: 0 };
  window.__anumAudit = audit;
  const bump = () => { audit.effects += 1; };
  const start = () => new MutationObserver((list) => { audit.mutations += list.length; })
    .observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
  const wrap = <T extends object>(target: T | undefined, key: string) => {
    if (!target) return;
    const original = (target as Record<string, unknown>)[key];
    if (typeof original !== 'function') return;
    try {
      Object.defineProperty(target, key, { configurable: true, writable: true, value(this: unknown, ...args: unknown[]) { bump(); return (original as (...a: unknown[]) => unknown).apply(this, args); } });
    } catch { /* read-only in this browser */ }
  };
  wrap(window, 'open');
  wrap(window, 'print');
  wrap(HTMLAnchorElement.prototype, 'click');
  wrap(HTMLInputElement.prototype, 'click');
  wrap(HTMLInputElement.prototype, 'showPicker');
  wrap(HTMLMediaElement.prototype, 'play');
  wrap(URL, 'createObjectURL');
  wrap(navigator.clipboard, 'writeText');
  wrap(navigator.clipboard, 'write');
  wrap(navigator.clipboard, 'readText');
  wrap(navigator.mediaDevices, 'getUserMedia');
  wrap(window.speechSynthesis, 'speak');
  wrap(navigator as unknown as object, 'share');
}

interface Candidate { key: string; label: string }
interface Scan { list: Candidate[]; found: boolean; invalidSubmit: boolean }

/**
 * Lists the buttons to try, in document order, as stable keys (label, class and occurrence); with a
 * key, marks that button for Playwright to click and says whether field validation would block it.
 */
function scan({ key, shell }: { key: string | null; shell: boolean }): Scan {
  document.querySelectorAll('[data-anum-audit]').forEach((el) => el.removeAttribute('data-anum-audit'));
  const main = document.querySelector('main') ?? document.body;
  // A modal (a dialog, or a drawer such as an opened report) covers the page: only its buttons can be pressed.
  const modal = [...document.querySelectorAll<HTMLElement>('dialog:modal, [aria-modal="true"]')].find((el) => el.checkVisibility());
  const roots: Element[] = modal ? [modal] : shell ? [...document.querySelectorAll('.topbar, .sidebar')] : [main];
  const seen = new Map<string, number>();
  const perClass = new Map<string, number>();
  const list: Candidate[] = [];
  const all = roots.flatMap((root) => [...root.querySelectorAll<HTMLElement>('button, [role="button"], summary')]);
  for (const el of all) {
    // The shell's header sits inside main; it is audited once on its own (shell: true).
    if (!shell && !modal && el.closest('.topbar')) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 2 || box.height < 2 || !el.checkVisibility({ visibilityProperty: true, opacityProperty: true })) continue;
    if ((el as HTMLButtonElement).disabled || el.getAttribute('aria-disabled') === 'true' || el.closest('fieldset:disabled, [inert], [hidden]')) continue;
    // Draws on a canvas (the Voice page's shooting star): no DOM change to see, so not judged here.
    if (el.hasAttribute('data-draws')) continue;
    // Already the current choice: pressing it again rightly changes nothing.
    if (el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true' || el.hasAttribute('aria-current')) continue;
    const label = (el.getAttribute('aria-label') || el.innerText || el.textContent || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ').slice(0, 60);
    const cls = typeof el.className === 'string' ? el.className.trim() : '';
    const group = `${el.tagName}.${cls}`;
    const count = perClass.get(group) ?? 0;
    perClass.set(group, count + 1);
    if (cls && count >= 3) continue;
    const base = `${label}|${group}`;
    const index = seen.get(base) ?? 0;
    seen.set(base, index + 1);
    const id = `${base}#${index}`;
    if (key === null) { list.push({ key: id, label: label || `${el.tagName.toLowerCase()}.${cls}` }); continue; }
    if (id !== key) continue;
    el.setAttribute('data-anum-audit', '1');
    const form = (el as HTMLButtonElement).form ?? null;
    const submits = el.tagName === 'BUTTON' && (el as HTMLButtonElement).type === 'submit';
    return { list, found: true, invalidSubmit: Boolean(submits && form && !form.noValidate && !form.checkValidity()) };
  }
  return { list, found: false, invalidSubmit: false };
}

const counters = (page: Page) => page.evaluate(() => ({ mutations: window.__anumAudit?.mutations ?? 0, effects: window.__anumAudit?.effects ?? 0 }));

/**
 * Clicks every button on the page at `url` (opened fresh, with `ready` visible) and returns the
 * labels of the buttons that did nothing. `shell` audits the shared header and sidebar instead of
 * the page.
 */
export async function auditButtons(page: Page, url: string, ready: string, { shell = false }: { shell?: boolean } = {}): Promise<string[]> {
  let requests = 0;
  let events = 0;
  page.on('request', (request) => { if (request.url().startsWith('http://localhost:8000/') && !request.url().endsWith('/events/stream')) requests += 1; });
  page.on('dialog', (dialog) => { events += 1; void dialog.dismiss().catch(() => undefined); });
  page.on('filechooser', () => { events += 1; });
  page.on('popup', (popup) => { events += 1; void popup.close().catch(() => undefined); });
  page.on('download', () => { events += 1; });
  await page.addInitScript(instrument);
  const open = async () => {
    // A hash-only goto would keep the page as it is; start from a blank page so it really reloads.
    // Choices a click saved (a view mode, a dismissed hint) would change the next load: forget them.
    if (page.url().startsWith('http')) await page.evaluate(() => { try { localStorage.clear(); sessionStorage.clear(); } catch { /* storage blocked */ } }).catch(() => undefined);
    await page.goto('about:blank');
    await page.goto(url);
    await page.locator(ready).first().waitFor({ state: 'visible' });
    await settle(page);
  };
  await open();
  const start = page.url();
  const { list } = await page.evaluate(scan, { key: null, shell });
  const dead: string[] = [];
  let fresh = true;
  for (const candidate of list) {
    let target = await page.evaluate(scan, { key: candidate.key, shell });
    if (!target.found && !fresh) {
      await open();
      fresh = true;
      target = await page.evaluate(scan, { key: candidate.key, shell });
    }
    if (!target.found) continue; // only reachable after another action; covered by the journeys
    const before = { ...(await counters(page)), requests, events, url: page.url() };
    try {
      await page.locator('[data-anum-audit="1"]').click({ timeout: 3_000 });
    } catch {
      // Covered by something another click opened: start clean and try once more.
      await open();
      if (!(await page.evaluate(scan, { key: candidate.key, shell })).found) continue;
      try { await page.locator('[data-anum-audit="1"]').click({ timeout: 3_000 }); } catch (error) {
        dead.push(`"${candidate.label}" cannot be clicked: ${String(error).split('\n')[0]}`);
        continue;
      }
    }
    fresh = false;
    let acted = target.invalidSubmit;
    for (let waited = 0; !acted && waited < 1_500; waited += 100) {
      const now = page.url() === before.url ? await counters(page).catch(() => before) : before;
      acted = page.url() !== before.url || now.mutations > before.mutations || now.effects > before.effects || requests > before.requests || events > before.events;
      if (!acted) await page.waitForTimeout(100);
    }
    if (!acted) dead.push(`"${candidate.label}" does nothing`);
    // Back to the page as it opened when the click left it or covered it.
    const covered = await page.locator('dialog[open], [role="dialog"]:visible, [role="alertdialog"]:visible').count().catch(() => 1);
    if (page.url() !== start || covered > 0) { await open(); fresh = true; }
  }
  return dead;
}
