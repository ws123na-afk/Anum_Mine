import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../owner_desk/desk_widgets.dart';
import '../workspace/workspace_models.dart' show WorkspaceApproval;
import 'action_browser_data.dart';

AnumTone _tone(BrowserRun run) => switch (run.status) {
      'completed' => AnumTone.ok,
      'failed' || 'stopped' => AnumTone.stop,
      _ => AnumTone.warn,
    };

/// The action browser's runs on a phone (docs/action-browser.md#on-the-phone),
/// newest first, a page at a time; a run opens its steps and approvals.
class ActionBrowserScreen extends StatefulWidget {
  const ActionBrowserScreen(
      {required this.controller,
      required this.openRun,
      this.openVault,
      super.key});
  final ActionBrowserController controller;

  /// Opens the saved logins and cards (owners; others are told who can).
  final Future<void> Function(BuildContext context)? openVault;

  /// Opens one run; reloads the list when it closes.
  final Future<void> Function(BuildContext context, BrowserRun run) openRun;

  @override
  State<ActionBrowserScreen> createState() => _ActionBrowserScreenState();
}

class _ActionBrowserScreenState extends State<ActionBrowserScreen> {
  ActionBrowserController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          final p = context.palette;
          return Scaffold(
            appBar: AppBar(title: Text(l.t('od.ab.title')), actions: [
              if (widget.openVault != null)
                IconButton(
                    key: const Key('ab-vault'),
                    tooltip: l.t('ab.vaultTitle'),
                    onPressed: () => widget.openVault!(context),
                    icon: const Icon(Icons.key_outlined)),
              IconButton(
                  key: const Key('ab-refresh'),
                  tooltip: l.t('od.refresh'),
                  onPressed: c.phase == RunsPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              AnumHeader(
                  eyebrow: l.t('od.eyebrow'),
                  title: l.t('od.ab.title'),
                  subtitle: l.t('od.ab.intro'),
                  large: true),
              ...switch (c.phase) {
                RunsPhase.loading => [DeskLoading(label: l.t('od.ab.loading'))],
                RunsPhase.denied => [
                    AnumSurface(
                        child: AnumEmpty(
                            key: const Key('ab-denied'),
                            icon: Icons.lock_outline,
                            title: l.t('od.ab.deniedTitle'),
                            message: l.t('od.ab.deniedText')))
                  ],
                RunsPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                            key: const Key('ab-error'),
                            icon: Icons.error_outline,
                            title: l.t('od.loadFailedTitle'),
                            message: l.f('od.ab.loadFailed',
                                {'message': c.error ?? '-'}),
                            action: l.t('od.retry'),
                            onAction: c.load))
                  ],
                RunsPhase.ready when c.runs.isEmpty => [
                    AnumSurface(
                        child: AnumEmpty(
                            key: const Key('ab-empty'),
                            icon: Icons.travel_explore_outlined,
                            title: l.t('od.ab.emptyTitle'),
                            message: l.t('od.ab.emptyText'),
                            action: l.t('od.refresh'),
                            onAction: c.load))
                  ],
                RunsPhase.ready => [
                    for (final run in c.runs) _row(context, run),
                    if (c.moreError != null)
                      DeskMessage(
                          text: l
                              .f('od.ab.loadFailed', {'message': c.moreError!}),
                          failed: true),
                    if (c.nextOffset != null)
                      Center(
                        child: OutlinedButton(
                            key: const Key('ab-more'),
                            onPressed: c.loadingMore ? null : c.more,
                            child: Text(c.loadingMore
                                ? l.t('od.loading')
                                : l.t('od.loadMore'))),
                      ),
                  ],
              },
            ]),
          );
        },
      );

  Widget _row(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: Key('ab-run-${run.id}'),
      semanticLabel: '${run.goal}, ${l.t(run.statusKey)}',
      onTap: () async {
        await widget.openRun(context, run);
        if (mounted) await c.load();
      },
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(Icons.travel_explore_outlined, color: p.muted),
        const SizedBox(width: AnumSpacing.sm),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(run.goal,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
            const SizedBox(height: AnumSpacing.xxs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xxs,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  AnumPill(label: l.t(run.statusKey), tone: _tone(run)),
                  Text(l.f('od.ab.steps', {'count': run.stepCount}),
                      style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
                  Text(deskWhen(context, run.createdAt),
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                ]),
            if ((run.currentUrl ?? run.startUrl).isNotEmpty)
              Text(run.currentUrl ?? run.startUrl,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textDirection: TextDirection.ltr,
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          ]),
        ),
        Icon(Icons.chevron_right, color: p.muted),
      ]),
    );
  }
}

