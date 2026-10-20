import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../admin/admin_controller.dart' show AdminPhase, describeAdminError;
import 'autonomy_models.dart';
import 'autonomy_policy_draft.dart';
import 'autonomy_repository.dart';

AdminPhase _phaseOf(Object error) =>
    error is ApiException && error.isPermissionDenied
        ? AdminPhase.forbidden
        : (error is ApiException && error.statusCode == 0) ||
                error.toString().toLowerCase().contains('socket')
            ? AdminPhase.offline
            : AdminPhase.error;

/// The activity feed: everything ANUM did on its own, with Undo, "Always ask
/// for this", paging, a category filter and search, plus pending suggestions.
class ActivityController extends ChangeNotifier {
  ActivityController(this.repository, {DateTime Function()? clock})
      : clock = clock ?? DateTime.now;
  final AutonomyRepository repository;
  final DateTime Function() clock;

  /// The language of the notices this controller writes; the screen sets it.
  AnumLocalizations l = AnumLocalizations.english;

  AdminPhase phase = AdminPhase.initial;
  List<AutonomousAction> items = [];
  List<AutonomyProposal> proposals = [];
  String? nextCursor;
  String category = '';
  String query = '';
  bool canDecide = false;
  bool canTighten = true;
  bool loadingMore = false;
  String? loadMessage;
  String? notice;
  bool noticeFailed = false;

  /// Per-row errors (a refused or failed undo), keyed by action id.
  final Map<String, String> rowErrors = {};
  String? busy;

  bool get filtered => category.isNotEmpty || query.trim().isNotEmpty;

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      final page = await repository.actions(category: category, query: query);
      List<AutonomyProposal> open = const [];
      try {
        open = await repository.proposals();
      } on Object {
        open = const [];
      }
      try {
        final info = await repository.overview();
        canDecide = info.canManageWorkspace;
        canTighten = info.canTighten;
      } on Object {
        canDecide = false;
      }
      items = page.items;
      nextCursor = page.nextCursor;
      proposals = open;
      rowErrors.clear();
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = _phaseOf(error);
    }
    notifyListeners();
  }

  Future<void> loadMore() async {
    final cursor = nextCursor;
    if (cursor == null || loadingMore) return;
    loadingMore = true;
    notifyListeners();
    try {
      final page = await repository.actions(
        category: category,
        query: query,
        cursor: cursor,
      );
      items = [...items, ...page.items];
      nextCursor = page.nextCursor;
    } on Object catch (error) {
      _say(describeAdminError(error), failed: true);
    } finally {
      loadingMore = false;
      notifyListeners();
    }
  }

  void setCategory(String value) {
    category = value;
    load();
  }

  void search(String value) {
    query = value;
    load();
  }

  void clearFilters() {
    category = '';
    query = '';
    load();
  }

  Future<void> undo(AutonomousAction action) async {
    busy = action.id;
    notifyListeners();
    try {
      final next = await repository.undo(action);
      items = [for (final i in items) i.id == next.id ? next : i];
      rowErrors.remove(action.id);
      _say(l.f('auto.undone', {'summary': action.summary}));
    } on Object catch (error) {
      rowErrors[action.id] = describeAdminError(error);
    } finally {
      busy = null;
      notifyListeners();
    }
  }

  Future<void> tighten(AutonomousAction action) async {
    busy = action.id;
    notifyListeners();
    try {
      await repository.tighten(action.id);
      items = [
        for (final i in items)
          i.patternKey == action.patternKey ? i.tightened() : i,
      ];
      _say(l.f('auto.tightened',
          {'pattern': patternLabel(action.tool, action.template, l)}));
    } on Object catch (error) {
      rowErrors[action.id] = describeAdminError(error);
    } finally {
      busy = null;
      notifyListeners();
    }
  }

  Future<void> decide(AutonomyProposal proposal, {required bool accept}) async {
    busy = proposal.id;
    notifyListeners();
    try {
      await repository.decide(proposal.id, accept: accept);
      proposals = [
        for (final p in proposals)
          if (p.id != proposal.id) p,
      ];
      final pattern = patternLabel(proposal.tool, proposal.template, l);
      _say(l
          .f(accept ? 'auto.accepted' : 'auto.declined', {'pattern': pattern}));
    } on Object catch (error) {
      _say(describeAdminError(error), failed: true);
    } finally {
      busy = null;
      notifyListeners();
    }
  }

  void _say(String text, {bool failed = false}) {
    notice = text;
    noticeFailed = failed;
  }
}

