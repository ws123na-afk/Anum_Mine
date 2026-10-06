import '../../data/api_models.dart';

/// Members, invitations and monthly model budgets, as the API returns them.
/// docs/identity.md (Invitations and Membership Management) and
/// docs/model-gateway.md (Monthly budgets).

const workspaceRoles = ['owner', 'member', 'viewer'];
const invitationPrefix = 'anum_inv_';
const defaultInvitationTtlHours = 168;
const maxInvitationTtlHours = 720;

String roleLabel(String role) => switch (role) {
      'owner' => 'Owner',
      'member' => 'Member',
      'viewer' => 'Viewer',
      _ => role,
    };

DateTime _time(Object? value) => DateTime.parse(value! as String);
DateTime? _maybeTime(Object? value) =>
    value is String ? DateTime.parse(value) : null;
num _num(Object? value) => value is num ? value : 0;

class WorkspaceMember {
  const WorkspaceMember({
    required this.userId,
    required this.workspaceId,
    required this.role,
    required this.active,
    required this.createdAt,
    required this.updatedAt,
  });

  factory WorkspaceMember.fromJson(JsonMap json) => WorkspaceMember(
        userId: json['user_id']! as String,
        workspaceId: json['workspace_id']! as String,
        role: json['role']! as String,
        active: json['active'] as bool? ?? true,
        createdAt: _time(json['created_at']),
        updatedAt: _time(json['updated_at']),
      );

  final String userId, workspaceId, role;
  final bool active;
  final DateTime createdAt, updatedAt;
}

class WorkspaceInvitation {
  const WorkspaceInvitation({
    required this.id,
    required this.workspaceId,
    required this.role,
    required this.status,
    required this.createdByUserId,
    required this.expiresAt,
    required this.createdAt,
    this.inviteeUserId,
    this.inviteeEmail,
    this.acceptedByUserId,
    this.acceptedAt,
    this.revokedAt,
  });

  factory WorkspaceInvitation.fromJson(JsonMap json) => WorkspaceInvitation(
        id: json['id']! as String,
        workspaceId: json['workspace_id']! as String,
        role: json['role']! as String,
        status: json['status']! as String,
        createdByUserId: json['created_by_user_id']! as String,
        expiresAt: _time(json['expires_at']),
        createdAt: _time(json['created_at']),
        inviteeUserId: json['invitee_user_id'] as String?,
        inviteeEmail: json['invitee_email'] as String?,
        acceptedByUserId: json['accepted_by_user_id'] as String?,
        acceptedAt: _maybeTime(json['accepted_at']),
        revokedAt: _maybeTime(json['revoked_at']),
      );

  final String id, workspaceId, role, status, createdByUserId;
  final String? inviteeUserId, inviteeEmail, acceptedByUserId;
  final DateTime expiresAt, createdAt;
  final DateTime? acceptedAt, revokedAt;

  bool get pending => status == 'pending';

  String get invitee =>
      [inviteeUserId, inviteeEmail].whereType<String>().join(' · ');
}

/// The token is returned exactly once: the API stores only its hash.
class CreatedInvitation {
  const CreatedInvitation({required this.invitation, required this.token});
  final WorkspaceInvitation invitation;
  final String token;
}

class AcceptedInvitation {
  const AcceptedInvitation({required this.invitation, required this.member});
  final WorkspaceInvitation invitation;
  final WorkspaceMember member;
}

class InvitationDraft {
  const InvitationDraft({
    required this.role,
    this.userId,
    this.email,
    this.ttlHours = defaultInvitationTtlHours,
  });
  final String role;
  final String? userId, email;
  final int ttlHours;

  JsonMap toJson() => {
        'role': role,
        if (userId != null) 'invitee_user_id': userId,
        if (email != null) 'invitee_email': email,
        'ttl_hours': ttlHours,
      };
}

/// Validates the invite form the way the API does. Returns the draft or an
/// error sentence.
(InvitationDraft?, String?) validateInvitation({
  required String role,
  required String userId,
  required String email,
  required String ttlHours,
}) {
  final user = userId.trim();
  final mail = email.trim();
  if (user.isEmpty && mail.isEmpty) {
    return (null, 'Enter the invitee’s user id, email address, or both.');
  }
  if (user.length > 120) return (null, 'User ids are at most 120 characters.');
  if (mail.isNotEmpty) {
    final at = mail.indexOf('@');
    final valid = at > 0 &&
        mail.substring(at + 1).contains('.') &&
        !mail.contains(RegExp(r'\s')) &&
        mail.length <= 320;
    if (!valid) return (null, 'Enter a valid email address.');
  }
  final ttlText = ttlHours.trim();
  final ttl =
      ttlText.isEmpty ? defaultInvitationTtlHours : int.tryParse(ttlText);
  if (ttl == null || ttl < 1 || ttl > maxInvitationTtlHours) {
    return (
      null,
      'Expiry must be a whole number of hours from 1 to $maxInvitationTtlHours.'
    );
  }
  return (
    InvitationDraft(
        role: role,
        userId: user.isEmpty ? null : user,
        email: mail.isEmpty ? null : mail,
        ttlHours: ttl),
    null
  );
}

