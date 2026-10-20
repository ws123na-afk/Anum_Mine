import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'billing_data.dart';

enum BlPhase { loading, ready, denied, error }

/// Opens a web address outside the app (the card payment page).
typedef BlUrlOpener = Future<bool> Function(Uri url);

Future<bool> _launch(Uri url) =>
    launchUrl(url, mode: LaunchMode.externalApplication);

/// State of the Billing screen (docs/billing.md): the overview, the plans the
/// organisation may move to, and its invoices paged 20 at a time.
class BillingController extends ChangeNotifier {
  BillingController(this.repository, {this.openUrl = _launch});
  final BillingRepository repository;
  final BlUrlOpener openUrl;
  static const pageSize = 20;

  BlPhase phase = BlPhase.loading;
  String? error;
  BlOverview? overview;

  List<BlPlan> plans = const [];
  String? plansError;

  /// True while a plan change, cancel or reactivate is being sent.
  bool busy = false;

  String query = '';
  String? status;
  List<BlInvoice> invoices = const [];
  int invoiceTotal = 0;
  bool invoicesLoading = false;
  String? invoicesError;
  bool loadingMore = false;
  String? moreError;

  bool get hasMoreInvoices => invoices.length < invoiceTotal;

  /// The plan named [id], for "changes to ... at period end".
  BlPlan? planById(String? id) {
    for (final plan in plans) {
      if (plan.id == id) return plan;
    }
    return null;
  }

  Future<void> load() async {
    phase = BlPhase.loading;
    error = null;
    notifyListeners();
    try {
      overview = await repository.overview();
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? BlPhase.denied : BlPhase.error;
      error = e.message;
      notifyListeners();
      return;
    } on Object catch (e) {
      phase = BlPhase.error;
      error = '$e';
      notifyListeners();
      return;
    }
    await Future.wait([_loadPlans(), _loadInvoices()]);
    phase = BlPhase.ready;
    notifyListeners();
  }

  Future<void> _loadPlans() async {
    plansError = null;
    try {
      plans = await repository.plans();
    } on ApiException catch (e) {
      plansError = e.message;
    } on Object catch (e) {
      plansError = '$e';
    }
  }

  Future<void> reloadPlans() async {
    await _loadPlans();
    notifyListeners();
  }

  Future<void> _loadInvoices() async {
    invoicesLoading = true;
    invoicesError = null;
    moreError = null;
    try {
      final page = await repository.invoices(
          query: query, status: status, limit: pageSize);
      invoices = page.items;
      invoiceTotal = page.total;
    } on ApiException catch (e) {
      invoicesError = e.message;
    } on Object catch (e) {
      invoicesError = '$e';
    }
    invoicesLoading = false;
  }

  /// Searches by invoice number and filters by status from the first page.
  Future<void> searchInvoices({String? text, String? withStatus}) async {
    if (text != null) query = text;
    status = withStatus;
    invoicesLoading = true;
    notifyListeners();
    await _loadInvoices();
    notifyListeners();
  }

  Future<void> moreInvoices() async {
    if (loadingMore || !hasMoreInvoices) return;
    loadingMore = true;
    moreError = null;
    notifyListeners();
    try {
      final page = await repository.invoices(
          query: query,
          status: status,
          offset: invoices.length,
          limit: pageSize);
      invoices = [...invoices, ...page.items];
      invoiceTotal = page.total;
    } on ApiException catch (e) {
      moreError = e.message;
    } on Object catch (e) {
      moreError = '$e';
    }
    loadingMore = false;
    notifyListeners();
  }

  /// Sends a subscription change, then reloads everything. Returns the
  /// failure to show, or null when it worked.
  Future<String?> act(Future<void> Function() action) async {
    if (busy) return null;
    busy = true;
    notifyListeners();
    String? failure;
    try {
      await action();
    } on ApiException catch (e) {
      failure = e.statusCode == 409 ? e.message : e.readable;
    } on Object catch (e) {
      failure = '$e';
    }
    busy = false;
    if (failure == null) {
      await load();
    } else {
      notifyListeners();
    }
    return failure;
  }

  Future<String?> change(String planId) => act(() => repository.change(planId));
  Future<String?> cancel() => act(repository.cancel);
  Future<String?> reactivate() => act(repository.reactivate);
}

AnumTone blSubscriptionTone(String status) => switch (status) {
      'active' => AnumTone.ok,
      'trialing' => AnumTone.info,
      'past_due' => AnumTone.stop,
      'paused' => AnumTone.warn,
      _ => AnumTone.neutral,
    };

String blSubscriptionStatus(AnumLocalizations l, String status) {
  const known = {'trialing', 'active', 'past_due', 'paused', 'cancelled'};
  return l.t('bl.sub.${known.contains(status) ? status : 'unknown'}');
}

AnumTone blInvoiceTone(BlInvoice invoice) => invoice.overdue
    ? AnumTone.stop
    : switch (invoice.status) {
        'paid' => AnumTone.ok,
        'open' => AnumTone.warn,
        'uncollectible' => AnumTone.stop,
        _ => AnumTone.neutral,
      };

String blInvoiceStatus(AnumLocalizations l, BlInvoice invoice) {
  if (invoice.overdue) return l.t('bl.inv.overdue');
  const known = {'draft', 'open', 'paid', 'void', 'uncollectible'};
  return l.t(
      'bl.inv.${known.contains(invoice.status) ? invoice.status : 'unknown'}');
}