/// The workspace's autonomy levels, thresholds and approved templates.
/// Owners edit; everyone else sees what is in force (the API decides).
class AutonomySettingsController extends ChangeNotifier {
  AutonomySettingsController(this.repository);
  final AutonomyRepository repository;

  /// The language of the notices this controller writes; the screen sets it.
  AnumLocalizations l = AnumLocalizations.english;

  AdminPhase phase = AdminPhase.initial;
  AutonomyOverview? overview;
  Map<String, String> levels = {};
  String recipients = '';
  String amount = '';
  String templates = '';
  String priceChange = '';
  String proposalAfter = '';

  /// Quiet hours, daily caps and blocked lists being edited.
  final PolicyDraft policy = PolicyDraft();
  String? loadMessage;
  String? notice;
  bool noticeFailed = false;
  String? denied;
  bool saving = false;

  bool get readOnly =>
      overview == null || !overview!.canManageWorkspace || denied != null;

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      _apply(await repository.overview());
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = _phaseOf(error);
    }
    notifyListeners();
  }

  void _apply(AutonomyOverview value) {
    overview = value;
    levels = {
      for (final c in autonomyCategories) c: value.levels[c] ?? 'act_silently',
    };
    recipients = '${value.recipientsThreshold}';
    amount = value.amountThreshold == value.amountThreshold.roundToDouble()
        ? '${value.amountThreshold.round()}'
        : '${value.amountThreshold}';
    templates = value.approvedTemplates.join('\n');
    priceChange = _plain(value.priceChangePercentThreshold);
    proposalAfter = '${value.proposalAfter}';
    policy.load(
      quiet: value.quietHours,
      dailyCaps: value.dailyCaps,
      blockedRecipients: value.blockedRecipients,
      blockedDomains: value.blockedDomains,
    );
  }

  static String _plain(double v) =>
      v == v.roundToDouble() ? '${v.round()}' : '$v';

  /// Re-draws the screen after a list or row edit.
  void changed() {
    notice = null;
    notifyListeners();
  }

  void setLevel(String category, String level) {
    if (readOnly) return;
    levels = {...levels, category: level};
    notice = null;
    notifyListeners();
  }

  /// The problem with the edited numbers, or null when they can be saved.
  String? get problem {
    final r = int.tryParse(recipients.trim());
    if (r == null || r < 0 || r > 10000) {
      return l.t('auto.err.recipients');
    }
    final max = overview?.maxAutoRecipients;
    if (max != null && r > max) {
      return l.f('auto.err.recipientsMax', {'max': max});
    }
    final a = double.tryParse(amount.trim());
    if (a == null || a < 0) return l.t('auto.err.amount');
    final maxAmount = overview?.maxAutoAmount;
    if (maxAmount != null && a > maxAmount) {
      return l.f('ap.err.amountMax', {'max': _plain(maxAmount)});
    }
    final price = double.tryParse(priceChange.trim());
    if (price == null || price < 0 || price > 1000) {
      return l.t('ap.err.priceChange');
    }
    final maxPrice = overview?.maxAutoPricePercent;
    if (maxPrice != null && price > maxPrice) {
      return l.f('ap.err.priceChangeMax', {'max': _plain(maxPrice)});
    }
    final after = int.tryParse(proposalAfter.trim());
    if (after == null || after < 2 || after > 50) {
      return l.t('ap.err.proposalAfter');
    }
    return policy.validate(l);
  }

  Future<bool> save() async {
    final current = overview;
    if (current == null || readOnly) return false;
    final issue = problem;
    if (issue != null) {
      notice = issue;
      noticeFailed = true;
      notifyListeners();
      return false;
    }
    saving = true;
    notice = null;
    notifyListeners();
    try {
      final list = <String>[];
      for (final line in templates.split(RegExp(r'[\n,]'))) {
        final t = line.trim();
        if (t.isNotEmpty && !list.contains(t)) list.add(t);
      }
      _apply(
        await repository.save(
          current.body(
            levels: levels,
            recipientsThreshold: int.parse(recipients.trim()),
            amountThreshold: double.parse(amount.trim()),
            approvedTemplates: list,
            priceChangePercentThreshold: double.parse(priceChange.trim()),
            proposalAfter: int.parse(proposalAfter.trim()),
            dailyCaps: policy.capsMap,
            quietHours: policy.quiet,
            clearQuietHours: !policy.quietOn,
            blockedRecipients: policy.recipients,
            blockedDomains: policy.domains,
          ),
        ),
      );
      notice = l.t('auto.saved');
      noticeFailed = false;
      return true;
    } on Object catch (error) {
      if (error is ApiException && error.isPermissionDenied) {
        denied = error.message;
        _apply(current);
      } else {
        notice = describeAdminError(error);
        noticeFailed = true;
      }
      return false;
    } finally {
      saving = false;
      notifyListeners();
    }
  }
}

