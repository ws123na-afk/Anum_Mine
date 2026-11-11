import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../records_kit/records_kit.dart';
import '../records_kit/records_export.dart';
import 'books_data.dart';
import 'books_extra_data.dart';

/// The rest of the business books on the phone, over the same API as the
/// web (docs/business-books.md, docs/business-tax.md): every journal
/// adjustment with its state, the manual journal entries (for their
/// supporting documents), the VAT filing settings and the receipts
/// dismissed for bills. The API decides who may read and change each one.

/// A status filter of the adjustments list: `''` is what is live (active
/// and pending, the API's default), `with_void` adds voided ones.
const adjustmentStatuses = ['', 'pending', 'active', 'with_void'];

/// One line of a journal entry, in the base currency.
class LedgerLine {
  const LedgerLine(
      {required this.account, required this.debit, required this.credit});
  factory LedgerLine.fromJson(JsonReader j) => LedgerLine(
        account: [
          j.optString('account_code') ?? '',
          j.optString('account_name') ?? '',
        ].where((part) => part.isNotEmpty).join(' '),
        debit: j.optString('debit') ?? '0.00',
        credit: j.optString('credit') ?? '0.00',
      );
  final String account, debit, credit;
  bool get isDebit => (double.tryParse(debit) ?? 0) != 0;
}

/// A manual journal entry (`LedgerEntry` with `source_kind: manual`).
class ManualEntry {
  const ManualEntry({
    required this.id,
    required this.entryDate,
    required this.description,
    required this.status,
    required this.total,
    this.lines = const [],
    this.evidenceCount = 0,
  });
  factory ManualEntry.fromJson(JsonReader j) => ManualEntry(
        id: j.string('id'),
        entryDate: j.string('entry_date'),
        description: j.optString('description') ?? '',
        status: j.optString('status') ?? 'posted',
        total: j.optString('total') ?? '0.00',
        lines: j.optObjects('lines', LedgerLine.fromJson),
        evidenceCount:
            (j['evidence'] is List) ? (j['evidence']! as List).length : 0,
      );
  final String id, entryDate, description, status, total;
  final List<LedgerLine> lines;
  final int evidenceCount;
  bool get reversed => status == 'reversed';
}

class ManualEntriesChunk extends RecordsChunk<ManualEntry> {
  ManualEntriesChunk(RecordsChunk<ManualEntry> page,
      {required this.baseCurrency, required this.canAccount})
      : super(page.items, next: page.next, total: page.total);
  final String baseCurrency;
  final bool canAccount;
}

/// Where the VAT return deadline comes from: the owner's own due day, a
/// built-in rule, a country without VAT, or none known
/// (docs/business-tax.md#filing-deadlines).
enum DeadlineSource { owner, preset, noVat, none }

/// Countries with a built-in deadline when the jurisdiction is Other, and
/// those without VAT; the same lists as the API's `filing_presets` and
/// `no_vat_countries`, which win when sent.
const presetCountries = ['BH', 'OM'];
const noVatCountries = ['KW', 'QA'];
const maxDueDays = 120;

/// What the settings form holds while it is edited.
class TaxFilingForm {
  const TaxFilingForm({
    required this.jurisdiction,
    required this.frequency,
    required this.cycleMonth,
    this.country = '',
    this.dueDays = '',
  });
  final String jurisdiction, frequency, country, dueDays;
  final int cycleMonth;

  TaxFilingForm copyWith(
          {String? jurisdiction,
          String? frequency,
          int? cycleMonth,
          String? country,
          String? dueDays}) =>
      TaxFilingForm(
        jurisdiction: jurisdiction ?? this.jurisdiction,
        frequency: frequency ?? this.frequency,
        cycleMonth: cycleMonth ?? this.cycleMonth,
        country: country ?? this.country,
        dueDays: dueDays ?? this.dueDays,
      );

  /// The same order as the API: the owner's due day, a preset, no VAT,
  /// none.
  DeadlineSource source(
      {List<String> presets = presetCountries,
      List<String> noVat = noVatCountries}) {
    if (dueDays.trim().isNotEmpty) return DeadlineSource.owner;
    if (jurisdiction != 'OTHER') return DeadlineSource.preset;
    final code = country.trim().toUpperCase();
    if (presets.contains(code)) return DeadlineSource.preset;
    if (noVat.contains(code)) return DeadlineSource.noVat;
    return DeadlineSource.none;
  }

