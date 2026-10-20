import '../../data/json_read.dart';

/// Invoices, the billing profile and the customer portal's owner side
/// (docs/business.md#invoices, docs/business-portal.md): what the phone reads
/// from `/api/v1/business`. Same wire shapes as packages/contracts
/// (business.ts). The server computes every net, tax and total.

const invoiceStatuses = ['draft', 'pending_approval', 'sent', 'paid', 'void'];
const paymentMethods = ['bank_transfer', 'cash', 'card', 'cheque', 'other'];
const lineKinds = ['service', 'labor', 'external_cost', 'other'];

/// The date part of an ISO date or date-time as `YYYY-MM-DD`, or `-`.
String dayOf(String? value) {
  if (value == null || value.length < 10) return '-';
  return value.substring(0, 10);
}

String dayOfDate(DateTime? value) {
  if (value == null) return '-';
  final local = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)}';
}

/// Who issues the invoices and how they are paid (`GET/PUT /billing-profile`).
class BillingProfile {
  const BillingProfile({
    this.legalName = '',
    this.address = '',
    this.taxLabel = 'Tax registration number',
    this.taxNumber = '',
    this.email = '',
    this.phone = '',
    this.bankDetails = '',
    this.footerNote = '',
    this.invoicePrefix = 'INV',
    this.defaultDueDays = 14,
    this.defaultTaxRatePct = 0,
  });
  final String legalName,
      address,
      taxLabel,
      taxNumber,
      email,
      phone,
      bankDetails,
      footerNote,
      invoicePrefix;
  final int defaultDueDays;
  final double defaultTaxRatePct;

  factory BillingProfile.fromJson(JsonReader r) => BillingProfile(
        legalName: r.optString('legal_name') ?? '',
        address: r.optString('address') ?? '',
        taxLabel: r.optString('tax_label') ?? '',
        taxNumber: r.optString('tax_number') ?? '',
        email: r.optString('email') ?? '',
        phone: r.optString('phone') ?? '',
        bankDetails: r.optString('bank_details') ?? '',
        footerNote: r.optString('footer_note') ?? '',
        invoicePrefix: r.optString('invoice_prefix') ?? 'INV',
        defaultDueDays: r.optInt('default_due_days') ?? 14,
        defaultTaxRatePct: r.optNumber('default_tax_rate_pct') ?? 0,
      );

  Map<String, Object?> toJson() => {
        'legal_name': legalName,
        'address': address,
        'tax_label': taxLabel,
        'tax_number': taxNumber,
        'email': email,
        'phone': phone,
        'bank_details': bankDetails,
        'footer_note': footerNote,
        'invoice_prefix': invoicePrefix,
        'default_due_days': defaultDueDays,
        'default_tax_rate_pct': defaultTaxRatePct,
      };
}

class InvoiceLine {
  const InvoiceLine({
    required this.description,
    required this.kind,
    required this.quantity,
    required this.unitPrice,
    required this.taxRatePct,
    required this.source,
    required this.net,
    required this.tax,
    required this.total,
  });
  final String description, kind, source;
  final double quantity, unitPrice, taxRatePct, net, tax, total;

  bool get manual => source == 'manual';

  factory InvoiceLine.fromJson(JsonReader r) => InvoiceLine(
        description: r.string('description'),
        kind: r.optString('kind') ?? 'service',
        quantity: r.number('quantity'),
        unitPrice: r.number('unit_price'),
        taxRatePct: r.optNumber('tax_rate_pct') ?? 0,
        source: r.optString('source') ?? 'manual',
        net: r.number('net'),
        tax: r.number('tax'),
        total: r.number('total'),
      );
}

class InvoicePayment {
  const InvoicePayment({
    required this.id,
    required this.amount,
    required this.paidOn,
    required this.method,
    required this.reference,
    required this.note,
  });
  final String id, paidOn, method, reference, note;
  final double amount;
  factory InvoicePayment.fromJson(JsonReader r) => InvoicePayment(
        id: r.string('id'),
        amount: r.number('amount'),
        paidOn: dayOf(r.string('paid_on')),
        method: r.string('method'),
        reference: r.optString('reference') ?? '',
        note: r.optString('note') ?? '',
      );
}

