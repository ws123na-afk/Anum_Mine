import 'dart:async';

import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'invoice_models.dart' show ClientAccount, dayOfDate;
import 'invoices.dart' show friendly, invoiceTone, money, today;

/// Recurring invoices on the phone (docs/business.md#recurring-invoices): the
/// list, a schedule with its next dates and the invoices it drafted, pause,
/// resume, end, delete and "draft what is due now", a form to make or edit
/// one, and the owner's switches for the planned emails. The phone never sends
/// an invoice: every draft and every planned email waits for an owner in
/// Approvals.

class ScheduleLine {
  const ScheduleLine(
      this.description, this.quantity, this.unitPrice, this.taxRatePct);
  final String description;
  final double quantity, unitPrice, taxRatePct;
  factory ScheduleLine.fromJson(JsonReader r) => ScheduleLine(
        r.optString('description') ?? '',
        r.optNumber('quantity') ?? 1,
        r.optNumber('unit_price') ?? 0,
        r.optNumber('tax_rate_pct') ?? 0,
      );
  Map<String, Object?> toJson() => {
        'description': description,
        'quantity': quantity,
        'unit_price': unitPrice,
        'tax_rate_pct': taxRatePct,
      };
}

class ScheduleInvoice {
  const ScheduleInvoice(this.id, this.number, this.status, this.periodDate,
      this.total, this.currency);
  final String id, status, currency;
  final String? number, periodDate;
  final double total;
  factory ScheduleInvoice.fromJson(JsonReader r) => ScheduleInvoice(
        r.string('id'),
        r.optString('number'),
        r.string('status'),
        r.optString('period_date'),
        r.optNumber('total') ?? 0,
        r.string('currency'),
      );
}

class InvoiceSchedule {
  const InvoiceSchedule({
    required this.id,
    required this.title,
    required this.accountId,
    required this.currency,
    required this.frequency,
    required this.interval,
    required this.startDate,
    required this.status,
    required this.issueMode,
    required this.total,
    required this.version,
    required this.createdCount,
    required this.lines,
    required this.dueInDays,
    this.accountName,
    this.nextDate,
    this.endDate,
    this.maxCount,
    this.lastError = '',
    this.endReason = '',
    this.lastPeriod,
    this.customerReference = '',
    this.notes = '',
    this.paymentTerms = '',
    this.upcoming = const [],
    this.invoices = const [],
    this.invoicesTotal = 0,
  });
  final String id, title, accountId, currency, frequency, startDate, status;
  final String issueMode, lastError, endReason;
  final String customerReference, notes, paymentTerms;
  final String? accountName, nextDate, endDate, lastPeriod;
  final int interval, version, createdCount, dueInDays, invoicesTotal;
  final int? maxCount;
  final double total;
  final List<ScheduleLine> lines;
  final List<String> upcoming;
  final List<ScheduleInvoice> invoices;

  factory InvoiceSchedule.fromJson(JsonReader r) => InvoiceSchedule(
        id: r.string('id'),
        title: r.optString('title') ?? '',
        accountId: r.string('account_id'),
        accountName: r.optString('account_name'),
        currency: r.string('currency'),
        frequency: r.string('frequency'),
        interval: r.optInt('interval') ?? 1,
        startDate: r.string('start_date'),
        endDate: r.optString('end_date'),
        maxCount: r.optInt('max_count'),
        status: r.string('status'),
        issueMode: r.optString('issue_mode') ?? 'draft',
        total: r.optNumber('total') ?? 0,
        version: r.optInt('version') ?? 1,
        createdCount: r.optInt('created_count') ?? 0,
        dueInDays: r.optInt('due_in_days') ?? 14,
        nextDate: r.optString('next_date'),
        lastError: r.optString('last_error') ?? '',
        endReason: r.optString('end_reason') ?? '',
        lastPeriod: r.optString('last_period'),
        customerReference: r.optString('customer_reference') ?? '',
        notes: r.optString('notes') ?? '',
        paymentTerms: r.optString('payment_terms') ?? '',
        lines: r.optObjects('lines', ScheduleLine.fromJson),
        upcoming: [
          for (final item in r.optList('upcoming') ?? const <Object?>[])
            if (item is String) item
        ],
        invoices: r.optObjects('invoices', ScheduleInvoice.fromJson),
        invoicesTotal: r.optInt('invoices_total') ?? 0,
      );
}

class InvoiceAutomation {
  const InvoiceAutomation({
    required this.autoReminders,
    required this.emailScheduled,
    required this.language,
    required this.canChange,
    this.lastRunAt,
    this.planned = 0,
    this.emails = 0,
    this.skipped = 0,
    this.failed = 0,
    this.note = '',
  });
  final bool autoReminders, emailScheduled, canChange;
  final String language, note;
  final DateTime? lastRunAt;
  final int planned, emails, skipped, failed;
  factory InvoiceAutomation.fromJson(JsonReader r) {
    final s = r.optObject('last_run_summary');
    return InvoiceAutomation(
      autoReminders: r.optBool('auto_reminders') ?? false,
      emailScheduled: r.optBool('email_scheduled') ?? false,
      language: r.optString('language') ?? 'en',
      canChange: r.optBool('can_change') ?? false,
      lastRunAt: r.optDate('last_run_at'),
      planned: s?.optInt('reminders_planned') ?? 0,
      emails: s?.optInt('emails_planned') ?? 0,
      skipped: s?.optInt('skipped_do_not_contact') ?? 0,
      failed: s?.optInt('failed') ?? 0,
      note: s?.optString('note') ?? '',
    );
  }
}