/// One browser run: status, sites, the step log, the approvals it waits for
/// and Stop. Refreshes every few seconds while it runs.
class BrowserRunScreen extends StatefulWidget {
  const BrowserRunScreen(
      {required this.controller,
      this.pollEvery = const Duration(seconds: 4),
      this.framePollEvery = const Duration(milliseconds: 1500),
      super.key});
  final BrowserRunController controller;

  /// How often an active run is read again; null turns it off (tests).
  final Duration? pollEvery;

  /// How often the live picture is read while a browser is open; null turns
  /// the timer off (tests call [BrowserRunController.loadFrame] themselves).
  final Duration? framePollEvery;

  @override
  State<BrowserRunScreen> createState() => _BrowserRunScreenState();
}

class _BrowserRunScreenState extends State<BrowserRunScreen> {
  BrowserRunController get c => widget.controller;
  Timer? _poll, _frames;
  final _typed = TextEditingController();

  @override
  void initState() {
    super.initState();
    c.load().then((_) => c.loadFrame());
    final every = widget.pollEvery;
    if (every != null) {
      _poll = Timer.periodic(every, (_) {
        if (mounted && (c.run?.active ?? false) && c.busy == null) c.load();
      });
    }
    final frames = widget.framePollEvery;
    if (frames != null) {
      _frames = Timer.periodic(frames, (_) {
        final run = c.run;
        if (mounted && run != null && run.live && run.canWatch) c.loadFrame();
      });
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    _frames?.cancel();
    _typed.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: c,
        builder: (context, _) {
          final l = AnumLocalizations.of(context);
          final p = context.palette;
          final run = c.run;
          return Scaffold(
            appBar: AppBar(title: Text(l.t('od.ab.runTitle')), actions: [
              IconButton(
                  key: const Key('ab-run-refresh'),
                  tooltip: l.t('od.refresh'),
                  onPressed: c.busy != null ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              if (c.noteKey != null)
                DeskMessage(
                    key: const Key('ab-note'),
                    text: c.noteRaw != null && c.noteKey!.startsWith('od.')
                        ? l.f(c.noteKey!, {'message': c.noteRaw!})
                        : l.t(c.noteKey!),
                    failed: c.noteFailed),
              ...switch (c.phase) {
                RunPhase.loading when run == null => [
                    DeskLoading(label: l.t('ab.loadingLive'))
                  ],
                RunPhase.notFound => [
                    AnumSurface(
                        child: AnumEmpty(
                            key: const Key('ab-run-gone'),
                            icon: Icons.search_off,
                            title: l.t('od.ab.notFound'),
                            message: c.error ?? '-'))
                  ],
                RunPhase.error when run == null => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                            key: const Key('ab-run-error'),
                            icon: Icons.error_outline,
                            title: l.t('od.loadFailedTitle'),
                            message:
                                l.f('ab.liveFailed', {'error': c.error ?? '-'}),
                            action: l.t('ab.retry'),
                            onAction: c.load))
                  ],
                _ => [
                    // A failed refresh keeps the last answer and says so.
                    if (c.phase == RunPhase.error)
                      DeskMessage(
                          text: l.f('ab.liveFailed', {'error': c.error ?? '-'}),
                          failed: true),
                    ..._detail(context, run!),
                  ],
              },
            ]),
          );
        },
      );

  List<Widget> _detail(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return [
      AnumSurface(
        key: const Key('ab-run-head'),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xxs,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Semantics(
                    liveRegion: true,
                    child:
                        AnumPill(label: l.t(run.statusKey), tone: _tone(run))),
                Text(
                    l.f('od.ab.started',
                        {'when': deskWhen(context, run.createdAt)}),
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ]),
          const SizedBox(height: AnumSpacing.xs),
          Text(run.goal,
              style: TextStyle(
                  color: p.text,
                  fontSize: AnumType.base,
                  fontWeight: FontWeight.w800)),
          if ((run.currentUrl ?? '').isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.xxs),
            Text(run.currentUrl!,
                textDirection: TextDirection.ltr,
                style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
          ],
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('ab.sites'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          const SizedBox(height: AnumSpacing.xxs),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            for (final site in run.sites)
              Directionality(
                  textDirection: TextDirection.ltr,
                  child: AnumPill(label: site)),
          ]),
          if (run.savedItems > 0) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(l.f('od.ab.savedItems', {'count': run.savedItems}),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          ],
          if (run.status == 'completed' && run.message.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(l.t('ab.answerTitle'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
            SelectableText(run.message, style: TextStyle(color: p.text)),
          ] else if ((run.status == 'failed' || run.status == 'stopped') &&
              run.message.isNotEmpty) ...[
            const SizedBox(height: AnumSpacing.sm),
            Text(run.message,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          ],
          if (run.canStop) ...[
            const SizedBox(height: AnumSpacing.sm),
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton.icon(
                  key: const Key('ab-stop'),
                  style: OutlinedButton.styleFrom(foregroundColor: p.stop),
                  onPressed: c.busy != null
                      ? null
                      : () async {
                          if (await deskConfirm(context,
                              title: l.t('ab.stopAsk'),
                              text: run.goal,
                              confirm: l.t('ab.stopYes'),
                              cancel: l.t('ab.cancel'),
                              danger: true,
                              confirmKey: const Key('ab-stop-confirm'))) {
                            await c.stop();
                          }
                        },
                  icon: const Icon(Icons.stop_circle_outlined),
                  label: Text(
                      c.busy == 'stop' ? l.t('ab.stopping') : l.t('ab.stop'))),
            ),
          ],
        ]),
      ),
      if (run.active) _picture(context, run),
      if (run.needsYou && run.canWatch) _handoff(context, run),
      if (run.active) _approvals(context, run),
      _steps(context, run),
      AnumSurface(
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.desktop_windows_outlined, size: 18, color: p.muted),
          const SizedBox(width: AnumSpacing.sm),
          Expanded(
              child: Text(l.t('od.ab.webOnly'),
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
        ]),
      ),
    ];
  }

  /// The live picture (masked by the browser service, held in memory only).
  /// While the run needs the owner, a tap on it is a click on the page.
  Widget _picture(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    Widget body;
    if (!run.canWatch) {
      body = Text(l.t('ab.pictureMembers'),
          style: TextStyle(color: p.muted, fontSize: AnumType.sm));
    } else if (!run.live) {
      body = Text(l.t('ab.noPicture'),
          style: TextStyle(color: p.muted, fontSize: AnumType.sm));
    } else {
      final bytes = c.picture;
      final label = l.f('ab.pictureAlt', {'url': run.currentUrl ?? ''});
      final image = AspectRatio(
        aspectRatio: browserFrameWidth / browserFrameHeight,
        child: bytes == null
            ? Center(
                child: Text(
                    l.t(c.pictureFailed
                        ? 'ab.pictureFailed'
                        : 'ab.loadingLive'),
                    style: TextStyle(color: p.muted, fontSize: AnumType.sm)))
            : Image.memory(bytes,
                key: const Key('ab-picture'),
                gaplessPlayback: true,
                fit: BoxFit.contain,
                semanticLabel: label),
      );
      body = Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(AnumRadius.md),
          child: DecoratedBox(
            decoration: BoxDecoration(border: Border.all(color: p.line)),
            child: run.needsYou && bytes != null
                ? Semantics(
                    button: true,
                    label: label,
                    child: LayoutBuilder(
                      builder: (context, box) => GestureDetector(
                        key: const Key('ab-picture-tap'),
                        onTapUp: (d) {
                          final point = browserPagePoint(
                              d.localPosition.dx,
                              d.localPosition.dy,
                              box.maxWidth,
                              box.maxWidth *
                                  browserFrameHeight /
                                  browserFrameWidth);
                          if (point != null) {
                            c.send(
                                {'kind': 'click', 'x': point.x, 'y': point.y});
                          }
                        },
                        child: image,
                      ),
                    ),
                  )
                : image,
          ),
        ),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('ab.pictureMasked'),
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
      ]);
    }
    return AnumSurface(
      key: const Key('ab-live'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('ab.liveTitle'), icon: Icons.desktop_windows_outlined),
        const SizedBox(height: AnumSpacing.sm),
        body,
      ]),
    );
  }

  /// "Needs you": the owner types on the page and presses keys, then
  /// approves the step to let the agent continue.
  Widget _handoff(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    void sendText() {
      final text = _typed.text;
      if (text.isEmpty) return;
      c.send({'kind': 'text', 'text': text});
      _typed.clear();
      setState(() {});
    }

    return AnumSurface(
      key: const Key('ab-handoff'),
      accent: p.warn,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('ab.handoffTitle'), icon: Icons.back_hand_outlined),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('ab.handoffText'),
            style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.sm),
        TextField(
          key: const Key('ab-type'),
          controller: _typed,
          maxLength: 200,
          autocorrect: false,
          enableSuggestions: false,
          onChanged: (_) => setState(() {}),
          onSubmitted: (_) => sendText(),
          decoration: InputDecoration(labelText: l.t('ab.typeHere')),
        ),
        Align(
          alignment: AlignmentDirectional.centerStart,
          child: FilledButton(
              key: const Key('ab-type-send'),
              onPressed: _typed.text.isEmpty ? null : sendText,
              child: Text(l.t('ab.sendText'))),
        ),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          for (final key in browserOwnerKeys)
            OutlinedButton(
                key: Key('ab-key-$key'),
                onPressed: () => c.send({'kind': 'key', 'key': key}),
                child: Text(l.f('ab.key', {'key': key}))),
        ]),
        if (c.inputError != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          DeskMessage(
              key: const Key('ab-input-error'),
              text: l.f('ab.inputFailed', {'error': c.inputError!}),
              failed: true),
        ],
      ]),
    );
  }

  Widget _approvals(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    return AnumSurface(
      key: const Key('ab-approvals'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('od.ab.waitingTitle'),
            count: c.approvals.length,
            icon: Icons.approval_outlined),
        const SizedBox(height: AnumSpacing.sm),
        if (c.approvalsError != null)
          DeskMessage(
              text:
                  l.f('od.ab.approvalsFailed', {'message': c.approvalsError!}),
              failed: true)
        else if (c.approvals.isEmpty)
          Text(l.t('od.ab.noApprovals'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final a in c.approvals) _approval(context, run, a),
      ]),
    );
  }

  Widget _approval(BuildContext context, BrowserRun run, WorkspaceApproval a) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final facts = BrowserStepFacts.of(a);
    // The card security code is typed on the web; a "Needs you" step is
    // done here by an owner with the picture and input above.
    final webOnly = a.needsCardCodeOnWeb || (facts.handoff && !run.canWatch);
    return Container(
      key: Key('ab-approval-${a.id}'),
      margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
      padding: const EdgeInsets.all(AnumSpacing.sm),
      decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(AnumRadius.lg),
          border: Border.all(color: p.line)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(l.t(facts.kindKey),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w800)),
        if (a.reason.isNotEmpty)
          Text(a.reason,
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        const SizedBox(height: AnumSpacing.xs),
        if (facts.site.isNotEmpty)
          DeskFact(label: l.t('ab.approvalSite'), value: facts.site, ltr: true),
        if (facts.url.isNotEmpty)
          DeskFact(
              label: l.t('ab.approvalAddress'), value: facts.url, ltr: true),
        if (facts.target.isNotEmpty)
          DeskFact(label: l.t('ab.approvalAction'), value: facts.target),
        if (facts.amount.isNotEmpty) ...[
          DeskFact(
              label: l.t('ab.approvalAmount'), value: facts.amount, ltr: true),
          Text(l.t('ab.amountNote'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ],
        if (facts.kindKey == 'ab.kind_scope')
          Text(l.t('ab.approvalScope'),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        if (a.payloadHash != null) ...[
          const SizedBox(height: AnumSpacing.xs),
          DeskHash(label: l.t('od.ab.hash'), hash: a.payloadHash!),
        ],
        if (webOnly) ...[
          const SizedBox(height: AnumSpacing.xs),
          Row(
              key: Key('ab-web-only-${a.id}'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                    a.needsCardCodeOnWeb
                        ? Icons.credit_card
                        : Icons.back_hand_outlined,
                    size: 16,
                    color: p.warn),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(
                    child: Text(
                        l.t(a.needsCardCodeOnWeb
                            ? 'od.ab.cardCodeWeb'
                            : 'od.ab.handoffWeb'),
                        style:
                            TextStyle(color: p.text, fontSize: AnumType.sm))),
              ]),
        ],
        const SizedBox(height: AnumSpacing.sm),
        Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
          if (!webOnly)
            FilledButton(
                key: Key('ab-approve-${a.id}'),
                onPressed: c.busy != null || !a.canApprove()
                    ? null
                    : () async {
                        if (await deskConfirm(context,
                            title: l.t('od.ab.approveTitle'),
                            text: l.t('od.ab.approveText'),
                            extra: a.payloadHash == null
                                ? null
                                : DeskHash(
                                    label: l.t('od.ab.hash'),
                                    hash: a.payloadHash!),
                            confirm: l.t('od.ab.approve'),
                            cancel: l.t('ab.cancel'),
                            confirmKey: const Key('ab-approve-confirm'))) {
                          await c.decide(a, approve: true);
                        }
                      },
                child: Text(l.t('od.ab.approve'))),
          OutlinedButton(
              key: Key('ab-reject-${a.id}'),
              onPressed:
                  c.busy != null ? null : () => c.decide(a, approve: false),
              child: Text(l.t('od.ab.reject'))),
        ]),
      ]),
    );
  }

  Widget _steps(BuildContext context, BrowserRun run) {
    final l = AnumLocalizations.of(context);
    final p = context.palette;
    final arabic = l.isArabic;
    return AnumSurface(
      key: const Key('ab-steps'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        DeskSectionTitle(
            title: l.t('ab.stepsTitle'),
            count: run.steps.length,
            icon: Icons.format_list_numbered),
        const SizedBox(height: AnumSpacing.sm),
        if (run.steps.isEmpty)
          Text(l.t('ab.noSteps'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
        for (final step in run.steps)
          Padding(
            key: Key('ab-step-${step.seq}'),
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              SizedBox(
                width: 28,
                child: Text('${step.seq}',
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ),
              Expanded(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Text(browserStepText(step, arabic: arabic),
                          style:
                              TextStyle(color: p.text, fontSize: AnumType.sm)),
                      if (step.at != null)
                        Text(deskWhen(context, step.at),
                            style: TextStyle(
                                color: p.faint, fontSize: AnumType.xs)),
                    ]),
              ),
            ]),
          ),
      ]),
    );
  }
}
