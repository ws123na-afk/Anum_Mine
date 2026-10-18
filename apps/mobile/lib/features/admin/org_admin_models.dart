import '../../data/json_read.dart';

// Wire types for the organization admin screens: the contact suppression
// list (docs/privacy.md#contact-suppression), the workspace lifecycle
// (docs/identity.md#workspace-lifecycle), setup plans (docs/setup-plan.md)
// and the AI team org chart (docs/org-chart.md).

// --------------------------------------------------------------- suppression

const suppressionChannels = ['all', 'whatsapp', 'email', 'sms'];
const suppressionPurposes = ['all', 'marketing'];
const suppressionSystemActor = 'system:inbound';

class SuppressionEntry {
  const SuppressionEntry({
    required this.id,
    required this.masked,
    required this.kind,
    required this.channel,
    required this.purpose,
    required this.source,
    required this.reason,
    required this.createdBy,
    required this.createdAt,
  });

  factory SuppressionEntry.fromJson(JsonReader r) => SuppressionEntry(
        id: r.string('id'),
        masked: r.string('masked'),
        kind: r.string('kind'),
        channel: r.string('channel'),
        purpose: r.string('purpose'),
        source: r.string('source'),
        reason: r.optString('reason') ?? '',
        createdBy: r.string('created_by'),
        createdAt: r.date('created_at'),
      );

  final String id, masked, kind, channel, purpose, source, reason, createdBy;
  final DateTime createdAt;
}

class SuppressionPage {
  const SuppressionPage(
      {required this.items,
      required this.total,
      required this.canManage,
      required this.canExport});

  factory SuppressionPage.fromJson(JsonReader r) => SuppressionPage(
        items: r.objects('items', SuppressionEntry.fromJson),
        total: r.integer('total'),
        canManage: r.optBool('can_manage') ?? false,
        canExport: r.optBool('can_export') ?? false,
      );

  final List<SuppressionEntry> items;
  final int total;
  final bool canManage, canExport;
}

class SuppressionDraft {
  const SuppressionDraft({
    required this.recipient,
    this.channel = 'all',
    this.purpose = 'all',
    required this.reason,
  });
  final String recipient, channel, purpose, reason;

  Map<String, Object?> toJson() => {
        'recipient': recipient,
        'channel': channel,
        'purpose': purpose,
        'reason': reason,
      };
}

/// Why a draft cannot be sent.
enum SuppressionProblem { contact, channel, reason }

/// `phone`, `email` or null, by the same rules as the API's `normalize`.
String? contactKind(String value) {
  var text = value.trim();
  if (text.startsWith('<')) text = text.substring(1);
  if (text.endsWith('>')) text = text.substring(0, text.length - 1);
  text = text.trim();
  if (text.contains('@')) {
    return RegExp(r'^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$').hasMatch(text)
        ? 'email'
        : null;
  }
  final digits = text.replaceAll(RegExp(r'\D'), '');
  return digits.length >= 8 && digits.length <= 15 ? 'phone' : null;
}

/// The first problem with a new entry, checked before it is sent.
SuppressionProblem? suppressionProblem(SuppressionDraft draft) {
  final kind = contactKind(draft.recipient);
  if (kind == null) return SuppressionProblem.contact;
  if ((kind == 'email' &&
          (draft.channel == 'whatsapp' || draft.channel == 'sms')) ||
      (kind == 'phone' && draft.channel == 'email')) {
    return SuppressionProblem.channel;
  }
  return reasonProblem(draft.reason);
}

/// A reason is 3 to 300 characters once trimmed (kept in the audit log).
SuppressionProblem? reasonProblem(String reason) {
  final length = reason.trim().length;
  return length >= 3 && length <= 300 ? null : SuppressionProblem.reason;
}

// ---------------------------------------------------------------- workspace

class WorkspaceInfo {
  const WorkspaceInfo({
    required this.id,
    required this.tenantId,
    required this.name,
    required this.createdAt,
    this.archivedAt,
    this.archivedBy,
  });

  factory WorkspaceInfo.fromJson(JsonReader r) => WorkspaceInfo(
        id: r.string('id'),
        tenantId: r.string('tenant_id'),
        name: r.string('name'),
        createdAt: r.date('created_at'),
        archivedAt: r.optDate('archived_at'),
        archivedBy: r.optString('archived_by'),
      );

  final String id, tenantId, name;
  final DateTime createdAt;
  final DateTime? archivedAt;
  final String? archivedBy;
  bool get archived => archivedAt != null;
}

class WorkspacePermissions {
  const WorkspacePermissions({
    required this.role,
    required this.organizationAdmin,
    required this.canManageWorkspace,
  });

  factory WorkspacePermissions.fromJson(JsonReader r) => WorkspacePermissions(
        role: r.optString('role'),
        organizationAdmin: r.optBool('organization_admin') ?? false,
        canManageWorkspace: r.optBool('can_manage_workspace') ?? false,
      );

  final String? role;
  final bool organizationAdmin, canManageWorkspace;
}

