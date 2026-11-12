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
/// shown) and Stop; for owners also the live picture (`GET /sessions/{id}/
/// frame`, masked by the browser service, held in memory only), their own
/// input for a "Needs you" step (`POST /sessions/{id}/input`) and the saved
/// logins and cards (`/credentials`): names and masked hints only, a secret
/// is typed once into a hidden field, sent, and never stored on the phone or
/// shown back. A step that needs the card security code is still approved on
/// the web, where the code is typed.

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
      this.live = false,
      this.canWatch = false,
      this.logins = const [],
      this.cards = const [],
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

  /// A browser is open for this run now (a picture can be taken).
  final bool live;

  /// The caller is an owner: sees the live picture and gives input.
  final bool canWatch;

  /// The saved items' handles the run may use (never their values).
  final List<String> logins, cards;

  /// Waiting for the owner's own hands on the page ("Needs you").
  bool get needsYou => status == 'waiting' && waiting == 'handoff';
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
        live: j.optBool('live') ?? false,
        canWatch: j.optBool('can_watch') ?? false,
        logins: j.optStrings('logins') ?? const [],
        cards: j.optStrings('cards') ?? const [],
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

/// The live picture's page size (docs/action-browser.md#the-live-picture).
const browserFrameWidth = 1280.0, browserFrameHeight = 800.0;

/// A point on the shown picture as a point on the page, or null outside it
/// (`toPagePoint` on the web). The picture is never mirrored in Arabic.
({int x, int y})? browserPagePoint(
    double dx, double dy, double width, double height) {
  if (width <= 0 || height <= 0) return null;
  final x = (dx / width * browserFrameWidth).round();
  final y = (dy / height * browserFrameHeight).round();
  if (x < 0 || y < 0 || x > browserFrameWidth || y > browserFrameHeight) {
    return null;
  }
  return (x: x, y: y);
}

/// The keys the owner may press on the page (`OWNER_KEYS` on the web).
const browserOwnerKeys = [
  'Enter',
  'Tab',
  'Backspace',
  'Escape',
  'ArrowUp',
  'ArrowDown'
];

/// A saved login or card as the API lists it: the handle, the site and a
/// masked hint. The secret parts never come back.
class BrowserCredential {
  const BrowserCredential(
      {required this.id,
      required this.kind,
      required this.handle,
      required this.hint,
      this.site,
      this.updatedAt});
  final String id, kind, handle, hint;
  final String? site;
  final DateTime? updatedAt;

  bool get isLogin => kind == 'login';

  factory BrowserCredential.fromJson(JsonReader j) => BrowserCredential(
      id: j.string('id'),
      kind: j.optString('kind') ?? 'login',
      handle: j.optString('handle') ?? '',
      hint: j.optString('hint') ?? '',
      site: j.optString('site'),
      updatedAt: j.optDate('updated_at'));
}

/// What a login or card form sends; empty secret fields keep the saved ones.
class CredentialDraft {
  CredentialDraft(
      {required this.kind,
      this.handle = '',
      this.site = '',
      this.username = '',
      this.password = '',
      this.name = '',
      this.number = '',
      this.expiry = ''});
  final String kind;
  String handle, site, username, password, name, number, expiry;

  /// The catalog key of the first problem (`credentialProblem` on the web),
  /// or null when it may be sent.
  String? problem({required bool editing}) {
    if (!RegExp(r'^[a-z0-9][a-z0-9-]{0,39}$').hasMatch(handle.trim())) {
      return 'ab.handleRule';
    }
    if (kind == 'login') {
      if (!editing && site.trim().isEmpty) return 'ab.siteNeeded';
      if (!editing && (username.trim().isEmpty || password.isEmpty)) {
        return 'ab.loginNeeded';
      }
      return null;
    }
    final digits = number.replaceAll(RegExp(r'[\s-]'), '');
    if ((!editing || digits.isNotEmpty) && !luhn(digits)) {
      return 'ab.cardNumberBad';
    }
    if ((!editing || expiry.trim().isNotEmpty) &&
        !RegExp(r'^(0[1-9]|1[0-2])/\d{2}$').hasMatch(expiry.trim())) {
      return 'ab.expiryBad';
    }
    if (!editing && name.trim().isEmpty) return 'ab.nameNeeded';
    return null;
  }

  /// The request body (`credentialBody` on the web): only filled parts.
  Map<String, Object?> body() {
    final out = <String, Object?>{
      'kind': kind,
      'handle': handle.trim().toLowerCase()
    };
    void add(String key, String value, {bool keepSpaces = false}) {
      if (value.trim().isNotEmpty) out[key] = keepSpaces ? value : value.trim();
    }

    add('site', site);
    add('username', username);
    add('password', password, keepSpaces: true);
    add('name', name);
    add('number', number);
    add('expiry', expiry);
    return out;
  }

  /// Forgets every secret typed into this draft.
  void forgetSecrets() {
    password = '';
    number = '';
  }
}

