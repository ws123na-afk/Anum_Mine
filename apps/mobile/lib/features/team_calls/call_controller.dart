// The app-wide state of in-app calls (docs/in-app-calls.md#flutter): this
// phone's call stream (reconnecting with backoff), the incoming ring, the call
// in progress with its timer and heartbeats, and what ended it.
//
// Group calls (docs/in-app-calls.md#group-calls): the call that rang or was
// placed stays [call]; every other person of the group is a [CallLink] with
// its own connection that shares this phone's microphone and camera (a mesh of
// up to four people). Hanging up leaves the group; the others stay connected.
import '../../data/readable_error.dart';
import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'call_media.dart';
import 'call_models.dart';
import 'call_repository.dart';
import 'system_call_screen.dart';

enum CallConfigPhase { loading, ready, denied, error }

enum TeamCallPhase {
  idle,
  preparing,
  ringing,
  incoming,
  connecting,
  connected,
  reconnecting,
  ended
}

/// A message for the person; [key] is a [TeamCallText] key.
class CallNotice {
  const CallNotice(this.key, [this.values = const {}]);
  final String key;
  final Map<String, Object> values;
}

/// Another person of a group call: their pair call and its connection.
class CallLink {
  CallLink(this.call);
  AppCall call;
  CallMediaSession? session;
  CallLinkState state = CallLinkState.connecting;
  bool offered = false;

  bool get live => call.status != CallStatus.ended;
}

class AppCallsController extends ChangeNotifier {
  AppCallsController({
    required this.repository,
    required this.mediaFactory,
    String? clientId,
    this.heartbeatEvery = const Duration(seconds: 15),
    this.systemCallScreen = const NoSystemCallScreen(),
  }) : clientId = clientId ?? newCallClientId();

  /// The phone's own call screen (CallKit / ConnectionService) for rings that
  /// arrive while the app is in the background; none is linked yet.
  final SystemCallScreen systemCallScreen;

  /// Whether the app is on screen (from the app's lifecycle).
  bool foreground = true;
  StreamSubscription<SystemCallAction>? _systemActions;

  void setForeground(bool value) => foreground = value;

  void _onSystemAction(SystemCallAction action) {
    final current = call;
    if (current == null || current.id != action.callId) return;
    switch (action) {
      case SystemCallAccepted():
        if (phase == TeamCallPhase.incoming) unawaited(answer());
      case SystemCallDeclined():
        if (phase == TeamCallPhase.incoming) unawaited(decline());
      case SystemCallEnded():
        if (busy) unawaited(hangUp());
    }
  }

  void _systemDismiss(String id) {
    if (systemCallScreen.available) {
      unawaited(systemCallScreen.dismiss(id).catchError((Object _) {}));
    }
  }

  final CallRepository repository;
  final CallMediaFactory mediaFactory;
  final String clientId;
  final Duration heartbeatEvery;

  CallConfigPhase configPhase = CallConfigPhase.loading;
  CallConfig? config;
  String? configError;
  bool online = true;

  /// The signed-in person, left out of the People list.
  String? selfUserId;

  TeamCallPhase phase = TeamCallPhase.idle;
  AppCall? call;
  CallPerson? peer;
  CallMediaKind media = CallMediaKind.audio;
  CallMediaSession? session;
  CallNotice? notice;
  bool muted = false;
  bool speaker = true;
  bool camera = true;

  /// Bumped when a call starts or ends, so the history reloads.
  int revision = 0;

  /// The group call this call is part of, once the stream has described it.
  CallRoom? room;

  /// The other people of the group call (besides [call]'s peer), by call id.
  final Map<String, CallLink> links = {};

  /// The group call ended for this phone (the ended screen says "Group call").
  bool endedGroup = false;

  bool _primaryEnded = false;
  final Map<String, CallRoom> _rooms = {};

  String? get roomId => room?.id ?? call?.roomId;
  bool get group => roomId != null;

