// Adjustment approval on the phone (docs/business-books.md
// #adjustment-approval): pending adjustments in the Accountant review with
// Approve and Reject for another owner or accountant, an explanation for
// their maker, and the note on a new adjustment that says whether it waits
// for approval. Against the books' fake API, at 390x844 and 768x1024 in
// English and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/books/books_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';
import 'books_review_test.dart' show ReviewApi, base, draftItem;
import 'books_test.dart';

Map<String, Object?> pendingItem({bool canAct = true}) => {
      'key': 'adjustment_approval:badj_1',
      'producer': 'adjustments',
      'kind': 'adjustment_approval',
      'detected':
          'An adjustment above the approval threshold waits for another owner or accountant.',
      'params': {
        'adjustment_kind': 'accrual',
        'description': 'Office rent for October'
      },
      'severity': 'blocking',
      'occurred_on': '2026-10-31',
      'amount': '4500.00',
      'currency': 'AED',
      'ref_id': 'badj_1',
      'section': 'journal',
      'action': 'approve',
      'can_act': canAct,
      'why_not': canAct ? null : 'own_adjustment',
    };

/// An accountant (not an owner) of books with an approval threshold.
class AdjustmentApi extends ReviewApi {
  AdjustmentApi({this.owner = false, this.threshold = '1000.00'})
      : super(manage: owner);
  final bool owner;
  final String? threshold;
  bool maker = false, decided = false, many = false;

  Map<String, Object?> _adjustment(Map<String, Object?> body, String status) =>
      {
        'id': 'badj_2',
        ...body,
        'status': status,
        'created_by': 'user_me',
      };

  @override
  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    if (r.method == 'GET' && path == '$base/setup') {
      final json = setupJson(manage: owner);
      json['settings'] = <String, Object?>{
        ...json['settings']! as Map<String, Object?>,
        'adjustment_approval_threshold': threshold,
      };
      json['permissions'] = {
        'read': true,
        'write': true,
        'manage': owner,
        'accountant': !owner,
        'account': true,
      };
      json['user_id'] = 'user_me';
      return ok(json);
    }
    if (r.method == 'GET' && path == '$base/review') {
      final offset = int.parse(r.uri.queryParameters['offset'] ?? '0');
      final items = decided
          ? <Object?>[]
          : many
              ? [
                  for (var i = offset; i < (offset == 0 ? 25 : 30); i++)
                    {
                      ...pendingItem(),
                      'key': 'adjustment_approval:badj_$i',
                      'ref_id': 'badj_$i',
                    }
                ]
              : [pendingItem(canAct: !maker), draftItem()];
      return ok({
        'items': items,
        'total': decided ? 0 : (many ? 30 : 2),
        'limit': 25,
        'offset': offset,
        'counts': {if (!decided) 'adjustment_approval': many ? 30 : 1},
        'blocking': decided ? 0 : 1,
        'close_months': <Object?>[],
        'closed_through': null,
        'to_post': 0,
        'producers': ['adjustments'],
        'unavailable': <String>[],
      });
    }
    if (r.method == 'POST' && path == '$base/adjustments/badj_1/approve') {
      decided = true;
      return ok(_adjustment({'kind': 'accrual'}, 'active'));
    }
    if (r.method == 'POST' && path == '$base/adjustments/badj_1/void') {
      decided = true;
      return ok(_adjustment({'kind': 'accrual'}, 'void'));
    }
    if (r.method == 'POST' && path == '$base/adjustments') {
      final amount = double.parse(r.body!['amount']! as String);
      final waits = !owner && threshold != null && amount >= 1000;
      return ok(_adjustment(r.body!, waits ? 'pending' : 'active'), 201);
    }
    return super.route(r);
  }
}

Future<void> openReview(WidgetTester tester, AdjustmentApi fake,
    {Size size = phone, Locale locale = const Locale('en')}) async {
  await pumpBooks(tester, fake, size: size, locale: locale);
  await tapVisible(tester, find.byKey(const Key('books-tab-review')));
}

