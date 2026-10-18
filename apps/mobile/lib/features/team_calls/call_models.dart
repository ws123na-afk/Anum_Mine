// In-app calls between teammates (docs/in-app-calls.md): the API's shapes.
import 'dart:math';
import 'dart:convert';

import '../../data/json_read.dart';

enum CallMediaKind { audio, video }

enum CallStatus { ringing, accepted, ended }

enum CallMode { off, stunOnly, turn }

/// How the signed-in participant reads a call's outcome.
enum CallWord {
  completed,
  missed,
  noAnswer,
  cancelled,
  declinedByYou,
  declinedByPeer,
  busyPeer,
  failed,
  dropped,
}

class AppCall {
  const AppCall({
    required this.id,
    required this.incoming,
    required this.peerUserId,
    required this.peerName,
    required this.media,
    required this.status,
    required this.outcome,
    required this.createdAt,
    this.answeredAt,
    this.endedAt,
    this.durationSeconds,
    this.onThisDevice,
    this.answeredElsewhere = false,
  });

  final String id;
  final bool incoming;
  final String peerUserId;
  final String peerName;
  final CallMediaKind media;
  final CallStatus status;
  final String? outcome;
  final DateTime createdAt;
  final DateTime? answeredAt;
  final DateTime? endedAt;
  final int? durationSeconds;
  final bool? onThisDevice;
  final bool answeredElsewhere;

  bool get video => media == CallMediaKind.video;

  static AppCall fromJson(JsonReader r) => AppCall(
        id: r.string('id'),
        incoming: r.string('direction') == 'incoming',
        peerUserId: r.string('peer_user_id'),
        peerName: r.string('peer_name'),
        media: r.string('media') == 'video'
            ? CallMediaKind.video
            : CallMediaKind.audio,
        status: switch (r.string('status')) {
          'ringing' => CallStatus.ringing,
          'accepted' => CallStatus.accepted,
          _ => CallStatus.ended,
        },
        outcome: r.optString('outcome'),
        createdAt: r.date('created_at'),
        answeredAt: r.optDate('answered_at'),
        endedAt: r.optDate('ended_at'),
        durationSeconds: r.optInt('duration_seconds'),
        onThisDevice: r.optBool('on_this_device'),
        answeredElsewhere: r.optBool('answered_elsewhere') ?? false,
      );

  /// A cancelled or busy ring is a missed call for the callee.
  CallWord? get word => switch (outcome) {
        null => null,
        'completed' => CallWord.completed,
        'missed' => incoming ? CallWord.missed : CallWord.noAnswer,
        'cancelled' => incoming ? CallWord.missed : CallWord.cancelled,
        'busy' => incoming ? CallWord.missed : CallWord.busyPeer,
        'declined' =>
          incoming ? CallWord.declinedByYou : CallWord.declinedByPeer,
        'failed' => CallWord.failed,
        _ => CallWord.dropped,
      };

  bool get missed => word == CallWord.missed;
}

class CallConfig {
  const CallConfig(
      {required this.mode,
      required this.reason,
      required this.videoEnabled,
      required this.ringTimeoutSeconds});

  final CallMode mode;
  final String? reason;
  final bool videoEnabled;
  final int ringTimeoutSeconds;

  bool get enabled => mode != CallMode.off;

  static CallConfig fromJson(JsonReader r) => CallConfig(
        mode: switch (r.string('mode')) {
          'turn' => CallMode.turn,
          'stun_only' => CallMode.stunOnly,
          _ => CallMode.off,
        },
        reason: r.optString('reason'),
        videoEnabled: r.optBool('video_enabled') ?? true,
        ringTimeoutSeconds: r.optInt('ring_timeout_seconds') ?? 45,
      );
}

class IceServer {
  const IceServer({required this.urls, this.username, this.credential});
  final List<String> urls;
  final String? username;
  final String? credential;

  Map<String, Object> toRtc() => {
        'urls': urls,
        if (username != null) 'username': username!,
        if (credential != null) 'credential': credential!,
      };

  static List<IceServer> listFromJson(JsonReader r) => r.optObjects(
      'ice_servers',
      (server) => IceServer(
          urls: server.strings('urls'),
          username: server.optString('username'),
          credential: server.optString('credential')));
}

/// An offer, answer or ICE batch addressed to this device.
class CallSignal {
  const CallSignal(
      {required this.id,
      required this.callId,
      required this.kind,
      this.sdp,
      this.candidates = const []});

  final String id;
  final String callId;
  final String kind;
  final String? sdp;
  final List<Map<String, Object?>> candidates;

  static CallSignal fromJson(JsonReader r) {
    final payload = r.optObject('payload');
    return CallSignal(
      id: r.string('id'),
      callId: r.string('call_id'),
      kind: r.string('kind'),
      sdp: payload?.optString('sdp'),
      candidates: payload?.optObjects('candidates', (c) => c.json) ?? const [],
    );
  }
}

class CallPerson {
  const CallPerson({required this.userId, required this.name});
  final String userId;
  final String name;
}

class CallPage {
  const CallPage(this.items, this.nextCursor);
  final List<AppCall> items;
  final String? nextCursor;
}

/// A random device id for this app run (base64url, 22 characters).
String newCallClientId([Random? random]) {
  final source = random ?? Random.secure();
  final bytes = List<int>.generate(16, (_) => source.nextInt(256));
  return base64Url.encode(bytes).replaceAll('=', '');
}

/// 0:07, 12:30, 1:02:03.
String formatCallDuration(int totalSeconds) {
  final seconds = totalSeconds < 0 ? 0 : totalSeconds;
  final h = seconds ~/ 3600, m = (seconds % 3600) ~/ 60, s = seconds % 60;
  String pad(int n) => n.toString().padLeft(2, '0');
  return h > 0 ? '$h:${pad(m)}:${pad(s)}' : '$m:${pad(s)}';
}