  /// The live connections of the call: [call] (until it ends in a group) and
  /// the other people of the group.
  List<AppCall> get liveCalls => [
        if (call != null && !_primaryEnded && call!.status != CallStatus.ended)
          call!,
        for (final link in links.values)
          if (link.live) link.call,
      ];

  /// How many more people can be added now.
  int get freeSlots =>
      room?.freeSlots ?? (4 - 1 - liveCalls.length).clamp(0, 3);

  /// Whether video can be turned on in this call now.
  bool get canTurnOnVideo =>
      (config?.videoEnabled ?? false) &&
      phase == TeamCallPhase.connected &&
      media != CallMediaKind.video;

  bool _offered = false;
  bool _running = false;
  int _failures = 0;
  StreamSubscription<CallStreamEvent>? _subscription;
  Timer? _reconnect;
  Timer? _heartbeat;
  Timer? _ticker;
  final Map<String, List<CallSignal>> _queued = {};

  /// Seconds since the call was answered (the timer).
  int get elapsed {
    final answered = call?.answeredAt;
    if (answered == null) return 0;
    final seconds = DateTime.now().difference(answered).inSeconds;
    return seconds < 0 ? 0 : seconds;
  }

  /// Why calls cannot be placed now, as a [TeamCallText] key, or null.
  String? get unavailableKey {
    switch (configPhase) {
      case CallConfigPhase.loading:
        return null;
      case CallConfigPhase.denied:
        return 'denied';
      case CallConfigPhase.error:
        return 'configFailed';
      case CallConfigPhase.ready:
        if (!(config?.enabled ?? false)) return 'off';
        if (!online) return 'offline';
        return null;
    }
  }

  bool get busy => phase != TeamCallPhase.idle && phase != TeamCallPhase.ended;

  /// Signed in: read the server's call mode and, when calls are on, listen.
  Future<void> start() async {
    if (_running) return;
    _running = true;
    _systemActions ??= systemCallScreen.actions.listen(_onSystemAction);
    configPhase = CallConfigPhase.loading;
    notifyListeners();
    try {
      config = await repository.config();
      configPhase = CallConfigPhase.ready;
    } on ApiException catch (error) {
      configPhase = error.statusCode == 403
          ? CallConfigPhase.denied
          : CallConfigPhase.error;
      configError = error.message;
    } on Object catch (error) {
      configPhase = CallConfigPhase.error;
      configError = readableError(error);
    }
    notifyListeners();
    if (_running && (config?.enabled ?? false)) _listen();
  }

  void _listen() {
    _subscription?.cancel();
    _subscription = repository.stream(clientId).listen(_onEvent, onError: (_) {
      _failures += 1;
      _scheduleReconnect();
    }, onDone: () {
      _failures = 0;
      _scheduleReconnect();
    });
  }

  void _scheduleReconnect() {
    if (!_running) return;
    _reconnect?.cancel();
    final ms = _failures <= 0
        ? 500
        : (1000 * (1 << (_failures - 1))).clamp(1000, 15000);
    _reconnect = Timer(Duration(milliseconds: ms), () {
      if (_running) _listen();
    });
  }

  /// Network reachability from the platform (or tests).
  void setOnline(bool value) {
    if (online == value) return;
    online = value;
    notifyListeners();
  }

  void _onEvent(CallStreamEvent event) {
    _failures = 0;
    switch (event) {
      case CallChanged(:final call):
        _onCall(call);
      case RoomChanged(:final room):
        _rooms[room.id] = room;
        if (room.id == roomId) {
          this.room = room;
          notifyListeners();
        }
      case SignalReceived(:final signal):
        final current = call;
        final media = session;
        final link = links[signal.callId];
        if (current != null &&
            current.id == signal.callId &&
            media != null &&
            phase != TeamCallPhase.incoming) {
          unawaited(media
              .handle(signal)
              .catchError((Object _) => _connectionFailed(signal.callId)));
        } else if (link?.session != null) {
          unawaited(link!.session!
              .handle(signal)
              .catchError((Object _) => _connectionFailed(signal.callId)));
        } else {
          (_queued[signal.callId] ??= []).add(signal);
        }
    }
  }