/// The Luhn check of a card number of 12 to 19 digits.
bool luhn(String digits) {
  if (!RegExp(r'^\d{12,19}$').hasMatch(digits)) return false;
  var sum = 0;
  for (var i = 0; i < digits.length; i++) {
    var n = int.parse(digits[digits.length - 1 - i]);
    if (i.isOdd) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    sum += n;
  }
  return sum % 10 == 0;
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

  /// The live picture (JPEG bytes), or null when the session has none now.
  Future<Uint8List?> frame(String sessionId);

  /// The owner's own click, text, key or scroll, only while it needs them.
  Future<void> input(String sessionId, Map<String, Object> event);

  Future<List<BrowserCredential>> credentials();

  /// Creates ([id] null) or changes a saved login or card.
  Future<BrowserCredential> saveCredential(
      String? id, Map<String, Object?> body);
  Future<void> deleteCredential(String id);
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

  String _session(String id) => '$_base/sessions/${Uri.encodeComponent(id)}';

  @override
  Future<Uint8List?> frame(String sessionId) async {
    try {
      return await api.bytes('${_session(sessionId)}/frame');
    } on ApiException catch (e) {
      if (e.statusCode == 409) return null; // no browser open right now
      rethrow;
    }
  }

  @override
  Future<void> input(String sessionId, Map<String, Object> event) async {
    await api.request('POST', '${_session(sessionId)}/input', body: event);
  }

  @override
  Future<List<BrowserCredential>> credentials() async {
    // A JSON list arrives as {"data": [...]} (HttpApiTransport).
    return JsonReader(await api.request('GET', '$_base/credentials'))
        .optObjects('data', BrowserCredential.fromJson);
  }

  @override
  Future<BrowserCredential> saveCredential(
          String? id, Map<String, Object?> body) async =>
      BrowserCredential.fromJson(JsonReader(await api.request(
          id == null ? 'POST' : 'PUT',
          id == null
              ? '$_base/credentials'
              : '$_base/credentials/${Uri.encodeComponent(id)}',
          body: body)));

  @override
  Future<void> deleteCredential(String id) async {
    await api.request(
        'DELETE', '$_base/credentials/${Uri.encodeComponent(id)}');
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

  // -- the live picture and the owner's input (owners only) -----------------

  /// The last picture of the page, held in memory only (never written).
  Uint8List? picture;
  bool pictureFailed = false;
  bool _framing = false;

  /// The page's answer to the last input: a catalog key and its message.
  String? inputError;

  /// Reads the live picture once; the screen calls it on a timer while the
  /// run has a browser open and the screen is shown.
  Future<void> loadFrame() async {
    final current = run;
    if (_framing || current == null || !current.live || !current.canWatch) {
      if (current != null && !current.live && picture != null) {
        picture = null;
        notifyListeners();
      }
      return;
    }
    _framing = true;
    try {
      final next = await repository.frame(current.id);
      picture = next;
      pictureFailed = false;
    } on Object {
      pictureFailed = true;
    }
    _framing = false;
    notifyListeners();
  }

  /// Sends a click, text, key or scroll to the page while the run needs the
  /// owner ("Needs you"); the server refuses it at any other time.
  Future<void> send(Map<String, Object> event) async {
    final current = run;
    if (current == null || !current.needsYou) return;
    inputError = null;
    notifyListeners();
    try {
      await repository.input(current.id, event);
    } on Object catch (e) {
      inputError = _message(e);
    }
    notifyListeners();
    await loadFrame();
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

enum CredentialsPhase { loading, ready, denied, error }

/// The workspace's saved logins and cards (owners only). The list carries
/// handles, sites and masked hints; a secret goes from a form to the API
/// once and is never kept here.
class CredentialsController extends ChangeNotifier {
  CredentialsController(this.repository);
  final ActionBrowserRepository repository;

  CredentialsPhase phase = CredentialsPhase.loading;
  String? error;
  List<BrowserCredential> items = const [];
  String? busy;

  /// A catalog key with the handle, for the saved or deleted message.
  String? noteKey;
  String? noteHandle;
  String? noteRaw;

  Future<void> load() async {
    phase = items.isEmpty ? CredentialsPhase.loading : phase;
    error = null;
    notifyListeners();
    try {
      items = await repository.credentials();
      phase = CredentialsPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied
          ? CredentialsPhase.denied
          : CredentialsPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = CredentialsPhase.error;
      error = _message(e);
    }
    notifyListeners();
  }

  /// Sends [draft]; null when saved, else a catalog key or the API's
  /// sentence for the form. The draft's secrets are forgotten either way.
  Future<String?> save(CredentialDraft draft,
      {BrowserCredential? existing}) async {
    final problem = draft.problem(editing: existing != null);
    if (problem != null) return problem;
    busy = 'save';
    notifyListeners();
    try {
      final saved = await repository.saveCredential(existing?.id, draft.body());
      noteKey = 'ab.saved';
      noteHandle = saved.handle;
      noteRaw = null;
      return null;
    } on Object catch (e) {
      return _message(e);
    } finally {
      draft.forgetSecrets();
      busy = null;
      notifyListeners();
      await load();
    }
  }

  Future<void> delete(BrowserCredential item) async {
    if (busy != null) return;
    busy = item.id;
    noteRaw = null;
    notifyListeners();
    try {
      await repository.deleteCredential(item.id);
      noteKey = 'ab.removed';
      noteHandle = item.handle;
    } on Object catch (e) {
      noteKey = null;
      noteRaw = _message(e);
    }
    busy = null;
    notifyListeners();
    await load();
  }
}
