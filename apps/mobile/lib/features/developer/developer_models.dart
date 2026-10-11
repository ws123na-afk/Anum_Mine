// Developer mode (docs/developer-mode.md): the request, setup and timeline
// models, plus the pure helpers the Developer screen, the Assistant chat and
// the voice view share. Mirrors apps/web/src/lib/developer.ts.
import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/sse.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';

enum DevStatus {
  queued('queued', 'Queued'),
  coding('coding', 'Coding'),
  ciRunning('ci_running', 'CI running'),
  ciFailed('ci_failed', 'CI failing'),
  waitingApproval('waiting_approval', 'Waiting for approval'),
  merged('merged', 'Merged'),
  failed('failed', 'Failed'),
  cancelled('cancelled', 'Cancelled'),

  /// A status this app does not know yet (a newer API); shown as its raw name.
  unknown('unknown', 'Unknown');

  const DevStatus(this.apiValue, this.label);
  final String apiValue, label;

  static DevStatus parse(String value) => DevStatus.values
      .firstWhere((s) => s.apiValue == value, orElse: () => DevStatus.unknown);
}

enum StageState { done, active, failed, pending }

/// One step of the request's progress: Queued, Coding, Pull request opened,
/// CI checks, Waiting for approval, Merged.
class DevStage {
  const DevStage({required this.id, required this.label, required this.state});
  factory DevStage.fromJson(JsonReader j) => DevStage(
        id: j.string('id'),
        label: j.string('label'),
        state: StageState.values.firstWhere((s) => s.name == j.string('state'),
            orElse: () => StageState.pending),
      );
  final String id, label;
  final StageState state;
}

class DevChecks {
  const DevChecks(
      {required this.total,
      required this.passed,
      required this.failed,
      required this.pending,
      this.failing = const []});
  factory DevChecks.fromJson(JsonReader j) => DevChecks(
        total: j.optInt('total') ?? 0,
        passed: j.optInt('passed') ?? 0,
        failed: j.optInt('failed') ?? 0,
        pending: j.optInt('pending') ?? 0,
        failing: j.optStrings('failing') ?? const [],
      );
  final int total, passed, failed, pending;
  final List<String> failing;
}

/// A `GET /api/v1/developer/requests` row. [approval] is the pending merge
/// approval while one waits (approve it with its payload hash).
class DevRequest {
  const DevRequest({
    required this.id,
    required this.text,
    required this.status,
    required this.branch,
    required this.createdAt,
    required this.updatedAt,
    required this.summary,
    this.statusName = '',
    this.createdBy = '',
    this.source = 'text',
    this.allowProtectedPaths = false,
    this.runId,
    this.runUrl,
    this.prNumber,
    this.prUrl,
    this.prTitle,
    this.prDraft,
    this.headSha,
    this.checks,
    this.failureReason,
    this.mergeError,
    this.approvalId,
    this.mergeCommitSha,
    this.mergedAt,
    this.timeline = const [],
    this.approval,
  });

  factory DevRequest.fromJson(JsonReader j) {
    final status = j.string('status');
    final approval = j.optObject('approval');
    final checks = j.optObject('checks');
    return DevRequest(
      id: j.string('id'),
      createdBy: j.optString('created_by') ?? '',
      text: j.string('text'),
      source: j.optString('source') ?? 'text',
      status: DevStatus.parse(status),
      statusName: status,
      allowProtectedPaths: j.optBool('allow_protected_paths') ?? false,
      branch: j.optString('branch') ?? '',
      runId: j.optInt('run_id'),
      runUrl: j.optString('run_url'),
      prNumber: j.optInt('pr_number'),
      prUrl: j.optString('pr_url'),
      prTitle: j.optString('pr_title'),
      prDraft: j.optBool('pr_draft'),
      headSha: j.optString('head_sha'),
      checks: checks == null ? null : DevChecks.fromJson(checks),
      failureReason: j.optString('failure_reason'),
      mergeError: j.optString('merge_error'),
      approvalId: j.optString('approval_id'),
      mergeCommitSha: j.optString('merge_commit_sha'),
      createdAt: j.date('created_at').toLocal(),
      updatedAt: (j.optDate('updated_at') ?? j.date('created_at')).toLocal(),
      mergedAt: j.optDate('merged_at')?.toLocal(),
      timeline: j.optObjects('timeline', DevStage.fromJson),
      summary: j.optString('summary') ?? '',
      approval: approval == null ? null : workspaceApprovalFromJson(approval),
    );
  }