String _date(BuildContext context, DateTime? value) {
  if (value == null) return '-';
  return MaterialLocalizations.of(context).formatMediumDate(value.toLocal());
}

String _locale(BuildContext context) =>
    Localizations.localeOf(context).toLanguageTag();

String _money(BuildContext context, int minor, String currency) =>
    blMoney(minor, currency, _locale(context));

/// "SAR 99.00 per month", with "per seat" for per-seat plans.
String _price(BuildContext context, int minor, String currency, String interval,
    bool perSeat) {
  final l = context.anum;
  return l.f(perSeat ? 'bl.pricePerSeat' : 'bl.price', {
    'amount': _money(context, minor, currency),
    'interval': l.t(interval == 'year' ? 'bl.year' : 'bl.month'),
  });
}

String _meterName(AnumLocalizations l, String key) {
  const known = {'seats', 'workspaces', 'agent_runs', 'storage', 'messages'};
  return known.contains(key) ? l.t('bl.meter.$key') : key;
}

/// "3 of 5", "2.5 GB of 10.0 GB", or "3, unlimited".
String _meterValue(BuildContext context, BlMeter meter) {
  final l = context.anum;
  String amount(int value) => meter.unit == 'bytes'
      ? l.f('bl.gb', {'value': blGigabytes(value, _locale(context))})
      : '$value';
  final limit = meter.limit;
  return limit == null
      ? l.f('bl.usedUnlimited', {'used': amount(meter.used)})
      : l.f('bl.usedOf', {'used': amount(meter.used), 'limit': amount(limit)});
}

/// A plan limit for the plan cards: "Seats: 5", "Storage: 10 GB".
String _limitText(AnumLocalizations l, String key, int? value) {
  const names = {
    'seats': 'bl.meter.seats',
    'workspaces': 'bl.meter.workspaces',
    'agent_runs_per_month': 'bl.limit.agentRuns',
    'storage_gb': 'bl.meter.storage',
    'messages_per_month': 'bl.limit.messages',
  };
  final name = names[key] == null ? key : l.t(names[key]!);
  final amount = value == null
      ? l.t('bl.unlimited')
      : key == 'storage_gb'
          ? l.f('bl.gb', {'value': '$value'})
          : '$value';
  return l.f('bl.limitLine', {'name': name, 'value': amount});
}

/// The organisation's plan, usage, payment details and invoices, for its
/// billing admins (workspace owners and organisation administrators).
class BillingScreen extends StatefulWidget {
  const BillingScreen({required this.controller, super.key});
  final BillingController controller;
  @override
  State<BillingScreen> createState() => _BillingScreenState();
}

class _BillingScreenState extends State<BillingScreen>
    with SingleTickerProviderStateMixin {
  BillingController get c => widget.controller;
  late final TabController _tabs = TabController(length: 3, vsync: this);

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final ready = c.phase == BlPhase.ready;
        return Scaffold(
          appBar: AppBar(
            title: Text(l.t('bl.title')),
            actions: [
              IconButton(
                  onPressed: c.busy ? null : c.load,
                  tooltip: l.t('refresh'),
                  icon: const Icon(Icons.refresh))
            ],
            bottom: ready
                ? TabBar(
                    controller: _tabs,
                    isScrollable: true,
                    tabAlignment: TabAlignment.start,
                    tabs: [
                      Tab(text: l.t('bl.tab.overview')),
                      Tab(text: l.t('bl.tab.plans')),
                      Tab(text: l.t('bl.tab.invoices')),
                    ],
                  )
                : null,
          ),
          body: switch (c.phase) {
            BlPhase.loading => Center(
                child: Semantics(
                    label: l.t('bl.loading'),
                    child: const CircularProgressIndicator())),
            BlPhase.denied => _message(
                Icons.lock_outline, l.t('bl.deniedTitle'), l.t('bl.denied')),
            BlPhase.error => _message(Icons.error_outline, l.t('bl.errorTitle'),
                l.f('bl.errorHint', {'message': c.error ?? ''}),
                action: l.t('retry'), onAction: c.load),
            BlPhase.ready => Column(children: [
                if (c.busy) const LinearProgressIndicator(),
                Expanded(
                  child: TabBarView(controller: _tabs, children: [
                    _OverviewTab(
                        controller: c, onChangePlan: () => _tabs.animateTo(1)),
                    _PlansTab(controller: c),
                    _InvoicesTab(controller: c),
                  ]),
                ),
              ]),
          },
        );
      },
    );
  }

  Widget _message(IconData icon, String title, String text,
          {String? action, VoidCallback? onAction}) =>
      AnumPage(children: [
        AnumEmpty(
            icon: icon,
            title: title,
            message: text,
            action: action,
            onAction: onAction)
      ]);
}

void _snack(BuildContext context, String text) =>
    ScaffoldMessenger.maybeOf(context)
        ?.showSnackBar(SnackBar(content: Text(text)));

/// Asks before a cancel or reactivate; true when confirmed.
Future<bool> _confirm(BuildContext context,
    {required String title,
    required String message,
    required String confirm}) async {
  final l = context.anum;
  final answer = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      scrollable: true,
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(l.t('bl.keep'))),
        FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(confirm)),
      ],
    ),
  );
  return answer ?? false;
}

