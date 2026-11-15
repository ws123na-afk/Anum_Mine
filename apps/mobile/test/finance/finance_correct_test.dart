// A transaction's corrections on the phone, the same as the web drawer
// (docs/finance.md#transaction-detail): type, subcategory, "Use this for
// later payments", "Remember this category", Apply to similar with the
// preview and Undo, the AI category, money in corrected to money out, a
// duplicate copy deleted and a receipt sent to the vault. Every fixture is
// plainly fake.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/finance/finance_core_repository.dart';
import 'package:anum_mobile/features/finance/finance_core_view.dart';
import 'package:anum_mobile/features/finance/finance_correct_strings.dart';
import 'package:anum_mobile/features/meetings/meetings_repository.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart'
    show UploadCancellation;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> row(
        {String origin = 'statement',
        String amount = '-42.00',
        String type = 'expense',
        String categorySource = 'rule',
        String category = 'dining',
        String description = 'EXAMPLE CAFE RIYADH'}) =>
    {
      'id': 'txn_1',
      'account_id': 'acc_current',
      'posted_on': '2026-11-14',
      'amount': amount,
      'currency': 'SAR',
      'description': description,
      'category': category,
      'category_source': categorySource,
      'subcategory_id': null,
      'transaction_type': type,
      'type_source': 'rule',
      'canonical_id': 'ct_1',
      'origin': origin,
      'sources': [origin],
      'status': 'recorded',
      'reconciled': false,
      'receipts': 0,
      'links_need_review': 0,
    };

Map<String, Object?> preview(int changes) => {
      'matched': changes + 1,
      'changes': changes,
      'unchanged': 1,
      'manual_kept': 0,
      'sms_kept': 0,
      'truncated': false,
      'preview_hash': 'hash_$changes',
      'rows': [
        for (var i = 0; i < changes; i++)
          {
            'transaction_id': 'txn_${i + 2}',
            'posted_on': '2026-10-0${i + 1}',
            'description': 'EXAMPLE CAFE RIYADH',
            'amount': '-30.00',
            'currency': 'SAR',
            'before_category': 'other',
            'after_category': 'dining',
            'before_type': 'expense',
            'after_type': 'expense',
          }
      ],
    };

const content = {
  'match': {
    'merchant': 'EXAMPLE CAFE',
    'words': <String>[],
    'direction': 'out'
  },
  'category': 'dining',
  'subcategory_id': null,
  'transaction_type': null,
  'priority': 100,
  'enabled': true,
  'auto_apply': false,
  'note': null,
};

class Api {
  Api({Map<String, Object?>? detailRow, this.duplicate = false}) {
    current = detailRow ?? row();
    fake = FakeApi(route);
  }
  late final FakeApi fake;
  late Map<String, Object?> current;
  final bool duplicate;
  final patches = <Map<String, Object?>>[];

  FinanceCoreRepository repo({MeetingUploadTransfer? uploads}) =>
      FinanceCoreRepository(fake.client(), uploads: uploads);

