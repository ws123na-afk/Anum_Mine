import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/sse.dart';
import '../voice/speech_service.dart';
import 'command_models.dart';
import 'command_repository.dart';
import 'command_transfer.dart';

/// Where the person is in the flow (docs/command.md).
enum CommandStage { start, listening, understanding, plan, executing, results }

enum LoadPhase { idle, loading, ready, error }

enum FailureKind { permission, offline, notFound, other }

class CommandFailure {
  const CommandFailure(this.kind, this.message);

  factory CommandFailure.of(Object error) {
    if (error is ApiException) {
      if (error.isPermissionDenied) {
        return CommandFailure(FailureKind.permission, error.readable);
      }
      if (error.statusCode == 0) {
        return CommandFailure(FailureKind.offline, error.readable);
      }
      if (error.statusCode == 404) {
        return CommandFailure(FailureKind.notFound, error.readable);
      }
      return CommandFailure(FailureKind.other, error.readable);
    }
    if (error is EventStreamTimeoutException) {
      return CommandFailure(FailureKind.offline, error.message);
    }
    if (error is FormatException) {
      return CommandFailure(FailureKind.other, error.message);
    }
    return CommandFailure(FailureKind.other, '$error');
  }

  final FailureKind kind;
  final String message;
}

/// The outcome of "Approve all": how many were approved and what failed.
class ApproveOutcome {
  const ApproveOutcome(this.approved, this.failures);
  final int approved;
  final List<String> failures;
}

/// Drives the ANUM Command flow. Lives as long as the app, so a command keeps
/// being followed after "Keep working in background".
class CommandController extends ChangeNotifier {
  CommandController({
    required this.repository,
    required this.speech,
    required this.transfer,
    this.pollInterval = const Duration(seconds: 3),
    this.refreshDelay = const Duration(milliseconds: 400),
    this.reconnectDelay = const Duration(seconds: 10),
  });

  final CommandRepository repository;
  final SpeechService speech;
  final CommandTransfer transfer;
  final Duration pollInterval;
  final Duration refreshDelay;
  final Duration reconnectDelay;

  /// Called once when a followed command stops running (finished, or waiting
  /// for approval), for a notice while the person is elsewhere.
  void Function(CommandView command)? onSettled;

  /// True while the Command screen is open (no notice is needed then).
  bool onScreen = false;

  bool _disposed = false;

  // -- language ----------------------------------------------------------------

  /// `en` or `ar`: speech recognition and the language of the plan.
  String language = 'en';
  String get speechLocale => language == 'ar' ? 'ar-SA' : 'en-US';

  void setLanguage(String code) {
    if (code != 'en' && code != 'ar') return;
    language = code;
    _notify();
  }

  // -- stage ---------------------------------------------------------------------

  CommandStage stage = CommandStage.start;

  void goTo(CommandStage value) {
    stage = value;
    _notify();
  }

  /// Back one stage; false when the screen itself should close.
  bool back() {
    switch (stage) {
      case CommandStage.start:
        return false;
      case CommandStage.listening:
        unawaited(cancelListening());
        return true;
      case CommandStage.understanding:
      case CommandStage.plan:
        stage = CommandStage.start;
      case CommandStage.executing:
      case CommandStage.results:
        stage = CommandStage.start;
    }
    _notify();
    return true;
  }

  // -- suggestions -------------------------------------------------------------

  LoadPhase suggestionsPhase = LoadPhase.idle;
  List<CommandSuggestion> suggestions = const [];

  Future<void> loadSuggestions() async {
    suggestionsPhase = LoadPhase.loading;
    _notify();
    try {
      suggestions = await repository.suggestions(language);
      suggestionsPhase = LoadPhase.ready;
    } on Object {
      suggestionsPhase = LoadPhase.error;
    }
    _notify();
  }

  // -- attachments ---------------------------------------------------------------

  List<CommandAttachment> attachments = const [];
  bool attaching = false;
  CommandFailure? attachError;

  Future<void> attachFile() async {
    if (attaching || attachments.length >= 5) return;
    attachError = null;
    final path = await transfer.pickFile();
    if (path == null) return;
    attaching = true;
    _notify();
    try {
      final attachment = await transfer.attach(path);
      attachments = [...attachments, attachment];
    } on Object catch (error) {
      attachError = CommandFailure.of(error);
    }
    attaching = false;
    _notify();
  }

