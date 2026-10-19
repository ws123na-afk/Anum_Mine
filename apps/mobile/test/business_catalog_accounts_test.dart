import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/features/business/accounts_data.dart';
import 'package:anum_mobile/features/business/accounts_screen.dart';
import 'package:anum_mobile/features/business/business_controller.dart';
import 'package:anum_mobile/features/business/business_models.dart';
import 'package:anum_mobile/features/business/business_screen.dart';
import 'package:anum_mobile/features/business/catalog_data.dart';
import 'package:anum_mobile/features/business/catalog_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show FakeBusiness, app;

// Business catalog, accounts and contacts on the phone (docs/business.md):
// create, edit and delete with the server's own permission answers, every
// list state, paging, Arabic and phone widths. Wire shapes as
// /api/v1/business sends them.

Map<String, Object?> offerJson(int version, String status,
        {double price = 5000, double hours = 10, double rate = 100}) =>
    {
      'id': 'boffer_$version',
      'product_id': 'bprod_1',
      'version': version,
      'status': status,
      'currency': 'SAR',
      'price_type': 'fixed',
      'price_min': price,
      'price_max': null,
      'deliverables': ['Fleet dashboard'],
      'limitations': ['Up to 50 vehicles'],
      'cost': {
        'labor_hours': hours,
        'hourly_cost': rate,
        'external_costs': 0.0,
        'contingency_pct': 0.0,
      },
      'notes': '',
      'estimated_cost': hours * rate,
      'margin_at_price': price >= hours * rate ? price - hours * rate : null,
      'margin_negative': price < hours * rate,
    };

Map<String, Object?> productJson(int i,
        {List<Map<String, Object?>> offers = const []}) =>
    {
      'id': 'bprod_$i',
      'kind': 'service_package',
      'name': 'Reporting Quick Win $i',
      'name_ar': '',
      'summary': 'A fleet report in a week.',
      'maturity': 'live',
      'integrations': ['Excel'],
      'versions': offers.length,
      'active_offer': offers
          .where((o) => o['status'] == 'active')
          .cast<Map<String, Object?>?>()
          .firstOrNull,
      'offers': offers,
    };

class FakeCatalog implements CatalogRepository {
  FakeCatalog({this.manage = true, this.total = 25, this.deny = false});
  final bool manage, deny;
  final int total;
  final calls = <String>[];
  final bodies = <Map<String, Object?>>[];
  var offers = <Map<String, Object?>>[offerJson(1, 'active')];
  String? refuse;

  void need() {
    if (!manage) {
      throw const ApiException(
          403, 'Only workspace owners can change the catalog.');
    }
    if (refuse != null) throw ApiException(409, refuse!);
  }

  CatalogProduct detail() =>
      CatalogProduct.fromJson(JsonReader.of(productJson(1, offers: offers)));

  @override
  Future<BusinessPage<CatalogProduct>> products({
    String? query,
    String? kind,
    String? maturity,
    required int offset,
    int limit = 20,
  }) async {
    calls.add('list $query $maturity $offset');
    if (deny) throw const ApiException(403, 'Business is for owners.');
    final count = (total - offset).clamp(0, limit);
    return BusinessPage([
      for (var i = 0; i < count; i++)
        CatalogProduct.fromJson(JsonReader.of(
            productJson(offset + i, offers: i == 0 ? offers : const [])))
    ], total);
  }

  @override
  Future<CatalogProduct> product(String id) async {
    calls.add('get $id');
    return detail();
  }