  /// A connection that could not be set up: in a group call only that pair
  /// ends; otherwise the call does.
  void _connectionFailed(String callId) {
    if (group) {
      unawaited(repository
          .end(callId, 'failed')
          .then((_) {}, onError: (Object _) {}));
    } else {
      unawaited(hangUp(reason: 'failed'));
    }
  }

  void _adoptRoom(String id) {
    room ??= _rooms[id];
  }

  /// Another person's pair call of this group call, as the stream reports it.
  void _onLink(AppCall update) {
    var link = links[update.id];
    if (link == null) {
      if (update.status == CallStatus.ended) {
        _noteRingEnd(update);
        return;
      }
      link = links[update.id] = CallLink(update);
    }
    link.call = update;
    if (update.status == CallStatus.ended) {
      final media0 = link.session;
      link.session = null;
      unawaited(media0?.close());
      _noteRingEnd(update);
      revision += 1;
      _maybeFinishGroup();
      notifyListeners();
      return;
    }
    if (update.status == CallStatus.accepted && link.session == null) {
      unawaited(_connectLink(link));
    }
    notifyListeners();
  }

  Future<void> _connectLink(CallLink link) async {
    final owner = session;
    if (owner == null || link.session != null) return;
    final media0 = link.session = owner.fork();
    final id = link.call.id;
    try {
      await media0.connect(
          caller: !link.call.incoming,
          iceServers: await repository.iceServers(),
          sink: _sink(id),
          onState: (state) {
            link.state = state;
            if (state == CallLinkState.connected) {
              phase = TeamCallPhase.connected;
              _startHeartbeats();
            }
            if (state == CallLinkState.failed) _connectionFailed(id);
            notifyListeners();
          });
      if (media == CallMediaKind.video && camera) await media0.sendVideo();
      if (!link.call.incoming && !link.offered) {
        link.offered = true;
        await media0.offer();
      }
      for (final signal in _queued.remove(id) ?? const <CallSignal>[]) {
        await media0.handle(signal);
      }
    } on Object {
      _connectionFailed(id);
    }
  }

  void _noteRingEnd(AppCall ended) {
    if (ended.incoming) return;
    final name = ended.peerName;
    final key = switch (ended.word) {
      CallWord.declinedByPeer => 'ended.declinedByPeer',
      CallWord.busyPeer => 'ended.busyPeer',
      CallWord.noAnswer => 'ended.noAnswer',
      _ => null,
    };
    if (key != null) notice = CallNotice(key, {'name': name});
  }

  /// Nobody left on any connection: the group call is over for this phone.
  void _maybeFinishGroup() {
    if (liveCalls.isNotEmpty || phase == TeamCallPhase.ended) return;
    final id = roomId;
    if (id != null) {
      unawaited(repository.leave(id).then((_) {}, onError: (Object _) {}));
    }
    endedGroup = true;
    _finish(call!);
  }

