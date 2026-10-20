import 'dart:math' as math;
import 'dart:typed_data';

import 'package:intl/intl.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Customer billing (docs/billing.md): what an organisation billing admin (a
/// workspace owner or an organisation administrator) reads and changes from
/// the phone. Money is an integer number of minor units plus an ISO 4217
/// currency; SAR is the default.

/// One usage meter of the current month. [limit] null is unlimited; storage
/// is counted in bytes.
class BlMeter {
  const BlMeter(
      {required this.key, required this.used, this.limit, required this.unit});
  final String key, unit;
  final int used;
  final int? limit;

  factory BlMeter.fromJson(JsonReader json) => BlMeter(
        key: json.string('key'),
        used: json.optInt('used') ?? 0,
        limit: json.optInt('limit'),
        unit: json.optString('unit') ?? '',
      );

  /// Share of the limit in use, 0 to 1 (more than 1 when over), or null
  /// when unlimited.
  double? get ratio {
    final max = limit;
    if (max == null) return null;
    if (max <= 0) return used > 0 ? 1.5 : 0;
    return used / max;
  }
}

class BlSubscription {
  const BlSubscription({
    required this.id,
    required this.planId,
    required this.planName,
    required this.priceMinor,
    required this.currency,
    required this.interval,
    required this.perSeat,
    required this.seats,
    required this.status,
    required this.cancelAtPeriodEnd,
    this.trialEnd,
    this.currentPeriodEnd,
    this.pendingPlanId,
    this.graceUntil,
  });
  final String id, planId, planName, currency, interval, status;
  final int priceMinor, seats;
  final bool perSeat, cancelAtPeriodEnd;
  final DateTime? trialEnd, currentPeriodEnd, graceUntil;
  final String? pendingPlanId;

  factory BlSubscription.fromJson(JsonReader json) => BlSubscription(
        id: json.string('id'),
        planId: json.optString('plan_id') ?? '',
        planName: json.optString('plan_name') ?? '',
        priceMinor: json.optInt('price_minor') ?? 0,
        currency: json.optString('currency') ?? 'SAR',
        interval: json.optString('interval') ?? 'month',
        perSeat: json.optBool('per_seat') ?? false,
        seats: json.optInt('seats') ?? 1,
        status: json.optString('status') ?? 'active',
        cancelAtPeriodEnd: json.optBool('cancel_at_period_end') ?? false,
        trialEnd: json.optDate('trial_end'),
        currentPeriodEnd: json.optDate('current_period_end'),
        pendingPlanId: json.optString('pending_plan_id'),
        graceUntil: json.optDate('grace_until'),
      );

  bool get isCancelled => status == 'cancelled';
}

class BlAccount {
  const BlAccount({
    this.displayName = '',
    this.contactName = '',
    this.contactEmail = '',
    this.companyName = '',
    this.addressLine1 = '',
    this.addressLine2 = '',
    this.city = '',
    this.postalCode = '',
    this.country = '',
    this.vatNumber = '',
    this.creditBalanceMinor = 0,
  });
  final String displayName, contactName, contactEmail, companyName;
  final String addressLine1, addressLine2, city, postalCode, country, vatNumber;
  final int creditBalanceMinor;

  factory BlAccount.fromJson(JsonReader json) => BlAccount(
        displayName: json.optString('display_name') ?? '',
        contactName: json.optString('contact_name') ?? '',
        contactEmail: json.optString('contact_email') ?? '',
        companyName: json.optString('company_name') ?? '',
        addressLine1: json.optString('address_line1') ?? '',
        addressLine2: json.optString('address_line2') ?? '',
        city: json.optString('city') ?? '',
        postalCode: json.optString('postal_code') ?? '',
        country: json.optString('country') ?? '',
        vatNumber: json.optString('vat_number') ?? '',
        creditBalanceMinor: json.optInt('credit_balance_minor') ?? 0,
      );

  /// The address on one line, without empty parts.
  String get address => [addressLine1, addressLine2, city, postalCode, country]
      .where((part) => part.trim().isNotEmpty)
      .join(', ');
}

/// The issuer's bank account for transfers; [enabled] is false when the
/// issuer has not entered an IBAN.
class BlBankDetails {
  const BlBankDetails(
      {this.enabled = false,
      this.bankName = '',
      this.accountName = '',
      this.iban = '',
      this.swift = ''});
  final bool enabled;
  final String bankName, accountName, iban, swift;

  factory BlBankDetails.fromJson(JsonReader json) => BlBankDetails(
        enabled: json.optBool('enabled') ?? false,
        bankName: json.optString('bank_name') ?? '',
        accountName: json.optString('account_name') ?? '',
        iban: json.optString('iban') ?? '',
        swift: json.optString('swift') ?? '',
      );
}

