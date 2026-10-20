import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'learning_models.dart';
import 'learning_repository.dart';

enum LearningPhase { loading, ready, permissionDenied, offline, error }

const learningPermissionMessage =
    'Only active members of this workspace have learning data here. Ask a workspace owner to restore your membership, or switch to a workspace you belong to.';
const routinePermissionMessage =
    'Only members and owners can create automations. Ask a workspace owner for a member role.';
const autonomyPermissionMessage =
    'Only a workspace owner can apply autonomy changes. Ask an owner to review this suggestion.';

/// Settings -> What ANUM learned (docs/learning.md).
class LearningController extends ChangeNotifier {
  LearningController(this.repository, {String Function()? timezone})
      : routineTimezone = timezone?.call() ?? 'UTC';
  final LearningRepository repository;

  /// The IANA zone routines are detected in (`Asia/Riyadh`); the phone has no
  /// way to read its own IANA name, so the person can type it. The server
  /// refuses an unknown name and says so.
  String routineTimezone;

  LearningPhase phase = LearningPhase.loading;
  String? loadError;
  LearningOverview? overview;
  List<LearnedPreference> preferences = [];
  int preferenceTotal = 0;
  String preferenceQuery = '';
  List<LearnedRoutine> routines = [];
  List<ImprovementNote> notes = [];
  List<LearningSignal> signals = [];
  int signalTotal = 0;
  String signalQuery = '';
  bool busy = false;

  /// The outcome of the last action: what happened, or what failed and how to fix it.
  String? message;
  bool messageIsError = false;

  bool get learningOn => overview?.state == LearningState.on;

  Future<void> load() async {
    phase = LearningPhase.loading;
    notifyListeners();
    try {
      overview = await repository.overview();
      final results = await Future.wait([
        repository.preferences(query: preferenceQuery),
        repository.signals(query: signalQuery),
      ]);
      final prefs = results[0] as LearningPage<LearnedPreference>;
      final sigs = results[1] as LearningPage<LearningSignal>;
      preferences = prefs.items;
      preferenceTotal = prefs.total;
      signals = sigs.items;
      signalTotal = sigs.total;
      routines = await repository.routines();
      notes = await repository.notes();
      phase = LearningPhase.ready;
    } on Object catch (error) {
      loadError = describe(error, 'load what ANUM learned');
      phase = error is ApiException && error.isPermissionDenied
          ? LearningPhase.permissionDenied
          : _offline(error)
              ? LearningPhase.offline
              : LearningPhase.error;
    }
    notifyListeners();
  }

  Future<bool> _run(
    String what,
    Future<String> Function() action, {
    String? denied,
  }) async {
    busy = true;
    message = null;
    notifyListeners();
    var ok = false;
    try {
      message = await action();
      messageIsError = false;
      ok = true;
    } on Object catch (error) {
      messageIsError = true;
      message =
          error is ApiException && error.isPermissionDenied && denied != null
              ? denied
              : describe(error, what);
    }
    busy = false;
    notifyListeners();
    return ok;
  }

  Future<void> setEnabled(bool enabled) => _run('change learning', () async {
        overview = await repository.setEnabled(enabled);
        return enabled
            ? 'Learning is on.'
            : 'Learning is off. Nothing new will be learned.';
      });

  Future<void> saveStyle(StyleOverrides overrides) =>
      _run('save your style', () async {
        await repository.saveStyle(overrides);
        overview = await repository.overview();
        return 'Saved. Drafts use your style from now on.';
      });

  Future<void> resetStyle() => _run('reset your style', () async {
        final count = await repository.resetStyle();
        overview = await repository.overview();
        return count == 1
            ? 'Reset. 1 draft was forgotten.'
            : 'Reset. $count drafts were forgotten.';
      });

  Future<void> _reloadPreferences({bool more = false}) async {
    final page = await repository.preferences(
      query: preferenceQuery,
      offset: more ? preferences.length : 0,
    );
    preferences = more ? [...preferences, ...page.items] : page.items;
    preferenceTotal = page.total;
  }

  Future<void> searchPreferences(String query) =>
      _run('search your preferences', () async {
        preferenceQuery = query.trim();
        await _reloadPreferences();
        return preferences.isEmpty && preferenceQuery.isNotEmpty
            ? 'No preference matches "$preferenceQuery".'
            : 'Showing ${preferences.length} of $preferenceTotal.';
      });

