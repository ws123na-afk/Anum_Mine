import '../../data/json_read.dart';

/// What an item opens: a feature key (see docs/home.md, "Targets") and an
/// optional id inside it.
class HomeTarget {
  const HomeTarget(this.feature, [this.id]);

  factory HomeTarget.fromJson(JsonReader r) =>
      HomeTarget(r.string('feature'), r.optString('id'));

  final String feature;
  final String? id;

  @override
  bool operator ==(Object other) =>
      other is HomeTarget && other.feature == feature && other.id == id;

  @override
  int get hashCode => Object.hash(feature, id);

  @override
  String toString() => 'HomeTarget($feature, $id)';
}

enum HomeSeverity { high, medium, low }

HomeSeverity _severity(String? value) => switch (value) {
      'high' => HomeSeverity.high,
      'low' => HomeSeverity.low,
      _ => HomeSeverity.medium,
    };

/// Whether one part of Home could be read: `ok`, `not_installed`,
/// `not_permitted`, `not_connected` or `unavailable`.
class HomeSourceStatus {
  const HomeSourceStatus(this.name, this.status, [this.detail]);

  factory HomeSourceStatus.fromJson(JsonReader r) => HomeSourceStatus(
      r.string('name'), r.string('status'), r.optString('detail'));

  final String name;
  final String status;
  final String? detail;

  bool get failed => status == 'unavailable';
}

class FocusItem {
  const FocusItem({
    required this.id,
    required this.source,
    required this.kind,
    required this.title,
    required this.target,
    this.detail,
    this.count,
    this.progress,
    this.severity = HomeSeverity.medium,
    this.at,
    this.params = const {},
  });

  factory FocusItem.fromJson(JsonReader r) => FocusItem(
        id: r.string('id'),
        source: r.string('source'),
        kind: r.string('kind'),
        title: r.string('title'),
        detail: r.optString('detail'),
        count: r.optInt('count'),
        progress: r.optNumber('progress'),
        severity: _severity(r.optString('severity')),
        at: r.optDate('at'),
        target: HomeTarget.fromJson(r.object('target')),
        params: r.optMap('params') ?? const {},
      );

  final String id, source, kind, title;
  final String? detail;
  final int? count;
  final double? progress;
  final HomeSeverity severity;
  final DateTime? at;
  final HomeTarget target;
  final Map<String, Object?> params;
}

class ActivityItem {
  const ActivityItem({
    required this.id,
    required this.kind,
    required this.title,
    required this.status,
    required this.at,
    required this.target,
  });

  factory ActivityItem.fromJson(JsonReader r) => ActivityItem(
        id: r.string('id'),
        kind: r.string('kind'),
        title: r.string('title'),
        status: r.string('status'),
        at: r.date('at'),
        target: HomeTarget.fromJson(r.object('target')),
      );

  final String id, kind, title, status;
  final DateTime at;
  final HomeTarget target;
}

class TodayProgress {
  const TodayProgress(this.completed, this.open);
  final int completed, open;

  int get total => completed + open;
  double get fraction => total == 0 ? 0 : completed / total;
}

class HomeSummary {
  const HomeSummary({
    required this.generatedAt,
    required this.focus,
    required this.counts,
    required this.activity,
    required this.sources,
    this.displayName,
    this.progress,
  });

  factory HomeSummary.fromJson(JsonReader r) {
    final progress = r.optObject('progress');
    final counts = <String, int>{};
    (r.optMap('counts') ?? const {}).forEach((key, value) {
      if (value is num) counts[key] = value.toInt();
    });
    return HomeSummary(
      generatedAt: r.date('generated_at'),
      displayName: r.optObject('profile')?.optString('display_name'),
      focus: r.optObjects('focus', FocusItem.fromJson),
      counts: counts,
      progress: progress == null
          ? null
          : TodayProgress(
              progress.integer('completed'), progress.integer('open')),
      activity: r.optObjects('activity', ActivityItem.fromJson),
      sources: r.optObjects('sources', HomeSourceStatus.fromJson),
    );
  }

  final DateTime generatedAt;
  final String? displayName;
  final List<FocusItem> focus;
  final Map<String, int> counts;
  final TodayProgress? progress;
  final List<ActivityItem> activity;
  final List<HomeSourceStatus> sources;

  List<HomeSourceStatus> get failedSources =>
      sources.where((s) => s.failed).toList();

  int get highPriority =>
      focus.where((f) => f.severity == HomeSeverity.high).length;

  HomeSummary withName(String? name) => HomeSummary(
        generatedAt: generatedAt,
        displayName: name,
        focus: focus,
        counts: counts,
        progress: progress,
        activity: activity,
        sources: sources,
      );
}

enum PinKind { workspace, link, feature }

