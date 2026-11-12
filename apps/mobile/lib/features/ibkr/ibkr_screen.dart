import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../app_lock/app_lock_screen.dart';
import '../app_lock/app_lock_controller.dart' show AppLockArea;
import '../owner_desk/desk_widgets.dart';
import 'ibkr_data.dart';

/// IBKR trading on a phone (docs/finance-live-trading.md#on-the-phone).
class IbkrScreen extends StatefulWidget {
  const IbkrScreen(
      {required this.controller, this.countdown = true, super.key});
  final IbkrController controller;

  /// Whether the approval countdown ticks every second (off in tests that
  /// settle the widget tree).
  final bool countdown;

  @override
  State<IbkrScreen> createState() => _IbkrScreenState();
}

class _IbkrScreenState extends State<IbkrScreen> {
  IbkrController get c => widget.controller;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    c.load();
    // The approval countdown moves while something waits.
    if (!widget.countdown) return;
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted && (c.overview?.pending.isNotEmpty ?? false)) {
        setState(() {});
      }
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  String _usd(String? v) => v == null || v.isEmpty ? '-' : 'USD $v';

  // Money: behind the app lock's Finance area when it is on
  // (docs/mobile.md#app-lock), like the Finance screen.
  @override
  Widget build(BuildContext context) =>
      AppLockGate(area: AppLockArea.finance, child: _content(context));

  Widget _content(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          return Scaffold(
            appBar: AppBar(title: Text(l.t('od.ib.title')), actions: [
              IconButton(
                  key: const Key('ib-refresh'),
                  tooltip: l.t('od.refresh'),
                  onPressed: c.phase == IbkrPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.eyebrow'),
                  title: l.t('od.ib.title'),
                  subtitle: l.t('od.ib.intro'),
                  large: true),
              if (c.note case final n?)
                DeskMessage(
                    key: const Key('ib-note'),
                    text: n.raw ?? deskText(l, n.key!, n.values),
                    failed: n.failed),
              ..._body(context),
            ]),
          );
        },
      );

  List<Widget> _body(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    switch (c.phase) {
      case IbkrPhase.loading:
        // A reload keeps the last answer on screen.
        if (c.overview == null) {
          return [DeskLoading(label: l.t('ibkr.loading'))];
        }
      case IbkrPhase.denied:
        return [
          AnumSurface(
              child: AnumEmpty(
                  key: const Key('ib-denied'),
                  icon: Icons.lock_outline,
                  title: l.t('od.ib.deniedTitle'),
                  message: l.t('od.ib.deniedText')))
        ];
      case IbkrPhase.error:
        return [
          AnumSurface(
              accent: p.stop,
              child: AnumEmpty(
                  key: const Key('ib-error'),
                  icon: Icons.error_outline,
                  title: l.t('od.loadFailedTitle'),
                  message: l.f('ibkr.error.load', {'message': c.error ?? '-'}),
                  action: l.t('od.retry'),
                  onAction: c.load))
        ];
      case IbkrPhase.ready:
        break;
    }
    final o = c.overview!;
    return [
      _status(context, o),
      _approvals(context, o),
      IbkrTicket(controller: c, overview: o),
      if (o.unreadAlerts > 0 || c.alerts.isNotEmpty || c.alertsError != null)
        _alerts(context, o),
      _connections(context, o),
      _limits(context, o),
      _proof(context, o),
      _gates(context, o),
      _account(context, o),
      _orders(context),
    ];
  }

  Widget _modePill(AnumLocalizations l, String mode) => AnumPill(
      label: l.t('ibkr.mode.$mode'),
      tone: mode == 'live' ? AnumTone.stop : AnumTone.info);

  Widget _status(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('ib-status'),
      accent: o.killSwitch ? p.stop : null,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(l.t('ibkr.status.mode'),
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
              _modePill(l, o.mode),
            ]),
        Text(
            l.t(o.mode == 'live'
                ? 'ibkr.status.modeLive'
                : 'ibkr.status.modePaper'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        const SizedBox(height: AnumSpacing.xs),
        DeskFact(
            label: l.t('ibkr.status.market'),
            value: o.marketOpen
                ? l.t('ibkr.status.marketOpen')
                : l.t('od.ib.marketClosed')),
        if (!o.marketOpen && o.marketMessage.isNotEmpty)
          Text(o.marketMessage,
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        DeskFact(
            label: l.t('ibkr.status.approval'),
            value: l.f(
                'ibkr.status.approvalValue', {'minutes': o.approvalMinutes})),
        DeskFact(
            label: l.t('ibkr.status.kill'),
            value: l.t(o.killSwitch ? 'od.ib.killOn' : 'od.ib.killOff')),
        if (o.unreadAlerts > 0)
          Text(l.f('od.ib.alerts', {'count': o.unreadAlerts}),
              key: const Key('ib-alerts'),
              style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: o.killSwitch
              ? OutlinedButton.icon(
                  key: const Key('ib-release'),
                  onPressed: c.busy != null ? null : () => _release(context),
                  icon: const Icon(Icons.play_arrow),
                  label: Text(l.t('ibkr.kill.release')))
              : FilledButton.icon(
                  key: const Key('ib-kill'),
                  style: FilledButton.styleFrom(
                      backgroundColor: p.stop, foregroundColor: p.background),
                  onPressed: c.busy != null ? null : () => _kill(context),
                  icon: const Icon(Icons.block),
                  label: Text(l.t('ibkr.kill.button'))),
        ),
        if (o.riskNote.isNotEmpty) ...[
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('od.ib.risk'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ],
      ]),
    );
  }

  Future<void> _kill(BuildContext context) async {
    final l = AnumLocalizations.of(context);
    var reason = '';
    final ok = await deskConfirm(context,
        title: l.t('ibkr.kill.title'),
        text: l.t('ibkr.kill.text'),
        extra: DeskTextField(
            key: const Key('ib-kill-reason'),
            label: l.t('ibkr.kill.reason'),
            maxLength: 300,
            onChanged: (v) => reason = v),
        confirm: l.t('ibkr.kill.button'),
        cancel: l.t('od.cancel'),
        danger: true,
        confirmKey: const Key('ib-kill-confirm'));
    if (ok) await c.kill(reason);
  }

  Future<void> _release(BuildContext context) async {
    final l = AnumLocalizations.of(context);
    var typed = '';
    final ok = await deskConfirm(context,
        title: l.t('ibkr.kill.releaseTitle'),
        text: l.t('ibkr.kill.releaseText'),
        extra: DeskTextField(
            key: const Key('ib-release-word'),
            label: l.t('od.ib.releaseType'),
            ltr: true,
            onChanged: (v) => typed = v),
        confirm: l.t('ibkr.kill.release'),
        cancel: l.t('od.cancel'),
        confirmKey: const Key('ib-release-confirm'));
    if (ok) await c.release(typed);
  }

  Widget _approvals(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('ib-approvals'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('od.ib.approvalsTitle'),
            count: o.pending.length,
            icon: Icons.approval_outlined),
        const SizedBox(height: AnumSpacing.sm),
        if (o.pending.isEmpty)
          Text(l.t('ibkr.approvals.none'),
              key: const Key('ib-approvals-none'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm))
        else
          for (final i in o.pending) _intent(context, o, i),
      ]),
    );
  }

  Widget _intent(BuildContext context, IbkrOverview o, IbkrIntent i) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final left = i.secondsLeft(c.now());
    final account = o.connection(i.environment)?.hint ?? '-';
    // The figures keep their left to right order inside an Arabic sentence.
    final order = '${l.t('ibkr.side.${i.side}')} '
        '\u2066${i.quantity} ${i.symbol} @ USD ${i.limitPrice}\u2069';
    return Container(
      key: Key('ib-intent-${i.id}'),
      margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
      padding: const EdgeInsets.all(AnumSpacing.sm),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(AnumRadius.lg),
        border: Border.all(color: i.live ? p.stop : p.line),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xxs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              _modePill(l, i.environment),
              Text(order,
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
            ]),
        if (i.live)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xxs),
            child: Text(l.t('ibkr.review.realMoney'),
                style: TextStyle(color: p.stop, fontWeight: FontWeight.w700)),
          ),
        const SizedBox(height: AnumSpacing.xs),
        DeskFact(label: l.t('ibkr.review.account'), value: account, ltr: true),
        DeskFact(
            label: l.t('ibkr.review.type'),
            value: l.t('ibkr.review.typeValue')),
        DeskFact(label: l.t('ibkr.review.total'), value: _usd(i.amount)),
        DeskFact(
            label: l.t('ibkr.review.expires'),
            value: left == 0
                ? l.t('ibkr.review.expired')
                : l.f('ibkr.review.seconds', {'count': left})),
        if (i.requestedBy == 'agent')
          Text(l.t('od.ib.byAgent'),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        const SizedBox(height: AnumSpacing.xs),
        DeskHash(label: l.t('ibkr.review.hash'), hash: i.payloadHash),
        const SizedBox(height: AnumSpacing.xs),
        Text(l.t('ibkr.review.checks'),
            style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          FilledButton(
              key: Key('ib-approve-${i.id}'),
              style: i.live
                  ? FilledButton.styleFrom(
                      backgroundColor: p.stop, foregroundColor: p.background)
                  : null,
              onPressed: c.busy != null || left == 0
                  ? null
                  : () async {
                      if (await deskConfirm(context,
                          title: l.t(i.live
                              ? 'ibkr.review.titleLive'
                              : 'ibkr.review.titlePaper'),
                          text: order,
                          extra: DeskHash(
                              label: l.t('ibkr.review.hash'),
                              hash: i.payloadHash),
                          confirm: l.t(i.live
                              ? 'ibkr.review.approveLive'
                              : 'ibkr.review.approve'),
                          cancel: l.t('od.cancel'),
                          danger: i.live,
                          confirmKey: const Key('ib-approve-confirm'))) {
                        await c.approve(i);
                      }
                    },
              child: Text(l.t(
                  i.live ? 'ibkr.review.approveLive' : 'ibkr.review.approve'))),
          OutlinedButton(
              key: Key('ib-reject-${i.id}'),
              onPressed: c.busy != null ? null : () => c.reject(i),
              child: Text(l.t('ibkr.review.reject'))),
        ]),
      ]),
    );
  }

  /// Check at IBKR, Look up and send, Cancel: the same buttons and states as
  /// the web's order list.
  Widget _orderActions(BuildContext context, IbkrOrder o) {
    final l = AnumLocalizations.of(context);
    final open = ibkrOpenStates.contains(o.state);
    final unknown = o.state == 'unknown';
    final cancellable = o.state == 'submitted' || o.state == 'partially_filled';
    if (!open && !unknown && !cancellable) return const SizedBox.shrink();
    final idle = c.busy == null;
    return Padding(
      padding: const EdgeInsets.only(top: AnumSpacing.xxs),
      child:
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        if (open)
          OutlinedButton(
              key: Key('ib-check-order-${o.id}'),
              onPressed: idle ? () => c.checkOrder(o) : null,
              child: Text(l.t('ibkr.orders.check'))),
        if (unknown)
          OutlinedButton(
              key: Key('ib-resend-${o.id}'),
              onPressed: idle ? () => c.resendOrder(o) : null,
              child: Text(l.t('ibkr.orders.resend'))),
        if (cancellable)
          OutlinedButton(
              key: Key('ib-cancel-${o.id}'),
              style: OutlinedButton.styleFrom(
                  foregroundColor: context.palette.stop),
              onPressed: !idle
                  ? null
                  : () async {
                      if (await deskConfirm(context,
                          title: l.t('od.ib.cancelOrderTitle'),
                          text: l.t('od.ib.cancelOrderText'),
                          confirm: l.t('ibkr.orders.cancel'),
                          cancel: l.t('od.ib.keepOrder'),
                          danger: true,
                          confirmKey: const Key('ib-cancel-confirm'))) {
                        await c.cancelOrder(o);
                      }
                    },
              child: Text(l.t('ibkr.orders.cancel'))),
      ]),
    );
  }

  Widget _alerts(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    AnumTone tone(String severity) => switch (severity) {
          'critical' => AnumTone.stop,
          'warning' => AnumTone.warn,
          _ => AnumTone.neutral,
        };
    return AnumSurface(
      key: const Key('ib-alerts-list'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.f('ibkr.alerts.title', {'count': o.unreadAlerts}),
            icon: Icons.notifications_outlined),
        const SizedBox(height: AnumSpacing.sm),
        if (c.alertsError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('od.loadFailedTitle'),
              message: l.f('od.ib.alertsFailed', {'message': c.alertsError!}),
              action: l.t('od.retry'),
              onAction: c.loadAlerts)
        else if (c.alerts.isEmpty && c.alertsLoading)
          DeskLoading(label: l.t('ibkr.loading'))
        else if (c.alerts.isEmpty)
          Text(l.t('od.ib.alertsNone'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final a in c.alerts)
          Padding(
            key: Key('ib-alert-${a.id}'),
            padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xxs,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        AnumPill(
                            label: l.t('ibkr.severity.${a.severity}'),
                            tone: tone(a.severity)),
                        if (a.environment != null) _modePill(l, a.environment!),
                      ]),
                  Text(a.message,
                      style: TextStyle(
                          color: p.text,
                          fontWeight:
                              a.read ? FontWeight.w400 : FontWeight.w700)),
                  Text(deskWhen(context, a.createdAt),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                ]),
          ),
        if (o.unreadAlerts > 0)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton(
                key: const Key('ib-alerts-read'),
                onPressed: c.busy != null ? null : c.markAlertsRead,
                child: Text(l.t('ibkr.alerts.read'))),
          ),
      ]),
    );
  }

  Widget _limits(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('ib-limits'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('ibkr.limits.title'), icon: Icons.shield_outlined),
        const SizedBox(height: AnumSpacing.sm),
        for (final field in ibkrLimitFields)
          DeskFact(
              label: l.t('ibkr.limit.$field'),
              value: o.limits[field] ?? '-',
              ltr: true),
        const SizedBox(height: AnumSpacing.xs),
        Text(l.t('ibkr.limits.lossWhy'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        Text(
            l.f('ibkr.limits.lossText',
                {'amount': _usd(o.limits['max_daily_loss'])}),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        if (o.liveOn) ...[
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('ibkr.limits.liveStricter'),
              style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
              key: const Key('ib-limits-edit'),
              onPressed: o.limits.isEmpty
                  ? null
                  : () => showDialog<void>(
                      context: context,
                      builder: (_) =>
                          _LimitsDialog(controller: c, overview: o)),
              icon: const Icon(Icons.tune),
              label: Text(l.t('ibkr.limits.edit'))),
        ),
      ]),
    );
  }

  Widget _connections(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    Widget one(String env) {
      final con = o.connection(env);
      return Column(
        key: Key('ib-connection-$env'),
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
              spacing: AnumSpacing.xs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(l.t('ibkr.connect.$env'),
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w800)),
                _modePill(l, env),
                if (con != null)
                  AnumPill(
                      label: l.t('ibkr.session.${con.session}'),
                      tone: con.session == 'authenticated'
                          ? AnumTone.ok
                          : con.session == 'unknown'
                              ? AnumTone.neutral
                              : AnumTone.warn),
              ]),
          const SizedBox(height: AnumSpacing.xxs),
          if (con == null)
            Text(
                env == 'paper'
                    ? l.t('ibkr.connectFirst.text')
                    : l.t(o.deploymentLiveEnabled
                        ? 'od.ib.liveNotConnected'
                        : 'od.ib.liveOff'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm))
          else ...[
            DeskFact(
                label: l.t('ibkr.connect.account'), value: con.hint, ltr: true),
            DeskFact(
                label: l.t('ibkr.connect.checked'),
                value: con.verifiedAt == null
                    ? l.t('ibkr.connect.notChecked')
                    : deskWhen(context, con.verifiedAt)),
            DeskFact(
                label: l.t('ibkr.connect.synced'),
                value: deskWhen(context, con.lastSyncAt)),
            DeskFact(
                label: l.t('ibkr.recon.title'),
                value: l
                    .t('ibkr.recon.${o.reconciliation[env] ?? 'not_checked'}')),
            if (con.session == 'expired')
              Text(l.t('ibkr.session.loginSteps'),
                  style: TextStyle(color: p.warn, fontSize: AnumType.sm)),
            if (con.lastError != null)
              Text(con.lastError!,
                  style: TextStyle(color: p.stop, fontSize: AnumType.xs)),
          ],
        ],
      );
    }

    return AnumSurface(
      key: const Key('ib-connections'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(title: l.t('ibkr.connect.title'), icon: Icons.link),
        const SizedBox(height: AnumSpacing.sm),
        if (o.connections.isEmpty)
          AnumEmpty(
              key: const Key('ib-not-connected'),
              icon: Icons.link_off,
              title: l.t('ibkr.connectFirst.title'),
              message: l.t('od.ib.connectOnWeb'))
        else ...[
          one('paper'),
          Divider(height: 24, color: p.line),
          one('live'),
        ],
      ]),
    );
  }

  Widget _checkRow(BuildContext context, IbkrCheck check, {bool bar = false}) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final progress = check.progress;
    return Padding(
      key: Key('ib-check-${check.code}'),
      padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(check.ok ? Icons.check_circle_outline : Icons.cancel_outlined,
            size: 18,
            color: check.ok ? p.ok : p.stop,
            semanticLabel: l.t(check.ok ? 'od.ib.met' : 'od.ib.unmet')),
        const SizedBox(width: AnumSpacing.xs),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(l.t('ibkr.gate.${check.code}'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            if (bar && progress != null) ...[
              const SizedBox(height: AnumSpacing.xxs),
              Semantics(
                label: l.f('od.ib.progress',
                    {'done': progress.$1, 'total': progress.$2}),
                child: ExcludeSemantics(
                  child: LinearProgressIndicator(
                      value: progress.$2 == 0
                          ? 1
                          : (progress.$1 / progress.$2).clamp(0, 1).toDouble(),
                      minHeight: 6,
                      color: check.ok ? p.ok : p.sky,
                      backgroundColor: p.line,
                      borderRadius: BorderRadius.circular(AnumRadius.pill)),
                ),
              ),
            ],
            if (check.message.isNotEmpty)
              Text(check.message,
                  textDirection: TextDirection.ltr,
                  style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
          ]),
        ),
      ]),
    );
  }

  Widget _proof(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('ib-proof'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              DeskSectionTitle(
                  title: l.t('od.ib.proofTitle'), icon: Icons.flag_outlined),
              AnumPill(
                  label: l.t(o.paperDone ? 'od.ib.met' : 'od.ib.inProgress'),
                  tone: o.paperDone ? AnumTone.ok : AnumTone.warn),
            ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(
            l.f('od.ib.proofIntro',
                {'orders': o.paperMinOrders, 'days': o.paperMinDays}),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.sm),
        for (final check in o.paperProof) _checkRow(context, check, bar: true),
      ]),
    );
  }

  Widget _gates(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final met = o.liveGates.where((g) => g.ok).length;
    return AnumSurface(
      key: const Key('ib-gates'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              DeskSectionTitle(
                  title: l.t('od.ib.gatesTitle'), icon: Icons.shield_outlined),
              AnumPill(
                  label: l.f('od.ib.progress',
                      {'done': met, 'total': o.liveGates.length}),
                  tone: met == o.liveGates.length
                      ? AnumTone.ok
                      : AnumTone.neutral),
            ]),
        const SizedBox(height: AnumSpacing.sm),
        for (final g in o.liveGates) _checkRow(context, g),
      ]),
    );
  }

  Widget _account(BuildContext context, IbkrOverview o) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final snap = o.snapshots[o.mode] ?? const IbkrSnapshot();
    return AnumSurface(
      key: const Key('ib-account'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              DeskSectionTitle(
                  title: l.t('ibkr.account.title'),
                  icon: Icons.account_balance_wallet_outlined),
              _modePill(l, o.mode),
            ]),
        const SizedBox(height: AnumSpacing.sm),
        if (snap.syncedAt == null)
          Text(l.t('od.ib.notSynced'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm))
        else ...[
          Text(
              l.f('ibkr.account.source',
                  {'date': deskWhen(context, snap.syncedAt)}),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
          const SizedBox(height: AnumSpacing.xs),
          DeskFact(
              label: l.t('ibkr.account.cash'),
              value: _usd(snap.cash),
              ltr: true),
          DeskFact(
              label: l.t('ibkr.account.net'), value: _usd(snap.net), ltr: true),
          DeskFact(
              label: l.t('ibkr.account.today'),
              value: _usd(snap.today),
              ltr: true),
          DeskFact(
              label: l.t('ibkr.account.unrealised'),
              value: _usd(snap.unrealized),
              ltr: true),
          DeskFact(
              label: l.t('ibkr.account.realised'),
              value: _usd(snap.realized),
              ltr: true),
          Text(l.t('ibkr.account.pnlHelp'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('ibkr.account.positions'),
              style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
          if (snap.positions.isEmpty)
            Text(l.t('ibkr.account.noPositions'),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm))
          else
            for (final pos in snap.positions)
              DeskFact(
                  label: pos.symbol,
                  value: '${pos.quantity} | ${_usd(pos.marketValue)}',
                  ltr: true),
        ],
      ]),
    );
  }

  Widget _orders(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    AnumTone tone(String state) => switch (state) {
          'filled' => AnumTone.ok,
          'rejected' || 'unknown' => AnumTone.stop,
          'cancelled' => AnumTone.neutral,
          _ => AnumTone.warn,
        };
    return AnumSurface(
      key: const Key('ib-orders'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('ibkr.orders.title'),
            count: c.ordersTotal,
            icon: Icons.receipt_long_outlined),
        const SizedBox(height: AnumSpacing.sm),
        if (c.ordersError != null)
          AnumEmpty(
              icon: Icons.error_outline,
              title: l.t('od.loadFailedTitle'),
              message: c.ordersError!,
              action: l.t('od.retry'),
              onAction: c.loadOrders)
        else if (c.orders.isEmpty && c.ordersLoading)
          DeskLoading(label: l.t('ibkr.loading'))
        else if (c.orders.isEmpty)
          Text(l.t('ibkr.orders.none'),
              key: const Key('ib-orders-none'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final o in c.orders)
          Padding(
            key: Key('ib-order-${o.id}'),
            padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Wrap(
                      spacing: AnumSpacing.xs,
                      runSpacing: AnumSpacing.xxs,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        _modePill(l, o.environment),
                        AnumPill(
                            label: l.t('ibkr.state.${o.state}'),
                            tone: tone(o.state)),
                        Text(
                            '${l.t('ibkr.side.${o.side}')} \u2066${o.quantity} ${o.symbol} @ USD ${o.limitPrice}\u2069',
                            style: TextStyle(
                                color: p.text, fontWeight: FontWeight.w700)),
                      ]),
                  if (o.filled)
                    Text(
                        l.f('ibkr.orders.filled', {
                          'qty': o.filledQuantity,
                          'price': _usd(o.averagePrice)
                        }),
                        style:
                            TextStyle(color: p.muted, fontSize: AnumType.xs)),
                  if (o.message != null && o.message!.isNotEmpty)
                    Text(o.message!,
                        style:
                            TextStyle(color: p.muted, fontSize: AnumType.xs)),
                  Text(deskWhen(context, o.createdAt),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                  _orderActions(context, o),
                ]),
          ),
        if (c.hasMoreOrders)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton(
                key: const Key('ib-orders-more'),
                onPressed:
                    c.ordersLoading ? null : () => c.loadOrders(more: true),
                child: Text(l.t('od.loadMore'))),
          ),
        Text(l.t('od.ib.ordersOnWeb'),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
      ]),
    );
  }
}

