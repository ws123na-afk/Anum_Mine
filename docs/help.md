# In-app help

Short, task-based help articles for every module, shown inside the apps and working offline.

## Where people find it

- **Web**: the **?** button in the top bar opens the help panel (a side drawer). It lists every article, puts the article for the current page first under "For this page", and searches as you type.
- **Flutter**: Settings has a **?** button in its top bar that opens the Help screen: search, the article list and each article, with the same files.
- Every article ends with a link to the full documentation in this folder, and the panel links to this page when nothing matches a search.

## Articles

- One article per module, in `docs/help/<id>.md` (English) and `docs/help/<id>.ar.md` (Arabic). `<id>` is the web page id (`apps/web/src/lib/shell.ts`); tabs share their parent's article (Collections uses `knowledge`, Autonomy uses `activity`).
- Each article says what the module is, how to do its three main tasks, and who can do them (the roles the API enforces). Steps name the exact labels the web app shows; nothing describes a feature that does not exist.
- Format: a frontmatter block (`id`, `title`, `summary`, `keywords`, `docs`), then Markdown with headings, numbered and bulleted lists, bold text and links only. Links are relative to `docs/help` and must resolve (`pnpm docs:check`).
- No em or en dashes, like everything else people read.

The full list is in the [README documentation index](../README.md#documentation-index).

## How it is bundled

- **Web**: `apps/web/src/helpArticles.ts` imports `docs/help/*.md` with `import.meta.glob(..., { query: '?raw', eager: true })`, so the articles are part of the build. The help panel chunk loads only when someone opens help. Parsing, locale fallback (English when an Arabic file is missing), search and the page-to-article map are in `apps/web/src/lib/help.ts`.
- **Flutter**: the files are copied to `apps/mobile/assets/help/` by `node scripts/sync-help.mjs` and declared as assets. `apps/mobile/test/help/help_assets_test.dart` fails when the copies differ from `docs/help`, so run the script after editing an article.

## Search

Every word of the query must appear in the article. Title hits rank first, then keywords and summary, then the body. Arabic diacritics are ignored.

## Tests

- `apps/web/test/help.test.ts`: parsing, links, search, and that every page has an English and an Arabic article that parses and links to an existing doc.
- `apps/web/e2e/help.spec.ts`: open help from the top bar, search, read an article.
- `apps/mobile/test/help/`: the Help screen widget test and the asset copy check.

## Related

- [Sales demo workspace](demo-workspace.md)
- [Web localization](web-localization.md)
