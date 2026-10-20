import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'dart:math' show Random;

import 'package:clock/clock.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import '../business/invoice_document.dart';
import '../business/invoice_models.dart' show dayOfDate;
import 'portal_controller.dart';
import 'portal_link.dart';
import 'portal_models.dart';

/// The customer portal on the phone (docs/business-portal.md): a customer pastes
/// the link a business sent, then reads their offers, change orders, invoices
/// and requests and approves or declines an offer or a change order. It is not
/// a ANUM sign-in and gives no workspace role. The token stays in memory and
/// is forgotten when the customer leaves.

typedef PortalSessionFactory = PortalController Function(String token);

String _money(BuildContext context, double? v, String currency) =>
    formatMoney(v, currency, locale: context.anum.locale);

String _signedMoney(BuildContext context, double? v, String currency) {
  if (v == null) return '-';
  final text = _money(context, v.abs(), currency);
  return v > 0 ? '+$text' : (v < 0 ? '-$text' : text);
}

String _qty(double v) => v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v';

/// A sent invoice that still has a balance.
bool _unsettled(PortalInvoice i) =>
    i.status == 'sent' &&
    i.paymentState != 'paid' &&
    i.paymentState != 'credited';

AnumTone _invoiceTone(String status) => switch (status) {
      'paid' => AnumTone.ok,
      'sent' => AnumTone.info,
      'void' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

/// Lets the sign-in screen open the customer portal without the screens in
/// between knowing about it: the app provides how, the sign-in asks.
class PortalEntryScope extends InheritedWidget {
  const PortalEntryScope({required this.open, required super.child, super.key});
  final VoidCallback open;

  static VoidCallback? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<PortalEntryScope>()?.open;

  @override
  bool updateShouldNotify(PortalEntryScope oldWidget) => open != oldWidget.open;
}

/// The sign-in screen with a "Customer portal" entry under it, for a customer
/// who was sent a link rather than an account. Without [onOpen] the child is
/// shown as it is.
class PortalEntryBar extends StatelessWidget {
  const PortalEntryBar({required this.child, this.onOpen, super.key});
  final Widget child;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final open = onOpen ?? PortalEntryScope.maybeOf(context);
    if (open == null) return child;
    final l = AnumLocalizations.maybeOf(context);
    return Column(
      children: [
        Expanded(
          child: MediaQuery.removePadding(
              context: context, removeBottom: true, child: child),
        ),
        Material(
          color: Theme.of(context).colorScheme.surface,
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: TextButton.icon(
                key: const Key('open-portal'),
                onPressed: open,
                icon: const Icon(Icons.storefront_outlined),
                label:
                    Text(l.t('portal.signIn.cta'), textAlign: TextAlign.center),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// "Customer portal" entry: paste the link or the access code.
class PortalEntryScreen extends StatefulWidget {
  const PortalEntryScreen({
    required this.createSession,
    this.saver = const FilePickerInvoiceSaver(),
    super.key,
  });
  final PortalSessionFactory createSession;
  final InvoiceDocumentSaver saver;

  @override
  State<PortalEntryScreen> createState() => _PortalEntryState();
}

class _PortalEntryState extends State<PortalEntryScreen> {
  final link = TextEditingController();
  String? problem;

  @override
  void dispose() {
    link.clear();
    link.dispose();
    super.dispose();
  }

  Future<void> _paste() async {
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    if (data?.text != null && mounted) {
      setState(() {
        link.text = data!.text!;
        problem = null;
      });
    }
  }

  Future<void> _open() async {
    final l = context.anum;
    final result = parsePortalLink(link.text);
    switch (result) {
      case PortalLinkInvalid(:final reason):
        setState(() => problem = l.t('portal.link.$reason'));
      case PortalLinkOk(:final token):
        setState(() => problem = null);
        final session = widget.createSession(token);
        // The pasted text is dropped as soon as it has been read.
        link.clear();
        // The home screen owns the session: leaving it forgets the token.
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) =>
                PortalHomeScreen(controller: session, saver: widget.saver),
          ),
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('portal.title'))),
      body: AnumPage(
        children: [
          AnumHeader(
              eyebrow: l.t('portal.eyebrow'),
              title: l.t('portal.entry.title'),
              subtitle: l.t('portal.entry.intro')),
          TextField(
            key: const Key('portal-link-field'),
            controller: link,
            minLines: 2,
            maxLines: 5,
            keyboardType: TextInputType.url,
            autocorrect: false,
            enableSuggestions: false,
            onChanged: (_) {
              if (problem != null) setState(() => problem = null);
            },
            decoration: InputDecoration(
              labelText: l.t('portal.entry.field'),
              hintText: 'https://.../portal#access=...',
              errorText: problem,
              errorMaxLines: 4,
            ),
          ),
          Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton.icon(
              key: const Key('portal-open'),
              onPressed: _open,
              icon: const Icon(Icons.login),
              label: Text(l.t('portal.entry.open')),
            ),
            OutlinedButton.icon(
              key: const Key('portal-paste'),
              onPressed: _paste,
              icon: const Icon(Icons.content_paste),
              label: Text(l.t('portal.entry.paste')),
            ),
          ]),
          Text(l.t('portal.entry.privacy'),
              style: TextStyle(color: context.palette.muted)),
        ],
      ),
    );
  }
}

/// A refused link: the API's sentence, no retry (it never starts working
/// again), and a way back.
class PortalLinkProblem extends StatelessWidget {
  const PortalLinkProblem({required this.message, super.key});
  final String message;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('portal.title'))),
      body: AnumPage(
        children: [
          AnumFeedback(
              key: const Key('portal-link-problem'),
              kind: AnumFeedbackKind.permission,
              message: message),
          Text(l.t('portal.problem.hint'),
              textAlign: TextAlign.center,
              style: TextStyle(color: context.palette.muted)),
          Center(
            child: OutlinedButton(
              key: const Key('portal-problem-back'),
              onPressed: () => Navigator.of(context).maybePop(),
              child: Text(l.t('portal.back')),
            ),
          ),
        ],
      ),
    );
  }
}

/// The portal's overview with counts.
class PortalHomeScreen extends StatefulWidget {
  const PortalHomeScreen(
      {required this.controller,
      this.saver = const FilePickerInvoiceSaver(),
      super.key});
  final PortalController controller;
  final InvoiceDocumentSaver saver;

  @override
  State<PortalHomeScreen> createState() => _PortalHomeState();
}

