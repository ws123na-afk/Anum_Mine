import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../support/fake_api.dart';
import '../support/fixtures.dart';
import '../support/harness.dart';

/// The Notifications screen (docs/team-work.md#notifications-page) and the
/// Favorites screen's own order (docs/knowledge-base.md#favorites), through
/// the whole app against API-shaped answers.
Map<String, Object?> _notice(String id, String kind, String title,
        {bool read = false}) =>
    {
      'id': id,
      'user_id': Fx.userId,
      'kind': kind,
      'title': title,
      'body': 'Body of $title.',
      'work_item_id': null,
      'read_at': read ? Fx.iso(const Duration(minutes: -5)) : null,
      'email_status': 'not_requested',
      'email_attempts': 0,
      'email_error': null,
      'push_status': 'not_requested',
      'created_at': Fx.iso(const Duration(hours: -1)),
    };

void installNotifications(FakeAnumApi api) {
  final rows = [
    _notice('ntf_1', 'calendar_reminder', 'Supplier meeting at 3'),
    _notice('ntf_2', 'budget_alert', 'Model budget at 80%', read: true),
  ];
  api.get('/api/v1/notifications', (r) {
    final state = r.query['state'], kind = r.query['kind'];
    final items = rows.where((x) {
      final read = x['read_at'] != null;
      if (state == 'unread' && read) return false;
      if (state == 'read' && !read) return false;
      return kind == null || x['kind'] == kind;
    }).toList();
    return FakeResponse.json({
      'items': items,
      'unread': rows.where((x) => x['read_at'] == null).length,
      'next_cursor': null,
    });
  });
  api.get('/api/v1/notifications/kinds', (_) {
    final counts = <String, List<int>>{};
    for (final x in rows) {
      final c = counts.putIfAbsent(x['kind']! as String, () => [0, 0]);
      c[0]++;
      if (x['read_at'] == null) c[1]++;
    }
    return FakeResponse.json({
      'items': [
        for (final e in counts.entries)
          {'kind': e.key, 'total': e.value[0], 'unread': e.value[1]}
      ]
    });
  });
  api.post('/api/v1/notifications/{id}/unread', (r) {
    final row = rows.firstWhere((x) => x['id'] == r.params['id']);
    row['read_at'] = null;
    return FakeResponse.json(row);
  });
  api.delete('/api/v1/notifications/{id}', (r) {
    rows.removeWhere((x) => x['id'] == r.params['id']);
    return const FakeResponse.empty();
  });
}

void installFavorites(FakeAnumApi api) {
  final rows = [
    for (final name in ['Tyre cost report', 'Supplier list', 'Weekly summary'])
      {
        'id': 'fav_${name.split(' ').first.toLowerCase()}',
        'item_type': 'report',
        'item_id': 'report_${name.split(' ').first.toLowerCase()}',
        'label': name,
        'created_at': Fx.iso(const Duration(days: -2)),
        'position': null,
      }
  ];
  api.get('/api/v1/favorites', (r) {
    final q = (r.query['q'] ?? '').toLowerCase();
    final items =
        rows.where((x) => x['label']!.toLowerCase().contains(q)).toList();
    return FakeResponse.json({'items': items, 'total': items.length});
  });
  api.post('/api/v1/favorites/{id}/move', (r) {
    final moving = rows.firstWhere((x) => x['id'] == r.params['id']);
    final before = r.body['before_id'], after = r.body['after_id'];
    rows.remove(moving);
    final at = rows.indexWhere((x) => x['id'] == (before ?? after));
    rows.insert(before != null ? at : at + 1, moving);
    return FakeResponse.json(moving);
  });
}

void notificationsFavoritesJourneys() =>
    group('Notifications and favorites', () {
      testWidgets('notifications: filter, mark unread, delete', (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installNotifications(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Team work');
        await waitFor(tester, find.text('Notifications'), api: api);
        await tapOn(tester, find.text('Notifications').last);
        await waitFor(tester, find.text('Supplier meeting at 3'), api: api);
        expect(find.text('1 unread'), findsOneWidget);

        await tapOn(tester, find.text('Read'));
        await waitFor(tester, find.text('Model budget at 80%'), api: api);
        expect(find.text('Supplier meeting at 3'), findsNothing);
        expect(api.log.last.query['state'], 'read');

        await tapOn(
            tester, find.byTooltip('More actions for Model budget at 80%'));
        await tapOn(tester, find.text('Mark unread'));
        await waitFor(tester, find.text('2 unread'), api: api);
        // It no longer matches Read, so it leaves the list.
        expect(find.text('Model budget at 80%'), findsNothing);

        await tapOn(tester, find.text('All'));
        await waitFor(tester, find.text('Model budget at 80%'), api: api);
        await tapOn(
            tester, find.byTooltip('More actions for Model budget at 80%'));
        await tapOn(tester, find.text('Delete'));
        await tapOn(tester, find.widgetWithText(FilledButton, 'Delete'));
        await waitFor(tester, find.text('Notification deleted.'), api: api);
        expect(find.text('Model budget at 80%'), findsNothing);
        expect(
            api.unmatched.where((r) => r.contains('/notifications')), isEmpty);
      });

      testWidgets('favorites: search, then move one up in my order',
          (tester) async {
        final api = FakeAnumApi();
        seedBaseline(api);
        installFavorites(api);
        await launchApp(tester, api);
        await signIn(tester, api);

        await openFromMenu(tester, api, 'Favorites');
        await waitFor(tester, find.text('Weekly summary'), api: api);
        expect(
            api.log
                .lastWhere((r) => r.path == '/api/v1/favorites')
                .query['sort'],
            'custom');

        await tapOn(tester, find.byTooltip('Arrange Weekly summary'));
        await tapOn(tester, find.text('Move up'));
        await waitUntil(tester, 'the move request',
            () => api.log.any((r) => r.path.endsWith('/fav_weekly/move')),
            api: api);
        final move =
            api.log.lastWhere((r) => r.path.endsWith('/fav_weekly/move'));
        expect(move.body, {'before_id': 'fav_supplier'});

        await tester.enterText(find.byType(TextField).first, 'supplier');
        await waitUntil(tester, 'the search request',
            () => api.log.any((r) => r.query['q'] == 'supplier'),
            api: api);
        await waitFor(tester, find.text('Supplier list'), api: api);
        expect(find.text('Tyre cost report'), findsNothing);
      });
    });