  void removeAttachment(CommandAttachment attachment) {
    attachments = [
      for (final item in attachments)
        if (item.fileId != attachment.fileId) item
    ];
    _notify();
  }

  // -- listening -----------------------------------------------------------------

  String transcript = '';
  bool listeningActive = false;
  Duration elapsed = Duration.zero;
  final ValueNotifier<double> level = ValueNotifier(0);
  SpeechAvailability? micState;
  bool heardNothing = false;
  Timer? _clock;

  Future<void> startListening() async {
    stage = CommandStage.listening;
    transcript = '';
    heardNothing = false;
    elapsed = Duration.zero;
    _notify();
    micState = await speech.initialize();
    if (micState != SpeechAvailability.ready) {
      _notify();
      return;
    }
    listeningActive = true;
    _clock?.cancel();
    _clock = Timer.periodic(const Duration(seconds: 1), (_) {
      elapsed += const Duration(seconds: 1);
      _notify();
    });
    _notify();
    await speech.listen(
      locale: speechLocale,
      pauseFor: const Duration(seconds: 4),
      listenFor: const Duration(seconds: 60),
      onResult: (text, isFinal) {
        transcript = text;
        _notify();
      },
      onLevel: (value) => level.value = ((value + 2) / 12).clamp(0, 1),
      onError: (_, __) {},
      onDone: _listeningDone,
    );
  }

  void _listeningDone() {
    listeningActive = false;
    _clock?.cancel();
    level.value = 0;
    heardNothing = transcript.trim().isEmpty;
    _notify();
  }

  /// Stop listening and keep what was heard (it stays editable).
  Future<void> stopListening() async {
    await speech.stop();
    _listeningDone();
  }

  Future<void> cancelListening() async {
    if (listeningActive) await speech.cancel();
    listeningActive = false;
    _clock?.cancel();
    level.value = 0;
    stage = CommandStage.start;
    _notify();
  }

  Future<void> openMicSettings() => speech.openSettings();

  // -- understanding and plan ------------------------------------------------------

  String requestText = '';
  String source = 'typed';
  String? quickCommandId;
  bool planning = false;
  CommandFailure? planError;
  CommandPlan? plan;
  List<PlanStep> steps = const [];

  /// Plans [text] (the Understanding screen, then the plan).
  Future<void> submit(String text,
      {String source = 'typed', String? quickCommandId}) async {
    final cleaned = text.trim();
    if (cleaned.isEmpty) return;
    if (listeningActive) await stopListening();
    requestText = cleaned;
    this.source = source;
    this.quickCommandId = quickCommandId;
    await _plan();
  }

  /// The person corrected the request: plan it again.
  Future<void> editRequest(String text) async {
    final cleaned = text.trim();
    if (cleaned.isEmpty || cleaned == requestText) return;
    requestText = cleaned;
    quickCommandId = null;
    await _plan();
  }

  Future<void> _plan() async {
    stage = CommandStage.understanding;
    planning = true;
    planError = null;
    plan = null;
    steps = const [];
    _notify();
    try {
      final result = await repository.preview(requestText, language);
      plan = result;
      steps = List.of(result.steps);
      stage = CommandStage.plan;
    } on Object catch (error) {
      planError = CommandFailure.of(error);
    }
    planning = false;
    _notify();
  }

  Future<void> retryPlan() => _plan();

  void removeStep(int index) {
    steps = [...steps]..removeAt(index);
    _notify();
  }

  void moveStep(int index, int delta) {
    final target = index + delta;
    if (target < 0 || target >= steps.length) return;
    final list = [...steps];
    final step = list.removeAt(index);
    list.insert(target, step);
    steps = list;
    _notify();
  }

  void editStep(int index, String instruction) {
    final cleaned = instruction.trim();
    if (cleaned.isEmpty) return;
    steps = [...steps]..[index] = steps[index].withInstruction(cleaned);
    _notify();
  }

  bool get hasBlockedStep =>
      steps.any((step) => step.approval == ApprovalNeed.blocked);
  int get approvalsAhead =>
      steps.where((step) => step.approval == ApprovalNeed.required).length;
  bool get canStart => steps.isNotEmpty && !hasBlockedStep && !starting;