/// The name the API will store: whitespace collapsed, 1 to 160 characters;
/// null when it is empty or too long.
String? cleanWorkspaceName(String raw) {
  final name = raw.split(RegExp(r'\s+')).where((w) => w.isNotEmpty).join(' ');
  return name.isEmpty || name.length > 160 ? null : name;
}

/// Archiving needs the current name typed exactly (surrounding spaces aside).
bool archiveConfirmed(String typed, String name) => typed.trim() == name;

// -------------------------------------------------------------- setup plans

const setupPlanSections = [
  'workspaces',
  'governance',
  'knowledge',
  'budget',
  'skills',
  'agents',
  'assistants',
  'commands',
  'automations',
  'manual',
  'unsupported',
];

class SetupPlanWeek {
  const SetupPlanWeek(this.week, this.title);
  final int week;
  final String title;
}

class SetupPlanSummary {
  const SetupPlanSummary({
    required this.id,
    required this.version,
    required this.name,
    required this.summary,
    required this.currency,
    required this.timezone,
    required this.businessHours,
    required this.weeks,
    required this.counts,
    required this.appliedItems,
    this.lastAppliedAt,
  });

  factory SetupPlanSummary.fromJson(JsonReader r) => SetupPlanSummary(
        id: r.string('id'),
        version: r.integer('version'),
        name: r.string('name'),
        summary: r.optString('summary') ?? '',
        currency: r.optString('currency') ?? '',
        timezone: r.optString('timezone') ?? '',
        businessHours: r.optString('business_hours') ?? '',
        weeks: r.optObjects('weeks',
            (w) => SetupPlanWeek(w.integer('week'), w.string('title'))),
        counts: {
          for (final entry in (r.optMap('counts') ?? const {}).entries)
            if (entry.value is num) entry.key: (entry.value! as num).toInt(),
        },
        appliedItems: r.optInt('applied_items') ?? 0,
        lastAppliedAt: r.optDate('last_applied_at'),
      );

  final String id, name, summary, currency, timezone, businessHours;
  final int version, appliedItems;
  final List<SetupPlanWeek> weeks;
  final Map<String, int> counts;
  final DateTime? lastAppliedAt;

  int get totalItems => counts.values.fold(0, (sum, n) => sum + n);
}

class SetupPlanItem {
  const SetupPlanItem({
    required this.key,
    required this.section,
    required this.kind,
    required this.name,
    required this.action,
    required this.reasonCode,
    required this.reason,
    required this.facts,
    this.week,
  });

  factory SetupPlanItem.fromJson(JsonReader r) => SetupPlanItem(
        key: r.string('key'),
        section: r.string('section'),
        kind: r.optString('kind') ?? '',
        name: r.string('name'),
        action: r.string('action'),
        reasonCode: r.optString('reason_code') ?? '',
        reason: r.optString('reason') ?? '',
        week: r.optInt('week'),
        facts: r.optStrings('facts') ?? const [],
      );

  final String key, section, kind, name, action, reasonCode, reason;
  final int? week;
  final List<String> facts;
}

class SetupPlanNote {
  const SetupPlanNote(
      {required this.label,
      required this.plan,
      required this.support,
      required this.anum});

  factory SetupPlanNote.fromJson(JsonReader r) => SetupPlanNote(
        label: r.string('label'),
        plan: r.optString('plan') ?? '',
        support: r.string('support'),
        anum: r.optString('anum') ?? '',
      );

  final String label, plan, support, anum;
}

class SetupPlanPreview {
  const SetupPlanPreview({
    required this.plan,
    required this.phase,
    required this.canApply,
    required this.accessNotes,
    required this.budgetAed,
    required this.budgetUsd,
    required this.items,
    required this.approvalThresholds,
    required this.memoryRules,
  });

  factory SetupPlanPreview.fromJson(JsonReader r) {
    final access = r.optObject('access');
    return SetupPlanPreview(
      plan: SetupPlanSummary.fromJson(r.object('plan')),
      phase: r.string('phase'),
      canApply: access?.optBool('can_apply') ?? false,
      accessNotes: access?.optStrings('notes') ?? const [],
      budgetAed: r.optNumber('budget_aed') ?? 0,
      budgetUsd: r.optNumber('budget_usd') ?? 0,
      items: r.optObjects('items', SetupPlanItem.fromJson),
      approvalThresholds:
          r.optObjects('approval_thresholds', SetupPlanNote.fromJson),
      memoryRules: r.optObjects('memory_rules', SetupPlanNote.fromJson),
    );
  }

  final SetupPlanSummary plan;
  final String phase;
  final bool canApply;
  final List<String> accessNotes;
  final double budgetAed, budgetUsd;
  final List<SetupPlanItem> items;
  final List<SetupPlanNote> approvalThresholds, memoryRules;

  /// Items applying would create, update or link (as the web counts them).
  int get changes => items
      .where((i) =>
          i.action == 'create' ||
          i.action == 'update' ||
          i.reasonCode == 'found_by_name' ||
          i.reasonCode == 'resume_stopped_apply')
      .length;
  int get present => items.where((i) => i.action == 'present').length;
  int get skipped =>
      items.where((i) => i.action == 'skipped' || i.action == 'kept').length;
}