  void _onCall(AppCall update) {
    final current = call;
    final inGroup = roomId;
    if (links.containsKey(update.id) ||
        (inGroup != null &&
            update.roomId == inGroup &&
            update.onThisDevice == true &&
            update.id != current?.id &&
            phase != TeamCallPhase.ended)) {
      _onLink(update);
      return;
    }
    if (current != null && current.id == update.id) {
      if (update.roomId != null) _adoptRoom(update.roomId!);
      if (phase == TeamCallPhase.incoming) {
        if (update.status != CallStatus.ringing || update.answeredElsewhere) {
          _systemDismiss(update.id);
          _reset();
          if (update.answeredElsewhere) {
            notice = const CallNotice('answeredElsewhere');
          }
          revision += 1;
          notifyListeners();
        }
        return;
      }
      if (update.status == CallStatus.ended) {
        if (group && phase != TeamCallPhase.ended) {
          // The others of the group call stay connected.
          call = update;
          _primaryEnded = true;
          _noteRingEnd(update);
          _maybeFinishGroup();
          notifyListeners();
          return;
        }
        _finish(update);
        return;
      }
      call = update;
      if (update.media == CallMediaKind.video) media = CallMediaKind.video;
      if (!update.incoming &&
          update.status == CallStatus.accepted &&
          !_offered) {
        _offered = true;
        phase = TeamCallPhase.connecting;
        unawaited(session
            ?.offer()
            .catchError((Object _) => hangUp(reason: 'failed')));
      }
      notifyListeners();
      return;
    }
    if (update.incoming &&
        update.status == CallStatus.ringing &&
        !update.answeredElsewhere) {
      if (busy) {
        unawaited(repository
            .end(update.id, 'busy')
            .then((_) {}, onError: (Object _) {}));
        return;
      }
      call = update;
      peer = CallPerson(userId: update.peerUserId, name: update.peerName);
      media = update.media;
      phase = TeamCallPhase.incoming;
      notice = null;
      if (!foreground && systemCallScreen.available) {
        unawaited(systemCallScreen
            .showIncoming(
                callId: update.id,
                callerName: update.peerName,
                video: update.video,
                ringFor: Duration(seconds: config?.ringTimeoutSeconds ?? 45))
            .catchError((Object _) {}));
      }
      notifyListeners();
    } else if (update.status == CallStatus.ended) {
      revision += 1;
      notifyListeners();
    }
  }

  CallSignalSink _sink(String callId) => _ApiSink(repository, callId);

  void _linkChanged(CallLinkState state) {
    switch (state) {
      case CallLinkState.connecting:
        if (phase != TeamCallPhase.ringing) phase = TeamCallPhase.connecting;
      case CallLinkState.connected:
        if (phase != TeamCallPhase.connected && systemCallScreen.available) {
          final id = call?.id;
          if (id != null) {
            unawaited(
                systemCallScreen.reportConnected(id).catchError((Object _) {}));
          }
        }
        phase = TeamCallPhase.connected;
        _startHeartbeats();
      case CallLinkState.reconnecting:
        phase = TeamCallPhase.reconnecting;
      case CallLinkState.failed:
        if (_primaryEnded) return;
        final id = call?.id;
        if (group && id != null) {
          _connectionFailed(id);
        } else {
          unawaited(hangUp(reason: 'failed'));
        }
        return;
    }
    notifyListeners();
  }

  void _beat() {
    for (final live in liveCalls) {
      if (live.status != CallStatus.accepted) continue;
      unawaited(repository.heartbeat(live.id).catchError((Object _) {}));
    }
  }

  void _startHeartbeats() {
    if (call == null || _heartbeat != null) return;
    _beat();
    _heartbeat = Timer.periodic(heartbeatEvery, (_) => _beat());
    _ticker ??=
        Timer.periodic(const Duration(seconds: 1), (_) => notifyListeners());
  }

  /// Rings [person]: the microphone (and camera) first, so a refusal rings nobody.
  Future<void> place(CallPerson person, {bool video = false}) async {
    if (busy) {
      notice = const CallNotice('alreadyInCall');
      notifyListeners();
      return;
    }
    final unavailable = unavailableKey;
    if (unavailable != null) {
      notice = CallNotice(unavailable);
      notifyListeners();
      return;
    }
    peer = person;
    media = video ? CallMediaKind.video : CallMediaKind.audio;
    camera = video;
    endedGroup = false;
    phase = TeamCallPhase.preparing;
    notice = null;
    call = null;
    notifyListeners();
    final media0 = mediaFactory();
    try {
      await media0.open(video: video);
    } on CallMediaException catch (error) {
      await media0.close();
      _reset();
      notice =
          CallNotice('media.${error.problem.name}', {'message': error.message});
      notifyListeners();
      return;
    }
    try {
      final placed = await repository.place(person.userId, media, clientId);
      if (placed.status == CallStatus.ended) {
        await media0.close();
        session = null;
        _finish(placed);
        return;
      }
      session = media0;
      call = placed;
      phase = TeamCallPhase.ringing;
      _offered = false;
      revision += 1;
      notifyListeners();
      await media0.connect(
          caller: true,
          iceServers: await repository.iceServers(),
          sink: _sink(placed.id),
          onState: _linkChanged);
    } on Object catch (error) {
      await media0.close();
      final placedId = call?.id;
      _reset();
      if (placedId != null) {
        unawaited(repository
            .end(placedId, 'failed')
            .then((_) {}, onError: (Object _) {}));
      }
      notice = CallNotice('startFailed', {
        'message': error is ApiException ? error.message : readableError(error)
      });
      notifyListeners();
    }
  }

