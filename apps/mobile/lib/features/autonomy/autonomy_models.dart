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
    this.maxAutoRecipients,
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
      maxAutoRecipients: g.optInt('max_auto_recipients'),
      spendingLimit: g.optNumber('spending_limit'),
    );
  }

  /// The full stored settings, so a save keeps fields this client does not edit.
  final Map<String, Object?> settings;
  final Map<String, String> levels, ceilings;
  final int recipientsThreshold;
  final double amountThreshold;
  final List<String> approvedTemplates;
  final bool canManageWorkspace, canTighten;
  final int pendingProposals;
  final int? maxAutoRecipients;
  final double? spendingLimit;

  /// The `PUT /autonomy/settings` body with this client's edits applied.
  Map<String, Object?> body({
    required Map<String, String> levels,
    required int recipientsThreshold,
    required double amountThreshold,
    required List<String> approvedTemplates,
  }) {
    final next = Map<String, Object?>.from(settings)
      ..remove('updated_by')
      ..remove('updated_at');
    next['category_levels'] = Map<String, String>.from(levels);
    next['recipients_threshold'] = recipientsThreshold;
    next['amount_threshold'] = amountThreshold;
    next['approved_templates'] = approvedTemplates;
    return next;
  }
}