class _PortalHomeState extends State<PortalHomeScreen> {
  PortalController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) c.open();
    });
  }

  @override
  void dispose() {
    // Leaving the portal forgets the link.
    c.leave(notify: false);
    c.dispose();
    super.dispose();
  }

  void _go(Widget screen) => Navigator.of(context)
      .push(MaterialPageRoute<void>(builder: (_) => screen));

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final problem = c.linkProblem;
        if (problem != null) return PortalLinkProblem(message: problem);
        final me = c.me;
        return Scaffold(
          appBar: AppBar(
            title: Text(l.t('portal.title')),
            actions: [
              IconButton(
                key: const Key('portal-leave'),
                tooltip: l.t('portal.leave'),
                onPressed: () => Navigator.of(context).maybePop(),
                icon: const Icon(Icons.logout),
              ),
            ],
          ),
          body: AnumPage(
            onRefresh: c.open,
            children: [
              if (c.state == PortalLoad.idle || c.state == PortalLoad.loading)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (c.state == PortalLoad.error)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: l.f('portal.failed', {'message': c.error ?? ''}),
                    onRetry: c.open),
              if (me != null && c.state == PortalLoad.ready) ..._ready(me),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _ready(PortalMe me) {
    final l = context.anum;
    final counts = me.counts;
    return [
      AnumHeader(
        eyebrow:
            me.businessName.isEmpty ? l.t('portal.eyebrow') : me.businessName,
        title: me.accountName,
        subtitle: l.f('portal.hello', {'name': me.contactName}),
      ),
      if (!me.canDecide)
        AnumSurface(
          accent: context.palette.warn,
          child:
              Text(l.t('portal.readOnly'), key: const Key('portal-read-only')),
        ),
      Text(l.f('portal.expires', {'date': dayOfDate(me.expiresAt)}),
          style: TextStyle(color: context.palette.muted)),
      AnumSurface(
        key: const Key('portal-go-offers'),
        onTap: () => _go(PortalOffersScreen(controller: c)),
        child: PillRow(
          title: l.t('portal.offers'),
          subtitle: counts.offersWaiting == 0
              ? l.t('portal.offers.none')
              : l.f('portal.offers.waiting', {'count': counts.offersWaiting}),
          icon: Icons.description_outlined,
          pills: counts.offersWaiting == 0
              ? const []
              : [
                  AnumPill(
                      label: '${counts.offersWaiting}', tone: AnumTone.warn)
                ],
        ),
      ),
      AnumSurface(
        key: const Key('portal-go-changes'),
        onTap: () => _go(PortalChangesScreen(controller: c)),
        child: PillRow(
          title: l.t('portal.changes'),
          subtitle: counts.changeOrdersWaiting == 0
              ? l.t('portal.changes.none')
              : l.f('portal.changes.waiting',
                  {'count': counts.changeOrdersWaiting}),
          icon: Icons.difference_outlined,
          pills: counts.changeOrdersWaiting == 0
              ? const []
              : [
                  AnumPill(
                      label: '${counts.changeOrdersWaiting}',
                      tone: AnumTone.warn)
                ],
        ),
      ),
      AnumSurface(
        key: const Key('portal-go-invoices'),
        onTap: () =>
            _go(PortalInvoicesScreen(controller: c, saver: widget.saver)),
        child: PillRow(
          title: l.t('portal.invoices'),
          subtitle: counts.invoicesOpen == 0
              ? l.t('portal.invoices.none')
              : l.f('portal.invoices.open', {
                  'count': counts.invoicesOpen,
                  'overdue': counts.invoicesOverdue,
                }),
          icon: Icons.receipt_long_outlined,
          pills: counts.invoicesOverdue == 0
              ? const []
              : [
                  AnumPill(
                      label: l.f('portal.invoices.overdueCount',
                          {'count': counts.invoicesOverdue}),
                      tone: AnumTone.stop)
                ],
        ),
      ),
      AnumSurface(
        key: const Key('portal-go-tickets'),
        onTap: () => _go(PortalTicketsScreen(controller: c)),
        child: PillRow(
          title: l.t('portal.tickets'),
          subtitle: counts.ticketsOpen == 0
              ? l.t('portal.tickets.none')
              : l.f('portal.tickets.open', {'count': counts.ticketsOpen}),
          icon: Icons.support_agent_outlined,
          pills: counts.ticketsOpen == 0
              ? const []
              : [AnumPill(label: '${counts.ticketsOpen}')],
        ),
      ),
    ];
  }
}

/// A list screen of the portal with every state: loading, empty, error with
/// retry, and the link problem.
class _PortalList<T> extends StatefulWidget {
  const _PortalList({
    required this.controller,
    required this.title,
    required this.section,
    required this.load,
    required this.row,
    required this.emptyTitle,
    required this.emptyText,
    this.fab,
  });
  final PortalController controller;
  final String title, emptyTitle, emptyText;
  final PortalSection<T> section;
  final Future<void> Function() load;
  final Widget Function(BuildContext context, T item) row;
  final Widget? fab;

  @override
  State<_PortalList<T>> createState() => _PortalListState<T>();
}

class _PortalListState<T> extends State<_PortalList<T>> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) widget.load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final problem = widget.controller.linkProblem;
        if (problem != null) return PortalLinkProblem(message: problem);
        final s = widget.section;
        return Scaffold(
          appBar: AppBar(title: Text(widget.title)),
          floatingActionButton: widget.fab,
          body: AnumPage(
            onRefresh: widget.load,
            children: [
              if (s.state == PortalLoad.idle || s.state == PortalLoad.loading)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (s.state == PortalLoad.error)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: l.f('portal.failed', {'message': s.error ?? ''}),
                    onRetry: widget.load),
              if (s.state == PortalLoad.ready && s.items.isEmpty)
                AnumEmpty(
                    title: widget.emptyTitle,
                    message: widget.emptyText,
                    icon: Icons.inbox_outlined),
              if (s.state == PortalLoad.ready)
                for (final item in s.items) widget.row(context, item),
            ],
          ),
        );
      },
    );
  }
}

/// Loads one record and shows it, with the same states as a list.
mixin _DetailLoader<W extends StatefulWidget, R> on State<W> {
  PortalController get controller;
  Future<R> fetch();
  R? record;
  String? loadError;
  String? notice;
  String? problem;
  bool busy = false;

  Future<void> load() async {
    try {
      final next = await fetch();
      if (mounted) {
        setState(() {
          record = next;
          loadError = null;
        });
      }
    } catch (e) {
      controller.noteError(e);
      if (mounted) setState(() => loadError = PortalController.messageOf(e));
    }
  }

  @override
  void initState() {
    super.initState();
    load();
  }
}

// -- offers ------------------------------------------------------------------

