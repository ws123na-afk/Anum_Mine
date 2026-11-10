// Lists a screen needs whole are read page by page (docs/mobile.md#long-lists).
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/paging.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/business/invoice_repository.dart';
import 'package:anum_mobile/features/finance/finance_repository.dart';
import 'package:flutter_test/flutter_test.dart';

class PagedTransport implements ApiTransport {
  PagedTransport(this.rows);

  /// Rows by path suffix, served `limit` at a time from `offset`.
  final Map<String, List<Map<String, Object?>>> rows;
  final requests = <Uri>[];

  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request.uri);
    final all =
        rows.entries.firstWhere((e) => request.uri.path.endsWith(e.key)).value;
    final offset = int.parse(request.uri.queryParameters['offset'] ?? '0');
    final limit = int.parse(request.uri.queryParameters['limit']!);
    return ApiResponse(statusCode: 200, body: {
      'items': all.skip(offset).take(limit).toList(),
      'total': all.length,
      'limit': limit,
      'offset': offset,
    });
  }
}

Future<AnumApiClient> client(PagedTransport transport) async {
  final sessions = MemorySessionStore();
  await sessions.write(LocalSession(
      accessToken: 'anum_local_example',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_foundation',
          userId: 'user_owner',
          roles: ['owner'])));
  return AnumApiClient(
      baseUri: Uri.parse('http://127.0.0.1:8000'),
      transport: transport,
      sessions: sessions);
}

void main() {
  test('readAllPages stops at the total, an empty page or the cap', () async {
    final offsets = <int>[];
    final all = await readAllPages<int>((offset) async {
      offsets.add(offset);
      return (
        items: [for (var i = offset; i < offset + 50 && i < 120; i++) i],
        total: 120
      );
    });
    expect(all, hasLength(120));
    expect(offsets, [0, 50, 100]);

    // A total that shrinks while reading cannot loop for ever.
    var calls = 0;
    final short = await readAllPages<int>((offset) async {
      calls++;
      return (items: offset == 0 ? [1, 2] : <int>[], total: 10);
    });
    expect(short, [1, 2]);
    expect(calls, 2);

    final capped = await readAllPages<int>(
        (offset) async => (items: List.filled(50, 0), total: 1000000),
        maxItems: 100);
    expect(capped, hasLength(100));
  });

  test('the invoice form offers every open project, past the first hundred',
      () async {
    final transport = PagedTransport({
      '/business/projects': [
        for (var n = 1; n <= 230; n++)
          {
            'id': 'bprj_$n',
            'name': 'Project $n',
            'status': n == 230 ? 'closed' : 'active',
            'account_id': 'bacc_1',
            'account_name': 'Gulf Mixers LLC',
            'currency': 'SAR',
          }
      ]
    });
    final projects =
        await ApiInvoicesRepository(await client(transport)).projects();
    expect(projects, hasLength(229));
    expect(projects.last.id, 'bprj_229');
    expect(
        [for (final uri in transport.requests) uri.queryParameters['offset']],
        ['0', '100', '200']);
  });

  test('every order waiting for approval is read, past the first fifty',
      () async {
    final transport = PagedTransport({
      '/execution/intents': [
        for (var n = 1; n <= 70; n++)
          <String, Object?>{
            'id': 'intent_$n',
            'mode': 'paper',
            'kind': 'order',
            'status': 'pending_approval',
            'payload_hash': 'ab' * 32,
            'expires_at': '2026-12-01T00:00:00Z',
          }
      ]
    });
    final repository = ApiFinanceRepository(await client(transport));
    final intents = await repository.pendingIntents();
    expect(intents, hasLength(70));
    expect(intents.last.id, 'intent_70');
    expect(
        [for (final uri in transport.requests) uri.queryParameters['offset']],
        ['0', '50']);
  });
}