class BlOverview {
  const BlOverview({
    required this.account,
    required this.subscription,
    required this.period,
    required this.meters,
    required this.readOnly,
    required this.openBalanceMinor,
    required this.overdue,
    required this.currency,
    required this.bank,
    required this.cardEnabled,
    required this.issuerName,
    required this.issuerEmail,
  });
  final BlAccount? account;
  final BlSubscription? subscription;
  final String period, currency, issuerName, issuerEmail;
  final List<BlMeter> meters;
  final bool readOnly, overdue, cardEnabled;
  final int openBalanceMinor;
  final BlBankDetails bank;

  factory BlOverview.fromJson(JsonReader json) {
    final usage = json.optObject('usage');
    final payment = json.optObject('payment');
    final bank = payment?.optObject('bank_transfer');
    final issuer = json.optObject('issuer');
    final account = json.optObject('account');
    final subscription = json.optObject('subscription');
    return BlOverview(
      account: account == null ? null : BlAccount.fromJson(account),
      subscription:
          subscription == null ? null : BlSubscription.fromJson(subscription),
      period: usage?.optString('period') ?? '',
      meters: usage?.optObjects('meters', BlMeter.fromJson) ?? const [],
      readOnly: json.optString('state') == 'read_only',
      openBalanceMinor: json.optInt('open_balance_minor') ?? 0,
      overdue: json.optBool('overdue') ?? false,
      currency: json.optString('currency') ?? 'SAR',
      bank: bank == null ? const BlBankDetails() : BlBankDetails.fromJson(bank),
      cardEnabled: payment?.optBool('card_enabled') ?? false,
      issuerName: issuer?.optString('name') ?? '',
      issuerEmail: issuer?.optString('email') ?? '',
    );
  }
}

class BlPlan {
  const BlPlan({
    required this.id,
    required this.code,
    required this.name,
    required this.description,
    required this.priceMinor,
    required this.currency,
    required this.interval,
    required this.trialDays,
    required this.perSeat,
    required this.limits,
    required this.features,
    required this.current,
  });
  final String id, code, name, description, currency, interval;
  final int priceMinor, trialDays;
  final bool perSeat, current;

  /// `seats`, `workspaces`, `agent_runs_per_month`, `storage_gb`,
  /// `messages_per_month`; a null value is unlimited.
  final Map<String, int?> limits;
  final List<String> features;

  factory BlPlan.fromJson(JsonReader json) {
    final raw = json.optMap('limits') ?? const {};
    return BlPlan(
      id: json.string('id'),
      code: json.optString('code') ?? '',
      name: json.optString('name') ?? '',
      description: json.optString('description') ?? '',
      priceMinor: json.optInt('price_minor') ?? 0,
      currency: json.optString('currency') ?? 'SAR',
      interval: json.optString('interval') ?? 'month',
      trialDays: json.optInt('trial_days') ?? 0,
      perSeat: json.optBool('per_seat') ?? false,
      limits: {
        for (final entry in raw.entries)
          entry.key: switch (entry.value) {
            final num value => value.toInt(),
            _ => null,
          }
      },
      features: json.optStrings('features') ?? const [],
      current: json.optBool('current') ?? false,
    );
  }
}

/// What changing to a plan would cost, and which limits the current usage
/// is over (then the change is refused).
class BlPreview {
  const BlPreview({
    required this.effective,
    required this.amountMinor,
    required this.taxMinor,
    required this.totalMinor,
    required this.creditMinor,
    required this.blockedBy,
    required this.currency,
  });
  final String effective, currency;
  final int amountMinor, taxMinor, totalMinor, creditMinor;
  final List<BlMeter> blockedBy;

  factory BlPreview.fromJson(JsonReader json) => BlPreview(
        effective: json.optString('effective') ?? 'now',
        amountMinor: json.optInt('amount_minor') ?? 0,
        taxMinor: json.optInt('tax_minor') ?? 0,
        totalMinor: json.optInt('total_minor') ?? 0,
        creditMinor: json.optInt('credit_minor') ?? 0,
        blockedBy: json.optObjects('blocked_by', BlMeter.fromJson),
        currency: json.optString('currency') ?? 'SAR',
      );
}

class BlInvoice {
  const BlInvoice({
    required this.id,
    required this.number,
    required this.status,
    required this.kind,
    required this.currency,
    required this.subtotalMinor,
    required this.taxRateBp,
    required this.taxCountry,
    required this.taxMinor,
    required this.totalMinor,
    required this.amountPaidMinor,
    required this.amountCreditedMinor,
    required this.amountDueMinor,
    required this.overdue,
    this.periodStart,
    this.periodEnd,
    this.issuedAt,
    this.dueAt,
  });
  final String id, number, status, kind, currency, taxCountry;
  final int subtotalMinor, taxRateBp, taxMinor, totalMinor;
  final int amountPaidMinor, amountCreditedMinor, amountDueMinor;
  final bool overdue;
  final DateTime? periodStart, periodEnd, issuedAt, dueAt;