class PortalOffersScreen extends StatelessWidget {
  const PortalOffersScreen({required this.controller, super.key});
  final PortalController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return _PortalList<PortalOfferSummary>(
      controller: controller,
      title: l.t('portal.offers'),
      section: controller.offers,
      load: controller.loadOffers,
      emptyTitle: l.t('portal.offers.emptyTitle'),
      emptyText: l.t('portal.offers.emptyText'),
      row: (context, o) => AnumSurface(
        key: Key('portal-offer-${o.id}'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) =>
                PortalOfferScreen(controller: controller, offerId: o.id))),
        child: PillRow(
          title: o.title,
          subtitle: [
            l.f('portal.version', {'version': o.version}),
            if (o.validUntil != null)
              l.f('portal.validUntil', {'date': dayPart(o.validUntil)}),
          ].join(' · '),
          detail: _money(context, o.total, o.currency),
          icon: Icons.description_outlined,
          pills: [
            AnumPill(
              label: _offerState(l, o),
              tone: o.decision != null
                  ? (o.decision!.approved ? AnumTone.ok : AnumTone.neutral)
                  : o.expired
                      ? AnumTone.stop
                      : o.waiting
                          ? AnumTone.warn
                          : AnumTone.ok,
            ),
          ],
        ),
      ),
    );
  }
}

String _offerState(AnumLocalizations l, PortalOfferSummary o) {
  if (o.decision != null) return l.t('portal.decision.${o.decision!.decision}');
  if (!o.waiting) return l.t('portal.offer.accepted');
  if (o.expired) return l.t('portal.offer.expired');
  return l.t('portal.offer.waiting');
}

/// Asks for the typed name and an optional note before a decision, stating
/// exactly what is being approved and for how much.
class PortalDecisionDialog extends StatefulWidget {
  const PortalDecisionDialog({
    required this.approve,
    required this.what,
    required this.subject,
    required this.amount,
    super.key,
  });

  /// True to approve, false to decline.
  final bool approve;

  /// `offer` or `change`.
  final String what;
  final String subject, amount;

  @override
  State<PortalDecisionDialog> createState() => _PortalDecisionState();
}

class _PortalDecisionState extends State<PortalDecisionDialog> {
  final name = TextEditingController();
  final note = TextEditingController();
  String? problem;

  @override
  void dispose() {
    name.dispose();
    note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final verb = widget.approve ? 'approve' : 'decline';
    return AlertDialog(
      title: Text(l.t('portal.confirm.${widget.what}.$verb')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              l.f('portal.confirm.${widget.what}.$verb.body',
                  {'subject': widget.subject, 'amount': widget.amount}),
              key: const Key('portal-confirm-body'),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-signer'),
              controller: name,
              textCapitalization: TextCapitalization.words,
              decoration: InputDecoration(
                  labelText: l.t('portal.confirm.name'),
                  helperText: l.t('portal.confirm.nameHint'),
                  errorText: problem),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-note'),
              controller: note,
              maxLength: 500,
              minLines: 1,
              maxLines: 4,
              decoration:
                  InputDecoration(labelText: l.t('portal.confirm.note')),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('portal-confirm'),
          onPressed: () {
            final typed = name.text.trim().replaceAll(RegExp(r'\s+'), ' ');
            if (typed.length < 2) {
              setState(() => problem = l.t('portal.confirm.nameNeeded'));
              return;
            }
            Navigator.pop(context, (name: typed, note: note.text.trim()));
          },
          child: Text(l.t('portal.confirm.$verb')),
        ),
      ],
    );
  }
}

class _Bullets extends StatelessWidget {
  const _Bullets(this.title, this.items);
  final String title;
  final List<String> items;
  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(title, style: Theme.of(context).textTheme.titleSmall),
        for (final item in items) Text('• $item'),
      ],
    );
  }
}

class _DecidedLine extends StatelessWidget {
  const _DecidedLine(this.decision);
  final PortalDecisionView decision;
  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AnumSurface(
      key: const Key('portal-decided'),
      accent: decision.approved ? context.palette.ok : context.palette.warn,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
              l.f('portal.decided.${decision.decision}', {
                'name': decision.signerName.isEmpty ? '-' : decision.signerName,
                'date': dayOfDate(decision.decidedAt),
              }),
              style: const TextStyle(fontWeight: FontWeight.w700)),
          if (decision.note.isNotEmpty)
            Text(l.f('portal.decided.note', {'note': decision.note})),
          Text(l.t('portal.decided.hint'),
              style: TextStyle(color: context.palette.muted)),
        ],
      ),
    );
  }
}

class PortalOfferScreen extends StatefulWidget {
  const PortalOfferScreen(
      {required this.controller, required this.offerId, super.key});
  final PortalController controller;
  final String offerId;

  @override
  State<PortalOfferScreen> createState() => _PortalOfferState();
}

