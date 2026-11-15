// Automatic matching on the phone (docs/finance-matching.md): a match that
// waits is linked, an automatic one undone, the policy approved, history
// previewed and undone, installments and capture health shown. Against a
// fake API with the API's JSON shapes, in English and Arabic, at 320 and
// 1024 px and large text. Fixtures are plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';

import 'package:anum_mobile/features/finance/finance_matching_data.dart';
import 'package:anum_mobile/features/finance/finance_matching_screens.dart';
import 'package:anum_mobile/features/finance/finance_matching_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

const fin = '/api/v1/finance';
final _dash = RegExp('[–—]');

Map<String, Object?> record(String id, String outcome,
        {String label = 'Example shop 2/4'}) =>
    {
      'id': id,
      'canonical_id': 'fin_je_$id',
      'kind': 'installment',
      'dimension': 'bill',
      'target_type': 'finance_bill_due',
      'target_id': 'fin_bill_$id:2026-11-15',
      'label': label,
      'due_on': '2026-11-15',
      'posted_on': '2026-11-15',
      'amount': '125.00',
      'currency': 'SAR',
      'outcome': outcome,
      'evidence_state': outcome == 'auto_linked' ? 'verified' : 'pending',
      'reason_code':
          outcome == 'review' ? 'ambiguous_obligation' : 'provider_amount_date',
      'confidence': '0.900',
      'evidence': [
        {'code': 'provider', 'value': 'tamara', 'weight': '0.30'}
      ],
      'flags': <String>[],
      'competitors': outcome == 'review' ? 1 : 0,
      'policy_version': 'pfm-1.r1',
      'link_id': outcome == 'auto_linked' ? 'fin_link_$id' : null,
      'version': 1,
    };

class MatchingFake {
  MatchingFake() {
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  bool approved = false;
  final records = [
    record('a', 'review'),
    record('c', 'auto_linked', label: 'Demo market 3/4')
  ];
  final runs = <Map<String, Object?>>[];

  Map<String, Object?> get policyView => {
        'policy': {
          'match_transactions': approved,
          'reconcile_installments': approved,
          'link_recurring_bills': false,
          'link_card_repayments': false,
          'link_bank_loan_repayments': false,
          'prevent_duplicates': true,
          'reprocess_history': approved,
          'notify_exceptions': false,
          'min_confidence': '0.9',
          'review_confidence': '0.5',
          'settled_sources': ['statement', 'sms'],
          'source_account_ids': approved ? ['acc_1'] : <String>[],
          'revision': approved ? 1 : 0,
        },
        'version': approved ? 'pfm-1.r1' : 'pfm-1.r0',
        'approved': approved,
        'never_automated': ['approve_payment', 'link_person_loan_payments'],
        'providers': [
          {
            'key': 'tamara',
            'name': 'Tamara',
            'reads': ['gmail'],
            'connected': true
          },
          {
            'key': 'tabby',
            'name': 'Tabby',
            'reads': <String>[],
            'connected': false
          },
        ],
        'spending_policy': 'installments_when_paid',
      };

  ApiResponse? route(ApiRequest request) {
    final path = request.uri.path;
    final method = request.method;
    if (path == '$fin/automation/matching') {
      if (method == 'PUT') approved = true;
      return ok(policyView);
    }
    if (path == '$fin/accounts') {
      return okList([
        {
          'id': 'acc_1',
          'name': 'Example bank',
          'kind': 'bank',
          'currency': 'SAR',
          'archived': false
        }
      ]);
    }
    if (path == '$fin/matching/records') {
      final outcome = request.uri.queryParameters['outcome'];
      final items = records.where((r) => r['outcome'] == outcome).toList();
      return ok({
        'items': items,
        'total': items.length,
        'limit': 25,
        'offset': 0,
        'counts': <String, int>{}
      });
    }
    final decided = RegExp(r'/matching/records/([^/]+)/(accept|reject|undo)$')
        .firstMatch(path);
    if (decided != null) {
      final r = records.firstWhere((r) => r['id'] == decided.group(1));
      r['outcome'] = {
        'accept': 'accepted',
        'reject': 'rejected',
        'undo': 'undone'
      }[decided.group(2)];
      return ok(r);
    }
    if (path == '$fin/matching/run') {
      return ok({
        'counts': {'auto_linked': 1, 'review': 1},
        'policy_version': 'pfm-1.r1'
      });
    }
    if (path == '$fin/matching/backfill' && method == 'GET') {
      return ok({'items': runs.reversed.toList(), 'total': runs.length});
    }
    if (path == '$fin/matching/backfill' && method == 'POST') {
      final run = {
        'id': 'run_${runs.length + 1}',
        'mode': 'apply',
        'status': 'done',
        'start_on': '2025-01-03',
        'end_on': '2026-11-15',
        'cursor_on': '2026-11-30',
        'counts': {'matched': 3, 'auto_linked': 2, 'review': 1},
        'error_code': null,
      };
      runs.add(run);
      return ok(run, 201);
    }
    if (path.endsWith('/undo') && path.contains('/backfill/')) {
      runs.last['status'] = 'undone';
      return ok({'run': runs.last, 'links_removed': 2});
    }
    if (path == '$fin/matching/installments') {
      return ok({
        'today': '2026-11-15',
        'spending_policy': 'installments_when_paid',
        'orders': [
          {
            'provider': 'Tamara',
            'order_id': 'example-order-1',
            'merchant': 'Example shop',
            'currency': 'SAR',
            'still_due': '250.00',
            'next_due_on': '2026-11-15',
            'installments': [
              for (final (n, s) in const [
                (1, 'verified_paid'),
                (2, 'provisional'),
                (3, 'upcoming')
              ])
                {
                  'installment': n,
                  'total_installments': 3,
                  'due_on': '2026-1$n-15',
                  'amount': '125.00',
                  'currency': 'SAR',
                  'status': s
                }
            ],
          }
        ],
      });
    }
    if (path == '$fin/capture-health') {
      return ok({
        'state': 'attention',
        'channels': [
          {
            'channel': 'sms',
            'last_captured_at': '2026-11-15T09:00:00Z',
            'refused_7d': 0,
            'awaiting_review': 1
          },
          {
            'channel': 'wallet',
            'last_captured_at': null,
            'refused_7d': 0,
            'awaiting_review': 0
          },
        ],
        'devices': [
          {
            'name': 'Example phone',
            'status': 'active',
            'last_ok_at': '2026-11-15T09:00:00Z',
            'last_error': null
          }
        ],
        'unknown_senders': [
          {'sender': 'EXAMPLEBANK', 'messages': 1}
        ],
        'mappings': [
          {
            'kind': 'card_number',
            'label': '1234',
            'account_name': 'Example bank'
          }
        ],
        'accounts_without_mapping': <String>[],
        'gaps': <Object>[],
        'problems': ['unknown_senders'],
        'unmapped_wallet_cards': 0,
      });
    }
    return null;
  }

  FinanceMatchingRepository repo() => FinanceMatchingRepository(fake.client());
}

/// Scrolls the page's list until [finder] is built (lists build lazily).
Future<void> reach(WidgetTester tester, Finder finder) => tester
    .scrollUntilVisible(finder, 200, scrollable: find.byType(Scrollable).last);

void main() {
  test('the catalogs match and use no long dashes', () {
    expect(FinanceMatchingStrings.catalogsMatch, isTrue);
    for (final text in FinanceMatchingStrings.allTexts) {
      expect(text.contains(_dash), isFalse, reason: text);
    }
  });

  test('a policy sends back only what the owner may change', () {
    final view = MatchingFake();
    view.approved = true;
    final policy = MatchPolicyView.fromJson(JsonReader(view.policyView)).policy;
    final body = policy.body(approve: false);
    expect(body['expected_revision'], 1);
    expect(body['source_account_ids'], ['acc_1']);
    expect(body.containsKey('approved_at'), isFalse);
    expect(policy.sameAs(policy.copy()), isTrue);
  });

  testWidgets('a waiting match is linked and an automatic one undone',
      (tester) async {
    final fake = MatchingFake();
    await pumpModule(tester, MatchReviewScreen(repository: fake.repo()));
    expect(find.text('Example shop 2/4'), findsOneWidget);
    expect(find.text('It could pay more than one bill or installment'),
        findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('match-accept-a')));
    expect(
        fake.fake.sent('POST', '$fin/matching/records/a/accept'), hasLength(1));
    await clearToasts(tester);
    await tapVisible(tester, find.text('Linked'));
    await tapVisible(tester, find.byKey(const Key('match-undo-c')));
    expect(
        fake.fake.sent('POST', '$fin/matching/records/c/undo'), hasLength(1));
    expectClean(tester);
  });