  factory BlInvoice.fromJson(JsonReader json) => BlInvoice(
        id: json.string('id'),
        number: json.optString('number') ?? '',
        status: json.optString('status') ?? 'draft',
        kind: json.optString('kind') ?? 'subscription',
        currency: json.optString('currency') ?? 'SAR',
        subtotalMinor: json.optInt('subtotal_minor') ?? 0,
        taxRateBp: json.optInt('tax_rate_bp') ?? 0,
        taxCountry: json.optString('tax_country') ?? '',
        taxMinor: json.optInt('tax_minor') ?? 0,
        totalMinor: json.optInt('total_minor') ?? 0,
        amountPaidMinor: json.optInt('amount_paid_minor') ?? 0,
        amountCreditedMinor: json.optInt('amount_credited_minor') ?? 0,
        amountDueMinor: json.optInt('amount_due_minor') ?? 0,
        overdue: json.optBool('overdue') ?? false,
        periodStart: json.optDate('period_start'),
        periodEnd: json.optDate('period_end'),
        issuedAt: json.optDate('issued_at'),
        dueAt: json.optDate('due_at'),
      );

  /// Open with something left to pay.
  bool get payable => status == 'open' && amountDueMinor > 0;
}

class BlLine {
  const BlLine(
      {required this.description,
      required this.quantity,
      required this.unitMinor,
      required this.amountMinor,
      required this.kind});
  final String description, kind;
  final int quantity, unitMinor, amountMinor;

  factory BlLine.fromJson(JsonReader json) => BlLine(
        description: json.optString('description') ?? '',
        quantity: json.optInt('quantity') ?? 1,
        unitMinor: json.optInt('unit_minor') ?? 0,
        amountMinor: json.optInt('amount_minor') ?? 0,
        kind: json.optString('kind') ?? 'plan',
      );
}

class BlPayment {
  const BlPayment(
      {required this.amountMinor,
      required this.method,
      required this.reference,
      required this.hasProof,
      this.receivedAt});
  final int amountMinor;
  final String method, reference;
  final bool hasProof;
  final DateTime? receivedAt;

  factory BlPayment.fromJson(JsonReader json) => BlPayment(
        amountMinor: json.optInt('amount_minor') ?? 0,
        method: json.optString('method') ?? 'other',
        reference: json.optString('reference') ?? '',
        hasProof: json.optBool('has_proof') ?? false,
        receivedAt: json.optDate('received_at'),
      );
}

class BlCreditNote {
  const BlCreditNote(
      {required this.number,
      required this.reason,
      required this.totalMinor,
      this.issuedAt});
  final String number, reason;
  final int totalMinor;
  final DateTime? issuedAt;

  factory BlCreditNote.fromJson(JsonReader json) => BlCreditNote(
        number: json.optString('number') ?? '',
        reason: json.optString('reason') ?? '',
        totalMinor: json.optInt('total_minor') ?? 0,
        issuedAt: json.optDate('issued_at'),
      );
}

/// One invoice with its lines, payments, credit notes and the snapshots of
/// who billed whom when it was issued.
class BlInvoiceDetail {
  const BlInvoiceDetail({
    required this.summary,
    required this.billTo,
    required this.issuer,
    required this.bank,
    required this.lines,
    required this.payments,
    required this.creditNotes,
  });
  final BlInvoice summary;
  final Map<String, String> billTo, issuer;
  final BlBankDetails bank;
  final List<BlLine> lines;
  final List<BlPayment> payments;
  final List<BlCreditNote> creditNotes;

  static Map<String, String> _texts(Map<String, Object?>? raw) => {
        for (final entry in (raw ?? const <String, Object?>{}).entries)
          if (entry.value case final String text when text.trim().isNotEmpty)
            entry.key: text
      };

  factory BlInvoiceDetail.fromJson(JsonReader json) {
    final bank = json.optObject('bank_details');
    return BlInvoiceDetail(
      summary: BlInvoice.fromJson(json),
      billTo: _texts(json.optMap('bill_to')),
      issuer: _texts(json.optMap('issuer')),
      bank: bank == null ? const BlBankDetails() : BlBankDetails.fromJson(bank),
      lines: json.optObjects('lines', BlLine.fromJson),
      payments: json.optObjects('payments', BlPayment.fromJson),
      creditNotes: json.optObjects('credit_notes', BlCreditNote.fromJson),
    );
  }
}

class BlInvoicePage {
  const BlInvoicePage(this.items, this.total, this.offset);
  final List<BlInvoice> items;
  final int total, offset;
}