  @override
  Future<CatalogProduct> createProduct(Map<String, Object?> fields) async {
    need();
    calls.add('create');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<CatalogProduct> updateProduct(
      String id, Map<String, Object?> fields) async {
    need();
    calls.add('update $id');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<void> deleteProduct(String id) async {
    need();
    calls.add('delete $id');
  }

  @override
  Future<CatalogProduct> addVersion(
      String id, Map<String, Object?> fields) async {
    need();
    calls.add('addVersion $id');
    bodies.add(fields);
    offers = [offerJson(2, 'draft', price: 4000), ...offers];
    return detail();
  }

  @override
  Future<CatalogProduct> updateVersion(
      String offerId, Map<String, Object?> fields) async {
    need();
    calls.add('updateVersion $offerId');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<CatalogProduct> activateVersion(String offerId) async {
    need();
    calls.add('activate $offerId');
    offers = [
      offerJson(2, 'active', price: 4000),
      offerJson(1, 'retired'),
    ];
    return detail();
  }

  @override
  Future<CatalogProduct> retireVersion(String offerId) async {
    need();
    calls.add('retire $offerId');
    offers = [
      for (final o in offers)
        if (o['id'] != offerId) o
    ];
    return detail();
  }
}

Map<String, Object?> contactJson(int i, {String consent = 'consent_given'}) => {
      'id': 'bcont_$i',
      'account_id': 'bacct_1',
      'name': 'Sara Khan $i',
      'role_title': 'Operations lead',
      'email': 'sara$i@gulfmixers.example',
      'phone': null,
      'profile_url': null,
      'consent_basis': consent,
      'consent_note': '',
      'do_not_contact': false,
      'dnc_reason': '',
      'opted_out': <String>[],
      'block_reason': consent == 'unknown' ? 'Consent basis is unknown.' : null,
      'contactable': consent != 'unknown',
    };

class FakeAccounts implements AccountsRepository {
  FakeAccounts({this.total = 25, this.deny = false});
  final int total;
  final bool deny;
  final calls = <String>[];
  final bodies = <Map<String, Object?>>[];
  bool dnc = false;
  var contacts = <Map<String, Object?>>[contactJson(1)];
  String? refuse;

  BusinessAccount detail() => BusinessAccount.fromJson(JsonReader.of({
        'id': 'bacct_1',
        'name': 'Gulf Mixers',
        'website': 'https://gulfmixers.example',
        'industry': 'Construction',
        'country': 'Saudi Arabia',
        'city': 'Riyadh',
        'notes': '',
        'do_not_contact': dnc,
        'dnc_reason': dnc ? 'Asked us to stop' : '',
        'contacts': contacts,
        'leads': [
          {'id': 'blead_1', 'title': 'Fleet reporting', 'state': 'qualified'}
        ],
      }));

  void maybeRefuse() {
    if (refuse != null) throw ApiException(409, refuse!);
  }

  @override
  Future<BusinessPage<BusinessAccount>> accounts({
    String? query,
    required int offset,
    int limit = 20,
  }) async {
    calls.add('list $query $offset');
    if (deny) throw const ApiException(403, 'Business is for owners.');
    final count = (total - offset).clamp(0, limit);
    return BusinessPage([
      for (var i = 0; i < count; i++)
        BusinessAccount.fromJson(JsonReader.of({
          'id': 'bacct_${offset + i + 1}',
          'name': 'Company ${offset + i}',
          'website': null,
          'industry': 'Construction',
          'country': 'Saudi Arabia',
          'city': 'Riyadh',
          'notes': '',
          'do_not_contact': false,
          'dnc_reason': '',
          'contacts': 2,
          'open_leads': 1,
        }))
    ], total);
  }

  @override
  Future<BusinessAccount> account(String id) async {
    calls.add('get $id');
    return detail();
  }

  @override
  Future<BusinessAccount> createAccount(Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('create');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<BusinessAccount> updateAccount(
      String id, Map<String, Object?> fields) async {
    calls.add('update $id');
    bodies.add(fields);
    return detail();
  }

  @override
  Future<void> deleteAccount(String id) async {
    maybeRefuse();
    calls.add('delete $id');
  }

  @override
  Future<BusinessAccount> accountDnc(String id, bool on, String reason) async {
    calls.add('accountDnc $on $reason');
    dnc = on;
    return detail();
  }

  @override
  Future<BusinessAccount> addContact(
      String accountId, Map<String, Object?> fields) async {
    maybeRefuse();
    calls.add('addContact');
    bodies.add(fields);
    contacts = [...contacts, contactJson(2, consent: 'unknown')];
    return detail();
  }

  @override
  Future<AccountContact> updateContact(
      String id, Map<String, Object?> fields) async {
    calls.add('updateContact $id');
    bodies.add(fields);
    return AccountContact.fromJson(JsonReader.of(contactJson(1)));
  }

  @override
  Future<AccountContact> contactDnc(String id, bool on, String reason) async {
    calls.add('contactDnc $id $on');
    return AccountContact.fromJson(JsonReader.of(contactJson(1)));
  }

  @override
  Future<void> deleteContact(String id) async {
    calls.add('deleteContact $id');
    contacts = [
      for (final c in contacts)
        if (c['id'] != id) c
    ];
  }
}

Future<void> phone(WidgetTester tester, {double width = 390}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = Size(width, 844);
  addTearDown(tester.view.reset);
}

Widget catalogPage(CatalogController c,
        {bool manage = true, Locale locale = const Locale('en')}) =>
    app(
      Scaffold(
        body: SingleChildScrollView(
          child: CatalogView(controller: c, canManage: manage),
        ),
      ),
      locale: locale,
    );

Widget accountsPage(AccountsController c,
        {bool write = true,
        bool manage = true,
        Locale locale = const Locale('en')}) =>
    app(
      Scaffold(
        body: SingleChildScrollView(
          child:
              AccountsView(controller: c, canWrite: write, canManage: manage),
        ),
      ),
      locale: locale,
    );

Future<void> enter(WidgetTester tester, String key, String text) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(Key(key)), text);
}

Future<void> tap(WidgetTester tester, String key) async {
  await tester.ensureVisible(find.byKey(Key(key)));
  await tester.pumpAndSettle();
  await tester.tap(find.byKey(Key(key)));
  await tester.pumpAndSettle();
}

void main() {
  group('catalog', () {
    testWidgets('lists products, pages and filters by maturity',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog();
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      expect(find.text('Reporting Quick Win 0'), findsOneWidget);
      expect(find.textContaining('Version 1'), findsOneWidget);
      expect(find.text('Load more (20 of 25)'), findsOneWidget);
      await tap(tester, 'desk-more');
      expect(repo.calls, contains('list null null 20'));
      expect(find.text('Reporting Quick Win 24'), findsOneWidget);
      await tap(tester, 'cat-maturity-pilot');
      expect(repo.calls.last, 'list null pilot 0');
      expect(tester.takeException(), isNull);
    });

    testWidgets('shows loading, empty, error and permission states',
        (tester) async {
      await phone(tester);
      final empty = CatalogController(FakeCatalog(total: 0));
      await tester.pumpWidget(catalogPage(empty));
      await empty.load();
      await tester.pumpAndSettle();
      expect(find.text('The catalog is empty'), findsOneWidget);
      expect(find.byKey(const Key('cat-new')), findsOneWidget);

      final denied = CatalogController(FakeCatalog(deny: true));
      await tester.pumpWidget(catalogPage(denied));
      await denied.load();
      await tester.pumpAndSettle();
      expect(
          find.textContaining('workspace owners and members'), findsOneWidget);

      final member = CatalogController(FakeCatalog(total: 0, manage: false));
      await tester.pumpWidget(catalogPage(member, manage: false));
      await member.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('cat-new')), findsNothing);
      expect(find.text('An owner has not added any products or packages yet.'),
          findsOneWidget);
    });

    testWidgets('an owner creates a product with the right fields',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog();
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tap(tester, 'cat-new');
      await tap(tester, 'desk-save');
      expect(
          find.text('Enter a name of at least 2 characters.'), findsOneWidget);
      await enter(tester, 'product-name', 'Telematics Pilot');
      await enter(tester, 'product-integrations', 'Excel, Zoho ,');
      await tap(tester, 'desk-save');
      expect(repo.calls, contains('create'));
      expect(repo.bodies.single, {
        'kind': 'product',
        'name': 'Telematics Pilot',
        'name_ar': '',
        'summary': '',
        'maturity': 'idea',
        'integrations': ['Excel', 'Zoho'],
      });
    });

