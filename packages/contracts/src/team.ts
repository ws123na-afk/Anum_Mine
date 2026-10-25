// Team work, department assistants, notifications and customer organisations, as the API
// sends them (snake_case wire shapes, like account.ts). Routes and rules:
// docs/team-work.md and docs/customer-organisations.md; server models in
// services/api/anum_api/team_models.py and organisations.py.

// --------------------------------------------------------------------------- work items

export type WorkStatus = 'todo' | 'in_progress' | 'blocked' | 'done' | 'cancelled';
export type WorkPriority = 'low' | 'normal' | 'high' | 'urgent';
/** `anum` is ANUM itself (its id is always "anum"); `assistant` a department assistant. */
export type AssigneeType = 'user' | 'assistant' | 'anum';
export type ActorType = AssigneeType;
export type WorkSource = 'manual' | 'command' | 'inbound' | 'follow_up';
export type Department =
  | 'sales' | 'support' | 'operations' | 'finance' | 'tyre_engineering'
  | 'marketing' | 'technology' | 'research' | 'legal' | 'compliance' | 'security' | 'hr' | 'procurement'
  | 'custom';
export type Autonomy = 'observe' | 'suggest' | 'act_low_risk';

export interface WorkAssignee { type: AssigneeType; id: string }

export interface ChecklistEntry {
  id: string;
  label: string;
  done: boolean;
  position: number;
  done_by: string | null;
  done_at: string | null;
  created_at: string;
}

export interface WorkComment {
  id: string;
  author_type: ActorType;
  author_id: string;
  author_name: string | null;
  body: string;
  created_at: string;
}

export interface WorkAttachment {
  id: string;
  file_id: string;
  file_name: string;
  added_by: string;
  created_at: string;
  /** False when the file was deleted from Files after it was attached. */
  available: boolean;
}

export interface WorkItem {
  id: string;
  tenant_id: string;
  workspace_id: string;
  title: string;
  description: string;
  status: WorkStatus;
  priority: WorkPriority;
  assignee_type: AssigneeType | null;
  assignee_id: string | null;
  assignee_name: string | null;
  created_by: string;
  created_by_name: string | null;
  due_at: string | null;
  follow_up_at: string | null;
  completed_at: string | null;
  reminder_sent_at: string | null;
  estimate_hours: number | null;
  source: WorkSource;
  source_detail: Record<string, unknown>;
  agent_task_id: string | null;
  /** Send it back on PATCH; a stale version answers 409. */
  version: number;
  created_at: string;
  updated_at: string;
  overdue: boolean;
  checklist_total: number;
  checklist_done: number;
  comment_count: number;
  attachment_count: number;
  /** What the server will allow this caller (the server still checks every change). */
  can_edit: boolean;
  can_delete: boolean;
}

export interface WorkItemDetail extends WorkItem {
  checklist: ChecklistEntry[];
  comments: WorkComment[];
  attachments: WorkAttachment[];
}

export interface WorkItemPage { items: WorkItem[]; total: number; limit: number; offset: number; next_offset: number | null }

export interface WorkItemCreateBody {
  title: string;
  description?: string;
  priority?: WorkPriority;
  assignee?: WorkAssignee | null;
  /** ISO 8601 with an offset. */
  due_at?: string | null;
  follow_up_at?: string | null;
  estimate_hours?: number | null;
  checklist?: string[];
}

export interface WorkItemUpdateBody {
  version: number;
  title?: string;
  description?: string;
  status?: WorkStatus;
  priority?: WorkPriority;
  assignee?: WorkAssignee;
  clear_assignee?: boolean;
  due_at?: string;
  clear_due?: boolean;
  follow_up_at?: string;
  clear_follow_up?: boolean;
  estimate_hours?: number;
}

export interface TeamPerson {
  user_id: string;
  role: string;
  display_name: string;
  has_profile: boolean;
  email: string | null;
  department: Department | null;
  job_title: string | null;
  capacity_hours: number | null;
  timezone: string;
  assignable: boolean;
}

export interface MemberProfileBody {
  display_name: string;
  email?: string | null;
  department?: Department | null;
  job_title?: string | null;
  capacity_hours?: number | null;
  timezone?: string;
}

