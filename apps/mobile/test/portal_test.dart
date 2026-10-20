import 'dart:async';
import 'dart:convert';
import 'package:anum_mobile/data/api_client.dart';
import 'package:anum_mobile/data/json_read.dart';
import 'package:anum_mobile/data/session_store.dart';
import 'package:anum_mobile/features/auth/auth_controller.dart';
import 'package:anum_mobile/features/auth/auth_repository.dart';
import 'package:anum_mobile/features/auth/auth_screens.dart';
import 'package:anum_mobile/features/business/invoice_document.dart';
import 'package:anum_mobile/features/portal/portal_controller.dart';
import 'package:anum_mobile/features/portal/portal_link.dart';
import 'package:anum_mobile/features/portal/portal_models.dart';
import 'package:anum_mobile/features/portal/portal_repository.dart';
import 'package:anum_mobile/features/portal/portal_screens.dart';
import 'package:anum_mobile/src/localization/business_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'business_test.dart' show app;

// The customer portal on the phone (docs/business-portal.md): reading the
// link, the token kept in memory and sent only as a bearer header, the
// overview, offers and change orders with the typed-name decision (and the
// 409 reload), invoices with the printable copy, requests, every state, and
// layout and Arabic. Wire shapes as /api/v1/portal sends them.

const token =
    'anum_portal.tenant_local.ws_default.bpacc_1.Zk3fQ8m1Vb7nX2cL5hRt9yUpWd4sAe6J';
final hashA = 'a' * 64;
final hashB = 'b' * 64;

Map<String, Object?> meJson({
  bool canDecide = true,
  int offers = 1,
  int changes = 1,
  int open = 2,
  int overdue = 1,
  int tickets = 3,
}) =>
    {
      'business_name': 'Shahzeb Digital',
      'account_name': 'Gulf Mixers',
      'contact_name': 'Sara Khan',
      'expires_at': '2027-01-17T00:00:00Z',
      'can_decide': canDecide,
      'counts': {
        'offers_waiting': offers,
        'change_orders_waiting': changes,
        'invoices_open': open,
        'invoices_overdue': overdue,
        'tickets_open': tickets,
      },
      'projects': [
        {'id': 'bproj_1', 'name': 'Fleet reporting pilot'}
      ],
    };

Map<String, Object?> offerJson({
  String id = 'bprop_1',
  String status = 'waiting',
  Map<String, Object?>? decision,
  bool expired = false,
  bool canDecide = true,
  String hash = 'a',
  int version = 2,
}) =>
    {
      'id': id,
      'title': 'Fleet reporting pilot',
      'status': status,
      'currency': 'SAR',
      'total': 2875.0,
      'version': version,
      'sent_at': '2026-10-10T09:00:00Z',
      'valid_until': expired ? '2026-10-01' : '2026-12-31',
      'expired': expired,
      'decision': decision,
      'can_decide': canDecide,
      'summary': 'Dashboards for the mixer fleet.',
      'scope': 'Three dashboards and a monthly report.',
      'deliverables': ['Three dashboards', 'Monthly report'],
      'exclusions': ['Hardware'],
      'assumptions': ['Data is exported weekly'],
      'acceptance_criteria': ['Dashboards load in under 3 seconds'],
      'line_items': [
        {
          'description': 'Dashboards',
          'quantity': 3.0,
          'unit_price': 500.0,
          'amount': 1500.0
        }
      ],
      'milestones': [
        {
          'title': 'Deposit',
          'amount': 1250.0,
          'due_in_days': 7,
          'acceptance': 'Signed offer'
        }
      ],
      'subtotal': 2500.0,
      'tax': 375.0,
      'tax_rate_pct': 15.0,
      'tax_note': 'VAT',
      'payment_terms': 'Half now, half on delivery.',
      'content_hash': hash * 64,
    };

Map<String, Object?> decidedJson(String decision) => {
      'decision': decision,
      'signer_name': 'Sara Khan',
      'note': 'Looks good.',
      'decided_at': '2026-10-18T09:00:00Z',
    };

Map<String, Object?> changeJson({
  String id = 'bchg_1',
  String status = 'waiting',
  Map<String, Object?>? decision,
  bool stale = false,
  bool canDecide = true,
  String hash = 'a',
}) =>
    {
      'id': id,
      'number': 1,
      'title': 'Extra dashboards',
      'project_name': 'Fleet reporting pilot',
      'status': status,
      'currency': 'SAR',
      'total_delta': 400.0,
      'timeline_days': 5,
      'decision': decision,
      'can_decide': canDecide && !stale,
      'stale': stale,
      'request': 'Add two more dashboards.',
      'subtotal_delta': 348.0,
      'tax_delta': 52.0,
      'content_hash': hash * 64,
      'lines': [
        {
          'action': 'add',
          'description': 'Two dashboards',
          'quantity': 2.0,
          'unit_price': 200.0,
          'amount': 400.0
        }
      ],
    };

Map<String, Object?> invoiceSummaryJson(String id, {bool overdue = false}) => {
      'id': id,
      'number': 'INV-2026-0001',
      'status': 'sent',
      'currency': 'SAR',
      'total': 1150.0,
      'amount_paid': 200.0,
      'balance': 950.0,
      'issue_date': '2026-10-05',
      'due_date': '2026-10-19',
      'overdue': overdue,
      'days_overdue': overdue ? 4 : 0,
      'payment_state': 'partially_paid',
      'customer_reference': 'PO-77',
    };