/// Money an owner took off a sent invoice (docs/business.md#credit-notes). The
/// invoice itself never changes; its balance does.
class CreditNote {
  const CreditNote({
    required this.id,
    required this.number,
    required this.status,
    required this.amount,
    required this.subtotal,
    required this.tax,
    required this.reason,
    required this.issueDate,
    required this.issuedBy,
    required this.voidReason,
  });
  final String id, number, status, reason, issueDate, issuedBy, voidReason;
  final double amount, subtotal, tax;
  bool get isVoid => status == 'void';
  factory CreditNote.fromJson(JsonReader r) => CreditNote(
        id: r.string('id'),
        number: r.string('number'),
        status: r.optString('status') ?? 'issued',
        amount: r.number('amount'),
        subtotal: r.optNumber('subtotal') ?? 0,
        tax: r.optNumber('tax') ?? 0,
        reason: r.optString('reason') ?? '',
        issueDate: dayOf(r.optString('issue_date')),
        issuedBy: r.optString('issued_by') ?? '',
        voidReason: r.optString('void_reason') ?? '',
      );
}

/// What a customer reported paying from the portal (docs/business-portal.md).
/// A report changes no balance until a person confirms it.
class PaymentNotice {
  const PaymentNotice({
    required this.id,
    required this.payerName,
    required this.amount,
    required this.paidOn,
    required this.method,
    required this.reference,
    required this.note,
    required this.status,
    required this.decidedBy,
    required this.decisionNote,
  });
  final String id, payerName, paidOn, method, reference, note, status;
  final String decidedBy, decisionNote;
  final double amount;
  bool get pending => status == 'pending';
  factory PaymentNotice.fromJson(JsonReader r) => PaymentNotice(
        id: r.string('id'),
        payerName: r.optString('payer_name') ?? '',
        amount: r.number('amount'),
        paidOn: dayOf(r.optString('paid_on')),
        method: r.optString('method') ?? 'bank_transfer',
        reference: r.optString('reference') ?? '',
        note: r.optString('note') ?? '',
        status: r.optString('status') ?? 'pending',
        decidedBy: r.optString('decided_by') ?? '',
        decisionNote: r.optString('decision_note') ?? '',
      );
}

/// What the caller may do with this invoice now (the server decides; the
/// phone only shows the buttons).
class InvoiceCan {
  const InvoiceCan({
    this.edit = false,
    this.requestSend = false,
    this.cancelRequest = false,
    this.delete = false,
    this.voidIt = false,
    this.recordPayment = false,
    this.removePayment = false,
    this.credit = false,
    this.creditEnabled = true,
    this.voidCreditNote = false,
    this.decideNotice = false,
  });
  final bool edit,
      requestSend,
      cancelRequest,
      delete,
      voidIt,
      recordPayment,
      removePayment,
      credit,
      creditEnabled,
      voidCreditNote,
      decideNotice;
  factory InvoiceCan.fromJson(JsonReader r) => InvoiceCan(
        edit: r.boolean('edit'),
        requestSend: r.boolean('request_send'),
        cancelRequest: r.boolean('cancel_request'),
        delete: r.boolean('delete'),
        voidIt: r.boolean('void'),
        recordPayment: r.boolean('record_payment'),
        removePayment: r.boolean('remove_payment'),
        credit: r.optBool('credit') ?? false,
        creditEnabled: r.optBool('credit_enabled') ?? true,
        voidCreditNote: r.optBool('void_credit_note') ?? false,
        decideNotice: r.optBool('decide_notice') ?? false,
      );
}

