// The Document vault on the phone (docs/documents.md#mobile) against a fake
// API with the API's JSON shapes (packages/contracts/src/documents.ts):
// search, category, expiry and trash filters with cursor paging, a document
// with its masked number and expiry badge, revealing the number (owner
// only), adding from the phone's files with progress and the duplicate
// refusal, versions, links, the trash, and every state.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/meetings/meetings_repository.dart';
import 'package:anum_mobile/features/vault/vault_controller.dart';
import 'package:anum_mobile/features/vault/vault_data.dart';
import 'package:anum_mobile/features/vault/vault_screen.dart';
import 'package:anum_mobile/features/vault/vault_strings.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../records/records_harness.dart';

Map<String, Object?> docJson(String id,
        {String title = 'Passport',
        String category = 'passport',
        String state = 'soon',
        int? days = 20,
        String status = 'active',
        bool isOwner = true,
        List<Map<String, Object?>>? links}) =>
    {
      'id': id,
      'title': title,
      'category': category,
      'access_class': 'private',
      'status': status,
      'issue_date': '2021-12-01',
      'expiry_date': '2026-11-25',
      'expiry_state': state,
      'days_left': days,
      'issuer': 'Example Authority',
      'country': 'AE',
      'tags': ['travel'],
      'description': '',
      'number_masked': '•••• 1234',
      'assistant_may_read': false,
      'reminder_days': null,
      'retention_until': null,
      'receipt': null,
      'source': 'web',
      'owner_user_id': 'user_me',
      'is_owner': isOwner,
      'can_edit': true,
      'row_version': 1,
      'created_by': 'user_me',
      'created_at': '2026-11-01T08:00:00Z',
      'updated_by': 'user_me',
      'updated_at': '2026-11-01T08:00:00Z',
      'deleted_at': status == 'deleted' ? '2026-11-04T08:00:00Z' : null,
      'purge_after': status == 'deleted' ? '2026-12-04T08:00:00Z' : null,
      'file': {
        'name': 'passport.pdf',
        'content_type': 'application/pdf',
        'size_bytes': 245760,
        'version': 2,
        'kind': 'pdf',
        'previewable': true,
        'scan_status': 'clean',
      },
      if (links != null) ...{
        'links': links,
        'shared_with': <Object?>[],
        'extractions': <Object?>[],
        'activity': <Object?>[],
        'can_share': true,
        'can_extract': true,
      },
    };

Map<String, Object?> metaJson({bool canWrite = true}) => {
      'summary': {
        'active': 2,
        'private': 2,
        'expiring': 1,
        'expired': 1,
        'review': 0,
        'trash': 1
      },
      'categories': [
        for (final id in ['passport', 'contract', 'receipt', 'other'])
          {
            'id': id,
            'label': null,
            'built_in': true,
            'reads': null,
            'identity': id == 'passport'
          },
        {
          'id': 'vcat_1',
          'label': 'Medical',
          'built_in': false,
          'reads': null,
          'identity': false
        },
      ],
      'settings': {
        'reminder_days': 30,
        'trash_days': 30,
        'ocr_receipts': false,
        'ocr_identity_dates': false,
        'updated_by': null,
        'updated_at': null,
      },
      'ocr_connected': false,
      'can_write': canWrite,
      'can_manage': false,
      'max_upload_bytes': 26214400,
      'accepted': '.pdf,.jpg,.png',
      'accepted_extensions': ['.pdf', '.jpg', '.png'],
      'link_types': [
        'vehicle',
        'person',
        'business',
        'purchase',
        'finance_transaction'
      ],
      'reminder_window_days': 30,
    };

class VaultApi {
  VaultApi({this.empty = false, this.canWrite = true});
  bool empty, canWrite;
  bool trashed = false;
  final links = <Map<String, Object?>>[];
  late final api = FakeApi(route);

