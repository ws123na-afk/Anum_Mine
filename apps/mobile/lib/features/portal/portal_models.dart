import '../../data/json_read.dart';

/// The customer portal's own view (`/api/v1/portal`, docs/business-portal.md):
/// what one client account sees of its supplier. No internal figure, note,
/// assignee or SLA ever appears here. Same wire shapes as packages/contracts
/// (business.ts, `Portal*`). Raw ids are carried only to call the API and are
/// never shown.

String dayPart(String? value) =>
    value == null || value.length < 10 ? '-' : value.substring(0, 10);

class PortalCounts {
  const PortalCounts({
    required this.offersWaiting,
    required this.changeOrdersWaiting,
    required this.invoicesOpen,
    required this.invoicesOverdue,
    required this.ticketsOpen,
  });
  final int offersWaiting,
      changeOrdersWaiting,
      invoicesOpen,
      invoicesOverdue,
      ticketsOpen;
  factory PortalCounts.fromJson(JsonReader r) => PortalCounts(
        offersWaiting: r.integer('offers_waiting'),
        changeOrdersWaiting: r.integer('change_orders_waiting'),
        invoicesOpen: r.integer('invoices_open'),
        invoicesOverdue: r.integer('invoices_overdue'),
        ticketsOpen: r.integer('tickets_open'),
      );
}

class PortalProject {
  const PortalProject(this.id, this.name);
  final String id, name;
  factory PortalProject.fromJson(JsonReader r) =>
      PortalProject(r.string('id'), r.string('name'));
}

class PortalMe {
  const PortalMe({
    required this.businessName,
    required this.accountName,
    required this.contactName,
    required this.expiresAt,
    required this.canDecide,
    required this.counts,
    required this.projects,
  });
  final String businessName, accountName, contactName;
  final DateTime expiresAt;
  final bool canDecide;
  final PortalCounts counts;
  final List<PortalProject> projects;
  factory PortalMe.fromJson(JsonReader r) => PortalMe(
        businessName: r.optString('business_name') ?? '',
        accountName: r.optString('account_name') ?? '',
        contactName: r.optString('contact_name') ?? '',
        expiresAt: r.date('expires_at'),
        canDecide: r.boolean('can_decide'),
        counts: PortalCounts.fromJson(r.object('counts')),
        projects: r.optObjects('projects', PortalProject.fromJson),
      );
}

/// A decision already made on the exact version shown.
class PortalDecisionView {
  const PortalDecisionView({
    required this.decision,
    required this.signerName,
    required this.note,
    required this.decidedAt,
  });
  final String decision, signerName, note;
  final DateTime decidedAt;
  bool get approved => decision == 'approved';
  static PortalDecisionView? maybe(JsonReader r) {
    final d = r.optObject('decision');
    if (d == null) return null;
    return PortalDecisionView(
      decision: d.string('decision'),
      signerName: d.optString('signer_name') ?? '',
      note: d.optString('note') ?? '',
      decidedAt: d.date('decided_at'),
    );
  }
}

class PortalOfferSummary {
  const PortalOfferSummary({
    required this.id,
    required this.title,
    required this.status,
    required this.currency,
    required this.total,
    required this.version,
    required this.expired,
    required this.canDecide,
    this.validUntil,
    this.decision,
  });
  final String id, title, status, currency;
  final double total;
  final int version;
  final String? validUntil;
  final bool expired, canDecide;
  final PortalDecisionView? decision;

  bool get waiting => status == 'waiting';

  factory PortalOfferSummary.fromJson(JsonReader r) => PortalOfferSummary(
        id: r.string('id'),
        title: r.string('title'),
        status: r.string('status'),
        currency: r.string('currency'),
        total: r.number('total'),
        version: r.integer('version'),
        validUntil: r.optString('valid_until'),
        expired: r.boolean('expired'),
        decision: PortalDecisionView.maybe(r),
        canDecide: r.boolean('can_decide'),
      );
}

class PortalLine {
  const PortalLine(this.description, this.quantity, this.unitPrice, this.amount,
      {this.action});
  final String description;
  final double quantity, unitPrice, amount;
  final String? action;
}

class PortalMilestone {
  const PortalMilestone(
      this.title, this.amount, this.dueInDays, this.acceptance);
  final String title, acceptance;
  final double amount;
  final int? dueInDays;
}