  Future<void> morePreferences() => _run('load more preferences', () async {
        await _reloadPreferences(more: true);
        return 'Showing ${preferences.length} of $preferenceTotal.';
      });

  Future<bool> addPreference(String scope, String text) =>
      _run('save the preference', () async {
        await repository.addPreference(scope, text);
        await _reloadPreferences();
        return 'Added. ANUM follows it from the next draft.';
      });

  Future<bool> updatePreference(String id, String scope, String text) =>
      _run('save the preference', () async {
        await repository.updatePreference(id, scope, text);
        await _reloadPreferences();
        return 'Saved.';
      });

  Future<void> deletePreference(String id) =>
      _run('delete the preference', () async {
        await repository.deletePreference(id);
        await _reloadPreferences();
        return 'Deleted.';
      });

  Future<void> detectRoutines() => _run('find routines', () async {
        final (items, detected) = await repository.detectRoutines(
            routineTimezone.trim().isEmpty ? 'UTC' : routineTimezone.trim());
        routines = items;
        return switch (detected) {
          0 =>
            'No new routines. ANUM needs the same task at about the same time in three different weeks, or on five days.',
          1 => 'Found 1 new routine.',
          _ => 'Found $detected new routines.',
        };
      });

  Future<void> routineAction(String id, String action) =>
      _run('update the routine', () async {
        final next = await repository.routineAction(id, action);
        routines = [for (final r in routines) r.id == id ? next : r];
        return switch (action) {
          'accept' =>
            'Created a paused automation. Check it in the Workbench, then start its schedule.',
          'confirm' => 'The schedule is on.',
          _ => 'Dismissed. ANUM will not propose it again.',
        };
      }, denied: routinePermissionMessage);

  Future<void> deleteRoutine(String id) => _run('delete the routine', () async {
        await repository.deleteRoutine(id);
        routines = routines.where((r) => r.id != id).toList();
        return 'Deleted. It may be proposed again if you keep doing it.';
      });

  Future<void> suggestionAction(String id, String action) =>
      _run('apply the suggestion', () async {
        final next = await repository.suggestionAction(id, action);
        notes = [
          for (final note in notes)
            ImprovementNote(
              week: note.week,
              summary: note.summary,
              suggestions: [
                for (final s in note.suggestions) s.id == id ? next : s,
              ],
            ),
        ];
        if (action != 'apply') return 'Dismissed.';
        await _reloadPreferences();
        return next.changesPolicy
            ? 'Applied. The workspace approval policy changed; the change is in the audit log.'
            : 'Applied. It is now one of your learned preferences.';
      }, denied: autonomyPermissionMessage);

  Future<void> _reloadSignals({bool more = false}) async {
    final page = await repository.signals(
      query: signalQuery,
      offset: more ? signals.length : 0,
    );
    signals = more ? [...signals, ...page.items] : page.items;
    signalTotal = page.total;
  }

  Future<void> searchSignals(String query) =>
      _run('search your feedback', () async {
        signalQuery = query.trim();
        await _reloadSignals();
        return signals.isEmpty && signalQuery.isNotEmpty
            ? 'Nothing matches "$signalQuery".'
            : 'Showing ${signals.length} of $signalTotal.';
      });

  Future<void> moreSignals() => _run('load more feedback', () async {
        await _reloadSignals(more: true);
        return 'Showing ${signals.length} of $signalTotal.';
      });

  Future<void> deleteSignal(String id) => _run('delete the feedback', () async {
        await repository.deleteSignal(id);
        await _reloadSignals();
        overview = await repository.overview();
        return 'Deleted. ANUM no longer uses it.';
      });

  static bool _offline(Object error) {
    final text = error.toString().toLowerCase();
    return text.contains('socket') || text.contains('network');
  }

  /// What failed and how to fix it.
  static String describe(Object error, String what) {
    if (error is ApiException) {
      if (error.isPermissionDenied) return learningPermissionMessage;
      if (error.statusCode == 401) {
        return 'Your sign-in expired. Sign in again, then retry.';
      }
      return 'Could not $what: ${error.message}. Check your connection and try again.';
    }
    if (_offline(error)) return 'You are offline. Reconnect, then try again.';
    return 'Could not $what: $error. Try again.';
  }
}
