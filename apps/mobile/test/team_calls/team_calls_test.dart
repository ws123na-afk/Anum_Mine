// In-app calls in the mobile app (docs/in-app-calls.md#flutter): the wire
// shapes, the incoming ring, answering, mute, hang up, decline, a refused
// microphone, calls off, the history with missed calls, Arabic, and the
// layout at phone and tablet sizes. The API and WebRTC are fakes.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/team_calls/call_controller.dart';
import 'package:anum_mobile/features/team_calls/call_media.dart';
import 'package:anum_mobile/features/team_calls/call_models.dart';
import 'package:anum_mobile/features/team_calls/call_repository.dart';
import 'package:anum_mobile/features/team_calls/call_screens.dart';
import 'package:anum_mobile/features/team_calls/call_strings.dart';
import 'package:anum_mobile/features/team_calls/system_call_screen.dart';
import 'package:anum_mobile/features/push/push_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, Object?> _callJson({
  String id = 'call_1',
  String direction = 'incoming',
  String status = 'ringing',
  String? outcome,
  String media = 'audio',
  String? answeredAt,
  int? duration,
  String? roomId,
  bool? onThisDevice,
  String peerId = 'user_ahmed',
  String peerName = 'Ahmed Saleh',
}) =>
    {
      'id': id,
      'direction': direction,
      'peer_user_id': peerId,
      'peer_name': peerName,
      'media': media,
      'status': status,
      'outcome': outcome,
      'created_at': '2026-10-18T09:00:00Z',
      'answered_at': answeredAt,
      'ended_at': null,
      'duration_seconds': duration,
      'ring_expires_at': '2026-10-18T09:00:45Z',
      'on_this_device': onThisDevice,
      'answered_elsewhere': false,
      'room_id': roomId,
    };

AppCall _call(
        {String id = 'call_1',
        String direction = 'incoming',
        String status = 'ringing',
        String? outcome,
        String media = 'audio',
        String? answeredAt,
        int? duration,
        String? roomId,
        bool? onThisDevice,
        String peerId = 'user_ahmed',
        String peerName = 'Ahmed Saleh'}) =>
    AppCall.fromJson(JsonReader(_callJson(
        id: id,
        direction: direction,
        status: status,
        outcome: outcome,
        media: media,
        answeredAt: answeredAt,
        duration: duration,
        roomId: roomId,
        onThisDevice: onThisDevice,
        peerId: peerId,
        peerName: peerName)));

class FakeCallRepository implements CallRepository {
  FakeCallRepository({this.mode = 'turn', this.history_ = const []});
  final String mode;
  final List<AppCall> history_;
  final events = StreamController<CallStreamEvent>.broadcast();
  final List<String> log = [];
  AppCall? placeAnswer;

  @override
  Future<CallConfig> config() async => CallConfig.fromJson(JsonReader({
        'enabled': mode != 'off',
        'mode': mode,
        'reason': null,
        'video_enabled': true,
        'ring_timeout_seconds': 45,
      }));

  @override
  Future<List<IceServer>> iceServers() async => const [];

  @override
  Future<List<CallPerson>> people() async => const [
        CallPerson(userId: 'user_me', name: 'Omar Owner'),
        CallPerson(userId: 'user_ahmed', name: 'Ahmed Saleh'),
        CallPerson(userId: 'user_sara', name: 'Sara Ali'),
      ];

  @override
  Future<AppCall> place(
      String calleeUserId, CallMediaKind media, String clientId) async {
    log.add('place:$calleeUserId:${media.name}');
    return placeAnswer ?? _call(direction: 'outgoing', media: media.name);
  }

  String? acceptRoom;

  @override
  Future<AppCall> accept(String callId, String clientId) async {
    log.add('accept:$callId');
    return _call(
        id: callId,
        status: 'accepted',
        roomId: acceptRoom,
        answeredAt: DateTime.now().toUtc().toIso8601String());
  }

  @override
  Future<AppCall> end(String callId, String reason) async {
    log.add('end:$callId:$reason');
    return _call(
        status: 'ended',
        outcome: reason == 'decline' ? 'declined' : 'completed',
        duration: 12,
        answeredAt: '2026-10-18T09:00:05Z');
  }

