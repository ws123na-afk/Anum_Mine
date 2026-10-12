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
| Document | a [workspace file](files.md) plus a `kb_items` row (`kind=file`) | text files, as for any upload |
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
item, collection, title, kind, provenance, chunk and a snippet. No passage, no model call:
`status=no_sources`. A failed model call returns `status=unavailable` with the citations
found. With the mock provider (local development) the answer is the best passages quoted
with their numbers.

## Index state and re-index

Each item shows its retrieval state: **Indexed**, **Not indexed yet**, **Index failed**
(often the model was unavailable) or **Not searchable** (a file type retrieval does not
index; it is still found by title and tags). `POST /collections/{id}/reindex` re-indexes
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

| Route | |
| --- | --- |
| `GET /api/v1/favorites?item_type=&limit=&offset=` | newest first, with `total` |
| `POST /api/v1/favorites {"item_type","item_id"}` | star (201, or 200 if already starred) |
| `DELETE /api/v1/favorites?item_type=&item_id=` | unstar by item (204) |
| `DELETE /api/v1/favorites/{id}` | unstar by favorite id |

### Where favorites appear

- **Web**: the Favorites page (type filters, paging, open, remove), a star on reports,
  collections and the open conversation, and the first starred items on the empty
  Assistant home. Opening a report or collection deep-links it (`#reports/<id>`,
  `#knowledge/<id>`); other types open their view.
- **Flutter**: the Menu drawer's Knowledge Base, Favorites and Reports entries, Home's
  "Quick access" card (the same three and the first starred items), the Favorites screen,
  and a star on the report screen. The Knowledge Base screen's "Memories & files" action
  goes to Resources.

### Navigation

The web sidebar has one **Knowledge Base** entry with two tabs: **Memories** (`#memory`,
what ANUM remembers) and **Collections** (`#knowledge`, `#knowledge/<id>`, this page's
collections, search and Ask). `knowledge` is a tab of `memory` in `apps/web/src/lib/shell.ts`
(`TABS_OF`), so it lights up the same entry and stays out of the sidebar and the command
palette. **Favorites** is under More and **Reports** in the main group.

## States the clients handle

Loading; empty with the next action (create a collection, add a note, star something);
errors that name what failed with Try again; permission denied that says who can do it;
long lists paged ("Show more") and filtered (title, tag, kind; type for favorites).
English and Arabic, right to left. Covered by `services/api/tests/test_library.py`,
`test_postgres_library.py`, `apps/web/test/library.test.ts`, `apps/web/e2e/library.spec.ts`
(and the axe gate in `accessibility.spec.ts`) and `apps/mobile/test/library_test.dart`.

## Not in this change

- **Import from Google Drive or OneDrive**: no such connector exists yet. When one lands
  it should create workspace files through the Files API and link them with
  `POST /collections/{id}/files`, so indexing and permissions stay as above.
- **Uploading a document from the phone into a collection**: the Flutter app writes notes
  and lists, searches and asks; documents are uploaded on the web (or in Files) for now.
- Changing a collection between shared and private after creation (its notes' memory
  ownership would have to move with it).
