// The spend breakdown and the alert threshold on Model budgets
// (docs/model-gateway.md#spend-breakdown, #budget-alerts): the request path,
// ranges, labels and shares, the controller's paging and CSV export, every
// state of the card, and layouts from 320 to 1024 wide at 2x text in English
// and Arabic.
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/api_downloads.dart';
import 'package:anum_mobile/data/api_models.dart';
import 'package:anum_mobile/features/admin/admin_controller.dart';
import 'package:anum_mobile/features/admin/admin_models.dart';
import 'package:anum_mobile/features/admin/admin_repository.dart';
import 'package:anum_mobile/features/admin/budget_usage.dart';
import 'package:anum_mobile/features/admin/budgets_screen.dart';
import 'package:anum_mobile/src/localization/admin_depth_strings.dart';
import 'package:anum_mobile/src/localization/anum_localizations.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../lists/lists_harness.dart';

const _en = AnumLocalizations(Locale('en'));
const _ar = AnumLocalizations(Locale('ar'));

JsonMap _row(String key, double cost, int tokens, {String? label}) => {
      'key': key,
      'label': label,
      'calls': 3,
      'input_tokens': tokens ~/ 2,
      'output_tokens': tokens - tokens ~/ 2,
      'total_tokens': tokens,
      'estimated_cost_usd': cost,
      'unpriced_calls': 0,
    };

JsonMap _overview() => {
      'period_start': '2026-11-01',
      'resets_on': '2026-12-01',
      'can_manage_tenant_budget': false,
      for (final scope in ['tenant', 'workspace'])
        scope: {
          'budget': scope == 'workspace'
              ? {
                  'scope': 'workspace',
                  'monthly_cost_limit_usd': 50.0,
                  'monthly_token_limit': null,
                  'alert_percent': 60,
                  'updated_at': '2026-11-02T10:00:00Z',
                  'updated_by': 'user_me'
                }
              : null,
          'usage': {
            'input_tokens': 10,
            'output_tokens': 5,
            'estimated_cost_usd': 1.5,
            'calls': 2,
            'unpriced_calls': 0
          },
          'total_tokens': 15,
          'exceeded': false,
        },
    };

/// The usage route: [rows] paged by offset and limit, as the API does.
class _Usage {
  _Usage(this.rows, {this.status = 200, this.recordedSince});
  List<JsonMap> rows;
  int status;
  String? recordedSince;

  ApiResponse answer(ApiRequest r) {
    if (status != 200) {
      return ApiResponse(statusCode: status, body: {
        'error': {
          'code': 'forbidden',
          'message': status == 403
              ? 'Workspace permission denied'
              : 'Database unavailable'
        }
      });
    }
    final q = r.uri.queryParameters;
    final offset = int.parse(q['offset'] ?? '0');
    final limit = int.parse(q['limit'] ?? '50');
    final page = rows.skip(offset).take(limit).toList();
    double cost = 0;
    var tokens = 0;
    for (final row in rows) {
      cost += row['estimated_cost_usd']! as double;
      tokens += row['total_tokens']! as int;
    }
    return ApiResponse(statusCode: 200, body: {
      'scope': q['scope'],
      'group_by': q['group_by'],
      'from_day': q['from'],
      'to_day': q['to'],
      'rows': page,
      'total': {
        'input_tokens': tokens ~/ 2,
        'output_tokens': tokens - tokens ~/ 2,
        'estimated_cost_usd': cost,
        'calls': rows.length * 3,
        'unpriced_calls': 0
      },
      'count': rows.length,
      'limit': limit,
      'offset': offset,
      'truncated': false,
      'recorded_since': recordedSince,
    });
  }
}

class _Downloads implements ApiDownloads {
  final calls = <String>[];
  bool saved = true;
  Object? error;

  @override
  Future<bool> save(String method, String path,
      {JsonMap? body,
      required String fileName,
      required String dialogTitle}) async {
    calls.add('$method $path $fileName');
    if (error != null) throw error!;
    return saved;
  }
}