Map<String, Object?> invoiceJson({String status = 'sent'}) => {
      ...invoiceSummaryJson('binv_1', overdue: true),
      'status': status,
      'lines': [
        {
          'description': 'Deposit',
          'quantity': 1.0,
          'unit_price': 1000.0,
          'tax_rate_pct': 15.0,
          'total': 1150.0
        }
      ],
      'subtotal': 1000.0,
      'tax': 150.0,
      'notes': 'Thank you.',
      'payment_terms': 'Pay within 14 days.',
      'bill_to_name': 'Gulf Mixers',
      'bill_to_address': 'Riyadh',
      'bill_to_tax_number': '300011112200003',
      'seller': {
        'legal_name': 'Shahzeb Digital',
        'address': 'Riyadh, Saudi Arabia',
        'tax_label': 'VAT number',
        'tax_number': '300099998800003',
        'email': 'billing@shahzeb.example',
        'phone': '',
        'bank_details': 'IBAN SA00 0000 0000 0000',
      },
      'payments': [
        {
          'paid_on': '2026-10-10',
          'amount': 200.0,
          'method': 'bank_transfer',
          'reference': 'TRX-1'
        }
      ],
      'void_reason': status == 'void' ? 'Issued by mistake.' : '',
    };

Map<String, Object?> ticketSummaryJson(String id,
        {String status = 'in_progress'}) =>
    {
      'id': id,
      'reference': 'T-1A2B3C',
      'title': 'Report export fails',
      'severity': 'high',
      'status': status,
      'project_name': 'Fleet reporting pilot',
      'reported_at': '2026-10-18T06:00:00Z',
      'updated_at': '2026-10-18T08:00:00Z',
      'opened_here': true,
    };

Map<String, Object?> ticketJson() => {
      ...ticketSummaryJson('btkt_1', status: 'resolved'),
      'description': 'The monthly export stops at 50%.',
      'resolution_summary': 'Fixed in the new release.',
      'resolved_at': '2026-10-19T08:00:00Z',
      'replies': [
        {'body': 'We are looking into it.', 'sent_at': '2026-10-18T09:00:00Z'}
      ],
    };

class FakePortal implements PortalRepository {
  FakePortal({this.canDecide = true});
  bool canDecide;
  bool closed = false;
  final calls = <String>[];
  ApiException? meError;
  ApiException? decideError;
  ApiException? listError;
  ApiException? ticketError;
  ApiException? reportError;
  Map<String, Object?>? reportBody;
  final reportKeys = <String>[];
  Completer<void>? gate;
  Map<String, Object?> offerDoc = offerJson();
  Map<String, Object?> changeDoc = changeJson();
  List<Map<String, Object?>> offerRows = [offerJson()];
  List<Map<String, Object?>> changeRows = [changeJson()];
  List<Map<String, Object?>> invoiceRows = [
    invoiceSummaryJson('binv_1', overdue: true)
  ];
  List<Map<String, Object?>> ticketRows = [ticketSummaryJson('btkt_1')];
  Map<String, Object?> invoiceDoc = invoiceJson();
  Map<String, Object?>? decisionBody;
  Map<String, Object?>? ticketBody;

  Future<void> _enter(String call) async {
    calls.add(call);
    if (closed) throw const ApiException(401, 'You left the portal.');
    if (gate != null) await gate!.future;
  }

  @override
  void close() => closed = true;

  @override
  Future<PortalMe> me() async {
    await _enter('me');
    if (meError != null) throw meError!;
    return PortalMe.fromJson(JsonReader.of(meJson(canDecide: canDecide)));
  }

  @override
  Future<List<PortalOfferSummary>> offers() async {
    await _enter('offers');
    if (listError != null) throw listError!;
    return [
      for (final o in offerRows) PortalOfferSummary.fromJson(JsonReader.of(o))
    ];
  }

  @override
  Future<PortalOffer> offer(String id) async {
    await _enter('offer $id');
    return PortalOffer.fromJson(JsonReader.of(offerDoc));
  }

  @override
  Future<PortalOffer> decideOffer(String id,
      {required String decision,
      required String contentHash,
      required String signerName,
      String note = ''}) async {
    await _enter('decide-offer $id');
    decisionBody = {
      'decision': decision,
      'content_hash': contentHash,
      'signer_name': signerName,
      'note': note,
    };
    if (decideError != null) throw decideError!;
    offerDoc = offerJson(
        status: 'waiting', decision: decidedJson(decision), canDecide: false);
    return PortalOffer.fromJson(JsonReader.of(offerDoc));
  }

  @override
  Future<List<PortalChangeSummary>> changeOrders() async {
    await _enter('changes');
    if (listError != null) throw listError!;
    return [
      for (final o in changeRows) PortalChangeSummary.fromJson(JsonReader.of(o))
    ];
  }

  @override
  Future<PortalChange> changeOrder(String id) async {
    await _enter('change $id');
    return PortalChange.fromJson(JsonReader.of(changeDoc));
  }

  @override
  Future<PortalChange> decideChangeOrder(String id,
      {required String decision,
      required String contentHash,
      required String signerName,
      String note = ''}) async {
    await _enter('decide-change $id');
    decisionBody = {
      'decision': decision,
      'content_hash': contentHash,
      'signer_name': signerName,
      'note': note,
    };
    if (decideError != null) throw decideError!;
    changeDoc = changeJson(decision: decidedJson(decision), canDecide: false);
    return PortalChange.fromJson(JsonReader.of(changeDoc));
  }

  @override
  Future<List<PortalInvoiceSummary>> invoices() async {
    await _enter('invoices');
    if (listError != null) throw listError!;
    return [
      for (final o in invoiceRows)
        PortalInvoiceSummary.fromJson(JsonReader.of(o))
    ];
  }

