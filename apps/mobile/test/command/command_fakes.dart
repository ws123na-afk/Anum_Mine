import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/command/command.dart';

Map<String, Object?> agentJson(String id, [String? name]) => {
      'id': id,
      'name': name ?? '${id[0].toUpperCase()}${id.substring(1)} agent'
    };

Map<String, Object?> planStepJson(int position, String agent, String text,
        {String approval = 'none',
        String risk = 'low',
        String tool = 'anum.respond',
        bool connected = true,
        String? note}) =>
    {
      'position': position,
      'title': text,
      'instruction': text,
      'agent': agentJson(agent),
      'skills': [
        {'id': 'anum.email', 'name': 'Email inbox'}
      ],
      'tool': {'name': tool, 'description': 'Tool', 'category': 'general'},
      'risk_level': risk,
      'approval': approval,
      'approval_reason': approval == 'blocked'
          ? 'An organization policy pack denies this action.'
          : 'ok',
      'connected': connected,
      'note': note,
    };

Map<String, Object?> planJson(List<Map<String, Object?>> steps) => {
      'text': 'Check my emails, then send the summary to the team',
      'language': 'en',
      'steps': steps,
      'agents': [
        for (final step in steps) step['agent'],
      ],
      'approvals_required': 0,
      'blocked': 0,
    };

Map<String, Object?> approvalJson(String id, String taskId) => {
      'id': id,
      'task_id': taskId,
      'action': 'external.action',
      'risk_level': 'high',
      'status': 'pending',
      'reason': 'External actions require explicit approval.',
      'payload_hash': 'a' * 64,
      'arguments': {'action': 'send the summary'},
    };

Map<String, Object?> stepViewJson(int position, String agent, String status,
        {String? result, List<Map<String, Object?>> approvals = const []}) =>
    {
      'position': position,
      'title': 'Step $position title',
      'instruction': 'Step $position instruction',
      'agent': agentJson(agent),
      'tool': 'anum.respond',
      'risk_level': 'low',
      'approval': approvals.isEmpty ? 'none' : 'required',
      'task_id': 'task_$position',
      'task': null,
      'status': status,
      'progress': const {
            'completed': 1.0,
            'failed': 1.0,
            'cancelled': 1.0,
            'waiting_approval': 0.6,
            'running': 0.5,
          }[status] ??
          0.0,
      'run_id': status == 'created' ? null : 'run_$position',
      'result': result,
      'started_at': null,
      'finished_at': null,
      'approvals': approvals,
    };

Map<String, Object?> viewJson(String status, List<Map<String, Object?>> steps,
        {String id = 'command_1'}) =>
    {
      'id': id,
      'text': 'Check my emails, then send the summary to the team',
      'source': 'typed',
      'locale': 'en',
      'status': status,
      'progress': 0.5,
      'step_count': steps.length,
      'completed_steps':
          steps.where((step) => step['status'] == 'completed').length,
      'pending_approvals': 0,
      'agents': [agentJson('email')],
      'created_at': '2026-10-11T09:41:00Z',
      'updated_at': '2026-10-11T09:41:00Z',
      'created_by': 'user_test',
      'steps': steps,
      'pending': [
        for (final step in steps)
          for (final approval in (step['approvals']! as List<Object?>)) approval
      ],
      'timeline': [
        {
          'id': 'event_1',
          'at': '2026-10-11T09:41:00Z',
          'type': 'agent_run.completed',
          'task_id': 'task_1',
          'position': 1,
          'agent': agentJson('email'),
          'title': 'Step 1 title',
        }
      ],
      'files': [
        {
          'kind': 'run_log',
          'name': '01-step-run-log.md',
          'path': '/api/v1/tasks/task_1/export?format=markdown',
          'file_id': null,
          'task_id': 'task_1',
          'content_type': 'text/markdown',
        }
      ],
      'attachments': [],
      'duration_seconds': status == 'completed' ? 154.0 : null,
    };

CommandView view(String status, List<Map<String, Object?>> steps) =>
    CommandView.fromJson(JsonReader(viewJson(status, steps)));