class _PortalOfferState extends State<PortalOfferScreen>
    with _DetailLoader<PortalOfferScreen, PortalOffer> {
  @override
  PortalController get controller => widget.controller;
  @override
  Future<PortalOffer> fetch() => controller.repository.offer(widget.offerId);

  Future<void> _decide(PortalOffer o, bool approve) async {
    final l = context.anum;
    final answer = await showDialog<({String name, String note})>(
      context: context,
      builder: (_) => PortalDecisionDialog(
        approve: approve,
        what: 'offer',
        subject: o.title,
        amount: _money(context, o.total, o.currency),
      ),
    );
    if (answer == null || !mounted) return;
    setState(() {
      busy = true;
      problem = null;
      notice = null;
    });
    try {
      final next = await controller.decideOffer(
          o, approve ? 'approved' : 'declined', answer.name, answer.note);
      if (mounted) {
        setState(() {
          record = next;
          notice = l.t('portal.decision.recorded');
        });
      }
    } on ApiException catch (e) {
      if (controller.noteError(e)) return;
      // 409: the offer changed after it was opened. Show the latest version.
      if (e.statusCode == 409) await load();
      if (mounted) {
        setState(() => problem = e.statusCode == 409
            ? '${e.message} ${l.t('portal.reloaded')}'
            : e.readable);
      }
    } catch (e) {
      if (mounted) setState(() => problem = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final linkIssue = controller.linkProblem;
        if (linkIssue != null) return PortalLinkProblem(message: linkIssue);
        final o = record;
        return Scaffold(
          appBar: AppBar(title: Text(o?.title ?? l.t('portal.offers'))),
          body: AnumPage(
            onRefresh: load,
            children: [
              if (o == null && loadError == null)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (o == null && loadError != null)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: loadError!,
                    onRetry: load),
              if (o != null) ..._body(context, o),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(BuildContext context, PortalOffer o) {
    final l = context.anum;
    String m(double v) => _money(context, v, o.currency);
    return [
      AnumHeader(
        eyebrow: _offerState(l, o),
        title: o.title,
        subtitle: [
          l.f('portal.version', {'version': o.version}),
          if (o.validUntil != null)
            l.f('portal.validUntil', {'date': dayPart(o.validUntil)}),
        ].join(' · '),
      ),
      if (notice != null)
        Text(notice!,
            key: const Key('portal-notice'),
            style: TextStyle(color: context.palette.ok)),
      if (problem != null)
        Text(problem!,
            key: const Key('portal-problem'),
            style: TextStyle(color: context.palette.stop)),
      if (o.decision != null) _DecidedLine(o.decision!),
      _decisionArea(context, o),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (o.summary.isNotEmpty) Text(o.summary),
            if (o.scope.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(l.t('portal.offer.scope'),
                  style: Theme.of(context).textTheme.titleSmall),
              Text(o.scope),
            ],
            const SizedBox(height: 8),
            _Bullets(l.t('portal.offer.deliverables'), o.deliverables),
            _Bullets(l.t('portal.offer.exclusions'), o.exclusions),
            _Bullets(l.t('portal.offer.assumptions'), o.assumptions),
            _Bullets(l.t('portal.offer.criteria'), o.acceptanceCriteria),
          ],
        ),
      ),
      if (o.lines.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('portal.lines')),
              for (final line in o.lines)
                AnumRow(
                  title: line.description,
                  subtitle: '${_qty(line.quantity)} x ${m(line.unitPrice)}',
                  detail: m(line.amount),
                ),
            ],
          ),
        ),
      if (o.milestones.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('portal.milestones')),
              for (final x in o.milestones)
                AnumRow(
                  title: x.title,
                  subtitle: [
                    if (x.dueInDays != null)
                      l.f('portal.dueInDays', {'days': x.dueInDays!}),
                    if (x.acceptance.isNotEmpty) x.acceptance,
                  ].join(' · '),
                  detail: m(x.amount),
                ),
            ],
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumRow(title: l.t('biz.inv.doc.subtotal'), detail: m(o.subtotal)),
            AnumRow(
                title: '${l.t('biz.inv.doc.tax')} ${_qty(o.taxRatePct)}%',
                detail: m(o.tax),
                subtitle: o.taxNote.isEmpty ? null : o.taxNote),
            AnumRow(title: l.t('biz.inv.doc.total'), detail: m(o.total)),
            if (o.paymentTerms.isNotEmpty)
              AnumRow(
                  title: l.t('portal.paymentTerms'), subtitle: o.paymentTerms),
          ],
        ),
      ),
    ];
  }

  Widget _decisionArea(BuildContext context, PortalOffer o) {
    final l = context.anum;
    if (o.decision != null) return const SizedBox.shrink();
    if (!o.waiting) {
      return Text(l.t('portal.offer.alreadyAccepted'),
          key: const Key('portal-state'));
    }
    if (o.expired) {
      return Text(
          l.f('portal.offer.expiredBody', {'date': dayPart(o.validUntil)}),
          key: const Key('portal-state'),
          style: TextStyle(color: context.palette.stop));
    }
    if (!controller.canDecide || !o.canDecide) {
      return Text(l.t('portal.readOnlyBody'), key: const Key('portal-state'));
    }
    return Wrap(spacing: 8, runSpacing: 8, children: [
      FilledButton.icon(
        key: const Key('portal-approve'),
        onPressed: busy ? null : () => _decide(o, true),
        icon: const Icon(Icons.check),
        label: Text(l.t('portal.confirm.approve')),
      ),
      OutlinedButton.icon(
        key: const Key('portal-decline'),
        onPressed: busy ? null : () => _decide(o, false),
        icon: const Icon(Icons.close),
        label: Text(l.t('portal.confirm.decline')),
      ),
    ]);
  }
}

// -- change orders -------------------------------------------------------------

class PortalChangesScreen extends StatelessWidget {
  const PortalChangesScreen({required this.controller, super.key});
  final PortalController controller;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return _PortalList<PortalChangeSummary>(
      controller: controller,
      title: l.t('portal.changes'),
      section: controller.changes,
      load: controller.loadChanges,
      emptyTitle: l.t('portal.changes.emptyTitle'),
      emptyText: l.t('portal.changes.emptyText'),
      row: (context, c) => AnumSurface(
        key: Key('portal-change-${c.id}'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) =>
                PortalChangeScreen(controller: controller, changeId: c.id))),
        child: PillRow(
          title: l.f(
              'portal.changeHeading', {'number': c.number, 'title': c.title}),
          subtitle: c.projectName,
          detail: l.f('portal.changeImpact', {
            'price': _signedMoney(context, c.totalDelta, c.currency),
            'days': c.timelineDays,
          }),
          icon: Icons.difference_outlined,
          pills: [
            AnumPill(
              label: _changeState(l, c),
              tone: c.decision != null
                  ? (c.decision!.approved ? AnumTone.ok : AnumTone.neutral)
                  : c.stale
                      ? AnumTone.stop
                      : c.waiting
                          ? AnumTone.warn
                          : AnumTone.ok,
            ),
          ],
        ),
      ),
    );
  }
}

String _changeState(AnumLocalizations l, PortalChangeSummary c) {
  if (c.decision != null) return l.t('portal.decision.${c.decision!.decision}');
  if (!c.waiting) return l.t('portal.change.approved');
  if (c.stale) return l.t('portal.change.stale');
  return l.t('portal.change.waiting');
}

class PortalChangeScreen extends StatefulWidget {
  const PortalChangeScreen(
      {required this.controller, required this.changeId, super.key});
  final PortalController controller;
  final String changeId;

  @override
  State<PortalChangeScreen> createState() => _PortalChangeState();
}