  @override
  Future<PortalInvoice> invoice(String id) async {
    await _enter('invoice $id');
    return PortalInvoice.fromJson(JsonReader.of(invoiceDoc));
  }

  @override
  Future<String> printHtml(String id, String lang) async {
    await _enter('print $id $lang');
    return '<html><body>Portal invoice $lang</body></html>';
  }

  @override
  Future<Uint8List> pdf(String id) async {
    await _enter('pdf $id');
    return Uint8List.fromList(utf8.encode('%PDF-1.4 portal'));
  }

  @override
  Future<PortalInvoice> reportPayment(
    String id, {
    required String idempotencyKey,
    required String payerName,
    required double amount,
    required String paidOn,
    required String method,
    String reference = '',
    String note = '',
  }) async {
    await _enter('report $id');
    reportKeys.add(idempotencyKey);
    reportBody = {
      'payer_name': payerName,
      'amount': amount,
      'paid_on': paidOn,
      'method': method,
      'reference': reference,
      'note': note,
    };
    if (reportError != null) throw reportError!;
    final existing = (invoiceDoc['payment_notices'] as List?) ?? const [];
    invoiceDoc = {
      ...invoiceDoc,
      'payment_notices': [
        {
          'id': 'bpnote_1',
          'amount': amount,
          'paid_on': paidOn,
          'method': method,
          'reference': reference,
          'status': 'pending',
          'decision_note': '',
        },
        ...existing,
      ],
    };
    return PortalInvoice.fromJson(JsonReader.of(invoiceDoc));
  }

  @override
  Future<List<PortalTicketSummary>> tickets() async {
    await _enter('tickets');
    if (listError != null) throw listError!;
    return [
      for (final o in ticketRows) PortalTicketSummary.fromJson(JsonReader.of(o))
    ];
  }

  @override
  Future<PortalTicket> ticket(String id) async {
    await _enter('ticket $id');
    return PortalTicket.fromJson(JsonReader.of(ticketJson()));
  }

  @override
  Future<PortalTicket> openTicket({
    required String title,
    required String description,
    required String severity,
    String? projectId,
  }) async {
    await _enter('open-ticket');
    ticketBody = {
      'title': title,
      'description': description,
      'severity': severity,
      'project_id': projectId,
    };
    if (ticketError != null) throw ticketError!;
    return PortalTicket.fromJson(JsonReader.of(ticketJson()));
  }
}

class FakeSaver implements InvoiceDocumentSaver {
  final saved = <(String, String)>[];
  @override
  Future<bool> save(String fileName, Uint8List bytes) async {
    saved.add((fileName, utf8.decode(bytes)));
    return true;
  }
}