/// A label and value that share a line when they fit and stack when not.
class _Field extends StatelessWidget {
  const _Field(this.label, this.value, {this.strong = false, this.trailing});
  final String label, value;
  final bool strong;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final valueStyle = TextStyle(
        color: p.text,
        fontSize: strong ? AnumType.base : AnumType.md,
        fontWeight: strong ? FontWeight.w700 : FontWeight.w500);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
      child: Wrap(
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: AnumSpacing.xs,
        children: [
          Text(label,
              style: TextStyle(color: p.muted, fontSize: AnumType.smPlus)),
          Row(mainAxisSize: MainAxisSize.min, children: [
            Flexible(child: SelectableText(value, style: valueStyle)),
            if (trailing != null) trailing!,
          ]),
        ],
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Semantics(
        header: true,
        child: Text(text,
            style: TextStyle(
                color: context.palette.text,
                fontSize: AnumType.lg,
                fontWeight: FontWeight.w700)),
      );
}

/// A tinted notice: read-only, overdue, or a plan that ends.
class _Banner extends StatelessWidget {
  const _Banner(
      {required this.icon,
      required this.title,
      required this.message,
      required this.tone});
  final IconData icon;
  final String title, message;
  final AnumTone tone;

  @override
  Widget build(BuildContext context) {
    final color = anumToneColor(context, tone);
    final p = context.palette;
    return Semantics(
      liveRegion: true,
      container: true,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(AnumRadius.md),
          border: Border.all(color: color.withValues(alpha: 0.4)),
        ),
        child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.sm),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(icon, color: color),
            const SizedBox(width: AnumSpacing.sm),
            Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title,
                        style: TextStyle(
                            color: p.text, fontWeight: FontWeight.w700)),
                    const SizedBox(height: AnumSpacing.xxs),
                    Text(message, style: TextStyle(color: p.muted)),
                  ]),
            ),
          ]),
        ),
      ),
    );
  }
}

/// Two columns of sections on wide screens, one on phones.
class _Columns extends StatelessWidget {
  const _Columns({required this.start, required this.end});
  final List<Widget> start, end;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        Widget column(List<Widget> items) =>
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              for (var i = 0; i < items.length; i++) ...[
                items[i],
                if (i < items.length - 1)
                  const SizedBox(height: AnumSpacing.md),
              ]
            ]);
        if (box.maxWidth < AnumBreakpoints.expanded) {
          return column([...start, ...end]);
        }
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(child: column(start)),
          const SizedBox(width: AnumSpacing.md),
          Expanded(child: column(end)),
        ]);
      });
}

class _OverviewTab extends StatelessWidget {
  const _OverviewTab({required this.controller, required this.onChangePlan});
  final BillingController controller;
  final VoidCallback onChangePlan;

  Future<void> _cancel(BuildContext context) async {
    final l = context.anum;
    final sub = controller.overview?.subscription;
    final ok = await _confirm(context,
        title: l.t('bl.cancelTitle'),
        message: l.f('bl.cancelMessage',
            {'date': _date(context, sub?.currentPeriodEnd)}),
        confirm: l.t('bl.cancelConfirm'));
    if (!ok || !context.mounted) return;
    final failure = await controller.cancel();
    if (context.mounted) {
      _snack(context, failure ?? l.t('bl.cancelled'));
    }
  }

