// Automations (docs/automation.md): what the API returns, the step and
// schedule drafts the forms edit, and the plain-words labels both screens use.
// Pure Dart, so the rules are unit tested without widgets.

import '../../data/json_read.dart';

class AutomationStepDef {
  const AutomationStepDef(
      {required this.id,
      required this.name,
      required this.action,
      this.input = const {},
      this.maxAttempts = 3});
  final String id, name, action;
  final Map<String, Object?> input;
  final int maxAttempts;

  Map<String, Object?> toJson() => {
        'id': id,
        'name': name,
        'action': action,
        'input': input,
        'max_attempts': maxAttempts,
      };
}

class AutomationWorkflow {
  const AutomationWorkflow({
    required this.id,
    required this.name,
    required this.description,
    required this.status,
    required this.version,
    required this.overlapPolicy,
    required this.steps,
    required this.updatedAt,
    this.createdBy,
    this.updatedBy,
    this.lastRunAt,
    this.lastRunStatus,
    this.nextRunAt,
    this.activeRuns = 0,
    this.runCount = 0,
    this.scheduleCount = 0,
  });
  final String id, name, description, status, overlapPolicy;
  final int version, activeRuns, runCount, scheduleCount;
  final List<AutomationStepDef> steps;
  final DateTime updatedAt;
  final String? createdBy, updatedBy, lastRunStatus;
  final DateTime? lastRunAt, nextRunAt;

  bool get paused => status == 'disabled';

  factory AutomationWorkflow.fromJson(JsonReader j) => AutomationWorkflow(
        id: j.string('id'),
        name: j.string('name'),
        description: j.optString('description') ?? '',
        status: j.optString('status') ?? 'active',
        version: j.optInt('version') ?? 1,
        overlapPolicy: j.optString('overlap_policy') ?? 'allow',
        steps: j.optObjects(
            'steps',
            (s) => AutomationStepDef(
                id: s.string('id'),
                name: s.optString('name') ?? s.string('id'),
                action: s.string('action'),
                input: s.optMap('input') ?? const {},
                maxAttempts: s.optInt('max_attempts') ?? 3)),
        updatedAt: j.optDate('updated_at') ?? DateTime.now(),
        createdBy: j.optString('created_by'),
        updatedBy: j.optString('updated_by'),
        lastRunAt: j.optDate('last_run_at'),
        lastRunStatus: j.optString('last_run_status'),
        nextRunAt: j.optDate('next_run_at'),
        activeRuns: j.optInt('active_runs') ?? 0,
        runCount: j.optInt('run_count') ?? 0,
        scheduleCount: j.optInt('schedule_count') ?? 0,
      );
}

class AutomationScheduleItem {
  const AutomationScheduleItem({
    required this.id,
    required this.workflowId,
    required this.name,
    required this.cron,
    required this.description,
    required this.timezone,
    required this.enabled,
    this.nextRunAt,
    this.lastRunAt,
    this.lastOutcome,
    this.createdBy,
  });
  final String id, workflowId, name, cron, description, timezone;
  final bool enabled;
  final DateTime? nextRunAt, lastRunAt;
  final String? lastOutcome, createdBy;

  factory AutomationScheduleItem.fromJson(JsonReader j) =>
      AutomationScheduleItem(
        id: j.string('id'),
        workflowId: j.string('workflow_id'),
        name: j.string('name'),
        cron: j.string('cron'),
        description: j.optString('cron_description') ?? j.string('cron'),
        timezone: j.optString('timezone') ?? 'UTC',
        enabled: j.optBool('enabled') ?? true,
        nextRunAt: j.optDate('next_run_at'),
        lastRunAt: j.optDate('last_run_at'),
        lastOutcome: j.optString('last_outcome'),
        createdBy: j.optString('created_by'),
      );
}

class AutomationRunStep {
  const AutomationRunStep(
      {required this.id,
      required this.name,
      required this.action,
      required this.status,
      this.attempt = 0,
      this.output = const {},
      this.error});
  final String id, name, action, status;
  final int attempt;
  final Map<String, Object?> output;
  final String? error;

  /// The task a "Create agent task" step created, for a link to it.
  String? get taskId {
    final id = output['task_id'];
    return action == 'task.create' && id is String ? id : null;
  }
}

class AutomationRunItem {
  const AutomationRunItem({
    required this.id,
    required this.workflowId,
    required this.status,
    required this.trigger,
    required this.steps,
    required this.createdAt,
    required this.updatedAt,
    this.workflowName,
    this.workflowVersion,
    this.createdBy,
    this.error,
    this.retryOf,
  });
  final String id, workflowId, status, trigger;
  final List<AutomationRunStep> steps;
  final DateTime createdAt, updatedAt;
  final String? workflowName, createdBy, error, retryOf;
  final int? workflowVersion;

  bool get active =>
      status == 'queued' || status == 'running' || status == 'paused';
  int get completedSteps => steps.where((s) => s.status == 'completed').length;