void sized(WidgetTester tester, double width, [double height = 3000]) {
  tester.view.physicalSize = Size(width, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Future<void> reveal(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
}

Future<PortalController> pumpHome(WidgetTester tester, FakePortal repo,
    {Locale locale = const Locale('en'),
    double textScale = 1,
    InvoiceDocumentSaver? saver}) async {
  final c = PortalController(repo);
  await tester.pumpWidget(const SizedBox.shrink());
  await tester.pumpWidget(app(
      PortalHomeScreen(controller: c, saver: saver ?? FakeSaver()),
      locale: locale,
      textScale: textScale));
  await tester.pumpAndSettle();
  return c;
}

Future<PortalController> pumpScreen(WidgetTester tester, FakePortal repo,
    Widget Function(PortalController c) screen,
    {Locale locale = const Locale('en'), double textScale = 1}) async {
  final c = PortalController(repo);
  await c.open();
  await tester.pumpWidget(const SizedBox.shrink());
  await tester.pumpWidget(app(screen(c), locale: locale, textScale: textScale));
  await tester.pumpAndSettle();
  return c;
}

class _RecordingTransport implements ApiTransport {
  final requests = <ApiRequest>[];
  @override
  Future<ApiResponse> send(ApiRequest request) async {
    requests.add(request);
    return ApiResponse(
        statusCode: 200,
        body: request.uri.path.endsWith('/offers')
            ? {
                'data': [offerJson()]
              }
            : request.uri.path.endsWith('/print')
                ? {'html': '<html></html>'}
                : meJson());
  }
}

/// Stands in for the sign-in controller the way the auth tests do.
class _SignedOut extends AuthController {
  _SignedOut()
      : super(AuthRepository(
            api: AnumApiClient(
                baseUri: Uri.parse('https://anum.test'),
                transport: _RecordingTransport(),
                sessions: MemorySessionStore()),
            sessions: MemorySessionStore())) {
    phase = AuthPhase.signedOut;
  }
}

void main() {
  group('reading the link', () {
    test('the full link, the bare code and a messy paste give the token', () {
      for (final text in [
        'https://app.anum360os.com/portal#access=$token',
        '  https://app.anum360os.com/portal#access=$token\n',
        '<https://app.anum360os.com/portal#access=$token>',
        '"https://app.anum360os.com/portal#access=$token"',
        'https://app.anum360os.com/portal#foo=1&access=$token&x=2',
        'https://app.anum360os.com/portal#access=${Uri.encodeComponent(token)}',
        '/portal#access=$token',
        'access=$token',
        token,
        '  $token  ',
      ]) {
        final result = parsePortalLink(text);
        expect(result, isA<PortalLinkOk>(), reason: text);
        expect((result as PortalLinkOk).token, token, reason: text);
      }
    });

    test('anything else says why, and never echoes the token', () {
      PortalLinkInvalid invalid(String text) =>
          parsePortalLink(text) as PortalLinkInvalid;
      expect(invalid('').reason, 'empty');
      expect(invalid('   \n').reason, 'empty');
      // A link whose fragment was cut off, or one that is not a portal link.
      expect(invalid('https://app.anum360os.com/portal').reason, 'noToken');
      expect(invalid('https://app.anum360os.com/portal#access=').reason,
          'noToken');
      expect(invalid('https://example.com/page#section').reason, 'noToken');
      expect(invalid('hello world').reason, 'malformed');
      expect(invalid('anum_portal.a.b.c.short').reason, 'malformed');
      expect(invalid('anum_portal.a.b.c.${'x' * 200}').reason, 'malformed');
      expect(invalid('https://x.test/portal#access=%ZZ').reason, 'malformed');
      expect('${const PortalLinkOk('anum_portal.secret')}',
          isNot(contains('secret')));
    });

    test('every reason has a message in both languages', () {
      for (final reason in ['empty', 'noToken', 'malformed']) {
        expect(businessEn['portal.link.$reason'], isNotEmpty);
        expect(businessAr['portal.link.$reason'], isNotEmpty);
      }
    });
  });

  group('the repository', () {
    test(
        'sends the token as a bearer header, no workspace header, no URL token',
        () async {
      final transport = _RecordingTransport();
      // An empty session store: an authenticated call would fail locally.
      final api = AnumApiClient(
          baseUri: Uri.parse('https://api.anum.test'),
          transport: transport,
          sessions: MemorySessionStore());
      final repo = ApiPortalRepository(api, token);
      final me = await repo.me();
      expect(me.accountName, 'Gulf Mixers');
      final offers = await repo.offers();
      expect(offers.single.title, 'Fleet reporting pilot');
      await repo.printHtml('binv_1', 'ar');
      for (final r in transport.requests) {
        expect(r.headers['authorization'], 'Bearer $token');
        expect(r.headers.keys.map((k) => k.toLowerCase()),
            isNot(contains('x-workspace-id')));
        expect(r.uri.toString(), isNot(contains('anum_portal')));
        expect(r.uri.host, 'api.anum.test');
        expect(r.uri.path, startsWith('/api/v1/portal/'));
      }
      expect(transport.requests.last.uri.toString(),
          'https://api.anum.test/api/v1/portal/invoices/binv_1/print?lang=ar');
    });

    test('after close nothing is sent and the token is gone', () async {
      final transport = _RecordingTransport();
      final repo = ApiPortalRepository(
          AnumApiClient(
              baseUri: Uri.parse('https://api.anum.test'),
              transport: transport,
              sessions: MemorySessionStore()),
          token);
      repo.close();
      await expectLater(
          repo.me(),
          throwsA(
              isA<ApiException>().having((e) => e.statusCode, 'status', 401)));
      expect(transport.requests, isEmpty);
    });
  });

  group('the controller', () {
    test('opens, counts and forgets the token on leave', () async {
      final repo = FakePortal();
      final c = PortalController(repo);
      await c.open();
      expect(c.state, PortalLoad.ready);
      expect(c.me!.counts.offersWaiting, 1);
      await c.loadOffers();
      expect(c.offers.items, hasLength(1));
      c.leave();
      expect(repo.closed, isTrue);
      expect(c.me, isNull);
      expect(c.offers.items, isEmpty);
      await c.refreshCounts();
      expect(c.linkProblem, 'You left the portal.');
    });

    test('a refused link is a link problem, not an error to retry', () async {
      final repo = FakePortal()
        ..meError = const ApiException(401,
            'This link was turned off. Ask the business that sent it for a new one.');
      final c = PortalController(repo);
      await c.open();
      expect(c.linkProblem, contains('turned off'));
      expect(repo.calls.where((x) => x == 'me'), hasLength(1));
    });

    test('a decision sends the hash that was shown and refreshes the counts',
        () async {
      final repo = FakePortal();
      final c = PortalController(repo);
      await c.open();
      final offer = await repo.offer('bprop_1');
      final next = await c.decideOffer(offer, 'approved', 'Sara Khan', 'ok');
      expect(repo.decisionBody, {
        'decision': 'approved',
        'content_hash': hashA,
        'signer_name': 'Sara Khan',
        'note': 'ok',
      });
      expect(next.decision!.approved, isTrue);
      expect(repo.calls.where((x) => x == 'me'), hasLength(2));
    });
  });

  group('signing in', () {
    testWidgets('the sign-in screen offers the portal only when it can open it',
        (tester) async {
      sized(tester, 390, 900);
      var opened = 0;
      await tester.pumpWidget(app(
          AuthFlow(controller: _SignedOut(), onOpenPortal: () => opened++)));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('open-portal')), findsOneWidget);
      expect(find.textContaining('Customer portal'), findsOneWidget);
      await tester.tap(find.byKey(const Key('open-portal')));
      expect(opened, 1);

      // The app provides the opener once, above the navigator.
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpWidget(app(PortalEntryScope(
          open: () => opened += 10,
          child: AuthFlow(controller: _SignedOut()))));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('open-portal')));
      expect(opened, 11);

      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpWidget(app(AuthFlow(controller: _SignedOut())));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('open-portal')), findsNothing);
    });

    testWidgets('paste a link: an invalid one says why, a valid one opens',
        (tester) async {
      sized(tester, 390, 900);
      FakePortal? repo;
      String? received;
      await tester.pumpWidget(app(PortalEntryScreen(
        createSession: (value) {
          received = value;
          repo = FakePortal();
          return PortalController(repo!);
        },
        saver: FakeSaver(),
      )));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('portal-open')));
      await tester.pumpAndSettle();
      expect(
          find.text('Paste the link the business sent you.'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('portal-link-field')), 'https://x.test/portal');
      await tester.tap(find.byKey(const Key('portal-open')));
      await tester.pumpAndSettle();
      expect(find.textContaining('has no access code'), findsOneWidget);
      expect(received, isNull);

      await tester.enterText(find.byKey(const Key('portal-link-field')),
          'https://app.anum360os.com/portal#access=$token');
      await tester.tap(find.byKey(const Key('portal-open')));
      await tester.pumpAndSettle();
      expect(received, token);
      expect(find.text('Gulf Mixers'), findsOneWidget);
      expect(repo!.calls, contains('me'));
      // Leaving forgets the token, and the pasted text is not kept.
      await tester.tap(find.byKey(const Key('portal-leave')));
      await tester.pumpAndSettle();
      expect(repo!.closed, isTrue);
      final field =
          tester.widget<TextField>(find.byKey(const Key('portal-link-field')));
      expect(field.controller!.text, isEmpty);
      expect(find.textContaining('anum_portal.'), findsNothing);
    });
  });

  group('overview', () {
    testWidgets('counts, the expiry and the way to every section',
        (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal();
      await pumpHome(tester, repo);
      expect(find.text('Shahzeb Digital'), findsOneWidget);
      expect(find.text('Gulf Mixers'), findsOneWidget);
      expect(find.text('Hello Sara Khan'), findsOneWidget);
      expect(find.text('This link works until 2027-01-17.'), findsOneWidget);
      expect(find.text('1 waiting for your answer'), findsNWidgets(2));
      expect(find.text('2 to pay, 1 overdue'), findsOneWidget);
      expect(find.text('3 open'), findsOneWidget);
      expect(find.byKey(const Key('portal-read-only')), findsNothing);
      // Raw ids are never shown.
      expect(find.textContaining('bproj_'), findsNothing);
      expect(find.textContaining('bprop_'), findsNothing);
    });

    testWidgets('a read-only link says so', (tester) async {
      sized(tester, 390, 1400);
      await pumpHome(tester, FakePortal(canDecide: false));
      expect(find.byKey(const Key('portal-read-only')), findsOneWidget);
      expect(find.textContaining('for reading only'), findsOneWidget);
    });

    testWidgets('loading, then a failure with a retry', (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal()..gate = Completer<void>();
      final c = PortalController(repo);
      await tester.pumpWidget(app(PortalHomeScreen(controller: c)));
      await tester.pump();
      await tester.pump();
      expect(find.text('Loading...'), findsOneWidget);
      repo.meError = const ApiException(500, 'The server failed.');
      repo.gate!.complete();
      await tester.pumpAndSettle();
      expect(find.textContaining('Could not load: The server failed.'),
          findsOneWidget);
      repo.meError = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('Gulf Mixers'), findsOneWidget);
    });

    testWidgets('a turned-off link shows the API sentence and never retries',
        (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal()
        ..meError = const ApiException(401,
            'This link was turned off. Ask the business that sent it for a new one.');
      await pumpHome(tester, repo);
      expect(find.byKey(const Key('portal-link-problem')), findsOneWidget);
      expect(
          find.text(
              'This link was turned off. Ask the business that sent it for a new one.'),
          findsOneWidget);
      expect(find.text('Retry'), findsNothing);
      expect(find.byKey(const Key('portal-problem-back')), findsOneWidget);
      await tester.pump(const Duration(seconds: 5));
      expect(repo.calls.where((x) => x == 'me'), hasLength(1));
    });
  });

  group('offers', () {
    testWidgets('the list, an empty state, and a failure that retries',
        (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal();
      await pumpScreen(tester, repo, (c) => PortalOffersScreen(controller: c));
      expect(find.text('Fleet reporting pilot'), findsOneWidget);
      expect(find.text('Waiting for you'), findsOneWidget);
      expect(find.text('SAR 2,875.00'), findsOneWidget);

      final empty = FakePortal()..offerRows = [];
      await pumpScreen(tester, empty, (c) => PortalOffersScreen(controller: c));
      expect(find.text('No offers yet'), findsOneWidget);

      final failing = FakePortal()
        ..listError = const ApiException(500, 'The server failed.');
      await pumpScreen(
          tester, failing, (c) => PortalOffersScreen(controller: c));
      expect(find.textContaining('The server failed.'), findsOneWidget);
      failing.listError = null;
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('Fleet reporting pilot'), findsOneWidget);
    });

    testWidgets('an offer shows its scope, lines, milestones and total',
        (tester) async {
      sized(tester, 390, 3000);
      await pumpScreen(tester, FakePortal(),
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      expect(
          find.text('Three dashboards and a monthly report.'), findsOneWidget);
      expect(find.text('• Hardware'), findsOneWidget);
      expect(find.text('Dashboards'), findsOneWidget);
      expect(find.text('Deposit'), findsOneWidget);
      expect(find.text('SAR 375.00'), findsOneWidget);
      expect(find.text('SAR 2,875.00'), findsOneWidget);
      expect(find.text('Half now, half on delivery.'), findsOneWidget);
      expect(find.text('Version 2 · valid until 2026-12-31'), findsOneWidget);
    });

    testWidgets('approving asks for the name and sends the hash shown',
        (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal();
      await pumpScreen(tester, repo,
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      // The dialog says what is approved and for how much.
      expect(find.text('Approve this offer?'), findsOneWidget);
      expect(
          find.textContaining(
              '"Fleet reporting pilot" for a total of SAR 2,875.00'),
          findsOneWidget);
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(find.text('Type your full name (at least 2 characters).'),
          findsOneWidget);
      expect(repo.decisionBody, isNull);
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara  Khan');
      await tester.enterText(find.byKey(const Key('portal-note')), 'Go ahead.');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(repo.decisionBody, {
        'decision': 'approved',
        'content_hash': hashA,
        'signer_name': 'Sara Khan',
        'note': 'Go ahead.',
      });
      expect(find.text('Your answer was recorded. The business has been told.'),
          findsOneWidget);
      expect(find.byKey(const Key('portal-decided')), findsOneWidget);
      expect(find.text('Approved by Sara Khan on 2026-10-18'), findsOneWidget);
      expect(find.byKey(const Key('portal-approve')), findsNothing);
    });

    testWidgets('declining says it cannot be reversed here', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal();
      await pumpScreen(tester, repo,
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      await tester.tap(find.byKey(const Key('portal-decline')));
      await tester.pumpAndSettle();
      expect(find.text('Decline this offer?'), findsOneWidget);
      expect(find.textContaining('cannot reverse this here'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(repo.decisionBody!['decision'], 'declined');
      expect(find.text('Declined by Sara Khan on 2026-10-18'), findsOneWidget);
    });

    testWidgets('a 409 reloads the latest version and says it changed',
        (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()
        ..decideError = const ApiException(409,
            'This offer changed after you opened it, so nothing was recorded. Reload the page to read the latest version.');
      await pumpScreen(tester, repo,
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      // The business sent a new version while the page was open.
      repo.offerDoc = offerJson(version: 3, hash: 'b');
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(repo.decisionBody!['content_hash'], hashA);
      expect(find.byKey(const Key('portal-problem')), findsOneWidget);
      expect(find.textContaining('This offer changed after you opened it'),
          findsOneWidget);
      expect(find.textContaining('The latest version is shown below'),
          findsOneWidget);
      expect(find.textContaining('Version 3'), findsOneWidget);
      expect(find.byKey(const Key('portal-decided')), findsNothing);
      // The next answer carries the new version's hash.
      repo.decideError = null;
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(repo.decisionBody!['content_hash'], hashB);
    });

    testWidgets(
        'a read-only link, a decided offer, an expired offer and an '
        'accepted one have no buttons and say why', (tester) async {
      sized(tester, 390, 3000);
      Future<void> check(FakePortal repo, String text) async {
        await pumpScreen(tester, repo,
            (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
        expect(find.byKey(const Key('portal-approve')), findsNothing);
        expect(find.byKey(const Key('portal-decline')), findsNothing);
        expect(find.textContaining(text), findsWidgets);
      }

      await check(
          FakePortal(canDecide: false)..offerDoc = offerJson(canDecide: false),
          'for reading only');
      await check(
          FakePortal()
            ..offerDoc =
                offerJson(decision: decidedJson('approved'), canDecide: false),
          'Approved by Sara Khan on 2026-10-18');
      await check(
          FakePortal()..offerDoc = offerJson(expired: true, canDecide: false),
          'valid until 2026-10-01');
      await check(
          FakePortal()
            ..offerDoc = offerJson(status: 'accepted', canDecide: false),
          'already accepted');
    });

    testWidgets('a paused business refuses with its own sentence',
        (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()
        ..decideError = const ApiException(403,
            'This business has paused its account, so nothing can be changed now. You can still read everything.');
      await pumpScreen(tester, repo,
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(find.textContaining('has paused its account'), findsOneWidget);
    });

    testWidgets('a link that stops working mid-way shows the link problem',
        (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()
        ..decideError = const ApiException(401,
            'This link has expired. Ask the business that sent it for a new one.');
      await pumpScreen(tester, repo,
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('portal-link-problem')), findsOneWidget);
      expect(find.textContaining('has expired'), findsOneWidget);
      expect(find.text('Retry'), findsNothing);
    });
  });

  group('change orders', () {
    testWidgets('list, detail and approving', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal();
      await pumpScreen(tester, repo, (c) => PortalChangesScreen(controller: c));
      expect(find.text('Change order 1: Extra dashboards'), findsOneWidget);
      expect(find.text('Price +SAR 400.00, timeline 5 days'), findsOneWidget);
      await tester.tap(find.byKey(const Key('portal-change-bchg_1')));
      await tester.pumpAndSettle();
      expect(find.text('Add two more dashboards.'), findsOneWidget);
      expect(find.text('Added, 2 x SAR 200.00'), findsOneWidget);
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      expect(find.text('Approve this change?'), findsOneWidget);
      expect(
          find.textContaining('price change of +SAR 400.00'), findsOneWidget);
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(repo.decisionBody!['content_hash'], hashA);
      expect(find.text('Approved by Sara Khan on 2026-10-18'), findsOneWidget);
    });

    testWidgets('a stale change order cannot be answered', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()..changeDoc = changeJson(stale: true);
      await pumpScreen(tester, repo,
          (c) => PortalChangeScreen(controller: c, changeId: 'bchg_1'));
      expect(find.byKey(const Key('portal-approve')), findsNothing);
      expect(find.textContaining('the business will send it again'),
          findsOneWidget);
    });

    testWidgets('a 409 reloads and says so', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()
        ..decideError = const ApiException(409,
            'This change order changed after you opened it, so nothing was recorded. Reload the page to read the latest version.');
      await pumpScreen(tester, repo,
          (c) => PortalChangeScreen(controller: c, changeId: 'bchg_1'));
      await tester.tap(find.byKey(const Key('portal-decline')));
      await tester.pumpAndSettle();
      await tester.enterText(
          find.byKey(const Key('portal-signer')), 'Sara Khan');
      await tester.tap(find.byKey(const Key('portal-confirm')));
      await tester.pumpAndSettle();
      expect(
          find.textContaining('changed after you opened it'), findsOneWidget);
      expect(
          find.textContaining('latest version is shown below'), findsOneWidget);
    });

    testWidgets('empty', (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal()..changeRows = [];
      await pumpScreen(tester, repo, (c) => PortalChangesScreen(controller: c));
      expect(find.text('No change orders'), findsOneWidget);
    });
  });

  group('invoices', () {
    testWidgets('list, detail with payments and how to pay, and the copy',
        (tester) async {
      sized(tester, 390, 4000);
      final repo = FakePortal();
      final saver = FakeSaver();
      await pumpScreen(tester, repo,
          (c) => PortalInvoicesScreen(controller: c, saver: saver));
      expect(find.text('INV-2026-0001'), findsOneWidget);
      expect(find.text('Total SAR 1,150.00, left to pay SAR 950.00'),
          findsOneWidget);
      expect(find.text('due 2026-10-19 · 4 days overdue'), findsOneWidget);
      await tester.tap(find.byKey(const Key('portal-invoice-binv_1')));
      await tester.pumpAndSettle();
      expect(find.text('Payments received'), findsOneWidget);
      expect(find.text('SAR 200.00'), findsWidgets);
      expect(find.byKey(const Key('portal-how-to-pay')), findsOneWidget);
      expect(find.text('IBAN SA00 0000 0000 0000'), findsOneWidget);
      expect(find.textContaining('cannot pay inside this app'), findsOneWidget);

      await tester.tap(find.byKey(const Key('portal-invoice-preview')));
      await tester.pumpAndSettle();
      expect(find.text('Shahzeb Digital'), findsOneWidget);
      await tester.tap(find.byKey(const Key('inv-doc-save')));
      await tester.pumpAndSettle();
      expect(repo.calls, contains('print binv_1 en'));
      expect(saver.saved.single.$1, 'INV-2026-0001.html');
      expect(saver.saved.single.$2, contains('Portal invoice en'));
    });

    testWidgets('a void invoice says nothing is owed', (tester) async {
      sized(tester, 390, 4000);
      final repo = FakePortal()..invoiceDoc = invoiceJson(status: 'void');
      await pumpScreen(tester, repo,
          (c) => PortalInvoiceScreen(controller: c, invoiceId: 'binv_1'));
      expect(find.byKey(const Key('portal-invoice-void')), findsOneWidget);
      expect(find.textContaining('You do not need to pay it'), findsOneWidget);
      expect(find.byKey(const Key('portal-how-to-pay')), findsNothing);
    });

    testWidgets('empty', (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal()..invoiceRows = [];
      await pumpScreen(
          tester, repo, (c) => PortalInvoicesScreen(controller: c));
      expect(find.text('No invoices yet'), findsOneWidget);
    });
  });

  group('requests', () {
    testWidgets('list and a request with the replies sent to the customer',
        (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal();
      await pumpScreen(tester, repo, (c) => PortalTicketsScreen(controller: c));
      expect(find.text('Report export fails'), findsOneWidget);
      expect(
          find.text('T-1A2B3C · High · Fleet reporting pilot'), findsOneWidget);
      await tester.tap(find.byKey(const Key('portal-ticket-btkt_1')));
      await tester.pumpAndSettle();
      expect(find.text('The monthly export stops at 50%.'), findsOneWidget);
      expect(find.text('Fixed in the new release.'), findsOneWidget);
      expect(find.text('We are looking into it.'), findsOneWidget);
      expect(find.text('Opened by you'), findsOneWidget);
    });

    testWidgets('empty, with a way to open one', (tester) async {
      sized(tester, 390, 1400);
      final repo = FakePortal()..ticketRows = [];
      await pumpScreen(tester, repo, (c) => PortalTicketsScreen(controller: c));
      expect(find.text('No requests yet'), findsOneWidget);
      expect(find.byKey(const Key('portal-new-ticket')), findsOneWidget);
    });

    testWidgets('a new request is checked, sent, and opens', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()..ticketRows = [];
      await pumpScreen(tester, repo, (c) => PortalTicketsScreen(controller: c));
      await tester.tap(find.byKey(const Key('portal-new-ticket')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('portal-ticket-send')));
      await tester.pumpAndSettle();
      expect(
          find.text('Give it a title of 3 to 200 characters.'), findsOneWidget);
      expect(find.text('Tell the business what you need.'), findsOneWidget);
      expect(repo.ticketBody, isNull);
      await tester.enterText(
          find.byKey(const Key('portal-ticket-title')), 'Report export fails');
      await tester.enterText(find.byKey(const Key('portal-ticket-details')),
          'The monthly export stops at 50%.');
      await tester.tap(find.byKey(const Key('portal-ticket-severity')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('High').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('portal-ticket-project')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Fleet reporting pilot').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const Key('portal-ticket-send')));
      await tester.pumpAndSettle();
      expect(repo.ticketBody, {
        'title': 'Report export fails',
        'description': 'The monthly export stops at 50%.',
        'severity': 'high',
        'project_id': 'bproj_1',
      });
      // The new request opens.
      expect(find.text('We are looking into it.'), findsOneWidget);
    });

    testWidgets('the daily limit is shown as the API says it', (tester) async {
      sized(tester, 390, 3000);
      final repo = FakePortal()
        ..ticketError = const ApiException(429,
            'You can open 5 requests a day from this link. Add to an earlier one, or call the business.');
      await pumpScreen(
          tester, repo, (c) => PortalNewTicketScreen(controller: c));
      await tester.enterText(
          find.byKey(const Key('portal-ticket-title')), 'Another one');
      await tester.enterText(
          find.byKey(const Key('portal-ticket-details')), 'More details');
      await tester.tap(find.byKey(const Key('portal-ticket-send')));
      await tester.pumpAndSettle();
      expect(
          find.textContaining('You can open 5 requests a day'), findsOneWidget);
    });
  });

  for (final width in [320.0, 768.0, 1024.0]) {
    testWidgets('every portal screen lays out at $width wide with 200% text',
        (tester) async {
      sized(tester, width, 2400);
      final repo = FakePortal();
      await pumpHome(tester, repo, textScale: 2);
      expect(tester.takeException(), isNull);

      // Leaving a screen forgets the token, so each screen gets its own session.
      // Rows in their longest states: decided, expired, stale, waiting for you.
      FakePortal rich() => FakePortal()
        ..offerRows = [
          offerJson(
              id: 'o1', decision: decidedJson('approved'), canDecide: false),
          offerJson(id: 'o2', expired: true, canDecide: false),
          offerJson(id: 'o3', status: 'accepted', canDecide: false),
        ]
        ..changeRows = [changeJson(id: 'c1', stale: true), changeJson(id: 'c2')]
        ..ticketRows = [
          ticketSummaryJson('btkt_1', status: 'waiting_on_customer'),
          ticketSummaryJson('btkt_2', status: 'in_progress'),
        ];
      Future<void> screen(Widget Function(PortalController c) build) async {
        await pumpScreen(tester, rich(), build, textScale: 2);
        expect(tester.takeException(), isNull);
      }

      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpWidget(app(
          PortalEntryScreen(createSession: (_) => PortalController(repo)),
          textScale: 2));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await screen((c) => PortalOffersScreen(controller: c));
      await screen((c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'));
      await screen((c) => PortalChangesScreen(controller: c));
      await screen(
          (c) => PortalChangeScreen(controller: c, changeId: 'bchg_1'));
      await screen((c) => PortalInvoicesScreen(controller: c));
      await screen(
          (c) => PortalInvoiceScreen(controller: c, invoiceId: 'binv_1'));
      await screen((c) => PortalTicketsScreen(controller: c));
      await screen(
          (c) => PortalTicketScreen(controller: c, ticketId: 'btkt_1'));
      await screen((c) => PortalNewTicketScreen(controller: c));
      await screen((c) => const PortalLinkProblem(
          message:
              'This link has expired. Ask the business that sent it for a new one.'));

      // The confirm dialog at the largest text size.
      await pumpScreen(tester, FakePortal(),
          (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'),
          textScale: 2);
      await reveal(tester, find.byKey(const Key('portal-approve')));
      await tester.tap(find.byKey(const Key('portal-approve')));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.byKey(const Key('portal-confirm')), findsOneWidget);
    });
  }

  testWidgets('Arabic renders the portal right to left', (tester) async {
    sized(tester, 390, 1600);
    final repo = FakePortal();
    await pumpHome(tester, repo, locale: const Locale('ar'));
    expect(tester.takeException(), isNull);
    expect(find.text('العروض'), findsOneWidget);
    expect(find.text('الفواتير'), findsOneWidget);
    expect(find.text('الطلبات'), findsOneWidget);
    expect(find.text('مرحبًا Sara Khan'), findsOneWidget);
    expect(Directionality.of(tester.element(find.text('العروض'))),
        TextDirection.rtl);

    await pumpScreen(tester, FakePortal(),
        (c) => PortalOfferScreen(controller: c, offerId: 'bprop_1'),
        locale: const Locale('ar'));
    expect(find.text('موافقة'), findsOneWidget);
    expect(find.text('رفض'), findsOneWidget);
    await tester.tap(find.byKey(const Key('portal-approve')));
    await tester.pumpAndSettle();
    expect(find.text('الموافقة على هذا العرض؟'), findsOneWidget);
    expect(find.text('اسمك الكامل'), findsOneWidget);

    await pumpScreen(
        tester, FakePortal(), (c) => PortalEntryScreen(createSession: (_) => c),
        locale: const Locale('ar'));
    expect(find.text('افتح رابطك'), findsOneWidget);
  });

  testWidgets('Arabic clipboard text has no long dashes', (tester) async {
    sized(tester, 390, 1600);
    final saver = FakeSaver();
    final repo = FakePortal();
    String? copied;
    tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') {
        copied = (call.arguments as Map)['text'] as String;
      }
      return null;
    });
    addTearDown(() => tester.binding.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null));
    await pumpScreen(
        tester,
        repo,
        (c) => PortalInvoiceScreen(
            controller: c, invoiceId: 'binv_1', saver: saver),
        locale: const Locale('ar'));
    await tester.tap(find.byKey(const Key('portal-invoice-preview')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('inv-doc-copy')));
    await tester.pumpAndSettle();
    expect(copied, contains('فاتورة INV-2026-0001'));
    expect(copied!.contains('\u2014') || copied!.contains('\u2013'), isFalse);
    await tester.tap(find.byKey(const Key('inv-doc-save')));
    await tester.pumpAndSettle();
    // The Arabic page is asked for.
    expect(repo.calls, contains('print binv_1 ar'));
  });
}
