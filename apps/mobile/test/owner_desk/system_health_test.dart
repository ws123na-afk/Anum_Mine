// System health on the phone (docs/system-health.md#on-the-phone): the
// summary from the API's real state, problems first, each check's line and
// what to do, who may see it, a failed read with a retry, Arabic, and 320 to
// 1024 px at 200 percent.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/system_health/system_health_data.dart';
import 'package:anum_mobile/features/system_health/system_health_screen.dart';
import 'package:anum_mobile/features/team/notifications_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'pump.dart';

Map<String, Object?> _report({bool healthy = false}) => {
      'checked_at': '2026-11-12T08:00:00Z',
      'overall': healthy ? 'green' : 'red',
      'started_at': '2026-11-10T08:00:00Z',
      'uptime_seconds': 172800,
      'items': [
        {
          'key': 'backup_local',
          'state': 'green',
          'detail': {
            'recorded': true,
            'ever': true,
            'last_ok': true,
            'last_success_at': '2026-11-12T02:41:00Z',
            'age_hours': 5.3
          }
        },
        {
          'key': 'backup_offsite',
          'state': healthy ? 'green' : 'red',
          'detail': {'recorded': true, 'ever': healthy, 'last_ok': healthy}
        },
        {
          'key': 'ai_model',
          'state': healthy ? 'green' : 'red',
          'detail': {
            'provider': healthy ? 'anthropic' : 'mock',
            'placeholder': !healthy,
            'key_missing': false
          }
        },
        {
          'key': 'disk',
          'state': 'green',
          'detail': {'readable': true, 'free_pct': 63.5}
        },
        {
          'key': 'memory',
          'state': healthy ? 'green' : 'amber',
          'detail': {
            'readable': true,
            'available_pct': 60.0,
            'api_free_pct': 15
          }
        },
        {
          'key': 'errors',
          'state': 'green',
          'detail': {'count_24h': 0}
        },
        {
          'key': 'restarts',
          'state': 'unknown',
          'detail': {'configured': false}
        },
        {'key': 'later_check', 'state': 'red', 'detail': <String, Object?>{}},
      ],
      'you': {'tenant_id': 'tenant_a', 'workspace_id': 'workspace_a'},
    };

class _Fake implements SystemHealthRepository {
  _Fake({this.fail});
  bool healthy = false;
  Exception? fail;
  int reads = 0;

  @override
  Future<SystemHealth> read() async {
    reads += 1;
    final error = fail;
    if (error != null) {
      fail = null;
      throw error;
    }
    return SystemHealth.fromJson(JsonReader(_report(healthy: healthy)));
  }
}

void main() {
  test('problems first, unknown keys dropped, and the count of checks to do',
      () {
    final h = SystemHealth.fromJson(JsonReader(_report()));
    expect(h.items.map((i) => i.key), isNot(contains('later_check')));
    expect(h.sorted.map((i) => i.key).toList(), [
      'backup_offsite',
      'ai_model',
      'memory',
      'restarts',
      'backup_local',
      'disk',
      'errors'
    ]);
    expect(h.attention, 3);
    expect(NotificationsScreen.routeFor('system_health'), '/system-health');
    expect(NotificationsScreen.routeFor('system_restart'), '/system-health');
  });

  testWidgets('the owner reads each check and what to do', (tester) async {
    final fake = _Fake();
    await pumpDesk(
        tester, SystemHealthScreen(controller: SystemHealthController(fake)));
    expect(find.text('Checks that need you: 3'), findsOneWidget);
    expect(find.text('It has never succeeded.'), findsOneWidget);
    expect(
        find.text('The mock model: answers are placeholders.'), findsOneWidget);
    expect(find.text('15% free'), findsOneWidget);
    expect(find.text('63.5% free'), findsOneWidget);
    expect(find.text('Errors in the last 24 hours: 0'), findsOneWidget);
    expect(find.text('Tracked once owner notices are set up.'), findsOneWidget);
    // A fix for each problem, none for the healthy ones or the untracked restarts.
    expect(find.textContaining('backup.sh offsite', findRichText: true),
        findsOneWidget);
    expect(find.textContaining('What to do:', findRichText: true),
        findsNWidgets(3));
    fake.healthy = true;
    await tapDesk(tester, find.byKey(const Key('sh-refresh')));
    expect(find.text('All checks are green'), findsOneWidget);
    expect(fake.reads, 2);
    expect(tester.takeException(), isNull);
  });

  testWidgets('who may see it, and a failed read with a retry', (tester) async {
    final denied = _Fake(
        fail: const ApiException(403, 'Only a platform operator can do this.'));
    await pumpDesk(
        tester, SystemHealthScreen(controller: SystemHealthController(denied)));
    expect(find.byKey(const Key('sh-denied')), findsOneWidget);

    final failing = _Fake(fail: const ApiException(503, 'Service unavailable'));
    await pumpDesk(
        tester,
        SystemHealthScreen(
            key: const Key('second'),
            controller: SystemHealthController(failing)));
    expect(find.byKey(const Key('sh-error')), findsOneWidget);
    await tapDesk(tester, find.text('Try again'));
    expect(find.byKey(const Key('sh-item-backup_offsite')), findsOneWidget);
  });

  for (final (size, scale, locale) in deskSizes) {
    testWidgets(
        'fits ${size.width.toInt()} px at ${scale}x in ${locale.languageCode}',
        (tester) async {
      await pumpDesk(tester,
          SystemHealthScreen(controller: SystemHealthController(_Fake())),
          size: size, textScale: scale, locale: locale);
      expect(tester.takeException(), isNull);
      if (locale.languageCode == 'ar') {
        expect(find.text('صحة النظام'), findsWidgets);
      }
    });
  }
}
