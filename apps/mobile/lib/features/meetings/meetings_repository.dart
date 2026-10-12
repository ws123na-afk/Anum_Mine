import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../workspace/files_repository.dart' show UploadCancellation;
import 'meetings_models.dart';

/// Streams a file on the device to an API path with progress and cancel
/// (the app's HttpWorkspaceFileTransfer; a fake in tests).
abstract interface class MeetingUploadTransfer {
  Future<JsonMap> uploadTo(
    String endpoint,
    Map<String, String> query,
    String path, {
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  });
}

/// Calls & Meetings (docs/meetings.md#mobile).
abstract interface class MeetingsRepository {
  Future<MeetingSetup> setup();
  Future<MeetingPage> list({String query, String status, int offset});
  Future<Meeting> get(String id);
  Future<TranscriptPage> transcript(
    String id, {
    String query,
    String speaker,
    int offset,
  });
  Future<Meeting> importFile({
    required bool recording,
    required String path,
    required String name,
    String title,
    String language,
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  });
  Future<List<ExternalMeeting>> external(String provider);
  Future<Meeting> importExternal(
    String provider,
    String externalId, {
    String language,
  });
  Future<Meeting> reprocess(String id);
  Future<Meeting> setDone(String id, String itemId, bool done);
  Future<Meeting> createTask(String id, String itemId);
  Future<Meeting> saveFollowUp(
    String id,
    List<String> to,
    String subject,
    String body,
  );
  Future<Meeting> sendFollowUp(String id, String accountId);
  Future<void> delete(String id);
}

class ApiMeetingsRepository implements MeetingsRepository {
  const ApiMeetingsRepository(this.api, {this.transfer});
  final AnumApiClient api;
  final MeetingUploadTransfer? transfer;

  String _one(String id, [String rest = '']) =>
      '/api/v1/meetings/${Uri.encodeComponent(id)}$rest';
  Meeting _meeting(JsonMap body) => Meeting.fromJson(JsonReader(body));

  @override
  Future<MeetingSetup> setup() async => MeetingSetup.fromJson(
        JsonReader(await api.request('GET', '/api/v1/meetings/setup')),
      );

  @override
  Future<MeetingPage> list({
    String query = '',
    String status = '',
    int offset = 0,
  }) async {
    final j = JsonReader(
      await api.request(
        'GET',
        meetingsListPath(query: query, status: status, offset: offset),
      ),
    );
    return MeetingPage(
      j.optObjects('items', Meeting.fromJson),
      j.optInt('total') ?? 0,
    );
  }

  @override
  Future<Meeting> get(String id) async =>
      _meeting(await api.request('GET', _one(id)));

  @override
  Future<TranscriptPage> transcript(
    String id, {
    String query = '',
    String speaker = '',
    int offset = 0,
  }) async {
    final params = <String, String>{
      'limit': '$transcriptPageSize',
      'offset': '$offset',
      if (query.trim().isNotEmpty) 'q': query.trim(),
      if (speaker.isNotEmpty) 'speaker': speaker,
    };
    final j = JsonReader(
      await api.request(
        'GET',
        Uri(path: _one(id, '/transcript'), queryParameters: params).toString(),
      ),
    );
    return TranscriptPage(
      j.optObjects('items', TranscriptLine.fromJson),
      j.optInt('total') ?? 0,
      j.optStrings('speakers') ?? const [],
    );
  }

  @override
  Future<Meeting> importFile({
    required bool recording,
    required String path,
    required String name,
    String title = '',
    String language = 'auto',
    required void Function(int sent, int total) onProgress,
    required UploadCancellation cancellation,
  }) async {
    final upload = transfer;
    if (upload == null) {
      throw const ApiException(501, 'Uploads are not available in this build.');
    }
    final body = await upload.uploadTo(
      recording ? 'api/v1/meetings/recordings' : 'api/v1/meetings/transcripts',
      {
        'name': name,
        if (title.trim().isNotEmpty) 'title': title.trim(),
        if (language != 'auto') 'language': language,
      },
      path,
      onProgress: onProgress,
      cancellation: cancellation,
    );
    return _meeting(body);
  }

  @override
  Future<List<ExternalMeeting>> external(String provider) async => JsonReader(
        await api.request(
            'GET', '/api/v1/meetings/connectors/$provider/meetings'),
      ).optObjects('data', ExternalMeeting.fromJson);

  @override
  Future<Meeting> importExternal(
    String provider,
    String externalId, {
    String language = 'auto',
  }) async =>
      _meeting(
        await api.request(
          'POST',
          '/api/v1/meetings/connectors/$provider/import',
          body: {'external_id': externalId, 'language': language},
        ),
      );

  @override
  Future<Meeting> reprocess(String id) async =>
      _meeting(await api.request('POST', _one(id, '/process')));

  @override
  Future<Meeting> setDone(String id, String itemId, bool done) async =>
      _meeting(
        await api.request(
          'PATCH',
          _one(id, '/action-items/${Uri.encodeComponent(itemId)}'),
          body: {'done': done},
        ),
      );

  @override
  Future<Meeting> createTask(String id, String itemId) async => _meeting(
        await api.request(
          'POST',
          _one(id, '/action-items/${Uri.encodeComponent(itemId)}/task'),
        ),
      );

  @override
  Future<Meeting> saveFollowUp(
    String id,
    List<String> to,
    String subject,
    String body,
  ) async =>
      _meeting(
        await api.request(
          'PUT',
          _one(id, '/follow-up'),
          body: {'to': to, 'subject': subject, 'body': body},
        ),
      );

  @override
  Future<Meeting> sendFollowUp(String id, String accountId) async =>
      _meeting(await api.request('POST', _one(id, '/follow-up/send'),
          body: {'account_id': accountId}));

  @override
  Future<void> delete(String id) async {
    await api.request('DELETE', _one(id));
  }
}
