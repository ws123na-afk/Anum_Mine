// Developer mode (docs/developer-mode.md): the request, setup and timeline
// models, plus the pure helpers the Developer screen, the Assistant chat and
// the voice view share. Mirrors apps/web/src/lib/developer.ts.
import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/sse.dart';
import '../../src/localization/anum_localizations.dart';
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
    this.projectId,
    this.projectName,
    this.repository,
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
      projectId: j.optString('project_id'),
      projectName: j.optString('project_name'),
      repository: j.optString('repository'),
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

  /// The project it went to (null for requests from before projects), with
  /// its name and repository when it was sent.
  final String? projectId, projectName, repository;
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
  String get statusLabel => statusLabelIn(AnumLocalizations.english);

  /// The status in [l]'s language; an unknown status shows its raw name.
  String statusLabelIn(AnumLocalizations l) =>
      status == DevStatus.unknown && statusName.isNotEmpty
          ? statusName
          : status == DevStatus.unknown
              ? status.label
              : l.t('dev.status.${status.apiValue}');
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
    this.projects = 0,
    this.readyProjects = 0,
    this.maxProjects = 100,
    this.environmentTokenConfigured = false,
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
        projects: j.optInt('projects') ?? 0,
        readyProjects: j.optInt('ready_projects') ?? 0,
        maxProjects: j.optInt('max_projects') ?? 100,
        environmentTokenConfigured:
            j.optBool('environment_token_configured') ?? false,
      );
  final bool configured, enabledHere;
  final String? repository;
  final String workflow, baseBranch, message;
  final List<String> missing;
  final int maxRequestsPerDay, requestsToday;
  final int projects, readyProjects, maxProjects;

  /// Whether ANUM_DEVELOPER_GITHUB_TOKEN is set on the API (never its value).
  final bool environmentTokenConfigured;

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
        projects: projects,
        readyProjects: readyProjects,
        maxProjects: maxProjects,
        environmentTokenConfigured: environmentTokenConfigured,
      );
}

/// What Test connection found, kept on the project until the next test.
class ProjectCheck {
  const ProjectCheck({
    required this.ok,
    required this.checkedAt,
    required this.message,
    this.repositoryReachable = false,
    this.defaultBranchExists = false,
    this.workflowPresent = false,
    this.actionsReadable = false,
  });
  factory ProjectCheck.fromJson(JsonReader j) => ProjectCheck(
        ok: j.optBool('ok') ?? false,
        checkedAt: (j.optDate('checked_at') ?? DateTime.now()).toLocal(),
        message: j.optString('message') ?? '',
        repositoryReachable: j.optBool('repository_reachable') ?? false,
        defaultBranchExists: j.optBool('default_branch_exists') ?? false,
        workflowPresent: j.optBool('workflow_present') ?? false,
        actionsReadable: j.optBool('actions_readable') ?? false,
      );
  final bool ok,
      repositoryReachable,
      defaultBranchExists,
      workflowPresent,
      actionsReadable;
  final DateTime checkedAt;
  final String message;
}

/// A project: one GitHub repository the developer may change
/// (`GET /api/v1/developer/projects`). Never a token, only whether one is set.
class DevProject {
  const DevProject({
    required this.id,
    required this.name,
    required this.githubOwner,
    required this.githubRepo,
    this.defaultBranch = 'main',
    this.description = '',
    this.enabled = true,
    this.protectedPaths = const [],
    this.workflowFile = 'anum-developer.yml',
    this.tokenSource = 'project',
    this.tokenConfigured = false,
    this.lastCheck,
    this.installPrNumber,
    this.installPrUrl,
    this.ready = false,
    this.problems = const [],
  });
  factory DevProject.fromJson(JsonReader j) {
    final check = j.optObject('last_check');
    return DevProject(
      id: j.string('id'),
      name: j.string('name'),
      githubOwner: j.optString('github_owner') ?? '',
      githubRepo: j.optString('github_repo') ?? '',
      defaultBranch: j.optString('default_branch') ?? 'main',
      description: j.optString('description') ?? '',
      enabled: j.optBool('enabled') ?? true,
      protectedPaths: j.optStrings('protected_paths') ?? const [],
      workflowFile: j.optString('workflow_file') ?? 'anum-developer.yml',
      tokenSource: j.optString('token_source') ?? 'project',
      tokenConfigured: j.optBool('token_configured') ?? false,
      lastCheck: check == null ? null : ProjectCheck.fromJson(check),
      installPrNumber: j.optInt('install_pr_number'),
      installPrUrl: j.optString('install_pr_url'),
      ready: j.optBool('ready') ?? false,
      problems: j.optStrings('problems') ?? const [],
    );
  }
  final String id, name, githubOwner, githubRepo, defaultBranch, description;
  final String workflowFile, tokenSource;
  final bool enabled, tokenConfigured, ready;
  final List<String> protectedPaths, problems;
  final ProjectCheck? lastCheck;
  final int? installPrNumber;
  final String? installPrUrl;