  ApiResponse? route(ApiRequest r) {
    final path = r.uri.path;
    final q = r.uri.queryParameters;
    const base = '/api/v1/documents';
    if (r.method == 'GET' && path == '$base/meta') {
      return ok(metaJson(canWrite: canWrite));
    }
    if (r.method == 'GET' && path == base) {
      if (q['status'] == 'deleted') {
        return ok({
          'items':
              trashed ? [docJson('vdoc_1', status: 'deleted')] : <Object?>[],
          'next_cursor': null
        });
      }
      if (empty || q['category'] == 'contract') {
        return ok({'items': <Object?>[], 'next_cursor': null});
      }
      if (q['cursor'] == 'cursor_2') {
        return ok({
          'items': [
            docJson('vdoc_3',
                title: 'Car insurance',
                category: 'other',
                state: 'valid',
                days: 200)
          ],
          'next_cursor': null
        });
      }
      return ok({
        'items': [
          if (!trashed) docJson('vdoc_1'),
          docJson('vdoc_2',
              title: 'Tenancy contract',
              category: 'contract',
              state: 'expired',
              days: -3),
        ],
        'next_cursor': 'cursor_2',
      });
    }
    if (r.method == 'GET' && path == '$base/vdoc_1') {
      return ok(docJson('vdoc_1',
          status: trashed ? 'deleted' : 'active', links: links));
    }
    if (r.method == 'POST' && path == '$base/vdoc_1/number') {
      return ok({'number': 'X1234'});
    }
    if (r.method == 'GET' && path == '$base/vdoc_1/versions') {
      return ok({
        'current_version': 2,
        'current_size_bytes': 245760,
        'total': 1,
        'items': [
          {
            'id': 'fver_1',
            'version': 1,
            'content_type': 'application/pdf',
            'size_bytes': 200000,
            'created_by': 'user_me',
            'created_at': '2021-12-01T08:00:00Z',
            'replaced_by': 'user_me',
            'replaced_at': '2026-11-01T08:00:00Z',
            'downloadable': true,
          }
        ],
      });
    }
    if (r.method == 'POST' && path == '$base/vdoc_1/links') {
      final link = {
        'id': 'vlink_${links.length + 1}',
        'document_id': 'vdoc_1',
        'target_type': r.body!['target_type'],
        'target_id': r.body!['target_id'],
        'label': r.body!['label'],
        'status': 'confirmed',
        'created_by': 'user_me',
        'created_at': '2026-11-05T08:00:00Z',
      };
      links.add(link);
      return ok(link, 201);
    }
    if (r.method == 'DELETE' && path.startsWith('$base/vdoc_1/links/')) {
      links.removeWhere((l) => l['id'] == path.split('/').last);
      return const ApiResponse(statusCode: 204);
    }
    if (r.method == 'DELETE' && path == '$base/vdoc_1') {
      trashed = true;
      return ok(docJson('vdoc_1', status: 'deleted'));
    }
    if (r.method == 'POST' && path == '$base/vdoc_1/restore') {
      trashed = false;
      return ok(docJson('vdoc_1'));
    }
    return null;
  }
}

class FakeUploads implements MeetingUploadTransfer {
  final calls = <(String, Map<String, String>, String)>[];
  bool duplicateOnce = false;

  @override
  Future<JsonMap> uploadTo(
      String endpoint, Map<String, String> query, String path,
      {required void Function(int sent, int total) onProgress,
      required UploadCancellation cancellation}) async {
    calls.add((endpoint, query, path));
    if (duplicateOnce) {
      duplicateOnce = false;
      throw const ApiException(409, 'This file is already in the vault.');
    }
    onProgress(5, 10);
    onProgress(10, 10);
    return docJson('vdoc_9',
        title: query['title'] ?? 'Upload', state: 'none', days: null);
  }
}

class FakeSaver implements VaultFileSaver {
  final saved = <(String, String, String?)>[];
  @override
  Future<void> save(String documentId, String name,
          {String? versionId}) async =>
      saved.add((documentId, name, versionId));
}