class _PortalChangeState extends State<PortalChangeScreen>
    with _DetailLoader<PortalChangeScreen, PortalChange> {
  @override
  PortalController get controller => widget.controller;
  @override
  Future<PortalChange> fetch() =>
      controller.repository.changeOrder(widget.changeId);

  Future<void> _decide(PortalChange o, bool approve) async {
    final l = context.anum;
    final answer = await showDialog<({String name, String note})>(
      context: context,
      builder: (_) => PortalDecisionDialog(
        approve: approve,
        what: 'change',
        subject:
            l.f('portal.changeHeading', {'number': o.number, 'title': o.title}),
        amount: _signedMoney(context, o.totalDelta, o.currency),
      ),
    );
    if (answer == null || !mounted) return;
    setState(() {
      busy = true;
      problem = null;
      notice = null;
    });
    try {
      final next = await controller.decideChange(
          o, approve ? 'approved' : 'declined', answer.name, answer.note);
      if (mounted) {
        setState(() {
          record = next;
          notice = l.t('portal.decision.recorded');
        });
      }
    } on ApiException catch (e) {
      if (controller.noteError(e)) return;
      if (e.statusCode == 409) await load();
      if (mounted) {
        setState(() => problem = e.statusCode == 409
            ? '${e.message} ${l.t('portal.reloaded')}'
            : e.readable);
      }
    } catch (e) {
      if (mounted) setState(() => problem = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final linkIssue = controller.linkProblem;
        if (linkIssue != null) return PortalLinkProblem(message: linkIssue);
        final o = record;
        return Scaffold(
          appBar: AppBar(title: Text(l.t('portal.changes'))),
          body: AnumPage(
            onRefresh: load,
            children: [
              if (o == null && loadError == null)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (o == null && loadError != null)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: loadError!,
                    onRetry: load),
              if (o != null) ..._body(context, o),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(BuildContext context, PortalChange o) {
    final l = context.anum;
    String m(double v) => _money(context, v, o.currency);
    return [
      AnumHeader(
        eyebrow: _changeState(l, o),
        title:
            l.f('portal.changeHeading', {'number': o.number, 'title': o.title}),
        subtitle: o.projectName,
      ),
      if (notice != null)
        Text(notice!,
            key: const Key('portal-notice'),
            style: TextStyle(color: context.palette.ok)),
      if (problem != null)
        Text(problem!,
            key: const Key('portal-problem'),
            style: TextStyle(color: context.palette.stop)),
      if (o.decision != null) _DecidedLine(o.decision!),
      _decisionArea(context, o),
      if (o.request.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('portal.change.request'),
                  style: Theme.of(context).textTheme.titleSmall),
              Text(o.request),
            ],
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('portal.lines')),
            for (final line in o.lines)
              AnumRow(
                title: line.description,
                subtitle:
                    '${l.t('portal.change.${line.action ?? 'add'}')}, ${_qty(line.quantity)} x ${m(line.unitPrice)}',
                detail: m(line.amount),
              ),
          ],
        ),
      ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumRow(
                title: l.t('biz.inv.doc.subtotal'),
                detail: _signedMoney(context, o.subtotalDelta, o.currency)),
            AnumRow(
                title: l.t('biz.inv.doc.tax'),
                detail: _signedMoney(context, o.taxDelta, o.currency)),
            AnumRow(
                title: l.t('portal.change.price'),
                detail: _signedMoney(context, o.totalDelta, o.currency)),
            AnumRow(
                title: l.t('portal.change.timeline'),
                detail: l.f('portal.change.days', {'days': o.timelineDays})),
          ],
        ),
      ),
    ];
  }

  Widget _decisionArea(BuildContext context, PortalChange o) {
    final l = context.anum;
    if (o.decision != null) return const SizedBox.shrink();
    if (!o.waiting) {
      return Text(l.t('portal.change.alreadyApproved'),
          key: const Key('portal-state'));
    }
    if (o.stale) {
      return Text(l.t('portal.change.staleBody'),
          key: const Key('portal-state'),
          style: TextStyle(color: context.palette.stop));
    }
    if (!controller.canDecide || !o.canDecide) {
      return Text(l.t('portal.readOnlyBody'), key: const Key('portal-state'));
    }
    return Wrap(spacing: 8, runSpacing: 8, children: [
      FilledButton.icon(
        key: const Key('portal-approve'),
        onPressed: busy ? null : () => _decide(o, true),
        icon: const Icon(Icons.check),
        label: Text(l.t('portal.confirm.approve')),
      ),
      OutlinedButton.icon(
        key: const Key('portal-decline'),
        onPressed: busy ? null : () => _decide(o, false),
        icon: const Icon(Icons.close),
        label: Text(l.t('portal.confirm.decline')),
      ),
    ]);
  }
}

// -- invoices ------------------------------------------------------------------

class PortalInvoicesScreen extends StatelessWidget {
  const PortalInvoicesScreen(
      {required this.controller,
      this.saver = const FilePickerInvoiceSaver(),
      super.key});
  final PortalController controller;
  final InvoiceDocumentSaver saver;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return _PortalList<PortalInvoiceSummary>(
      controller: controller,
      title: l.t('portal.invoices'),
      section: controller.invoices,
      load: controller.loadInvoices,
      emptyTitle: l.t('portal.invoices.emptyTitle'),
      emptyText: l.t('portal.invoices.emptyText'),
      row: (context, i) => AnumSurface(
        key: Key('portal-invoice-${i.id}'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) => PortalInvoiceScreen(
                controller: controller, invoiceId: i.id, saver: saver))),
        child: PillRow(
          title: i.number.isEmpty ? l.t('portal.invoice') : i.number,
          subtitle: [
            if (i.status == 'sent')
              l.f('portal.dueOn', {'date': dayPart(i.dueDate)}),
            if (i.overdue) l.f('portal.daysOverdue', {'days': i.daysOverdue}),
          ].join(' · '),
          detail: l.f('portal.invoiceFigures', {
            'total': _money(context, i.total, i.currency),
            'balance': _money(context, i.balance, i.currency),
          }),
          icon: Icons.receipt_long_outlined,
          pills: [
            AnumPill(
              label: l.t('biz.inv.status.${i.status}'),
              tone: i.overdue ? AnumTone.stop : _invoiceTone(i.status),
            ),
          ],
        ),
      ),
    );
  }
}

class PortalInvoiceScreen extends StatefulWidget {
  const PortalInvoiceScreen(
      {required this.controller,
      required this.invoiceId,
      this.saver = const FilePickerInvoiceSaver(),
      super.key});
  final PortalController controller;
  final String invoiceId;
  final InvoiceDocumentSaver saver;

  @override
  State<PortalInvoiceScreen> createState() => _PortalInvoiceState();
}