  String get repository => '$githubOwner/$githubRepo';
  ProjectRef get ref => ProjectRef(
      id: id, name: name, repository: repository, description: description);
}

/// A project as `POST /api/v1/developer/resolve` names it.
class ProjectRef {
  const ProjectRef(
      {required this.id,
      required this.name,
      required this.repository,
      this.description = ''});
  factory ProjectRef.fromJson(JsonReader j) => ProjectRef(
        id: j.string('id'),
        name: j.string('name'),
        repository: j.optString('repository') ?? '',
        description: j.optString('description') ?? '',
      );
  final String id, name, repository, description;
}

enum ResolutionStatus { resolved, ambiguous, none }

/// Which project a chat or voice request is for. `resolved` is still
/// confirmed before anything starts; `ambiguous` asks which of [candidates].
class Resolution {
  const Resolution(
      {required this.status,
      required this.text,
      this.project,
      this.candidates = const []});
  factory Resolution.fromJson(JsonReader j) {
    final project = j.optObject('project');
    return Resolution(
      status: ResolutionStatus.values.firstWhere(
          (s) => s.name == j.optString('status'),
          orElse: () => ResolutionStatus.ambiguous),
      text: j.optString('text') ?? '',
      project: project == null ? null : ProjectRef.fromJson(project),
      candidates: j.optObjects('candidates', ProjectRef.fromJson),
    );
  }
  final ResolutionStatus status;
  final String text;
  final ProjectRef? project;
  final List<ProjectRef> candidates;
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

// "ANUM, in TyrePulse add a login page": the project named first, no "build".
final _inProject = RegExp(
    r'^\s*(?:(?:hey|ok|okay)[\s,]+)?anum[\s,:]+((?:in|for|on)\s+\S.*)$',
    caseSensitive: false,
    dotAll: true);

/// The request text when [text] starts with a developer trigger
/// ("developer, ...", "build ...", "Anum build ...", "ANUM, in PROJECT
/// ..."), otherwise null. "build" alone (nothing to build) is not a request.
/// The project phrase stays in the text: the API resolves and strips it.
String? parseDeveloperCommand(String text) {
  final match = _trigger.firstMatch(text) ?? _inProject.firstMatch(text);
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

/// The confirmation always names the project.
String confirmPrompt(String request, String project) =>
    "I'll ask the developer to: $request in $project. Start?";

/// Names joined as alternatives: "a", "a or b", "a, b or c".
String orList(List<String> names) => switch (names.length) {
      0 => '',
      1 => names.single,
      _ => '${names.sublist(0, names.length - 1).join(', ')} or ${names.last}',
    };

String whichProjectPrompt(List<ProjectRef> candidates) =>
    'Which project is this for: '
    '${orList([for (final c in candidates) c.name])}?';

String whichProjectAgain(List<ProjectRef> candidates) =>
    "I didn't catch the project. Say "
    '${orList([for (final c in candidates) c.name])}, or cancel.';

const noProjectsReply = 'There is no project ready for requests. Add one '
    'under Developer › Projects, then ask again.';

String _normalize(String value) => value
    .toLowerCase()
    .replaceAll(RegExp(r'[^\p{L}\p{N}]+', unicode: true), ' ')
    .trim();

/// The project a spoken or typed answer names among [candidates]
/// ("TyrePulse", "the tyre pulse one", "acme/tyrepulse"), or null when it
/// names none or more than one.
ProjectRef? matchProjectAnswer(String answer, List<ProjectRef> candidates) {
  final said = _normalize(answer);
  if (said.isEmpty) return null;
  final squashed = said.replaceAll(' ', '');
  final hits = candidates.where((candidate) {
    final names = [
      candidate.name,
      candidate.repository.split('/').last,
      candidate.repository
    ].map(_normalize).where((name) => name.isNotEmpty);
    return names.any((name) =>
        said == name ||
        ' $said '.contains(' $name ') ||
        squashed.contains(name.replaceAll(' ', '')));
  }).toList();
  return hits.length == 1 ? hits.single : null;
}

const ownerOnlyReply =
    'Only a workspace owner can ask the developer to change the apps. Ask an '
    'owner of this workspace.';

const ownerOnlyExplanation =
    "An owner can ask ANUM's developer to change this workspace's apps: it "
    'writes the change on a branch, runs CI and opens a pull request, and '
    'nothing merges until an owner approves it. Ask an owner of this '
    'workspace if you want something built.';

/// The sentence spoken and shown when a pull request is ready.
String readyAnnouncement(DevRequest request) => request.projectName == null
    ? 'The change is ready for review: ${request.title}. Say approve to merge.'
    : 'The change is ready for review in ${request.projectName}: '
        '${request.title}. Say approve to merge.';

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
String checksLabel(DevChecks? checks,
    [AnumLocalizations l = AnumLocalizations.english]) {
  if (checks == null || checks.total == 0) return l.t('dev.checks.none');
  final parts = [
    l.f('dev.checks.passed', {'passed': checks.passed, 'total': checks.total})
  ];
  if (checks.pending > 0) {
    parts.add(l.f('dev.checks.running', {'count': checks.pending}));
  }
  if (checks.failed > 0) {
    final names = checks.failing.take(3).join(', ');
    parts.add('${l.f('dev.checks.failing', {
          'count': checks.failed
        })}${names.isEmpty ? '' : ' ($names)'}');
  }
  return parts.join(l.isArabic ? '، ' : ', ');
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
  'ANUM_DEVELOPER_GITHUB_REPO':
      'the repository used before projects, as owner/repo (or remove the '
          'setting).',
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

/// Optional repository variables the workflow reads (names only).
const githubVariableNames = [
  'ANUM_DEV_PROTECTED_PATHS',
  'ANUM_DEV_ALLOW_PROTECTED_PATHS'
];

/// Project ids with a queued or coding request: one at a time per project.
Set<String> busyProjects(List<DevRequest> requests) => {
      for (final r in requests)
        if (r.status == DevStatus.queued || r.status == DevStatus.coding)
          r.projectId ?? ''
    };

/// Projects requests can go to (enabled with a token; the API re-checks).
List<DevProject> sendableProjects(List<DevProject> projects) => [
      for (final p in projects)
        if (p.enabled && p.tokenConfigured) p
    ];

/// The project the request form starts with: the only sendable one, else
/// the one used last.
String? defaultProjectId(List<DevProject> projects, String? lastUsed) {
  final sendable = sendableProjects(projects);
  if (sendable.length == 1) return sendable.single.id;
  return sendable.any((p) => p.id == lastUsed) ? lastUsed : null;
}

enum ProjectState { ready, untested, attention, off }

ProjectState projectState(DevProject project) {
  if (!project.enabled) return ProjectState.off;
  if (!project.ready) return ProjectState.attention;
  return project.lastCheck == null ? ProjectState.untested : ProjectState.ready;
}

String projectStateLabel(ProjectState state,
        [AnumLocalizations l = AnumLocalizations.english]) =>
    l.t(switch (state) {
      ProjectState.ready => 'dev.project.ready',
      ProjectState.untested => 'dev.project.untested',
      ProjectState.attention => 'dev.project.attention',
      ProjectState.off => 'dev.project.off',
    });

/// Install workflow is offered while the workflow is not known to be there.
bool canInstallWorkflow(DevProject project) =>
    project.tokenConfigured && !(project.lastCheck?.workflowPresent ?? false);

final _owner = RegExp(r'^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$');
final _repo = RegExp(r'^[A-Za-z0-9_.-]{1,100}$');
final _branch = RegExp(r'^[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}$');
final _protectedPath = RegExp(r'^[A-Za-z0-9._-][A-Za-z0-9._/-]{0,199}$');
final _workflow = RegExp(r'^[A-Za-z0-9_.-]{1,100}\.ya?ml$');

/// "acme/tyrepulse", "https://github.com/acme/tyrepulse(.git)" or
/// "git@github.com:acme/tyrepulse.git"; null when it is none of those.
({String owner, String repo})? parseRepository(String value) {
  var text = value.trim();
  text =
      text.replaceFirst(RegExp(r'^git@github\.com:', caseSensitive: false), '');
  text = text.replaceFirst(
      RegExp(r'^https?://(?:www\.)?github\.com/', caseSensitive: false), '');
  text = text.replaceFirst(RegExp(r'\.git$', caseSensitive: false), '');
  text = text.replaceFirst(RegExp(r'/+$'), '');
  final parts = text.split('/');
  if (parts.length != 2) return null;
  final (owner, repo) = (parts[0], parts[1]);
  if (!_owner.hasMatch(owner) || !_repo.hasMatch(repo)) return null;
  if (repo == '.' || repo == '..') return null;
  return (owner: owner, repo: repo);
}

/// One path per line (or comma-separated), trimmed, "./" removed, no
/// duplicates.
List<String> parseProtectedPaths(String value) {
  final paths = <String>[];
  for (final raw in value.split(RegExp(r'[\n,]'))) {
    var path = raw.trim();
    if (path.startsWith('./')) path = path.substring(2);
    if (path.isNotEmpty && !paths.contains(path)) paths.add(path);
  }
  return paths;
}

/// The project form's fields as typed.
class ProjectDraft {
  const ProjectDraft({
    this.name = '',
    this.repository = '',
    this.defaultBranch = 'main',
    this.description = '',
    this.protectedPaths = '',
    this.enabled = true,
    this.tokenSource = 'project',
    this.token = '',
    this.clearToken = false,
    this.workflowFile = 'anum-developer.yml',
  });
  factory ProjectDraft.from(DevProject project) => ProjectDraft(
        name: project.name,
        repository: project.repository,
        defaultBranch: project.defaultBranch,
        description: project.description,
        protectedPaths: project.protectedPaths.join('\n'),
        enabled: project.enabled,
        tokenSource: project.tokenSource,
        workflowFile: project.workflowFile,
      );
  final String name, repository, defaultBranch, description, protectedPaths;
  final String tokenSource, token, workflowFile;
  final bool enabled, clearToken;
}

/// The POST/PATCH body for a draft, or the field and what to fix.
sealed class ProjectPayload {
  const ProjectPayload();
}

class ValidProject extends ProjectPayload {
  const ValidProject(this.body);
  final JsonMap body;
}

class InvalidProject extends ProjectPayload {
  const InvalidProject(this.field, this.error);
  final String field, error;
}

ProjectPayload projectPayload(ProjectDraft draft) {
  final name = draft.name.trim().replaceAll(RegExp(r'\s+'), ' ');
  if (name.isEmpty) {
    return const InvalidProject('name', 'Give the project a name.');
  }
  if (name.length > 60) {
    return const InvalidProject(
        'name', 'Keep the project name under 60 characters.');
  }
  final repository = parseRepository(draft.repository);
  if (repository == null) {
    return const InvalidProject('repository',
        'Enter the repository as owner/repo, for example acme/tyrepulse.');
  }
  final branch = draft.defaultBranch.trim();
  if (!_branch.hasMatch(branch) ||
      branch.contains('..') ||
      branch.endsWith('/')) {
    return const InvalidProject('branch',
        'The branch name is not valid (for example main or release/2026).');
  }
  final paths = parseProtectedPaths(draft.protectedPaths);
  for (final path in paths) {
    if (!_protectedPath.hasMatch(path) ||
        path.split('/').contains('..') ||
        path.contains('//')) {
      return InvalidProject(
          'protected',
          'Protected path "$path" is not valid: use a repository-relative '
              'file or directory, without wildcards or "..".');
    }
  }
  final workflow = draft.workflowFile.trim().isEmpty
      ? 'anum-developer.yml'
      : draft.workflowFile.trim();
  if (!_workflow.hasMatch(workflow)) {
    return const InvalidProject(
        'workflow',
        'The workflow file is a file name ending in .yml, for example '
            'anum-developer.yml.');
  }
  final token = draft.token.trim();
  if (token.isNotEmpty &&
      (token.length < 20 || token.contains(RegExp(r'\s')))) {
    return const InvalidProject(
        'token', 'Paste the whole fine-grained token (it has no spaces).');
  }
  return ValidProject({
    'name': name,
    'github_owner': repository.owner,
    'github_repo': repository.repo,
    'default_branch': branch,
    'description': draft.description.trim().replaceAll(RegExp(r'\s+'), ' '),
    'protected_paths': paths,
    'enabled': draft.enabled,
    'token_source': draft.tokenSource,
    'workflow_file': workflow,
    if (draft.tokenSource == 'project' && token.isNotEmpty) 'token': token,
    if (draft.clearToken && token.isEmpty) 'clear_token': true,
  });
}

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