class Invoice {
  const Invoice({
    required this.id,
    required this.accountId,
    required this.status,
    required this.currency,
    required this.lines,
    required this.subtotal,
    required this.tax,
    required this.total,
    required this.amountPaid,
    required this.dueInDays,
    required this.customerReference,
    required this.notes,
    required this.paymentTerms,
    required this.billToName,
    required this.billToAddress,
    required this.billToEmail,
    required this.billToTaxNumber,
    required this.voidReason,
    required this.decisionNote,
    required this.accountName,
    required this.balance,
    required this.overdue,
    required this.daysOverdue,
    required this.paymentState,
    required this.payments,
    required this.approvalState,
    required this.issues,
    required this.can,
    this.amountCredited = 0,
    this.creditNotes = const [],
    this.paymentNotices = const [],
    this.projectId,
    this.projectName,
    this.number,
    this.issueDate,
    this.dueDate,
    this.seller,
    this.approvalId,
    this.approvalHash,
  });
  final String id, accountId, status, currency, accountName, paymentState;
  final String customerReference, notes, paymentTerms, voidReason;
  final String billToName, billToAddress, billToEmail, billToTaxNumber;
  final String decisionNote, approvalState;
  final String? projectId, projectName, number, issueDate, dueDate;
  final String? approvalId, approvalHash;
  final List<InvoiceLine> lines;
  final double subtotal, tax, total, amountPaid, balance, amountCredited;
  final int dueInDays, daysOverdue;
  final bool overdue;
  final List<InvoicePayment> payments;
  final List<CreditNote> creditNotes;
  final List<PaymentNotice> paymentNotices;
  final List<String> issues;
  final InvoiceCan can;
  final BillingProfile? seller;

  /// The text a person sees for it: the number, or the draft/waiting label
  /// the screen supplies.
  bool get issued => status == 'sent' || status == 'paid' || status == 'void';

  factory Invoice.fromJson(JsonReader r) => Invoice(
        id: r.string('id'),
        accountId: r.string('account_id'),
        projectId: r.optString('project_id'),
        status: r.string('status'),
        currency: r.string('currency'),
        lines: r.objects('lines', InvoiceLine.fromJson),
        subtotal: r.number('subtotal'),
        tax: r.number('tax'),
        total: r.number('total'),
        amountPaid: r.number('amount_paid'),
        amountCredited: r.optNumber('amount_credited') ?? 0,
        dueInDays: r.optInt('due_in_days') ?? 0,
        number: r.optString('number'),
        issueDate: r.optString('issue_date'),
        dueDate: r.optString('due_date'),
        customerReference: r.optString('customer_reference') ?? '',
        notes: r.optString('notes') ?? '',
        paymentTerms: r.optString('payment_terms') ?? '',
        billToName: r.optString('bill_to_name') ?? '',
        billToAddress: r.optString('bill_to_address') ?? '',
        billToEmail: r.optString('bill_to_email') ?? '',
        billToTaxNumber: r.optString('bill_to_tax_number') ?? '',
        seller: r.optObject('seller') == null
            ? null
            : BillingProfile.fromJson(r.object('seller')),
        voidReason: r.optString('void_reason') ?? '',
        decisionNote: r.optString('decision_note') ?? '',
        accountName: r.optString('account_name') ?? '',
        projectName: r.optString('project_name'),
        balance: r.number('balance'),
        overdue: r.optBool('overdue') ?? false,
        daysOverdue: r.optInt('days_overdue') ?? 0,
        paymentState: r.optString('payment_state') ?? 'unpaid',
        payments: r.optObjects('payments', InvoicePayment.fromJson),
        creditNotes: r.optObjects('credit_notes', CreditNote.fromJson),
        paymentNotices: r.optObjects('payment_notices', PaymentNotice.fromJson),
        approvalState: r.optString('approval_state') ?? 'none',
        approvalId: r.optObject('approval')?.optString('id'),
        approvalHash: r.optObject('approval')?.optString('payload_hash'),
        issues: r.optStrings('issues') ?? const [],
        can: r.optObject('can') == null
            ? const InvoiceCan()
            : InvoiceCan.fromJson(r.object('can')),
      );
}