/// The workspace's rules: list, add and remove. Owners raise or lower; anyone
/// who runs tasks can add an "always ask" rule (the API decides and says why).
class AutonomyRulesController extends ChangeNotifier {
  AutonomyRulesController(this.repository);
  final AutonomyRepository repository;

  /// The language of the notices this controller writes; the screen sets it.
  AnumLocalizations l = AnumLocalizations.english;

  /// Rules shown before "Show more", and the step each tap adds.
  static const pageSize = 20;

  AdminPhase phase = AdminPhase.initial;
  List<AutonomyRule> rules = [];
  bool canManage = false, canTighten = false;
  String? loadMessage, notice, addError;
  bool noticeFailed = false, adding = false;
  String query = '';
  int visible = pageSize;
  String? busy;
  final Map<String, String> rowErrors = {};

  bool get searchable => rules.length > pageSize;

  List<AutonomyRule> get matching {
    final q = query.trim().toLowerCase();
    if (q.isEmpty) return rules;
    return [
      for (final r in rules)
        if ('${r.tool} ${r.template ?? ''} ${r.category}'
            .toLowerCase()
            .contains(q))
          r,
    ];
  }

  List<AutonomyRule> get shown => matching.take(visible).toList();

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      rules = await repository.rules();
      try {
        final info = await repository.overview();
        canManage = info.canManageWorkspace;
        canTighten = info.canTighten;
      } on Object {
        canManage = false;
        canTighten = false;
      }
      rowErrors.clear();
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = _phaseOf(error);
    }
    notifyListeners();
  }

  void search(String value) {
    query = value;
    visible = pageSize;
    notifyListeners();
  }

  void showMore() {
    visible += pageSize;
    notifyListeners();
  }

  /// Adds a rule; returns whether it was created. The API's refusal text is
  /// kept in [addError] exactly as sent.
  Future<bool> add({
    required String tool,
    required String template,
    required String level,
  }) async {
    final name = tool.trim();
    final tpl = template.trim();
    if (name.isEmpty) return _refuse(l.t('ap.err.tool'));
    if (name.length > 160) return _refuse(l.t('ap.err.toolLong'));
    if (tpl.length > 120) return _refuse(l.t('ap.err.template'));
    adding = true;
    addError = null;
    notice = null;
    notifyListeners();
    try {
      final rule = await repository.addRule(
        tool: name,
        template: tpl.isEmpty ? null : tpl,
        level: level,
      );
      rules = [
        rule,
        for (final r in rules)
          if (r.id != rule.id &&
              !(r.tool == rule.tool && r.template == rule.template))
            r,
      ];
      notice = l.f('ap.rules.added',
          {'pattern': patternLabel(rule.tool, rule.template, l)});
      noticeFailed = false;
      return true;
    } on Object catch (error) {
      addError = describeAdminError(error);
      return false;
    } finally {
      adding = false;
      notifyListeners();
    }
  }

  bool _refuse(String text) {
    addError = text;
    notifyListeners();
    return false;
  }

  Future<void> remove(AutonomyRule rule) async {
    busy = rule.id;
    notifyListeners();
    try {
      await repository.deleteRule(rule.id);
      rules = [
        for (final r in rules)
          if (r.id != rule.id) r,
      ];
      rowErrors.remove(rule.id);
      notice = l.f('ap.rules.deleted',
          {'pattern': patternLabel(rule.tool, rule.template, l)});
      noticeFailed = false;
    } on Object catch (error) {
      rowErrors[rule.id] = describeAdminError(error);
    } finally {
      busy = null;
      notifyListeners();
    }
  }
}

/// The organization's guardrails. Editable only when the API says the caller
/// is an organization administrator (`can_manage_guardrails`).
class GuardrailsController extends ChangeNotifier {
  GuardrailsController(this.repository);
  final AutonomyRepository repository;