class _PortalInvoiceState extends State<PortalInvoiceScreen>
    with _DetailLoader<PortalInvoiceScreen, PortalInvoice> {
  @override
  PortalController get controller => widget.controller;
  @override
  Future<PortalInvoice> fetch() =>
      controller.repository.invoice(widget.invoiceId);

  InvoiceDocument _document(PortalInvoice i) {
    final s = i.seller;
    return InvoiceDocument(
      status: i.status,
      currency: i.currency,
      number: i.number.isEmpty ? null : i.number,
      issueDate: i.issueDate,
      dueDate: i.dueDate,
      sellerName: s.legalName,
      sellerAddress: s.address,
      sellerTax: s.taxNumber.isEmpty ? '' : '${s.taxLabel}: ${s.taxNumber}',
      sellerContact: [
        if (s.email.isNotEmpty) s.email,
        if (s.phone.isNotEmpty) s.phone
      ].join(', '),
      bankDetails: s.bankDetails,
      buyerName: i.billToName,
      buyerAddress: i.billToAddress,
      buyerTaxNumber: i.billToTaxNumber,
      reference: i.customerReference,
      notes: i.notes,
      paymentTerms: i.paymentTerms,
      voidReason: i.voidReason,
      lines: [
        for (final x in i.lines)
          InvoiceDocumentLine(
              x.description, x.quantity, x.unitPrice, x.taxRatePct, x.total),
      ],
      subtotal: i.subtotal,
      tax: i.tax,
      total: i.total,
      paid: i.amountPaid,
      balance: i.balance,
      credited: i.amountCredited,
      creditNotes: [
        for (final c in i.creditNotes)
          InvoiceDocumentCredit(c.number, c.issueDate, c.amount, c.reason,
              isVoid: c.isVoid),
      ],
      payments: [
        for (final p in i.payments)
          InvoiceDocumentPayment(p.paidOn, p.amount, p.method, p.reference),
      ],
    );
  }

  /// One key per report until it is sent: a retry after a lost answer is the same report, a later payment is a new one.
  String? _reportKey;

  static String _newKey() {
    final random = Random.secure();
    return List.generate(
            4, (_) => random.nextInt(1 << 32).toRadixString(16).padLeft(8, '0'))
        .join();
  }

  Future<void> _report(PortalInvoice i) async {
    final l = context.anum;
    final body = await showDialog<Map<String, Object?>>(
      context: context,
      builder: (context) => PortalReportDialog(
          balance: i.balance, currency: i.currency, issueDate: i.issueDate),
    );
    if (body == null) return;
    setState(() {
      busy = true;
      problem = null;
      notice = null;
    });
    try {
      final key = _reportKey ??= _newKey();
      final next = await controller.repository.reportPayment(
        i.id,
        idempotencyKey: key,
        payerName: body['payer_name']! as String,
        amount: body['amount']! as double,
        paidOn: body['paid_on']! as String,
        method: body['method']! as String,
        reference: body['reference']! as String,
        note: body['note']! as String,
      );
      if (!mounted) return;
      _reportKey = null;
      setState(() {
        record = next;
        notice = l.t('portal.report.sent');
      });
    } catch (e) {
      controller.noteError(e);
      if (mounted) setState(() => problem = PortalController.messageOf(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final linkIssue = controller.linkProblem;
        if (linkIssue != null) return PortalLinkProblem(message: linkIssue);
        final i = record;
        return Scaffold(
          appBar: AppBar(title: Text(i?.number ?? l.t('portal.invoice'))),
          body: AnumPage(
            onRefresh: load,
            children: [
              if (i == null && loadError == null)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (i == null && loadError != null)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: loadError!,
                    onRetry: load),
              if (i != null) ..._body(context, i),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(BuildContext context, PortalInvoice i) {
    final l = context.anum;
    String m(double v) => _money(context, v, i.currency);
    final doc = _document(i);
    return [
      AnumHeader(
        eyebrow: l.t('biz.inv.status.${i.status}'),
        title: i.number.isEmpty ? l.t('portal.invoice') : i.number,
        subtitle: l.f('portal.invoiceDates',
            {'issued': dayPart(i.issueDate), 'due': dayPart(i.dueDate)}),
      ),
      Wrap(spacing: 8, runSpacing: 8, children: [
        AnumPill(
            label: l.t('biz.inv.status.${i.status}'),
            tone: _invoiceTone(i.status)),
        if (i.status == 'sent' || i.status == 'paid')
          AnumPill(
              label: l.t('biz.inv.pay.${i.paymentState}'),
              tone: i.paymentState == 'paid' || i.paymentState == 'credited'
                  ? AnumTone.ok
                  : AnumTone.neutral),
        if (i.overdue)
          AnumPill(
              label: l.f('portal.daysOverdue', {'days': i.daysOverdue}),
              tone: AnumTone.stop),
      ]),
      if (i.status == 'void')
        Text(
            i.voidReason.isEmpty
                ? l.t('portal.invoice.void')
                : '${l.t('portal.invoice.void')} ${i.voidReason}',
            key: const Key('portal-invoice-void'),
            style: TextStyle(color: context.palette.stop)),
      OutlinedButton.icon(
        key: const Key('portal-invoice-preview'),
        onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => InvoicePreviewScreen(
            document: doc,
            loadHtml: (lang) => controller.repository.printHtml(i.id, lang),
            saver: widget.saver,
            loadPdf: () => controller.repository.pdf(i.id),
            fileStem: i.number.isEmpty ? 'invoice' : i.number,
          ),
        )),
        icon: const Icon(Icons.picture_as_pdf_outlined),
        label: Text(l.t('biz.inv.preview')),
      ),
      if (notice != null)
        Text(notice!,
            key: const Key('portal-invoice-notice'),
            style: TextStyle(color: context.palette.ok)),
      if (problem != null)
        Text(problem!,
            key: const Key('portal-invoice-problem'),
            style: TextStyle(color: context.palette.stop)),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('biz.inv.lines')),
            for (final line in i.lines)
              AnumRow(
                title: line.description,
                subtitle:
                    '${_qty(line.quantity)} x ${m(line.unitPrice)}, ${l.t('biz.inv.doc.tax')} ${_qty(line.taxRatePct)}%',
                detail: m(line.total),
              ),
          ],
        ),
      ),
      AnumSurface(child: InvoiceTotals(document: doc)),
      if (i.payments.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('portal.invoice.payments')),
              for (final p in i.payments)
                AnumRow(
                  title: m(p.amount),
                  subtitle: '${p.paidOn}, ${l.t('biz.inv.method.${p.method}')}',
                  detail: p.reference.isEmpty ? null : p.reference,
                ),
            ],
          ),
        ),
      if (i.creditNotes.isNotEmpty)
        AnumSurface(
          key: const Key('portal-credit-notes'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('biz.inv.doc.creditNotes')),
              for (final c in i.creditNotes)
                AnumRow(
                  title: c.number,
                  subtitle: '${c.issueDate}, ${c.reason}',
                  detail: c.isVoid
                      ? '${m(c.amount)} (${l.t('biz.inv.credit.voidTag')})'
                      : m(c.amount),
                ),
            ],
          ),
        ),
      if (i.paymentNotices.isNotEmpty || i.canReportPayment || _unsettled(i))
        AnumSurface(
          key: const Key('portal-report'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('portal.report.title')),
              for (final n in i.paymentNotices)
                PillRow(
                  key: Key('portal-notice-${n.id}'),
                  title: m(n.amount),
                  subtitle: '${n.paidOn}, ${l.t('biz.inv.method.${n.method}')}',
                  detail: [
                    if (n.reference.isNotEmpty) n.reference,
                    if (n.status == 'rejected' && n.decisionNote.isNotEmpty)
                      l.f('portal.report.why', {'reason': n.decisionNote}),
                  ].join(' · '),
                  pills: [
                    AnumPill(
                      label: l.t('portal.report.status.${n.status}'),
                      tone: n.status == 'confirmed'
                          ? AnumTone.ok
                          : n.status == 'rejected'
                              ? AnumTone.stop
                              : AnumTone.warn,
                    ),
                  ],
                ),
              if (i.canReportPayment) ...[
                Text(l.t('portal.report.help'),
                    style: TextStyle(color: context.palette.muted)),
                Align(
                  alignment: AlignmentDirectional.centerStart,
                  child: FilledButton.icon(
                    key: const Key('portal-report-open'),
                    onPressed: busy ? null : () => _report(i),
                    icon: const Icon(Icons.payments_outlined),
                    label: Text(l.t('portal.report.open')),
                  ),
                ),
              ] else if (_unsettled(i))
                Text(l.t('portal.report.readOnly'),
                    style: TextStyle(color: context.palette.muted)),
            ],
          ),
        ),
      if (i.status == 'sent')
        AnumSurface(
          key: const Key('portal-how-to-pay'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AnumHeader(title: l.t('portal.invoice.howToPay')),
              Text(l.t('portal.invoice.noOnline')),
              if (i.seller.bankDetails.isNotEmpty) Text(i.seller.bankDetails),
              if (i.paymentTerms.isNotEmpty) Text(i.paymentTerms),
            ],
          ),
        ),
      if (i.notes.isNotEmpty) AnumSurface(child: Text(i.notes)),
    ];
  }
}