  // -- execution -----------------------------------------------------------------

  CommandView? command;
  bool starting = false;
  CommandFailure? runError;
  CommandFailure? loadError;

  /// Why a step could not be started (budget used up, permission), by task id.
  Map<String, String> stepErrors = const {};
  final Set<String> _inFlight = {};

  /// True while the live event stream is connected.
  bool live = false;
  StreamSubscription<SseEvent>? _events;
  Timer? _poll;
  Timer? _refreshSoon;
  Timer? _reconnect;
  bool _settledNotified = false;

  bool get running => command?.status.active ?? false;

  Future<void> start() async {
    if (!canStart) return;
    starting = true;
    runError = null;
    _notify();
    try {
      final created = await repository.create(
        text: requestText,
        steps: [for (final step in steps) step.instruction],
        source: source,
        locale: language,
        attachments: [for (final item in attachments) item.fileId],
        quickCommandId: quickCommandId,
      );
      attachments = const [];
      _follow(created);
      stage = CommandStage.executing;
      starting = false;
      _notify();
      await _runSteps(created.steps.map((step) => step.taskId).toList());
    } on Object catch (error) {
      runError = CommandFailure.of(error);
      starting = false;
      _notify();
    }
  }

  /// Steps whose task was created but never started (the app closed while
  /// starting them): offered as "Start the remaining steps".
  List<CommandStepView> get notStarted => [
        for (final step in command?.steps ?? const <CommandStepView>[])
          if (step.status == 'created' && !_inFlight.contains(step.taskId)) step
      ];

  Future<void> startRemaining() =>
      _runSteps([for (final step in notStarted) step.taskId]);

  Future<void> _runSteps(List<String> taskIds) async {
    _inFlight.addAll(taskIds);
    _notify();
    await Future.wait([
      for (final id in taskIds)
        repository.runTask(id).then((_) {}, onError: (Object error) {
          stepErrors = {...stepErrors, id: CommandFailure.of(error).message};
        }).whenComplete(() {
          _inFlight.remove(id);
          _scheduleRefresh();
        }),
    ]);
    await refresh();
  }

  /// Opens a command (from history, a notice or "Running now").
  Future<void> open(String commandId) async {
    loadError = null;
    stage = CommandStage.executing;
    if (command?.id != commandId) {
      command = null;
      stepErrors = const {};
    }
    _notify();
    try {
      _follow(await repository.load(commandId));
    } on Object catch (error) {
      loadError = CommandFailure.of(error);
    }
    _notify();
  }

  void _follow(CommandView view) {
    final changed = command?.id != view.id;
    command = view;
    if (changed) {
      _settledNotified = false;
      nextSteps = const [];
      nextPhase = LoadPhase.idle;
      approveOutcome = null;
    }
    _afterUpdate();
  }

  void _afterUpdate() {
    final view = command;
    if (view == null) return;
    final stillRunning =
        view.status == CommandStatus.running || _inFlight.isNotEmpty;
    // Waiting on a person: keep listening, an approval changes it.
    if (stillRunning || view.status == CommandStatus.waitingApproval) {
      _watch();
    } else {
      _unwatch();
    }
    if (!stillRunning &&
        view.status != CommandStatus.ready &&
        !_settledNotified) {
      _settledNotified = true;
      if (stage == CommandStage.executing) stage = CommandStage.results;
      unawaited(loadNextSteps());
      onSettled?.call(view);
    }
  }

  void _watch() {
    if (_events != null || _disposed) return;
    _events = transfer.events().listen(
      (event) {
        live = true;
        final view = command;
        if (view == null) return;
        final fields = event.fields;
        final subject = fields['subject'];
        final payload = fields['payload'];
        final taskId = payload is Map ? payload['task_id'] : null;
        if (view.taskIds.contains(subject) || view.taskIds.contains(taskId)) {
          _scheduleRefresh();
        }
      },
      onError: (Object _) => _streamLost(),
      onDone: _streamLost,
      cancelOnError: true,
    );
    _poll?.cancel();
    // Until the stream proves itself, and while it is down, poll.
    _poll = Timer.periodic(pollInterval, (_) {
      if (!live) unawaited(refresh());
    });
  }

