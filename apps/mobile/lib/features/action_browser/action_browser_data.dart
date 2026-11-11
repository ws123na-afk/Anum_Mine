import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../data/readable_error.dart';
import '../workspace/api_workspace_repository.dart'
    show workspaceApprovalFromJson;
import '../workspace/workspace_models.dart'
    show WorkspaceApproval, decisionBody;

/// The action browser on the phone (docs/action-browser.md#on-the-phone):
/// the workspace's browser runs (`GET /api/v1/action-browser/runs`), one
/// run's step log (`GET /sessions/{id}`), the approvals it waits for (the
/// ordinary approvals API, decided with the payload hash the person was
/// shown) and Stop. Saved logins and cards, the live picture and the
/// owner's input for a "Needs you" step stay on the web, and a step that
/// needs the card security code is approved there: no credential or card
/// data is read or typed on the phone.

const actionBrowserTool = 'web.act';

class BrowserStep {
  const BrowserStep(
      {required this.seq,
      required this.kind,
      required this.summary,
      this.detail = const {},
      this.at});
  final int seq;
  final String kind, summary;
  final Map<String, Object?> detail;
  final DateTime? at;
}

class BrowserRun {
  const BrowserRun(
      {required this.id,
      required this.taskId,
      required this.status,
      required this.goal,
      required this.startUrl,
      required this.sites,
      required this.savedItems,
      required this.stepCount,
      required this.canStop,
      this.waiting,
      this.currentUrl,
      this.message = '',
      this.createdAt,
      this.endedAt,
      this.steps = const []});
  final String id, taskId, status, goal, startUrl, message;
  final String? waiting, currentUrl;
  final List<String> sites;

  /// How many saved logins and cards the run may use (their names stay on
  /// the web).
  final int savedItems;
  final int stepCount;
  final bool canStop;
  final DateTime? createdAt, endedAt;
  final List<BrowserStep> steps;

  bool get active => status == 'running' || status == 'waiting';

  /// The catalog key of the status line (`statusText` on the web).
  String get statusKey {
    if (status == 'waiting') {
      return switch (waiting) {
        'handoff' => 'ab.status_waiting_handoff',
        _ => 'ab.status_waiting_approval',
      };
    }
    return switch (status) {
      'completed' => 'ab.status_completed',
      'failed' => 'ab.status_failed',
      'stopped' => 'ab.status_stopped',
      _ => 'ab.status_running',
    };
  }

  factory BrowserRun.fromJson(JsonReader j) => BrowserRun(
        id: j.string('id'),
        taskId: j.optString('task_id') ?? '',
        status: j.optString('status') ?? 'running',
        goal: j.optString('goal') ?? '',
        startUrl: j.optString('start_url') ?? '',
        sites: j.optStrings('sites') ?? const [],
        savedItems: (j.optList('logins')?.length ?? 0) +
            (j.optList('cards')?.length ?? 0),
        stepCount: j.optInt('step_count') ?? 0,
        canStop: j.optBool('can_stop') ?? false,
        waiting: j.optString('waiting'),
        currentUrl: j.optString('current_url'),
        message: j.optString('message') ?? '',
        createdAt: j.optDate('created_at'),
        endedAt: j.optDate('ended_at'),
        steps: j.optObjects(
            'steps',
            (s) => BrowserStep(
                seq: s.optInt('seq') ?? 0,
                kind: s.optString('kind') ?? '',
                summary: s.optString('summary') ?? '',
                detail: s.optMap('detail') ?? const {},
                at: s.optDate('created_at'))),
      );
}