  ApiResponse? route(ApiRequest r) {
    if (r.method == 'POST' && r.uri.path.endsWith('/doc_1/links')) {
      return ok({
        'id': 'link_1',
        'target_type': 'finance_transaction',
        'target_id': 'ct_1',
        'label': 'EXAMPLE CAFE RIYADH'
      });
    }
    final path = r.uri.path.replaceFirst('/api/v1/finance', '');
    final body = (r.body as Map?)?.cast<String, Object?>() ?? const {};
    if (r.method == 'GET' && path == '/register/txn_1') {
      return ok({
        'row': current,
        'account_name': 'Example Current',
        'kind': null,
        'merchant': 'EXAMPLE CAFE',
        'reference': null,
        'suggested_category': 'dining',
        'suggested_by': 'rule',
        'category_by': 'rule',
        'sources': [],
        'receipt_document_ids': [],
        'duplicate': duplicate
            ? {...row(), 'id': 'txn_0', 'posted_on': '2026-11-13'}
            : null,
        'links': [],
        'related': [],
        'split_count': 0,
      });
    }
    if (r.method == 'GET' && path == '/categories') {
      return ok({
        'groups': [
          {
            'id': 'living',
            'name': 'Living',
            'name_ar': 'المعيشة',
            'categories': [
              {
                'id': 'dining',
                'name': 'Dining',
                'name_ar': 'مطاعم',
                'subcategories': [
                  {
                    'id': 'dining.coffee',
                    'name': 'Coffee',
                    'name_ar': 'قهوة',
                    'built_in': true
                  }
                ]
              }
            ]
          }
        ]
      });
    }
    if (r.method == 'GET' && path == '/subcategories') {
      return okList([
        {
          'id': 'sub_1',
          'parent': 'dining',
          'name': 'Team lunches',
          'archived': false
        }
      ]);
    }
    if (r.method == 'PATCH' && path == '/transactions/txn_1') {
      patches.add(body);
      current = {
        ...current,
        if (body['category'] != null) 'category': body['category'],
        if (body['transaction_type'] != null)
          'transaction_type': body['transaction_type'],
        if (body['amount'] != null) 'amount': body['amount'],
        'category_source': 'manual',
      };
      return ok({});
    }
    if (r.method == 'DELETE' && path == '/transactions/txn_1') {
      return const ApiResponse(statusCode: 204, body: null);
    }
    if (r.method == 'POST' && path == '/transactions/txn_1/similar') {
      return ok({
        'content': content,
        'preview': preview(body['scope'] == 'account' ? 1 : 2),
        'source_transaction_id': 'txn_1',
      });
    }
    if (r.method == 'POST' && path == '/transactions/txn_1/ai-suggestion') {
      return ok({
        'category': 'groceries',
        'transaction_type': 'expense',
        'note': 'Not saved.'
      });
    }
    if (r.method == 'POST' && path == '/rules') {
      return ok(version('proposed'));
    }
    if (r.method == 'POST' && path == '/rules/rule_1/approve') {
      expect(body, {'version': 1, 'payload_hash': 'ph_1'});
      return ok(version('approved'));
    }
    if (r.method == 'POST' && path == '/rules/rule_1/preview') {
      return ok(preview(2));
    }
    if (r.method == 'POST' && path == '/rules/rule_1/apply') {
      expect(body, {'preview_hash': 'hash_2'});
      return ok({
        'id': 'batch_1',
        'origin': 'owner',
        'changed': 2,
        'status': 'applied',
        'created_at': '2026-11-15T08:00:00Z'
      });
    }
    if (r.method == 'POST' && path == '/rule-batches/batch_1/undo') {
      return ok({'restored': 2, 'kept': 0});
    }
    return null;
  }

  Map<String, Object?> version(String status) => {
        'rule_id': 'rule_1',
        'version': 1,
        'status': status,
        'content': content,
        'provenance': 'owner',
        'payload_hash': 'ph_1',
        'created_at': '2026-11-15T08:00:00Z',
        'source_transaction_id': 'txn_1',
      };
}

class FakeUploads implements MeetingUploadTransfer {
  final uploads = <(String, Map<String, String>, String)>[];
  @override
  Future<JsonMap> uploadTo(
      String endpoint, Map<String, String> query, String path,
      {required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation}) async {
    uploads.add((endpoint, query, path));
    return {'id': 'doc_1', 'title': 'receipt', 'category': 'receipt'};
  }
}

Future<void> open(WidgetTester tester, FinanceCoreRepository repo,
    {Locale locale = const Locale('en'),
    Size size = phone,
    VoidCallback? onDeleted}) async {
  await pumpModule(
      tester,
      Scaffold(
          body: ListView(children: [
        TransactionEvidenceSection(
            repository: repo,
            transactionId: 'txn_1',
            onDeleted: onDeleted,
            pickFile: (_) async => ('/tmp/example.pdf', 'example.pdf', 10))
      ])),
      locale: locale,
      size: size);
  await tester.pumpAndSettle();
}

Future<void> tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Future<void> choose(WidgetTester tester, Key field, String option) async {
  await tap(tester, find.byKey(field));
  await tester.tap(find.text(option).last);
  await tester.pumpAndSettle();
}

