import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';

/// Autonomy policy (docs/autonomy.md): what ANUM did on its own, and the
/// workspace's levels. Same words as the web client.
const autonomyCategories = [
  'email',
  'whatsapp',
  'commerce',
  'developer',
  'files',
  'tasks',
  'general',
];
const autonomyLevels = ['ask_always', 'act_and_notify', 'act_silently'];

const _levelRank = {'ask_always': 0, 'act_and_notify': 1, 'act_silently': 2};

/// The level's name in [l]'s language (English by default); an unknown level
/// shows as itself with spaces.
String levelLabel(String level,
        [AnumLocalizations l = AnumLocalizations.english]) =>
    autonomyLevels.contains(level)
        ? l.t('auto.level.$level')
        : level.replaceAll('_', ' ');

String categoryLabel(String category,
        [AnumLocalizations l = AnumLocalizations.english]) =>
    autonomyCategories.contains(category)
        ? l.t('auto.cat.$category')
        : category;

/// Whether [level] is allowed under an organization [ceiling] (null: any).
bool levelAllowed(String level, String? ceiling) =>
    ceiling == null || (_levelRank[level] ?? 0) <= (_levelRank[ceiling] ?? 0);

String patternLabel(String tool, String? template,
        [AnumLocalizations l = AnumLocalizations.english]) =>
    template == null
        ? tool
        : l.f('auto.withTemplate', {'tool': tool, 'template': template});

class AutonomousAction {
  const AutonomousAction({
    required this.id,
    required this.tool,
    required this.category,
    required this.level,
    required this.patternKey,
    required this.summary,
    required this.status,
    required this.createdAt,
    required this.reversible,
    required this.canUndo,
    this.template,
    this.undoExpiresAt,
    this.undoToken,
    this.undoneBy,
    this.undoError,
    this.patternLevel,
  });

  factory AutonomousAction.fromJson(JsonReader r) => AutonomousAction(
        id: r.string('id'),
        tool: r.string('tool'),
        category: r.string('category'),
        level: r.string('level'),
        patternKey: r.string('pattern_key'),
        summary: r.string('summary'),
        status: r.string('status'),
        createdAt: r.date('created_at'),
        reversible: r.optBool('reversible') ?? false,
        canUndo: r.optBool('can_undo') ?? false,
        template: r.optString('template'),
        undoExpiresAt: r.optDate('undo_expires_at'),
        undoToken: r.optString('undo_token'),
        undoneBy: r.optString('undone_by'),
        undoError: r.optString('undo_error'),
        patternLevel: r.optString('pattern_level'),
      );

  final String id, tool, category, level, patternKey, summary, status;
  final DateTime createdAt;
  final bool reversible, canUndo;
  final String? template, undoToken, undoneBy, undoError, patternLevel;
  final DateTime? undoExpiresAt;

  bool get undone => status == 'undone';
  bool get asksNow => patternLevel == 'ask_always';

  /// Undo is offered while the window is open, to someone the API allows.
  bool undoableAt(DateTime now) =>
      !undone &&
      canUndo &&
      undoToken != null &&
      undoExpiresAt != null &&
      now.isBefore(undoExpiresAt!);

  AutonomousAction tightened() => AutonomousAction(
        id: id,
        tool: tool,
        category: category,
        level: level,
        patternKey: patternKey,
        summary: summary,
        status: status,
        createdAt: createdAt,
        reversible: reversible,
        canUndo: canUndo,
        template: template,
        undoExpiresAt: undoExpiresAt,
        undoToken: undoToken,
        undoneBy: undoneBy,
        undoError: undoError,
        patternLevel: 'ask_always',
      );
}

class ActionPage {
  const ActionPage(this.items, this.nextCursor);
  final List<AutonomousAction> items;
  final String? nextCursor;
}

class AutonomyProposal {
  const AutonomyProposal({
    required this.id,
    required this.tool,
    required this.category,
    required this.proposedLevel,
    required this.approvals,
    this.template,
  });

  factory AutonomyProposal.fromJson(JsonReader r) => AutonomyProposal(
        id: r.string('id'),
        tool: r.string('tool'),
        category: r.string('category'),
        proposedLevel: r.string('proposed_level'),
        approvals: r.integer('approvals'),
        template: r.optString('template'),
      );

  final String id, tool, category, proposedLevel;
  final int approvals;
  final String? template;

  String get sentence => sentenceIn(AnumLocalizations.english);

  /// "Let ANUM "Act and notify" for …? You approved it 3 times." in [l].
  String sentenceIn(AnumLocalizations l) => l.p('auto.proposal', approvals, {
        'level': levelLabel(proposedLevel, l),
        'pattern': patternLabel(tool, template, l),
      });
}