  Future<void> _reactivate(BuildContext context) async {
    final l = context.anum;
    final sub = controller.overview?.subscription;
    final ok = await _confirm(context,
        title: l.t('bl.reactivateTitle'),
        message: l.t(sub?.isCancelled ?? false
            ? 'bl.restartMessage'
            : 'bl.reactivateMessage'),
        confirm: l.t('bl.reactivateConfirm'));
    if (!ok || !context.mounted) return;
    final failure = await controller.reactivate();
    if (context.mounted) {
      _snack(context, failure ?? l.t('bl.reactivated'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final view = controller.overview!;
    final sub = view.subscription;
    final banners = <Widget>[
      if (view.readOnly)
        _Banner(
            icon: Icons.lock_clock_outlined,
            tone: AnumTone.stop,
            title: l.t('bl.readOnlyTitle'),
            message: l.t('bl.readOnly'))
      else if (view.overdue)
        _Banner(
            icon: Icons.warning_amber,
            tone: AnumTone.warn,
            title: l.t('bl.overdueTitle'),
            message: l.f('bl.overdue', {
              'amount': _money(context, view.openBalanceMinor, view.currency)
            })),
    ];
    if (sub == null) {
      return AnumPage(onRefresh: controller.load, children: [
        ...banners,
        AnumEmpty(
          icon: Icons.receipt_long_outlined,
          title: l.t('bl.noPlanTitle'),
          message: view.issuerEmail.isEmpty
              ? l.t('bl.noPlan')
              : l.f('bl.noPlanContact', {
                  'name': view.issuerName.isEmpty ? '-' : view.issuerName,
                  'email': view.issuerEmail
                }),
          action: l.t('bl.checkAgain'),
          onAction: controller.load,
        ),
        if (view.account != null) _ContactCard(account: view.account!),
      ]);
    }
    final pending = controller.planById(sub.pendingPlanId);
    final status = AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.t('bl.currentPlan'),
            style: TextStyle(
                color: context.palette.muted, fontSize: AnumType.xsPlus)),
        const SizedBox(height: AnumSpacing.xxs),
        Wrap(
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(sub.planName.isEmpty ? '-' : sub.planName,
                style: Theme.of(context).textTheme.titleLarge),
            AnumPill(
                label: blSubscriptionStatus(l, sub.status),
                tone: blSubscriptionTone(sub.status)),
          ],
        ),
        const SizedBox(height: AnumSpacing.xs),
        Text(_price(
            context, sub.priceMinor, sub.currency, sub.interval, sub.perSeat)),
        if (sub.perSeat) Text(l.f('bl.seats', {'count': sub.seats})),
        const SizedBox(height: AnumSpacing.xs),
        if (sub.status == 'trialing' && sub.trialEnd != null)
          _Field(l.t('bl.trialEnds'), _date(context, sub.trialEnd)),
        if (!sub.isCancelled && sub.currentPeriodEnd != null)
          _Field(l.t(sub.cancelAtPeriodEnd ? 'bl.endsOn' : 'bl.renewsOn'),
              _date(context, sub.currentPeriodEnd)),
        if (sub.pendingPlanId != null && sub.pendingPlanId!.isNotEmpty)
          _Field(
              l.t('bl.pendingPlan'),
              l.f('bl.pendingPlanValue', {
                'plan': pending?.name ?? l.t('bl.anotherPlan'),
                'date': _date(context, sub.currentPeriodEnd)
              })),
        _Field(l.t('bl.openBalance'),
            _money(context, view.openBalanceMinor, view.currency),
            strong: view.openBalanceMinor > 0),
        if ((view.account?.creditBalanceMinor ?? 0) > 0)
          _Field(l.t('bl.credit'),
              _money(context, view.account!.creditBalanceMinor, view.currency)),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton.icon(
              onPressed: controller.busy ? null : onChangePlan,
              icon: const Icon(Icons.swap_vert),
              label: Text(l.t('bl.changePlan'))),
          if (sub.isCancelled || sub.cancelAtPeriodEnd)
            OutlinedButton.icon(
                onPressed: controller.busy ? null : () => _reactivate(context),
                icon: const Icon(Icons.restart_alt),
                label:
                    Text(l.t(sub.isCancelled ? 'bl.restart' : 'bl.reactivate')))
          else
            OutlinedButton.icon(
                onPressed: controller.busy ? null : () => _cancel(context),
                icon: const Icon(Icons.cancel_outlined),
                label: Text(l.t('bl.cancel'))),
        ]),
      ]),
    );
    final notices = <Widget>[
      if (sub.cancelAtPeriodEnd && !sub.isCancelled)
        _Banner(
            icon: Icons.event_busy_outlined,
            tone: AnumTone.warn,
            title: l.t('bl.endingTitle'),
            message: l.f(
                'bl.ending', {'date': _date(context, sub.currentPeriodEnd)})),
    ];
    return AnumPage(onRefresh: controller.load, children: [
      ...banners,
      ...notices,
      _Columns(start: [
        status,
        _UsageCard(view: view),
      ], end: [
        _PaymentCard(view: view),
        if (view.account != null) _ContactCard(account: view.account!),
      ]),
    ]);
  }
}

class _UsageCard extends StatelessWidget {
  const _UsageCard({required this.view});
  final BlOverview view;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.f('bl.usage', {'period': view.period})),
        const SizedBox(height: AnumSpacing.xs),
        for (final meter in view.meters) ...[
          _Field(_meterName(l, meter.key), _meterValue(context, meter)),
          if (meter.ratio != null)
            Semantics(
              label: l.f('bl.meterA11y', {
                'name': _meterName(l, meter.key),
                'value': _meterValue(context, meter)
              }),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(AnumRadius.pill),
                child: LinearProgressIndicator(
                  minHeight: 6,
                  value: meter.ratio!.clamp(0, 1).toDouble(),
                  backgroundColor: p.line,
                  color: anumToneColor(
                      context,
                      meter.ratio! >= 1
                          ? AnumTone.stop
                          : meter.ratio! >= 0.8
                              ? AnumTone.warn
                              : AnumTone.ok),
                ),
              ),
            ),
          const SizedBox(height: AnumSpacing.xs),
        ],
        Text(l.t('bl.usageNote'),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
      ]),
    );
  }
}

/// Bank transfer details with a copy button for the IBAN.
class _BankDetails extends StatelessWidget {
  const _BankDetails({required this.bank});
  final BlBankDetails bank;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (bank.bankName.isNotEmpty) _Field(l.t('bl.bankName'), bank.bankName),
      if (bank.accountName.isNotEmpty)
        _Field(l.t('bl.accountName'), bank.accountName),
      _Field(
        l.t('bl.iban'),
        bank.iban,
        trailing: IconButton(
          tooltip: l.t('bl.copyIban'),
          icon: const Icon(Icons.copy, size: 18),
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: bank.iban));
            if (context.mounted) _snack(context, l.t('bl.ibanCopied'));
          },
        ),
      ),
      if (bank.swift.isNotEmpty) _Field(l.t('bl.swift'), bank.swift),
    ]);
  }
}

class _PaymentCard extends StatelessWidget {
  const _PaymentCard({required this.view});
  final BlOverview view;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.t('bl.payment')),
        const SizedBox(height: AnumSpacing.xs),
        if (view.bank.enabled) ...[
          Text(l.t('bl.bankTransfer'),
              style: const TextStyle(fontWeight: FontWeight.w600)),
          _BankDetails(bank: view.bank),
          const SizedBox(height: AnumSpacing.xxs),
          Text(l.t('bl.reference'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ] else
          Text(
              view.issuerEmail.isEmpty
                  ? l.t('bl.noBank')
                  : l.f('bl.noBankContact', {'email': view.issuerEmail}),
              style: TextStyle(color: p.muted)),
        if (view.cardEnabled) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('bl.cardEnabled'), style: TextStyle(color: p.muted)),
        ],
      ]),
    );
  }
}