  final String id, createdBy, text, source, branch, summary;
  final DevStatus status;

  /// The API's status string, kept for statuses this app does not know.
  final String statusName;
  final bool allowProtectedPaths;
  final int? runId, prNumber;
  final String? runUrl, prUrl, prTitle, headSha;
  final bool? prDraft;
  final DevChecks? checks;
  final String? failureReason, mergeError, approvalId, mergeCommitSha;
  final DateTime createdAt, updatedAt;
  final DateTime? mergedAt;
  final List<DevStage> timeline;
  final WorkspaceApproval? approval;

  String get title => prTitle ?? shortText(text);
  String get statusLabel => status == DevStatus.unknown && statusName.isNotEmpty
      ? statusName
      : status.label;
  bool get spoken => source == 'voice';
}

/// `GET /api/v1/developer/setup`: what is configured, by name only. The
/// token itself is never returned.
class DevSetup {
  const DevSetup({
    required this.configured,
    required this.enabledHere,
    required this.message,
    this.repository,
    this.workflow = 'anum-developer.yml',
    this.baseBranch = 'main',
    this.missing = const [],
    this.maxRequestsPerDay = 10,
    this.requestsToday = 0,
  });
  factory DevSetup.fromJson(JsonReader j) => DevSetup(
        configured: j.boolean('configured'),
        enabledHere: j.boolean('enabled_here'),
        repository: j.optString('repository'),
        workflow: j.optString('workflow') ?? 'anum-developer.yml',
        baseBranch: j.optString('base_branch') ?? 'main',
        missing: j.optStrings('missing') ?? const [],
        message: j.optString('message') ?? '',
        maxRequestsPerDay: j.optInt('max_requests_per_day') ?? 10,
        requestsToday: j.optInt('requests_today') ?? 0,
      );
  final bool configured, enabledHere;
  final String? repository;
  final String workflow, baseBranch, message;
  final List<String> missing;
  final int maxRequestsPerDay, requestsToday;

  bool get ready => configured && enabledHere;
  bool get limitReached => requestsToday >= maxRequestsPerDay;
  DevSetup withOneMore() => DevSetup(
        configured: configured,
        enabledHere: enabledHere,
        message: message,
        repository: repository,
        workflow: workflow,
        baseBranch: baseBranch,
        missing: missing,
        maxRequestsPerDay: maxRequestsPerDay,
        requestsToday: requestsToday + 1,
      );
}

const minRequestChars = 10;
const maxRequestChars = 4000;

const exampleRequests = [
  'Add a dark/light toggle to Settings',
  'Show the number of pending approvals on the Tasks page',
  'Let me rename a conversation in the Assistant',
];

/// Same rule as the API's PROTECTED_REQUEST: requests that touch CI
/// workflows or secret handling.
final _protected = RegExp(
    r'\.github|github[ -]actions?|\bci[ -]workflows?\b|workflow[ -]files?|\bsecrets?\b|credentials?|passwords?|api[ _-]?keys?|access[ _-]?tokens?|personal access token|gitleaks|claude\.md',
    caseSensitive: false);

bool mentionsProtected(String text) => _protected.hasMatch(text);

/// The request as sent ([text] trimmed), or an error that says what to fix.
sealed class RequestBody {
  const RequestBody();
}

class ValidRequest extends RequestBody {
  const ValidRequest(this.text, this.allowProtectedPaths);
  final String text;
  final bool allowProtectedPaths;
}

class InvalidRequest extends RequestBody {
  const InvalidRequest(this.error);
  final String error;
}

