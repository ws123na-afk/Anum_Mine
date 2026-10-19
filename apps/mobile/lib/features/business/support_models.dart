import '../../data/json_read.dart';

/// Business support tickets (docs/business.md#support-tickets): what the phone
/// reads from `/api/v1/business/support`. The same wire shapes as the API's
/// `TicketView`, `TicketDetail` and `SupportSetup`. A ticket links to the
/// inbound email or WhatsApp conversation it came from by id only: the inbox
/// keeps the message text, so the phone never shows it here.

const ticketStatuses = [
  'new',
  'triaged',
  'in_progress',
  'waiting_on_customer',
  'resolved',
  'closed',
];
const openTicketStatuses = [
  'new',
  'triaged',
  'in_progress',
  'waiting_on_customer'
];
const ticketSeverities = ['critical', 'high', 'normal', 'low'];
const resolutionCodes = [
  'fixed',
  'workaround',
  'answered',
  'no_fault_found',
  'duplicate',
  'out_of_scope',
];

class SeverityTarget {
  const SeverityTarget(this.responseHours, this.resolutionHours);
  final double responseHours, resolutionHours;
  factory SeverityTarget.fromJson(JsonReader r) =>
      SeverityTarget(r.number('response_hours'), r.number('resolution_hours'));
}

/// The owner's support capacity: working days and hours in one time zone.
/// Targets count only inside them; there is no round the clock cover.
class SupportPolicy {
  const SupportPolicy({
    required this.timezone,
    required this.workDays,
    required this.startHour,
    required this.endHour,
    required this.targets,
    required this.note,
  });
  final String timezone, note;

  /// Python weekdays: Monday 0 to Sunday 6.
  final List<int> workDays;
  final int startHour, endHour;
  final Map<String, SeverityTarget> targets;

  factory SupportPolicy.fromJson(JsonReader r) {
    final targets = r.optMap('targets') ?? const {};
    return SupportPolicy(
      timezone: r.string('timezone'),
      workDays: [
        for (final day in r.optList('work_days') ?? const <Object?>[])
          if (day is num) day.toInt(),
      ],
      startHour: r.integer('start_hour'),
      endHour: r.integer('end_hour'),
      targets: {
        for (final key in targets.keys)
          key: SeverityTarget.fromJson(r.object('targets').object(key)),
      },
      note: r.optString('note') ?? '',
    );
  }
}

class SupportSetup {
  const SupportSetup({
    required this.policy,
    required this.canWrite,
    required this.canManage,
    required this.canDecide,
  });
  final SupportPolicy policy;
  final bool canWrite, canManage, canDecide;

  factory SupportSetup.fromJson(JsonReader r) => SupportSetup(
        policy: SupportPolicy.fromJson(r.object('policy')),
        canWrite: r.boolean('can_write'),
        canManage: r.boolean('can_manage'),
        canDecide: r.optBool('can_decide') ?? false,
      );
}

class SupportAssignee {
  const SupportAssignee(this.userId, this.role, this.you);
  final String userId, role;
  final bool you;
  factory SupportAssignee.fromJson(JsonReader r) => SupportAssignee(
        r.string('user_id'),
        r.string('role'),
        r.boolean('you'),
      );
}

class SupportSummary {
  const SupportSummary({
    required this.open,
    required this.newCount,
    required this.unassigned,
    required this.escalated,
    required this.breached,
    required this.waiting,
  });
  final int open, newCount, unassigned, escalated, breached, waiting;
  factory SupportSummary.fromJson(JsonReader r) => SupportSummary(
        open: r.integer('open'),
        newCount: r.integer('new'),
        unassigned: r.integer('unassigned'),
        escalated: r.integer('escalated'),
        breached: r.integer('breached'),
        waiting: r.integer('waiting_on_customer'),
      );
}

/// Where a ticket stands against its targets: `response` and `resolution` are
/// none, met, missed, paused, breached, due_soon or on_track.
class TicketSla {
  const TicketSla(this.response, this.resolution, this.breached);
  final String response, resolution;
  final bool breached;
  factory TicketSla.fromJson(JsonReader r) => TicketSla(
        r.string('response'),
        r.string('resolution'),
        r.boolean('breached'),
      );
}

class SupportTicket {
  const SupportTicket({
    required this.id,
    required this.reference,
    required this.title,
    required this.description,
    required this.severity,
    required this.status,
    required this.source,
    required this.requesterName,
    required this.requesterVerified,
    required this.entitlement,
    required this.entitlementNote,
    required this.escalated,
    required this.escalationReason,
    required this.resolutionSummary,
    required this.reportedAt,
    required this.updatedAt,
    required this.sla,
    required this.allowed,
    this.requesterEmail,
    this.requesterPhone,
    this.accountName,
    this.contactName,
    this.projectId,
    this.projectName,
    this.supportPlanName,
    this.assigneeId,
    this.responseDueAt,
    this.resolutionDueAt,
    this.firstRespondedAt,
    this.resolutionCode,
    this.resolvedAt,
    this.closedAt,
  });
  final String id, reference, title, description, severity, status, source;
  final String requesterName, entitlement, entitlementNote;
  final String escalationReason, resolutionSummary;
  final bool requesterVerified, escalated;
  final DateTime reportedAt, updatedAt;
  final TicketSla sla;
  final List<String> allowed;
  final String? requesterEmail, requesterPhone, accountName, contactName;
  final String? projectId, projectName, supportPlanName, assigneeId;
  final String? resolutionCode;
  final DateTime? responseDueAt, resolutionDueAt, firstRespondedAt;
  final DateTime? resolvedAt, closedAt;

  bool get isOpen => openTicketStatuses.contains(status);