class InvoiceListItem {
  const InvoiceListItem({
    required this.id,
    required this.status,
    required this.accountName,
    required this.currency,
    required this.total,
    required this.amountPaid,
    required this.balance,
    required this.overdue,
    required this.daysOverdue,
    required this.paymentState,
    required this.customerReference,
    required this.lines,
    this.amountCredited = 0,
    this.noticesPending = 0,
    this.number,
    this.projectName,
    this.issueDate,
    this.dueDate,
  });
  final String id, status, accountName, currency, paymentState;
  final String customerReference;
  final String? number, projectName, issueDate, dueDate;
  final double total, amountPaid, balance, amountCredited;
  final bool overdue;
  final int daysOverdue, lines, noticesPending;

  factory InvoiceListItem.fromJson(JsonReader r) => InvoiceListItem(
        id: r.string('id'),
        number: r.optString('number'),
        status: r.string('status'),
        accountName: r.optString('account_name') ?? '',
        projectName: r.optString('project_name'),
        currency: r.string('currency'),
        total: r.number('total'),
        amountPaid: r.number('amount_paid'),
        amountCredited: r.optNumber('amount_credited') ?? 0,
        balance: r.number('balance'),
        issueDate: r.optString('issue_date'),
        dueDate: r.optString('due_date'),
        overdue: r.optBool('overdue') ?? false,
        daysOverdue: r.optInt('days_overdue') ?? 0,
        paymentState: r.optString('payment_state') ?? 'unpaid',
        customerReference: r.optString('customer_reference') ?? '',
        noticesPending: r.optInt('notices_pending') ?? 0,
        lines: r.optInt('lines') ?? 0,
      );
}

/// One currency's figures; currencies are never added together.
class InvoiceCurrencyFigures {
  const InvoiceCurrencyFigures({
    required this.currency,
    required this.outstanding,
    required this.overdue,
    required this.overdueCount,
    required this.invoicedThisMonth,
    required this.collectedThisMonth,
    required this.drafts,
  });
  final String currency;
  final double outstanding,
      overdue,
      invoicedThisMonth,
      collectedThisMonth,
      drafts;
  final int overdueCount;
  factory InvoiceCurrencyFigures.fromJson(JsonReader r) =>
      InvoiceCurrencyFigures(
        currency: r.string('currency'),
        outstanding: r.number('outstanding'),
        overdue: r.number('overdue'),
        overdueCount: r.integer('overdue_count'),
        invoicedThisMonth: r.number('invoiced_this_month'),
        collectedThisMonth: r.number('collected_this_month'),
        drafts: r.number('drafts'),
      );
}

class InvoiceSummary {
  const InvoiceSummary({
    required this.currencies,
    required this.truncated,
    required this.notes,
    this.noticesPending = 0,
  });
  final List<InvoiceCurrencyFigures> currencies;
  final bool truncated;
  final List<String> notes;

  /// Customer payment reports waiting for a member or owner.
  final int noticesPending;
  factory InvoiceSummary.fromJson(JsonReader r) => InvoiceSummary(
        currencies: r.objects('currencies', InvoiceCurrencyFigures.fromJson),
        truncated: r.optBool('truncated') ?? false,
        notes: r.optStrings('notes') ?? const [],
        noticesPending: r.optInt('notices_pending') ?? 0,
      );
}

/// The list filter: status (null = any), only the overdue, a search.
class InvoiceFilter {
  const InvoiceFilter({this.status, this.overdueOnly = false, this.query});
  final String? status;
  final bool overdueOnly;
  final String? query;

  InvoiceFilter copyWith({
    String? Function()? status,
    bool? overdueOnly,
    String? Function()? query,
  }) =>
      InvoiceFilter(
        status: status == null ? this.status : status(),
        overdueOnly: overdueOnly ?? this.overdueOnly,
        query: query == null ? this.query : query(),
      );

  bool get active => status != null || overdueOnly || (query ?? '').isNotEmpty;

  Map<String, String> toQuery() => {
        if (overdueOnly) 'overdue': 'true',
        if (!overdueOnly && status != null) 'status': status!,
        if (!overdueOnly && (query ?? '').isNotEmpty) 'q': query!,
      };
}