  void _streamLost() {
    live = false;
    _events = null;
    _notify();
    _reconnect?.cancel();
    _reconnect = Timer(reconnectDelay, () {
      if (command?.status.active ?? false) _watch();
    });
  }

  void _unwatch() {
    unawaited(_events?.cancel());
    _events = null;
    _poll?.cancel();
    _poll = null;
    _reconnect?.cancel();
    live = false;
  }

  void _scheduleRefresh() {
    _refreshSoon?.cancel();
    _refreshSoon = Timer(refreshDelay, () => unawaited(refresh()));
  }

  Future<void> refresh() async {
    final current = command;
    if (current == null || _disposed) return;
    try {
      final view = await repository.load(current.id);
      if (_disposed || command?.id != view.id) return;
      command = view;
      loadError = null;
      _afterUpdate();
    } on Object catch (error) {
      loadError = CommandFailure.of(error);
    }
    _notify();
  }

  bool stopping = false;

  Future<void> stop() async {
    final view = command;
    if (view == null || stopping) return;
    stopping = true;
    _notify();
    try {
      await repository.cancelTasks([
        for (final step in view.steps)
          if (!step.terminal) step.taskId
      ]);
    } on Object catch (error) {
      runError = CommandFailure.of(error);
    }
    stopping = false;
    await refresh();
  }

  // -- results ---------------------------------------------------------------------

  List<NextStep> nextSteps = const [];
  LoadPhase nextPhase = LoadPhase.idle;
  bool approving = false;
  ApproveOutcome? approveOutcome;

  Future<void> loadNextSteps() async {
    final view = command;
    if (view == null) return;
    nextPhase = LoadPhase.loading;
    _notify();
    try {
      nextSteps = await repository.nextSteps(view.id);
      nextPhase = LoadPhase.ready;
    } on Object {
      nextPhase = LoadPhase.error;
    }
    _notify();
  }

  /// Approves the command's pending approvals one at a time, each with the
  /// payload hash that was shown; one refusal does not stop the others.
  Future<ApproveOutcome> approveAll() async {
    final view = command;
    if (view == null || approving) return const ApproveOutcome(0, []);
    approving = true;
    _notify();
    var approved = 0;
    final failures = <String>[];
    for (final approval in view.pending) {
      try {
        await repository.approve(approval);
        approved++;
      } on Object catch (error) {
        failures.add('${approval.action}: ${CommandFailure.of(error).message}');
      }
    }
    approveOutcome = ApproveOutcome(approved, failures);
    approving = false;
    _settledNotified = false;
    await refresh();
    return approveOutcome!;
  }

  Future<Uint8List> download(CommandFile file) => transfer.download(file.path);

  Future<bool> saveFile(CommandFile file) async =>
      transfer.save(file.name, await transfer.download(file.path));

  Future<bool> saveReport() async {
    final view = command;
    if (view == null) return false;
    final bytes = await transfer.download(
        '/api/v1/commands/${Uri.encodeComponent(view.id)}/report?format=markdown');
    return transfer.save('anum-command-report.md', bytes);
  }

  /// The report as text, for the full report view.
  Future<String> reportText() async {
    final view = command;
    if (view == null) return '';
    final bytes = await transfer.download(
        '/api/v1/commands/${Uri.encodeComponent(view.id)}/report?format=markdown');
    return utf8.decode(bytes, allowMalformed: true);
  }

  /// Starts a new command from a next step, a suggestion or a quick command.
  Future<void> startFrom(String text,
      {required String source, String? quickCommandId}) async {
    stage = CommandStage.start;
    await submit(text, source: source, quickCommandId: quickCommandId);
  }

  void newCommand() {
    stage = CommandStage.start;
    requestText = '';
    plan = null;
    steps = const [];
    planError = null;
    runError = null;
    _notify();
    unawaited(loadSuggestions());
  }

  // -- history ---------------------------------------------------------------------

  List<CommandSummary> history = const [];
  LoadPhase historyPhase = LoadPhase.idle;
  CommandFailure? historyError;
  bool historyHasMore = false;
  static const historyPage = 20;