class SchedulePage {
  const SchedulePage(this.items, this.total);
  final List<InvoiceSchedule> items;
  final int total;
}

abstract class RecurringRepository {
  Future<SchedulePage> list({String? status, required int offset, int limit});
  Future<InvoiceSchedule> get(String id);
  Future<InvoiceSchedule> create(Map<String, Object?> body);
  Future<InvoiceSchedule> update(
      String id, int version, Map<String, Object?> body);
  Future<InvoiceSchedule> change(String id, String action, int version);
  Future<void> delete(String id);
  Future<(int, String, InvoiceSchedule)> run(String id);
  Future<InvoiceAutomation> automation();
  Future<InvoiceAutomation> saveAutomation(
      {required bool reminders,
      required bool emails,
      required String language});
  Future<InvoiceAutomation> runAutomation();
  Future<List<ClientAccount>> accounts(String query);
}

class ApiRecurringRepository implements RecurringRepository {
  ApiRecurringRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/business';
  String _id(String value) => Uri.encodeComponent(value);

  Future<JsonReader> _call(String method, String path,
          [Map<String, Object?>? body]) async =>
      JsonReader.of(await api.request(method, '$_base$path', body: body));

  @override
  Future<SchedulePage> list(
      {String? status, required int offset, int limit = 20}) async {
    final query = Uri(queryParameters: {
      if (status != null && status.isNotEmpty) 'status': status,
      'offset': '$offset',
      'limit': '$limit',
    }).query;
    final r = await _call('GET', '/invoice-schedules?$query');
    return SchedulePage(
        r.objects('items', InvoiceSchedule.fromJson), r.optInt('total') ?? 0);
  }

  @override
  Future<InvoiceSchedule> get(String id) async => InvoiceSchedule.fromJson(
      await _call('GET', '/invoice-schedules/${_id(id)}'));

  @override
  Future<InvoiceSchedule> create(Map<String, Object?> body) async =>
      InvoiceSchedule.fromJson(await _call('POST', '/invoice-schedules', body));

  @override
  Future<InvoiceSchedule> update(
          String id, int version, Map<String, Object?> body) async =>
      InvoiceSchedule.fromJson(await _call('PATCH',
          '/invoice-schedules/${_id(id)}', {...body, 'version': version}));

  @override
  Future<InvoiceSchedule> change(String id, String action, int version) async =>
      InvoiceSchedule.fromJson(await _call('POST',
          '/invoice-schedules/${_id(id)}/$action', {'version': version}));

  @override
  Future<void> delete(String id) async {
    await api.request('DELETE', '$_base/invoice-schedules/${_id(id)}');
  }

  @override
  Future<(int, String, InvoiceSchedule)> run(String id) async {
    final r = await _call('POST', '/invoice-schedules/${_id(id)}/run');
    return (
      r.optInt('drafted') ?? 0,
      r.optString('note') ?? '',
      InvoiceSchedule.fromJson(r.object('schedule')),
    );
  }

  @override
  Future<InvoiceAutomation> automation() async =>
      InvoiceAutomation.fromJson(await _call('GET', '/invoice-automation'));

  @override
  Future<InvoiceAutomation> saveAutomation(
          {required bool reminders,
          required bool emails,
          required String language}) async =>
      InvoiceAutomation.fromJson(await _call('PUT', '/invoice-automation', {
        'auto_reminders': reminders,
        'email_scheduled': emails,
        'language': language,
      }));

  @override
  Future<InvoiceAutomation> runAutomation() async => InvoiceAutomation.fromJson(
      await _call('POST', '/invoice-automation/run'));

  @override
  Future<List<ClientAccount>> accounts(String query) async {
    final q = Uri(queryParameters: {
      if (query.isNotEmpty) 'q': query,
      'limit': '100',
      'offset': '0',
    }).query;
    final r = await _call('GET', '/accounts?$q');
    return r.objects('items', ClientAccount.fromJson);
  }
}

String frequencyText(AnumLocalizations l, InvoiceSchedule s) {
  final every = s.frequency == 'days' ||
      s.frequency == 'weeks' ||
      s.frequency == 'months';
  if (!every || s.interval == 1) return l.t('biz.rinv.every.${s.frequency}');
  return l.f('biz.rinv.everyN.${s.frequency}', {'count': s.interval});
}

String nextText(AnumLocalizations l, InvoiceSchedule s) => switch (s.status) {
      'paused' => l.t('biz.rinv.next.paused'),
      'ended' => l.t(
          'biz.rinv.next.ended.${s.endReason.isEmpty ? 'owner' : s.endReason}'),
      _ => s.nextDate == null
          ? l.t('biz.rinv.next.none')
          : l.f('biz.rinv.next.on', {'date': s.nextDate!}),
    };

