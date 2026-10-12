// Team work, notifications, department assistants and customer organisations
// as the API sends them (docs/team-work.md, docs/customer-organisations.md).
import '../../data/api_models.dart';

const openStatuses = ['todo', 'in_progress', 'blocked'];
const workStatuses = ['todo', 'in_progress', 'blocked', 'done', 'cancelled'];
const workPriorities = ['urgent', 'high', 'normal', 'low'];

class ChecklistEntry {
  const ChecklistEntry({
    required this.id,
    required this.label,
    required this.done,
  });
  factory ChecklistEntry.fromJson(JsonReader r) => ChecklistEntry(
        id: r.string('id'),
        label: r.string('label'),
        done: r.boolean('done'),
      );
  final String id;
  final String label;
  final bool done;
}

class WorkComment {
  const WorkComment({
    required this.id,
    required this.authorType,
    required this.authorName,
    required this.body,
    required this.createdAt,
  });
  factory WorkComment.fromJson(JsonReader r) => WorkComment(
        id: r.string('id'),
        authorType: r.string('author_type'),
        authorName: r.optString('author_name') ?? r.string('author_id'),
        body: r.string('body'),
        createdAt: r.date('created_at'),
      );
  final String id;
  final String authorType;
  final String authorName;
  final String body;
  final DateTime createdAt;
}

class WorkItem {
  const WorkItem({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    required this.priority,
    required this.assigneeName,
    required this.assigneeType,
    required this.createdByName,
    required this.dueAt,
    required this.overdue,
    required this.version,
    required this.checklistDone,
    required this.checklistTotal,
    required this.commentCount,
    required this.canEdit,
    required this.source,
    this.checklist = const [],
    this.comments = const [],
  });

  factory WorkItem.fromJson(JsonReader r) => WorkItem(
        id: r.string('id'),
        title: r.string('title'),
        description: r.optString('description') ?? '',
        status: r.string('status'),
        priority: r.string('priority'),
        assigneeName: r.optString('assignee_name'),
        assigneeType: r.optString('assignee_type'),
        createdByName: r.optString('created_by_name') ?? r.string('created_by'),
        dueAt: r.optDate('due_at'),
        overdue: r.optBool('overdue') ?? false,
        version: r.integer('version'),
        checklistDone: r.optInt('checklist_done') ?? 0,
        checklistTotal: r.optInt('checklist_total') ?? 0,
        commentCount: r.optInt('comment_count') ?? 0,
        canEdit: r.optBool('can_edit') ?? false,
        source: r.optString('source') ?? 'manual',
        checklist: r.optObjects('checklist', ChecklistEntry.fromJson),
        comments: r.optObjects('comments', WorkComment.fromJson),
      );

  final String id;
  final String title;
  final String description;
  final String status;
  final String priority;
  final String? assigneeName;
  final String? assigneeType;
  final String createdByName;
  final DateTime? dueAt;
  final bool overdue;
  final int version;
  final int checklistDone;
  final int checklistTotal;
  final int commentCount;
  final bool canEdit;
  final String source;
  final List<ChecklistEntry> checklist;
  final List<WorkComment> comments;

  bool get isOpen => openStatuses.contains(status);
}

class WorkPage {
  const WorkPage({
    required this.items,
    required this.total,
    required this.nextOffset,
  });
  factory WorkPage.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return WorkPage(
      items: r.objects('items', WorkItem.fromJson),
      total: r.integer('total'),
      nextOffset: r.optInt('next_offset'),
    );
  }
  final List<WorkItem> items;
  final int total;
  final int? nextOffset;
}

class AssigneeChoice {
  const AssigneeChoice({
    required this.type,
    required this.id,
    required this.name,
  });
  factory AssigneeChoice.fromJson(JsonReader r) => AssigneeChoice(
        type: r.string('type'),
        id: r.string('id'),
        name: r.string('name'),
      );
  final String type;
  final String id;
  final String name;
  JsonMap toJson() => {'type': type, 'id': id};
}

class CommandPreview {
  const CommandPreview({
    required this.understood,
    required this.problem,
    required this.title,
    required this.dueAt,
    required this.assignee,
    required this.candidates,
  });
  factory CommandPreview.fromJson(JsonMap json) {
    final r = JsonReader(json);
    final assignee = r.optObject('assignee');
    return CommandPreview(
      understood: r.boolean('understood'),
      problem: r.optString('problem'),
      title: r.optString('title'),
      dueAt: r.optDate('due_at'),
      assignee: assignee == null ? null : AssigneeChoice.fromJson(assignee),
      candidates: r.optObjects('candidates', AssigneeChoice.fromJson),
    );
  }
  final bool understood;
  final String? problem;
  final String? title;
  final DateTime? dueAt;
  final AssigneeChoice? assignee;
  final List<AssigneeChoice> candidates;
}