/// The workspace settings this client edits (levels, thresholds, templates)
/// plus what it shows read-only (the organization's ceilings and limits).
class AutonomyOverview {
  const AutonomyOverview({
    required this.settings,
    required this.levels,
    required this.ceilings,
    required this.recipientsThreshold,
    required this.amountThreshold,
    required this.approvedTemplates,
    required this.canManageWorkspace,
    required this.canTighten,
    required this.pendingProposals,
    this.canManageGuardrails = false,
    this.priceChangePercentThreshold = 0,
    this.proposalAfter = 3,
    this.dailyCaps = const {},
    this.quietHours,
    this.blockedRecipients = const [],
    this.blockedDomains = const [],
    this.maxAutoRecipients,
    this.maxAutoAmount,
    this.maxAutoPricePercent,
    this.spendingLimit,
  });

  factory AutonomyOverview.fromJson(JsonReader r) {
    final s = r.object('settings');
    final g = r.object('guardrails');
    final levels = <String, String>{};
    final ceilings = <String, String>{};
    for (final c in r.objects('categories', (x) => x)) {
      levels[c.string('category')] = c.string('level');
      final ceiling = c.optString('ceiling');
      if (ceiling != null) ceilings[c.string('category')] = ceiling;
    }
    return AutonomyOverview(
      settings: Map<String, Object?>.from(s.json),
      levels: levels,
      ceilings: ceilings,
      recipientsThreshold: s.integer('recipients_threshold'),
      amountThreshold: s.number('amount_threshold'),
      approvedTemplates: s.optStrings('approved_templates') ?? const [],
      canManageWorkspace: r.boolean('can_manage_workspace'),
      canTighten: r.optBool('can_tighten') ?? false,
      pendingProposals: r.optInt('pending_proposals') ?? 0,
      canManageGuardrails: r.optBool('can_manage_guardrails') ?? false,
      priceChangePercentThreshold:
          s.optNumber('price_change_percent_threshold') ?? 0,
      proposalAfter: s.optInt('proposal_after') ?? 3,
      dailyCaps: _caps(s.optMap('daily_caps')),
      quietHours: _quiet(s.optMap('quiet_hours')),
      blockedRecipients: s.optStrings('blocked_recipients') ?? const [],
      blockedDomains: s.optStrings('blocked_domains') ?? const [],
      maxAutoRecipients: g.optInt('max_auto_recipients'),
      maxAutoAmount: g.optNumber('max_auto_amount'),
      maxAutoPricePercent: g.optNumber('max_auto_price_change_percent'),
      spendingLimit: g.optNumber('spending_limit'),
    );
  }

  /// The full stored settings, so a save keeps fields this client does not edit.
  final Map<String, Object?> settings;
  final Map<String, String> levels, ceilings;
  final int recipientsThreshold;
  final double amountThreshold;
  final List<String> approvedTemplates;
  final bool canManageWorkspace, canTighten, canManageGuardrails;
  final int pendingProposals, proposalAfter;
  final double priceChangePercentThreshold;
  final Map<String, int> dailyCaps;
  final QuietHours? quietHours;
  final List<String> blockedRecipients, blockedDomains;
  final int? maxAutoRecipients;
  final double? maxAutoAmount, maxAutoPricePercent, spendingLimit;

  /// The `PUT /autonomy/settings` body with this client's edits applied.
  Map<String, Object?> body({
    required Map<String, String> levels,
    required int recipientsThreshold,
    required double amountThreshold,
    required List<String> approvedTemplates,
    double? priceChangePercentThreshold,
    int? proposalAfter,
    Map<String, int>? dailyCaps,
    QuietHours? quietHours,
    bool clearQuietHours = false,
    List<String>? blockedRecipients,
    List<String>? blockedDomains,
  }) {
    final next = Map<String, Object?>.from(settings)
      ..remove('updated_by')
      ..remove('updated_at');
    next['category_levels'] = Map<String, String>.from(levels);
    next['recipients_threshold'] = recipientsThreshold;
    next['amount_threshold'] = amountThreshold;
    next['approved_templates'] = approvedTemplates;
    if (priceChangePercentThreshold != null) {
      next['price_change_percent_threshold'] = priceChangePercentThreshold;
    }
    if (proposalAfter != null) next['proposal_after'] = proposalAfter;
    if (dailyCaps != null) {
      next['daily_caps'] = Map<String, int>.from(dailyCaps);
    }
    if (clearQuietHours) next['quiet_hours'] = null;
    if (quietHours != null) next['quiet_hours'] = quietHours.toJson();
    if (blockedRecipients != null) {
      next['blocked_recipients'] = blockedRecipients;
    }
    if (blockedDomains != null) next['blocked_domains'] = blockedDomains;
    return next;
  }
}

Map<String, int> _caps(Map<String, Object?>? raw) => {
      for (final e in (raw ?? const <String, Object?>{}).entries)
        if (e.value is num) e.key: (e.value! as num).toInt(),
    };

QuietHours? _quiet(Map<String, Object?>? raw) =>
    raw == null ? null : QuietHours.fromJson(JsonReader(raw));

