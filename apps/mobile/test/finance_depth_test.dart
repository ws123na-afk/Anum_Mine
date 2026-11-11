// Finance settings, the emergency reserve form, the exception notices, the
// card FX section and the attribution suggestions on the phone
// (docs/finance.md#finance-settings, #emergency-reserve, #card-fx-and-fees,
// #attribution-suggestions, #exception-notices), against a fake API with the
// API's JSON shapes. Fixtures are plainly fake.
import 'dart:convert';

import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/features/finance/finance_controller.dart';
import 'package:anum_mobile/features/finance/finance_depth_models.dart';
import 'package:anum_mobile/features/finance/finance_depth_strings.dart';
import 'package:anum_mobile/features/finance/finance_depth_view.dart';
import 'package:anum_mobile/features/finance/finance_review_models.dart';
import 'package:anum_mobile/features/finance/finance_screen.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'finance_test.dart' show FakeFinance;
import 'records/records_harness.dart';

const base = '/api/v1/finance';

Map<String, Object?> reserveJson({bool drift = false}) => {
      'reserves': [
        {
          'goal_id': 'fin_goal_1',
          'name': 'Rainy day',
          'currency': 'AED',
          'mode': 'months',
          'months': 3,
          'target': '9000.00',
          'recalculated_target': drift ? '12000.00' : '9000.00',
          'essential_monthly': drift ? '4000.00' : '3000.00',
          'essential_basis': 'essential',
          'saved': '4500.00',
          'progress_percent': 50,
          'months_covered': '1.5',
          'held_in': 'goal',
          'account_id': null,
          'account_name': null,
          'paused': false,
          'monthly_contribution': '500.00',
          'status': 'building',
        }
      ],
      'suggestions': [
        {
          'currency': 'USD',
          'essential_monthly': '1000.50',
          'essential_basis': 'essential',
          'months': 3,
          'target': '3001.50',
        }
      ],
      'default_months': 3,
      'note': 'example',
    };

Map<String, Object?> fxJson(
        {String split = 'available',
        bool canCorrect = true,
        bool rate = true,
        String feeSource = 'markup_stated'}) =>
    {
      'transaction_id': 'fin_txn_1',
      'foreign': true,
      'original_amount': '20.00',
      'original_currency': 'USD',
      'billed_amount': '76.80',
      'billed_currency': 'AED',
      'purchase_amount': '75.00',
      'rate': rate ? '3.750000' : null,
      'rate_kind': rate ? 'printed' : 'not_stated',
      'rate_date': rate ? '2026-11-01' : null,
      'rate_source': rate ? 'statement' : null,
      'fee': rate
          ? {
              'amount': '1.80',
              'currency': 'AED',
              'source': feeSource,
              'transaction_id': null,
              'markup_percent': '2.4',
              'included_in_billed': true,
              'linked': false,
            }
          : null,
      'split': rate ? split : 'none',
      'notes': rate ? <String>[] : ['rate_not_stated'],
      'plan_hash': 'a' * 64,
      'can_correct': canCorrect,
      'transfer': null,
    };

class DepthApi {
  DepthApi() {
    api = FakeApi(route);
  }
  late final FakeApi api;
  Map<String, Object?> policy = {
    'auto_classify': false,
    'min_confidence': '0.9',
    'sources': ['rule'],
    'notify_unresolved': false,
    'notify_min_items': 1,
    'notices': <String>[],
    'notice_exception_days': 7,
    'notice_uncategorized_min': '500',
  };
  Map<String, Object?> fx = fxJson();
  Map<String, Object?> transfer = {'paired': false};
  bool deny = false;

  Map<String, Object?> _body(ApiRequest r) => r.body == null
      ? const {}
      : jsonDecode(jsonEncode(r.body)) as Map<String, Object?>;

