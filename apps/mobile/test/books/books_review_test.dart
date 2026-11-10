// The Accountant review, "Evidence missing" badges and attaching a vault
// document to a bill on the phone (docs/business-books.md#accountant-review,
// #supporting-documents-and-evidence), against the books' fake API.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/books/books_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';
import 'books_test.dart';

const base = '/api/v1/business/books';

Map<String, Object?> draftItem({bool canAct = true}) => {
      'key': 'draft_bill:bbill_1',
      'producer': 'bills',
      'kind': 'draft_bill',
      'detected': 'A draft bill waits for approval.',
      'params': {
        'vendor': 'Gulf Office Supplies',
        'reference': 'INV-1',
        'version': '3',
        'due': '2026-11-20'
      },
      'severity': 'blocking',
      'occurred_on': '2026-11-01',
      'amount': '1050.00',
      'currency': 'AED',
      'ref_id': 'bbill_1',
      'section': 'bills',
      'action': 'approve',
      'can_act': canAct,
      'why_not': canAct ? null : 'own_draft',
    };

class ReviewApi extends BooksApi {
  ReviewApi({super.manage = true});
  bool own = false, approved = false, denyReview = false, noVault = false;
  final attached = <Map<String, Object?>>[];

  @override
  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    if (r.method == 'GET' && path == '$base/review') {
      if (denyReview) {
        return refuse(
            403,
            'Only an owner or an accountant of this business can do this.',
            'forbidden');
      }
      final items = approved ? <Object?>[] : [draftItem(canAct: !own)];
      return ok({
        'items': items,
        'total': items.length,
        'limit': 25,
        'offset': 0,
        'counts': {if (!approved) 'draft_bill': 1},
        'blocking': approved ? 0 : 1,
        'close_months': [
          {
            'month': '2026-10',
            'start': '2026-10-01',
            'end': '2026-10-31',
            'status': 'open',
            'issues': 0,
            'drafts': 1,
            'allocations_in_review': 0,
            'ready': false
          }
        ],
        'closed_through': null,
        'to_post': 0,
        'producers': ['bills'],
        'unavailable': <String>[],
      });
    }
    if (r.method == 'POST' && path == '$base/bills/bbill_1/approve') {
      approved = true;
      return ok(billJson('bbill_1'));
    }
    if (r.method == 'GET' && path == '$base/evidence') {
      if (noVault) {
        return refuse(
            403,
            'Reading business evidence needs access to the Document vault.',
            'forbidden');
      }
      final ids = r.uri.queryParameters['ids']!.split(',');
      return ok({
        'items': {
          for (final id in ids)
            id: {
              'documents': id == 'bbill_1' ? attached : <Object?>[],
              'evidence_missing': id == 'bbill_1' && attached.isEmpty,
            }
        },
        'can_attach': true,
      });
    }
    if (r.method == 'GET' && path == '/api/v1/documents') {
      expect(r.uri.queryParameters['access_class'], 'workspace');
      return ok({
        'items': [
          {'id': 'doc_1', 'title': 'Receipt INV-1', 'category': 'receipt'}
        ],
        'next_cursor': null,
      });
    }
    if (r.method == 'POST' && path == '$base/evidence') {
      attached.add({
        'link_id': 'vlink_1',
        'document_id': r.body!['document_id'],
        'title': 'Receipt INV-1',
        'category': 'receipt',
        'added_by': 'user_me',
        'added_at': '2026-11-05T08:00:00Z',
      });
      return ok(attached.last, 201);
    }
    if (r.method == 'DELETE' && path == '$base/evidence/bill/bbill_1/vlink_1') {
      attached.clear();
      return const ApiResponse(statusCode: 204, body: {});
    }
    return super.route(r);
  }
}

void main() {
  test(
      'catalogs still match', () => expect(BooksStrings.catalogsMatch, isTrue));

  testWidgets('an accountant approves a draft from the review', (tester) async {
    final fake = ReviewApi();
    await pumpBooks(tester, fake);
    await tapVisible(tester, find.byKey(const Key('books-tab-review')));
    expect(find.text('Draft bill from Gulf Office Supplies'), findsOneWidget);
    expect(find.byKey(const Key('review-blocking')), findsOneWidget);
    expect(find.byKey(const Key('review-close')), findsOneWidget);
    await tapVisible(
        tester, find.byKey(const Key('review-approve-draft_bill:bbill_1')));
    expect(fake.api.sent('POST', '$base/bills/bbill_1/approve').single.body,
        {'version': 3});
    expect(find.byKey(const Key('review-empty')), findsOneWidget);
    expectClean(tester);
  });

  testWidgets('the maker of a draft cannot approve it', (tester) async {
    final fake = ReviewApi()..own = true;
    await pumpBooks(tester, fake);
    await tapVisible(tester, find.byKey(const Key('books-tab-review')));
    expect(
        find.byKey(const Key('review-own-draft_bill:bbill_1')), findsOneWidget);
    expect(find.byKey(const Key('review-approve-draft_bill:bbill_1')),
        findsNothing);
  });

  testWidgets('a member has no review tab', (tester) async {
    await pumpBooks(tester, ReviewApi(manage: false));
    expect(find.byKey(const Key('books-tab-review')), findsNothing);
  });

  testWidgets('a refused review says who can see it', (tester) async {
    final fake = ReviewApi()..denyReview = true;
    await pumpBooks(tester, fake);
    await tapVisible(tester, find.byKey(const Key('books-tab-review')));
    expect(find.byKey(const Key('records-denied')), findsOneWidget);
  });

  testWidgets('evidence missing badge, attach and remove a vault document',
      (tester) async {
    final fake = ReviewApi();
    await pumpBooks(tester, fake);
    await tapVisible(tester, find.text('Bills'));
    expect(find.byKey(const Key('evidence-missing')), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('bill-bbill_1')));
    expect(find.byKey(const Key('bill-evidence-missing')), findsOneWidget);
    await tapVisible(tester, find.byKey(const Key('evidence-attach')));
    await tapVisible(tester, find.byKey(const Key('pick-doc_1')));
    expect(fake.api.sent('POST', '$base/evidence').single.body, {
      'source_type': 'bill',
      'source_id': 'bbill_1',
      'document_id': 'doc_1',
    });
    expect(find.byKey(const Key('evidence-vlink_1')), findsOneWidget);
    expect(find.byKey(const Key('bill-evidence-missing')), findsNothing);
    await clearToasts(tester);
    await tapVisible(tester, find.byKey(const Key('evidence-remove-vlink_1')));
    expect(fake.api.sent('DELETE', '$base/evidence/bill/bbill_1/vlink_1'),
        hasLength(1));
    expect(find.byKey(const Key('bill-evidence-missing')), findsOneWidget);
  });

  testWidgets('without vault access the bill still opens and says why',
      (tester) async {
    final fake = ReviewApi()..noVault = true;
    await pumpBooks(tester, fake);
    await tapVisible(tester, find.text('Bills'));
    expect(find.byKey(const Key('evidence-missing')), findsNothing);
    await tapVisible(tester, find.byKey(const Key('bill-bbill_1')));
    expect(find.byKey(const Key('evidence-denied')), findsOneWidget);
  });

  for (final size in [phone, const Size(768, 1024)]) {
    for (final locale in [const Locale('en'), const Locale('ar')]) {
      testWidgets('review lays out at $size in ${locale.languageCode}',
          (tester) async {
        await pumpBooks(tester, ReviewApi(), size: size, locale: locale);
        await tapVisible(tester, find.byKey(const Key('books-tab-review')));
        expectClean(tester);
      });
    }
  }
}