/// A pasted token, or a web invitation link
/// (`…#invitation=<token>&workspace=<id>`). Null when it holds no token.
({String token, String? workspaceId})? parseInvitationInput(String text) {
  final value = text.trim();
  if (value.isEmpty) return null;
  if (value.startsWith(invitationPrefix) &&
      !value.contains(RegExp(r'[\s&#?]'))) {
    return (token: value, workspaceId: null);
  }
  final hash = value.indexOf('#');
  final query = value.indexOf('?');
  final params = hash >= 0
      ? value.substring(hash + 1)
      : query >= 0
          ? value.substring(query + 1)
          : '';
  if (params.isEmpty) return null;
  final Map<String, String> map;
  try {
    map = Uri.splitQueryString(params);
  } on FormatException {
    return null;
  }
  final token = map['invitation'];
  if (token == null || !token.startsWith(invitationPrefix)) return null;
  final workspace = map['workspace']?.trim();
  return (
    token: token,
    workspaceId: workspace == null || workspace.isEmpty ? null : workspace
  );
}

/// "Expires in 3 d", "Expires in 5 h", "Expired 2 h ago".
String expiryLabel(DateTime expiresAt, {DateTime? now}) {
  final diff = expiresAt.difference(now ?? DateTime.now());
  final abs = diff.abs();
  final amount = abs.inDays >= 1
      ? '${abs.inDays} d'
      : abs.inHours >= 1
          ? '${abs.inHours} h'
          : '${abs.inMinutes < 1 ? 1 : abs.inMinutes} min';
  return diff.isNegative ? 'Expired $amount ago' : 'Expires in $amount';
}

int activeOwnerCount(List<WorkspaceMember> members) =>
    members.where((m) => m.active && m.role == 'owner').length;

/// The only active owner: the API refuses to demote or deactivate them (409).
bool isLastActiveOwner(List<WorkspaceMember> members, WorkspaceMember member) =>
    member.active && member.role == 'owner' && activeOwnerCount(members) == 1;

List<WorkspaceMember> sortMembers(List<WorkspaceMember> members) {
  int rank(String role) {
    final index = workspaceRoles.indexOf(role);
    return index < 0 ? workspaceRoles.length : index;
  }

  return [...members]..sort((a, b) {
      if (a.active != b.active) return a.active ? -1 : 1;
      final byRole = rank(a.role).compareTo(rank(b.role));
      return byRole != 0 ? byRole : a.userId.compareTo(b.userId);
    });
}

// ----------------------------------------------------------------- budgets

class ModelBudgetLimits {
  const ModelBudgetLimits({this.costUsd, this.tokens});
  final double? costUsd;
  final int? tokens;
  JsonMap toJson() =>
      {'monthly_cost_limit_usd': costUsd, 'monthly_token_limit': tokens};
}

class ModelBudget {
  const ModelBudget({
    required this.limits,
    required this.updatedAt,
    required this.updatedBy,
  });
  factory ModelBudget.fromJson(JsonMap json) => ModelBudget(
        limits: ModelBudgetLimits(
          costUsd: (json['monthly_cost_limit_usd'] as num?)?.toDouble(),
          tokens: (json['monthly_token_limit'] as num?)?.toInt(),
        ),
        updatedAt: _time(json['updated_at']),
        updatedBy: json['updated_by'] as String? ?? '',
      );
  final ModelBudgetLimits limits;
  final DateTime updatedAt;
  final String updatedBy;
}

class BudgetScopeView {
  const BudgetScopeView({
    required this.budget,
    required this.inputTokens,
    required this.outputTokens,
    required this.costUsd,
    required this.calls,
    required this.unpricedCalls,
    required this.totalTokens,
    required this.exceeded,
  });
  factory BudgetScopeView.fromJson(JsonMap json) {
    final usage = (json['usage'] as JsonMap?) ?? const {};
    final budget = json['budget'];
    return BudgetScopeView(
      budget: budget is JsonMap ? ModelBudget.fromJson(budget) : null,
      inputTokens: _num(usage['input_tokens']).toInt(),
      outputTokens: _num(usage['output_tokens']).toInt(),
      costUsd: _num(usage['estimated_cost_usd']).toDouble(),
      calls: _num(usage['calls']).toInt(),
      unpricedCalls: _num(usage['unpriced_calls']).toInt(),
      totalTokens: _num(json['total_tokens']).toInt(),
      exceeded: json['exceeded'] as bool? ?? false,
    );
  }
  final ModelBudget? budget;
  final int inputTokens, outputTokens, calls, unpricedCalls, totalTokens;
  final double costUsd;
  final bool exceeded;