  Future<void> answer({bool video = false}) async {
    final ringing = call;
    if (phase != TeamCallPhase.incoming || ringing == null) return;
    endedGroup = false;
    phase = TeamCallPhase.connecting;
    camera = video && ringing.video;
    notifyListeners();
    final media0 = mediaFactory();
    try {
      await media0.open(video: video && ringing.video);
    } on CallMediaException catch (error) {
      await media0.close();
      unawaited(repository
          .end(ringing.id, 'failed')
          .then((_) {}, onError: (Object _) {}));
      _reset();
      notice =
          CallNotice('media.${error.problem.name}', {'message': error.message});
      revision += 1;
      notifyListeners();
      return;
    }
    try {
      session = media0;
      await media0.connect(
          caller: false,
          iceServers: await repository.iceServers(),
          sink: _sink(ringing.id),
          onState: _linkChanged);
      call = await repository.accept(ringing.id, clientId);
      final joined = call?.roomId;
      if (joined != null) _adoptRoom(joined);
      for (final signal in _queued.remove(ringing.id) ?? const <CallSignal>[]) {
        await media0.handle(signal);
      }
      revision += 1;
      notifyListeners();
    } on Object catch (error) {
      await media0.close();
      _reset();
      notice = CallNotice('answerFailed', {
        'message': error is ApiException ? error.message : readableError(error)
      });
      revision += 1;
      notifyListeners();
    }
  }

  Future<void> decline() async {
    final ringing = call;
    if (phase != TeamCallPhase.incoming || ringing == null) return;
    _systemDismiss(ringing.id);
    _reset();
    notifyListeners();
    try {
      await repository.end(ringing.id, 'decline');
    } on Object {
      // The ring times out on the server anyway.
    }
    revision += 1;
    notifyListeners();
  }

  Future<void> hangUp({String reason = 'hangup'}) async {
    final current = call;
    final id = roomId;
    if (id != null && current != null && phase != TeamCallPhase.ended) {
      try {
        await repository.leave(id, reason: reason);
      } on Object {
        // Silent members leave on the server after a minute anyway.
      }
      endedGroup = true;
      _finish(current);
      return;
    }
    if (current == null) {
      if (phase == TeamCallPhase.preparing) return;
      _reset();
      notifyListeners();
      return;
    }
    try {
      _finish(await repository.end(current.id, reason));
    } on Object {
      _finish(AppCall(
        id: current.id,
        incoming: current.incoming,
        peerUserId: current.peerUserId,
        peerName: current.peerName,
        media: current.media,
        status: CallStatus.ended,
        outcome: reason == 'failed' ? 'failed' : 'completed',
        createdAt: current.createdAt,
        answeredAt: current.answeredAt,
      ));
    }
  }

  void toggleMute() {
    muted = !muted;
    session?.setMuted(muted);
    for (final link in links.values) {
      link.session?.setMuted(muted);
    }
    notifyListeners();
  }

  String? get _anyLiveId =>
      liveCalls.where((c) => c.status == CallStatus.accepted).firstOrNull?.id;

  /// Whether the "Add person" picker is open over the call screen.
  bool picking = false;

  void openPicker() {
    picking = true;
    notifyListeners();
  }

  void closePicker() {
    picking = false;
    notifyListeners();
  }