  /// The built-in rule's string key, or null: KSA and Bahrain the end of
  /// the next month, the UAE 28 days, Oman 30 days.
  String? presetRule() {
    final code =
        jurisdiction == 'OTHER' ? country.trim().toUpperCase() : jurisdiction;
    return switch (code) {
      'SA' || 'BH' => 'deadline.endOfNextMonth',
      'AE' => 'deadline.days28',
      'OM' => 'deadline.days30',
      _ => null,
    };
  }

  /// The string key of the first problem, or null when it can be saved.
  String? problem() {
    final code = country.trim();
    if (jurisdiction == 'OTHER' &&
        code.isNotEmpty &&
        !RegExp(r'^[A-Za-z]{2}$').hasMatch(code)) {
      return 'problem.country';
    }
    if (jurisdiction == 'OTHER' && ['SA', 'AE'].contains(code.toUpperCase())) {
      return 'problem.countryJurisdiction';
    }
    final due = dueDays.trim();
    if (due.isNotEmpty) {
      final n = RegExp(r'^\d{1,3}$').hasMatch(due) ? int.parse(due) : 0;
      if (n < 1 || n > maxDueDays) return 'problem.dueDays';
    }
    return null;
  }
}

/// The tax settings and what the form offers (`GET /tax`).
class TaxOverview {
  const TaxOverview({
    required this.settings,
    required this.canEdit,
    this.jurisdictions = const ['SA', 'AE', 'OTHER'],
    this.frequencies = const ['monthly', 'quarterly'],
    this.presets = presetCountries,
    this.noVat = noVatCountries,
    this.maxDue = maxDueDays,
  });
  factory TaxOverview.fromJson(JsonReader j) => TaxOverview(
        settings: j.optObject('settings')?.json,
        canEdit: j.optBool('can_edit') ?? false,
        jurisdictions:
            j.optStrings('jurisdictions') ?? const ['SA', 'AE', 'OTHER'],
        frequencies:
            j.optStrings('frequencies') ?? const ['monthly', 'quarterly'],
        presets: j.optStrings('filing_presets') ?? presetCountries,
        noVat: j.optStrings('no_vat_countries') ?? noVatCountries,
        maxDue: j.optInt('max_due_days') ?? maxDueDays,
      );

  /// The saved settings as the API sends them; null before tax is set up.
  final Map<String, Object?>? settings;
  final bool canEdit;
  final List<String> jurisdictions, frequencies, presets, noVat;
  final int maxDue;

  int? get version => switch (settings?['version']) {
        final num n => n.toInt(),
        _ => null,
      };

  /// The form as saved, or proposed from the books' country.
  TaxFilingForm form({String? booksCountry}) {
    final s = settings;
    final country = booksCountry?.toUpperCase();
    final jurisdiction = s?['jurisdiction'] as String? ??
        (country == 'SA' || country == 'AE' ? country! : 'OTHER');
    final due = s?['filing_due_days'];
    return TaxFilingForm(
      jurisdiction: jurisdiction,
      frequency: s?['filing_frequency'] as String? ?? 'quarterly',
      cycleMonth: switch (s?['period_start_month']) {
        final num n => n.toInt(),
        _ => 1,
      },
      country: s != null
          ? (s['country'] as String? ?? '')
          : (jurisdiction == 'OTHER' && country != null ? country : ''),
      dueDays: due is num ? '${due.toInt()}' : '',
    );
  }

  /// The full body of `PUT /tax/settings`: the filing fields from [form],
  /// every other field (registration, ZATCA, seller address) as saved.
  Map<String, Object?> body(TaxFilingForm form) {
    final s = settings ?? const <String, Object?>{};
    String text(String key) => s[key] as String? ?? '';
    final country = form.country.trim().toUpperCase();
    final due = form.dueDays.trim();
    return {
      'jurisdiction': form.jurisdiction,
      'registered': s['registered'] as bool? ?? false,
      'registration_number': text('registration_number'),
      'registration_date': s['registration_date'],
      'filing_frequency': form.frequency,
      'period_start_month': form.cycleMonth,
      'country':
          form.jurisdiction == 'OTHER' && country.isNotEmpty ? country : null,
      'filing_due_days': due.isEmpty ? null : int.parse(due),
      'zatca_enabled':
          form.jurisdiction == 'SA' && (s['zatca_enabled'] as bool? ?? false),
      'seller_crn': text('seller_crn'),
      'seller_street': text('seller_street'),
      'seller_building': text('seller_building'),
      'seller_district': text('seller_district'),
      'seller_city': text('seller_city'),
      'seller_postal_code': text('seller_postal_code'),
      'version': version,
    };
  }
}