class TeamNotification {
  const TeamNotification({
    required this.id,
    required this.title,
    required this.body,
    required this.workItemId,
    required this.read,
    required this.createdAt,
  });
  factory TeamNotification.fromJson(JsonReader r) => TeamNotification(
        id: r.string('id'),
        title: r.string('title'),
        body: r.string('body'),
        workItemId: r.optString('work_item_id'),
        read: r.optString('read_at') != null,
        createdAt: r.date('created_at'),
      );
  final String id;
  final String title;
  final String body;
  final String? workItemId;
  final bool read;
  final DateTime createdAt;
}

class NotificationPage {
  const NotificationPage({
    required this.items,
    required this.unread,
    required this.nextCursor,
  });
  factory NotificationPage.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return NotificationPage(
      items: r.objects('items', TeamNotification.fromJson),
      unread: r.integer('unread'),
      nextCursor: r.optString('next_cursor'),
    );
  }
  final List<TeamNotification> items;
  final int unread;
  final String? nextCursor;
}

class WorkloadRow {
  const WorkloadRow({
    required this.name,
    required this.assigneeType,
    required this.open,
    required this.overdue,
    required this.dueThisWeek,
    required this.loadPercent,
  });
  factory WorkloadRow.fromJson(JsonReader r) => WorkloadRow(
        name: r.string('name'),
        assigneeType: r.string('assignee_type'),
        open: r.integer('open'),
        overdue: r.integer('overdue'),
        dueThisWeek: r.integer('due_this_week'),
        loadPercent: r.optInt('load_percent'),
      );
  final String name;
  final String assigneeType;
  final int open;
  final int overdue;
  final int dueThisWeek;
  final int? loadPercent;
}

class Assistant {
  const Assistant({
    required this.id,
    required this.name,
    required this.department,
    required this.autonomy,
    required this.enabled,
    required this.tools,
  });
  factory Assistant.fromJson(JsonReader r) => Assistant(
        id: r.string('id'),
        name: r.string('name'),
        department: r.string('department'),
        autonomy: r.string('autonomy'),
        enabled: r.boolean('enabled'),
        tools: r.optStrings('allowed_tools') ?? const [],
      );
  final String id;
  final String name;
  final String department;
  final String autonomy;
  final bool enabled;
  final List<String> tools;
}

class OrganisationSummary {
  const OrganisationSummary({
    required this.tenantId,
    required this.displayName,
    required this.plan,
    required this.health,
    required this.activeMembers,
    required this.openWork,
    required this.overdueWork,
    required this.pendingInvitations,
  });
  factory OrganisationSummary.fromJson(JsonReader r) => OrganisationSummary(
        tenantId: r.string('tenant_id'),
        displayName: r.string('display_name'),
        plan: r.string('plan'),
        health: r.string('health'),
        activeMembers: r.integer('active_members'),
        openWork: r.integer('open_work_items'),
        overdueWork: r.integer('overdue_work_items'),
        pendingInvitations: r.integer('pending_invitations'),
      );
  final String tenantId;
  final String displayName;
  final String plan;
  final String health;
  final int activeMembers;
  final int openWork;
  final int overdueWork;
  final int pendingInvitations;
}

class CreatedOrganisation {
  const CreatedOrganisation({
    required this.organisation,
    required this.token,
    required this.workspaceId,
    required this.expiresAt,
  });
  factory CreatedOrganisation.fromJson(JsonMap json) {
    final r = JsonReader(json);
    return CreatedOrganisation(
      organisation: OrganisationSummary.fromJson(r.object('organisation')),
      token: r.string('invitation_token'),
      workspaceId: r.string('workspace_id'),
      expiresAt: r.date('invitation_expires_at'),
    );
  }
  final OrganisationSummary organisation;
  final String token;
  final String workspaceId;
  final DateTime expiresAt;
}

/// The same pattern the API enforces for a new organisation id.
final organisationIdPattern = RegExp(r'^[a-z0-9][a-z0-9_-]{2,79}$');
final emailPattern = RegExp(r'^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$');
