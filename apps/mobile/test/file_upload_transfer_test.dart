// HttpWorkspaceFileTransfer.uploadTo (lib/src/infrastructure/mobile_adapters.dart):
// the streamed body ends, so the client finishes sending and the upload
// returns the server's answer instead of hanging at 100%.
import 'dart:convert';
import 'dart:io';

import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/workspace/files_repository.dart';
import 'package:anum_mobile/src/infrastructure/mobile_adapters.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('an upload sends every byte, ends the body and returns the answer',
      () async {
    final dir = await Directory.systemTemp.createTemp('anum_upload');
    addTearDown(() => dir.delete(recursive: true));
    final file = File('${dir.path}/notes.md')..writeAsStringSync('# Notes\n');
    final sessions = MemorySessionStore();
    await sessions.write(LocalSession(
      accessToken: 'anum_local_example',
      tokenType: 'bearer',
      expiresAt: DateTime.now().toUtc().add(const Duration(hours: 1)),
      context: const TenantContext(
          tenantId: 'tenant_local',
          workspaceId: 'workspace_foundation',
          userId: 'user_owner',
          roles: ['owner']),
    ));
    List<int>? received;
    // Answers only once the whole body arrived, as a server does.
    final client = MockClient.streaming((request, body) async {
      received = await body.toBytes();
      return http.StreamedResponse(
          Stream.value(utf8.encode('{"id": "file_1", "name": "notes.md"}')),
          201);
    });
    final transfer = HttpWorkspaceFileTransfer(
        baseUri: Uri.parse('https://api.example/'),
        sessions: sessions,
        client: client,
        uploadClient: () => client);
    final progress = <int>[];

    final answer = await transfer
        .uploadStream(file.path,
            name: 'notes.md',
            onProgress: (sent, _) => progress.add(sent),
            cancellation: UploadCancellation())
        .timeout(const Duration(seconds: 5));

    expect(answer['id'], 'file_1');
    expect(utf8.decode(received!), '# Notes\n');
    expect(progress.last, 8);
  });
}