RequestBody requestBody(String text, {bool allowProtected = false}) {
  final value = text.replaceAll('\r\n', '\n').trim();
  if (value.length < minRequestChars) {
    return const InvalidRequest(
        'Describe the change in at least $minRequestChars characters.');
  }
  if (value.length > maxRequestChars) {
    return const InvalidRequest(
        'Keep the request under $maxRequestChars characters.');
  }
  if (mentionsProtected(value) && !allowProtected) {
    return const InvalidRequest(
        'This request mentions CI workflows or secret handling. Tick "Allow '
        'changes to workflows and secret handling" if you really mean it.');
  }
  return ValidRequest(value, allowProtected);
}

final _trigger = RegExp(
    r'^\s*(?:(?:hey|ok|okay)[\s,]+)?(?:anum[\s,:]+)?(?:developer|build)\b[\s,:.-]*(.*)$',
    caseSensitive: false,
    dotAll: true);

/// The request text when [text] starts with a developer trigger
/// ("developer, ...", "build ...", "Anum build ..."), otherwise null. "build"
/// alone (nothing to build) is not a request.
String? parseDeveloperCommand(String text) {
  final match = _trigger.firstMatch(text);
  if (match == null) return null;
  final rest = (match.group(1) ?? '')
      .replaceFirst(RegExp(r'^(?:to|please)\s+', caseSensitive: false), '')
      .trim();
  return rest.length >= minRequestChars ? rest : null;
}

enum Confirmation { yes, no }

final _yes = RegExp(
    r'^\s*(?:yes|yeah|yep|start|start it|go|go ahead|do it|confirm|ok|okay|sure|please do|نعم|ابدأ)[\s.!]*$',
    caseSensitive: false);
final _no = RegExp(
    r"^\s*(?:no|nope|cancel|stop|not now|never mind|nevermind|don't|do not|لا|إلغاء)[\s.!]*$",
    caseSensitive: false);

/// A spoken or typed answer to "Start?": yes, no, or null when it is neither.
Confirmation? parseConfirmation(String text) {
  if (_yes.hasMatch(text)) return Confirmation.yes;
  if (_no.hasMatch(text)) return Confirmation.no;
  return null;
}

/// "approve", "approve it", "approve and merge", "merge it": asks to review
/// the merge. It never approves by itself.
bool isApproveIntent(String text) => RegExp(
        r'^\s*(?:(?:anum[\s,]+)?(?:approve(?:\s+(?:it|this|the change|and merge))?|merge(?:\s+it)?))[\s.!]*$',
        caseSensitive: false)
    .hasMatch(text);

String confirmPrompt(String request) =>
    "I'll ask the developer to: $request. Start?";

const ownerOnlyReply =
    'Only a workspace owner can ask the developer to change the app. Ask an '
    'owner of this workspace.';

const ownerOnlyExplanation =
    "An owner can ask ANUM's developer to change the app itself: it writes the "
    'change on a branch, runs CI and opens a pull request, and nothing merges '
    'until an owner approves it. Ask an owner of this workspace if you want '
    'something built.';

/// The sentence spoken and shown when a pull request is ready.
String readyAnnouncement(DevRequest request) =>
    'The change is ready for review: ${request.title}. Say approve to merge.';

String shortText(String text, {int limit = 80}) {
  final words = text.split(RegExp(r'\s+')).join(' ').trim();
  return words.length <= limit
      ? words
      : '${words.substring(0, limit - 1).trimRight()}…';
}

enum DevTone { ok, warn, stop, neutral }

DevTone statusTone(DevStatus status) => switch (status) {
      DevStatus.merged => DevTone.ok,
      DevStatus.failed || DevStatus.ciFailed => DevTone.stop,
      DevStatus.cancelled || DevStatus.unknown => DevTone.neutral,
      _ => DevTone.warn,
    };

bool isActive(DevRequest request) => !{
      DevStatus.merged,
      DevStatus.failed,
      DevStatus.cancelled
    }.contains(request.status);

bool canCancel(DevRequest request) =>
    isActive(request) && request.status != DevStatus.unknown;