/// In-memory [CommandRepository]: [views] are served in order by `load`.
class FakeCommandRepository implements CommandRepository {
  List<CommandSuggestion> suggestionList = [
    CommandSuggestion.fromJson(JsonReader(const {
      'kind': 'quick',
      'text': 'Check PMV tyre report',
      'title': 'PMV report',
      'quick_command_id': 'quick_1',
    })),
    CommandSuggestion.fromJson(JsonReader(const {
      'kind': 'starter',
      'text': 'Check my emails and summarize the urgent ones',
      'title': 'Check my emails and summarize the urgent ones',
    })),
  ];
  Object? suggestionsError;
  Object? previewError;
  Object? createError;
  Map<String, Object> runErrors = {};
  CommandPlan plan = CommandPlan.fromJson(JsonReader(planJson([
    planStepJson(1, 'email', 'Check my emails', tool: 'search_email'),
    planStepJson(2, 'action', 'Send the summary to the team',
        approval: 'required', risk: 'high', tool: 'external.action'),
  ])));
  CommandView created = view('ready', [
    stepViewJson(1, 'email', 'created'),
    stepViewJson(2, 'action', 'created'),
  ]);
  List<CommandView> views = [];
  final previews = <String>[];
  final creates = <Map<String, Object?>>[];
  final runs = <String>[];
  final cancels = <List<String>>[];
  final approvals = <String>[];
  List<NextStep> next = [
    NextStep.fromJson(JsonReader(const {
      'id': 'email_0',
      'kind': 'command',
      'label': 'Draft replies to the most urgent emails',
      'text': 'Draft replies to the most urgent emails',
    })),
  ];
  List<QuickCommand> quick = [];
  Object? quickError;
  int quickIds = 0;
  List<CommandSummary> historyItems = [];

  @override
  Future<List<CommandSuggestion>> suggestions(String locale) async {
    if (suggestionsError != null) throw suggestionsError!;
    return suggestionList;
  }

  @override
  Future<CommandPlan> preview(String text, String locale) async {
    previews.add(text);
    if (previewError != null) throw previewError!;
    return plan;
  }

  @override
  Future<CommandView> create(
      {required String text,
      required List<String> steps,
      required String source,
      required String locale,
      List<String> attachments = const [],
      String? quickCommandId}) async {
    if (createError != null) throw createError!;
    creates.add({
      'text': text,
      'steps': steps,
      'source': source,
      'locale': locale,
      'attachments': attachments,
      'quick': quickCommandId,
    });
    return created;
  }

  @override
  Future<void> runTask(String taskId) async {
    runs.add(taskId);
    final error = runErrors[taskId];
    if (error != null) throw error;
  }

  @override
  Future<void> cancelTasks(List<String> taskIds) async => cancels.add(taskIds);

  @override
  Future<CommandView> load(String commandId) async {
    if (views.isEmpty) return created;
    return views.length == 1 ? views.single : views.removeAt(0);
  }

  @override
  Future<List<CommandSummary>> list({String? cursor, int limit = 20}) async =>
      historyItems;

  @override
  Future<void> delete(String commandId) async =>
      historyItems.removeWhere((item) => item.id == commandId);

  @override
  Future<List<NextStep>> nextSteps(String commandId) async => next;

  @override
  Future<void> approve(CommandApproval approval) async {
    if (approval.payloadHash == null) {
      throw const ApiException(409, 'not bound');
    }
    approvals.add(approval.id);
  }

  @override
  Future<List<QuickCommand>> quickCommands() async {
    if (quickError != null) throw quickError!;
    return quick;
  }

  QuickCommand _quick(String id, String title, String text,
          QuickCategory category, int position) =>
      QuickCommand.fromJson(JsonReader({
        'id': id,
        'title': title,
        'text': text,
        'category': category.name,
        'position': position,
        'use_count': 0,
      }));

  @override
  Future<QuickCommand> createQuick(
      {required String title,
      required String text,
      required QuickCategory category}) async {
    if (quickError != null) throw quickError!;
    final item = _quick('quick_${++quickIds}', title, text, category,
        quick.where((q) => q.category == category).length);
    quick = [...quick, item];
    return item;
  }

  @override
  Future<QuickCommand> updateQuick(String id,
      {String? title,
      String? text,
      QuickCategory? category,
      int? position}) async {
    final current = quick.firstWhere((item) => item.id == id);
    final item = _quick(id, title ?? current.title, text ?? current.text,
        category ?? current.category, position ?? current.position);
    quick = [for (final q in quick) q.id == id ? item : q];
    return item;
  }

  @override
  Future<void> deleteQuick(String id) async => quick = [
        for (final q in quick)
          if (q.id != id) q
      ];
}

class FakeCommandTransfer implements CommandTransfer {
  final events$ = StreamController<SseEvent>.broadcast();
  int subscriptions = 0;
  String? picked;
  final saved = <String>[];
  final downloads = <String>[];

  @override
  Stream<SseEvent> events({String? lastEventId}) {
    subscriptions++;
    return events$.stream;
  }

  @override
  Future<Uint8List> download(String apiPath) async {
    downloads.add(apiPath);
    return Uint8List.fromList(utf8.encode('# Report\n\nAll done.'));
  }

  @override
  Future<String?> pickFile() async => picked;

  @override
  Future<CommandAttachment> attach(String path) async =>
      CommandAttachment(fileId: 'file_1', name: path.split('/').last);

  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    saved.add(fileName);
    return true;
  }
}