class PortalOffer extends PortalOfferSummary {
  const PortalOffer({
    required super.id,
    required super.title,
    required super.status,
    required super.currency,
    required super.total,
    required super.version,
    required super.expired,
    required super.canDecide,
    required this.summary,
    required this.scope,
    required this.deliverables,
    required this.exclusions,
    required this.assumptions,
    required this.acceptanceCriteria,
    required this.lines,
    required this.milestones,
    required this.subtotal,
    required this.tax,
    required this.taxRatePct,
    required this.taxNote,
    required this.paymentTerms,
    required this.contentHash,
    super.validUntil,
    super.decision,
  });
  final String summary, scope, taxNote, paymentTerms, contentHash;
  final List<String> deliverables, exclusions, assumptions, acceptanceCriteria;
  final List<PortalLine> lines;
  final List<PortalMilestone> milestones;
  final double subtotal, tax, taxRatePct;

  factory PortalOffer.fromJson(JsonReader r) {
    final s = PortalOfferSummary.fromJson(r);
    return PortalOffer(
      id: s.id,
      title: s.title,
      status: s.status,
      currency: s.currency,
      total: s.total,
      version: s.version,
      expired: s.expired,
      canDecide: s.canDecide,
      validUntil: s.validUntil,
      decision: s.decision,
      summary: r.optString('summary') ?? '',
      scope: r.optString('scope') ?? '',
      deliverables: r.optStrings('deliverables') ?? const [],
      exclusions: r.optStrings('exclusions') ?? const [],
      assumptions: r.optStrings('assumptions') ?? const [],
      acceptanceCriteria: r.optStrings('acceptance_criteria') ?? const [],
      lines: r.optObjects(
          'line_items',
          (x) => PortalLine(x.string('description'), x.number('quantity'),
              x.number('unit_price'), x.number('amount'))),
      milestones: r.optObjects(
          'milestones',
          (x) => PortalMilestone(x.string('title'), x.number('amount'),
              x.optInt('due_in_days'), x.optString('acceptance') ?? '')),
      subtotal: r.number('subtotal'),
      tax: r.number('tax'),
      taxRatePct: r.optNumber('tax_rate_pct') ?? 0,
      taxNote: r.optString('tax_note') ?? '',
      paymentTerms: r.optString('payment_terms') ?? '',
      contentHash: r.string('content_hash'),
    );
  }
}

class PortalChangeSummary {
  const PortalChangeSummary({
    required this.id,
    required this.number,
    required this.title,
    required this.projectName,
    required this.status,
    required this.currency,
    required this.timelineDays,
    required this.canDecide,
    required this.stale,
    this.totalDelta,
    this.decision,
  });
  final String id, title, projectName, status, currency;
  final int number, timelineDays;
  final double? totalDelta;
  final bool canDecide, stale;
  final PortalDecisionView? decision;
  bool get waiting => status == 'waiting';

  factory PortalChangeSummary.fromJson(JsonReader r) => PortalChangeSummary(
        id: r.string('id'),
        number: r.integer('number'),
        title: r.string('title'),
        projectName: r.optString('project_name') ?? '',
        status: r.string('status'),
        currency: r.string('currency'),
        totalDelta: r.optNumber('total_delta'),
        timelineDays: r.integer('timeline_days'),
        decision: PortalDecisionView.maybe(r),
        canDecide: r.boolean('can_decide'),
        stale: r.boolean('stale'),
      );
}

class PortalChange extends PortalChangeSummary {
  const PortalChange({
    required super.id,
    required super.number,
    required super.title,
    required super.projectName,
    required super.status,
    required super.currency,
    required super.timelineDays,
    required super.canDecide,
    required super.stale,
    required this.request,
    required this.lines,
    required this.contentHash,
    super.totalDelta,
    super.decision,
    this.subtotalDelta,
    this.taxDelta,
  });
  final String request, contentHash;
  final List<PortalLine> lines;
  final double? subtotalDelta, taxDelta;

  factory PortalChange.fromJson(JsonReader r) {
    final s = PortalChangeSummary.fromJson(r);
    return PortalChange(
      id: s.id,
      number: s.number,
      title: s.title,
      projectName: s.projectName,
      status: s.status,
      currency: s.currency,
      timelineDays: s.timelineDays,
      canDecide: s.canDecide,
      stale: s.stale,
      totalDelta: s.totalDelta,
      decision: s.decision,
      request: r.optString('request') ?? '',
      subtotalDelta: r.optNumber('subtotal_delta'),
      taxDelta: r.optNumber('tax_delta'),
      contentHash: r.optString('content_hash') ?? '',
      lines: r.optObjects(
          'lines',
          (x) => PortalLine(x.string('description'), x.number('quantity'),
              x.number('unit_price'), x.number('amount'),
              action: x.optString('action'))),
    );
  }
}