class _ContactCard extends StatelessWidget {
  const _ContactCard({required this.account});
  final BlAccount account;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    String or(String value) => value.trim().isEmpty ? '-' : value;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.t('bl.contact')),
        const SizedBox(height: AnumSpacing.xs),
        _Field(l.t('bl.contactName'), or(account.contactName)),
        _Field(l.t('bl.contactEmail'), or(account.contactEmail)),
        _Field(
            l.t('bl.company'),
            or(account.companyName.isEmpty
                ? account.displayName
                : account.companyName)),
        _Field(l.t('bl.address'), or(account.address)),
        _Field(l.t('bl.vat'), or(account.vatNumber)),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('bl.contactWeb'),
            style:
                TextStyle(color: context.palette.faint, fontSize: AnumType.xs)),
      ]),
    );
  }
}

class _PlansTab extends StatelessWidget {
  const _PlansTab({required this.controller});
  final BillingController controller;

  Future<void> _choose(BuildContext context, BlPlan plan) async {
    final changed = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => PlanChangeSheet(controller: controller, plan: plan),
    );
    if (changed != null && context.mounted) _snack(context, changed);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final sub = controller.overview?.subscription;
    final canChange = sub != null && !sub.isCancelled;
    if (controller.plansError != null) {
      return AnumPage(children: [
        AnumEmpty(
            icon: Icons.error_outline,
            title: l.t('bl.plansErrorTitle'),
            message: l.f('bl.errorHint', {'message': controller.plansError!}),
            action: l.t('retry'),
            onAction: controller.reloadPlans)
      ]);
    }
    if (controller.plans.isEmpty) {
      return AnumPage(onRefresh: controller.load, children: [
        AnumEmpty(
            icon: Icons.inventory_2_outlined,
            title: l.t('bl.noPlansTitle'),
            message: l.t('bl.noPlans'),
            action: l.t('bl.checkAgain'),
            onAction: controller.reloadPlans)
      ]);
    }
    return AnumPage(onRefresh: controller.load, children: [
      Text(
          canChange
              ? l.t('bl.plansLead')
              : l.t(sub == null ? 'bl.plansNoSub' : 'bl.plansCancelled'),
          style: Theme.of(context).textTheme.bodyMedium),
      LayoutBuilder(builder: (context, box) {
        final columns = box.maxWidth >= 900
            ? 3
            : box.maxWidth >= AnumBreakpoints.compact
                ? 2
                : 1;
        const gap = AnumSpacing.md;
        final width = (box.maxWidth - gap * (columns - 1)) / columns;
        return Wrap(spacing: gap, runSpacing: gap, children: [
          for (final plan in controller.plans)
            SizedBox(
              width: width,
              child: _PlanCard(
                plan: plan,
                onChoose: canChange && !plan.current && !controller.busy
                    ? () => _choose(context, plan)
                    : null,
              ),
            ),
        ]);
      }),
    ]);
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.plan, required this.onChoose});
  final BlPlan plan;
  final VoidCallback? onChoose;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return AnumSurface(
      accent: plan.current ? p.sky : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(plan.name, style: Theme.of(context).textTheme.titleMedium),
              if (plan.current)
                AnumPill(label: l.t('bl.current'), tone: AnumTone.info),
            ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(
            _price(context, plan.priceMinor, plan.currency, plan.interval,
                plan.perSeat),
            style: const TextStyle(fontWeight: FontWeight.w700)),
        if (plan.description.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.xxs),
          Text(plan.description, style: TextStyle(color: p.muted)),
        ],
        if (plan.trialDays > 0)
          Text(l.f('bl.trialDays', {'days': plan.trialDays}),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        const SizedBox(height: AnumSpacing.xs),
        for (final entry in plan.limits.entries)
          Text(_limitText(l, entry.key, entry.value),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final feature in plan.features)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xxs),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.check, size: 16, color: p.ok),
              const SizedBox(width: AnumSpacing.xxs),
              Expanded(child: Text(feature, style: TextStyle(color: p.text))),
            ]),
          ),
        if (!plan.current) ...[
          const SizedBox(height: AnumSpacing.sm),
          OutlinedButton(
              onPressed: onChoose,
              child: Text(l.f('bl.choose', {'plan': plan.name}))),
        ],
      ]),
    );
  }
}

/// Previews a plan change (cost now or at period end, and any limit the
/// current usage is over) and confirms it. Pops with the success message.
class PlanChangeSheet extends StatefulWidget {
  const PlanChangeSheet(
      {required this.controller, required this.plan, super.key});
  final BillingController controller;
  final BlPlan plan;
  @override
  State<PlanChangeSheet> createState() => _PlanChangeSheetState();
}