Future<(FakeUploads, FakeSaver)> pumpVault(WidgetTester tester, VaultApi fake,
    {Size size = phone,
    double textScale = 1,
    Locale locale = const Locale('en'),
    FakeUploads? uploads}) async {
  final transfer = uploads ?? FakeUploads();
  final saver = FakeSaver();
  final controller = VaultController(
      ApiVaultRepository(fake.api.client(), transfer: transfer, saver: saver));
  addTearDown(controller.dispose);
  await pumpModule(
      tester,
      VaultScreen(
          controller: controller,
          picker: (_) async =>
              ('/tmp/example/receipt.pdf', 'receipt.pdf', 2048)),
      size: size,
      textScale: textScale,
      locale: locale);
  return (transfer, saver);
}

void main() {
  test('catalogs match in English and Arabic', () {
    expect(VaultStrings.catalogsMatch, isTrue);
    expect(const VaultStrings(false).size(245760), '240 KB');
  });

  group('repository', () {
    test('lists a page with its cursor and filters', () async {
      final fake = VaultApi();
      final repo = ApiVaultRepository(fake.api.client());
      final page = await repo.list(query: '1234', expiry: 'soon');
      expect(page.items.first.numberMasked, '•••• 1234');
      expect(page.items.first.expiryState, 'soon');
      expect(page.next, 'cursor_2');
      expect(fake.api.requests.last.uri.queryParameters,
          {'q': '1234', 'expiry': 'soon', 'limit': '20'});
      await repo.list(token: 'cursor_2', trash: true);
      expect(fake.api.requests.last.uri.queryParameters,
          {'status': 'deleted', 'cursor': 'cursor_2', 'limit': '20'});
    });

    test('reads a detail, versions and the revealed number', () async {
      final fake = VaultApi();
      final repo = ApiVaultRepository(fake.api.client());
      final doc = await repo.get('vdoc_1');
      expect(doc.detail, isTrue);
      expect(doc.file!.version, 2);
      expect((await repo.versions('vdoc_1')).items.single.version, 1);
      expect(await repo.revealNumber('vdoc_1'), 'X1234');
    });

    test('an upload sends name, title, category and access as the query',
        () async {
      final uploads = FakeUploads();
      final repo =
          ApiVaultRepository(VaultApi().api.client(), transfer: uploads);
      await repo.upload('/tmp/example/a.pdf',
          name: 'a.pdf',
          title: 'Receipt',
          category: 'receipt',
          accessClass: 'private');
      expect(uploads.calls.single.$1, 'api/v1/documents');
      expect(uploads.calls.single.$2, {
        'name': 'a.pdf',
        'title': 'Receipt',
        'category': 'receipt',
        'access_class': 'private'
      });
    });
  });

  group('screen', () {
    testWidgets('list with badges and paging, then a document', (tester) async {
      final fake = VaultApi();
      final (_, saver) = await pumpVault(tester, fake);
      expect(find.byKey(const Key('vault-summary')), findsOneWidget);
      expect(find.text('Expires in 20 days'), findsOneWidget);
      expect(find.text('Expired 3 days ago'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('records-more')));
      expect(find.text('Car insurance'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('doc-vdoc_1')));
      expect(find.text('•••• 1234'), findsOneWidget);
      expect(find.text('Version 1, 195 KB'), findsOneWidget);
      expect(const VaultStrings(true).days(2), 'يومان');
      expect(const VaultStrings(false).days(1), '1 day');
      await tapVisible(tester, find.byKey(const Key('vault-reveal')));
      expect(find.byKey(const Key('vault-number')), findsOneWidget);
      expect(find.text('X1234'), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      await tapVisible(tester, find.byKey(const Key('vault-download')));
      expect(saver.saved.single, ('vdoc_1', 'passport.pdf', null));
      expectClean(tester);
    });

    testWidgets('adds from the phone\'s files; a duplicate asks first',
        (tester) async {
      final uploads = FakeUploads()..duplicateOnce = true;
      await pumpVault(tester, VaultApi(), uploads: uploads);
      await tester.tap(find.byKey(const Key('vault-add')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('vault-pick')));
      await tester.pumpAndSettle();
      expect(find.text('Chosen: receipt.pdf'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('vault-upload')));
      expect(find.textContaining('already in the vault'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('vault-add-anyway')));
      expect(uploads.calls.last.$2['allow_duplicate'], 'true');
      expect(uploads.calls.last.$2['title'], 'receipt');
      expect(find.text('Document added'), findsOneWidget);
      expectClean(tester);
    });

    testWidgets('adds and removes a link, then moves to the trash and back',
        (tester) async {
      final fake = VaultApi();
      await pumpVault(tester, fake);
      await tapVisible(tester, find.byKey(const Key('doc-vdoc_1')));
      await tapVisible(tester, find.byKey(const Key('vault-add-link')));
      await tester.enterText(find.byKey(const Key('field-target_id')), 'veh_1');
      await tester.enterText(
          find.byKey(const Key('field-label')), 'Family car');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(
          fake.api.sent('POST', '/api/v1/documents/vdoc_1/links').single.body, {
        'target_type': 'vehicle',
        'target_id': 'veh_1',
        'label': 'Family car'
      });
      expect(find.text('Family car'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('link-remove-vlink_1')));
      expect(find.text('Not linked to anything.'), findsOneWidget);
      await clearToasts(tester);

      await tapVisible(tester, find.byKey(const Key('vault-delete')));
      await tester.tap(find.byKey(const Key('confirm-delete')));
      await tester.pumpAndSettle();
      expect(find.textContaining('Removed for good on'), findsWidgets);
      await clearToasts(tester);
      await tapVisible(tester, find.byKey(const Key('vault-restore')));
      expect(fake.api.sent('POST', '/api/v1/documents/vdoc_1/restore'),
          hasLength(1));
      expectClean(tester);
    });

    testWidgets('a category with nothing in it offers to clear the filters',
        (tester) async {
      await pumpVault(tester, VaultApi());
      await tester.tap(find.byKey(const Key('vault-category')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Contract').last);
      await tester.pumpAndSettle();
      expect(find.text('Nothing matches'), findsOneWidget);
      await tapVisible(tester, find.text('Clear filters'));
      expect(find.text('Passport'), findsWidgets);
      expect(find.text('All categories'), findsOneWidget);
      expect(versionName('passport.pdf', 1), 'passport (version 1).pdf');
    });

    testWidgets('empty vault offers to add; an empty trash says so',
        (tester) async {
      await pumpVault(tester, VaultApi(empty: true));
      expect(find.byKey(const Key('vault-empty')), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('vault-trash')));
      expect(find.text('The trash is empty'), findsOneWidget);
    });

    testWidgets('a failed load says what failed', (tester) async {
      final fake = VaultApi();
      fake.api.failWith = 500;
      fake.api.failMessage = 'Object storage is not reachable.';
      await pumpVault(tester, fake);
      expect(find.byKey(const Key('records-error')), findsOneWidget);
      expect(find.textContaining('Object storage is not reachable.'),
          findsOneWidget);
    });

    testWidgets('a viewer is told who can see documents', (tester) async {
      final fake = VaultApi();
      fake.api.failWith = 403;
      fake.api.failMessage =
          'The document vault needs a member or owner role in this workspace. Viewers do not see documents. Ask a workspace owner.';
      await pumpVault(tester, fake);
      expect(find.byKey(const Key('records-denied')), findsOneWidget);
      expect(find.byKey(const Key('vault-add')), findsNothing);
    });

    testWidgets('Arabic, 320 px, 200 percent text, then a tablet',
        (tester) async {
      await pumpVault(tester, VaultApi(),
          size: narrow, textScale: 2, locale: const Locale('ar'));
      expect(find.text('خزنة المستندات'), findsWidgets);
      expectClean(tester);
      await tapVisible(tester, find.byKey(const Key('doc-vdoc_1')));
      expectClean(tester);
      await pumpVault(tester, VaultApi(), size: tablet);
      await tester.tap(find.byKey(const Key('doc-vdoc_1')));
      await tester.pumpAndSettle();
      expect(find.byType(VaultDocumentHost), findsOneWidget);
      expectClean(tester);
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    });
  });
}