  testWidgets(
      'the owner approves the policy and applies history, then undoes it',
      (tester) async {
    final fake = MatchingFake();
    await pumpModule(tester, MatchingPolicyScreen(repository: fake.repo()));
    expect(find.textContaining('Not approved yet'), findsOneWidget);
    await reach(tester, find.byKey(const Key('match-approve')));
    await tapVisible(tester, find.byKey(const Key('match-approve')));
    final put = fake.fake.sent('PUT', '$fin/automation/matching').single;
    expect(put.body!['approve'], isTrue);
    await pumpModule(tester, BackfillScreen(repository: fake.repo()));
    await tapVisible(tester, find.byKey(const Key('history-apply')));
    expect(find.text('Linked automatically'), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('history-undo')));
    expect(fake.fake.sent('POST', '$fin/matching/backfill/run_1/undo'),
        hasLength(1));
    expectClean(tester);
  });

  for (final locale in const [Locale('en'), Locale('ar')]) {
    for (final (size, scale) in const [(narrow, 2.0), (tablet, 1.0)]) {
      testWidgets(
          'installments and capture fit at ${size.width} in ${locale.languageCode}',
          (tester) async {
        final fake = MatchingFake();
        await pumpModule(
            tester, InstallmentMatchScreen(repository: fake.repo()),
            size: size, textScale: scale, locale: locale);
        expectClean(tester);
        expect(
            find.textContaining(
                locale.languageCode == 'ar' ? 'مدفوع ومؤكد' : 'Paid, verified'),
            findsOneWidget);
        await pumpModule(tester, CaptureHealthScreen(repository: fake.repo()),
            size: size, textScale: scale, locale: locale);
        expectClean(tester);
        await reach(tester, find.textContaining('EXAMPLEBANK'));
        expect(find.textContaining('EXAMPLEBANK'), findsOneWidget);
        await pumpModule(tester, MatchReviewScreen(repository: fake.repo()),
            size: size, textScale: scale, locale: locale);
        expectClean(tester);
      });
    }
  }

  testWidgets('a refusal says what failed and offers retry', (tester) async {
    final fake = MatchingFake();
    fake.fake.failWith = 403;
    fake.fake.failMessage = 'Finance manage is needed.';
    await pumpModule(tester, MatchReviewScreen(repository: fake.repo()));
    expect(find.byKey(const Key('records-denied')), findsOneWidget);
    expectClean(tester);
  });
}