/// Reasons whose wording is fixed are shown in the reader's language; the
/// others are the API's own sentence (as on the web).
const localizedPlanReasons = {
  'needs_org_admin',
  'needs_owner',
  'not_in_phase',
  'edited_by_owner',
  'removed_by_owner',
  'found_by_name',
  'already_applied',
  'busy',
  'recreate',
  'set_by_someone',
  'resume_stopped_apply',
};

String _fold(String text) => text.toLowerCase();

/// Items in [section] (or every section) whose name, reason or facts hold
/// every word of [query].
List<SetupPlanItem> filterPlanItems(List<SetupPlanItem> items,
    {String? section, String query = '', bool changesOnly = false}) {
  final words =
      _fold(query).split(RegExp(r'\s+')).where((w) => w.isNotEmpty).toList();
  bool keep(SetupPlanItem item) =>
      (section == null || item.section == section) &&
      (!changesOnly || item.action == 'create' || item.action == 'update') &&
      words.every((word) =>
          _fold('${item.name} ${item.reason} ${item.facts.join(' ')}')
              .contains(word));
  // Grouped by section in the order the plan is applied, stable within one.
  return [
    for (final name in setupPlanSections)
      for (final item in items)
        if (item.section == name && keep(item)) item,
    for (final item in items)
      if (!setupPlanSections.contains(item.section) && keep(item)) item,
  ];
}

// ----------------------------------------------------------------- org chart

/// Known departments in the order the web chart shows them.
const orgDepartments = [
  'sales',
  'support',
  'operations',
  'tyre_engineering',
  'finance',
  'custom',
];

enum OrgMemberKind { agent, assistant, person }

class OrgMember {
  const OrgMember({
    required this.kind,
    required this.id,
    required this.name,
    required this.department,
    this.description = '',
    this.active = true,
    this.attention = false,
    this.jobTitle,
    this.role,
  });

  final OrgMemberKind kind;
  final String id, name, description;

  /// Free text for agents; a known key for assistants and people.
  final String? department;

  /// Agents: not paused. Assistants: enabled. People: always.
  final bool active;

  /// An agent with a tool or skill that is not connected.
  final bool attention;
  final String? jobTitle, role;
}

class OrgDepartment {
  OrgDepartment(this.key, {this.known, this.name});

  /// A known department, `text:<lower case name>` or `none`.
  final String key;
  final String? known;

  /// The free-text name when it is not a known department.
  final String? name;
  final List<OrgMember> members = [];

  int count(OrgMemberKind kind) => members.where((m) => m.kind == kind).length;
}

/// Everything the org chart shows, and which optional sources failed.
class OrgChartData {
  const OrgChartData(
      {required this.members,
      this.assistantsFailed = false,
      this.peopleFailed = false,
      this.truncated = false});
  final List<OrgMember> members;
  final bool assistantsFailed, peopleFailed;

  /// More agents exist than the app reads at once.
  final bool truncated;
}

({String key, String? known, String? name}) orgDepartmentKey(
    String? department) {
  final text = (department ?? '').trim();
  if (text.isEmpty) return (key: 'none', known: null, name: null);
  final value = text.split(RegExp(r'\s+')).join(' ').toLowerCase();
  final snake = value.replaceAll(' ', '_');
  if (orgDepartments.contains(snake)) {
    return (key: snake, known: snake, name: null);
  }
  return (key: 'text:$value', known: null, name: text);
}

/// Departments in the web chart's order (known ones, then free-text ones,
/// then members without a department), members sorted agents first.
List<OrgDepartment> groupOrgChart(List<OrgMember> members) {
  final departments = <String, OrgDepartment>{};
  for (final member in members) {
    final where = orgDepartmentKey(member.department);
    departments
        .putIfAbsent(
            where.key,
            () =>
                OrgDepartment(where.key, known: where.known, name: where.name))
        .members
        .add(member);
  }
  int rank(OrgDepartment d) => d.known != null
      ? orgDepartments.indexOf(d.known!)
      : d.key == 'none'
          ? 1000
          : 100;
  final list = departments.values.toList()
    ..sort((a, b) {
      final byRank = rank(a) - rank(b);
      return byRank != 0
          ? byRank
          : (a.name ?? a.key)
              .toLowerCase()
              .compareTo((b.name ?? b.key).toLowerCase());
    });
  for (final department in list) {
    department.members.sort((a, b) {
      final byKind = a.kind.index - b.kind.index;
      if (byKind != 0) return byKind;
      if (a.attention != b.attention) return a.attention ? -1 : 1;
      return a.name.toLowerCase().compareTo(b.name.toLowerCase());
    });
  }
  return list;
}

/// Members whose name, description, job title or department hold [query].
List<OrgMember> filterOrgMembers(List<OrgMember> members, String query) {
  final needle = query.trim().toLowerCase();
  if (needle.isEmpty) return members;
  return [
    for (final m in members)
      if ([m.name, m.description, m.jobTitle ?? '', m.department ?? '']
          .any((field) => field.toLowerCase().contains(needle)))
        m
  ];
}