/// The owner's limits, edited in a dialog; the server refuses loosening
/// while live, any value outside its bounds, and anyone but the owner.
class _LimitsDialog extends StatefulWidget {
  const _LimitsDialog({required this.controller, required this.overview});
  final IbkrController controller;
  final IbkrOverview overview;

  @override
  State<_LimitsDialog> createState() => _LimitsDialogState();
}

class _LimitsDialogState extends State<_LimitsDialog> {
  late final Map<String, String> _values = {
    for (final f in ibkrLimitFields) f: widget.overview.limits[f] ?? ''
  };
  String? _error;
  bool _saving = false;

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    final problem = await widget.controller.saveLimits(_values);
    if (!mounted) return;
    if (problem == null) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      _saving = false;
      _error = problem;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AlertDialog(
      title: Text(l.t('ibkr.limits.edit')),
      content: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final f in ibkrLimitFields)
                DeskTextField(
                    key: Key('ib-limit-$f'),
                    label: l.t('ibkr.limit.$f'),
                    initial: _values[f] ?? '',
                    number: true,
                    onChanged: (v) => _values[f] = v),
              Text(l.t('ibkr.limits.lossShort'),
                  style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
              if (_error != null) ...[
                const SizedBox(height: AnumSpacing.sm),
                DeskMessage(
                    key: const Key('ib-limits-error'),
                    text: _error!.startsWith('od.') ? l.t(_error!) : _error!,
                    failed: true),
              ],
            ]),
      ),
      actions: [
        TextButton(
            onPressed: _saving ? null : () => Navigator.of(context).pop(),
            child: Text(l.t('od.cancel'))),
        FilledButton(
            key: const Key('ib-limits-save'),
            onPressed: _saving ? null : _save,
            child: Text(l.t('od.ib.save'))),
      ],
    );
  }
}