/// [minor] units of [currency] in the reader's locale, e.g. "SAR 1,150.00".
String blMoney(int minor, String currency, String locale) {
  final format = NumberFormat.currency(
      locale: _numberLocale(locale), name: currency.toUpperCase());
  final digits = format.decimalDigits ?? 2;
  return format.format(minor / math.pow(10, digits));
}

String _numberLocale(String locale) {
  try {
    return NumberFormat.currency(locale: locale).locale;
  } on Object {
    return 'en';
  }
}

/// Bytes as gigabytes with one decimal, e.g. "2.5".
String blGigabytes(int bytes, String locale) {
  final format = NumberFormat.decimalPatternDigits(
      locale: _numberLocale(locale), decimalDigits: 1);
  return format.format(bytes / (1024 * 1024 * 1024));
}

/// A tax rate in basis points as a percentage, e.g. 1500 to "15".
String blRate(int basisPoints) {
  final value = basisPoints / 100;
  return value == value.roundToDouble()
      ? value.toStringAsFixed(0)
      : value.toStringAsFixed(2).replaceFirst(RegExp(r'0$'), '');
}

abstract interface class BillingRepository {
  Future<BlOverview> overview();
  Future<List<BlPlan>> plans();
  Future<BlPreview> preview(String planId);
  Future<void> change(String planId);
  Future<void> cancel();
  Future<void> reactivate();
  Future<BlInvoicePage> invoices(
      {String query = '', String? status, int offset = 0, int limit = 20});
  Future<BlInvoiceDetail> invoice(String id);

  /// The invoice PDF saved through the system save dialog; false when the
  /// person cancelled.
  Future<bool> savePdf(BlInvoice invoice);

  /// A Stripe Checkout address for paying [id] by card (only when the
  /// issuer turned card payments on).
  Future<Uri> checkout(String id);
}

class ApiBillingRepository implements BillingRepository {
  const ApiBillingRepository(this.api,
      {required this.download, required this.save});
  final AnumApiClient api;

  /// The bytes of an API download (the JSON client cannot carry them).
  final Future<Uint8List> Function(String path) download;

  /// Saves bytes as a file name with the system dialog; false on cancel.
  final Future<bool> Function(String fileName, Uint8List bytes) save;
  static const _base = '/api/v1/billing';

  @override
  Future<BlOverview> overview() async => BlOverview.fromJson(
      JsonReader(await api.request('GET', '$_base/overview')));

  @override
  Future<List<BlPlan>> plans() async {
    final body = await api.request('GET', '$_base/plans');
    // A JSON list arrives wrapped as {"data": [...]} by the transport.
    return JsonReader(body).optObjects('data', BlPlan.fromJson);
  }

  @override
  Future<BlPreview> preview(String planId) async =>
      BlPreview.fromJson(JsonReader(await api.request('GET',
          '$_base/subscription/preview?plan_id=${Uri.encodeQueryComponent(planId)}')));

  @override
  Future<void> change(String planId) => api
      .request('POST', '$_base/subscription/change', body: {'plan_id': planId});

  @override
  Future<void> cancel() => api.request('POST', '$_base/subscription/cancel');

  @override
  Future<void> reactivate() =>
      api.request('POST', '$_base/subscription/reactivate');

  @override
  Future<BlInvoicePage> invoices(
      {String query = '',
      String? status,
      int offset = 0,
      int limit = 20}) async {
    final params = <String, String>{
      if (query.trim().isNotEmpty) 'q': query.trim(),
      if (status != null) 'status': status,
      'limit': '$limit',
      'offset': '$offset',
    };
    final json = JsonReader(await api.request(
        'GET', '$_base/invoices?${Uri(queryParameters: params).query}'));
    return BlInvoicePage(json.objects('items', BlInvoice.fromJson),
        json.integer('total'), json.optInt('offset') ?? offset);
  }

  @override
  Future<BlInvoiceDetail> invoice(String id) async =>
      BlInvoiceDetail.fromJson(JsonReader(await api.request(
          'GET', '$_base/invoices/${Uri.encodeComponent(id)}')));

  @override
  Future<bool> savePdf(BlInvoice invoice) async {
    final bytes = await download(
        '$_base/invoices/${Uri.encodeComponent(invoice.id)}/pdf');
    final name = invoice.number.isEmpty ? invoice.id : invoice.number;
    return save('$name.pdf', bytes);
  }

  @override
  Future<Uri> checkout(String id) async {
    final json = JsonReader(await api.request(
        'POST', '$_base/invoices/${Uri.encodeComponent(id)}/checkout'));
    final url = Uri.tryParse(json.string('url'));
    if (url == null || url.scheme != 'https') {
      throw const ApiException(
          502, 'The card payment page address is not valid.');
    }
    return url;
  }
}