AnumTone scheduleTone(String status) => switch (status) {
      'active' => AnumTone.ok,
      'paused' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

/// The list and the planned emails' switches.
class RecurringInvoicesScreen extends StatefulWidget {
  const RecurringInvoicesScreen({
    required this.repository,
    required this.canWrite,
    this.onOpenInvoice,
    super.key,
  });
  final RecurringRepository repository;
  final bool canWrite;
  final void Function(String invoiceId)? onOpenInvoice;

  @override
  State<RecurringInvoicesScreen> createState() => _RecurringState();
}

class _RecurringState extends State<RecurringInvoicesScreen> {
  final items = <InvoiceSchedule>[];
  int total = 0;
  String? error, moreError;
  bool loading = true, loadingMore = false;
  String status = '';
  InvoiceAutomation? auto;
  String? autoError, autoNotice;
  bool autoBusy = false;

  RecurringRepository get repo => widget.repository;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    setState(() {
      loading = true;
      error = null;
    });
    unawaited(_loadAutomation());
    try {
      final page = await repo.list(status: status, offset: 0);
      if (!mounted) return;
      setState(() {
        items
          ..clear()
          ..addAll(page.items);
        total = page.total;
      });
    } catch (e) {
      if (mounted) setState(() => error = friendly(e));
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> _loadAutomation() async {
    try {
      final next = await repo.automation();
      if (mounted) setState(() => auto = next);
    } catch (e) {
      if (mounted) setState(() => autoError = friendly(e));
    }
  }

  Future<void> more() async {
    setState(() {
      loadingMore = true;
      moreError = null;
    });
    try {
      final page = await repo.list(status: status, offset: items.length);
      if (mounted) {
        setState(() {
          items.addAll(page.items);
          total = page.total;
        });
      }
    } catch (e) {
      if (mounted) setState(() => moreError = friendly(e));
    } finally {
      if (mounted) setState(() => loadingMore = false);
    }
  }

  Future<void> _saveAuto(
      {bool? reminders, bool? emails, String? language}) async {
    final a = auto;
    if (a == null) return;
    setState(() {
      autoBusy = true;
      autoError = null;
      autoNotice = null;
    });
    try {
      final next = await repo.saveAutomation(
          reminders: reminders ?? a.autoReminders,
          emails: emails ?? a.emailScheduled,
          language: language ?? a.language);
      if (mounted) {
        setState(() {
          auto = next;
          autoNotice = context.anum.t('biz.rinv.auto.saved');
        });
      }
    } catch (e) {
      if (mounted) setState(() => autoError = friendly(e));
    } finally {
      if (mounted) setState(() => autoBusy = false);
    }
  }

  Future<void> _runAuto() async {
    setState(() {
      autoBusy = true;
      autoError = null;
      autoNotice = null;
    });
    try {
      final next = await repo.runAutomation();
      if (mounted) {
        setState(() {
          auto = next;
          autoNotice = context.anum.t('biz.rinv.auto.ran');
        });
      }
    } catch (e) {
      if (mounted) setState(() => autoError = friendly(e));
    } finally {
      if (mounted) setState(() => autoBusy = false);
    }
  }

  Future<void> _open(InvoiceSchedule s) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => ScheduleScreen(
          repository: repo,
          scheduleId: s.id,
          canWrite: widget.canWrite,
          onOpenInvoice: widget.onOpenInvoice),
    ));
    if (mounted) await load();
  }

  Future<void> _new() async {
    final made = await Navigator.of(context).push<InvoiceSchedule>(
        MaterialPageRoute<InvoiceSchedule>(
            builder: (_) => ScheduleFormScreen(repository: repo)));
    if (made == null || !mounted) return;
    await load();
    if (mounted) await _open(made);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('biz.rinv.title'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          _automationCard(context),
          Wrap(spacing: 8, runSpacing: 8, children: [
            if (widget.canWrite)
              FilledButton.icon(
                key: const Key('rinv-new'),
                onPressed: _new,
                icon: const Icon(Icons.add),
                label: Text(l.t('biz.rinv.new')),
              ),
            DropdownButton<String>(
              key: const Key('rinv-status'),
              value: status,
              items: [
                DropdownMenuItem(
                    value: '', child: Text(l.t('biz.rinv.filter.all'))),
                for (final s in const ['active', 'paused', 'ended'])
                  DropdownMenuItem(
                      value: s, child: Text(l.t('biz.rinv.status.$s'))),
              ],
              onChanged: (value) {
                setState(() => status = value ?? '');
                load();
              },
            ),
          ]),
          if (loading && items.isEmpty)
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
          if (error != null)
            AnumFeedback(
                kind: AnumFeedbackKind.error, message: error!, onRetry: load),
          if (!loading && error == null && items.isEmpty)
            AnumFeedback(
              kind: AnumFeedbackKind.empty,
              message: status.isEmpty
                  ? l.t(widget.canWrite
                      ? 'biz.rinv.empty'
                      : 'biz.rinv.emptyMember')
                  : l.t('biz.rinv.emptyFiltered'),
            ),
          if (items.isNotEmpty)
            Text(l.f('biz.rinv.count', {'count': total}),
                style: TextStyle(color: p.muted)),
          for (final s in items)
            AnumRow(
              key: Key('rinv-${s.id}'),
              title: s.title,
              subtitle: [
                s.accountName ?? '-',
                frequencyText(l, s),
                nextText(l, s),
              ].join(' · '),
              detail:
                  '${money(context, s.total, s.currency)} · ${l.t('biz.rinv.status.${s.status}')}',
              icon: Icons.event_repeat_outlined,
              tone: s.lastError.isNotEmpty
                  ? AnumTone.stop
                  : scheduleTone(s.status),
              onTap: () => _open(s),
            ),
          if (items.length < total)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                onPressed: loadingMore ? null : more,
                child: Text(l.f(
                    'biz.rinv.more', {'shown': items.length, 'total': total})),
              ),
            ),
          if (moreError != null)
            Text(moreError!, style: TextStyle(color: p.stop)),
        ],
      ),
    );
  }

  Widget _automationCard(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final a = auto;
    return AnumSurface(
      key: const Key('rinv-auto'),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AnumHeader(
              title: l.t('biz.rinv.auto.title'),
              subtitle: l.t('biz.rinv.auto.help')),
          if (a == null && autoError == null)
            Text(l.t('biz.loading'), style: TextStyle(color: p.muted)),
          if (a != null) ...[
            Material(
              type: MaterialType.transparency,
              child: Column(children: [
                SwitchListTile(
                  key: const Key('rinv-auto-reminders'),
                  contentPadding: EdgeInsets.zero,
                  value: a.autoReminders,
                  onChanged: a.canChange && !autoBusy
                      ? (on) => _saveAuto(reminders: on)
                      : null,
                  title: Text(l.t('biz.rinv.auto.reminders')),
                ),
                SwitchListTile(
                  key: const Key('rinv-auto-emails'),
                  contentPadding: EdgeInsets.zero,
                  value: a.emailScheduled,
                  onChanged: a.canChange && !autoBusy
                      ? (on) => _saveAuto(emails: on)
                      : null,
                  title: Text(l.t('biz.rinv.auto.emails')),
                ),
              ]),
            ),
            if (!a.canChange)
              Text(l.t('biz.rinv.auto.ownersOnly'),
                  style: TextStyle(color: p.muted)),
            Text(
              a.lastRunAt == null
                  ? l.t('biz.rinv.auto.never')
                  : l.f('biz.rinv.auto.lastRun', {
                      'date': dayOfDate(a.lastRunAt),
                      'reminders': a.planned,
                      'emails': a.emails,
                      'skipped': a.skipped,
                      'failed': a.failed,
                    }),
              style: TextStyle(color: p.muted),
            ),
            if (a.note.isNotEmpty)
              Text(a.note, style: TextStyle(color: p.stop)),
            if (a.canChange && (a.autoReminders || a.emailScheduled))
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OutlinedButton.icon(
                  key: const Key('rinv-auto-run'),
                  onPressed: autoBusy ? null : _runAuto,
                  icon: const Icon(Icons.refresh),
                  label: Text(l.t('biz.rinv.auto.run')),
                ),
              ),
          ],
          if (autoNotice != null)
            Text(autoNotice!, style: TextStyle(color: p.ok)),
          if (autoError != null)
            Text(autoError!, style: TextStyle(color: p.stop)),
        ],
      ),
    );
  }
}