  Future<void> loadHistory({bool more = false}) async {
    historyPhase = LoadPhase.loading;
    _notify();
    try {
      final page = await repository.list(
          cursor: more && history.isNotEmpty ? history.last.id : null,
          limit: historyPage);
      history = more ? [...history, ...page] : page;
      historyHasMore = page.length == historyPage;
      historyPhase = LoadPhase.ready;
    } on Object catch (error) {
      historyError = CommandFailure.of(error);
      historyPhase = LoadPhase.error;
    }
    _notify();
  }

  Future<CommandFailure?> removeFromHistory(CommandSummary item) async {
    try {
      await repository.delete(item.id);
      history = [
        for (final entry in history)
          if (entry.id != item.id) entry
      ];
      _notify();
      return null;
    } on Object catch (error) {
      return CommandFailure.of(error);
    }
  }

  // -- quick commands --------------------------------------------------------------

  List<QuickCommand> quick = const [];
  LoadPhase quickPhase = LoadPhase.idle;
  CommandFailure? quickError;

  List<QuickCommand> quickIn(QuickCategory category) => [
        for (final item in quick)
          if (item.category == category) item
      ]..sort((a, b) => a.position.compareTo(b.position));

  Future<void> loadQuick() async {
    quickPhase = LoadPhase.loading;
    quickError = null;
    _notify();
    try {
      quick = await repository.quickCommands();
      quickPhase = LoadPhase.ready;
    } on Object catch (error) {
      quickError = CommandFailure.of(error);
      quickPhase = LoadPhase.error;
    }
    _notify();
  }

  /// Creates or updates a quick command; returns the failure, if any.
  Future<CommandFailure?> saveQuick(
      {QuickCommand? existing,
      required String title,
      required String text,
      required QuickCategory category}) async {
    try {
      if (existing == null) {
        final created = await repository.createQuick(
            title: title.trim(), text: text.trim(), category: category);
        quick = [...quick, created];
      } else {
        final updated = await repository.updateQuick(existing.id,
            title: title.trim(), text: text.trim(), category: category);
        quick = [
          for (final item in quick) item.id == updated.id ? updated : item
        ];
      }
      _notify();
      unawaited(loadSuggestions());
      return null;
    } on Object catch (error) {
      return CommandFailure.of(error);
    }
  }

  Future<CommandFailure?> deleteQuick(QuickCommand item) async {
    try {
      await repository.deleteQuick(item.id);
      quick = [
        for (final entry in quick)
          if (entry.id != item.id) entry
      ];
      _notify();
      return null;
    } on Object catch (error) {
      return CommandFailure.of(error);
    }
  }

  /// Swaps [item] with its neighbour in its category.
  Future<CommandFailure?> moveQuick(QuickCommand item, int delta) async {
    final list = quickIn(item.category);
    final index = list.indexWhere((entry) => entry.id == item.id);
    final target = index + delta;
    if (index < 0 || target < 0 || target >= list.length) return null;
    final other = list[target];
    // Positions can repeat (set by hand on the web); the list order decides.
    final same = other.position == item.position;
    try {
      final first = await repository.updateQuick(item.id,
          position: same ? target : other.position);
      final second = await repository.updateQuick(other.id,
          position: same ? index : item.position);
      quick = [
        for (final entry in quick)
          entry.id == first.id
              ? first
              : entry.id == second.id
                  ? second
                  : entry
      ];
      _notify();
      return null;
    } on Object catch (error) {
      return CommandFailure.of(error);
    }
  }

  /// "Save this as a quick command" from the next steps.
  Future<CommandFailure?> saveAsQuick(String text) {
    final title = text.length > 40 ? '${text.substring(0, 39)}…' : text;
    return saveQuick(title: title, text: text, category: QuickCategory.custom);
  }

  /// Clears everything when the person signs out.
  void reset() {
    _unwatch();
    stage = CommandStage.start;
    command = null;
    history = const [];
    quick = const [];
    suggestions = const [];
    attachments = const [];
    suggestionsPhase = LoadPhase.idle;
    quickPhase = LoadPhase.idle;
    historyPhase = LoadPhase.idle;
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _unwatch();
    _clock?.cancel();
    _refreshSoon?.cancel();
    level.dispose();
    super.dispose();
  }
}