class PortalInvoiceSummary {
  const PortalInvoiceSummary({
    required this.id,
    required this.number,
    required this.status,
    required this.currency,
    required this.total,
    required this.amountPaid,
    required this.balance,
    required this.overdue,
    required this.daysOverdue,
    required this.paymentState,
    required this.customerReference,
    this.amountCredited = 0,
    this.issueDate,
    this.dueDate,
  });
  final String id, number, status, currency, paymentState, customerReference;
  final double total, amountPaid, balance, amountCredited;
  final String? issueDate, dueDate;
  final bool overdue;
  final int daysOverdue;

  factory PortalInvoiceSummary.fromJson(JsonReader r) => PortalInvoiceSummary(
        id: r.string('id'),
        number: r.optString('number') ?? '',
        status: r.string('status'),
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
      );
}

/// A credit note the business issued on this invoice (the reason is theirs
/// and is meant for the customer to read).
class PortalCreditNote {
  const PortalCreditNote(
      this.number, this.issueDate, this.amount, this.reason, this.status);
  final String number, issueDate, reason, status;
  final double amount;
  bool get isVoid => status == 'void';
}

/// A payment this customer reported. It changes nothing until the business
/// confirms it.
class PortalPaymentNotice {
  const PortalPaymentNotice({
    required this.id,
    required this.amount,
    required this.paidOn,
    required this.method,
    required this.reference,
    required this.status,
    required this.decisionNote,
  });
  final String id, paidOn, method, reference, status, decisionNote;
  final double amount;
}

class PortalInvoiceLine {
  const PortalInvoiceLine(this.description, this.quantity, this.unitPrice,
      this.taxRatePct, this.total);
  final String description;
  final double quantity, unitPrice, taxRatePct, total;
}

class PortalPayment {
  const PortalPayment(this.paidOn, this.amount, this.method, this.reference);
  final String paidOn, method, reference;
  final double amount;
}

class PortalSeller {
  const PortalSeller({
    this.legalName = '',
    this.address = '',
    this.taxLabel = '',
    this.taxNumber = '',
    this.email = '',
    this.phone = '',
    this.bankDetails = '',
  });
  final String legalName,
      address,
      taxLabel,
      taxNumber,
      email,
      phone,
      bankDetails;
  factory PortalSeller.fromJson(JsonReader r) => PortalSeller(
        legalName: r.optString('legal_name') ?? '',
        address: r.optString('address') ?? '',
        taxLabel: r.optString('tax_label') ?? '',
        taxNumber: r.optString('tax_number') ?? '',
        email: r.optString('email') ?? '',
        phone: r.optString('phone') ?? '',
        bankDetails: r.optString('bank_details') ?? '',
      );
}

class PortalInvoice extends PortalInvoiceSummary {
  const PortalInvoice({
    required super.id,
    required super.number,
    required super.status,
    required super.currency,
    required super.total,
    required super.amountPaid,
    required super.balance,
    required super.overdue,
    required super.daysOverdue,
    required super.paymentState,
    required super.customerReference,
    required this.lines,
    required this.subtotal,
    required this.tax,
    required this.notes,
    required this.paymentTerms,
    required this.billToName,
    required this.billToAddress,
    required this.billToTaxNumber,
    required this.seller,
    required this.payments,
    required this.voidReason,
    super.amountCredited,
    super.issueDate,
    super.dueDate,
    this.creditNotes = const [],
    this.paymentNotices = const [],
    this.canReportPayment = false,
  });
  final List<PortalInvoiceLine> lines;
  final double subtotal, tax;
  final String notes, paymentTerms, billToName, billToAddress, billToTaxNumber;
  final PortalSeller seller;
  final List<PortalPayment> payments;
  final List<PortalCreditNote> creditNotes;
  final List<PortalPaymentNotice> paymentNotices;

  /// A sent invoice with a balance, on a link that may act, in a business that
  /// is not paused.
  final bool canReportPayment;
  final String voidReason;