bool canRequestMerge(DevRequest request) =>
    request.status == DevStatus.waitingApproval && request.prNumber != null;

/// "2 of 3 checks passed, 1 failing (Web and contracts)".
String checksLabel(DevChecks? checks) {
  if (checks == null || checks.total == 0) return 'No CI results yet';
  final parts = ['${checks.passed} of ${checks.total} checks passed'];
  if (checks.pending > 0) parts.add('${checks.pending} running');
  if (checks.failed > 0) {
    final names = checks.failing.take(3).join(', ');
    parts.add('${checks.failed} failing${names.isEmpty ? '' : ' ($names)'}');
  }
  return parts.join(', ');
}

/// Requests that just became ready for review compared with the previous
/// poll: what the voice view says out loud and the chat shows. Nothing is
/// announced on the first poll.
List<DevRequest> newlyReady(
    List<DevRequest>? previous, List<DevRequest> current) {
  if (previous == null) return const [];
  final before = {for (final item in previous) item.id: item.status};
  return [
    for (final item in current)
      if (item.status == DevStatus.waitingApproval &&
          before[item.id] != DevStatus.waitingApproval)
        item
  ];
}

/// How often the views refresh: often while something is moving, never
/// when nothing is.
Duration? pollDelay(List<DevRequest> requests) {
  if (requests.any((r) => {
        DevStatus.queued,
        DevStatus.coding,
        DevStatus.ciRunning
      }.contains(r.status))) {
    return const Duration(seconds: 20);
  }
  if (requests.any((r) => r.status == DevStatus.waitingApproval)) {
    return const Duration(seconds: 60);
  }
  return null;
}

/// One missing API setting: its name and what it is for. Never a value.
class SetupStep {
  const SetupStep(this.name, this.purpose);
  final String name, purpose;
}

const _setupPurpose = {
  'ANUM_DEVELOPER_GITHUB_REPO': 'the repository to build, as owner/repo.',
  'ANUM_DEVELOPER_GITHUB_TOKEN':
      'a fine-grained GitHub token for that repository only (Contents, Pull '
          'requests and Actions read and write; Commit statuses read).',
  'ANUM_DEVELOPER_TENANT_ID': 'the organization allowed to use developer mode.',
  'ANUM_DEVELOPER_WORKFLOW_FILE':
      'the workflow file name (anum-developer.yml).',
  'ANUM_DEVELOPER_BASE_BRANCH': 'the branch to build on (main).',
  'ANUM_DEVELOPER_GITHUB_API_URL': 'an https GitHub API address.',
};

/// Setup instructions for the missing API settings, by name only.
List<SetupStep> setupSteps(DevSetup setup) => [
      for (final name in setup.missing)
        SetupStep(name, _setupPurpose[name] ?? 'see docs/developer-mode.md.')
    ];

/// The GitHub repository secrets the coding workflow needs (names only).
const githubSecretNames = ['ANTHROPIC_API_KEY', 'ANUM_DEV_PUSH_TOKEN'];

const developerOwnerOnly = 'Only workspace owners can use developer mode.';

/// What went wrong, in words that say how to fix it.
String explainDeveloperError(Object error) => switch (error) {
      ApiException(isPermissionDenied: true, :final message) =>
        message == 'Forbidden' || message == 'Request failed'
            ? developerOwnerOnly
            : message,
      ApiException(statusCode: 401) =>
        'Your sign-in expired. Sign in again to continue.',
      ApiException(isBudgetExceeded: true, :final message) =>
        'The monthly model budget is used up. $message',
      ApiException(statusCode: 0, :final message) => message,
      ApiException(:final message) => message,
      EventStreamTimeoutException(:final message) => message,
      FormatException(:final message) =>
        'The server sent an answer this app cannot read. $message',
      _ =>
        'The server could not be reached. Check the connection and try again.',
    };

/// Only https links are opened in the browser.
Uri? safeLink(String? value) {
  if (value == null) return null;
  final uri = Uri.tryParse(value);
  return uri != null && uri.scheme == 'https' && uri.host.isNotEmpty
      ? uri
      : null;
}