export interface WorkloadRow {
  assignee_type: AssigneeType;
  assignee_id: string;
  name: string;
  open: number;
  in_progress: number;
  blocked: number;
  overdue: number;
  due_this_week: number;
  estimate_hours: number;
  capacity_hours: number | null;
  load_percent: number | null;
}

export interface Workload { rows: WorkloadRow[]; unassigned_open: number; generated_at: string }

export interface ActivityEntry {
  id: string;
  work_item_id: string | null;
  actor_type: ActorType;
  actor_id: string;
  actor_name: string | null;
  action: string;
  summary: string;
  created_at: string;
}

export interface ActivityPage { items: ActivityEntry[]; next_cursor: string | null }

export interface CommandCandidate { type: AssigneeType; id: string; name: string }

export interface CommandPreview {
  understood: boolean;
  problem: string | null;
  person: string | null;
  title: string | null;
  due_at: string | null;
  due_text: string | null;
  candidates: CommandCandidate[];
  assignee: CommandCandidate | null;
}

// --------------------------------------------------------------------------- notifications

export type EmailStatus = 'not_requested' | 'pending' | 'sent' | 'failed' | 'not_configured';
export type PushStatus = 'not_requested' | 'not_configured' | 'queued' | 'sent' | 'failed';

export interface UserNotification {
  id: string;
  user_id: string;
  kind: string;
  title: string;
  body: string;
  work_item_id: string | null;
  read_at: string | null;
  email_status: EmailStatus;
  email_attempts: number;
  email_error: string | null;
  push_status: PushStatus;
  created_at: string;
}

export interface NotificationPage { items: UserNotification[]; unread: number; next_cursor: string | null }

// --------------------------------------------------------------------------- assistants

export type AutoRouteChannel = 'email' | 'whatsapp';

export interface AssistantProfile {
  id: string;
  department: Department;
  name: string;
  instructions: string;
  allowed_tools: string[];
  routing_keywords: string[];
  /** Channels whose new inbound messages reach this assistant on arrival (keyword match required). */
  inbound_channels: AutoRouteChannel[];
  autonomy: Autonomy;
  enabled: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AssistantBody {
  department: Department;
  name: string;
  instructions: string;
  allowed_tools: string[];
  routing_keywords: string[];
  /** Omitted: the stored channels are kept. */
  inbound_channels?: AutoRouteChannel[];
  autonomy: Autonomy;
  enabled: boolean;
}

export type AssistantTemplate = Omit<AssistantBody, 'enabled'>;

export type InboundChannel = 'email' | 'whatsapp' | 'marketplace' | 'web' | 'phone' | 'other';

export interface InboundMessageBody { channel: InboundChannel; sender: string; subject?: string; body: string; assistant_id?: string | null }

export interface RouteResult { assistant: AssistantProfile | null; matched_keywords: string[]; work_item: WorkItem }

// --------------------------------------------------------------------------- organisations

export interface OrganisationBranding {
  tenant_id: string;
  display_name: string;
  has_logo: boolean;
  logo_content_type: string | null;
  updated_at: string | null;
}

export interface MyOrganisation {
  tenant_id: string;
  name: string | null;
  display_name: string | null;
  current: boolean;
  workspaces: { workspace_id: string; workspace_name: string | null; role: string }[];
}

export type OrganisationPlan = 'trial' | 'standard' | 'enterprise';
export type OrganisationHealth = 'setting_up' | 'active' | 'idle' | 'attention';

/** Counts and health only: the console never reads an organisation's data. */
export interface OrganisationSummary {
  tenant_id: string;
  display_name: string;
  plan: OrganisationPlan;
  first_workspace_id: string;
  created_at: string;
  workspaces: number;
  active_members: number;
  pending_invitations: number;
  open_agent_tasks: number;
  open_work_items: number;
  overdue_work_items: number;
  last_activity_at: string | null;
  health: OrganisationHealth;
}

export interface OrganisationCreateBody {
  tenant_id: string;
  display_name: string;
  workspace_name: string;
  first_admin_email: string;
  plan: OrganisationPlan;
}

export interface CreatedOrganisation {
  organisation: OrganisationSummary;
  invitation_id: string;
  /** Single-use owner invitation for the first admin; shown once. */
  invitation_token: string;
  invitation_expires_at: string;
  workspace_id: string;
}

export interface ReissuedInvitation { invitation_id: string; invitation_token: string; invitation_expires_at: string; workspace_id: string }
