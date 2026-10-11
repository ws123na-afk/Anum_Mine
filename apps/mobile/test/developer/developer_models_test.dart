// Developer mode helpers (docs/developer-mode.md): the same rules as the
// web's lib/developer.ts.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

DevRequest _request(String status, {int? pr, String id = 'dev_1'}) =>
    DevRequest.fromJson(JsonReader(requestJson(id, status: status, pr: pr)));

void main() {
  group('commands', () {
    test('developer and build triggers return the request text', () {
      expect(parseDeveloperCommand('developer, add a dark mode toggle'),
          'add a dark mode toggle');
      expect(parseDeveloperCommand('Build a settings export page'),
          'a settings export page');
      expect(parseDeveloperCommand('Anum build a weekly digest email'),
          'a weekly digest email');
      expect(parseDeveloperCommand('hey anum, developer: please add search'),
          'add search');
      expect(parseDeveloperCommand('developer, please fix'), isNull,
          reason: 'shorter than 10 characters after "please"');
      expect(parseDeveloperCommand('developer to add a search box to Files'),
          'add a search box to Files');
    });

    test('other text, and "build" with nothing to build, are not commands', () {
      expect(parseDeveloperCommand('build'), isNull);
      expect(parseDeveloperCommand('build it'), isNull);
      expect(
          parseDeveloperCommand('What should we build next quarter?'), isNull);
      expect(parseDeveloperCommand('builder pattern explained please'), isNull);
    });

    test('answers to "Start?" in English and Arabic', () {
      for (final yes in ['yes', 'Yes.', 'start', 'go ahead', 'OK!', 'نعم']) {
        expect(parseConfirmation(yes), Confirmation.yes, reason: yes);
      }
      for (final no in ['no', 'Cancel', 'not now', "don't", 'لا']) {
        expect(parseConfirmation(no), Confirmation.no, reason: no);
      }
      expect(parseConfirmation('yes but change the colour'), isNull);
    });

    test('approve intent only asks to review', () {
      for (final text in [
        'approve',
        'Approve it.',
        'approve and merge',
        'merge it',
        'Anum, approve'
      ]) {
        expect(isApproveIntent(text), isTrue, reason: text);
      }
      expect(isApproveIntent('approve the budget increase'), isFalse);
      expect(confirmPrompt('add search'),
          "I'll ask the developer to: add search. Start?");
    });
  });

  group('request body', () {
    test('trims, and says what to fix', () {
      expect(requestBody('  short '), isA<InvalidRequest>());
      expect(requestBody('x' * 4001), isA<InvalidRequest>());
      final ok = requestBody('  Add a dark/light toggle  ') as ValidRequest;
      expect(ok.text, 'Add a dark/light toggle');
      expect(ok.allowProtectedPaths, isFalse);
    });

    test('protected topics need the explicit allow', () {
      final refused = requestBody('Rotate the API keys in the workflow files');
      expect((refused as InvalidRequest).error, contains('Allow changes'));
      final allowed = requestBody('Rotate the API keys in the workflow files',
          allowProtected: true);
      expect((allowed as ValidRequest).allowProtectedPaths, isTrue);
      expect(mentionsProtected('Edit .github/workflows/ci.yml'), isTrue);
      expect(mentionsProtected('Add a dark mode toggle'), isFalse);
    });
  });

  group('requests', () {
    test('parses the API row with timeline, checks and pending approval', () {
      final item = DevRequest.fromJson(JsonReader(requestJson('dev_9',
          status: 'waiting_approval',
          pr: 42,
          prTitle: 'Dark mode toggle',
          approval: approvalJson('appr_9'))));
      expect(item.status, DevStatus.waitingApproval);
      expect(item.title, 'Dark mode toggle');
      expect(item.timeline.map((s) => s.state).toList(), [
        StageState.done,
        StageState.done,
        StageState.done,
        StageState.done,
        StageState.active,
        StageState.pending,
      ]);
      expect(item.approval!.payloadHash, exampleHash);
      expect(item.approval!.target, 'api.github.com');
      expect(checksLabel(item.checks), '3 of 3 checks passed');
      expect(canRequestMerge(item), isTrue);
      expect(canCancel(item), isTrue);
    });

    test('an unknown status shows its name and offers no actions', () {
      final item = _request('rebasing');
      expect(item.status, DevStatus.unknown);
      expect(item.statusLabel, 'rebasing');
      expect(canCancel(item), isFalse);
      expect(canRequestMerge(item), isFalse);
    });

    test('a malformed row names the field', () {
      expect(
          () => DevRequest.fromJson(JsonReader({'id': 'x'}, 'data[0]')),
          throwsA(isA<FormatException>()
              .having((e) => e.message, 'message', contains('data[0].'))));
    });

    test('checks, tones and finished states', () {
      expect(checksLabel(null), 'No CI results yet');
      expect(
          checksLabel(const DevChecks(
              total: 5,
              passed: 2,
              failed: 2,
              pending: 1,
              failing: ['Web', 'API', 'Docs', 'Mobile'])),
          '2 of 5 checks passed, 1 running, 2 failing (Web, API, Docs)');
      expect(statusTone(DevStatus.merged), DevTone.ok);
      expect(statusTone(DevStatus.ciFailed), DevTone.stop);
      expect(statusTone(DevStatus.coding), DevTone.warn);
      expect(isActive(_request('merged', pr: 1)), isFalse);
      expect(isActive(_request('cancelled')), isFalse);
      expect(isActive(_request('ci_running', pr: 1)), isTrue);
    });

    test('newly ready requests are announced once, never on the first poll',
        () {
      final coding = [_request('ci_running', pr: 3)];
      final ready = [_request('waiting_approval', pr: 3)];
      expect(newlyReady(null, ready), isEmpty);
      expect(newlyReady(coding, ready).single.id, 'dev_1');
      expect(newlyReady(ready, ready), isEmpty);
      expect(readyAnnouncement(ready.single),
          contains('The change is ready for review'));
    });

    test('polling is fast while moving, slow while waiting, off otherwise', () {
      expect(pollDelay([_request('coding')]), const Duration(seconds: 20));
      expect(pollDelay([_request('waiting_approval', pr: 1)]),
          const Duration(seconds: 60));
      expect(pollDelay([_request('merged', pr: 1)]), isNull);
      expect(pollDelay(const []), isNull);
    });
  });

  test('setup lists missing settings by name, never values', () {
    final setup = DevSetup.fromJson(JsonReader(setupJson(
        configured: false,
        missing: ['ANUM_DEVELOPER_GITHUB_TOKEN', 'ANUM_DEVELOPER_NEW'])));
    expect(setup.ready, isFalse);
    final steps = setupSteps(setup);
    expect(steps.map((s) => s.name),
        ['ANUM_DEVELOPER_GITHUB_TOKEN', 'ANUM_DEVELOPER_NEW']);
    expect(steps.first.purpose, contains('fine-grained GitHub token'));
    expect(steps.last.purpose, contains('docs/developer-mode.md'));
  });

  test('errors say what failed and how to fix it', () {
    expect(explainDeveloperError(const ApiException(403, 'Forbidden')),
        developerOwnerOnly);
    expect(
        explainDeveloperError(const ApiException(
            409, 'A developer request is already queued or coding.')),
        contains('already queued'));
    expect(explainDeveloperError(const ApiException(401, 'expired')),
        contains('Sign in again'));
    expect(explainDeveloperError(Exception('socket')),
        contains('Check the connection'));
  });

  test('only https links open', () {
    expect(safeLink('https://github.com/example/anum/pull/1'), isNotNull);
    expect(safeLink('http://github.com/example'), isNull);
    expect(safeLink('javascript:alert(1)'), isNull);
    expect(safeLink(null), isNull);
  });
}