/// A daily window in which nothing with an effect runs on its own.
class QuietHours {
  const QuietHours(
      {required this.start, required this.end, this.timezone = 'UTC'});
  factory QuietHours.fromJson(JsonReader r) => QuietHours(
        start: r.string('start'),
        end: r.string('end'),
        timezone: r.optString('timezone') ?? 'UTC',
      );
  final String start, end, timezone;
  Map<String, Object?> toJson() =>
      {'start': start, 'end': end, 'timezone': timezone};
}

final _hhmm = RegExp(r'^([01]\d|2[0-3]):[0-5]\d$');
final _zone = RegExp(r'^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*$');

/// HH:MM, 24-hour, like the server.
bool validHhmm(String value) => _hhmm.hasMatch(value.trim());

/// An IANA-looking zone name (the server has the final say on unknown zones).
bool plausibleZone(String value) {
  final v = value.trim();
  return v.isNotEmpty && v.length <= 64 && _zone.hasMatch(v);
}

/// The server's recipient normalization: lower-case addresses, phone numbers
/// as `+` and digits.
String normalizeRecipient(String value) {
  final text = value.trim().toLowerCase();
  if (text.contains('@')) {
    return text.replaceAll(RegExp(r'^<+|>+$'), '');
  }
  final digits = text.replaceAll(RegExp(r'\D'), '');
  return digits.isEmpty ? text : '+$digits';
}

/// The server's domain normalization: lower-case, no leading `@`.
String normalizeDomain(String value) =>
    value.trim().toLowerCase().replaceFirst(RegExp(r'^@+'), '');

/// The blocked-list limit the server keeps.
const maxListItems = 500;

/// A recipient worth storing: an address with a name and a domain, or a
/// phone number with at least 5 digits.
bool plausibleRecipient(String normalized) {
  if (normalized.contains(RegExp(r'\s'))) return false;
  if (normalized.contains('@')) {
    return RegExp(r'^[^@]+@[^@.]+(\.[^@.]+)+$').hasMatch(normalized);
  }
  return RegExp(r'^\+\d{5,}$').hasMatch(normalized);
}

bool plausibleDomain(String normalized) =>
    RegExp(r'^[a-z0-9-]+(\.[a-z0-9-]+)+$').hasMatch(normalized);

/// The cap range the server accepts.
bool validCap(int value) => value >= 0 && value <= 100000;

/// One workspace rule: the level for an exact tool-and-template pattern.
class AutonomyRule {
  const AutonomyRule({
    required this.id,
    required this.tool,
    required this.category,
    required this.level,
    required this.source,
    this.template,
  });
  factory AutonomyRule.fromJson(JsonReader r) => AutonomyRule(
        id: r.string('id'),
        tool: r.string('tool'),
        category: r.string('category'),
        level: r.string('level'),
        source: r.optString('source') ?? 'manual',
        template: r.optString('template'),
      );
  final String id, tool, category, level, source;
  final String? template;
}

/// The organization's floors (`/autonomy/guardrails`).
class OrgGuardrails {
  const OrgGuardrails({
    this.spendingLimit,
    this.maxAutoAmount,
    this.maxAutoRecipients,
    this.maxAutoPricePercent,
    this.ceilings = const {},
    this.dailyCaps = const {},
    this.quietHours,
    this.blockedRecipients = const [],
    this.blockedDomains = const [],
  });
  factory OrgGuardrails.fromJson(JsonReader r) => OrgGuardrails(
        spendingLimit: r.optNumber('spending_limit'),
        maxAutoAmount: r.optNumber('max_auto_amount'),
        maxAutoRecipients: r.optInt('max_auto_recipients'),
        maxAutoPricePercent: r.optNumber('max_auto_price_change_percent'),
        ceilings: {
          for (final e in (r.optMap('category_ceilings') ?? const {}).entries)
            if (e.value is String) e.key: e.value! as String,
        },
        dailyCaps: _caps(r.optMap('daily_caps')),
        quietHours: _quiet(r.optMap('quiet_hours')),
        blockedRecipients: r.optStrings('blocked_recipients') ?? const [],
        blockedDomains: r.optStrings('blocked_domains') ?? const [],
      );
  final double? spendingLimit, maxAutoAmount, maxAutoPricePercent;
  final int? maxAutoRecipients;
  final Map<String, String> ceilings;
  final Map<String, int> dailyCaps;
  final QuietHours? quietHours;
  final List<String> blockedRecipients, blockedDomains;

  /// The `PUT /autonomy/guardrails` body (the server sets who and when).
  Map<String, Object?> toBody() => {
        'spending_limit': spendingLimit,
        'max_auto_amount': maxAutoAmount,
        'max_auto_recipients': maxAutoRecipients,
        'max_auto_price_change_percent': maxAutoPricePercent,
        'category_ceilings': ceilings,
        'daily_caps': dailyCaps,
        'quiet_hours': quietHours?.toJson(),
        'blocked_recipients': blockedRecipients,
        'blocked_domains': blockedDomains,
      };
}
