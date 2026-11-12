// Calls through the voice provider from the phone (docs/calls.md#calls-through-anum-from-the-phone):
// the web's rules (full international number, recording only with the
// confirmation), the prepare request, the call states, mute and hang up, and
// the choice on the dialpad, which appears only with a provider SDK that says
// it is supported. The SDK is a fake: none is linked into the build yet.
import 'dart:async';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/calls/calls_controller.dart';
import 'package:anum_mobile/features/calls/calls_screen.dart';
import 'package:anum_mobile/features/calls/provider_voice.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/calls_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'calls_test.dart' show FakeCallsRepository;

class FakeVoice implements ProviderVoiceClient {
  final log = <String>[];
  final out = StreamController<ProviderCallState>.broadcast();
  Object? refuse;

  @override
  bool get supported => true;

  @override
  Future<void> connect(ProviderCallTicket ticket) async {
    if (refuse != null) throw refuse!;
    log.add('connect:${ticket.callId}');
  }

  @override
  Stream<ProviderCallState> get states => out.stream;

  @override
  Future<void> setMuted(bool muted) async => log.add('muted:$muted');

  @override
  Future<void> hangUp() async => log.add('hangUp');
}

ProviderCallTicket _ticket() => const ProviderCallTicket(
    callId: 'call_9', token: 'example', identity: 'user', expiresIn: 600);

Future<void> _sheet(WidgetTester tester, ProviderCallController controller,
    {bool recordingOn = true, Locale locale = const Locale('en')}) async {
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      ...GlobalMaterialLocalizations.delegates
    ],
    home: Scaffold(
        body: ProviderCallSheet(
            controller: controller,
            phone: '+971501234567',
            recordingOn: recordingOn)),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('both catalogs carry the new keys', () {
    for (final key
        in callsEn.keys.where((k) => k.startsWith('calls.provider.call.'))) {
      expect(callsAr.containsKey(key), isTrue, reason: key);
    }
  });

  test('the ticket never prints its token', () {
    expect(_ticket().toString(), isNot(contains('example')));
    expect(const UnsupportedProviderVoice().supported, isFalse);
  });

  test(
      'a number that is not international and a recording without the confirmation ring nobody',
      () async {
    final sent = <Map<String, Object?>>[];
    final controller = ProviderCallController(
        client: FakeVoice(),
        prepare: (body) async {
          sent.add(body);
          return _ticket();
        });
    await controller.start(
        phone: '0501234567', record: false, consent: false, language: 'en');
    expect(controller.problem, 'calls.provider.call.number');
    await controller.start(
        phone: '+971501234567', record: true, consent: false, language: 'en');
    expect(controller.problem, 'calls.provider.call.consent');
    expect(sent, isEmpty);
  });

  testWidgets('a recorded call: confirm, prepare, connect, mute and hang up',
      (tester) async {
    final voice = FakeVoice();
    final sent = <Map<String, Object?>>[];
    final controller = ProviderCallController(
        client: voice,
        prepare: (body) async {
          sent.add(body);
          return _ticket();
        });
    await _sheet(tester, controller);
    expect(find.text(callsEn['calls.provider.call.cost']!), findsOneWidget);
    await tester.tap(find.text(callsEn['calls.provider.call.record']!));
    await tester.pumpAndSettle();
    await tester.tap(find.text(callsEn['calls.provider.call.start']!));
    await tester.pumpAndSettle();
    expect(find.text(callsEn['calls.provider.call.consent']!), findsOneWidget);
    await tester.tap(find.text(callsEn['calls.provider.call.confirm']!));
    await tester.pumpAndSettle();
    await tester.tap(find.text(callsEn['calls.provider.call.start']!));
    await tester.pumpAndSettle();
    expect(sent.single, {
      'phone': '+971501234567',
      'record': true,
      'recording_consent': true,
      'language': 'en'
    });
    expect(voice.log, ['connect:call_9']);
    voice.out.add(ProviderCallState.connected);
    await tester.pumpAndSettle();
    expect(find.text('Connected'), findsOneWidget);
    await tester.tap(find.text(callsEn['calls.provider.call.mute']!));
    await tester.pumpAndSettle();
    expect(voice.log, contains('muted:true'));
    await tester.tap(find.text(callsEn['calls.provider.call.hangUp']!));
    await tester.pumpAndSettle();
    expect(voice.log, contains('hangUp'));
    expect(find.text('Call ended'), findsOneWidget);
    controller.dispose();
  });

  testWidgets(
      'a refusal from the server says what failed; no recording switch while recording is off',
      (tester) async {
    final controller = ProviderCallController(
        client: FakeVoice(),
        prepare: (body) async => throw const ApiException(422,
            'Provider calls need a full international number, for example +971501234567.'));
    await _sheet(tester, controller, recordingOn: false);
    expect(find.text(callsEn['calls.provider.call.record']!), findsNothing);
    await tester.tap(find.text(callsEn['calls.provider.call.start']!));
    await tester.pumpAndSettle();
    expect(find.textContaining('The call could not start'), findsOneWidget);
    expect(find.text('Could not connect'), findsOneWidget);
    controller.dispose();
  });

  testWidgets('the sheet in Arabic', (tester) async {
    final controller = ProviderCallController(
        client: FakeVoice(), prepare: (_) async => _ticket());
    await _sheet(tester, controller, locale: const Locale('ar'));
    expect(find.text(callsAr['calls.provider.call.title']!), findsOneWidget);
    expect(tester.takeException(), isNull);
    controller.dispose();
  });

  testWidgets(
      'the dialpad offers a call through ANUM only with a supported SDK and calls through ANUM on',
      (tester) async {
    final repo = FakeCallsRepository()..aiReady = true;
    final launched = <Uri>[];
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(
      theme: AnumTheme.dark(),
      supportedLocales: const [Locale('en'), Locale('ar')],
      localizationsDelegates: const [
        AnumLocalizations.delegate,
        ...GlobalMaterialLocalizations.delegates
      ],
      home: CallsScreen(
        controller: CallsController(repo),
        dialer: (uri) async {
          launched.add(uri);
          return true;
        },
        pickContact: () async => null,
        providerVoice: FakeVoice(),
        prepareProviderCall: (_) async => _ticket(),
      ),
    ));
    await tester.pumpAndSettle();
    await tester.tap(find.text(callsEn['calls.tab.dial']!));
    await tester.pumpAndSettle();
    await tester.longPress(find.byKey(const Key('dial-0')));
    for (final key in '971501234567'.split('')) {
      await tester.tap(find.byKey(Key('dial-$key')));
    }
    await tester.ensureVisible(find.byKey(const Key('dial-call')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('dial-call')));
    await tester.pumpAndSettle();
    expect(find.text(callsEn['calls.provider.call.choose']!), findsOneWidget);
    await tester.tap(find.text(callsEn['calls.provider.call.viaPhone']!));
    await tester.pumpAndSettle();
    expect(launched.single.toString(), 'tel:+971501234567');
  });
}