  factory SupportTicket.fromJson(JsonReader r) => SupportTicket(
        id: r.string('id'),
        reference: r.string('reference'),
        title: r.string('title'),
        description: r.optString('description') ?? '',
        severity: r.string('severity'),
        status: r.string('status'),
        source: r.optString('source') ?? 'manual',
        requesterName: r.optString('requester_name') ?? '',
        requesterVerified: r.optBool('requester_verified') ?? false,
        entitlement: r.optString('entitlement') ?? 'none',
        entitlementNote: r.optString('entitlement_note') ?? '',
        escalated: r.optBool('escalated') ?? false,
        escalationReason: r.optString('escalation_reason') ?? '',
        resolutionSummary: r.optString('resolution_summary') ?? '',
        reportedAt: r.date('reported_at'),
        updatedAt: r.date('updated_at'),
        sla: TicketSla.fromJson(r.object('sla')),
        allowed: r.strings('allowed'),
        requesterEmail: r.optString('requester_email'),
        requesterPhone: r.optString('requester_phone'),
        accountName: r.optString('account_name'),
        contactName: r.optString('contact_name'),
        projectId: r.optString('project_id'),
        projectName: r.optString('project_name'),
        supportPlanName: r.optString('support_plan_name'),
        assigneeId: r.optString('assignee_id'),
        responseDueAt: r.optDate('response_due_at'),
        resolutionDueAt: r.optDate('resolution_due_at'),
        firstRespondedAt: r.optDate('first_responded_at'),
        resolutionCode: r.optString('resolution_code'),
        resolvedAt: r.optDate('resolved_at'),
        closedAt: r.optDate('closed_at'),
      );
}

/// The approval a reply waits on: decided in Approvals with its payload hash.
class ReplyApproval {
  const ReplyApproval(this.id, this.status, this.payloadHash);
  final String id, status;
  final String? payloadHash;
  factory ReplyApproval.fromJson(JsonReader r) => ReplyApproval(
        r.string('id'),
        r.string('status'),
        r.optString('payload_hash'),
      );
}

/// A reply to the requester, drafted here. Asking for approval hands exactly
/// this text to the inbox's own approval path; ANUM never sends it from here.
class TicketReply {
  const TicketReply({
    required this.id,
    required this.channel,
    required this.body,
    required this.status,
    required this.error,
    required this.createdAt,
    this.approval,
    this.sentAt,
  });
  final String id, channel, body, status, error;
  final DateTime createdAt;
  final DateTime? sentAt;
  final ReplyApproval? approval;

  bool get editable => status == 'draft' || status == 'failed';

  factory TicketReply.fromJson(JsonReader r) => TicketReply(
        id: r.string('id'),
        channel: r.string('channel'),
        body: r.string('body'),
        status: r.string('status'),
        error: r.optString('error') ?? '',
        createdAt: r.date('created_at'),
        sentAt: r.optDate('sent_at'),
        approval: r.optObject('approval') == null
            ? null
            : ReplyApproval.fromJson(r.object('approval')),
      );
}

class TicketActivity {
  const TicketActivity(this.kind, this.summary, this.actor, this.createdAt);
  final String kind, summary, actor;
  final DateTime createdAt;
  factory TicketActivity.fromJson(JsonReader r) => TicketActivity(
        r.string('kind'),
        r.string('summary'),
        r.string('actor'),
        r.date('created_at'),
      );
}

class TicketDetail {
  const TicketDetail({
    required this.ticket,
    required this.replies,
    required this.activities,
    required this.canReply,
    this.replyChannel,
  });
  final SupportTicket ticket;
  final List<TicketReply> replies;
  final List<TicketActivity> activities;
  final bool canReply;
  final String? replyChannel;

  factory TicketDetail.fromJson(JsonReader r) => TicketDetail(
        ticket: SupportTicket.fromJson(r),
        replies: r.objects('replies', TicketReply.fromJson),
        activities: r.objects('activities', TicketActivity.fromJson),
        canReply: r.boolean('can_reply'),
        replyChannel: r.optString('reply_channel'),
      );
}

/// The list's filters, sent as `/tickets` query parameters.
class TicketFilter {
  const TicketFilter({
    this.query,
    this.openOnly = true,
    this.status,
    this.severities = const {},
    this.mine = false,
    this.unassigned = false,
    this.escalated = false,
    this.sort = 'updated',
  });
  final String? query;
  final bool openOnly, mine, unassigned, escalated;

  /// One status, or null for every status (or every open one with [openOnly]).
  final String? status;
  final Set<String> severities;
  final String sort;

  bool get narrowed =>
      (query ?? '').isNotEmpty ||
      status != null ||
      severities.isNotEmpty ||
      mine ||
      unassigned ||
      escalated;

  TicketFilter copyWith({
    String? Function()? query,
    bool? openOnly,
    String? Function()? status,
    Set<String>? severities,
    bool? mine,
    bool? unassigned,
    bool? escalated,
    String? sort,
  }) =>
      TicketFilter(
        query: query == null ? this.query : query(),
        openOnly: openOnly ?? this.openOnly,
        status: status == null ? this.status : status(),
        severities: severities ?? this.severities,
        mine: mine ?? this.mine,
        unassigned: unassigned ?? this.unassigned,
        escalated: escalated ?? this.escalated,
        sort: sort ?? this.sort,
      );

  Map<String, Object> toQuery() => {
        if ((query ?? '').isNotEmpty) 'q': query!,
        if (status != null) 'status': [status!],
        if (status == null && openOnly) 'open_only': 'true',
        if (severities.isNotEmpty) 'severity': severities.toList()..sort(),
        if (unassigned) 'unassigned': 'true',
        if (mine && !unassigned) 'assignee': 'me',
        if (escalated) 'escalated': 'true',
        'sort': sort,
      };
}