/// One recurring invoice: what it repeats, its next dates, the invoices it
/// drafted and what can be done with it.
class ScheduleScreen extends StatefulWidget {
  const ScheduleScreen({
    required this.repository,
    required this.scheduleId,
    required this.canWrite,
    this.onOpenInvoice,
    super.key,
  });
  final RecurringRepository repository;
  final String scheduleId;
  final bool canWrite;
  final void Function(String invoiceId)? onOpenInvoice;

  @override
  State<ScheduleScreen> createState() => _ScheduleState();
}

class _ScheduleState extends State<ScheduleScreen> {
  InvoiceSchedule? s;
  String? loadError, problem, notice;
  bool busy = false;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final next = await widget.repository.get(widget.scheduleId);
      if (mounted) setState(() => s = next);
    } catch (e) {
      if (mounted) setState(() => loadError = friendly(e));
    }
  }

  Future<void> _act(
      Future<InvoiceSchedule> Function() action, String done) async {
    setState(() {
      busy = true;
      problem = null;
      notice = null;
    });
    try {
      final next = await action();
      if (mounted) {
        setState(() {
          s = next;
          notice = done;
        });
      }
    } catch (e) {
      if (mounted) setState(() => problem = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<bool> _confirm(String title, String text, String action) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(text),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: Text(context.anum.t('biz.cancel'))),
            FilledButton(
                key: const Key('rinv-confirm'),
                onPressed: () => Navigator.pop(context, true),
                child: Text(action)),
          ],
        ),
      ) ??
      false;

  Future<void> _run(InvoiceSchedule x) async {
    final l = context.anum;
    setState(() {
      busy = true;
      problem = null;
      notice = null;
    });
    try {
      final (drafted, note, next) = await widget.repository.run(x.id);
      if (mounted) {
        setState(() {
          s = next;
          notice = drafted > 0
              ? l.f('biz.rinv.ran', {'count': drafted})
              : (note.isEmpty ? l.t('biz.rinv.ranNothing') : note);
        });
      }
    } catch (e) {
      if (mounted) setState(() => problem = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _edit(InvoiceSchedule x) async {
    final saved = await Navigator.of(context).push<InvoiceSchedule>(
        MaterialPageRoute<InvoiceSchedule>(
            builder: (_) => ScheduleFormScreen(
                repository: widget.repository, existing: x)));
    if (saved != null && mounted) {
      setState(() {
        s = saved;
        notice = context.anum.t('biz.rinv.saved');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final x = s;
    return Scaffold(
      appBar: AppBar(title: Text(x?.title ?? l.t('biz.rinv.title'))),
      body: AnumPage(
        onRefresh: load,
        children: [
          if (x == null && loadError == null)
            AnumFeedback(
                kind: AnumFeedbackKind.loading, message: l.t('biz.loading')),
          if (x == null && loadError != null)
            AnumFeedback(
                kind: AnumFeedbackKind.error,
                message: loadError!,
                onRetry: load),
          if (x != null) ...[
            Wrap(spacing: 8, runSpacing: 8, children: [
              AnumPill(
                  label: l.t('biz.rinv.status.${x.status}'),
                  tone: scheduleTone(x.status)),
              AnumPill(label: l.t('biz.rinv.issue.short.${x.issueMode}')),
            ]),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumRow(
                      title: x.accountName ?? '-',
                      subtitle: l.t('biz.rinv.client'),
                      icon: Icons.business_outlined),
                  AnumRow(
                      title: frequencyText(l, x),
                      subtitle: nextText(l, x),
                      icon: Icons.event_repeat_outlined),
                  AnumRow(
                      title: money(context, x.total, x.currency),
                      subtitle: l.t('biz.rinv.eachInvoice'),
                      icon: Icons.receipt_long_outlined),
                  AnumRow(
                      title: x.upcoming.isEmpty
                          ? l.t('biz.rinv.upcomingNone')
                          : x.upcoming.take(4).join(', '),
                      subtitle: l.t('biz.rinv.upcoming'),
                      icon: Icons.event_outlined),
                  AnumRow(
                      title:
                          l.f('biz.rinv.madeCount', {'count': x.createdCount}),
                      subtitle: l.t('biz.rinv.made'),
                      icon: Icons.history),
                ],
              ),
            ),
            if (x.lastError.isNotEmpty)
              Text(l.f('biz.rinv.lastError', {'reason': x.lastError}),
                  style: TextStyle(color: p.stop)),
            if (notice != null) Text(notice!, style: TextStyle(color: p.ok)),
            if (problem != null)
              Text(problem!, style: TextStyle(color: p.stop)),
            if (widget.canWrite)
              Wrap(spacing: 8, runSpacing: 8, children: [
                if (x.status == 'active')
                  FilledButton.icon(
                    key: const Key('rinv-run'),
                    onPressed: busy ? null : () => _run(x),
                    icon: const Icon(Icons.refresh),
                    label: Text(l.t('biz.rinv.run')),
                  ),
                if (x.status != 'ended')
                  OutlinedButton(
                    key: const Key('rinv-edit'),
                    onPressed: busy ? null : () => _edit(x),
                    child: Text(l.t('biz.rinv.edit')),
                  ),
                if (x.status == 'active')
                  OutlinedButton(
                    key: const Key('rinv-pause'),
                    onPressed: busy
                        ? null
                        : () => _act(
                            () => widget.repository
                                .change(x.id, 'pause', x.version),
                            l.t('biz.rinv.paused')),
                    child: Text(l.t('biz.rinv.pause')),
                  ),
                if (x.status == 'paused')
                  OutlinedButton(
                    key: const Key('rinv-resume'),
                    onPressed: busy
                        ? null
                        : () => _act(
                            () => widget.repository
                                .change(x.id, 'resume', x.version),
                            l.t('biz.rinv.resumed')),
                    child: Text(l.t('biz.rinv.resume')),
                  ),
                if (x.status != 'ended')
                  OutlinedButton(
                    key: const Key('rinv-end'),
                    onPressed: busy
                        ? null
                        : () async {
                            if (await _confirm(l.t('biz.rinv.endTitle'),
                                l.t('biz.rinv.endText'), l.t('biz.rinv.end'))) {
                              await _act(
                                  () => widget.repository
                                      .change(x.id, 'end', x.version),
                                  l.t('biz.rinv.ended'));
                            }
                          },
                    child: Text(l.t('biz.rinv.end')),
                  ),
                if (x.createdCount == 0)
                  OutlinedButton(
                    key: const Key('rinv-delete'),
                    onPressed: busy
                        ? null
                        : () async {
                            if (!await _confirm(
                                l.t('biz.rinv.deleteTitle'),
                                l.t('biz.rinv.deleteText'),
                                l.t('biz.rinv.delete'))) {
                              return;
                            }
                            try {
                              await widget.repository.delete(x.id);
                              if (context.mounted) Navigator.of(context).pop();
                            } catch (e) {
                              if (mounted) {
                                setState(() => problem = friendly(e));
                              }
                            }
                          },
                    child: Text(l.t('biz.rinv.delete')),
                  ),
              ])
            else
              Text(l.t('biz.rinv.readOnly'), style: TextStyle(color: p.muted)),
            AnumSurface(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AnumHeader(title: l.t('biz.rinv.history')),
                  if (x.invoices.isEmpty)
                    Text(l.t('biz.rinv.historyNone'),
                        style: TextStyle(color: p.muted)),
                  for (final i in x.invoices)
                    AnumRow(
                      key: Key('rinv-invoice-${i.id}'),
                      title: i.number ??
                          l.f('biz.rinv.draftFor',
                              {'date': i.periodDate ?? '-'}),
                      subtitle:
                          '${l.t('biz.inv.status.${i.status}')} · ${i.periodDate ?? '-'}',
                      detail: money(context, i.total, i.currency),
                      icon: Icons.receipt_outlined,
                      tone: invoiceTone(i.status),
                      onTap: widget.onOpenInvoice == null
                          ? null
                          : () => widget.onOpenInvoice!(i.id),
                    ),
                  if (x.invoicesTotal > x.invoices.length)
                    Text(
                        l.f('biz.rinv.historyMore', {
                          'shown': x.invoices.length,
                          'total': x.invoicesTotal
                        }),
                        style: TextStyle(color: p.muted)),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Make or edit a recurring invoice.
class ScheduleFormScreen extends StatefulWidget {
  const ScheduleFormScreen(
      {required this.repository, this.existing, super.key});
  final RecurringRepository repository;
  final InvoiceSchedule? existing;

  @override
  State<ScheduleFormScreen> createState() => _ScheduleFormState();
}

class _LineFields {
  _LineFields([ScheduleLine? line])
      : description = TextEditingController(text: line?.description ?? ''),
        quantity = TextEditingController(
            text: line == null ? '1' : _plain(line.quantity)),
        price = TextEditingController(
            text: line == null ? '' : _plain(line.unitPrice)),
        tax = TextEditingController(
            text: line == null ? '' : _plain(line.taxRatePct));
  final TextEditingController description, quantity, price, tax;
  void dispose() {
    description.dispose();
    quantity.dispose();
    price.dispose();
    tax.dispose();
  }

  static String _plain(double v) =>
      v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v';
}

class _ScheduleFormState extends State<ScheduleFormScreen> {
  late final title = TextEditingController(text: widget.existing?.title ?? '');
  late final start =
      TextEditingController(text: widget.existing?.startDate ?? today());
  late final interval =
      TextEditingController(text: '${widget.existing?.interval ?? 1}');
  late final endDate =
      TextEditingController(text: widget.existing?.endDate ?? '');
  late final count = TextEditingController(
      text: widget.existing?.maxCount == null
          ? ''
          : '${widget.existing!.maxCount}');
  late final due =
      TextEditingController(text: '${widget.existing?.dueInDays ?? 14}');
  late final reference =
      TextEditingController(text: widget.existing?.customerReference ?? '');
  final accountQuery = TextEditingController();
  late final lines = [
    for (final line in widget.existing?.lines ?? const <ScheduleLine>[])
      _LineFields(line),
    if ((widget.existing?.lines ?? const []).isEmpty) _LineFields(),
  ];
  late String frequency = widget.existing?.frequency ?? 'monthly';
  late String issueMode = widget.existing?.issueMode ?? 'draft';
  late String currency = widget.existing?.currency ?? 'SAR';
  late String end = widget.existing?.endDate != null
      ? 'date'
      : widget.existing?.maxCount != null
          ? 'count'
          : 'never';
  String? accountId;
  List<ClientAccount> accounts = const [];
  String? accountsError, problem;
  bool busy = false;

  bool get editing => widget.existing != null;
  bool get hasInterval =>
      frequency == 'days' || frequency == 'weeks' || frequency == 'months';

  @override
  void initState() {
    super.initState();
    if (!editing) _searchAccounts();
  }

  @override
  void dispose() {
    for (final c in [
      title,
      start,
      interval,
      endDate,
      count,
      due,
      reference,
      accountQuery
    ]) {
      c.dispose();
    }
    for (final line in lines) {
      line.dispose();
    }
    super.dispose();
  }

  Future<void> _searchAccounts() async {
    try {
      final found = await widget.repository.accounts(accountQuery.text.trim());
      if (mounted) {
        setState(() {
          accounts = found;
          accountsError = null;
          if (accountId != null && !found.any((a) => a.id == accountId)) {
            accountId = null;
          }
        });
      }
    } catch (e) {
      if (mounted) setState(() => accountsError = friendly(e));
    }
  }

  static final _day = RegExp(r'^\d{4}-\d{2}-\d{2}$');

  /// The request body, or a sentence saying what to fix.
  (Map<String, Object?>?, String?) _body(AnumLocalizations l) {
    if (!editing && accountId == null) {
      return (null, l.t('biz.rinv.err.account'));
    }
    if (!_day.hasMatch(start.text.trim())) {
      return (null, l.t('biz.rinv.err.start'));
    }
    final parsed = <Map<String, Object?>>[];
    for (final line in lines) {
      final text = line.description.text.trim();
      if (text.isEmpty && line.price.text.trim().isEmpty) continue;
      final qty = double.tryParse(line.quantity.text.trim());
      final price = double.tryParse(line.price.text.trim().replaceAll(',', ''));
      final tax = line.tax.text.trim().isEmpty
          ? 0.0
          : double.tryParse(line.tax.text.trim());
      if (text.isEmpty ||
          qty == null ||
          qty <= 0 ||
          price == null ||
          price <= 0) {
        return (null, l.t('biz.rinv.err.line'));
      }
      if (tax == null || tax < 0 || tax > 100) {
        return (null, l.t('biz.rinv.err.tax'));
      }
      parsed.add(ScheduleLine(text, qty, price, tax).toJson());
    }
    if (parsed.isEmpty) return (null, l.t('biz.rinv.err.lines'));
    final body = <String, Object?>{
      if (title.text.trim().isNotEmpty) 'title': title.text.trim(),
      if (!editing) 'account_id': accountId,
      if (!editing) 'currency': currency,
      'frequency': frequency,
      'start_date': start.text.trim(),
      'issue_mode': issueMode,
      'customer_reference': reference.text.trim(),
      'lines': parsed,
      'end_date': null,
      'max_count': null,
    };
    if (hasInterval) {
      final every = int.tryParse(interval.text.trim());
      if (every == null || every < 1) {
        return (null, l.t('biz.rinv.err.interval'));
      }
      body['interval'] = every;
    }
    if (end == 'date') {
      if (!_day.hasMatch(endDate.text.trim())) {
        return (null, l.t('biz.rinv.err.endDate'));
      }
      body['end_date'] = endDate.text.trim();
    } else if (end == 'count') {
      final n = int.tryParse(count.text.trim());
      if (n == null || n < 1 || n > 1000) {
        return (null, l.t('biz.rinv.err.count'));
      }
      body['max_count'] = n;
    }
    final days = int.tryParse(due.text.trim());
    if (days == null || days < 0 || days > 365) {
      return (null, l.t('biz.rinv.err.due'));
    }
    body['due_in_days'] = days;
    return (body, null);
  }

  Future<void> _save() async {
    final l = context.anum;
    final (body, error) = _body(l);
    if (body == null) {
      setState(() => problem = error);
      return;
    }
    setState(() {
      busy = true;
      problem = null;
    });
    try {
      final existing = widget.existing;
      final saved = existing == null
          ? await widget.repository.create(body)
          : await widget.repository.update(existing.id, existing.version, body);
      if (mounted) Navigator.of(context).pop(saved);
    } catch (e) {
      if (mounted) setState(() => problem = friendly(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return Scaffold(
      appBar:
          AppBar(title: Text(l.t(editing ? 'biz.rinv.edit' : 'biz.rinv.new'))),
      body: AnumPage(
        children: [
          Text(l.t(editing ? 'biz.rinv.form.editIntro' : 'biz.rinv.form.intro'),
              style: TextStyle(color: p.muted)),
          if (!editing) ...[
            TextField(
              key: const Key('rinv-account-search'),
              controller: accountQuery,
              decoration:
                  InputDecoration(labelText: l.t('biz.rinv.findClient')),
              onSubmitted: (_) => _searchAccounts(),
            ),
            DropdownButtonFormField<String>(
              key: const Key('rinv-account'),
              initialValue: accountId,
              isExpanded: true,
              decoration: InputDecoration(labelText: l.t('biz.rinv.client')),
              items: [
                for (final a in accounts)
                  DropdownMenuItem(value: a.id, child: Text(a.name)),
              ],
              onChanged: (value) => setState(() => accountId = value),
            ),
            if (accountsError != null)
              Text(accountsError!, style: TextStyle(color: p.stop)),
            DropdownButtonFormField<String>(
              key: const Key('rinv-currency'),
              isExpanded: true,
              initialValue: currency,
              decoration: InputDecoration(labelText: l.t('biz.rinv.currency')),
              items: [
                for (final c in const ['SAR', 'AED', 'USD', 'EUR', 'GBP'])
                  DropdownMenuItem(value: c, child: Text(c)),
              ],
              onChanged: (value) => setState(() => currency = value ?? 'SAR'),
            ),
          ] else
            Text(widget.existing!.accountName ?? '-',
                style: Theme.of(context).textTheme.titleMedium),
          TextField(
            key: const Key('rinv-title'),
            controller: title,
            decoration: InputDecoration(labelText: l.t('biz.rinv.form.title')),
          ),
          DropdownButtonFormField<String>(
            key: const Key('rinv-frequency'),
            isExpanded: true,
            initialValue: frequency,
            decoration:
                InputDecoration(labelText: l.t('biz.rinv.form.frequency')),
            items: [
              for (final f in const [
                'monthly',
                'quarterly',
                'yearly',
                'weeks',
                'days',
                'months'
              ])
                DropdownMenuItem(
                    value: f, child: Text(l.t('biz.rinv.freq.$f'))),
            ],
            onChanged: (value) =>
                setState(() => frequency = value ?? 'monthly'),
          ),
          if (hasInterval)
            TextField(
              key: const Key('rinv-interval'),
              controller: interval,
              keyboardType: TextInputType.number,
              decoration: InputDecoration(
                  labelText: l.t('biz.rinv.form.every.$frequency')),
            ),
          TextField(
            key: const Key('rinv-start'),
            controller: start,
            keyboardType: TextInputType.datetime,
            decoration: InputDecoration(
                labelText: l.t('biz.rinv.form.start'),
                helperText: 'YYYY-MM-DD'),
          ),
          DropdownButtonFormField<String>(
            key: const Key('rinv-end-kind'),
            isExpanded: true,
            initialValue: end,
            decoration: InputDecoration(labelText: l.t('biz.rinv.form.ends')),
            items: [
              for (final e in const ['never', 'date', 'count'])
                DropdownMenuItem(
                    value: e, child: Text(l.t('biz.rinv.form.ends.$e'))),
            ],
            onChanged: (value) => setState(() => end = value ?? 'never'),
          ),
          if (end == 'date')
            TextField(
              key: const Key('rinv-end-date'),
              controller: endDate,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(
                  labelText: l.t('biz.rinv.form.endDate'),
                  helperText: 'YYYY-MM-DD'),
            ),
          if (end == 'count')
            TextField(
              key: const Key('rinv-count'),
              controller: count,
              keyboardType: TextInputType.number,
              decoration:
                  InputDecoration(labelText: l.t('biz.rinv.form.count')),
            ),
          TextField(
            key: const Key('rinv-due'),
            controller: due,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(labelText: l.t('biz.rinv.form.due')),
          ),
          AnumHeader(title: l.t('biz.rinv.form.lines')),
          for (var i = 0; i < lines.length; i++)
            AnumSurface(
              key: Key('rinv-line-$i'),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    key: Key('rinv-line-$i-description'),
                    controller: lines[i].description,
                    decoration: InputDecoration(
                        labelText: l.t('biz.rinv.line.description')),
                  ),
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    SizedBox(
                      width: 96,
                      child: TextField(
                        key: Key('rinv-line-$i-quantity'),
                        controller: lines[i].quantity,
                        keyboardType: const TextInputType.numberWithOptions(
                            decimal: true),
                        decoration: InputDecoration(
                            labelText: l.t('biz.rinv.line.quantity')),
                      ),
                    ),
                    SizedBox(
                      width: 140,
                      child: TextField(
                        key: Key('rinv-line-$i-price'),
                        controller: lines[i].price,
                        keyboardType: const TextInputType.numberWithOptions(
                            decimal: true),
                        decoration: InputDecoration(
                            labelText: l.t('biz.rinv.line.price')),
                      ),
                    ),
                    SizedBox(
                      width: 96,
                      child: TextField(
                        key: Key('rinv-line-$i-tax'),
                        controller: lines[i].tax,
                        keyboardType: const TextInputType.numberWithOptions(
                            decimal: true),
                        decoration: InputDecoration(
                            labelText: l.t('biz.rinv.line.tax')),
                      ),
                    ),
                  ]),
                  if (lines.length > 1)
                    Align(
                      alignment: AlignmentDirectional.centerEnd,
                      child: TextButton(
                        onPressed: () =>
                            setState(() => lines.removeAt(i).dispose()),
                        child: Text(l.t('biz.rinv.line.remove')),
                      ),
                    ),
                ],
              ),
            ),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              key: const Key('rinv-add-line'),
              onPressed: lines.length >= 60
                  ? null
                  : () => setState(() => lines.add(_LineFields())),
              icon: const Icon(Icons.add),
              label: Text(l.t('biz.rinv.line.add')),
            ),
          ),
          AnumHeader(
              title: l.t('biz.rinv.form.send'),
              subtitle: l.t('biz.rinv.issue.help')),
          Material(
            type: MaterialType.transparency,
            child: RadioGroup<String>(
              groupValue: issueMode,
              onChanged: (value) =>
                  setState(() => issueMode = value ?? 'draft'),
              child: Column(children: [
                for (final mode in const ['draft', 'request_approval'])
                  RadioListTile<String>(
                    key: Key('rinv-issue-$mode'),
                    contentPadding: EdgeInsets.zero,
                    value: mode,
                    title: Text(l.t('biz.rinv.issue.$mode')),
                  ),
              ]),
            ),
          ),
          TextField(
            key: const Key('rinv-reference'),
            controller: reference,
            decoration:
                InputDecoration(labelText: l.t('biz.rinv.form.reference')),
          ),
          if (problem != null)
            Text(problem!,
                key: const Key('rinv-problem'),
                style: TextStyle(color: p.stop)),
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: FilledButton(
              key: const Key('rinv-save'),
              onPressed: busy ? null : _save,
              child: Text(l.t('biz.rinv.save')),
            ),
          ),
        ],
      ),
    );
  }
}