  @override
  Future<void> heartbeat(String callId) async => log.add('heartbeat');

  @override
  Future<void> sendDescription(String callId, String kind, String sdp) async =>
      log.add('signal:$kind');

  @override
  Future<void> sendCandidates(
          String callId, List<Map<String, Object?>> candidates) async =>
      log.add('ice');

  @override
  Future<CallPage> history({String? cursor, bool missed = false}) async =>
      CallPage(
          missed ? history_.where((c) => c.missed).toList() : history_, null);

  @override
  Stream<CallStreamEvent> stream(String clientId) => events.stream;

  CallRoom? inviteAnswer;

  @override
  Future<CallRoom> invite(String callId, String userId, String clientId) async {
    log.add('invite:$callId:$userId');
    return inviteAnswer ?? _room();
  }

  @override
  Future<CallRoom> leave(String roomId, {String reason = 'hangup'}) async {
    log.add('leave:$roomId:$reason');
    return _room(live: false);
  }

  Object? videoRefusal;

  @override
  Future<AppCall> turnOnVideo(String callId, {String source = 'camera'}) async {
    log.add('video:$callId:$source');
    if (videoRefusal != null) throw videoRefusal!;
    return _call(status: 'accepted', media: 'video');
  }
}

CallRoom _room({bool live = true, List<(String, String)>? members}) =>
    CallRoom.fromJson(JsonReader({
      'id': 'room_1',
      'media': 'audio',
      'status': live ? 'live' : 'ended',
      'host_user_id': 'user_ahmed',
      'max_people': 4,
      'created_at': '2026-10-18T09:00:00Z',
      'ended_at': null,
      'members': [
        for (final (id, state) in members ??
            [
              ('user_ahmed', 'joined'),
              ('user_me', 'joined'),
              ('user_sara', 'invited')
            ])
          {
            'user_id': id,
            'name': id,
            'state': state,
            'is_you': id == 'user_me'
          },
      ],
    }));

class FakeMedia implements CallMediaSession {
  FakeMedia({this.problem});
  final CallMediaProblem? problem;
  void Function(CallLinkState)? onState;
  final List<String> log = [];

  @override
  Future<void> open({required bool video}) async {
    if (problem != null) throw CallMediaException(problem!);
    log.add('open:$video');
  }

  @override
  Future<void> connect(
      {required bool caller,
      required List<IceServer> iceServers,
      required CallSignalSink sink,
      required void Function(CallLinkState state) onState}) async {
    this.onState = onState;
    log.add('connect:$caller');
    onState(CallLinkState.connecting);
  }

  @override
  Future<void> offer() async => log.add('offer');
  @override
  Future<void> handle(CallSignal signal) async =>
      log.add('handle:${signal.kind}');
  @override
  void setMuted(bool muted) => log.add('muted:$muted');
  @override
  Future<void> setSpeaker(bool on) async => log.add('speaker:$on');
  @override
  void setCamera(bool on) => log.add('camera:$on');
  @override
  Widget? remoteView() => null;
  @override
  Widget? localView() => null;
  @override
  Future<void> close() async => log.add('close');

  final List<FakeMedia> forks = [];
  bool canVideo = true;
  final ValueNotifier<CallPeerMedia?> peerState = ValueNotifier(null);

  @override
  CallMediaSession fork() {
    final child = FakeMedia();
    forks.add(child);
    log.add('fork');
    return child;
  }

  @override
  Future<void> startCamera() async => log.add('startCamera');

  @override
  Future<bool> sendVideo() async {
    log.add('sendVideo');
    return canVideo;
  }

  @override
  ValueListenable<CallPeerMedia?> get peer => peerState;
}