PinKind _pinKind(JsonReader r) {
  final value = r.string('kind');
  for (final kind in PinKind.values) {
    if (kind.name == value) return kind;
  }
  throw JsonShapeException(
      r.at('kind'), 'workspace, link or feature', 'the string "$value"');
}

class HomePin {
  const HomePin({
    required this.id,
    required this.kind,
    required this.label,
    required this.target,
    this.icon,
    this.position = 0,
  });

  factory HomePin.fromJson(JsonReader r) => HomePin(
        id: r.string('id'),
        kind: _pinKind(r),
        label: r.string('label'),
        target: r.string('target'),
        icon: r.optString('icon'),
        position: r.optInt('position') ?? 0,
      );

  final String id;
  final PinKind kind;
  final String label;
  final String target;
  final String? icon;
  final int position;
}

enum CommandMode { work, personal, custom }

CommandMode commandModeOf(String? value) => switch (value) {
      'work' => CommandMode.work,
      'personal' => CommandMode.personal,
      _ => CommandMode.custom,
    };

class QuickCommand {
  const QuickCommand({
    required this.id,
    required this.mode,
    required this.label,
    required this.prompt,
    this.key,
    this.builtIn = false,
  });

  factory QuickCommand.fromJson(JsonReader r) => QuickCommand(
        id: r.string('id'),
        mode: commandModeOf(r.optString('mode')),
        label: r.string('label'),
        prompt: r.string('prompt'),
        key: r.optString('key'),
        builtIn: r.optBool('built_in') ?? false,
      );

  /// One of the user's own, as `/api/v1/quick-commands` (the command
  /// module, their only store) returns it.
  factory QuickCommand.fromQuickCommandsApi(JsonReader r) => QuickCommand(
        id: r.string('id'),
        mode: commandModeOf(r.optString('category')),
        label: r.string('title'),
        prompt: r.string('text'),
      );

  final String id;
  final CommandMode mode;
  final String label, prompt;

  /// Built-in commands carry a stable key the app translates.
  final String? key;
  final bool builtIn;
}

class CommandList {
  const CommandList(this.items, this.canEdit);

  factory CommandList.fromJson(JsonReader r) => CommandList(
      r.optObjects('items', QuickCommand.fromJson),
      r.optBool('can_edit') ?? false);

  final List<QuickCommand> items;
  final bool canEdit;
}

class InsightAction {
  const InsightAction(
      {required this.type, required this.verb, this.target, this.command});

  factory InsightAction.fromJson(JsonReader r) {
    final target = r.optObject('target');
    return InsightAction(
      type: r.string('type'),
      verb: r.string('verb'),
      target: target == null ? null : HomeTarget.fromJson(target),
      command: r.optString('command'),
    );
  }

  /// `open` (open [target]) or `command` (hand [command] to ANUM Command).
  final String type;

  /// analyze, compare, send_reminder, check_now, review or open.
  final String verb;
  final HomeTarget? target;
  final String? command;

  bool get isCommand => type == 'command' && (command ?? '').isNotEmpty;
}

class Insight {
  const Insight({
    required this.key,
    required this.source,
    required this.kind,
    required this.title,
    required this.finding,
    required this.question,
    required this.action,
    this.severity = HomeSeverity.medium,
    this.params = const {},
    this.createdAt,
    this.state = 'new',
    this.snoozedUntil,
  });

  factory Insight.fromJson(JsonReader r) => Insight(
        key: r.string('key'),
        source: r.string('source'),
        kind: r.string('kind'),
        severity: _severity(r.optString('severity')),
        title: r.string('title'),
        finding: r.string('finding'),
        question: r.string('question'),
        params: r.optMap('params') ?? const {},
        action: InsightAction.fromJson(r.object('action')),
        createdAt: r.optDate('created_at'),
        state: r.optString('state') ?? 'new',
        snoozedUntil: r.optDate('snoozed_until'),
      );

  final String key, source, kind, title, finding, question;
  final HomeSeverity severity;
  final Map<String, Object?> params;
  final InsightAction action;
  final DateTime? createdAt;

  /// new, snoozed or dismissed.
  final String state;
  final DateTime? snoozedUntil;

  bool get hidden => state != 'new';
}

class InsightList {
  const InsightList(this.items, this.hidden, this.sources);

  factory InsightList.fromJson(JsonReader r) => InsightList(
        r.optObjects('items', Insight.fromJson),
        r.optInt('hidden') ?? 0,
        r.optObjects('sources', HomeSourceStatus.fromJson),
      );

  final List<Insight> items;
  final int hidden;
  final List<HomeSourceStatus> sources;
}

/// One of the caller's workspaces, offered when pinning a workspace.
class PinnableWorkspace {
  const PinnableWorkspace(this.id, this.name);
  final String id;
  final String? name;
}
