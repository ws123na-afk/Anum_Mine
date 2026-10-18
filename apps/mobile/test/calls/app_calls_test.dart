// In-app calls in the mobile app (docs/in-app-calls.md#flutter): the wire
// shapes, the incoming ring, answering, mute, hang up, decline, a refused
// microphone, calls off, the history with missed calls, Arabic, and the
// layout at phone and tablet sizes. The API and WebRTC are fakes.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/calls/call_controller.dart';
import 'package:anum_mobile/features/calls/call_media.dart';
import 'package:anum_mobile/features/calls/call_models.dart';
import 'package:anum_mobile/features/calls/call_repository.dart';
import 'package:anum_mobile/features/calls/call_screens.dart';
import 'package:anum_mobile/features/calls/call_strings.dart';
import 'package:anum_mobile/features/push/push_models.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
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
}) =>
    {
      'id': id,
      'direction': direction,
      'peer_user_id': 'user_ahmed',
      'peer_name': 'Ahmed Saleh',
      'media': media,
      'status': status,
      'outcome': outcome,
      'created_at': '2026-10-18T09:00:00Z',
      'answered_at': answeredAt,
      'ended_at': null,
      'duration_seconds': duration,
      'ring_expires_at': '2026-10-18T09:00:45Z',
      'on_this_device': null,
      'answered_elsewhere': false,
    };

AppCall _call(
        {String direction = 'incoming',
        String status = 'ringing',
        String? outcome,
        String media = 'audio',
        String? answeredAt,
        int? duration}) =>
    AppCall.fromJson(JsonReader(_callJson(
        direction: direction,
        status: status,
        outcome: outcome,
        media: media,
        answeredAt: answeredAt,
        duration: duration)));

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
      ];

  @override
  Future<AppCall> place(
      String calleeUserId, CallMediaKind media, String clientId) async {
    log.add('place:$calleeUserId:${media.name}');
    return placeAnswer ?? _call(direction: 'outgoing', media: media.name);
  }

  @override
  Future<AppCall> accept(String callId, String clientId) async {
    log.add('accept:$callId');
    return _call(
        status: 'accepted',
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
}

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
}

Future<AppCallsController> _pump(
    WidgetTester tester, FakeCallRepository repository,
    {FakeMedia? media,
    Locale locale = const Locale('en'),
    Widget? home,
    Size size = const Size(390, 844)}) async {
  tester.view.physicalSize = size * tester.view.devicePixelRatio;
  addTearDown(tester.view.resetPhysicalSize);
  final fake = media ?? FakeMedia();
  final controller = AppCallsController(
      repository: repository,
      mediaFactory: () => fake,
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
                builder: (context) => CallsScreen(controller: controller))),
  ));
  await controller.start();
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  test('both catalogs have the same keys and placeholders, without long dashes',
      () {
    expect(CallText.ar.keys.toSet(), CallText.en.keys.toSet());
    final placeholder = RegExp(r'\{(\w+)\}');
    for (final key in CallText.en.keys) {
      Set<String> names(String text) =>
          placeholder.allMatches(text).map((m) => m.group(1)!).toSet();
      expect(names(CallText.ar[key]!), names(CallText.en[key]!), reason: key);
      expect(
          RegExp('[\u2013\u2014]')
              .hasMatch(CallText.en[key]! + CallText.ar[key]!),
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
      expect(destination?.route, '/calls', reason: kind);
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
    expect(controller.phase, CallPhase.connected);
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
    expect(controller.phase, CallPhase.idle);
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
    expect(controller.phase, CallPhase.idle);
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
    expect(controller.phase, CallPhase.idle);
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
    expect(controller.phase, CallPhase.ended);
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

  test('an API refusal of the config reads as denied', () async {
    final controller = AppCallsController(
        repository: _DeniedRepository(), mediaFactory: FakeMedia.new);
    await controller.start();
    expect(controller.configPhase, CallConfigPhase.denied);
    expect(controller.unavailableKey, 'denied');
    controller.stop();
  });
}

class _DeniedRepository extends FakeCallRepository {
  @override
  Future<CallConfig> config() async =>
      throw const ApiException(403, 'Forbidden');
}