  /// The language of the notices this controller writes; the screen sets it.
  AnumLocalizations l = AnumLocalizations.english;

  AdminPhase phase = AdminPhase.initial;
  OrgGuardrails? current;
  bool canManage = false;
  String spending = '', maxAmount = '', maxRecipients = '', maxPrice = '';
  Map<String, String> ceilings = {};
  final PolicyDraft policy = PolicyDraft();
  String? loadMessage, notice, denied;
  bool noticeFailed = false, saving = false;

  bool get readOnly => !canManage || denied != null;

  Future<void> load() async {
    phase = AdminPhase.loading;
    loadMessage = null;
    notifyListeners();
    try {
      final value = await repository.guardrails();
      try {
        canManage = (await repository.overview()).canManageGuardrails;
      } on Object {
        canManage = false;
      }
      denied = null;
      _apply(value);
      phase = AdminPhase.ready;
    } on Object catch (error) {
      loadMessage = describeAdminError(error);
      phase = _phaseOf(error);
    }
    notifyListeners();
  }

  static String _plain(num? v) => v == null
      ? ''
      : v == v.roundToDouble()
          ? '${v.round()}'
          : '$v';

  void _apply(OrgGuardrails g) {
    current = g;
    spending = _plain(g.spendingLimit);
    maxAmount = _plain(g.maxAutoAmount);
    maxRecipients = _plain(g.maxAutoRecipients);
    maxPrice = _plain(g.maxAutoPricePercent);
    ceilings = {...g.ceilings};
    policy.load(
      quiet: g.quietHours,
      dailyCaps: g.dailyCaps,
      blockedRecipients: g.blockedRecipients,
      blockedDomains: g.blockedDomains,
    );
  }

  void setCeiling(String category, String? level) {
    if (readOnly) return;
    ceilings = {...ceilings};
    if (level == null || level.isEmpty) {
      ceilings.remove(category);
    } else {
      ceilings[category] = level;
    }
    changed();
  }

  void changed() {
    notice = null;
    notifyListeners();
  }

  double? _number(String text, double max) {
    final t = text.trim();
    if (t.isEmpty) return null;
    final v = double.tryParse(t);
    if (v == null || v < 0 || v > max) return double.nan;
    return v;
  }

  /// The first problem with the edited values, or null.
  String? get problem {
    final checks = <(String, String, double)>[
      ('ap.g.spending', spending, double.maxFinite),
      ('ap.g.maxAmount', maxAmount, double.maxFinite),
      ('ap.g.maxPrice', maxPrice, 1000),
    ];
    for (final (label, text, max) in checks) {
      final v = _number(text, max);
      if (v != null && v.isNaN) {
        return l.f('ap.err.gNumber', {'field': l.t(label)});
      }
    }
    final r = maxRecipients.trim();
    if (r.isNotEmpty) {
      final n = int.tryParse(r);
      if (n == null || n < 0 || n > 10000) return l.t('ap.err.gRecipients');
    }
    return policy.validate(l);
  }

  Future<bool> save() async {
    if (readOnly || current == null) return false;
    final issue = problem;
    if (issue != null) {
      notice = issue;
      noticeFailed = true;
      notifyListeners();
      return false;
    }
    final before = current!;
    saving = true;
    notice = null;
    notifyListeners();
    try {
      final body = OrgGuardrails(
        spendingLimit: _number(spending, double.maxFinite),
        maxAutoAmount: _number(maxAmount, double.maxFinite),
        maxAutoRecipients: maxRecipients.trim().isEmpty
            ? null
            : int.parse(maxRecipients.trim()),
        maxAutoPricePercent: _number(maxPrice, 1000),
        ceilings: ceilings,
        dailyCaps: policy.capsMap,
        quietHours: policy.quiet,
        blockedRecipients: policy.recipients,
        blockedDomains: policy.domains,
      ).toBody();
      _apply(await repository.saveGuardrails(body));
      notice = l.t('ap.g.saved');
      noticeFailed = false;
      return true;
    } on Object catch (error) {
      if (error is ApiException && error.isPermissionDenied) {
        denied = error.message;
        _apply(before);
      } else {
        notice = describeAdminError(error);
        noticeFailed = true;
      }
      return false;
    } finally {
      saving = false;
      notifyListeners();
    }
  }
}
