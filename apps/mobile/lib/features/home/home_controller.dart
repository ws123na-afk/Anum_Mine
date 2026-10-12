import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'home_models.dart';
import 'home_repository.dart';

enum HomePhase { idle, loading, ready, error, denied }

/// Home's state (docs/home.md): the summary, the caller's pins and quick
/// commands, the selected command mode and the proactive insights.
class HomeController extends ChangeNotifier {
  HomeController(this.repository);
  final HomeRepository repository;

  HomePhase phase = HomePhase.idle;
  String? error;
  HomeSummary? summary;

  List<HomePin> pins = const [];
  String? pinsError;

  List<QuickCommand> commands = const [];
  bool canEditCommands = false;
  String? commandsError;
  CommandMode mode = CommandMode.work;

  HomePhase insightsPhase = HomePhase.idle;
  String? insightsError;
  List<Insight> insights = const [];
  int hiddenInsights = 0;
  bool showHidden = false;
  List<HomeSourceStatus> insightSources = const [];

  /// The key of the pin, command or insight being changed.
  String? busy;

  /// The last action's outcome, said once by the screen.
  String? notice;
  bool noticeIsError = false;

  /// The last failed action's message, kept for sheets and dialogs (the
  /// screen's snack bar would sit under them).
  String? lastError;

  static String _describe(Object e) => e is ApiException ? e.readable : '$e';

  List<QuickCommand> get modeCommands =>
      commands.where((c) => c.mode == mode).toList();

  /// Insights still waiting for the user, for the badge on Home.
  int get openInsights => insights.where((i) => !i.hidden).length;

  Future<void> load() async {
    if (summary == null) phase = HomePhase.loading;
    error = null;
    notifyListeners();
    await Future.wait([
      _loadSummary(),
      _loadPins(),
      _loadCommands(),
      loadInsights(quiet: true),
    ]);
    notifyListeners();
  }

  Future<void> _loadSummary() async {
    try {
      summary = await repository.summary();
      phase = HomePhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? HomePhase.denied : HomePhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = HomePhase.error;
      error = _describe(e);
    }
  }

  Future<void> _loadPins() async {
    try {
      pins = await repository.pins();
      pinsError = null;
    } on Object catch (e) {
      pinsError = _describe(e);
    }
  }

  Future<void> _loadCommands() async {
    try {
      final list = await repository.commands();
      commands = list.items;
      canEditCommands = list.canEdit;
      commandsError = null;
    } on Object catch (e) {
      commandsError = _describe(e);
    }
  }

  void selectMode(CommandMode value) {
    if (mode == value) return;
    mode = value;
    notifyListeners();
  }

  void _say(String message, {bool error = false}) {
    notice = message;
    noticeIsError = error;
  }

  /// Takes the pending notice, so it is shown once.
  String? takeNotice() {
    final value = notice;
    notice = null;
    return value;
  }

  Future<bool> _run(String key, Future<void> Function() action) async {
    busy = key;
    notifyListeners();
    var ok = true;
    lastError = null;
    try {
      await action();
    } on Object catch (e) {
      ok = false;
      lastError = _describe(e);
      _say(lastError!, error: true);
    }
    busy = null;
    notifyListeners();
    return ok;
  }

  // -- greeting name ---------------------------------------------------------

  Future<bool> setDisplayName(String? name) => _run('name', () async {
        final trimmed = name?.trim();
        final saved = await repository.setDisplayName(
            trimmed == null || trimmed.isEmpty ? null : trimmed);
        summary = summary?.withName(saved);
      });

  // -- pins --------------------------------------------------------------------

  Future<bool> addPin(
          {required PinKind kind,
          required String label,
          required String target,
          String? icon}) =>
      _run('pin:new', () async {
        final pin = await repository.addPin(
            kind: kind, label: label, target: target, icon: icon);
        pins = [...pins, pin];
      });

  Future<bool> renamePin(HomePin pin, String label) =>
      _run('pin:${pin.id}', () async {
        final saved = await repository.updatePin(pin.id, label: label);
        pins = [for (final p in pins) p.id == pin.id ? saved : p];
      });

  Future<bool> removePin(HomePin pin) => _run('pin:${pin.id}', () async {
        await repository.removePin(pin.id);
        pins = pins.where((p) => p.id != pin.id).toList();
      });

  /// Moves [pin] by [delta] places (keyboard and screen-reader friendly).
  Future<bool> movePin(HomePin pin, int delta) {
    final index = pins.indexWhere((p) => p.id == pin.id);
    final to = index + delta;
    if (index < 0 || to < 0 || to >= pins.length) return Future.value(false);
    final order = [...pins]..removeAt(index);
    order.insert(to, pin);
    return reorderPins(order);
  }

  Future<bool> reorderPins(List<HomePin> order) {
    final previous = pins;
    pins = order; // optimistic: the list moves at once
    return _run('pin:order', () async {
      try {
        pins = await repository.reorderPins([for (final p in order) p.id]);
      } on Object {
        pins = previous;
        rethrow;
      }
    });
  }

  Future<List<PinnableWorkspace>> workspaces() => repository.workspaces();

  // -- quick commands ---------------------------------------------------------

  Future<bool> addCommand(
          {required CommandMode mode,
          required String label,
          required String prompt}) =>
      _run('cmd:new', () async {
        final command = await repository.addCommand(
            mode: mode, label: label, prompt: prompt);
        commands = [...commands, command];
        this.mode = command.mode;
      });

  Future<bool> updateCommand(QuickCommand command,
          {CommandMode? mode, String? label, String? prompt}) =>
      _run('cmd:${command.id}', () async {
        final saved = await repository.updateCommand(command.id,
            mode: mode, label: label, prompt: prompt);
        commands = [for (final c in commands) c.id == command.id ? saved : c];
      });

  Future<bool> removeCommand(QuickCommand command) =>
      _run('cmd:${command.id}', () async {
        await repository.removeCommand(command.id);
        commands = commands.where((c) => c.id != command.id).toList();
      });

  // -- insights ------------------------------------------------------------------

  Future<void> loadInsights({bool quiet = false}) async {
    if (insights.isEmpty) insightsPhase = HomePhase.loading;
    insightsError = null;
    if (!quiet) notifyListeners();
    try {
      final list = await repository.insights(includeHidden: showHidden);
      insights = list.items;
      hiddenInsights = list.hidden;
      insightSources = list.sources;
      insightsPhase = HomePhase.ready;
    } on ApiException catch (e) {
      insightsPhase = e.isPermissionDenied ? HomePhase.denied : HomePhase.error;
      insightsError = e.readable;
    } on Object catch (e) {
      insightsPhase = HomePhase.error;
      insightsError = _describe(e);
    }
    if (!quiet) notifyListeners();
  }

  Future<void> toggleHidden(bool value) async {
    showHidden = value;
    await loadInsights();
  }

  void _replace(Insight updated) {
    if (showHidden) {
      insights = [for (final i in insights) i.key == updated.key ? updated : i];
    } else {
      insights = insights.where((i) => i.key != updated.key).toList();
    }
    hiddenInsights += 1;
  }

  Future<bool> dismiss(Insight insight) =>
      _run('insight:${insight.key}', () async {
        _replace(await repository.dismiss(insight.key));
      });

  Future<bool> snooze(Insight insight, {int hours = 24}) =>
      _run('insight:${insight.key}', () async {
        _replace(await repository.snooze(insight.key, hours: hours));
      });

  Future<bool> restore(Insight insight) =>
      _run('insight:${insight.key}', () async {
        await repository.restore(insight.key);
        await loadInsights(quiet: true);
      });
}