class BillableMilestone {
  const BillableMilestone({
    required this.id,
    required this.title,
    required this.amount,
    required this.billable,
    required this.reason,
    this.dueOn,
    this.changeOrderNumber,
  });
  final String id, title, reason;
  final double amount;
  final bool billable;
  final String? dueOn;
  final int? changeOrderNumber;
  factory BillableMilestone.fromJson(JsonReader r) => BillableMilestone(
        id: r.string('id'),
        title: r.string('title'),
        amount: r.number('amount'),
        dueOn: r.optString('due_on'),
        changeOrderNumber: r.optInt('change_order_number'),
        billable: r.boolean('billable'),
        reason: r.optString('reason') ?? '',
      );
}

class BillableUsage {
  const BillableUsage({
    required this.id,
    required this.description,
    required this.occurredOn,
    required this.quantity,
    required this.unitCost,
    required this.amount,
    required this.currency,
    required this.billable,
    required this.reason,
  });
  final String id, description, occurredOn, currency, reason;
  final double quantity, unitCost, amount;
  final bool billable;
  factory BillableUsage.fromJson(JsonReader r) => BillableUsage(
        id: r.string('id'),
        description: r.string('description'),
        occurredOn: dayOf(r.string('occurred_on')),
        quantity: r.number('quantity'),
        unitCost: r.number('unit_cost'),
        amount: r.number('amount'),
        currency: r.string('currency'),
        billable: r.boolean('billable'),
        reason: r.optString('reason') ?? '',
      );
}

class BillableOffer {
  const BillableOffer({
    required this.id,
    required this.label,
    required this.currency,
    required this.priceType,
    this.price,
  });
  final String id, label, currency, priceType;
  final double? price;
  factory BillableOffer.fromJson(JsonReader r) => BillableOffer(
        id: r.string('id'),
        label: r.string('label'),
        currency: r.string('currency'),
        priceType: r.optString('price_type') ?? '',
        price: r.optNumber('price'),
      );
}

class BillableContract {
  const BillableContract(this.contractTotal, this.invoiced, this.remaining);
  final double contractTotal, invoiced, remaining;
}

class Billable {
  const Billable({
    required this.accountId,
    required this.currency,
    required this.taxRatePct,
    required this.milestones,
    required this.usage,
    required this.offers,
    this.projectId,
    this.contract,
  });
  final String accountId, currency;
  final String? projectId;
  final double taxRatePct;
  final List<BillableMilestone> milestones;
  final BillableContract? contract;
  final List<BillableUsage> usage;
  final List<BillableOffer> offers;

  factory Billable.fromJson(JsonReader r) {
    final contract = r.optObject('contract');
    return Billable(
      accountId: r.string('account_id'),
      projectId: r.optString('project_id'),
      currency: r.string('currency'),
      taxRatePct: r.optNumber('tax_rate_pct') ?? 0,
      milestones: r.objects('milestones', BillableMilestone.fromJson),
      contract: contract == null
          ? null
          : BillableContract(
              contract.number('contract_total'),
              contract.number('invoiced'),
              contract.number('remaining'),
            ),
      usage: r.objects('usage', BillableUsage.fromJson),
      offers: r.objects('offers', BillableOffer.fromJson),
    );
  }
}

/// A client account in the pickers and the Clients list.
class ClientAccount {
  const ClientAccount({
    required this.id,
    required this.name,
    required this.contacts,
    required this.openLeads,
    required this.doNotContact,
    this.city = '',
    this.country = '',
  });
  final String id, name, city, country;
  final int contacts, openLeads;
  final bool doNotContact;
  factory ClientAccount.fromJson(JsonReader r) => ClientAccount(
        id: r.string('id'),
        name: r.string('name'),
        // A list row carries the count, the detail carries the contacts.
        contacts: switch (r['contacts']) {
          final num count => count.toInt(),
          final List<Object?> items => items.length,
          _ => 0,
        },
        openLeads: r.optInt('open_leads') ?? 0,
        doNotContact: r.optBool('do_not_contact') ?? false,
        city: r.optString('city') ?? '',
        country: r.optString('country') ?? '',
      );
}

