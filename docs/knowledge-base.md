# Knowledge base and favorites

The knowledge base organises what ANUM already stores and indexes, workspace files and
memories, into **collections** of documents and notes, adds tags, search by words and by
meaning, and answers questions with numbered, provenance-labelled citations. **Favorites**
are the items each person starred for quick access. Reports, which favorites also point
at, are described in [Reports](reports.md).

Code: `services/api/anum_api/knowledge_base.py`, `favorites.py`,
`db/library_repository.py`, migration `0042_library`; web `apps/web/src/KnowledgeView.tsx`,
`Favorites.tsx`; Flutter `apps/mobile/lib/features/library/`.

## Collections, documents and notes

| Thing | Stored as | Indexed by retrieval |
| --- | --- | --- |
| Collection | `kb_collections` row: name, description, owner (null = shared) | n/a |
| Document | a [workspace file](files.md) plus a `kb_items` row (`kind=file`) | text files and PDF, DOCX, XLSX, PPTX, ODT, ODS and ODP files, as for any upload ([Document text extraction](files.md#document-text-extraction)) |
| Note | a [memory](memory.md) (`source_type=knowledge_base`, retention indefinite) plus a `kb_items` row (`kind=note`) | yes, on create and on edit |

- A collection is **shared with the workspace** or **private to its creator**. A note in
  a private collection is a private memory (`owner_user_id` set), so only its owner can
  read it, find it in search, or have it retrieved for an answer, in the Memory view as
  much as here. A note in a shared collection is a shared memory without a task.
- A **document stays a workspace file** even in a private collection: members who can read
  Files see it there. The upload form says so before uploading into a private collection
  and points at notes for private text.
- An item copies its collection's owner, so `kb_items` carries the same owner rule as its
  collection.
- Deleting a collection deletes its notes (memories and their index rows); documents stay
  in Files. Removing a note item deletes the note; removing a document item keeps the file.
- An item whose file or note was deleted elsewhere lists as **Missing** (`available=false`)
  with a hint to remove it.

### Tenant isolation

`kb_collections` and `kb_items` are under forced RLS: the permissive policy checks
`anum.tenant_id` and `anum.workspace_id`, and a `RESTRICTIVE` policy hides rows whose
`owner_user_id` is set and is not `anum.user_id` (the same rule as `memories` since
migration 0021). The PostgreSQL store repeats both predicates on every statement.
`test_postgres_library.py` proves it with no predicate in the query.

## Search and "Ask the knowledge base"

`GET /api/v1/knowledge/search?q=&collection_id=&mode=hybrid|keyword|semantic`

- **Keyword**: titles, tags and note text of the items in the collections the caller can
  see.
- **Semantic**: the query is embedded with the workspace's embedder and matched against
  the retrieval index (pgvector), then kept only when the chunk belongs to an item of a
  visible collection. `semantic` in the response says `ok`, `no_index` (nothing indexed yet,
  so no embedding call is spent), `unavailable` (the embedding failed) or `off`.
- Hybrid ranks keyword and meaning matches together; a hit says which matched.

`POST /api/v1/knowledge/ask {"question", "collection_id"?}` retrieves up to six passages
from the visible collections, wraps each with `label_untrusted` (provenance `file` or
`memory`, origin `<source id>/chunk-<n>`) under the untrusted-data rules
([threat model](threat-model.md)), and asks the workspace model (fast route, budgeted) to
answer only from them and cite `[n]`. The answer comes back with `citations`: number,
item, collection, title, kind, provenance, chunk and a snippet. A passage from a PDF,
DOCX, XLSX, PPTX or OpenDocument file also carries where it is: `page` (the number), `page_unit` (`page`,
`slide` or `sheet`) and `page_label` (the sheet name). The model sees the same location
in each passage's heading (`[2] manual.pdf, page 3`), and the web shows it next to the
collection name ("page 3", "sheet 1 (Budget)"), and so does the Flutter app after the
title ("[2] manual.pdf, page 3"). A DOCX without page marks is cited by
file name alone. Snippets never include the location line. No passage, no model call:
`status=no_sources`. A failed model call returns `status=unavailable` with the citations
found. With the mock provider (local development) the answer is the best passages quoted
with their numbers.

## Index state and re-index

Each item shows its retrieval state: **Indexed**, **Not indexed yet**, **Index failed**
(often the model was unavailable, or a document took too long to read) or **Not searchable**
(a file type retrieval does not index, or a document that is password protected, damaged
or too complex to read safely; it is still found by title and tags). A scanned PDF with
no text layer shows **No extractable text, OCR not available** instead, on the web and in
Flutter: there is no OCR. `POST /collections/{id}/reindex` re-indexes
the collection's missing, failed or changed sources (unchanged ones are reused, so no
budget is spent on them) and returns the new states.

## Permissions

| Action | Who |
| --- | --- |
| Read collections, items, search | `memory:read` (members, owners, viewers) |
| Create a collection, add documents or notes, re-index | `memory:create` (members, owners) |
| Ask | `memory:read` and `task:create` (it spends model budget, like chat) |
| Rename or delete a collection | its creator, or a workspace owner for a shared one |
| Edit or remove an item | its author, or whoever may manage the collection (removing also needs `memory:delete`) |

A private collection is invisible to everyone else: its routes answer 404, not 403.

## API

| Route | |
| --- | --- |
| `GET/POST /api/v1/knowledge/collections` | list (with item counts), create |
| `GET/PATCH/DELETE /api/v1/knowledge/collections/{id}` | read, rename, delete |
| `GET /api/v1/knowledge/collections/{id}/items?q=&tag=&kind=&limit=&offset=` | paged items, with every tag used |
| `POST /api/v1/knowledge/collections/{id}/documents?name=` | upload (body = bytes; `X-Item-Tags`) |
| `POST /api/v1/knowledge/collections/{id}/files` | organise an existing workspace file |
| `POST /api/v1/knowledge/collections/{id}/notes` | write a note |
| `GET/PATCH/DELETE /api/v1/knowledge/items/{id}` | read (a note with its text), edit title/tags/text, remove |
| `POST /api/v1/knowledge/collections/{id}/reindex` | re-index |
| `GET /api/v1/knowledge/search`, `POST /api/v1/knowledge/ask` | see above |

Wire types: `packages/contracts/src/library.ts`.

## Favorites

A favorite stars a `conversation`, `task`, `agent` (installed skill), `file`, `report`,
`memory`, `automation` (workflow) or `collection` for one user in one workspace.

- Starring checks, server side, that the caller holds the read permission for that type
  and that the item exists and is visible to them (another user's conversation or private
  report answers 404). The label is the item's title at that moment.
- `favorites` rows are private to their user: forced RLS on tenant, workspace and
  `anum.user_id`, like conversations. Starring again returns the existing row (200).
  At most 500 per user.
- Deleting a report or collection removes the deleting user's star of it; stars other users
  kept read as "gone" when opened.
- **The person's own order.** `position` (migration `0173_favorites_position`, nullable,
  expand only) is where the person put the item. `sort=custom` orders by it, rows without a
  place last and newest first among themselves (so before anyone arranges anything it equals
  newest first). A move renumbers all of the caller's rows 0, 1, 2... in one transaction, so
  every device shows the same order and two moves never leave a tie; a new star goes first
  in an arranged list (one below the lowest place).

