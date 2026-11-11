import 'package:anum_mobile/data/api_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../../test/finance/finance_tools_fake.dart';
import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// Finance on the phone (docs/finance.md#reconciliation-sessions,
/// #rules-and-learning): the whole app, Finance from the menu, then the
/// owner's main path through the tools the phone gained: a reconciliation's
/// statement row posted as a correcting entry approved with its payload hash,
/// the session completed, and a new rule approved with the hash it was shown.
/// The Finance routes answer from the same fake as the widget tests
/// (test/finance/finance_tools_fake.dart), through the real HTTP transport.
void _installTools(FakeAnumApi api, ToolsApi tools) {
  FakeResponse answer(FakeRequest r) {
    final body = r.json;
    final response = tools.route(ApiRequest(
        method: r.method,
        uri: r.uri,
        body: body is Map<String, Object?> ? body : null));
    if (response == null) {
      return FakeResponse.error(404, 'not_found', 'No fixture for $r');
    }
    final json = response.body;
    if (response.statusCode >= 400) {
      return FakeResponse.json(json, status: response.statusCode);
    }
    // A list answer is a JSON array on the wire.
    if (json != null && json.length == 1 && json['data'] is List) {
      return FakeResponse.json(json['data'], status: response.statusCode);
    }
    return response.statusCode == 204
        ? const FakeResponse.empty()
        : FakeResponse.json(json, status: response.statusCode);
  }

  const f = '/api/v1/finance';
  for (final path in [
    '/accounts',
    '/imports',
    '/reconciliations',
    '/reconciliations/exceptions',
    '/reconciliations/{id}',
    '/rules',
    '/rules/{id}',
    '/rule-batches',
    '/subcategories',
  ]) {
    api.get('$f$path', answer);
  }
  for (final path in [
    '/reconciliations/{id}/resolutions',
    '/reconciliations/{id}/corrections/preview',
    '/reconciliations/{id}/corrections',
    '/reconciliations/{id}/complete',
    '/rules',
    '/rules/preview',
    '/rules/{id}/approve',
  ]) {
    api.post('$f$path', answer);
  }
}

void financeJourneys() => group('Finance', () {
      testWidgets(
          'finance: correct a statement row with its payload hash, complete '
          'the reconciliation, then approve a new rule', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        final tools = ToolsApi();
        _installTools(api, tools);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Finance');
        await waitFor(tester, find.byKey(const Key('finance-more')), api: api);

        // Reconciliation: open the session that does not balance.
        await tapOn(tester, find.byKey(const Key('finance-more')));
        await tapOn(tester, find.byKey(const Key('finance-more-recon')));
        await waitFor(tester, find.byKey(const Key('recon-session-rec_1')),
            api: api);
        await tapOn(tester, find.byKey(const Key('recon-session-rec_1')));
        await waitFor(tester, find.byKey(const Key('recon-act-correct-nil_1')),
            api: api);

        // The correcting entry: its lines and approval code, then post.
        await tapOn(tester, find.byKey(const Key('recon-act-correct-nil_1')));
        await waitFor(tester, find.text('Post a correcting entry'), api: api);
        expect(find.textContaining('expense:shopping'), findsOneWidget);
        await tapOn(tester, find.byKey(const Key('recon-correct-post')));
        await waitFor(tester, find.text('Correcting entry posted'), api: api);
        final posted = api.sent(
            'POST', '/api/v1/finance/reconciliations/rec_1/corrections');
        expect(posted.single.body['payload_hash'], correctionHash);

        // It balances now: complete it.
        await tapOn(tester, find.byKey(const Key('recon-complete')));
        await waitFor(tester, find.byKey(const Key('recon-reopen')), api: api);
        expect(tools.status, 'completed');
        await tester.pageBack();
        await pumpFor(tester);
        await tester.pageBack();
        await pumpFor(tester);

        // Rules: a new rule runs only once approved with its payload hash.
        await tapOn(tester, find.byKey(const Key('finance-more')));
        await tapOn(tester, find.byKey(const Key('finance-more-rules')));
        await waitFor(tester, find.byKey(const Key('rules-new')), api: api);
        await tapOn(tester, find.byKey(const Key('rules-new')));
        await waitFor(tester, find.byKey(const Key('rule-field-merchant')),
            api: api);
        await tester.enterText(
            find.descendant(
                of: find.byKey(const Key('rule-field-merchant')),
                matching: find.byType(EditableText)),
            'Example gym');
        await pumpFor(tester);
        await tapOn(tester, find.byKey(const Key('rule-save')));
        await waitFor(tester, find.text('Approve version 1?'), api: api);
        await tapOn(tester, find.byKey(const Key('rule-approve-confirm')));
        await waitFor(tester, find.byKey(const Key('rule-rule_1')), api: api);
        final approved =
            api.sent('POST', '/api/v1/finance/rules/rule_1/approve');
        expect(approved.single.body['payload_hash'], ruleHash);
        expect(tools.ruleVersions.single['status'], 'approved');
      });
    });