  factory AutomationRunItem.fromJson(JsonReader j) => AutomationRunItem(
        id: j.string('id'),
        workflowId: j.string('workflow_id'),
        status: j.string('status'),
        trigger: j.optString('trigger') ?? 'manual',
        steps: j.optObjects(
            'steps',
            (s) => AutomationRunStep(
                id: s.string('id'),
                name: s.optString('name') ?? s.string('id'),
                action: s.optString('action') ?? '',
                status: s.optString('status') ?? 'queued',
                attempt: s.optInt('attempt') ?? 0,
                output: s.optMap('output') ?? const {},
                error: s.optString('error'))),
        createdAt: j.optDate('created_at') ?? DateTime.now(),
        updatedAt: j.optDate('updated_at') ?? DateTime.now(),
        workflowName: j.optString('workflow_name'),
        workflowVersion: j.optInt('workflow_version'),
        createdBy: j.optString('created_by'),
        error: j.optString('error'),
        retryOf: j.optString('retry_of'),
      );
}

class SchedulePreview {
  const SchedulePreview(
      {required this.description,
      required this.timezone,
      required this.nextRuns});
  final String description, timezone;
  final List<DateTime> nextRuns;
}

/// One page of a list, fetched with one extra item to know whether more exist.
class AutomationPage<T> {
  const AutomationPage(this.items, {required this.hasMore});
  final List<T> items;
  final bool hasMore;
}

// ------------------------------------------------------------------- Drafts

/// The actions the engine executes (anum_api/automation.py STEP_ACTIONS).
const stepActions = <String, String>{
  'task.create': 'Create agent task',
  'notify': 'Notify me',
  'pause': 'Wait for approval',
  'fail': 'Stop as failed',
};
const stepActionHelp = <String, String>{
  'task.create':
      'Creates a task in this workspace with this title and prompt, ready to run under Tasks.',
  'notify': 'Notifies whoever started the run.',
  'pause': 'Pauses the run until someone resumes it.',
  'fail': 'Ends the run as failed with this message.',
};
const overlapPolicies = <String, String>{
  'allow': 'Allow overlapping runs',
  'skip': 'One run at a time',
  'cancel_previous': 'Replace the active run',
};
const overlapHelp = <String, String>{
  'allow': 'Every start runs, even while earlier runs are still active.',
  'skip':
      'While a run is active, Run now is refused and scheduled times are skipped.',
  'cancel_previous': 'A new run cancels any active run first.',
};

String actionLabel(String action) => stepActions[action] ?? action;

class StepDraft {
  StepDraft(
      {required this.id,
      required this.name,
      this.action = 'task.create',
      this.title = '',
      this.prompt = '',
      this.message = '',
      this.maxAttempts = 3,
      Map<String, Object?>? extra})
      : extra = extra ?? {};
  String id, name, action, title, prompt, message;
  int maxAttempts;
  final Map<String, Object?> extra;

  factory StepDraft.fromStep(AutomationStepDef step) {
    final extra = Map<String, Object?>.of(step.input)
      ..remove('title')
      ..remove('prompt')
      ..remove('message');
    String text(String key) =>
        step.input[key] is String ? step.input[key] as String : '';
    return StepDraft(
        id: step.id,
        name: step.name,
        action: step.action,
        title: text('title'),
        prompt: text('prompt'),
        message: text('message'),
        maxAttempts: step.maxAttempts,
        extra: extra);
  }

  Map<String, Object?> input() => switch (action) {
        'task.create' => {
            ...extra,
            'title': title.trim(),
            'prompt': prompt.trim()
          },
        'notify' || 'fail' => message.trim().isEmpty
            ? {...extra}
            : {...extra, 'message': message.trim()},
        _ => {...extra},
      };
}

StepDraft newStepDraft(List<StepDraft> existing,
    [String action = 'task.create']) {
  final ids = existing.map((s) => s.id).toSet();
  var index = existing.length + 1;
  while (ids.contains('step-$index')) {
    index++;
  }
  return StepDraft(id: 'step-$index', name: 'Step $index', action: action);
}

class WorkflowDraftCheck {
  const WorkflowDraftCheck(this.errors, this.body);
  final Map<String, String> errors;
  final Map<String, Object?>? body;
  bool get ok => body != null;
}