Future<(AnumApiClient, ScriptedTransport, _Downloads)> _client(
    _Usage usage) async {
  late ScriptedTransport transport;
  transport = ScriptedTransport((r) {
    if (r.uri.path == '/api/v1/model-budgets/usage') return usage.answer(r);
    if (r.uri.path == '/api/v1/model-budgets') {
      return ApiResponse(statusCode: 200, body: _overview());
    }
    if (r.uri.path == '/api/v1/model-budgets/workspace') {
      final o = _overview();
      (o['workspace']! as JsonMap)['budget'] = {
        'scope': 'workspace',
        ...r.body!,
        'updated_at': '2026-11-03T10:00:00Z',
        'updated_by': 'user_me'
      };
      return ApiResponse(statusCode: 200, body: o);
    }
    return const ApiResponse(statusCode: 404, body: {'detail': 'Not found'});
  });
  final api = await scriptedClient(transport);
  final downloads = _Downloads();
  api.downloads = downloads;
  return (api, transport, downloads);
}

final _today = DateTime.utc(2026, 11, 11, 9);

final _page = find.byType(Scrollable).first;

void main() {
  group('strings', () {
    test('English and Arabic have the same keys and placeholders', () {
      expect(adminDepthAr.keys.toSet(), adminDepthEn.keys.toSet());
      final names = RegExp(r'\{(\w+)\}');
      Set<String> of(String text) =>
          names.allMatches(text).map((m) => m.group(1)!).toSet();
      for (final key in adminDepthEn.keys) {
        expect(of(adminDepthAr[key]!), of(adminDepthEn[key]!), reason: key);
        // Only the version label (v3) reads the same in both languages.
        if (key != 'ad.gov.packs.version') {
          expect(adminDepthAr[key], isNot(adminDepthEn[key]),
              reason: 'Arabic for $key is written, not copied');
        }
      }
      for (final text in [...adminDepthEn.values, ...adminDepthAr.values]) {
        expect(text.contains('–') || text.contains('—'), isFalse, reason: text);
      }
    });
  });

  group('requests and words', () {
    test('presets are UTC days, like the web', () {
      expect(rangeFor(UsagePreset.month, _today).fromDay, '2026-11-01');
      expect(rangeFor(UsagePreset.month, _today).toDay, '2026-11-11');
      expect(rangeFor(UsagePreset.last30, _today).fromDay, '2026-10-13');
      expect(rangeFor(UsagePreset.last3m, _today).fromDay, '2026-09-01');
      expect(rangeFor(UsagePreset.last12m, _today).fromDay, '2025-12-01');
      expect(
          UsageRange(DateTime.utc(2026, 1, 2), DateTime.utc(2026, 1, 1)).valid,
          isFalse);
      expect(
          UsageRange(DateTime.utc(2025, 1, 1), DateTime.utc(2026, 3, 1)).valid,
          isFalse);
    });

    test('the path carries group, scope, range and page, or the CSV format',
        () {
      final q = UsageQuery(
          group: UsageGroup.feature,
          scope: UsageScope.tenant,
          range: rangeFor(UsagePreset.month, _today));
      expect(q.path(offset: 50),
          '/api/v1/model-budgets/usage?group_by=feature&scope=tenant&from=2026-11-01&to=2026-11-11&limit=50&offset=50');
      expect(q.path(csv: true),
          '/api/v1/model-budgets/usage?group_by=feature&scope=tenant&from=2026-11-01&to=2026-11-11&format=csv');
      expect(q.csvFileName,
          'model-usage-tenant-feature-2026-11-01-2026-11-11.csv');
    });

    test('labels, shares and bars', () {
      final row = UsageRow.fromJson(JsonReader(_row('2026-11-03', 2, 100)));
      expect(usageRowLabel(UsageGroup.day, row, _en), '3 November 2026');
      expect(usageRowLabel(UsageGroup.day, row, _ar), '3 نوفمبر 2026');
      final month = UsageRow.fromJson(JsonReader(_row('2026-10-01', 1, 1)));
      expect(usageRowLabel(UsageGroup.month, month, _en), 'October 2026');
      final me = UsageRow.fromJson(JsonReader(_row('user_me', 1, 1)));
      expect(usageRowLabel(UsageGroup.user, me, _en, currentUserId: 'user_me'),
          'user_me (you)');
      final agent = UsageRow.fromJson(
          JsonReader(_row('agent_1', 1, 1, label: 'Sales helper')));
      expect(usageRowLabel(UsageGroup.agent, agent, _en), 'Sales helper');
      final voice = UsageRow.fromJson(JsonReader(_row('voice', 1, 1)));
      expect(usageRowLabel(UsageGroup.feature, voice, _ar), 'الصوت');
      final unknown = UsageRow.fromJson(JsonReader(_row('new_thing', 1, 1)));
      expect(usageRowLabel(UsageGroup.feature, unknown, _en), 'new_thing');
      final total = UsageRow.fromJson(JsonReader(_row('', 4, 400)));
      expect(shareOf(row, total, UsageMeasure.cost), 50);
      expect(shareOf(row, total, UsageMeasure.tokens), 25);
      final bars = barFractions(
          [row, UsageRow.fromJson(JsonReader(_row('x', 0.001, 1)))],
          UsageMeasure.cost);
      expect(bars, [1.0, 0.02]);
    });

    test('the alert threshold is checked and sent with the limits', () {
      final (limits, error) = parseLimits('25', '', alert: '60');
      expect(error, isNull);
      expect(limits!.toJson()['alert_percent'], 60);
      expect(parseLimits('25', '', alert: '100').$2, _en.t('ad.bud.err.alert'));
      expect(parseLimits('25', '', alert: '1.5').$2, _en.t('ad.bud.err.alert'));
      expect(parseLimits('', '', alert: '50').$2,
          _en.t('ad.bud.err.alertNeedsLimit'));
      expect(parseLimits('', '', alert: '50', l: _ar).$2,
          _ar.t('ad.bud.err.alertNeedsLimit'));
      final read = ModelBudget.fromJson(
          (_overview()['workspace']! as JsonMap)['budget']! as JsonMap);
      expect(read.limits.alertPercent, 60);
    });
  });

  group('controller', () {
    test('loads, pages with Load more and exports the range', () async {
      final usage = _Usage(
          [for (var i = 0; i < 60; i++) _row('user_$i', 60.0 - i, 1000 - i)]);
      final (api, transport, downloads) = await _client(usage);
      final c =
          BudgetUsageController(ApiAdminRepository(api), now: () => _today)
            ..group = UsageGroup.user;
      await c.load();
      expect(c.phase, UsagePhase.ready);
      expect(c.rows, hasLength(50));
      expect(c.hasMore, isTrue);
      await c.more();
      expect(c.rows, hasLength(60));
      expect(c.hasMore, isFalse);
      expect(transport.requests.last.uri.queryParameters['offset'], '50');
      await c.export(_en);
      expect(downloads.calls.single,
          startsWith('GET /api/v1/model-budgets/usage?group_by=user'));
      expect(downloads.calls.single, contains('format=csv'));
      expect(c.exportNotice, 'The CSV was saved.');
      downloads.saved = false;
      await c.export(_en);
      expect(c.exportNotice, 'The CSV was not saved.');
      downloads.error = const ApiException(403, 'Workspace permission denied');
      await c.export(_en);
      expect(c.exportFailed, isTrue);
      expect(c.exportNotice, contains('Workspace permission denied'));
      c.dispose();
    });

    test('a 403 is denied, a failure is an error; changing reloads', () async {
      final usage = _Usage([], status: 403);
      final (api, transport, _) = await _client(usage);
      final c =
          BudgetUsageController(ApiAdminRepository(api), now: () => _today);
      await c.load();
      expect(c.phase, UsagePhase.denied);
      usage.status = 503;
      await c.load();
      expect(c.phase, UsagePhase.error);
      expect(c.error, 'Database unavailable');
      usage.status = 200;
      await c.setScope(UsageScope.tenant);
      expect(c.phase, UsagePhase.ready);
      expect(transport.requests.last.uri.queryParameters['scope'], 'tenant');
      expect(
          await c.setCustom(
              UsageRange(DateTime.utc(2026, 5, 2), DateTime.utc(2026, 5, 1))),
          isFalse);
      expect(
          await c.setCustom(
              UsageRange(DateTime.utc(2026, 5, 1), DateTime.utc(2026, 5, 31))),
          isTrue);
      expect(transport.requests.last.uri.queryParameters['from'], '2026-05-01');
      c.dispose();
    });
  });

  group('screen', () {
    Future<(BudgetsController, ScriptedTransport)> pump(
        WidgetTester tester, _Usage usage,
        {double width = 390, double text = 1, String locale = 'en'}) async {
      final (api, transport, _) = await _client(usage);
      final controller = BudgetsController(ApiAdminRepository(api));
      // A fresh tree: the screen loads its controller in initState.
      await tester.pumpWidget(const SizedBox());
      await pumpList(tester,
          BudgetsScreen(controller: controller, currentUserId: 'user_me'),
          width: width,
          height: 2400,
          text: text,
          locale: locale,
          scaffold: false);
      return (controller, transport);
    }

    testWidgets('rows, total, share, Load more and the alert field',
        (tester) async {
      final usage = _Usage(
          [for (var i = 0; i < 55; i++) _row('user_$i', 1, 10)],
          recordedSince: '2026-11-05');
      final (controller, transport) = await pump(tester, usage);
      await tester.scrollUntilVisible(
          find.byKey(const Key('budget-usage')), 300,
          scrollable: _page);
      expect(find.text('Where the spend goes'), findsOneWidget);
      expect(find.byKey(const Key('usage-total')), findsOneWidget);
      expect(find.byKey(const Key('usage-recorded-since')), findsOneWidget);
      expect(find.text('Showing 50 of 55 groups.'), findsOneWidget);
      await tester.scrollUntilVisible(find.byKey(const Key('usage-more')), 300,
          scrollable: _page);
      await tester.ensureVisible(find.byKey(const Key('usage-more')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('usage-more')));
      await tester.pumpAndSettle();
      expect(find.text('Showing 55 of 55 groups.'), findsOneWidget);
      expect(find.byKey(const Key('usage-more')), findsNothing);
      // The alert threshold the web set is shown and saved again.
      expect(find.text('Owners are also alerted at 60%.'), findsOneWidget);
      await tester.ensureVisible(find.byKey(const Key('workspace-alert')));
      await tester.enterText(find.byKey(const Key('workspace-alert')), '150');
      await tester.ensureVisible(find.text('Save limits').last);
      await tester.tap(find.text('Save limits').last);
      await tester.pumpAndSettle();
      expect(find.text(_en.t('ad.bud.err.alert')), findsOneWidget);
      await tester.enterText(find.byKey(const Key('workspace-alert')), '70');
      await tester.ensureVisible(find.text('Save limits').last);
      await tester.tap(find.text('Save limits').last);
      await tester.pumpAndSettle();
      expect(
          transport.requests
              .lastWhere((r) => r.method == 'PUT')
              .body!['alert_percent'],
          70);
      controller.dispose();
    });

    testWidgets('empty offers a longer period; denied and error say why',
        (tester) async {
      final usage = _Usage([]);
      final (controller, transport) = await pump(tester, usage);
      await tester.scrollUntilVisible(find.byKey(const Key('usage-empty')), 300,
          scrollable: _page);
      expect(find.text('No model calls in this period'), findsOneWidget);
      await tester.ensureVisible(find.text('Show the last 12 months'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Show the last 12 months'));
      await tester.pumpAndSettle();
      expect(transport.requests.last.uri.queryParameters['from'],
          isNot('2026-11-01'));
      usage.status = 403;
      await controller.usage.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('usage-denied')), findsOneWidget);
      usage.status = 503;
      await controller.usage.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('usage-error')), findsOneWidget);
      usage
        ..status = 200
        ..rows = [_row('2026-11-03', 1, 10)];
      await tester.ensureVisible(find.text('Try again'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('usage-row-2026-11-03')), findsOneWidget);
      controller.dispose();
    });

    for (final width in [320.0, 390.0, 768.0, 1024.0]) {
      testWidgets('lays out at $width wide, 2x text, English and Arabic',
          (tester) async {
        for (final locale in ['en', 'ar']) {
          final usage = _Usage([
            _row('tasks', 12.5, 120000),
            _row('knowledge', 2.25, 4000),
          ]);
          final (controller, _) =
              await pump(tester, usage, width: width, text: 2, locale: locale);
          await controller.usage.setGroup(UsageGroup.feature);
          await tester.pumpAndSettle();
          await tester.scrollUntilVisible(
              find.byKey(const Key('usage-export')), 300,
              scrollable: _page);
          expect(tester.takeException(), isNull);
          if (locale == 'ar') {
            expect(find.text('أين يذهب الإنفاق'), findsOneWidget);
            expect(find.text('المهام وتشغيل الوكلاء'), findsOneWidget);
          }
          await tester.pumpWidget(const SizedBox());
          controller.dispose();
        }
      });
    }
  });
}
