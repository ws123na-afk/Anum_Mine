// "build ..." / "developer, ..." from the chat and the voice view: confirm
// first, refuse non-owners, and never merge without a tap.
import 'package:anum_mobile/features/developer/developer_assist.dart';
import 'package:anum_mobile/features/developer/developer_models.dart';
import 'package:anum_mobile/features/developer/developer_repository.dart';
import 'package:flutter_test/flutter_test.dart';

import 'developer_fakes.dart';

Future<DeveloperAssist> _assist(FakeDeveloperApi api,
        {String source = 'text', bool owner = true}) async =>
    DeveloperAssist(
      repository: ApiDeveloperRepository(await developerApi(api)),
      source: source,
      isOwner: () async => owner,
    );

void main() {
  test('ordinary questions are not for the developer', () async {
    final api = FakeDeveloperApi();
    final assist = await _assist(api);
    expect(await assist.intercept('What is waiting for my approval?'), isNull);
    expect(assist.hasPanel, isFalse);
    expect(api.sent, isEmpty);
  });

  test('a command asks first; Start creates it with its source', () async {
    final api = FakeDeveloperApi();
    final assist = await _assist(api, source: 'voice');
    final reply =
        await assist.intercept('Developer, add a dark mode toggle to Settings');
    expect(reply,
        "I'll ask the developer to: add a dark mode toggle to Settings. Start?");
    expect(assist.awaitingAnswer, isTrue);
    expect(api.calls('POST', '/developer/requests'), isEmpty);

    final started = await assist.intercept('yes');
    expect(started, startsWith('Started.'));
    expect(assist.awaitingAnswer, isFalse);
    final body = api.calls('POST', '/developer/requests').single.body!;
    expect(body['text'], 'add a dark mode toggle to Settings');
    expect(body['source'], 'voice');
    expect(body['allow_protected_paths'], false);
  });

  test('"no" declines; anything else drops the question', () async {
    final api = FakeDeveloperApi();
    final assist = await _assist(api);
    await assist.intercept('build a weekly digest email');
    expect(await assist.intercept('no'), "Okay, I won't start it.");
    expect(assist.awaitingAnswer, isFalse);

    await assist.intercept('build a weekly digest email');
    expect(await assist.intercept('What is running?'), isNull);
    expect(assist.awaitingAnswer, isFalse);

    await assist.intercept('build a weekly digest email');
    assist.decline();
    expect(assist.message, "Okay, I won't start it.");
    expect(api.calls('POST', '/developer/requests'), isEmpty);
  });

  test('non-owners get a polite refusal and nothing is sent', () async {
    final api = FakeDeveloperApi(role: 'member');
    final assist = await _assist(api, owner: false);
    expect(
        await assist.intercept('build a weekly digest email'), ownerOnlyReply);
    expect(assist.awaitingAnswer, isFalse);
    expect(api.calls('POST', '/developer/'), isEmpty);
  });

  test('protected topics go to the Developer screen instead', () async {
    final api = FakeDeveloperApi();
    final assist = await _assist(api);
    await assist.intercept('build a rotation of the API keys in CI');
    expect(await assist.intercept('yes'), contains('Developer screen'));
    expect(api.calls('POST', '/developer/requests'), isEmpty);
  });

  test('a refused start says why', () async {
    final api = FakeDeveloperApi()
      ..failures['POST create'] =
          (429, 'Developer mode allows 10 requests a day. Try again tomorrow.');
    final assist = await _assist(api);
    await assist.intercept('build a weekly digest email');
    expect(
        await assist.intercept('yes'),
        'The developer could not start: Developer mode allows 10 requests a '
        'day. Try again tomorrow.');
  });

  test('a ready pull request is announced; "approve" only opens the review',
      () async {
    final api = FakeDeveloperApi(requests: [
      requestJson('dev_1', status: 'ci_running', pr: 42, prTitle: 'Dark mode')
    ]);
    final assist = await _assist(api, source: 'voice');
    final said = <String>[];
    assist.announce = said.add;
    await assist.poll();
    expect(said, isEmpty, reason: 'the first poll announces nothing');

    api.requests[0] = requestJson('dev_1',
        status: 'waiting_approval', pr: 42, prTitle: 'Dark mode');
    await assist.poll();
    expect(said.single,
        'The change is ready for review: Dark mode. Say approve to merge.');
    expect(assist.ready!.id, 'dev_1');

    final reply = await assist.intercept('approve');
    expect(reply, contains('payload hash'));
    expect(reply, contains('Tap Confirm merge'));
    expect(assist.review!.approval.payloadHash, exampleHash);
    expect(api.calls('POST', '/approve'), isEmpty,
        reason: 'voice never approves');
    // Saying it again still does not approve.
    await assist.intercept('approve and merge');
    expect(api.calls('POST', '/approve'), isEmpty);

    await assist.confirmMerge();
    expect(api.calls('POST', '/approve').single.body,
        {'payload_hash': exampleHash});
    expect(assist.message, 'Merged pull request #42.');
    expect(assist.review, isNull);
  });

  test('"approve" with nothing waiting is not a developer command', () async {
    final api = FakeDeveloperApi();
    final assist = await _assist(api);
    await assist.poll();
    expect(await assist.intercept('approve'), isNull);
  });

  test('a refused merge keeps the review closed and says why', () async {
    final api = FakeDeveloperApi(
        requests: [requestJson('dev_1', status: 'waiting_approval', pr: 42)])
      ..failures['POST approve'] = (403, 'Another owner must approve this.');
    final assist = await _assist(api);
    await assist.poll();
    await assist.intercept('approve');
    await assist.confirmMerge();
    expect(assist.message, 'Merge refused: Another owner must approve this.');
    expect(assist.review, isNotNull, reason: 'it can be retried or dismissed');
    assist.dismissReview();
    expect(assist.review, isNull);
  });

  test('a review that cannot open explains it', () async {
    final api = FakeDeveloperApi(
        requests: [requestJson('dev_1', status: 'waiting_approval', pr: 42)])
      ..failures['POST merge'] = (409, 'Not ready to merge: CI is running.');
    final assist = await _assist(api);
    await assist.poll();
    expect(await assist.intercept('merge it'),
        'The merge is not ready: Not ready to merge: CI is running.');
    expect(assist.review, isNull);
    assist.reset();
    expect(assist.hasPanel, isFalse);
    expect(assist.requests, isNull);
    expect(DevStatus.parse('waiting_approval'), DevStatus.waitingApproval);
  });
}