    testWidgets('a draft version is added, activated and the old one retired',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog();
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cat-row-bprod_0')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('offer-1')), findsOneWidget);
      expect(find.textContaining('Margin at this price'), findsOneWidget);

      await tap(tester, 'version-add');
      await enter(tester, 'version-price-min', '4000');
      await enter(tester, 'version-labor', '12');
      await enter(tester, 'version-hourly', '150');
      await enter(tester, 'version-deliverables', 'Dashboard\n\nTraining ');
      await tap(tester, 'desk-save');
      expect(repo.bodies.single, containsPair('price_type', 'fixed'));
      expect(repo.bodies.single['price_min'], 4000.0);
      expect(repo.bodies.single['deliverables'], ['Dashboard', 'Training']);
      expect(repo.bodies.single['cost'], containsPair('labor_hours', 12.0));
      expect(find.byKey(const Key('offer-2')), findsOneWidget);
      // The estimated cost 1,000 is under the 4,000 price: no loss warning.
      expect(find.byKey(const Key('offer-2-margin')), findsNothing);

      await tap(tester, 'offer-2-activate');
      await tap(tester, 'desk-confirm');
      expect(repo.calls, contains('activate boffer_2'));
      expect(find.text('Active'), findsOneWidget);
      expect(find.byKey(const Key('offer-1-retire')), findsNothing);
    });

    testWidgets('a price under the cost says so', (tester) async {
      await phone(tester);
      final repo = FakeCatalog()
        ..offers = [offerJson(1, 'draft', price: 500, hours: 10, rate: 100)];
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cat-row-bprod_0')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('offer-1-margin')), findsOneWidget);
      // A draft cannot be added twice: the add button is gone.
      expect(find.byKey(const Key('version-add')), findsNothing);
    });

    testWidgets('deleting asks first and shows the server refusal',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog()
        ..refuse =
            'Leads or opportunities use this product. Mark it retired instead.';
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('cat-row-bprod_0')));
      await tester.pumpAndSettle();
      await tap(tester, 'product-delete');
      expect(
          find.textContaining('Delete Reporting Quick Win 1?'), findsOneWidget);
      await tap(tester, 'desk-confirm');
      expect(find.byKey(const Key('product-error')), findsOneWidget);
      expect(find.textContaining('Mark it retired instead'), findsOneWidget);

      repo.refuse = null;
      await tap(tester, 'product-delete');
      await tap(tester, 'desk-confirm');
      expect(repo.calls, contains('delete bprod_1'));
      expect(find.byKey(const Key('cat-search')), findsOneWidget);
    });

    testWidgets('a member reads the catalog and gets no editing controls',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog(manage: false);
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c, manage: false));
      await c.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('cat-readonly')), findsOneWidget);
      await tester.tap(find.byKey(const Key('cat-row-bprod_0')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('product-edit')), findsNothing);
      expect(find.byKey(const Key('product-delete')), findsNothing);
      expect(find.byKey(const Key('version-add')), findsNothing);
      expect(find.byKey(const Key('offer-1-retire')), findsNothing);
    });

    testWidgets('a rejected save keeps the form and shows the server sentence',
        (tester) async {
      await phone(tester);
      final repo = FakeCatalog(manage: false);
      final c = CatalogController(repo);
      await tester.pumpWidget(catalogPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tap(tester, 'cat-new');
      await enter(tester, 'product-name', 'Telematics Pilot');
      await tap(tester, 'desk-save');
      expect(find.byKey(const Key('desk-form-error')), findsOneWidget);
      expect(find.text('Only workspace owners can change the catalog.'),
          findsOneWidget);
      expect(find.byKey(const Key('product-name')), findsOneWidget);
    });

    for (final width in [320.0, 390.0]) {
      testWidgets('arabic, right to left, no overflow at $width',
          (tester) async {
        await phone(tester, width: width);
        final repo = FakeCatalog();
        final c = CatalogController(repo);
        await tester.pumpWidget(catalogPage(c, locale: const Locale('ar')));
        await c.load();
        await tester.pumpAndSettle();
        expect(find.text('منتج أو باقة جديدة'), findsOneWidget);
        await tester.tap(find.byKey(const Key('cat-row-bprod_0')));
        await tester.pumpAndSettle();
        expect(find.text('النسخ'), findsOneWidget);
        expect(find.byKey(const Key('offer-1')), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('accounts and contacts', () {
    testWidgets('lists, pages and searches accounts', (tester) async {
      await phone(tester);
      final repo = FakeAccounts();
      final c = AccountsController(repo);
      await tester.pumpWidget(accountsPage(c));
      await c.load();
      await tester.pumpAndSettle();
      expect(find.text('Company 0'), findsOneWidget);
      expect(find.text('Load more (20 of 25)'), findsOneWidget);
      await tap(tester, 'desk-more');
      expect(repo.calls, contains('list null 20'));
      await tester.enterText(find.byKey(const Key('acct-search')), 'gulf');
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      expect(repo.calls.last, 'list gulf 0');
    });

    testWidgets('shows empty, permission and no-match states', (tester) async {
      await phone(tester);
      final empty = AccountsController(FakeAccounts(total: 0));
      await tester.pumpWidget(accountsPage(empty));
      await empty.load();
      await tester.pumpAndSettle();
      expect(find.text('No accounts yet'), findsOneWidget);
      expect(find.text('New account'), findsWidgets);

      final denied = AccountsController(FakeAccounts(deny: true));
      await tester.pumpWidget(accountsPage(denied));
      await denied.load();
      await tester.pumpAndSettle();
      expect(
          find.textContaining('workspace owners and members'), findsOneWidget);

      final none = AccountsController(FakeAccounts(total: 0));
      await tester.pumpWidget(accountsPage(none, write: false));
      await none.load();
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('acct-new')), findsNothing);
      await none.search('zzz');
      await tester.pumpAndSettle();
      expect(find.text('Nothing matches'), findsOneWidget);
    });

    testWidgets('a member creates an account, then adds a contact',
        (tester) async {
      await phone(tester);
      final repo = FakeAccounts();
      final c = AccountsController(repo);
      await tester.pumpWidget(accountsPage(c, manage: false));
      await c.load();
      await tester.pumpAndSettle();
      await tap(tester, 'acct-new');
      await tap(tester, 'desk-save');
      expect(find.text('Enter a name.'), findsOneWidget);
      await enter(tester, 'account-name', 'Gulf Mixers');
      await enter(tester, 'account-website', 'https://gulfmixers.example');
      await tap(tester, 'desk-save');
      expect(repo.bodies.single['name'], 'Gulf Mixers');
      expect(repo.bodies.single['website'], 'https://gulfmixers.example');
      // Opened on the new account; a member has no delete.
      expect(find.byKey(const Key('account-delete')), findsNothing);
      expect(
          find.text('Only workspace owners delete accounts.'), findsOneWidget);

      await tap(tester, 'contact-add');
      await enter(tester, 'contact-name', 'Omar Said');
      await enter(tester, 'contact-email', 'omar@gulfmixers.example');
      await tap(tester, 'desk-save');
      expect(repo.bodies.last, containsPair('consent_basis', 'unknown'));
      expect(repo.bodies.last['email'], 'omar@gulfmixers.example');
      expect(repo.bodies.last['phone'], isNull);
      expect(find.text('Consent basis is unknown.'), findsOneWidget);
    });

    testWidgets('a contact is edited, marked do not contact and deleted',
        (tester) async {
      await phone(tester);
      final repo = FakeAccounts();
      final c = AccountsController(repo);
      await tester.pumpWidget(accountsPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('acct-row-bacct_1')));
      await tester.pumpAndSettle();
      await tap(tester, 'contact-bcont_1-edit');
      await enter(tester, 'contact-role', 'Director');
      await tap(tester, 'desk-save');
      expect(repo.calls, contains('updateContact bcont_1'));
      expect(repo.bodies.last['role_title'], 'Director');

      await tap(tester, 'contact-bcont_1-dnc');
      await tester.enterText(find.byKey(const Key('desk-reason')), 'ab');
      await tester.pump();
      expect(
          tester
              .widget<FilledButton>(find.byKey(const Key('desk-reason-ok')))
              .onPressed,
          isNull);
      await tester.enterText(
          find.byKey(const Key('desk-reason')), 'Asked us to stop');
      await tester.pump();
      await tap(tester, 'desk-reason-ok');
      expect(repo.calls, contains('contactDnc bcont_1 true'));

      await tap(tester, 'contact-bcont_1-delete');
      await tap(tester, 'desk-confirm');
      expect(repo.calls, contains('deleteContact bcont_1'));
      expect(find.text('No contacts yet'), findsOneWidget);
    });

    testWidgets('an account is marked do not contact; only owners clear it',
        (tester) async {
      await phone(tester);
      final repo = FakeAccounts();
      final c = AccountsController(repo);
      await tester.pumpWidget(accountsPage(c, manage: false));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('acct-row-bacct_1')));
      await tester.pumpAndSettle();
      await tap(tester, 'account-dnc-toggle');
      await tester.enterText(
          find.byKey(const Key('desk-reason')), 'Asked us to stop');
      await tester.pump();
      await tap(tester, 'desk-reason-ok');
      expect(repo.calls, contains('accountDnc true Asked us to stop'));
      expect(find.byKey(const Key('account-dnc')), findsOneWidget);
      // A member may not clear the mark: the control is gone.
      expect(find.byKey(const Key('account-dnc-toggle')), findsNothing);
    });

    testWidgets('an owner deletes an account and sees a refusal first',
        (tester) async {
      await phone(tester);
      final repo = FakeAccounts()
        ..refuse = 'An opportunity depends on this account.';
      final c = AccountsController(repo);
      await tester.pumpWidget(accountsPage(c));
      await c.load();
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('acct-row-bacct_1')));
      await tester.pumpAndSettle();
      await tap(tester, 'account-delete');
      await tap(tester, 'desk-confirm');
      expect(
          find.text('An opportunity depends on this account.'), findsOneWidget);
      repo.refuse = null;
      await tap(tester, 'account-delete');
      await tap(tester, 'desk-confirm');
      expect(repo.calls, contains('delete bacct_1'));
      expect(find.byKey(const Key('acct-search')), findsOneWidget);
    });

    for (final width in [320.0, 390.0]) {
      testWidgets('arabic, right to left, no overflow at $width',
          (tester) async {
        await phone(tester, width: width);
        final repo = FakeAccounts();
        final c = AccountsController(repo);
        await tester.pumpWidget(accountsPage(c, locale: const Locale('ar')));
        await c.load();
        await tester.pumpAndSettle();
        expect(find.text('حساب جديد'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tap(find.byKey(const Key('acct-row-bacct_1')));
        await tester.pumpAndSettle();
        expect(find.text('جهات الاتصال'), findsOneWidget);
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('the Business screen', () {
    testWidgets('has Catalog and Accounts tabs that open the editors',
        (tester) async {
      await phone(tester);
      final catalog = FakeCatalog();
      final accounts = FakeAccounts();
      final c = BusinessController(
        FakeBusiness(),
        catalog: CatalogController(catalog),
        accounts: AccountsController(accounts),
      );
      await tester.pumpWidget(app(BusinessScreen(controller: c)));
      await tester.pumpAndSettle();
      await tap(tester, 'biz-tab-catalog');
      expect(find.byKey(const Key('cat-new')), findsOneWidget);
      expect(catalog.calls, contains('list null null 0'));
      await tap(tester, 'biz-tab-accounts');
      expect(find.byKey(const Key('acct-new')), findsOneWidget);
      expect(accounts.calls, contains('list null 0'));
      expect(tester.takeException(), isNull);
    });
  });
}
