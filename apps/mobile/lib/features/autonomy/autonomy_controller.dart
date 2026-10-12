import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../admin/admin_controller.dart' show AdminPhase, describeAdminError;
import 'autonomy_models.dart';
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
    return null;
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