class ClientContact {
  const ClientContact({
    required this.id,
    required this.name,
    required this.doNotContact,
    this.email,
    this.roleTitle = '',
  });
  final String id, name, roleTitle;
  final String? email;
  final bool doNotContact;
  factory ClientContact.fromJson(JsonReader r) => ClientContact(
        id: r.string('id'),
        name: r.string('name'),
        email: r.optString('email'),
        roleTitle: r.optString('role_title') ?? '',
        doNotContact: r.optBool('do_not_contact') ?? false,
      );
}

class ClientDetail {
  const ClientDetail({required this.account, required this.contacts});
  final ClientAccount account;
  final List<ClientContact> contacts;
  factory ClientDetail.fromJson(JsonReader r) => ClientDetail(
        account: ClientAccount.fromJson(JsonReader(r.json, r.path)),
        contacts: r.optObjects('contacts', ClientContact.fromJson),
      );
}

/// A project in the new invoice picker.
class ProjectChoice {
  const ProjectChoice({
    required this.id,
    required this.name,
    required this.accountId,
    required this.accountName,
    required this.currency,
    required this.status,
  });
  final String id, name, accountId, accountName, currency, status;
  factory ProjectChoice.fromJson(JsonReader r) => ProjectChoice(
        id: r.string('id'),
        name: r.string('name'),
        accountId: r.string('account_id'),
        accountName: r.optString('account_name') ?? '',
        currency: r.string('currency'),
        status: r.optString('status') ?? '',
      );
}

/// What a customer decided in their portal about a proposal or change order.
class PortalDecisionSummary {
  const PortalDecisionSummary({
    required this.decision,
    required this.signerName,
    required this.note,
    required this.decidedAt,
    required this.current,
  });
  final String decision, signerName, note;
  final DateTime decidedAt;
  final bool current;
  bool get approved => decision == 'approved';

  static PortalDecisionSummary? maybe(JsonReader r) {
    final d = r.optObject('portal_decision');
    if (d == null) return null;
    return PortalDecisionSummary(
      decision: d.string('decision'),
      signerName: d.optString('signer_name') ?? '',
      note: d.optString('note') ?? '',
      decidedAt: d.date('decided_at'),
      current: d.optBool('current') ?? false,
    );
  }
}

/// One portal link of a contact. The token is never part of this.
class PortalAccess {
  const PortalAccess({
    required this.id,
    required this.contactId,
    required this.contactName,
    required this.active,
    required this.expired,
    required this.canDecide,
    required this.expiresAt,
    this.contactEmail,
    this.lastUsedAt,
    this.revokedAt,
  });
  final String id, contactId, contactName;
  final String? contactEmail;
  final bool active, expired, canDecide;
  final DateTime expiresAt;
  final DateTime? lastUsedAt, revokedAt;

  /// Working now: not turned off and not past its date.
  bool get working => active && !expired;

  factory PortalAccess.fromJson(JsonReader r) => PortalAccess(
        id: r.string('id'),
        contactId: r.optString('contact_id') ?? '',
        contactName: r.optString('contact_name') ?? '',
        contactEmail: r.optString('contact_email'),
        active: r.string('status') == 'active',
        expired: r.optBool('expired') ?? false,
        canDecide: r.optBool('can_decide') ?? false,
        expiresAt: r.date('expires_at'),
        lastUsedAt: r.optDate('last_used_at'),
        revokedAt: r.optDate('revoked_at'),
      );
}

/// A link just created: the token is shown once and kept nowhere.
class PortalAccessCreated {
  const PortalAccessCreated(this.access, this.token, this.portalPath);
  final PortalAccess access;
  final String token, portalPath;
  factory PortalAccessCreated.fromJson(JsonReader r) => PortalAccessCreated(
        PortalAccess.fromJson(r),
        r.string('token'),
        r.string('portal_path'),
      );
}