void main() {
  test('the books catalog still matches',
      () => expect(BooksStrings.catalogsMatch, isTrue));

  testWidgets('another accountant approves a pending adjustment',
      (tester) async {
    final fake = AdjustmentApi();
    await openReview(tester, fake);
    expect(
        find.text('Accrued cost waits for approval: Office rent for October'),
        findsOneWidget);
    expect(find.text('AED 4,500.00'), findsOneWidget);
    await tapVisible(tester,
        find.byKey(const Key('review-approve-adjustment_approval:badj_1')));
    expect(fake.api.sent('POST', '$base/adjustments/badj_1/approve'),
        hasLength(1));
    expect(find.text('Approved the adjustment. It is posted now.'),
        findsOneWidget);
    expect(find.byKey(const Key('review-empty')), findsOneWidget);
    expectClean(tester);
  });

  testWidgets('reject asks for a reason and voids the adjustment',
      (tester) async {
    final fake = AdjustmentApi();
    await openReview(tester, fake);
    await tapVisible(tester,
        find.byKey(const Key('review-reject-adjustment_approval:badj_1')));
    expect(find.text('Reject this adjustment'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('field-reason')), 'no');
    await tapVisible(tester, find.byKey(const Key('form-submit')));
    expect(fake.api.sent('POST', '$base/adjustments/badj_1/void'), isEmpty);
    await tester.enterText(
        find.byKey(const Key('field-reason')), 'Duplicate of a bill');
    await tapVisible(tester, find.byKey(const Key('form-submit')));
    expect(fake.api.sent('POST', '$base/adjustments/badj_1/void').single.body,
        {'reason': 'Duplicate of a bill'});
    expect(find.text('Adjustment rejected. It will not be posted.'),
        findsOneWidget);
    expectClean(tester);
  });

  testWidgets('its maker sees why, not the buttons', (tester) async {
    final fake = AdjustmentApi()..maker = true;
    await openReview(tester, fake);
    expect(find.byKey(const Key('review-own-adjustment_approval:badj_1')),
        findsOneWidget);
    expect(
        find.text('You recorded this adjustment: another owner or an '
            'accountant approves it.'),
        findsOneWidget);
    expect(find.byKey(const Key('review-approve-adjustment_approval:badj_1')),
        findsNothing);
    expect(find.byKey(const Key('review-reject-adjustment_approval:badj_1')),
        findsNothing);
  });

  testWidgets('a refused approval says why and keeps the item', (tester) async {
    final fake = AdjustmentApi();
    await openReview(tester, fake);
    fake.api.failWith = 403;
    fake.api.failMessage =
        'You recorded this adjustment: another owner or an accountant approves it.';
    await tapVisible(tester,
        find.byKey(const Key('review-approve-adjustment_approval:badj_1')));
    expect(find.textContaining('another owner or an accountant approves it'),
        findsWidgets);
    fake.api.failWith = null;
    expect(find.byKey(const Key('review-adjustment_approval:badj_1')),
        findsOneWidget);
  });

  testWidgets('the filter shows only adjustments; long lists page',
      (tester) async {
    final fake = AdjustmentApi()..many = true;
    await openReview(tester, fake);
    await tapVisible(
        tester, find.widgetWithText(ChoiceChip, 'Adjustments to approve (30)'));
    expect(fake.api.requests.last.uri.queryParameters['kind'],
        'adjustment_approval');
    await tapVisible(tester, find.byKey(const Key('records-more')));
    expect(find.byKey(const Key('review-adjustment_approval:badj_29')),
        findsOneWidget);
  });

  testWidgets('an accountant is told an adjustment above the threshold waits',
      (tester) async {
    final fake = AdjustmentApi();
    await openReview(tester, fake);
    await tapVisible(tester, find.byKey(const Key('adjustment-new')));
    expect(
        find.textContaining('Adjustments of AED 1,000.00 or more wait until '
            'another owner or accountant approves them'),
        findsOneWidget);
    await tester.enterText(find.byKey(const Key('field-amount')), '4500');
    await tester.enterText(
        find.byKey(const Key('field-description')), 'Office rent for November');
    await tester.pumpAndSettle();
    await tapVisible(tester, find.byKey(const Key('form-submit')));
    final sent = fake.api.sent('POST', '$base/adjustments').single;
    expect(sent.body, containsPair('kind', 'accrual'));
    expect(sent.body, containsPair('currency', 'AED'));
    expect(sent.body, containsPair('amount', '4500'));
    expect(sent.headers['Idempotency-Key'], isNotEmpty);
    expect(
        find.text('Recorded. It waits for another owner or accountant to '
            'approve it.'),
        findsOneWidget);
    expectClean(tester);
  });

  testWidgets('a spread kind needs its months; an owner posts at once',
      (tester) async {
    final fake = AdjustmentApi(owner: true);
    await openReview(tester, fake);
    await tapVisible(tester, find.byKey(const Key('adjustment-new')));
    expect(
        find.textContaining('As an owner, your adjustment is posted at once'),
        findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('field-kind')));
    await tapVisible(tester, find.text('Prepaid cost').last);
    await tester.enterText(find.byKey(const Key('field-amount')), '1200');
    await tester.enterText(
        find.byKey(const Key('field-description')), 'Insurance for a year');
    await tester.pumpAndSettle();
    await tapVisible(tester, find.byKey(const Key('form-submit')));
    expect(find.text('Give the first month and how many months it covers.'),
        findsOneWidget);
    await tester.enterText(
        find.byKey(const Key('field-start_month')), '2026-11-15');
    await tester.enterText(find.byKey(const Key('field-months')), '12');
    await tester.pumpAndSettle();
    await tapVisible(tester, find.byKey(const Key('form-submit')));
    final sent = fake.api.sent('POST', '$base/adjustments').single.body!;
    expect(sent['start_month'], '2026-11-01');
    expect(sent['months'], 12);
    expect(sent['reverse_on'], isNull);
    expect(find.text('Adjustment recorded and posted.'), findsOneWidget);
  });

  testWidgets('without a threshold nothing waits', (tester) async {
    final fake = AdjustmentApi(threshold: null);
    await openReview(tester, fake);
    await tapVisible(tester, find.byKey(const Key('adjustment-new')));
    expect(
        find.textContaining('No approval step is set, so it is posted at once'),
        findsOneWidget);
  });

  for (final size in const [phone, Size(768, 1024)]) {
    for (final locale in const [Locale('en'), Locale('ar')]) {
      testWidgets(
          'the review and the form lay out at '
          '${size.width.toInt()}x${size.height.toInt()} '
          '(${locale.languageCode})', (tester) async {
        final fake = AdjustmentApi();
        await openReview(tester, fake, size: size, locale: locale);
        expectClean(tester);
        final rtl = locale.languageCode == 'ar';
        expect(
            Directionality.of(tester.element(
                find.byKey(const Key('review-adjustment_approval:badj_1')))),
            rtl ? TextDirection.rtl : TextDirection.ltr);
        await tester.ensureVisible(
            find.byKey(const Key('review-reject-adjustment_approval:badj_1')));
        await tester.pumpAndSettle();
        expectClean(tester);
        await tapVisible(tester, find.byKey(const Key('adjustment-new')));
        expect(find.byKey(const Key('adjustment-form')), findsOneWidget);
        await tester.ensureVisible(find.byKey(const Key('form-submit')));
        await tester.pumpAndSettle();
        expectClean(tester);
      });
    }
  }

  testWidgets('the maker explanation reads in Arabic', (tester) async {
    final fake = AdjustmentApi()..maker = true;
    await openReview(tester, fake, locale: const Locale('ar'));
    expect(find.text('أنت سجلت هذا القيد: يوافق عليه مالك أو محاسب آخر.'),
        findsOneWidget);
    expect(find.text('تكلفة مستحقة ينتظر الموافقة: Office rent for October'),
        findsOneWidget);
  });
}
