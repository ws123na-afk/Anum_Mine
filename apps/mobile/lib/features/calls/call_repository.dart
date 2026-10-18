// In-app calls over the live API (docs/in-app-calls.md#api). The server decides
// who may ring whom (same workspace only) and who sends the offer.
import 'dart:async';

import 'package:http/http.dart' as http;

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';
import '../../data/sse.dart';
import 'call_models.dart';

/// One event of this device's call stream.
sealed class CallStreamEvent {
  const CallStreamEvent();
}

class CallChanged extends CallStreamEvent {
  const CallChanged(this.call);
  final AppCall call;
}

class SignalReceived extends CallStreamEvent {
  const SignalReceived(this.signal);
  final CallSignal signal;
}

abstract interface class CallRepository {
  Future<CallConfig> config();
  Future<List<IceServer>> iceServers();
  Future<List<CallPerson>> people();
  Future<AppCall> place(
      String calleeUserId, CallMediaKind media, String clientId);
  Future<AppCall> accept(String callId, String clientId);
  Future<AppCall> end(String callId, String reason);
  Future<void> heartbeat(String callId);
  Future<void> sendDescription(String callId, String kind, String sdp);
  Future<void> sendCandidates(
      String callId, List<Map<String, Object?>> candidates);
  Future<CallPage> history({String? cursor, bool missed = false});

  /// `GET /api/v1/calls/stream?client_id=`: ends when the connection does.
  Stream<CallStreamEvent> stream(String clientId);
}

class ApiCallRepository implements CallRepository {
  ApiCallRepository(this.api,
      {required this.sessions, http.Client Function()? clientFactory})
      : _newClient = clientFactory ?? http.Client.new;

  final AnumApiClient api;
  final SessionStore sessions;
  final http.Client Function() _newClient;

  static const _base = '/api/v1/calls';
  String _id(String value) => Uri.encodeComponent(value);

  @override
  Future<CallConfig> config() async => CallConfig.fromJson(
      JsonReader(await api.request('GET', '$_base/config')));

  @override
  Future<List<IceServer>> iceServers() async => IceServer.listFromJson(
      JsonReader(await api.request('GET', '$_base/ice-servers')));

  @override
  Future<List<CallPerson>> people() async {
    final body = await api.request('GET', '/api/v1/team/people');
    final list = body['data'];
    return [
      if (list is List)
        for (final item in list)
          if (item is Map)
            () {
              final r = JsonReader(jsonObject(item));
              return CallPerson(
                  userId: r.string('user_id'), name: r.string('display_name'));
            }(),
    ];
  }

  @override
  Future<AppCall> place(
          String calleeUserId, CallMediaKind media, String clientId) async =>
      AppCall.fromJson(JsonReader(await api.request('POST', _base, body: {
        'callee_user_id': calleeUserId,
        'media': media.name,
        'client_id': clientId,
      })));

  @override
  Future<AppCall> accept(String callId, String clientId) async =>
      AppCall.fromJson(JsonReader(await api.request(
          'POST', '$_base/${_id(callId)}/accept',
          body: {'client_id': clientId})));

  @override
  Future<AppCall> end(String callId, String reason) async => AppCall.fromJson(
      JsonReader(await api.request('POST', '$_base/${_id(callId)}/end',
          body: {'reason': reason})));

  @override
  Future<void> heartbeat(String callId) async {
    await api.request('POST', '$_base/${_id(callId)}/heartbeat');
  }

  @override
  Future<void> sendDescription(String callId, String kind, String sdp) async {
    await api.request('POST', '$_base/${_id(callId)}/signals',
        body: {'kind': kind, 'sdp': sdp});
  }

  @override
  Future<void> sendCandidates(
      String callId, List<Map<String, Object?>> candidates) async {
    await api.request('POST', '$_base/${_id(callId)}/signals',
        body: {'kind': 'ice', 'candidates': candidates});
  }

  @override
  Future<CallPage> history({String? cursor, bool missed = false}) async {
    final query = <String>['limit=20'];
    if (cursor != null) query.add('cursor=${_id(cursor)}');
    if (missed) query.add('missed=true');
    final r = JsonReader(await api.request('GET', '$_base?${query.join('&')}'));
    return CallPage(
        r.objects('items', AppCall.fromJson), r.optString('next_cursor'));
  }

  @override
  Stream<CallStreamEvent> stream(String clientId) {
    late final StreamController<CallStreamEvent> output;
    http.Client? client;
    StreamSubscription<SseEvent>? subscription;

    Future<void> start() async {
      try {
        final session = await sessions.read();
        if (session == null || session.isExpired) {
          throw const ApiException(401, 'Authentication required');
        }
        final current = client = _newClient();
        final uri = api.baseUri
            .resolve('api/v1/calls/stream?client_id=${_id(clientId)}');
        final request = http.Request('GET', uri)
          ..headers.addAll(
              {...sessionHeaders(session), 'accept': 'text/event-stream'});
        final response =
            await current.send(request).timeout(const Duration(seconds: 30));
        if (response.statusCode < 200 || response.statusCode >= 300) {
          throw ApiException(response.statusCode, 'The call stream refused');
        }
        // The server sends a keep-alive every 15 s; 45 s of silence is a dead link.
        subscription = decodeSse(response.stream)
            .timeout(const Duration(seconds: 45))
            .listen((event) {
          final fields = event.fields;
          if (fields.isEmpty) return;
          try {
            if (event.event == 'call') {
              output.add(CallChanged(AppCall.fromJson(JsonReader(fields))));
            } else if (event.event == 'signal') {
              output
                  .add(SignalReceived(CallSignal.fromJson(JsonReader(fields))));
            }
          } on FormatException {
            // A frame this version does not understand.
          }
        }, onError: (Object error, StackTrace stack) {
          if (!output.isClosed) output.addError(error, stack);
          client?.close();
          if (!output.isClosed) output.close();
        }, onDone: () {
          client?.close();
          if (!output.isClosed) output.close();
        }, cancelOnError: true);
      } on Object catch (error, stack) {
        client?.close();
        if (!output.isClosed) {
          output.addError(error, stack);
          await output.close();
        }
      }
    }

    output = StreamController<CallStreamEvent>(
      onListen: () => unawaited(start()),
      onCancel: () async {
        client?.close();
        await subscription?.cancel();
      },
    );
    return output.stream;
  }
}