  /// Rings [person] into this call; the call becomes a group call.
  Future<void> invite(CallPerson person) async {
    final id = _anyLiveId;
    if (id == null) return;
    try {
      room = await repository.invite(id, person.userId, clientId);
      _rooms[room!.id] = room!;
      revision += 1;
    } on Object catch (error) {
      notice = CallNotice('group.addFailed', {
        'message': error is ApiException ? error.message : readableError(error)
      });
    }
    notifyListeners();
  }

  /// A voice call becomes a video call: the server records it (and refuses
  /// while video calls are off), then the camera goes on every connection.
  Future<void> turnOnVideo() async {
    final id = _anyLiveId;
    final owner = session;
    if (id == null || owner == null) return;
    try {
      await repository.turnOnVideo(id);
    } on Object catch (error) {
      notice = CallNotice('video.refused', {
        'message': error is ApiException ? error.message : readableError(error)
      });
      notifyListeners();
      return;
    }
    try {
      await owner.startCamera();
    } on CallMediaException catch (error) {
      notice =
          CallNotice('media.${error.problem.name}', {'message': error.message});
      notifyListeners();
      return;
    }
    media = CallMediaKind.video;
    camera = true;
    var everyone = true;
    for (final each in [
      if (!_primaryEnded) owner,
      for (final link in links.values)
        if (link.session != null) link.session!,
    ]) {
      if (!await each.sendVideo()) everyone = false;
    }
    if (!everyone) notice = const CallNotice('video.peerCannot');
    notifyListeners();
  }

  void toggleSpeaker() {
    speaker = !speaker;
    unawaited(session?.setSpeaker(speaker).catchError((Object _) {}));
    notifyListeners();
  }

  void toggleCamera() {
    camera = !camera;
    session?.setCamera(camera);
    for (final link in links.values) {
      link.session?.setCamera(camera);
    }
    notifyListeners();
  }

  void dismiss() {
    if (phase == TeamCallPhase.ended) {
      _reset();
      endedGroup = false;
    }
    notice = null;
    notifyListeners();
  }

  void clearNotice() {
    notice = null;
    notifyListeners();
  }

  void _finish(AppCall ended) {
    if (systemCallScreen.available) {
      unawaited(
          systemCallScreen.reportEnded(ended.id).catchError((Object _) {}));
    }
    _stopTimers();
    _closeLinks();
    picking = false;
    final media0 = session;
    session = null;
    unawaited(media0?.close());
    call = ended;
    peer ??= CallPerson(userId: ended.peerUserId, name: ended.peerName);
    phase = TeamCallPhase.ended;
    revision += 1;
    notifyListeners();
  }

  void _stopTimers() {
    _heartbeat?.cancel();
    _heartbeat = null;
    _ticker?.cancel();
    _ticker = null;
  }

  void _closeLinks() {
    for (final link in links.values) {
      unawaited(link.session?.close());
    }
    links.clear();
    room = null;
    _primaryEnded = false;
  }

  void _reset() {
    _stopTimers();
    _closeLinks();
    picking = false;
    final media0 = session;
    session = null;
    unawaited(media0?.close());
    call = null;
    peer = null;
    phase = TeamCallPhase.idle;
    muted = false;
    speaker = true;
    _offered = false;
  }

  /// Signed out: stop listening and forget everything.
  void stop() {
    _running = false;
    unawaited(_systemActions?.cancel());
    _systemActions = null;
    _reconnect?.cancel();
    unawaited(_subscription?.cancel());
    _subscription = null;
    _reset();
    _queued.clear();
    _rooms.clear();
    endedGroup = false;
    config = null;
    configPhase = CallConfigPhase.loading;
    notice = null;
    notifyListeners();
  }

  @override
  void dispose() {
    stop();
    super.dispose();
  }
}

class _ApiSink implements CallSignalSink {
  _ApiSink(this.repository, this.callId);
  final CallRepository repository;
  final String callId;

  @override
  Future<void> description(String kind, String sdp) =>
      repository.sendDescription(callId, kind, sdp);

  @override
  Future<void> candidates(List<Map<String, Object?>> batch) =>
      repository.sendCandidates(callId, batch);
}