Future<AppCallsController> _pump(
    WidgetTester tester, FakeCallRepository repository,
    {FakeMedia? media,
    Locale locale = const Locale('en'),
    Widget? home,
    SystemCallScreen system = const NoSystemCallScreen(),
    Size size = const Size(390, 844)}) async {
  tester.view.physicalSize = size * tester.view.devicePixelRatio;
  addTearDown(tester.view.resetPhysicalSize);
  final fake = media ?? FakeMedia();
  final controller = AppCallsController(
      repository: repository,
      mediaFactory: () => fake,
      systemCallScreen: system,
      heartbeatEvery: const Duration(hours: 1))
    ..selfUserId = 'user_me';
  addTearDown(controller.stop);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.light(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    builder: (context, child) =>
        AppCallOverlay(controller: controller, child: child!),
    home: home ??
        Scaffold(
            body: Builder(
                builder: (context) => TeamCallsScreen(controller: controller))),
  ));
  await controller.start();
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  test('both catalogs have the same keys and placeholders, without long dashes',
      () {
    expect(TeamCallText.ar.keys.toSet(), TeamCallText.en.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in TeamCallText.en.keys) {
      Set<String> names(String text) =>
          placeholder.allMatches(text).map((m) => m.group(1)!).toSet();
      expect(names(TeamCallText.ar[key]!), names(TeamCallText.en[key]!),
          reason: key);
      expect(
          RegExp('[\u2013\u2014]')
              .hasMatch(TeamCallText.en[key]! + TeamCallText.ar[key]!),
          isFalse,
          reason: key);
    }
  });

  test('each side reads the outcome, and durations read like a phone', () {
    expect(_call(outcome: 'cancelled', status: 'ended').word, CallWord.missed);
    expect(
        _call(direction: 'outgoing', outcome: 'cancelled', status: 'ended')
            .word,
        CallWord.cancelled);
    expect(
        _call(direction: 'outgoing', outcome: 'missed', status: 'ended').word,
        CallWord.noAnswer);
    expect(_call(direction: 'outgoing', outcome: 'busy', status: 'ended').word,
        CallWord.busyPeer);
    expect(_call(outcome: 'declined', status: 'ended').word,
        CallWord.declinedByYou);
    expect(formatCallDuration(7), '0:07');
    expect(formatCallDuration(3723), '1:02:03');
    expect(newCallClientId(), matches(RegExp(r'^[A-Za-z0-9_-]{22}$')));
  });

  test('call pushes open the Calls screen', () {
    for (final kind in ['incoming_call', 'missed_call']) {
      final destination = PushDestination.fromData(
          {'anum_kind': kind, 'workspace_id': 'ws', 'app_call_id': 'call_1'});
      expect(destination?.route, '/team-calls', reason: kind);
    }
  });

  testWidgets('an incoming call rings, is answered, muted and hung up',
      (tester) async {
    final repository = FakeCallRepository();
    final media = FakeMedia();
    final controller = await _pump(tester, repository, media: media);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    expect(find.text('Ahmed Saleh'), findsWidgets);
    expect(find.text('Voice call · is calling you'), findsOneWidget);
    await tester.tap(find.bySemanticsLabel('Answer'));
    await tester.pump();
    await tester.pump();
    expect(repository.log, contains('accept:call_1'));
    expect(media.log, containsAllInOrder(['open:false', 'connect:false']));
    // The offer arrives on the stream; media connects and the timer starts.
    repository.events.add(SignalReceived(CallSignal.fromJson(JsonReader({
      'id': 'sig_1',
      'call_id': 'call_1',
      'kind': 'offer',
      'payload': {'type': 'offer', 'sdp': 'v=0'}
    }))));
    await tester.pump();
    await tester.pump();
    expect(media.log, contains('handle:offer'));
    media.onState!(CallLinkState.connected);
    await tester.pump();
    expect(controller.phase, TeamCallPhase.connected);
    expect(
        find.textContaining(RegExp(r'Voice call · \d:\d\d')), findsOneWidget);
    expect(repository.log, contains('heartbeat'));
    await tester.tap(find.bySemanticsLabel('Mute'));
    await tester.pump();
    expect(media.log, contains('muted:true'));
    expect(find.bySemanticsLabel('Unmute'), findsOneWidget);
    await tester.tap(find.bySemanticsLabel('Hang up'));
    await tester.pumpAndSettle();
    expect(repository.log, contains('end:call_1:hangup'));
    expect(find.text('Voice call · Call ended'), findsOneWidget);
    expect(find.text('Call lasted 0:12.'), findsOneWidget);
    expect(media.log, contains('close'));
    await tester.tap(find.bySemanticsLabel('Close'));
    await tester.pumpAndSettle();
    expect(controller.phase, TeamCallPhase.idle);
  });

  testWidgets(
      'a group call: answering joins the mesh, the others stay when the host leaves, hanging up leaves',
      (tester) async {
    final repository = FakeCallRepository()..acceptRoom = 'room_1';
    final media = FakeMedia();
    final controller = await _pump(tester, repository, media: media);
    repository.events.add(RoomChanged(_room(members: [
      ('user_ahmed', 'joined'),
      ('user_sara', 'joined'),
      ('user_me', 'invited'),
    ])));
    repository.events.add(CallChanged(_call(roomId: 'room_1')));
    await tester.pump();
    await tester.pump();
    expect(find.text('Group call · is calling you'), findsOneWidget);
    await tester.tap(find.bySemanticsLabel('Answer'));
    await tester.pump();
    await tester.pump();
    expect(controller.group, isTrue);
    // The server made this phone's pair to Sara, who was already in: it offers.
    repository.events.add(CallChanged(_call(
        id: 'call_2',
        direction: 'outgoing',
        status: 'accepted',
        roomId: 'room_1',
        onThisDevice: true,
        peerId: 'user_sara',
        peerName: 'Sara Ali',
        answeredAt: DateTime.now().toUtc().toIso8601String())));
    await tester.pump();
    await tester.pump();
    expect(media.log, contains('fork'));
    final sara = media.forks.single;
    expect(sara.log, containsAllInOrder(['connect:true', 'offer']));
    media.onState!(CallLinkState.connected);
    sara.onState!(CallLinkState.connected);
    await tester.pump();
    expect(find.text('Group call'), findsOneWidget);
    expect(find.textContaining('3 people'), findsOneWidget);
    expect(find.text('Sara Ali · Connected'), findsOneWidget);
    expect(find.text('Ahmed Saleh · Connected'), findsOneWidget);
    // Signals for Sara's pair go to her connection.
    repository.events.add(SignalReceived(CallSignal.fromJson(JsonReader({
      'id': 'sig_2',
      'call_id': 'call_2',
      'kind': 'answer',
      'payload': {'type': 'answer', 'sdp': 'v=0'}
    }))));
    await tester.pump();
    expect(sara.log, contains('handle:answer'));
    // Ahmed (the host) leaves: Sara stays connected.
    repository.events.add(CallChanged(_call(
        status: 'ended',
        outcome: 'completed',
        roomId: 'room_1',
        answeredAt: '2026-10-18T09:00:05Z')));
    await tester.pump();
    expect(controller.phase, TeamCallPhase.connected);
    expect(find.textContaining('2 people'), findsOneWidget);
    expect(find.text('Ahmed Saleh · Connected'), findsNothing);
    await tester.tap(find.bySemanticsLabel('Hang up'));
    await tester.pumpAndSettle();
    expect(repository.log, contains('leave:room_1:hangup'));
    expect(controller.endedGroup, isTrue);
    expect(find.text('Group call'), findsOneWidget);
    expect(find.bySemanticsLabel('Call again'), findsNothing);
    expect(sara.log, contains('close'));
  });

  testWidgets('adding a person rings them into the call', (tester) async {
    final repository = FakeCallRepository();
    final media = FakeMedia();
    final controller = await _pump(tester, repository, media: media);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Answer'));
    await tester.pump();
    await tester.pump();
    media.onState!(CallLinkState.connected);
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Add person'));
    await tester.pumpAndSettle();
    expect(find.text('Add a person to the call'), findsOneWidget);
    // You and Ahmed are already in the call.
    expect(
        find.descendant(
            of: find.byType(TeamCallPeoplePicker),
            matching: find.widgetWithText(ListTile, 'Omar Owner')),
        findsNothing);
    expect(
        find.descendant(
            of: find.byType(TeamCallPeoplePicker),
            matching: find.widgetWithText(ListTile, 'Ahmed Saleh')),
        findsNothing);
    await tester.enterText(
        find.descendant(
            of: find.byType(TeamCallPeoplePicker),
            matching: find.byType(TextField)),
        'nobody');
    await tester.pump();
    expect(find.text('No teammate matches the search.'), findsOneWidget);
    await tester.enterText(
        find.descendant(
            of: find.byType(TeamCallPeoplePicker),
            matching: find.byType(TextField)),
        'sara');
    await tester.pump();
    await tester.tap(find.descendant(
        of: find.byType(TeamCallPeoplePicker),
        matching: find.widgetWithText(ListTile, 'Sara Ali')));
    await tester.pumpAndSettle();
    expect(repository.log, contains('invite:call_1:user_sara'));
    expect(controller.room?.id, 'room_1');
    controller.stop(); // ends the timer of the call in progress
  });

  testWidgets('a voice call turns into a video call, and says when it cannot',
      (tester) async {
    final repository = FakeCallRepository();
    final media = FakeMedia()..canVideo = false;
    final controller = await _pump(tester, repository, media: media);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Answer'));
    await tester.pump();
    await tester.pump();
    media.onState!(CallLinkState.connected);
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Turn on video'));
    await tester.pumpAndSettle();
    expect(repository.log, contains('video:call_1:camera'));
    expect(media.log, containsAllInOrder(['startCamera', 'sendVideo']));
    expect(controller.media, CallMediaKind.video);
    expect(find.textContaining('older ANUM app'), findsOneWidget);
    expect(find.bySemanticsLabel('Turn camera off'), findsOneWidget);
    controller.stop(); // ends the timer of the call in progress
  });

  testWidgets('turning video on while video calls are off says so',
      (tester) async {
    final repository = FakeCallRepository()
      ..videoRefusal =
          const ApiException(422, 'Video calls are turned off on this server');
    final media = FakeMedia();
    final controller = await _pump(tester, repository, media: media);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Answer'));
    await tester.pump();
    await tester.pump();
    media.onState!(CallLinkState.connected);
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Turn on video'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Video calls are turned off on this server'),
        findsOneWidget);
    expect(media.log, isNot(contains('startCamera')));
    expect(controller.media, CallMediaKind.audio);
    controller.stop(); // ends the timer of the call in progress
  });

  test('a group call is read from the stream, with its free places', () {
    final room = _room();
    expect(room.live, isTrue);
    expect(room.freeSlots, 1);
    expect(room.members.where((m) => m.isYou).single.userId, 'user_me');
    expect(
        CallPeerMedia.parse('{"camera":true,"screen":false,"muted":false}')
            ?.camera,
        isTrue);
    expect(CallPeerMedia.parse('{"camera":"yes"}'), isNull);
    expect(CallPeerMedia.parse('nope'), isNull);
  });

  testWidgets('declining sends decline and clears the ring', (tester) async {
    final repository = FakeCallRepository();
    final controller = await _pump(tester, repository);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    await tester.tap(find.bySemanticsLabel('Decline'));
    await tester.pumpAndSettle();
    expect(repository.log, contains('end:call_1:decline'));
    expect(controller.phase, TeamCallPhase.idle);
  });

  testWidgets('a call answered on another device stops ringing here',
      (tester) async {
    final repository = FakeCallRepository();
    final controller = await _pump(tester, repository);
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    await tester.pump();
    final elsewhere = _callJson(status: 'accepted')
      ..['answered_elsewhere'] = true;
    repository.events.add(CallChanged(AppCall.fromJson(JsonReader(elsewhere))));
    await tester.pump();
    await tester.pump();
    expect(controller.phase, TeamCallPhase.idle);
    expect(
        find.text('You answered this call on another device.'), findsOneWidget);
  });

  testWidgets(
      'placing a call: ringing, the callee answers, the offer goes out, declined ends it',
      (tester) async {
    final repository = FakeCallRepository();
    final media = FakeMedia();
    final controller = await _pump(tester, repository, media: media);
    await tester.tap(find.byTooltip('Call Ahmed Saleh'));
    await tester.pump();
    await tester.pump();
    expect(repository.log, contains('place:user_ahmed:audio'));
    expect(find.text('Voice call · Ringing'), findsOneWidget);
    repository.events.add(CallChanged(_call(
        direction: 'outgoing',
        status: 'accepted',
        answeredAt: '2026-10-18T09:00:05Z')));
    await tester.pump();
    await tester.pump();
    expect(media.log, contains('offer'));
    repository.events.add(CallChanged(
        _call(direction: 'outgoing', status: 'ended', outcome: 'declined')));
    await tester.pumpAndSettle();
    expect(controller.phase, TeamCallPhase.ended);
    expect(find.text('Ahmed Saleh declined the call.'), findsOneWidget);
    // The caller's own row is not listed.
    expect(find.byTooltip('Call Omar Owner'), findsNothing);
  });

  testWidgets('a refused microphone says how to allow it and rings nobody',
      (tester) async {
    final repository = FakeCallRepository();
    await _pump(tester, repository,
        media: FakeMedia(problem: CallMediaProblem.denied));
    await tester.tap(find.byTooltip('Call Ahmed Saleh'));
    await tester.pumpAndSettle();
    expect(repository.log.where((e) => e.startsWith('place')), isEmpty);
    expect(find.textContaining('ANUM cannot use your microphone or camera'),
        findsOneWidget);
  });

  testWidgets(
      'calls off: the buttons are disabled and the screen says who turns them on',
      (tester) async {
    final repository = FakeCallRepository(mode: 'off');
    await _pump(tester, repository);
    expect(find.textContaining('Calls are off on this server'), findsOneWidget);
    final button = tester.widget<IconButton>(
        find.widgetWithIcon(IconButton, Icons.call_outlined).first);
    expect(button.onPressed, isNull);
  });

  testWidgets(
      'recent calls show missed ones, and the Missed filter keeps only them',
      (tester) async {
    final repository = FakeCallRepository(history_: [
      _call(status: 'ended', outcome: 'cancelled'),
      _call(
          direction: 'outgoing',
          status: 'ended',
          outcome: 'completed',
          answeredAt: '2026-10-18T09:00:05Z',
          duration: 75),
    ]);
    await _pump(tester, repository);
    await tester.tap(find.text('Recent'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Missed call · Incoming · Voice call'),
        findsOneWidget);
    expect(find.textContaining('Call ended · Outgoing · Voice call'),
        findsOneWidget);
    expect(find.textContaining('1:15'), findsOneWidget);
    await tester.tap(find.text('Missed'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Call ended'), findsNothing);
  });

  testWidgets('the incoming screen in Arabic, right to left', (tester) async {
    final repository = FakeCallRepository();
    await _pump(tester, repository, locale: const Locale('ar'));
    repository.events.add(CallChanged(_call(media: 'video')));
    await tester.pump();
    await tester.pump();
    expect(find.text('مكالمة فيديو · يتصل بك'), findsOneWidget);
    expect(find.bySemanticsLabel('رد بالفيديو'), findsOneWidget);
    expect(
        Directionality.of(tester.element(find.text('مكالمة فيديو · يتصل بك'))),
        TextDirection.rtl);
  });

  for (final size in const [
    Size(320, 568),
    Size(390, 844),
    Size(768, 1024),
    Size(1024, 768)
  ]) {
    testWidgets(
        'the call screen fits ${size.width.toInt()}x${size.height.toInt()} at 200% text',
        (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = 2;
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      final repository = FakeCallRepository();
      await _pump(tester, repository, size: size);
      repository.events.add(CallChanged(_call(media: 'video')));
      await tester.pump();
      await tester.pump();
      expect(tester.takeException(), isNull);
      expect(find.bySemanticsLabel('Decline'), findsOneWidget);
    });
  }

  for (final size in const [Size(320, 568), Size(1024, 768)]) {
    testWidgets(
        'a group call of four fits ${size.width.toInt()}x${size.height.toInt()} at 200% text, in Arabic',
        (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = 2;
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      final repository = FakeCallRepository()..acceptRoom = 'room_1';
      final media = FakeMedia();
      final controller = await _pump(tester, repository,
          media: media, size: size, locale: const Locale('ar'));
      repository.events.add(CallChanged(_call(roomId: 'room_1')));
      await tester.pump();
      await tester.pump();
      await controller.answer();
      for (final (n, id, name) in [
        (2, 'user_sara', 'Sara Ali'),
        (3, 'user_dan', 'Dan Long-Surname Example')
      ]) {
        repository.events.add(CallChanged(_call(
            id: 'call_$n',
            direction: 'outgoing',
            status: 'accepted',
            roomId: 'room_1',
            onThisDevice: true,
            peerId: id,
            peerName: name,
            answeredAt: DateTime.now().toUtc().toIso8601String())));
      }
      await tester.pump();
      await tester.pump();
      media.onState!(CallLinkState.connected);
      await tester.pump();
      expect(tester.takeException(), isNull);
      expect(find.text('مكالمة جماعية'), findsOneWidget);
      expect(find.bySemanticsLabel('إنهاء المكالمة'), findsOneWidget);
      controller.stop();
    });
  }

  testWidgets(
      'in the background a ring goes to the phone\'s own call screen, which answers it',
      (tester) async {
    final repository = FakeCallRepository();
    final media = FakeMedia();
    final system = FakeSystemCallScreen();
    final controller =
        await _pump(tester, repository, media: media, system: system);
    // In the foreground the app's own ring is used.
    repository.events.add(CallChanged(_call()));
    await tester.pump();
    expect(system.log, isEmpty);
    repository.events.add(CallChanged(
        _call(status: 'ended', outcome: 'cancelled', answeredAt: null)));
    await tester.pump();
    expect(system.log, ['dismiss:call_1']);
    controller.setForeground(false);
    repository.events.add(CallChanged(_call(id: 'call_2', media: 'video')));
    await tester.pump();
    expect(system.log.last, 'show:call_2:Ahmed Saleh:true:45');
    system.actionsOut.add(const SystemCallAccepted('call_2'));
    await tester.pump();
    await tester.pump();
    expect(repository.log, contains('accept:call_2'));
    media.onState!(CallLinkState.connected);
    await tester.pump();
    expect(system.log, contains('connected:call_2'));
    system.actionsOut.add(const SystemCallEnded('call_2'));
    await tester.pumpAndSettle();
    expect(repository.log, contains('end:call_2:hangup'));
    expect(system.log.last, startsWith('ended:'));
    controller.dismiss();
  });

  test('without a native plugin there is no system call screen', () async {
    const none = NoSystemCallScreen();
    expect(none.available, isFalse);
    expect(await none.actions.isEmpty, isTrue);
  });

  test('an API refusal of the config reads as denied', () async {
    final controller = AppCallsController(
        repository: _DeniedRepository(), mediaFactory: FakeMedia.new);
    await controller.start();
    expect(controller.configPhase, CallConfigPhase.denied);
    expect(controller.unavailableKey, 'denied');
    controller.stop();
  });
}

class FakeSystemCallScreen implements SystemCallScreen {
  final List<String> log = [];
  final actionsOut = StreamController<SystemCallAction>.broadcast();

  @override
  bool get available => true;

  @override
  Future<void> showIncoming(
          {required String callId,
          required String callerName,
          required bool video,
          required Duration ringFor}) async =>
      log.add('show:$callId:$callerName:$video:${ringFor.inSeconds}');

  @override
  Future<void> dismiss(String callId) async => log.add('dismiss:$callId');

  @override
  Future<void> reportConnected(String callId) async =>
      log.add('connected:$callId');

  @override
  Future<void> reportEnded(String callId) async => log.add('ended:$callId');

  @override
  Stream<SystemCallAction> get actions => actionsOut.stream;
}

class _DeniedRepository extends FakeCallRepository {
  @override
  Future<CallConfig> config() async =>
      throw const ApiException(403, 'Forbidden');
}