  ApiResponse? route(ApiRequest r) {
    if (deny) return refuse(403, 'Workspace permission denied');
    final path = r.uri.path.replaceFirst(base, '');
    return switch ((r.method, path)) {
      ('GET', '/settings') => ok({
          'base_currency': 'AED',
          'timezone': 'Asia/Dubai',
          'category_rules': [
            {'match': 'gym', 'category': 'health'}
          ],
          'updated_at': null,
        }),
      ('PUT', '/settings') => ok({..._body(r), 'updated_at': null}),
      ('GET', '/plan') => ok({
          'buffers': [
            {'currency': 'AED', 'amount': '1000.00'}
          ],
          'combine_currencies': false,
          'fx_max_age_days': 7,
          'updated_at': null,
        }),
      ('PUT', '/plan') => ok({..._body(r), 'updated_at': null}),
      ('GET', '/reserve') => ok(reserveJson()),
      ('GET', '/goals') => okList([
          {
            'id': 'fin_goal_1',
            'name': 'Rainy day',
            'target_on': '2027-06-30',
            'notes': 'example',
          }
        ]),
      ('PATCH', '/goals/fin_goal_1') ||
      ('POST', '/goals') =>
        ok({'id': 'fin_goal_1'}),
      ('GET', '/automation') =>
        ok({'policy': policy, 'updated_at': null, 'never_automated': []}),
      ('PUT', '/automation') => () {
          policy = _body(r);
          return ok(
              {'policy': policy, 'updated_at': null, 'never_automated': []});
        }(),
      ('GET', '/transactions/fin_txn_1/fx') => ok(fx),
      ('POST', '/transactions/fin_txn_1/fx/separate') => () {
          fx = fxJson(split: 'done');
          return ok({
            'purchase': fx,
            'fee_transaction_id': 'fin_txn_9',
            'mode': 'split'
          });
        }(),
      ('GET', '/links/transfer') => ok(transfer),
      ('POST', '/links/fin_link_1/void') => () {
          transfer = {'paired': false};
          return ok({'id': 'fin_link_1', 'status': 'void'});
        }(),
      _ => null,
    };
  }

  Map<String, Object?> last(String method, String path) =>
      _body(api.sent(method, '$base$path').last);
}

FinanceController controller(DepthApi d, {FakeFinance? finance}) =>
    FinanceController(finance ?? FakeFinance(),
        depthRepository: ApiFinanceDepthRepository(d.api.client()));

Future<void> openSettings(WidgetTester tester, FinanceController c,
    {Size size = phone, Locale locale = const Locale('en')}) async {
  await pumpModule(tester, FinanceScreen(controller: c, webAppUrl: ''),
      size: size, locale: locale);
  await c.select(FinanceSection.settings);
  await tester.pumpAndSettle();
}