/// A receipt dismissed for one bill (`BooksReceiptDismissal`).
class ReceiptDismissal {
  const ReceiptDismissal({
    required this.id,
    required this.billId,
    required this.documentId,
    required this.dismissedAt,
    this.billReference = '',
    this.vendor = '',
    this.amount,
    this.currency,
    this.documentTitle = '',
    this.billFound = true,
    this.documentFound = true,
  });
  factory ReceiptDismissal.fromJson(JsonReader j) => ReceiptDismissal(
        id: j.string('id'),
        billId: j.optString('bill_id') ?? '',
        documentId: j.optString('document_id') ?? '',
        dismissedAt: j.optString('dismissed_at') ?? '',
        billReference: j.optString('bill_reference') ?? '',
        vendor: j.optString('vendor') ?? '',
        amount: j.optString('amount'),
        currency: j.optString('currency'),
        documentTitle: j.optString('document_title') ?? '',
        billFound: j.optBool('bill_found') ?? true,
        documentFound: j.optBool('document_found') ?? true,
      );
  final String id, billId, documentId, dismissedAt;
  final String billReference, vendor, documentTitle;
  final String? amount, currency;
  final bool billFound, documentFound;
}

abstract interface class BooksMoreRepository {
  /// Adjustments, one page: [status] is one of [adjustmentStatuses] and
  /// [kind] one of `adjustmentKinds` or `''`.
  Future<RecordsChunk<BooksAdjustment>> adjustments(
      {String kind, String status, String? token});

  /// Manual journal entries, newest first, one page.
  Future<ManualEntriesChunk> manualEntries({String? token});
  Future<TaxOverview> tax();
  Future<void> saveTax(Map<String, Object?> body);
  Future<RecordsChunk<ReceiptDismissal>> dismissedReceipts({String? token});
  Future<void> restoreReceipt(String dismissalId);

  /// Attaches or dismisses a receipt proposed for a bill
  /// (docs/business-books.md#receipts-to-bills).
  Future<void> acceptReceipt(String billId, String documentId);
  Future<void> dismissReceipt(String billId, String documentId);

  /// The fixed asset register for the books' current period, one page.
  Future<AssetRegister> assets(
      {String status = 'all', String query = '', int offset = 0});
  Future<String> assetsCsv();
  Future<BookAsset> asset(String id);
  Future<void> updateAsset(String id, Map<String, Object?> body);
  Future<void> disposeAsset(String id, Map<String, Object?> body);
  Future<void> askImpairment(String assetId, Map<String, Object?> body);
  Future<void> decideImpairment(
      String assetId, String impairmentId, bool approve, int version);

  /// The business's loans, one page; a loan with its schedule.
  Future<RecordsChunk<BookLoan>> loans(
      {String status = 'active', String? token});
  Future<BookLoan> loan(String id);
  Future<void> createLoan(Map<String, Object?> body,
      {required String idempotencyKey});
  Future<void> recordRepayment(String loanId, Map<String, Object?> body,
      {required String idempotencyKey});
  Future<void> voidRepayment(String loanId, String repaymentId, String reason);
  Future<void> voidLoan(String loanId, String reason);

  /// The chart a manual entry posts to, and the entry itself.
  Future<List<LedgerAccount>> ledgerAccounts();
  Future<void> createManualEntry(Map<String, Object?> body,
      {required String idempotencyKey});
}

class ApiBooksMoreRepository implements BooksMoreRepository {
  const ApiBooksMoreRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business/books';
  static const pageSize = 25;
  String _id(String id) => Uri.encodeComponent(id);