/// A customer's report that they paid: their name, the amount (not above the
/// balance), the date, the method and a reference. The business confirms it
/// after checking its bank; nothing here changes a balance.
class PortalReportDialog extends StatefulWidget {
  const PortalReportDialog({
    required this.balance,
    required this.currency,
    this.issueDate,
    super.key,
  });
  final double balance;
  final String currency;
  final String? issueDate;

  @override
  State<PortalReportDialog> createState() => _PortalReportState();
}

class _PortalReportState extends State<PortalReportDialog> {
  final name = TextEditingController();
  late final amount =
      TextEditingController(text: widget.balance.toStringAsFixed(2));
  late final date = TextEditingController(text: dayOfDate(clock.now()));
  final reference = TextEditingController();
  final note = TextEditingController();
  String method = 'bank_transfer';
  String? nameError, amountError, dateError;

  @override
  void dispose() {
    name.dispose();
    amount.dispose();
    date.dispose();
    reference.dispose();
    note.dispose();
    super.dispose();
  }

  void _submit() {
    final l = context.anum;
    final who = name.text.replaceAll(RegExp(r'\s+'), ' ').trim();
    final value = double.tryParse(amount.text.trim().replaceAll(',', '.'));
    final text = date.text.trim();
    final when = DateTime.tryParse(text);
    final shape = RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text);
    final todayText = dayOfDate(clock.now());
    setState(() {
      nameError =
          who.length < 2 || !RegExp(r'\p{L}', unicode: true).hasMatch(who)
              ? l.t('portal.report.nameInvalid')
              : null;
      amountError = value == null || value <= 0
          ? l.t('portal.report.amountInvalid')
          : value > widget.balance + 0.004
              ? l.f('portal.report.tooMuch', {
                  'balance': formatMoney(widget.balance, widget.currency,
                      locale: l.locale)
                })
              : null;
      dateError = !shape || when == null || text.compareTo(todayText) > 0
          ? l.t('portal.report.dateInvalid')
          : null;
    });
    if (nameError != null || amountError != null || dateError != null) return;
    Navigator.pop(context, <String, Object?>{
      'payer_name': who,
      'amount': double.parse(value!.toStringAsFixed(2)),
      'paid_on': text,
      'method': method,
      'reference': reference.text.trim(),
      'note': note.text.trim(),
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return AlertDialog(
      title: Text(l.t('portal.report.title')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('portal.report.help')),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-report-name'),
              controller: name,
              maxLength: 160,
              decoration: InputDecoration(
                  labelText: l.t('portal.report.name'), errorText: nameError),
            ),
            TextField(
              key: const Key('portal-report-amount'),
              controller: amount,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                  labelText:
                      '${l.t('portal.report.amount')} (${widget.currency})',
                  errorText: amountError),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-report-date'),
              controller: date,
              keyboardType: TextInputType.datetime,
              decoration: InputDecoration(
                  labelText: l.t('portal.report.date'),
                  hintText: 'YYYY-MM-DD',
                  errorText: dateError),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              key: const Key('portal-report-method'),
              initialValue: method,
              isExpanded: true,
              decoration: InputDecoration(labelText: l.t('biz.inv.method')),
              items: [
                for (final m in const [
                  'bank_transfer',
                  'cash',
                  'cheque',
                  'card',
                  'other'
                ])
                  DropdownMenuItem(
                      value: m, child: Text(l.t('biz.inv.method.$m'))),
              ],
              onChanged: (v) => setState(() => method = v ?? method),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const Key('portal-report-reference'),
              controller: reference,
              maxLength: 120,
              decoration:
                  InputDecoration(labelText: l.t('portal.report.reference')),
            ),
            TextField(
              key: const Key('portal-report-note'),
              controller: note,
              maxLength: 300,
              decoration: InputDecoration(labelText: l.t('portal.report.note')),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(l.t('biz.cancel'))),
        FilledButton(
          key: const Key('portal-report-submit'),
          onPressed: _submit,
          child: Text(l.t('portal.report.send')),
        ),
      ],
    );
  }
}

// -- requests --------------------------------------------------------------------

AnumTone _ticketTone(String status) => switch (status) {
      'resolved' || 'closed' => AnumTone.ok,
      'waiting_on_customer' => AnumTone.warn,
      _ => AnumTone.info,
    };

class PortalTicketsScreen extends StatelessWidget {
  const PortalTicketsScreen({required this.controller, super.key});
  final PortalController controller;

  Future<void> _new(BuildContext context) async {
    final created = await Navigator.of(context).push<PortalTicket>(
      MaterialPageRoute<PortalTicket>(
          builder: (_) => PortalNewTicketScreen(controller: controller)),
    );
    if (created != null && context.mounted) {
      await Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => PortalTicketScreen(
              controller: controller, ticketId: created.id)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return _PortalList<PortalTicketSummary>(
      controller: controller,
      title: l.t('portal.tickets'),
      section: controller.tickets,
      load: controller.loadTickets,
      emptyTitle: l.t('portal.tickets.emptyTitle'),
      emptyText: l.t('portal.tickets.emptyText'),
      fab: FloatingActionButton.extended(
        key: const Key('portal-new-ticket'),
        onPressed: () => _new(context),
        icon: const Icon(Icons.add),
        label: Text(l.t('portal.tickets.new')),
      ),
      row: (context, t) => AnumSurface(
        key: Key('portal-ticket-${t.id}'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) =>
                PortalTicketScreen(controller: controller, ticketId: t.id))),
        child: PillRow(
          title: t.title,
          subtitle: [
            t.reference,
            l.t('portal.severity.${t.severity}'),
            if (t.projectName != null) t.projectName!,
          ].join(' · '),
          detail: dayOfDate(t.updatedAt),
          icon: Icons.support_agent_outlined,
          pills: [
            AnumPill(
                label: l.t('portal.ticket.${t.status}'),
                tone: _ticketTone(t.status)),
          ],
        ),
      ),
    );
  }
}