/// A step of the log in the page language: the stored English sentence, or
/// its Arabic template (`stepText` on the web).
String browserStepText(BrowserStep step, {required bool arabic}) {
  if (!arabic) return step.summary;
  String text(String key) =>
      step.detail[key] is String ? step.detail[key] as String : '';
  final host = text('host'), label = text('label'), name = text('name');
  return switch (step.kind) {
    'opened' when host.isNotEmpty => 'فتح $host',
    'clicked' when label.isNotEmpty => 'نقر $label',
    'typed' when label.isNotEmpty => 'كتب في $label',
    'selected' when label.isNotEmpty => 'اختار في $label',
    'scrolled' => 'مرر الصفحة',
    'waited' => 'انتظر الصفحة',
    'downloaded' when name.isNotEmpty => 'حفظ $name في الملفات',
    'extracted' => 'دون معلومات من الصفحة',
    'approval' ||
    'off_site' ||
    'continue' =>
      'بانتظار موافقتك: ${step.summary.replaceFirst('Waiting for your approval: ', '')}',
    'needs_you' => 'يحتاجك: ${step.summary.replaceFirst('Needs you: ', '')}',
    'approved' => 'وافق المالك',
    'page_changed' => 'تغيرت الصفحة؛ يسأل مرة أخرى',
    'done' => 'تم: ${step.summary}',
    'stopped' => 'أوقفه المالك',
    _ => step.summary,
  };
}

/// What a web.act approval asks for, read from its arguments; never a
/// secret (the arguments carry handles and the masked values only).
class BrowserStepFacts {
  const BrowserStepFacts(
      {required this.kindKey,
      this.site = '',
      this.url = '',
      this.target = '',
      this.amount = ''});
  final String kindKey, site, url, target, amount;

  factory BrowserStepFacts.of(WorkspaceApproval a) {
    String s(String key) =>
        a.arguments[key] is String ? a.arguments[key] as String : '';
    final resume = s('resume').isEmpty ? 'scope' : s('resume');
    final kind = switch (resume) {
      'scope' || 'handoff' || 'continue' => resume,
      _ => s('kind').isEmpty ? 'binding_button' : s('kind'),
    };
    return BrowserStepFacts(
        kindKey: 'ab.kind_$kind',
        site: s('site'),
        url: s('url').isEmpty ? s('start_url') : s('url'),
        target: s('target'),
        amount: s('amount_text'));
  }

  /// A "Needs you" step: the owner completes a check on the page itself.
  bool get handoff => kindKey == 'ab.kind_handoff';
}

class BrowserRunPage {
  const BrowserRunPage({required this.items, this.nextOffset});
  final List<BrowserRun> items;
  final int? nextOffset;
}

abstract interface class ActionBrowserRepository {
  Future<BrowserRunPage> runs({int offset = 0, int limit = 20});
  Future<BrowserRun> run(String id);
  Future<BrowserRun> stop(String id);

  /// Pending web.act approvals of [taskId].
  Future<List<WorkspaceApproval>> approvals(String taskId);
  Future<void> decide(WorkspaceApproval approval, {required bool approve});
}

class ApiActionBrowserRepository implements ActionBrowserRepository {
  const ApiActionBrowserRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/action-browser';

  @override
  Future<BrowserRunPage> runs({int offset = 0, int limit = 20}) async {
    final j = JsonReader(
        await api.request('GET', '$_base/runs?limit=$limit&offset=$offset'));
    return BrowserRunPage(
        items: j.optObjects('items', BrowserRun.fromJson),
        nextOffset: j.optInt('next_offset'));
  }

  @override
  Future<BrowserRun> run(String id) async => BrowserRun.fromJson(JsonReader(
      await api.request('GET', '$_base/sessions/${Uri.encodeComponent(id)}')));

  @override
  Future<BrowserRun> stop(String id) async =>
      BrowserRun.fromJson(JsonReader(await api.request(
          'POST', '$_base/sessions/${Uri.encodeComponent(id)}/stop')));

  @override
  Future<List<WorkspaceApproval>> approvals(String taskId) async {
    final j = JsonReader(
        await api.request('GET', '/api/v1/approvals?status=pending&limit=200'));
    return [
      for (final a in j.optObjects('data', workspaceApprovalFromJson))
        if (a.taskId == taskId &&
            a.action == actionBrowserTool &&
            a.status == 'pending')
          a
    ];
  }