/// The API's rules, checked before sending. Error keys: `name`, `description`,
/// `steps`, or `<step index>.<field>`.
WorkflowDraftCheck checkWorkflowDraft(
    {required String name,
    required String description,
    required String overlapPolicy,
    required List<StepDraft> steps}) {
  final errors = <String, String>{};
  if (name.trim().isEmpty) errors['name'] = 'Give the automation a name.';
  if (name.trim().length > 160) errors['name'] = 'Use at most 160 characters.';
  if (description.trim().length > 1000) {
    errors['description'] = 'Use at most 1000 characters.';
  }
  if (steps.isEmpty || steps.length > 100) {
    errors['steps'] = 'An automation has between 1 and 100 steps.';
  }
  final seen = <String>{};
  for (var i = 0; i < steps.length; i++) {
    final s = steps[i], at = 'Step ${i + 1}';
    if (s.id.trim().isEmpty) {
      errors['$i.id'] = '$at: give the step an id.';
    } else if (!seen.add(s.id.trim())) {
      errors['$i.id'] = '$at: the id "${s.id.trim()}" is already used.';
    }
    if (s.name.trim().isEmpty) errors['$i.name'] = '$at: give the step a name.';
    if (!stepActions.containsKey(s.action)) {
      errors['$i.action'] =
          '$at: choose what the step does; "${s.action}" is no longer supported.';
    }
    if (s.action == 'task.create') {
      if (s.title.trim().isEmpty) {
        errors['$i.title'] = '$at: the task needs a title.';
      } else if (s.title.trim().length > 160) {
        errors['$i.title'] = '$at: use at most 160 characters for the title.';
      }
      if (s.prompt.trim().isEmpty) {
        errors['$i.prompt'] = '$at: tell the agent what to do in the prompt.';
      } else if (s.prompt.trim().length > 8000) {
        errors['$i.prompt'] = '$at: use at most 8000 characters.';
      }
    }
    if ((s.action == 'notify' || s.action == 'fail') &&
        s.message.trim().length > 500) {
      errors['$i.message'] = '$at: use at most 500 characters.';
    }
  }
  if (errors.isNotEmpty) return WorkflowDraftCheck(errors, null);
  return WorkflowDraftCheck(const {}, {
    'name': name.trim(),
    'description': description.trim(),
    'overlap_policy': overlapPolicy,
    'steps': [
      for (final s in steps)
        AutomationStepDef(
                id: s.id.trim(),
                name: s.name.trim(),
                action: s.action,
                input: s.input(),
                maxAttempts: s.maxAttempts.clamp(1, 10))
            .toJson(),
    ],
  });
}

// ---------------------------------------------------------------- Schedules

const schedulePresets = <String, String>{
  '*/15 * * * *': 'Every 15 minutes',
  '0 * * * *': 'Every hour',
  '0 9 * * *': 'Daily at 09:00',
  '0 9 * * 1-5': 'Weekdays at 09:00',
  '0 9 * * 1': 'Mondays at 09:00',
  '0 9 1 * *': 'First of the month at 09:00',
};

/// A quick local check of the five-field shape; the API preview has the
/// final word (field ranges, time zone, whether it ever fires).
String? cronShapeError(String cron) {
  final fields =
      cron.trim().split(RegExp(r'\s+')).where((f) => f.isNotEmpty).length;
  if (fields == 5) return null;
  return 'A cron expression has five fields (minute hour day-of-month month '
      'day-of-week); this one has $fields. Example: 0 9 * * 1-5.';
}

String? outcomeLabel(String? outcome) => switch (outcome) {
      null => null,
      'fired' => 'Ran',
      'skipped_overlap' => 'Skipped: a run was still active',
      'workflow_paused' => 'Skipped: automation paused',
      'owner_inactive' => 'Failed: schedule owner no longer a member',
      'workspace_archived' => 'Skipped: workspace archived',
      _ => outcome.replaceAll('_', ' '),
    };

// ------------------------------------------------------------ Status words

String statusLabel(String status) => switch (status) {
      'disabled' => 'Paused',
      'active' => 'Active',
      'paused' => 'Waiting',
      _ => status.isEmpty
          ? status
          : '${status[0].toUpperCase()}${status.substring(1).replaceAll('_', ' ')}',
    };

String triggerLabel(String trigger) => switch (trigger) {
      'schedule' => 'Scheduled',
      'retry' => 'Retry',
      _ => 'Started manually',
    };

/// Relative time in both directions ("in 20 min", "3 h ago").
String relativeWhen(DateTime value, {DateTime? now}) {
  final diff = value.difference(now ?? DateTime.now());
  final size = diff.abs();
  final String phrase;
  if (size.inMinutes < 1) return diff.isNegative ? 'just now' : 'now';
  if (size.inMinutes < 60) {
    phrase = '${size.inMinutes} min';
  } else if (size.inHours < 48) {
    phrase = '${size.inHours} h';
  } else {
    phrase = '${size.inDays} days';
  }
  return diff.isNegative ? '$phrase ago' : 'in $phrase';
}

// ------------------------------------------------------------- Permissions

/// Owners and members manage automations; viewers only read. A null role
/// (not loaded) lets the API decide.
bool canManageAutomations(String? role) =>
    role == null || role == 'owner' || role == 'member';

/// Members change their own automations and schedules; owners change any.
bool canEditAutomation(String? role, String? userId, String? createdBy) =>
    canManageAutomations(role) &&
    (role == 'owner' ||
        role == null ||
        createdBy == null ||
        createdBy == userId);

const manageHint =
    'You can view automations. Creating, running or changing them needs the '
    'owner or member role; ask a workspace owner.';