class PortalTicketScreen extends StatefulWidget {
  const PortalTicketScreen(
      {required this.controller, required this.ticketId, super.key});
  final PortalController controller;
  final String ticketId;

  @override
  State<PortalTicketScreen> createState() => _PortalTicketState();
}

class _PortalTicketState extends State<PortalTicketScreen>
    with _DetailLoader<PortalTicketScreen, PortalTicket> {
  @override
  PortalController get controller => widget.controller;
  @override
  Future<PortalTicket> fetch() => controller.repository.ticket(widget.ticketId);

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final linkIssue = controller.linkProblem;
        if (linkIssue != null) return PortalLinkProblem(message: linkIssue);
        final t = record;
        return Scaffold(
          appBar: AppBar(title: Text(t?.reference ?? l.t('portal.tickets'))),
          body: AnumPage(
            onRefresh: load,
            children: [
              if (t == null && loadError == null)
                AnumFeedback(
                    kind: AnumFeedbackKind.loading,
                    message: l.t('portal.loading')),
              if (t == null && loadError != null)
                AnumFeedback(
                    kind: AnumFeedbackKind.error,
                    message: loadError!,
                    onRetry: load),
              if (t != null) ..._body(context, t),
            ],
          ),
        );
      },
    );
  }

  List<Widget> _body(BuildContext context, PortalTicket t) {
    final l = context.anum;
    return [
      AnumHeader(
        eyebrow: l.t('portal.ticket.${t.status}'),
        title: t.title,
        subtitle: [
          t.reference,
          l.t('portal.severity.${t.severity}'),
          if (t.projectName != null) t.projectName!,
        ].join(' · '),
      ),
      Wrap(spacing: 8, runSpacing: 8, children: [
        AnumPill(
            label: l.t('portal.ticket.${t.status}'),
            tone: _ticketTone(t.status)),
        if (t.openedHere) AnumPill(label: l.t('portal.ticket.openedHere')),
      ]),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('portal.ticket.details'),
                style: Theme.of(context).textTheme.titleSmall),
            Text(t.description.isEmpty ? '-' : t.description),
            Text(
                l.f('portal.ticket.reported',
                    {'date': dayOfDate(t.reportedAt)}),
                style: TextStyle(color: context.palette.muted)),
          ],
        ),
      ),
      if (t.resolutionSummary.isNotEmpty)
        AnumSurface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l.t('portal.ticket.resolution'),
                  style: Theme.of(context).textTheme.titleSmall),
              Text(t.resolutionSummary),
            ],
          ),
        ),
      AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AnumHeader(title: l.t('portal.ticket.replies')),
            if (t.replies.isEmpty)
              Text(l.t('portal.ticket.noReplies'),
                  style: TextStyle(color: context.palette.muted)),
            for (final r in t.replies)
              AnumRow(
                title: r.body,
                subtitle: r.sentAt == null ? null : dayOfDate(r.sentAt),
                icon: Icons.reply_outlined,
              ),
          ],
        ),
      ),
      Text(l.t('portal.ticket.followUp'),
          style: TextStyle(color: context.palette.muted)),
    ];
  }
}

class PortalNewTicketScreen extends StatefulWidget {
  const PortalNewTicketScreen({required this.controller, super.key});
  final PortalController controller;

  @override
  State<PortalNewTicketScreen> createState() => _PortalNewTicketState();
}

class _PortalNewTicketState extends State<PortalNewTicketScreen> {
  final title = TextEditingController();
  final details = TextEditingController();
  String severity = 'normal';
  String? projectId;
  String? error, titleError, detailsError;
  bool busy = false;

  @override
  void dispose() {
    title.dispose();
    details.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final l = context.anum;
    final t = title.text.trim();
    final d = details.text.trim();
    setState(() {
      titleError =
          t.length < 3 || t.length > 200 ? l.t('portal.new.titleNeeded') : null;
      detailsError = d.length < 3 ? l.t('portal.new.detailsNeeded') : null;
      error = null;
    });
    if (titleError != null || detailsError != null) return;
    setState(() => busy = true);
    try {
      final created = await widget.controller.openTicket(
          title: t, description: d, severity: severity, projectId: projectId);
      if (mounted) Navigator.of(context).pop(created);
    } on ApiException catch (e) {
      if (widget.controller.noteError(e)) return;
      if (mounted) {
        setState(() {
          error = e.readable;
          busy = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: widget.controller,
      builder: (context, _) {
        final linkIssue = widget.controller.linkProblem;
        if (linkIssue != null) return PortalLinkProblem(message: linkIssue);
        final me = widget.controller.me;
        return Scaffold(
          appBar: AppBar(title: Text(l.t('portal.tickets.new'))),
          body: AnumPage(
            children: [
              if (me != null && !me.canDecide)
                Text(l.t('portal.new.readOnlyNote'),
                    style: TextStyle(color: context.palette.muted)),
              TextField(
                key: const Key('portal-ticket-title'),
                controller: title,
                maxLength: 200,
                decoration: InputDecoration(
                    labelText: l.t('portal.new.title'), errorText: titleError),
              ),
              TextField(
                key: const Key('portal-ticket-details'),
                controller: details,
                maxLength: 4000,
                minLines: 4,
                maxLines: 8,
                decoration: InputDecoration(
                    labelText: l.t('portal.new.details'),
                    errorText: detailsError),
              ),
              DropdownButtonFormField<String>(
                key: const Key('portal-ticket-severity'),
                initialValue: severity,
                isExpanded: true,
                decoration:
                    InputDecoration(labelText: l.t('portal.new.severity')),
                items: [
                  for (final s in const ['low', 'normal', 'high'])
                    DropdownMenuItem(
                        value: s, child: Text(l.t('portal.severity.$s'))),
                ],
                onChanged: (v) => setState(() => severity = v ?? severity),
              ),
              if (me != null && me.projects.isNotEmpty)
                DropdownButtonFormField<String?>(
                  key: const Key('portal-ticket-project'),
                  initialValue: projectId,
                  isExpanded: true,
                  decoration:
                      InputDecoration(labelText: l.t('portal.new.project')),
                  items: [
                    DropdownMenuItem<String?>(
                        value: null, child: Text(l.t('portal.new.noProject'))),
                    for (final p in me.projects)
                      DropdownMenuItem<String?>(
                          value: p.id,
                          child: Text(p.name, overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setState(() => projectId = v),
                ),
              if (error != null)
                Text(error!,
                    key: const Key('portal-ticket-error'),
                    style: TextStyle(color: context.palette.stop)),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton(
                  key: const Key('portal-ticket-send'),
                  onPressed: busy ? null : _submit,
                  child: Text(l.t('portal.new.send')),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