  @override
  Future<RecordsChunk<BooksAdjustment>> adjustments(
      {String kind = '', String status = '', String? token}) async {
    final body = JsonReader(await api.request(
        'GET',
        '$_base/adjustments${recordsQuery({
              'kind': kind,
              if (status == 'with_void') 'include_void': 'true',
              if (status == 'pending' || status == 'active') 'status': status,
              'offset': token ?? 0,
              'limit': pageSize,
            })}'));
    return RecordsChunk.offsetPage(body, BooksAdjustment.fromJson);
  }

  @override
  Future<ManualEntriesChunk> manualEntries({String? token}) async {
    final body = JsonReader(await api.request(
        'GET',
        '$_base/ledger/journal${recordsQuery({
              'source_kind': 'manual',
              'offset': token ?? 0,
              'limit': pageSize,
            })}'));
    return ManualEntriesChunk(
        RecordsChunk.offsetPage(body, ManualEntry.fromJson),
        baseCurrency: body.optString('base_currency') ?? '',
        canAccount: body.optBool('can_account') ?? false);
  }

  @override
  Future<TaxOverview> tax() async =>
      TaxOverview.fromJson(JsonReader(await api.request('GET', '$_base/tax')));

  @override
  Future<void> saveTax(Map<String, Object?> body) =>
      api.request('PUT', '$_base/tax/settings', body: body);

  @override
  Future<RecordsChunk<ReceiptDismissal>> dismissedReceipts(
      {String? token}) async {
    final body = JsonReader(await api.request(
        'GET',
        '$_base/evidence/receipt-matches/dismissed${recordsQuery({
              'offset': token ?? 0,
              'limit': pageSize,
            })}'));
    return RecordsChunk.offsetPage(body, ReceiptDismissal.fromJson);
  }

  @override
  Future<void> restoreReceipt(String dismissalId) => api.request('DELETE',
      '$_base/evidence/receipt-matches/dismissed/${_id(dismissalId)}');

  @override
  Future<void> acceptReceipt(String billId, String documentId) =>
      api.request('POST', '$_base/evidence/receipt-matches/accept',
          body: {'bill_id': billId, 'document_id': documentId});

  @override
  Future<void> dismissReceipt(String billId, String documentId) =>
      api.request('POST', '$_base/evidence/receipt-matches/dismiss',
          body: {'bill_id': billId, 'document_id': documentId});

  @override
  Future<AssetRegister> assets(
          {String status = 'all', String query = '', int offset = 0}) async =>
      AssetRegister.fromJson(JsonReader(await api.request(
          'GET',
          '$_base/assets${recordsQuery({
                'status': status,
                'q': query,
                'offset': offset,
                'limit': pageSize,
              })}')));

  @override
  Future<String> assetsCsv() => fetchCsv(api, '$_base/assets.csv');

  @override
  Future<BookAsset> asset(String id) async => BookAsset.fromJson(
      JsonReader(await api.request('GET', '$_base/assets/${_id(id)}')));

  @override
  Future<void> updateAsset(String id, Map<String, Object?> body) =>
      api.request('PATCH', '$_base/assets/${_id(id)}', body: body);

  @override
  Future<void> disposeAsset(String id, Map<String, Object?> body) =>
      api.request('POST', '$_base/assets/${_id(id)}/dispose', body: body);

  @override
  Future<void> askImpairment(String assetId, Map<String, Object?> body) => api
      .request('POST', '$_base/assets/${_id(assetId)}/impairments', body: body);

  @override
  Future<void> decideImpairment(
          String assetId, String impairmentId, bool approve, int version) =>
      api.request(
          'POST',
          '$_base/assets/${_id(assetId)}/impairments/${_id(impairmentId)}/'
              '${approve ? 'approve' : 'reject'}',
          body: {'version': version});

  @override
  Future<RecordsChunk<BookLoan>> loans(
      {String status = 'active', String? token}) async {
    final body = JsonReader(await api.request(
        'GET',
        '$_base/loans${recordsQuery({
              'status': status,
              'offset': token ?? 0,
              'limit': pageSize,
            })}'));
    return RecordsChunk.offsetPage(body, BookLoan.fromJson);
  }

  @override
  Future<BookLoan> loan(String id) async => BookLoan.fromJson(
      JsonReader(await api.request('GET', '$_base/loans/${_id(id)}')));

  @override
  Future<void> createLoan(Map<String, Object?> body,
          {required String idempotencyKey}) =>
      api.request('POST', '$_base/loans',
          body: body, headers: {'Idempotency-Key': idempotencyKey});

  @override
  Future<void> recordRepayment(String loanId, Map<String, Object?> body,
          {required String idempotencyKey}) =>
      api.request('POST', '$_base/loans/${_id(loanId)}/repayments',
          body: body, headers: {'Idempotency-Key': idempotencyKey});

  @override
  Future<void> voidRepayment(
          String loanId, String repaymentId, String reason) =>
      api.request('POST',
          '$_base/loans/${_id(loanId)}/repayments/${_id(repaymentId)}/void',
          body: {'reason': reason});

  @override
  Future<void> voidLoan(String loanId, String reason) =>
      api.request('POST', '$_base/loans/${_id(loanId)}/void',
          body: {'reason': reason});

  @override
  Future<List<LedgerAccount>> ledgerAccounts() async =>
      JsonReader(await api.request('GET', '$_base/ledger/accounts'))
          .optObjects('items', LedgerAccount.fromJson);

  @override
  Future<void> createManualEntry(Map<String, Object?> body,
          {required String idempotencyKey}) =>
      api.request('POST', '$_base/ledger/journal',
          body: body, headers: {'Idempotency-Key': idempotencyKey});
}