void main() {
  test('the catalogs have the same keys and no long dashes', () {
    expect(FinanceCorrectStrings.catalogsMatch, isTrue);
    for (final text in FinanceCorrectStrings.allTexts) {
      expect(text.contains('—') || text.contains('–'), isFalse, reason: text);
    }
  });

  testWidgets(
      'type, subcategory and "use this for later payments" are saved together',
      (tester) async {
    final api = Api();
    await open(tester, api.repo());
    expect(find.byKey(const Key('tx-correct')), findsOneWidget);
    expect(find.textContaining('Type: Spending'), findsOneWidget);
    await choose(tester, const Key('tx-type'), 'Reimbursement');
    await choose(tester, const ValueKey('tx-subcategory-dining'), 'Coffee');
    expect(find.text('Save'), findsOneWidget);
    await tap(tester, find.byKey(const Key('tx-category-save')));
    expect(api.patches.single, {
      'transaction_type': 'reimbursement',
      'subcategory_id': 'dining.coffee',
      'learn_merchant': true,
    });
    expect(find.text('Saved as Reimbursement.'), findsOneWidget);
  });

  testWidgets('"Remember this category" checks the phrase, then saves it',
      (tester) async {
    final api = Api();
    await open(tester, api.repo());
    await choose(tester, const Key('tx-category'), 'Food and meals');
    await tap(tester, find.byKey(const Key('tx-remember')));
    // The merchant the description holds is offered; learning a merchant is
    // then left out, as on the web.
    expect(find.byKey(const Key('tx-learn')), findsNothing);
    await tester.enterText(
        find.byKey(const Key('tx-remember-phrase')), 'EXAMPLE BAKERY');
    await tap(tester, find.byKey(const Key('tx-category-save')));
    expect(api.patches, isEmpty);
    expect(
        find.textContaining('must appear in this transaction'), findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('tx-remember-phrase')), 'example cafe');
    await tap(tester, find.byKey(const Key('tx-category-save')));
    expect(api.patches.single, {
      'category': 'food',
      'remember_category_match': 'example cafe',
    });
    expect(find.textContaining('Saved as Food and meals'), findsOneWidget);
  });

  testWidgets('apply to similar previews, saves, applies and undoes',
      (tester) async {
    final api = Api(detailRow: row(categorySource: 'manual'));
    await open(tester, api.repo());
    await tap(tester, find.byKey(const Key('tx-similar')));
    expect(find.byKey(const Key('similar-rule')), findsOneWidget);
    expect(find.textContaining('EXAMPLE CAFE, money out, files as Dining'),
        findsOneWidget);
    expect(find.byKey(const Key('rule-preview-txn_2')), findsOneWidget);
    await tap(tester, find.text('Only this account'));
    expect(
        api.fake
            .sent('POST', '/api/v1/finance/transactions/txn_1/similar')
            .last
            .body,
        {'scope': 'account'});
    expect(find.text('Save rule and apply to 1 row'), findsOneWidget);
    await tap(tester, find.text('Every account'));
    await tap(tester, find.byKey(const Key('similar-apply')));
    expect(find.byKey(const Key('similar-rule')), findsNothing);
    expect(find.textContaining('Changed 2 rows'), findsOneWidget);
    await tap(tester, find.byKey(const Key('tx-undo-similar')));
    expect(api.fake.sent('POST', '/api/v1/finance/rule-batches/batch_1/undo'),
        hasLength(1));
    expect(find.text('Put back 2 rows.'), findsOneWidget);
    expect(find.byKey(const Key('tx-undo-similar')), findsNothing);
  });

  testWidgets('a failed preview says why and retries', (tester) async {
    final api = Api(detailRow: row(categorySource: 'manual'));
    await open(tester, api.repo());
    api.fake.failWith = 500;
    await tap(tester, find.byKey(const Key('tx-similar')));
    expect(find.byKey(const Key('similar-error')), findsOneWidget);
    api.fake.failWith = null;
    await tap(tester, find.text('Retry'));
    expect(find.byKey(const Key('similar-rule')), findsOneWidget);
  });

  testWidgets('the AI category is shown and saved only when chosen',
      (tester) async {
    final api = Api(detailRow: row(type: 'uncategorized'));
    await open(tester, api.repo());
    expect(find.byKey(const Key('tx-uncategorized')), findsOneWidget);
    await tap(tester, find.byKey(const Key('tx-ask-ai')));
    expect(find.textContaining('AI suggests Groceries'), findsOneWidget);
    expect(api.patches, isEmpty);
    await tap(tester, find.byKey(const Key('tx-category-save')));
    expect(api.patches.single['category'], 'groceries');
    expect(api.patches.single['from_ai_suggestion'], isTrue);
  });

  testWidgets('money in is corrected to money out after a confirmation',
      (tester) async {
    final api = Api(
        detailRow: row(
            amount: '1500.00',
            type: 'income',
            category: 'income',
            description: 'EXAMPLE ROOM RENT NOVEMBER'));
    await open(tester, api.repo());
    expect(find.byKey(const Key('tx-direction')), findsOneWidget);
    await tap(tester, find.byKey(const Key('tx-money-out')));
    expect(find.textContaining('Choose a spending category'), findsOneWidget);
    expect(api.patches, isEmpty);
    await choose(tester, const Key('tx-category'), 'Fees');
    await tap(tester, find.byKey(const Key('tx-money-out')));
    await tap(tester, find.byKey(const Key('records-confirm')));
    expect(api.patches.single, {'amount': '-1500.00', 'category': 'fees'});
    expect(find.textContaining('Corrected to money out'), findsOneWidget);
  });

  testWidgets('a duplicate copy is deleted after a confirmation',
      (tester) async {
    final api = Api(duplicate: true);
    var closed = false;
    await open(tester, api.repo(), onDeleted: () => closed = true);
    expect(find.byKey(const Key('tx-duplicate')), findsOneWidget);
    await tap(tester, find.byKey(const Key('tx-delete-copy')));
    await tap(tester, find.byKey(const Key('records-confirm')));
    expect(api.fake.sent('DELETE', '/api/v1/finance/transactions/txn_1'),
        hasLength(1));
    expect(closed, isTrue);
  });

  testWidgets('a receipt goes to the vault and is linked to the payment',
      (tester) async {
    final api = Api();
    final uploads = FakeUploads();
    await open(tester, api.repo(uploads: uploads));
    await tap(tester, find.byKey(const Key('tx-attach')));
    expect(uploads.uploads.single.$2['category'], 'receipt');
    expect(uploads.uploads.single.$2['access_class'], 'private');
    final link = api.fake.requests.firstWhere(
        (r) => r.method == 'POST' && r.uri.path.endsWith('/doc_1/links'));
    expect(link.body, {
      'target_type': 'finance_transaction',
      'target_id': 'ct_1',
      'label': 'EXAMPLE CAFE RIYADH'
    });
    expect(find.textContaining('Receipt saved in your vault'), findsOneWidget);
  });

  testWidgets('an SMS row is corrected under iPhone SMS, not here',
      (tester) async {
    final api = Api(detailRow: row(origin: 'sms'));
    await open(tester, api.repo());
    expect(find.byKey(const Key('tx-sms-origin')), findsOneWidget);
    expect(find.byKey(const Key('tx-category-save')), findsNothing);
    expect(find.byKey(const Key('tx-similar')), findsNothing);
  });

  for (final (name, size) in [('phone', phone), ('narrow', narrow)]) {
    testWidgets('Arabic, right to left, fits: $name', (tester) async {
      final api =
          Api(detailRow: row(categorySource: 'manual'), duplicate: true);
      await open(tester, api.repo(uploads: FakeUploads()),
          locale: const Locale('ar'), size: size);
      expect(tester.takeException(), isNull);
      expect(find.text('ما هذه المعاملة؟'), findsOneWidget);
      expect(find.text('تطبيق على المماثل'), findsOneWidget);
      expect(
          Directionality.of(
              tester.element(find.byKey(const Key('tx-correct')))),
          TextDirection.rtl);
    });
  }
}
