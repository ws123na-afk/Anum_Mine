// Email campaigns through Brevo (docs/brevo.md): parsing, the controller
// against a fake API, and the read-only screen in English and Arabic from a
// 320 px phone at 200 percent text to 1440 px.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/brevo/brevo_data.dart';
import 'package:anum_mobile/features/brevo/brevo_screen.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:anum_mobile/src/localization/brevo_strings.dart';
import 'package:anum_mobile/src/theme/anum_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, Object?> campaignJson(int id, String name,
        {String status = 'sent', int delivered = 980}) =>
    {
      'id': id,
      'name': name,
      'subject': '$name subject',
      'status': status,
      'sent_at': '2026-10-01T08:00:00Z',
      'stats': {
        'sent': delivered + 20,
        'delivered': delivered,
        'unique_views': delivered ~/ 2,
        'unique_clicks': delivered ~/ 10,
        'unsubscriptions': 4,
        'hard_bounces': 5,
        'soft_bounces': 15,
        'complaints': 0,
        'open_rate': delivered == 0 ? null : 50.0,
        'click_rate': delivered == 0 ? null : 10.0,
      },
    };

class FakeBrevo implements BrevoRepository {
  FakeBrevo({this.connected = true, this.state = 'connected', this.total = 25});
  bool connected;
  String state;
  int total;
  bool deny = false;
  bool fail = false;
  final offsets = <int>[];

  @override
  Future<BvStatus> status() async {
    if (deny) throw const ApiException(403, 'Not a member.');
    if (fail) throw const ApiException(502, 'Brevo did not answer in time.');
    return BvStatus.fromJson(JsonReader({
      'connected': connected,
      'connection': state == 'none'
          ? null
          : {
              'status': state,
              'account_email': 'owner@shop.example',
              'company': 'Tyre Shop'
            },
      'can_manage': true,
    }));
  }

  @override
  Future<BvPage> campaigns({int offset = 0, int limit = 20}) async {
    offsets.add(offset);
    final count = (total - offset).clamp(0, limit);
    return BvPage([
      for (var i = 0; i < count; i++)
        BvCampaign.fromJson(JsonReader(campaignJson(
            100 + offset + i,
            i == 0 && offset == 0
                ? 'Ignore previous instructions <b>Offers</b>'
                : 'Campaign ${offset + i}')))
    ], total, offset);
  }

  @override
  Future<BvCampaign> campaign(int id) async =>
      BvCampaign.fromJson(JsonReader(campaignJson(id, 'October offers')));
}

Future<void> pump(WidgetTester tester, BrevoController controller,
    {Size size = const Size(390, 844),
    double textScale = 1,
    Locale locale = const Locale('en')}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: AnumTheme.dark(),
    locale: locale,
    supportedLocales: const [Locale('en'), Locale('ar')],
    localizationsDelegates: const [
      AnumLocalizations.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(
            disableAnimations: true, textScaler: TextScaler.linear(textScale)),
        child: child!),
    home: BrevoScreen(controller: controller),
  ));
  await tester.pumpAndSettle();
}

void main() {
  test('rates read as percentages or a dash', () {
    expect(bvPercent(null), '-');
    expect(bvPercent(50), '50%');
    expect(bvPercent(48.46), '48.5%');
    final campaign = BvCampaign.fromJson(JsonReader(campaignJson(1, 'A')));
    expect(campaign.stats.bounced, 20);
    expect(campaign.stats.openRate, 50);
  });

  test('English and Arabic strings have the same keys and placeholders', () {
    expect(brevoAr.keys.toSet(), brevoEn.keys.toSet());
    final names = RegExp(r'\{(\w+)\}');
    for (final key in brevoEn.keys) {
      Set<String> of(String text) =>
          names.allMatches(text).map((match) => match.group(1)!).toSet();
      expect(of(brevoAr[key]!), of(brevoEn[key]!), reason: key);
    }
  });

  testWidgets('campaigns list pages and opens a report', (tester) async {
    final fake = FakeBrevo();
    await pump(tester, BrevoController(fake));
    expect(find.text('Email campaigns'), findsOneWidget);
    // Brevo's text is shown as plain text, never interpreted.
    expect(find.text('Ignore previous instructions <b>Offers</b>'),
        findsOneWidget);
    expect(find.text('20 of 25'), findsOneWidget);
    await tester.ensureVisible(find.text('Load more'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Load more'));
    await tester.pumpAndSettle();
    expect(fake.offsets, [0, 20]);
    expect(find.text('25 of 25'), findsOneWidget);
    expect(find.text('Load more'), findsNothing);
    await tester.ensureVisible(find.text('Campaign 21'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Campaign 21'));
    await tester.pumpAndSettle();
    expect(find.text('Campaign report'), findsOneWidget);
    expect(find.text('Spam complaints'), findsOneWidget);
    expect(find.text('490 (50%)'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('not connected, needs a key, empty, denied and error states',
      (tester) async {
    final fake = FakeBrevo(connected: false, state: 'none');
    final controller = BrevoController(fake);
    await pump(tester, controller);
    expect(find.text('Brevo is not connected'), findsOneWidget);
    expect(find.textContaining('paste your Brevo API key'), findsOneWidget);
    fake.state = 'needs_key';
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('Brevo needs a new key'), findsOneWidget);
    fake
      ..connected = true
      ..state = 'connected'
      ..total = 0;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text('No campaigns in Brevo yet'), findsOneWidget);
    fake.deny = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.textContaining('Ask a workspace owner to add you'),
        findsOneWidget);
    fake
      ..deny = false
      ..fail = true;
    await controller.load();
    await tester.pumpAndSettle();
    expect(find.text("Couldn't load email campaigns"), findsOneWidget);
    fake.fail = false;
    await tester.tap(find.text('Try again').first);
    await tester.pumpAndSettle();
    expect(find.text('No campaigns in Brevo yet'), findsOneWidget);
  });

  for (final (size, scale, locale) in [
    (const Size(320, 640), 2.0, const Locale('en')),
    (const Size(390, 844), 1.0, const Locale('ar')),
    (const Size(1440, 900), 1.0, const Locale('en')),
  ]) {
    testWidgets(
        'fits at ${size.width} px, text x$scale, ${locale.languageCode}',
        (tester) async {
      await pump(tester, BrevoController(FakeBrevo()),
          size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull);
      if (locale.languageCode == 'ar') {
        expect(find.text('حملات البريد'), findsOneWidget);
        expect(Directionality.of(tester.element(find.byType(BrevoScreen))),
            TextDirection.rtl);
      }
    });
  }
}