void main() {
  group('models', () {
    test('catalogs match, with no long dashes', () {
      expect(FinanceDepthStrings.catalogsMatch, isTrue);
      for (final text in FinanceDepthStrings.allTexts) {
        expect(text.contains('—') || text.contains('–'), isFalse, reason: text);
      }
    });

    test('months target is exact and drift follows the web rule', () {
      expect(monthsTarget('1000.50', 3), '3001.50');
      expect(monthsTarget('0.05', 2), '0.10');
      expect(monthsTarget(null, 3), isNull);
      expect(monthsTarget('100', 25), isNull);
      final book = ReserveBook.fromJson(JsonReader(reserveJson(drift: true)));
      expect(book.reserveIn('AED')!.drifted, isTrue);
      expect(
          ReserveBook.fromJson(JsonReader(reserveJson()))
              .reserveIn('AED')!
              .drifted,
          isFalse);
      expect(book.suggestionIn('USD')!.target, '3001.50');
    });

    test('FX: actions, rate never guessed, plain rate', () {
      final split = FxDetails.fromJson(JsonReader(fxJson()));
      expect(split.action, 'split');
      expect(
          FxDetails.fromJson(JsonReader(fxJson(split: 'link'))).action, 'link');
      expect(FxDetails.fromJson(JsonReader(fxJson(canCorrect: false))).action,
          isNull);
      final billedOnly = FxDetails.fromJson(JsonReader(fxJson(rate: false)));
      expect(billedOnly.rateNotStated, isTrue);
      expect(billedOnly.action, isNull);
      expect(plainRate('3.750000'), '3.75');
      expect(plainRate(null), '-');
    });

    test('attribution suggestions accept as links', () {
      final s = Suggestion.fromJson(JsonReader({
        'key': 'attribution:debt:t1:d1',
        'kind': 'attribution',
        'why': '',
        'params': {'dimension': 'debt', 'debt': 'Car loan'},
        'action': {
          'kind': 'link',
          'transaction_ids': ['t1'],
          'link': {'canonical_id': 'c1'}
        },
        'payload_hash': 'h',
      }));
      expect((s.acceptMode, s.variant), ('link', 'debt'));
      expect(s.transactionIds, ['t1']);
    });

    test('a vehicle record proposed from its scanned receipt has its words',
        () {
      Suggestion vehicle(Map<String, Object?> params) =>
          Suggestion.fromJson(JsonReader({
            'key': 'attribution:vehicle:t1:f1',
            'kind': 'attribution',
            'why': '',
            'params': {'dimension': 'vehicle', ...params},
            'action': {
              'kind': 'link',
              'transaction_ids': ['t1'],
              'link': {'canonical_id': 'c1'}
            },
            'payload_hash': 'h',
          }));
      expect(
          vehicle({'record': 'vehicle_fuel_entry', 'source': 'receipt'})
              .variant,
          'vehicle_receipt');
      expect(vehicle({'record': 'vehicle_fuel_entry'}).variant, 'vehicle');
      expect(vehicle({'record': 'none'}).variant, 'vehicle_record');
    });
  });

  group('Finance settings', () {
    testWidgets('loads every card and saves currency, buffer and reserve',
        (tester) async {
      final d = DepthApi();
      final c = controller(d);
      await openSettings(tester, c);
      expect(find.text('Asia/Dubai'), findsWidgets);
      expect(find.byKey(const Key('res-of-AED')), findsOneWidget);
      expect(find.text('Covers 1.5 months of essentials'), findsOneWidget);
      expect(find.byKey(const Key('res-USD')), findsOneWidget);
      expectClean(tester);

      await tapVisible(tester, find.byKey(const Key('set-general-edit')));
      await tester.enterText(find.byKey(const Key('field-currency')), 'sar');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final settings = d.last('PUT', '/settings');
      expect(settings['base_currency'], 'SAR');
      expect(settings['category_rules'], [
        {'match': 'gym', 'category': 'health'}
      ]);
      await clearToasts(tester);

      await tapVisible(tester, find.byKey(const Key('set-buffer-add')));
      await tester.enterText(find.byKey(const Key('field-currency')), 'USD');
      await tester.enterText(find.byKey(const Key('field-amount')), '200');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(d.last('PUT', '/plan')['buffers'], [
        {'currency': 'AED', 'amount': '1000.00'},
        {'currency': 'USD', 'amount': '200'},
      ]);
      await clearToasts(tester);

      await tapVisible(tester, find.byKey(const Key('res-edit-AED')));
      await tester.enterText(find.byKey(const Key('field-months')), '4');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final goal = d.last('PATCH', '/goals/fin_goal_1');
      expect(goal['target_amount'], '12000.00');
      expect(goal['reserve_months'], 4);
      expect(goal['purpose'], 'emergency');
      expect(goal['target_on'], '2027-06-30');
      expect(goal['name'], 'Rainy day');
    });

    testWidgets('a reserve month count out of range is refused on the phone',
        (tester) async {
      final d = DepthApi();
      await openSettings(tester, controller(d));
      await tapVisible(tester, find.byKey(const Key('res-edit-USD')));
      await tester.enterText(find.byKey(const Key('field-months')), '30');
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      expect(find.text('Choose 1 to 24 months.'), findsOneWidget);
      expect(d.api.sent('POST', '$base/goals'), isEmpty);
    });

    testWidgets('exception notices are turned on and every switch kept',
        (tester) async {
      final d = DepthApi();
      await openSettings(tester, controller(d));
      await tapVisible(tester, find.byKey(const Key('notices-edit')));
      await tapVisible(tester, find.byKey(const Key('field-evidence_missing')));
      await tapVisible(tester, find.byKey(const Key('form-submit')));
      final body = d.last('PUT', '/automation');
      expect(body['notices'], ['evidence_missing']);
      expect(body['min_confidence'], '0.9');
      expect(body['notice_exception_days'], 7);
      await tester.pumpAndSettle();
      expect(
          find.descendant(
              of: find.byKey(const Key('notice-evidence_missing')),
              matching: find.text('On')),
          findsOneWidget);
    });

    testWidgets('a viewer is told who can change the settings', (tester) async {
      final d = DepthApi()..deny = true;
      await openSettings(tester, controller(d));
      expect(find.byKey(const Key('records-denied')), findsWidgets);
      expectClean(tester);
    });

    for (final size in [phone, const Size(768, 1024)]) {
      for (final locale in [const Locale('en'), const Locale('ar')]) {
        testWidgets('lays out at $size in ${locale.languageCode}',
            (tester) async {
          await openSettings(tester, controller(DepthApi()),
              size: size, locale: locale);
          expectClean(tester);
        });
      }
    }
  });

  group('card FX section', () {
    Future<void> openFx(WidgetTester tester, DepthApi d,
        {Size size = phone, Locale locale = const Locale('en')}) async {
      await pumpModule(
          tester,
          TransactionScreen(
              repository: ApiFinanceDepthRepository(d.api.client()),
              transactionId: 'fin_txn_1'),
          size: size,
          locale: locale);
    }

    testWidgets('separates the fee with the plan hash shown', (tester) async {
      final d = DepthApi();
      await openFx(tester, d);
      expect(find.textContaining('3.75'), findsOneWidget);
      await tapVisible(tester, find.byKey(const Key('fx-split')));
      await tapVisible(tester, find.byKey(const Key('fx-confirm')));
      expect(d.last('POST', '/transactions/fin_txn_1/fx/separate'),
          {'plan_hash': 'a' * 64});
      expect(
          find.text('Fee recorded as its own bank fee line'), findsOneWidget);
      expect(find.byKey(const Key('fx-split')), findsNothing);
    });

    testWidgets('shows the own transfer pair and unpairs it', (tester) async {
      final d = DepthApi()
        ..transfer = {
          'paired': true,
          'link_id': 'fin_link_1',
          'link_version': 2,
          'role': 'out',
          'other': {
            'transaction_id': 'fin_txn_2',
            'account_id': 'card',
            'account_name': 'Card',
            'posted_on': '2026-11-05',
            'amount': '1000.00',
            'currency': 'AED',
            'description': 'EXAMPLE PAYMENT',
          },
        };
      await openFx(tester, d);
      expect(find.byKey(const Key('transfer-pair')), findsOneWidget);
      expect(find.textContaining('Card'), findsWidgets);
      await tapVisible(tester, find.byKey(const Key('transfer-unpair')));
      await tapVisible(
          tester, find.byKey(const Key('transfer-unpair-confirm')));
      expect(d.last('POST', '/links/fin_link_1/void'), {'version': 2});
      expect(find.byKey(const Key('transfer-pair')), findsNothing);
      expect(
          find.text('Unpaired. Both rows stay as they are.'), findsOneWidget);
    });

    testWidgets('link the fee row', (tester) async {
      final d = DepthApi()..fx = fxJson(split: 'link', feeSource: 'fee_row');
      await openFx(tester, d);
      expect(find.byKey(const Key('fx-link')), findsOneWidget);
    });

    testWidgets('only a billed amount says rate not stated', (tester) async {
      final d = DepthApi()..fx = fxJson(rate: false);
      await openFx(tester, d);
      expect(find.text('rate not stated'), findsOneWidget);
      expect(find.byKey(const Key('fx-split')), findsNothing);
    });

    testWidgets('an SMS row is corrected under Review, not here',
        (tester) async {
      final d = DepthApi()..fx = fxJson(canCorrect: false);
      await openFx(tester, d);
      expect(find.byKey(const Key('fx-split')), findsNothing);
      expect(find.textContaining('iPhone SMS, Review'), findsOneWidget);
    });

    testWidgets('a failure says what failed and retries', (tester) async {
      final d = DepthApi();
      d.api.failWith = 503;
      await openFx(tester, d);
      expect(find.byKey(const Key('fx-error')), findsOneWidget);
      d.api.failWith = null;
      await tapVisible(tester, find.text('Retry').first);
      expect(find.byKey(const Key('fx-section')), findsOneWidget);
    });

    for (final size in [phone, const Size(768, 1024)]) {
      testWidgets('Arabic at $size', (tester) async {
        await openFx(tester, DepthApi(),
            size: size, locale: const Locale('ar'));
        expectClean(tester);
      });
    }
  });

  group('attribution suggestions', () {
    testWidgets(
        'Link goes through the API with the payload hash; FX Review opens the transaction',
        (tester) async {
      final finance = FakeFinance()
        ..suggestionItems = [
          {
            'key': 'attribution:debt:t1:d1',
            'producer': 'attribution',
            'kind': 'attribution',
            'detected': 'x',
            'why': 'x',
            'params': {'dimension': 'debt', 'debt': 'Car loan'},
            'evidence': <Object?>[],
            'confidence': '0.85',
            'effect': [
              {'amount': '500.00', 'currency': 'AED', 'direction': 'out'}
            ],
            'action': {
              'kind': 'link',
              'transaction_ids': ['t1'],
              'link': {'canonical_id': 'c1', 'dimension': 'debt'}
            },
            'payload_hash': 'hash_debt',
            'href': '#finance-debts',
            'occurred_on': '2026-11-01',
          },
          {
            'key': 'fx_fee:fin_txn_1:markup_stated:1.80',
            'producer': 'card_fx',
            'kind': 'fx_fee',
            'detected': 'x',
            'why': 'x',
            'params': {
              'fee': '1.80',
              'currency': 'AED',
              'source': 'markup_stated'
            },
            'evidence': <Object?>[],
            'confidence': '0.9',
            'effect': <Object?>[],
            'action': {
              'kind': 'open',
              'transaction_ids': ['fin_txn_1']
            },
            'payload_hash': 'hash_fx',
            'href': '#finance-transactions?txn=fin_txn_1',
            'occurred_on': '2026-11-01',
          },
        ];
      final d = DepthApi();
      final c = controller(d, finance: finance);
      await pumpModule(tester, FinanceScreen(controller: c, webAppUrl: ''));
      await c.select(FinanceSection.suggestions);
      await tester.pumpAndSettle();
      expect(find.text('A payment of Car loan?'), findsOneWidget);
      expect(find.text('Separate the bank FX fee of AED 1.80'), findsOneWidget);
      await tapVisible(
          tester, find.byKey(const Key('sug-accept-attribution:debt:t1:d1')));
      expect(finance.requests,
          contains('accept attribution:debt:t1:d1 hash_debt'));
      expect(find.textContaining('Linked.'), findsOneWidget);
      await clearToasts(tester);
      await tapVisible(
          tester,
          find.byKey(
              const Key('sug-review-fx_fee:fin_txn_1:markup_stated:1.80')));
      expect(find.byKey(const Key('fx-section')), findsOneWidget);
    });
  });
}
