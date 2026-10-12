import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/data/sse.dart';
import 'package:anum_mobile/features/command/command.dart';
import 'package:anum_mobile/features/voice/speech_service.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import '../voice/voice_fakes.dart';
import 'command_fakes.dart';

void main() {
  late FakeCommandRepository repository;
  late FakeSpeech speech;
  late FakeCommandTransfer transfer;
  late CommandController controller;

  setUp(() {
    repository = FakeCommandRepository();
    speech = FakeSpeech();
    transfer = FakeCommandTransfer();
    controller = CommandController(
      repository: repository,
      speech: speech,
      transfer: transfer,
      pollInterval: const Duration(milliseconds: 20),
      refreshDelay: const Duration(milliseconds: 5),
      reconnectDelay: const Duration(milliseconds: 20),
    );
  });

  tearDown(() => controller.dispose());

  group('models', () {
    test('a plan and a live view parse, and wrong shapes name the field', () {
      final plan = repository.plan;
      expect(plan.steps.map((step) => step.agent.id), ['email', 'action']);
      expect(plan.steps.last.approval, ApprovalNeed.required);
      final live = view('waiting_approval', [
        stepViewJson(1, 'email', 'completed', result: 'Found 12 emails'),
        stepViewJson(2, 'action', 'waiting_approval',
            approvals: [approvalJson('approval_1', 'task_2')]),
      ]);
      expect(live.status, CommandStatus.waitingApproval);
      expect(live.pending.single.payloadHash, hasLength(64));
      expect(live.taskIds, {'task_1', 'task_2'});
      expect(live.plainSummary(), contains('Found 12 emails'));
      expect(
          () => CommandSummary.fromJson(
              JsonReader(const {'id': 'command_1', 'text': 3})),
          throwsA(
              isA<JsonShapeException>().having((e) => e.path, 'path', 'text')));
      expect(CommandStatus.parse('partial').finished, isTrue);
      expect(CommandStatus.parse('running').active, isTrue);
    });
  });

  test('typing a request plans it and lands on the plan', () async {
    await controller.submit('  Check my emails  ');
    expect(repository.previews, ['Check my emails']);
    expect(controller.stage, CommandStage.plan);
    expect(controller.steps, hasLength(2));
    expect(controller.approvalsAhead, 1);
    expect(controller.canStart, isTrue);
  });

  test('a planning failure stays on Understanding with the reason', () async {
    repository.previewError = const ApiException(403, 'Forbidden');
    await controller.submit('Check my emails');
    expect(controller.stage, CommandStage.understanding);
    expect(controller.planError!.kind, FailureKind.permission);
    repository.previewError = const ApiException(0, 'unreachable');
    await controller.retryPlan();
    expect(controller.planError!.kind, FailureKind.offline);
    repository.previewError = null;
    await controller.retryPlan();
    expect(controller.stage, CommandStage.plan);
  });

  test('steps can be edited, reordered and removed before starting', () async {
    await controller.submit('Check my emails');
    controller.moveStep(1, -1);
    expect(controller.steps.first.agent.id, 'action');
    controller.editStep(0, 'Send the summary to Sajid');
    expect(controller.steps.first.instruction, 'Send the summary to Sajid');
    controller.removeStep(1);
    expect(controller.steps, hasLength(1));
    controller.removeStep(0);
    expect(controller.canStart, isFalse);
  });

  test('a blocked step stops the start', () async {
    repository.plan = CommandPlan.fromJson(JsonReader(planJson([
      planStepJson(1, 'email', 'Check my emails', approval: 'blocked'),
    ])));
    await controller.submit('Check my emails');
    expect(controller.hasBlockedStep, isTrue);
    expect(controller.canStart, isFalse);
  });

  test('start creates the command, runs every step and follows it to results',
      () async {
    final settled = <CommandView>[];
    controller.onSettled = settled.add;
    repository.views = [
      view('running', [
        stepViewJson(1, 'email', 'running'),
        stepViewJson(2, 'action', 'queued'),
      ]),
      view('waiting_approval', [
        stepViewJson(1, 'email', 'completed', result: 'Found 12 urgent emails'),
        stepViewJson(2, 'action', 'waiting_approval',
            approvals: [approvalJson('approval_1', 'task_2')]),
      ]),
    ];
    await controller.submit('Check my emails, then send the summary');
    await controller.start();
    expect(repository.creates.single['steps'],
        ['Check my emails', 'Send the summary to the team']);
    expect(repository.runs, ['task_1', 'task_2']);
    await Future<void>.delayed(const Duration(milliseconds: 60));
    expect(controller.command!.status, CommandStatus.waitingApproval);
    expect(controller.stage, CommandStage.results);
    expect(settled, hasLength(1));
    expect(controller.nextSteps.single.text,
        'Draft replies to the most urgent emails');

    // Approve all: one request per approval, with its payload hash.
    repository.views = [
      view('completed', [
        stepViewJson(1, 'email', 'completed', result: 'Found 12 urgent emails'),
        stepViewJson(2, 'action', 'completed', result: 'Sent'),
      ]),
    ];
    final outcome = await controller.approveAll();
    expect(outcome.approved, 1);
    expect(repository.approvals, ['approval_1']);
    expect(controller.command!.status, CommandStatus.completed);
  });

  test('a step that cannot start shows why, and Stop cancels unfinished steps',
      () async {
    repository.runErrors = {
      'task_2': const ApiException(402, 'The monthly model budget is used up.')
    };
    repository.views = [
      view('running', [
        stepViewJson(1, 'email', 'running'),
        stepViewJson(2, 'action', 'created'),
      ]),
    ];
    await controller.submit('Check my emails');
    await controller.start();
    expect(controller.stepErrors['task_2'], contains('budget'));
    expect(controller.notStarted.single.taskId, 'task_2');
    await controller.stop();
    expect(repository.cancels.single, ['task_1', 'task_2']);
  });

  test('live events of the command refresh it; others are ignored', () async {
    repository.views = [
      view('running', [stepViewJson(1, 'email', 'running')]),
    ];
    await controller.open('command_1');
    expect(transfer.subscriptions, 1);
    repository.views = [
      view('completed', [stepViewJson(1, 'email', 'completed', result: 'ok')]),
    ];
    transfer.events$.add(const SseEvent('task.created',
        {'subject': 'task_other', 'payload': <String, Object?>{}}));
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(controller.live, isTrue);
    transfer.events$.add(const SseEvent('agent_run.completed',
        {'subject': 'task_1', 'payload': <String, Object?>{}}));
    await Future<void>.delayed(const Duration(milliseconds: 40));
    expect(controller.command!.status, CommandStatus.completed);
    expect(controller.stage, CommandStage.results);
  });

  test('when the stream drops the view is polled', () async {
    repository.views = [
      view('running', [stepViewJson(1, 'email', 'running')]),
    ];
    await controller.open('command_1');
    await transfer.events$.close();
    repository.views = [
      view('failed', [stepViewJson(1, 'email', 'failed')]),
    ];
    await Future<void>.delayed(const Duration(milliseconds: 80));
    expect(controller.live, isFalse);
    expect(controller.command!.status, CommandStatus.failed);
  });

  test('listening keeps an editable transcript and needs a confirmation',
      () async {
    controller.setLanguage('ar');
    await controller.startListening();
    expect(speech.last.locale, 'ar-SA');
    expect(controller.listeningActive, isTrue);
    speech.last.onResult('تحقق من بريدي', false);
    expect(controller.transcript, 'تحقق من بريدي');
    speech.last.onDone();
    expect(controller.listeningActive, isFalse);
    expect(controller.stage, CommandStage.listening);
    expect(repository.previews, isEmpty);
    await controller.submit('تحقق من بريدي اليوم', source: 'voice');
    expect(repository.previews, ['تحقق من بريدي اليوم']);
  });

  test('a denied microphone is reported, nothing is heard', () async {
    speech.availability = SpeechAvailability.denied;
    await controller.startListening();
    expect(controller.micState, SpeechAvailability.denied);
    expect(speech.listens, isEmpty);
    await controller.cancelListening();
    expect(controller.stage, CommandStage.start);
  });

  test('attachments are uploaded and sent with the command', () async {
    transfer.picked = '/tmp/tyre-photo.jpg';
    await controller.attachFile();
    expect(controller.attachments.single.name, 'tyre-photo.jpg');
    await controller.submit('Check this tyre');
    await controller.start();
    expect(repository.creates.single['attachments'], ['file_1']);
    expect(controller.attachments, isEmpty);
  });

  test('suggestions load, and fail softly', () async {
    await controller.loadSuggestions();
    expect(controller.suggestionsPhase, LoadPhase.ready);
    repository.suggestionsError = const ApiException(500, 'boom');
    await controller.loadSuggestions();
    expect(controller.suggestionsPhase, LoadPhase.error);
  });

  test('quick commands: create, edit, move, delete and permission', () async {
    await controller.loadQuick();
    expect(controller.quickPhase, LoadPhase.ready);
    expect(
        await controller.saveQuick(
            title: 'Mail',
            text: 'Check my emails',
            category: QuickCategory.work),
        isNull);
    await controller.saveQuick(
        title: 'Plan', text: 'Draft the plan', category: QuickCategory.work);
    final work = controller.quickIn(QuickCategory.work);
    expect(work.map((q) => q.title), ['Mail', 'Plan']);
    await controller.moveQuick(work.last, -1);
    expect(controller.quickIn(QuickCategory.work).map((q) => q.title),
        ['Plan', 'Mail']);
    await controller.saveQuick(
        existing: work.first,
        title: 'Morning mail',
        text: 'Check my emails',
        category: QuickCategory.personal);
    expect(controller.quickIn(QuickCategory.personal).single.title,
        'Morning mail');
    await controller.deleteQuick(controller.quick.first);
    expect(controller.quick, hasLength(1));
    repository.quickError = const ApiException(403, 'Forbidden');
    final failure = await controller.saveQuick(
        title: 'x', text: 'y', category: QuickCategory.custom);
    expect(failure!.kind, FailureKind.permission);
  });

  test('a quick command run counts as its source', () async {
    await controller.startFrom('Check PMV tyre report',
        source: 'quick_command', quickCommandId: 'quick_1');
    await controller.start();
    expect(repository.creates.single['source'], 'quick_command');
    expect(repository.creates.single['quick'], 'quick_1');
  });

  test('report and files are saved through the transfer', () async {
    repository.views = [
      view('completed', [stepViewJson(1, 'email', 'completed')]),
    ];
    await controller.open('command_1');
    expect(await controller.reportText(), contains('All done'));
    expect(await controller.saveReport(), isTrue);
    expect(await controller.saveFile(controller.command!.files.single), isTrue);
    expect(transfer.saved, ['anum-command-report.md', '01-step-run-log.md']);
    expect(transfer.downloads.first,
        '/api/v1/commands/command_1/report?format=markdown');
  });

  test('settled notice in both languages', () {
    final done = view('completed', [stepViewJson(1, 'email', 'completed')]);
    expect(commandSettledNotice(done, null), 'Your command finished: Done');
    expect(commandSettledNotice(done, const Locale('ar')), contains('انتهى'));
  });
}