  @override
  Future<void> decide(WorkspaceApproval approval,
      {required bool approve}) async {
    final body = decisionBody(approval, null);
    await api.request(
        'POST',
        '/api/v1/approvals/${Uri.encodeComponent(approval.id)}/'
            '${approve ? 'approve' : 'reject'}',
        body: body.isEmpty ? null : body);
  }
}

enum RunsPhase { loading, ready, denied, error }

String _message(Object e) => e is ApiException ? e.readable : readableError(e);

/// The runs list, newest first, a page at a time.
class ActionBrowserController extends ChangeNotifier {
  ActionBrowserController(this.repository);
  final ActionBrowserRepository repository;
  static const pageSize = 20;

  RunsPhase phase = RunsPhase.loading;
  String? error;
  List<BrowserRun> runs = const [];
  int? nextOffset;
  bool loadingMore = false;
  String? moreError;

  Future<void> load() async {
    phase = RunsPhase.loading;
    error = null;
    notifyListeners();
    try {
      final page = await repository.runs(limit: pageSize);
      runs = page.items;
      nextOffset = page.nextOffset;
      phase = RunsPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? RunsPhase.denied : RunsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = RunsPhase.error;
      error = _message(e);
    }
    notifyListeners();
  }

  Future<void> more() async {
    final offset = nextOffset;
    if (offset == null || loadingMore) return;
    loadingMore = true;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.runs(offset: offset, limit: pageSize);
      runs = [...runs, ...page.items];
      nextOffset = page.nextOffset;
    } on Object catch (e) {
      moreError = _message(e);
    }
    loadingMore = false;
    notifyListeners();
  }
}

enum RunPhase { loading, ready, notFound, error }

/// One run: its steps, what it waits for, Stop.
class BrowserRunController extends ChangeNotifier {
  BrowserRunController(this.repository, this.runId);
  final ActionBrowserRepository repository;
  final String runId;

  RunPhase phase = RunPhase.loading;
  String? error;
  BrowserRun? run;
  List<WorkspaceApproval> approvals = const [];
  String? approvalsError;
  String? busy;

  /// A catalog key, or the API's sentence in [noteRaw].
  String? noteKey;
  String? noteRaw;
  bool noteFailed = false;

  Future<void> load() async {
    phase = run == null ? RunPhase.loading : phase;
    error = null;
    notifyListeners();
    try {
      run = await repository.run(runId);
      phase = RunPhase.ready;
    } on ApiException catch (e) {
      phase = e.statusCode == 404 ? RunPhase.notFound : RunPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = RunPhase.error;
      error = _message(e);
    }
    notifyListeners();
    final current = run;
    if (phase == RunPhase.ready && current != null) {
      if (current.active && current.taskId.isNotEmpty) {
        try {
          approvals = await repository.approvals(current.taskId);
          approvalsError = null;
        } on Object catch (e) {
          approvalsError = _message(e);
        }
      } else {
        approvals = const [];
      }
      notifyListeners();
    }
  }

  void _note(String? key, {String? raw, bool failed = false}) {
    noteKey = key;
    noteRaw = raw;
    noteFailed = failed;
  }

  Future<void> decide(WorkspaceApproval approval,
      {required bool approve}) async {
    if (busy != null) return;
    // A step that needs the card security code is approved on the web,
    // where the code is typed; the phone never sends one.
    if (approve && (approval.needsCardCodeOnWeb || !approval.canApprove())) {
      return;
    }
    busy = approval.id;
    _note(null);
    notifyListeners();
    try {
      await repository.decide(approval, approve: approve);
      _note(approve ? 'od.ab.approved' : 'od.ab.rejected');
    } on Object catch (e) {
      _note('od.ab.decideFailed', raw: _message(e), failed: true);
    }
    busy = null;
    notifyListeners();
    await load();
  }

  Future<void> stop() async {
    final current = run;
    if (current == null || busy != null) return;
    busy = 'stop';
    _note(null);
    notifyListeners();
    try {
      run = await repository.stop(current.id);
      approvals = const [];
      _note('ab.stopped');
    } on Object catch (e) {
      _note('od.ab.stopFailed', raw: _message(e), failed: true);
    }
    busy = null;
    notifyListeners();
  }
}