| Route | |
| --- | --- |
| `GET /api/v1/favorites?item_type=&q=&sort=&limit=&offset=` | newest first unless `sort` is `oldest`, `name` or `custom`; `q` matches the label (LIKE characters match as typed); with `total` |
| `POST /api/v1/favorites {"item_type","item_id"}` | star (201, or 200 if already starred) |
| `POST /api/v1/favorites/{id}/move {"before_id"} or {"after_id"}` | put it just before or after another of the caller's favorites; exactly one anchor (422 otherwise), 404 when either is not the caller's |
| `DELETE /api/v1/favorites?item_type=&item_id=` | unstar by item (204) |
| `DELETE /api/v1/favorites/{id}` | unstar by favorite id (the Favorites page uses it) |

### Where favorites appear

- **Web**: the Favorites page: search (waits for a pause in typing), sort (**My order**,
  newest, oldest, name), type filters, paging with the shared Pager, open, remove with
  **Undo**, and with **My order** a drag handle and **Move up** / **Move down** on each row
  (keyboard reachable; moving past the page edge reads the neighbour from the API). Other
  sorts turn arranging off and say how to turn it back on. Also a star on reports,
  collections and the open conversation, and the first starred items on the empty
  Assistant home. Opening a report or collection deep-links it (`#reports/<id>`,
  `#knowledge/<id>`); other types open their view.
