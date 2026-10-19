// Favorites, knowledge base and reports: the API's wire shapes (services/api/anum_api/
// favorites.py, knowledge_base.py, reports.py; docs/knowledge-base.md, docs/reports.md).

export type FavoriteType = 'conversation' | 'task' | 'agent' | 'file' | 'report' | 'memory' | 'automation' | 'collection';

export interface Favorite {
  id: string;
  item_type: FavoriteType;
  item_id: string;
  label: string;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type FavoritePage = Page<Favorite>;

export type Visibility = 'workspace' | 'private';

export interface KnowledgeCollection {
  id: string;
  name: string;
  description: string;
  visibility: Visibility;
  created_by: string;
  created_at: string;
  updated_at: string;
  item_count: number;
  can_manage: boolean;
}

export type KnowledgeItemKind = 'file' | 'note';
export type IndexState = 'indexed' | 'pending' | 'failed' | 'skipped';

export interface KnowledgeItem {
  id: string;
  collection_id: string;
  kind: KnowledgeItemKind;
  source_id: string;
  title: string;
  tags: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
  index: { state: IndexState; error: string | null; chunk_count: number };
  content_type: string | null;
  size_bytes: number | null;
  available: boolean;
  /** Notes only (GET /items/{id}, note create and update). */
  content?: string;
}

export interface KnowledgeItemPage extends Page<KnowledgeItem> {
  tags: string[];
}

export type SearchMode = 'hybrid' | 'keyword' | 'semantic';

export interface KnowledgeSearchHit {
  item: KnowledgeItem;
  collection_name: string;
  score: number;
  matched_by: ('keyword' | 'semantic')[];
  snippet: string | null;
}

export interface KnowledgeSearchResult {
  query: string;
  mode: SearchMode;
  hits: KnowledgeSearchHit[];
  semantic: 'ok' | 'no_index' | 'unavailable' | 'off';
}

export interface KnowledgeCitation {
  n: number;
  item_id: string;
  collection_id: string;
  title: string;
  kind: KnowledgeItemKind;
  provenance: 'file' | 'memory';
  source_id: string;
  chunk_index: number;
  score: number;
  snippet: string;
  /** PDF page, slide or sheet number of a document passage; null for notes and text files. */
  page?: number | null;
  page_unit?: 'page' | 'slide' | 'sheet' | null;
  /** The sheet name of a spreadsheet passage. */
  page_label?: string | null;
}

export interface KnowledgeAnswer {
  status: 'answered' | 'no_sources' | 'unavailable';
  answer: string;
  citations: KnowledgeCitation[];
  model: string | null;
}

export interface KnowledgeReindexResult {
  indexed: number;
  reused: number;
  failed: number;
  skipped: number;
  missing: number;
  items: KnowledgeItem[];
}

export type ReportCreatedByKind = 'user' | 'agent' | 'automation';

export interface ReportMetric {
  label: string;
  value: string;
  unit?: string | null;
  change_percent?: number | null;
}

export type ReportCell = string | number | null;

export interface ReportTable {
  columns: string[];
  rows: ReportCell[][];
}

export interface ReportSource {
  conversation_id: string | null;
  turn_id: string | null;
  automation_run_id: string | null;
}

export interface ReportSummary {
  id: string;
  type: string;
  title: string;
  period_start: string | null;
  period_end: string | null;
  summary: string;
  metrics: ReportMetric[];
  visibility: Visibility;
  created_by: string;
  created_by_kind: ReportCreatedByKind;
  source: ReportSource;
  row_count: number;
  created_at: string;
}

export interface Report extends Omit<ReportSummary, 'row_count'> {
  tenant_id: string;
  workspace_id: string;
  highlights: string[];
  data: ReportTable;
  body_markdown: string;
  owner_user_id: string | null;
  updated_at: string;
  can_manage: boolean;
}

export interface ReportPage extends Page<ReportSummary> {
  types: { type: string; count: number }[];
}

export interface ReportGenerator {
  type: 'task_summary' | 'automation_summary' | 'commerce_summary' | 'tyre_summary';
  title: string;
  description: string;
}

/** The card a structured chat answer shows ("View Full Report"). */
export interface ReportCard {
  report_id: string;
  turn_id: string | null;
  title: string;
  type: string;
  period_start: string | null;
  period_end: string | null;
  metrics: ReportMetric[];
  highlights: string[];
}

/** POST /api/v1/reports/{id}/share/email and /share/whatsapp (docs/reports.md#sharing). */
export interface ReportShareResult {
  channel: 'email' | 'whatsapp';
  status: 'awaiting_approval' | 'sent' | 'failed';
  approval: { id: string; payload_hash: string; [key: string]: unknown } | null;
  detail: string;
  draft_id: string | null;
  message_id: string | null;
}