class _PlanChangeSheetState extends State<PlanChangeSheet> {
  BlPreview? preview;
  String? error, failure;
  bool sending = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final value = await widget.controller.repository.preview(widget.plan.id);
      if (mounted) setState(() => preview = value);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.readable);
    } on Object catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> _confirm() async {
    final l = context.anum;
    setState(() {
      sending = true;
      failure = null;
    });
    final result = await widget.controller.change(widget.plan.id);
    if (!mounted) return;
    if (result == null) {
      Navigator.of(context).pop(l.f('bl.changed', {'plan': widget.plan.name}));
      return;
    }
    setState(() {
      sending = false;
      failure = result;
    });
    // The usage may have grown since the preview: show what is over now.
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final value = preview;
    final blocked = value?.blockedBy ?? const <BlMeter>[];
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsetsDirectional.fromSTEB(
            AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l.f('bl.changeTitle', {'plan': widget.plan.name}),
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: AnumSpacing.sm),
          if (error != null)
            AnumEmpty(
                icon: Icons.error_outline,
                title: l.t('bl.previewErrorTitle'),
                message: l.f('bl.errorHint', {'message': error!}),
                action: l.t('retry'),
                onAction: _load)
          else if (value == null)
            Center(
                child: Semantics(
                    label: l.t('bl.previewLoading'),
                    child: const CircularProgressIndicator()))
          else ...[
            Text(
                l.t(value.effective == 'now'
                    ? 'bl.effectiveNow'
                    : 'bl.effectivePeriodEnd'),
                style: TextStyle(color: p.muted)),
            const SizedBox(height: AnumSpacing.xs),
            _Field(l.t('bl.amount'),
                _money(context, value.amountMinor, value.currency)),
            _Field(
                l.t('bl.tax'), _money(context, value.taxMinor, value.currency)),
            if (value.creditMinor > 0)
              _Field(l.t('bl.creditAdded'),
                  _money(context, value.creditMinor, value.currency)),
            _Field(l.t('bl.totalNow'),
                _money(context, value.totalMinor, value.currency),
                strong: true),
            if (blocked.isNotEmpty) ...[
              const SizedBox(height: AnumSpacing.sm),
              _Banner(
                icon: Icons.block,
                tone: AnumTone.stop,
                title: l.t('bl.blockedTitle'),
                message: [
                  l.t('bl.blocked'),
                  for (final meter in blocked)
                    l.f('bl.blockedLine', {
                      'name': _meterName(l, meter.key),
                      'value': _meterValue(context, meter)
                    }),
                ].join('\n'),
              ),
            ],
          ],
          if (failure != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Semantics(
              liveRegion: true,
              child: Text(failure!,
                  style:
                      TextStyle(color: anumToneColor(context, AnumTone.stop))),
            ),
          ],
          const SizedBox(height: AnumSpacing.md),
          Wrap(
              alignment: WrapAlignment.end,
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                TextButton(
                    onPressed:
                        sending ? null : () => Navigator.of(context).pop(),
                    child: Text(l.t('bl.keep'))),
                FilledButton(
                    onPressed: value == null || blocked.isNotEmpty || sending
                        ? null
                        : _confirm,
                    child: Text(sending
                        ? l.t('bl.changing')
                        : l.f('bl.confirmChange', {'plan': widget.plan.name}))),
              ]),
        ]),
      ),
    );
  }
}

class _InvoicesTab extends StatefulWidget {
  const _InvoicesTab({required this.controller});
  final BillingController controller;
  @override
  State<_InvoicesTab> createState() => _InvoicesTabState();
}

class _InvoicesTabState extends State<_InvoicesTab> {
  BillingController get c => widget.controller;
  late final _search = TextEditingController(text: c.query);

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _submit() => c.searchInvoices(text: _search.text, withStatus: c.status);

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final filtered = c.query.trim().isNotEmpty || c.status != null;
    return AnumPage(
        onRefresh: () => c.searchInvoices(withStatus: c.status),
        children: [
          TextField(
            controller: _search,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => _submit(),
            decoration: InputDecoration(
              labelText: l.t('bl.search'),
              hintText: l.t('bl.searchHint'),
              prefixIcon: const Icon(Icons.search),
              suffixIcon: IconButton(
                  tooltip: l.t('bl.searchGo'),
                  onPressed: _submit,
                  icon: const Icon(Icons.arrow_forward)),
            ),
          ),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            for (final (value, label) in [
              (null, l.t('bl.filter.all')),
              ('open', l.t('bl.inv.open')),
              ('paid', l.t('bl.inv.paid')),
              ('void', l.t('bl.inv.void')),
            ])
              ChoiceChip(
                label: Text(label),
                selected: c.status == value,
                onSelected: (_) =>
                    c.searchInvoices(text: _search.text, withStatus: value),
              ),
          ]),
          if (c.invoicesLoading && c.invoices.isEmpty)
            Center(
                child: Semantics(
                    label: l.t('bl.invoicesLoading'),
                    child: const CircularProgressIndicator()))
          else if (c.invoicesError != null)
            AnumEmpty(
                icon: Icons.error_outline,
                title: l.t('bl.invoicesErrorTitle'),
                message: l.f('bl.errorHint', {'message': c.invoicesError!}),
                action: l.t('retry'),
                onAction: () => c.searchInvoices(withStatus: c.status))
          else if (c.invoices.isEmpty)
            filtered
                ? AnumEmpty(
                    icon: Icons.search_off,
                    title: l.t('bl.noMatchTitle'),
                    message: l.t('bl.noMatch'),
                    action: l.t('bl.clearFilters'),
                    onAction: () {
                      _search.clear();
                      c.searchInvoices(text: '', withStatus: null);
                    })
                : AnumEmpty(
                    icon: Icons.receipt_long_outlined,
                    title: l.t('bl.noInvoicesTitle'),
                    message: l.t('bl.noInvoices'))
          else
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              if (c.invoicesLoading) const LinearProgressIndicator(),
              for (final invoice in c.invoices)
                AnumRow(
                  icon: Icons.receipt_long_outlined,
                  tone: blInvoiceTone(invoice),
                  title:
                      invoice.number.isEmpty ? l.t('bl.draft') : invoice.number,
                  subtitle:
                      _money(context, invoice.totalMinor, invoice.currency),
                  detail: invoice.payable
                      ? l.f('bl.dueLine', {
                          'amount': _money(context, invoice.amountDueMinor,
                              invoice.currency),
                          'date': _date(context, invoice.dueAt)
                        })
                      : l.f('bl.issuedLine',
                          {'date': _date(context, invoice.issuedAt)}),
                  trailing: AnumPill(
                      label: blInvoiceStatus(l, invoice),
                      tone: blInvoiceTone(invoice)),
                  onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                          builder: (_) =>
                              InvoiceScreen(controller: c, invoice: invoice))),
                ),
              if (c.moreError != null)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
                  child: Text(l.f('bl.errorHint', {'message': c.moreError!}),
                      style: TextStyle(
                          color: anumToneColor(context, AnumTone.stop))),
                ),
              if (c.hasMoreInvoices)
                Center(
                  child: OutlinedButton(
                      onPressed: c.loadingMore ? null : c.moreInvoices,
                      child: Text(c.loadingMore
                          ? l.t('bl.invoicesLoading')
                          : l.t('bl.more'))),
                ),
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(
                    l.f('bl.count',
                        {'shown': c.invoices.length, 'total': c.invoiceTotal}),
                    textAlign: TextAlign.center,
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ),
            ]),
        ]);
  }
}