- **Flutter**: the Menu drawer's Knowledge Base, Favorites and Reports entries, Home's
  "Quick access" card (the same three and the first four starred items in the person's own
  order), the Favorites screen, and a star on the report screen. The Favorites screen has
  the web's search (waits for a pause in typing; a search with no match says so with
  **Clear search**), sort (**My order** first, newest, oldest, name), type filters and
  **Show more** paging. With **My order** each row has a drag handle and an **Arrange**
  menu with **Move up** and **Move down** (off at the ends of the whole list, not only of
  the loaded rows), and screen readers get both as row actions; the move is shown at once,
  sent as `before_id` or `after_id`, announced ("Moved A before B"), then the loaded rows
  are read again, and a refused move puts the list back and says why. Moving past the last
  loaded row reads the neighbour from the API. Other sorts hide the handles and say how to
  arrange by hand. Tests: `apps/mobile/test/library_test.dart` and the journey
  `integration_test/journeys/notifications_favorites.dart`. The Knowledge Base screen's "Memories & files" action
  goes to Resources.

### Navigation

The web sidebar has one **Knowledge Base** entry with two tabs: **Memories** (`#memory`,
what ANUM remembers) and **Collections** (`#knowledge`, `#knowledge/<id>`, this page's
collections, search and Ask). `knowledge` is a tab of `memory` in `apps/web/src/lib/shell.ts`
(`TABS_OF`), so it lights up the same entry and stays out of the sidebar and the command
palette. **Favorites** is under More and **Reports** in the main group.

## Web page layout

The Knowledge Base follows the shared [page layout](design/anum360.md#page-layout). Both tabs open with a summary band from `GET /api/v1/retrieval/status`: memories and files indexed (of the sources of each type in the index), sources that failed to index, and the search passages with the embedding model (or how many sources need re-indexing).

- **Memories**: one toolbar row (search, All / From my chats / Task notes, the **Chat memory** status chip that opens the switch, Re-index and **Add memory**, which opens a dialog), then the memories in one table grouped by where they came from (from your chats, task notes, other notes) with their source, date, index state and delete.
- **Collections**: one row to ask or search the knowledge base with **New collection** (a dialog) at its end; the collection list next to the open collection, which shows its items table straight away (title and kind, tags, index state, size, date) with filters, paging and **Add** (upload, note or existing file, in a dialog), Re-index, Edit (a dialog), Delete and the star. With no collection yet, the create form is the page's empty state.

## States the clients handle

Loading; empty with the next action (create a collection, add a note, star something);
errors that name what failed with Try again; permission denied that says who can do it;
long lists paged ("Show more", or Previous and Next for favorites) and filtered (title, tag,
kind; type, name search and sort for favorites).
English and Arabic, right to left. Covered by `services/api/tests/test_library.py`,
`test_postgres_library.py`, `apps/web/test/library.test.ts`, `apps/web/e2e/library.spec.ts`
(and the axe gate in `ui-gate.spec.ts`) and `apps/mobile/test/library_test.dart`.

## Not in this change

- **Import from Google Drive or OneDrive**: no such connector exists yet. When one lands
  it should create workspace files through the Files API and link them with
  `POST /collections/{id}/files`, so indexing and permissions stay as above.
- **Uploading a document from the phone into a collection**: the Flutter app writes notes
  and lists, searches and asks; documents are uploaded on the web (or in Files) for now.
- Changing a collection between shared and private after creation (its notes' memory
  ownership would have to move with it).

### The knowledge page

The Collections tab leads with five figures from `GET /retrieval/status` (total sources, indexed, stale, indexing failed, total chunks with the skipped count) and, when some sources are stale or failed, a banner saying what that means for answers and how to fix it (Re-index on the item). Not built from the design mockup: SharePoint and database sources, a parse and deduplication preview, approval before publishing a source, per-source assistant access and source version history, because the knowledge base has no such features.