  factory PortalInvoice.fromJson(JsonReader r) {
    final s = PortalInvoiceSummary.fromJson(r);
    return PortalInvoice(
      id: s.id,
      number: s.number,
      status: s.status,
      currency: s.currency,
      total: s.total,
      amountPaid: s.amountPaid,
      balance: s.balance,
      overdue: s.overdue,
      daysOverdue: s.daysOverdue,
      paymentState: s.paymentState,
      customerReference: s.customerReference,
      amountCredited: s.amountCredited,
      issueDate: s.issueDate,
      dueDate: s.dueDate,
      creditNotes: r.optObjects(
          'credit_notes',
          (x) => PortalCreditNote(
              x.string('number'),
              dayPart(x.optString('issue_date')),
              x.number('amount'),
              x.optString('reason') ?? '',
              x.optString('status') ?? 'issued')),
      paymentNotices: r.optObjects(
          'payment_notices',
          (x) => PortalPaymentNotice(
              id: x.string('id'),
              amount: x.number('amount'),
              paidOn: dayPart(x.optString('paid_on')),
              method: x.optString('method') ?? 'bank_transfer',
              reference: x.optString('reference') ?? '',
              status: x.optString('status') ?? 'pending',
              decisionNote: x.optString('decision_note') ?? '')),
      canReportPayment: r.optBool('can_report_payment') ?? false,
      lines: r.optObjects(
          'lines',
          (x) => PortalInvoiceLine(
              x.string('description'),
              x.number('quantity'),
              x.number('unit_price'),
              x.optNumber('tax_rate_pct') ?? 0,
              x.number('total'))),
      subtotal: r.number('subtotal'),
      tax: r.number('tax'),
      notes: r.optString('notes') ?? '',
      paymentTerms: r.optString('payment_terms') ?? '',
      billToName: r.optString('bill_to_name') ?? '',
      billToAddress: r.optString('bill_to_address') ?? '',
      billToTaxNumber: r.optString('bill_to_tax_number') ?? '',
      seller: r.optObject('seller') == null
          ? const PortalSeller()
          : PortalSeller.fromJson(r.object('seller')),
      payments: r.optObjects(
          'payments',
          (x) => PortalPayment(
              dayPart(x.optString('paid_on')),
              x.number('amount'),
              x.string('method'),
              x.optString('reference') ?? '')),
      voidReason: r.optString('void_reason') ?? '',
    );
  }
}

class PortalTicketSummary {
  const PortalTicketSummary({
    required this.id,
    required this.reference,
    required this.title,
    required this.severity,
    required this.status,
    required this.reportedAt,
    required this.updatedAt,
    required this.openedHere,
    this.projectName,
  });
  final String id, reference, title, severity, status;
  final String? projectName;
  final DateTime reportedAt, updatedAt;
  final bool openedHere;

  bool get open => const {
        'new',
        'triaged',
        'in_progress',
        'waiting_on_customer'
      }.contains(status);

  factory PortalTicketSummary.fromJson(JsonReader r) => PortalTicketSummary(
        id: r.string('id'),
        reference: r.string('reference'),
        title: r.string('title'),
        severity: r.string('severity'),
        status: r.string('status'),
        projectName: r.optString('project_name'),
        reportedAt: r.date('reported_at'),
        updatedAt: r.date('updated_at'),
        openedHere: r.optBool('opened_here') ?? false,
      );
}

class PortalReply {
  const PortalReply(this.body, this.sentAt);
  final String body;
  final DateTime? sentAt;
}

class PortalTicket extends PortalTicketSummary {
  const PortalTicket({
    required super.id,
    required super.reference,
    required super.title,
    required super.severity,
    required super.status,
    required super.reportedAt,
    required super.updatedAt,
    required super.openedHere,
    required this.description,
    required this.resolutionSummary,
    required this.replies,
    super.projectName,
    this.resolvedAt,
  });
  final String description, resolutionSummary;
  final DateTime? resolvedAt;
  final List<PortalReply> replies;

  factory PortalTicket.fromJson(JsonReader r) {
    final s = PortalTicketSummary.fromJson(r);
    return PortalTicket(
      id: s.id,
      reference: s.reference,
      title: s.title,
      severity: s.severity,
      status: s.status,
      projectName: s.projectName,
      reportedAt: s.reportedAt,
      updatedAt: s.updatedAt,
      openedHere: s.openedHere,
      description: r.optString('description') ?? '',
      resolutionSummary: r.optString('resolution_summary') ?? '',
      resolvedAt: r.optDate('resolved_at'),
      replies: r.optObjects('replies',
          (x) => PortalReply(x.optString('body') ?? '', x.optDate('sent_at'))),
    );
  }
}