/// One invoice: who billed whom, its lines with VAT, payments, credit notes,
/// the bank details to pay it, its PDF and, when turned on, card payment.
class InvoiceScreen extends StatefulWidget {
  const InvoiceScreen(
      {required this.controller, required this.invoice, super.key});
  final BillingController controller;
  final BlInvoice invoice;
  @override
  State<InvoiceScreen> createState() => _InvoiceScreenState();
}

class _InvoiceScreenState extends State<InvoiceScreen> {
  BlInvoiceDetail? detail;
  String? error;
  bool saving = false, paying = false;

  BillingRepository get repository => widget.controller.repository;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => error = null);
    try {
      final value = await repository.invoice(widget.invoice.id);
      if (mounted) setState(() => detail = value);
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } on Object catch (e) {
      if (mounted) setState(() => error = '$e');
    }
  }

  Future<void> _pdf(BlInvoice invoice) async {
    final l = context.anum;
    setState(() => saving = true);
    String? message;
    try {
      message = await repository.savePdf(invoice) ? l.t('bl.pdfSaved') : null;
    } on ApiException catch (e) {
      message = l.f('bl.pdfFailed', {'message': e.message});
    } on Object catch (e) {
      message = l.f('bl.pdfFailed', {'message': '$e'});
    }
    if (!mounted) return;
    setState(() => saving = false);
    if (message != null) _snack(context, message);
  }

  Future<void> _pay(BlInvoice invoice) async {
    final l = context.anum;
    setState(() => paying = true);
    String? message;
    try {
      final url = await repository.checkout(invoice.id);
      if (!await widget.controller.openUrl(url)) {
        message = l.t('bl.payOpenFailed');
      }
    } on ApiException catch (e) {
      message = l.f('bl.payFailed', {'message': e.message});
    } on Object catch (e) {
      message = l.f('bl.payFailed', {'message': '$e'});
    }
    if (!mounted) return;
    setState(() => paying = false);
    if (message != null) _snack(context, message);
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final value = detail;
    final title = widget.invoice.number.isEmpty
        ? l.t('bl.draft')
        : l.f('bl.invoiceTitle', {'number': widget.invoice.number});
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: error != null
          ? AnumPage(children: [
              AnumEmpty(
                  icon: Icons.error_outline,
                  title: l.t('bl.invoiceErrorTitle'),
                  message: l.f('bl.errorHint', {'message': error!}),
                  action: l.t('retry'),
                  onAction: _load)
            ])
          : value == null
              ? Center(
                  child: Semantics(
                      label: l.t('bl.invoiceLoading'),
                      child: const CircularProgressIndicator()))
              : _body(context, value),
    );
  }

  Widget _body(BuildContext context, BlInvoiceDetail value) {
    final l = context.anum;
    final p = context.palette;
    final inv = value.summary;
    String money(int minor) => _money(context, minor, inv.currency);
    final cardEnabled = widget.controller.overview?.cardEnabled ?? false;
    final header = AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(inv.number.isEmpty ? l.t('bl.draft') : inv.number,
                  style: Theme.of(context).textTheme.titleLarge),
              AnumPill(
                  label: blInvoiceStatus(l, inv), tone: blInvoiceTone(inv)),
            ]),
        const SizedBox(height: AnumSpacing.xs),
        _Field(l.t('bl.issued'), _date(context, inv.issuedAt)),
        _Field(l.t('bl.due'), _date(context, inv.dueAt)),
        if (inv.periodStart != null || inv.periodEnd != null)
          _Field(
              l.t('bl.period'),
              l.f('bl.periodValue', {
                'from': _date(context, inv.periodStart),
                'to': _date(context, inv.periodEnd)
              })),
        _Field(l.t('bl.amountDue'), money(inv.amountDueMinor), strong: true),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          OutlinedButton.icon(
              onPressed: saving ? null : () => _pdf(inv),
              icon: const Icon(Icons.picture_as_pdf_outlined),
              label: Text(saving ? l.t('bl.pdfSaving') : l.t('bl.pdf'))),
          if (cardEnabled && inv.payable)
            FilledButton.icon(
                onPressed: paying ? null : () => _pay(inv),
                icon: const Icon(Icons.credit_card),
                label: Text(paying ? l.t('bl.payOpening') : l.t('bl.payCard'))),
        ]),
      ]),
    );
    final parties = AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.t('bl.billTo')),
        const SizedBox(height: AnumSpacing.xxs),
        for (final key in const [
          'company_name',
          'display_name',
          'contact_name',
          'contact_email',
          'address_line1',
          'address_line2',
          'city',
          'postal_code',
          'country',
        ])
          if (value.billTo[key] != null &&
              !(key == 'display_name' && value.billTo['company_name'] != null))
            Text(value.billTo[key]!),
        if (value.billTo['vat_number'] != null)
          Text(l.f('bl.vatLine', {'vat': value.billTo['vat_number']!})),
        const SizedBox(height: AnumSpacing.sm),
        _SectionTitle(l.t('bl.from')),
        const SizedBox(height: AnumSpacing.xxs),
        for (final entry in value.issuer.entries)
          if (entry.key != 'issuer_vat_number')
            Text(entry.value)
          else
            Text(l.f('bl.vatLine', {'vat': entry.value})),
      ]),
    );
    final lines = AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.t('bl.lines')),
        const SizedBox(height: AnumSpacing.xxs),
        if (value.lines.isEmpty)
          Text(l.t('bl.noLines'), style: TextStyle(color: p.muted)),
        for (final line in value.lines)
          _AmountRow(
            title: line.description.isEmpty ? '-' : line.description,
            subtitle: l.f('bl.lineQty',
                {'quantity': line.quantity, 'unit': money(line.unitMinor)}),
            amount: money(line.amountMinor),
          ),
        const Divider(),
        _Field(l.t('bl.subtotal'), money(inv.subtotalMinor)),
        _Field(
            inv.taxCountry.isEmpty
                ? l.f('bl.vatRate', {'rate': blRate(inv.taxRateBp)})
                : l.f('bl.vatRateCountry',
                    {'rate': blRate(inv.taxRateBp), 'country': inv.taxCountry}),
            money(inv.taxMinor)),
        _Field(l.t('bl.total'), money(inv.totalMinor), strong: true),
        if (inv.amountPaidMinor > 0)
          _Field(l.t('bl.paid'), money(inv.amountPaidMinor)),
        if (inv.amountCreditedMinor > 0)
          _Field(l.t('bl.credited'), money(inv.amountCreditedMinor)),
        _Field(l.t('bl.amountDue'), money(inv.amountDueMinor), strong: true),
      ]),
    );
    final payments = AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        _SectionTitle(l.t('bl.payments')),
        const SizedBox(height: AnumSpacing.xxs),
        if (value.payments.isEmpty)
          Text(l.t('bl.noPayments'), style: TextStyle(color: p.muted)),
        for (final payment in value.payments)
          AnumRow(
            title: money(payment.amountMinor),
            subtitle: [
              _methodName(l, payment.method),
              _date(context, payment.receivedAt),
              if (payment.reference.isNotEmpty) payment.reference,
            ].join(' · '),
            detail: payment.hasProof ? l.t('bl.hasProof') : null,
          ),
        if (value.creditNotes.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          _SectionTitle(l.t('bl.creditNotes')),
          for (final note in value.creditNotes)
            _AmountRow(
              title: note.number.isEmpty ? '-' : note.number,
              subtitle: [
                _date(context, note.issuedAt),
                if (note.reason.isNotEmpty) note.reason,
              ].join(' · '),
              amount: money(note.totalMinor),
            ),
        ],
      ]),
    );
    final bank = inv.payable && value.bank.enabled
        ? AnumSurface(
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _SectionTitle(l.t('bl.bankTransfer')),
                  _BankDetails(bank: value.bank),
                  Text(
                      l.f('bl.referenceNumber',
                          {'number': inv.number.isEmpty ? '-' : inv.number}),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                ]),
          )
        : null;
    return AnumPage(onRefresh: _load, children: [
      if (inv.overdue)
        _Banner(
            icon: Icons.warning_amber,
            tone: AnumTone.stop,
            title: l.t('bl.overdueTitle'),
            message: l.f('bl.invoiceOverdue', {
              'amount': money(inv.amountDueMinor),
              'date': _date(context, inv.dueAt)
            })),
      _Columns(start: [
        header,
        lines,
      ], end: [
        if (bank != null) bank,
        parties,
        payments,
      ]),
    ]);
  }
}

/// An invoice line or credit note: the amount shares the line with the
/// details when it fits and moves below them at large text sizes.
class _AmountRow extends StatelessWidget {
  const _AmountRow(
      {required this.title, required this.subtitle, required this.amount});
  final String title, subtitle, amount;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(title,
            style: TextStyle(color: p.text, fontWeight: FontWeight.w600)),
        Wrap(
          alignment: WrapAlignment.spaceBetween,
          spacing: AnumSpacing.xs,
          children: [
            Text(subtitle,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            Text(amount,
                style: TextStyle(color: p.text, fontWeight: FontWeight.w600)),
          ],
        ),
      ]),
    );
  }
}

String _methodName(AnumLocalizations l, String method) {
  const known = {'bank_transfer', 'cash', 'card', 'stripe', 'other'};
  return l.t('bl.method.${known.contains(method) ? method : 'other'}');
}