  List<BudgetMeter> get meters => [
        BudgetMeter(
            label: 'Estimated cost',
            used: costUsd,
            limit: budget?.limits.costUsd,
            format: formatUsd),
        BudgetMeter(
            label: 'Tokens',
            used: totalTokens.toDouble(),
            limit: budget?.limits.tokens?.toDouble(),
            format: formatTokens),
      ];
}

/// This UTC calendar month. Dates are `YYYY-MM-DD`.
class BudgetOverview {
  const BudgetOverview({
    required this.periodStart,
    required this.resetsOn,
    required this.tenant,
    required this.workspace,
  });
  factory BudgetOverview.fromJson(JsonMap json) => BudgetOverview(
        periodStart: json['period_start']! as String,
        resetsOn: json['resets_on']! as String,
        tenant: BudgetScopeView.fromJson(json['tenant']! as JsonMap),
        workspace: BudgetScopeView.fromJson(json['workspace']! as JsonMap),
      );
  final String periodStart, resetsOn;
  final BudgetScopeView tenant, workspace;
}

enum MeterTone { none, ok, warn, stop }

class BudgetMeter {
  const BudgetMeter({
    required this.label,
    required this.used,
    required this.limit,
    required this.format,
  });
  final String label;
  final double used;
  final double? limit;
  final String Function(num) format;

  /// Uncapped share of the limit in percent; null without a limit. A zero
  /// limit blocks every call, so it reads as 100%.
  double? get percent {
    final value = limit;
    if (value == null) return null;
    if (value <= 0) return 100;
    return used / value * 100;
  }

  /// 0..1 for the bar.
  double get fraction => ((percent ?? 0) / 100).clamp(0, 1).toDouble();

  /// The API's thresholds: 80% warns, 100% refuses calls.
  MeterTone get tone {
    final value = percent;
    if (value == null) return MeterTone.none;
    if (value >= 100) return MeterTone.stop;
    if (value >= 80) return MeterTone.warn;
    return MeterTone.ok;
  }

  String get usedText => format(used);
  String get limitText => limit == null ? 'No limit' : format(limit!);
  String get percentText =>
      percent == null ? 'No limit set' : '${percent!.round()}% used';
}

String _grouped(String digits) {
  final out = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) out.write(',');
    out.write(digits[i]);
  }
  return out.toString();
}

String formatTokens(num value) => _grouped(value.round().toString());

String formatUsd(num value) {
  if (value > 0 && value < 0.01) return '< \$0.01';
  final fixed = value.toStringAsFixed(2);
  final dot = fixed.indexOf('.');
  return '\$${_grouped(fixed.substring(0, dot))}${fixed.substring(dot)}';
}

const _months = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December'
];

/// "1 November 2026 (UTC)" for an ISO date.
String formatResetDate(String isoDate) {
  final date = DateTime.tryParse(isoDate);
  if (date == null) return isoDate;
  return '${date.day} ${_months[date.month - 1]} ${date.year} (UTC)';
}

String limitText(num? value) {
  if (value == null) return '';
  if (value is double && value == value.roundToDouble()) {
    return value.toInt().toString();
  }
  return value.toString();
}

/// Empty means no limit. Returns the limits or an error sentence.
(ModelBudgetLimits?, String?) parseLimits(String cost, String tokens) {
  final costText = cost.trim().replaceFirst(r'$', '').replaceAll(',', '');
  final tokenText = tokens.trim().replaceAll(',', '').replaceAll('_', '');
  double? costValue;
  int? tokenValue;
  if (costText.isNotEmpty) {
    costValue = double.tryParse(costText);
    if (costValue == null ||
        !costValue.isFinite ||
        costValue < 0 ||
        costValue > 1e9) {
      return (
        null,
        'Cost limit must be a dollar amount from 0 to 1,000,000,000, or empty for no limit.'
      );
    }
  }
  if (tokenText.isNotEmpty) {
    tokenValue = int.tryParse(tokenText);
    if (tokenValue == null || tokenValue < 0 || tokenValue > 1000000000000000) {
      return (
        null,
        'Token limit must be a whole number from 0 to 10^15, or empty for no limit.'
      );
    }
  }
  return (ModelBudgetLimits(costUsd: costValue, tokens: tokenValue), null);
}