/// The order ticket (docs/finance-live-trading.md#on-the-phone): a limit
/// order on an allowed ETF that waits for the owner's approval with its
/// payload hash, like the web's ticket. Making it sends nothing to IBKR.
class IbkrTicket extends StatefulWidget {
  const IbkrTicket(
      {required this.controller, required this.overview, super.key});
  final IbkrController controller;
  final IbkrOverview overview;

  @override
  State<IbkrTicket> createState() => _IbkrTicketState();
}

class _IbkrTicketState extends State<IbkrTicket> {
  IbkrController get c => widget.controller;
  String? _symbol;
  String _side = 'buy';
  final _quantity = TextEditingController(text: '1');
  final _price = TextEditingController();

  @override
  void dispose() {
    _quantity.dispose();
    _price.dispose();
    super.dispose();
  }

  String get _chosen {
    final allowed = [for (final a in widget.overview.allowed) a.symbol];
    return allowed.contains(_symbol) ? _symbol! : allowed.first;
  }

  Future<void> _quote() async {
    await c.getQuote(_chosen);
    final last = c.quote?.last;
    if (mounted && last != null && _price.text.trim().isEmpty) {
      setState(() => _price.text = last);
    }
  }

  Future<void> _create() async {
    final made = await c.createOrder(
        symbol: _chosen,
        side: _side,
        quantity: _quantity.text,
        limitPrice: _price.text);
    if (made && mounted) setState(_price.clear);
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final o = widget.overview;
    if (o.allowed.isEmpty) {
      return AnumSurface(
        key: const Key('ib-ticket'),
        child: AnumEmpty(
            key: const Key('ib-ticket-empty'),
            icon: Icons.playlist_add_check,
            title: l.t('ibkr.ticket.noSymbols'),
            message: l.t('ibkr.ticket.noSymbolsText')),
      );
    }
    final total = ibkrNotional(_quantity.text, _price.text);
    final quote = c.quote;
    final ticketNote = c.ticketNote;
    return AnumSurface(
      key: const Key('ib-ticket'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
            spacing: AnumSpacing.xs,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              DeskSectionTitle(
                  title: l.t('ibkr.ticket.title'), icon: Icons.send_outlined),
              AnumPill(
                  label: l.t('ibkr.mode.${o.mode}'),
                  tone: o.mode == 'live' ? AnumTone.stop : AnumTone.info),
            ]),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('ibkr.ticket.intro'),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.sm),
        DropdownButtonFormField<String>(
          key: const Key('ib-ticket-symbol'),
          initialValue: _chosen,
          isExpanded: true,
          decoration: InputDecoration(labelText: l.t('od.ib.symbol')),
          items: [
            for (final a in o.allowed)
              DropdownMenuItem(
                  value: a.symbol,
                  child: Text(
                      '${a.symbol} (${l.t(a.shariah ? 'ibkr.symbols.shariah' : 'ibkr.symbols.notScreened')})',
                      overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (v) => setState(() {
            _symbol = v;
            c.quote = null;
          }),
        ),
        const SizedBox(height: AnumSpacing.sm),
        Text(l.t('ibkr.ticket.side'),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.xxs),
        SegmentedButton<String>(
          key: const Key('ib-ticket-side'),
          segments: [
            ButtonSegment(value: 'buy', label: Text(l.t('ibkr.side.buy'))),
            ButtonSegment(value: 'sell', label: Text(l.t('ibkr.side.sell'))),
          ],
          selected: {_side},
          onSelectionChanged: (v) => setState(() => _side = v.first),
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('ib-ticket-quantity'),
          controller: _quantity,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textDirection: TextDirection.ltr,
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(
              labelText: l.t('od.ib.quantity'),
              helperText: l.t(o.fractionalOk
                  ? 'ibkr.ticket.fractional'
                  : 'ibkr.ticket.whole')),
        ),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('ib-ticket-price'),
          controller: _price,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textDirection: TextDirection.ltr,
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(labelText: l.t('ibkr.ticket.limit')),
        ),
        const SizedBox(height: AnumSpacing.xs),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
              key: const Key('ib-ticket-quote'),
              onPressed: c.quoting ? null : _quote,
              icon: const Icon(Icons.refresh),
              label: Text(l.t('ibkr.ticket.getPrice'))),
        ),
        if (quote != null)
          Semantics(
            liveRegion: true,
            child: Text(
                key: const Key('ib-ticket-quote-line'),
                quote.last == null
                    ? l.t('ibkr.ticket.noQuote')
                    : l.f(
                        quote.delayed
                            ? 'ibkr.ticket.quoteDelayed'
                            : 'ibkr.ticket.quoteLive',
                        {
                            'price': 'USD ${quote.last}',
                            'band': quote.bandPercent
                          }),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          ),
        const SizedBox(height: AnumSpacing.xs),
        Text(
            l.f('ibkr.ticket.estimate', {
              'total': total == null ? '-' : 'USD $total',
              'max': 'USD ${o.limits['max_order'] ?? '-'}'
            }),
            key: const Key('ib-ticket-estimate'),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
        if (ticketNote != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          DeskMessage(
              key: const Key('ib-ticket-note'),
              text: ticketNote.raw ??
                  deskText(l, ticketNote.key!, ticketNote.values),
              failed: ticketNote.failed),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton(
              key: const Key('ib-ticket-create'),
              onPressed: c.busy != null ? null : _create,
              child: Text(l.t('ibkr.ticket.create'))),
        ),
      ]),
    );
  }
}
