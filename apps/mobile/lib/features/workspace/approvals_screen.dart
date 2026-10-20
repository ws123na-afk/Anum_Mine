import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'approvals_paging.dart';
import 'list_kit.dart';
import 'tasks_screen.dart';
import 'work_status.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';

/// Approvals as a designed surface: risk first, the exact action, why it was
/// stopped, and which task asked. Approving always needs a deliberate confirm.
/// The queue and its history are paged: pending first, filter tabs, search and
/// "Load more" (docs/approvals-and-risk.md#paging-filters-and-search).
class ApprovalsScreen extends StatefulWidget {
  const ApprovalsScreen({required this.controller, super.key});
  final WorkspaceController controller;

  @override
  State<ApprovalsScreen> createState() => _ApprovalsScreenState();
}

class _ApprovalsScreenState extends State<ApprovalsScreen> {
  late final ApprovalsListController _list;
  final _search = TextEditingController();
  Timer? _debounce;
  String _signal = '';

  static int _riskRank(String risk) => switch (risk.toLowerCase()) {
        'blocked' || 'critical' => 0,
        'high' => 1,
        'medium' => 2,
        _ => 3,
      };

  /// What the shell knows about the queue; a change means an approval event
  /// or a decision happened, so the first page is reloaded quietly.
  String _signalOf(WorkspaceController c) => c.approvals
      .map((a) => '${a.id}:${a.status}:${a.approvers.length}')
      .join(',');

  @override
  void initState() {
    super.initState();
    final repository = widget.controller.repository;
    _list = ApprovalsListController(repository is ApprovalsPagingRepository
        ? repository as ApprovalsPagingRepository
        : LocalApprovalsPaging(() => widget.controller.approvals));
    _signal = _signalOf(widget.controller);
    widget.controller.addListener(_onShell);
    _list.load();
  }

  void _onShell() {
    final next = _signalOf(widget.controller);
    if (next == _signal) return;
    _signal = next;
    _list.load(quiet: true);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onShell);
    _debounce?.cancel();
    _search.dispose();
    _list.dispose();
    super.dispose();
  }

  void _onSearchChanged(String value) {
    setState(() {});
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 300), () {
      _list.setQuery(value);
    });
  }

  void _clearSearch() {
    _debounce?.cancel();
    _search.clear();
    setState(() {});
    _list.setQuery('');
  }

  Future<void> _refresh() async {
    await widget.controller.load();
    await _list.load(quiet: true);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: Listenable.merge([widget.controller, _list]),
        builder: (context, _) {
          final l = AnumLocalizations.maybeOf(context);
          final p = context.palette;
          final now = DateTime.now();
          final pending = _list.items
              .where((a) => a.effectiveStatus(now) == 'pending')
              .toList()
            ..sort((a, b) {
              final byRisk = _riskRank(a.risk).compareTo(_riskRank(b.risk));
              return byRisk != 0 ? byRisk : a.createdAt.compareTo(b.createdAt);
            });
          final decided = _list.items
              .where((a) => a.effectiveStatus(now) != 'pending')
              .toList();
          final summary = _list.summary;
          String count(int? value) => value == null ? '-' : '$value';
          final searching = _list.query.isNotEmpty || _search.text.isNotEmpty;
          final ready = _list.phase == ApprovalsPhase.ready;

          Widget noMatch() => AnumSurface(
                child: AnumEmpty(
                  icon: Icons.search_off,
                  title: l.f('apv.empty.searchTitle', {'query': _list.query}),
                  message: l.t('apv.empty.searchText'),
                  action: l.t('apv.search.clear'),
                  onAction: _clearSearch,
                ),
              );

          return AnumPage(
            onRefresh: _refresh,
            children: [
              AnumHeader(
                eyebrow: l.t('apv.eyebrow'),
                title: l.t('apv.title'),
                subtitle: l.t('apv.subtitle'),
                large: true,
              ),
              AnumMetricGrid(children: [
                AnumMetric(
                  label: l.t('apv.metric.waiting'),
                  value: count(summary?.pending),
                  icon: Icons.front_hand_outlined,
                  accent: (summary?.pending ?? 0) == 0 ? null : p.warn,
                ),
                AnumMetric(
                    label: l.t('apv.metric.approved'),
                    value: count(summary?.approved),
                    icon: Icons.check_circle_outline),
                AnumMetric(
                    label: l.t('apv.metric.rejected'),
                    value: count(summary?.rejected),
                    icon: Icons.block),
                AnumMetric(
                  label: l.t('apv.metric.total'),
                  value: count(summary?.total),
                  icon: Icons.fact_check_outlined,
                ),
              ]),
              Semantics(
                container: true,
                label: l.t('apv.filter.label'),
                child: AnumSegmentTabs<ApprovalFilter>(
                  selected: _list.filter,
                  onSelected: _list.setFilter,
                  segments: [
                    AnumSegment(ApprovalFilter.all, l.t('apv.filter.all'),
                        key: const Key('approvals-filter-all')),
                    AnumSegment(
                        ApprovalFilter.pending, l.t('apv.filter.pending'),
                        key: const Key('approvals-filter-pending')),
                    AnumSegment(
                        ApprovalFilter.decided, l.t('apv.filter.decided'),
                        key: const Key('approvals-filter-decided')),
                  ],
                ),
              ),
              TextField(
                key: const Key('approvals-search'),
                controller: _search,
                textInputAction: TextInputAction.search,
                decoration: InputDecoration(
                  labelText: l.t('apv.search.label'),
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: _search.text.isEmpty
                      ? null
                      : IconButton(
                          tooltip: l.t('apv.search.clear'),
                          icon: const Icon(Icons.close),
                          onPressed: _clearSearch),
                ),
                onChanged: _onSearchChanged,
                onSubmitted: (value) {
                  _debounce?.cancel();
                  _list.setQuery(value);
                },
              ),
              if (_list.phase == ApprovalsPhase.loading)
                Center(
                    child: Padding(
                        padding: const EdgeInsets.all(24),
                        child: CircularProgressIndicator(
                            semanticsLabel: l.t('apv.loading')))),
              if (_list.phase == ApprovalsPhase.denied)
                AnumSurface(
                    child: AnumEmpty(
                        icon: Icons.lock_outline,
                        title: l.t('apv.denied.title'),
                        message: l.t('apv.denied.text'))),
              if (_list.phase == ApprovalsPhase.error)
                AnumSurface(
                    child: AnumEmpty(
                        icon: Icons.cloud_off_outlined,
                        title: l.t('apv.error.title'),
                        message: l.f('apv.error.text',
                            {'reason': _list.error ?? 'The request failed.'}),
                        action: l.t('apv.retry'),
                        onAction: _list.load)),
              if (ready && _list.filter != ApprovalFilter.decided) ...[
                if (pending.isEmpty)
                  searching
                      ? noMatch()
                      : AnumSurface(
                          child: AnumEmpty(
                            icon: Icons.verified_outlined,
                            title: l.t('apv.empty.pendingTitle'),
                            message: l.t('apv.empty.pendingText'),
                          ),
                        )
                else
                  for (final approval in pending)
                    PendingApprovalCard(
                        key: ValueKey('pending-${approval.id}'),
                        approval: approval,
                        controller: widget.controller,
                        onDecided: () => _list.load(quiet: true)),
              ],
              if (ready &&
                  _list.filter == ApprovalFilter.decided &&
                  decided.isEmpty)
                searching
                    ? noMatch()
                    : AnumSurface(
                        child: AnumEmpty(
                          icon: Icons.history,
                          title: l.t('apv.empty.decidedTitle'),
                          message: l.t('apv.empty.decidedText'),
                        ),
                      ),
              if (ready &&
                  _list.filter != ApprovalFilter.pending &&
                  decided.isNotEmpty)
                AnumSurface(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        AnumHeader(
                            eyebrow: l.t('apv.history.eyebrow'),
                            title: l.t('apv.history.title')),
                        const SizedBox(height: 6),
                        for (final approval in decided)
                          AnumRow(
                            icon: approval.effectiveStatus() == 'approved'
                                ? Icons.check
                                : Icons.block,
                            tone: approval.effectiveStatus() == 'approved'
                                ? AnumTone.ok
                                : AnumTone.stop,
                            title: approvalTitle(
                                approval, widget.controller.tasks),
                            subtitle: 'Tool: ${approval.action}',
                            detail: [
                              decisionSummary(approval),
                              if (approval.decisionReason != null)
                                'Reason: ${approval.decisionReason}',
                            ].join('\n'),
                            trailing: AnumPill(
                                label: approval.risk,
                                tone: riskTone(approval.risk)),
                          ),
                      ]),
                ),
              if (ready && _list.items.isNotEmpty) _MoreFooter(list: _list),
              const SizedBox(height: AnumSpacing.lg),
            ],
          );
        },
      );
}

/// "N of M shown" and the Load more button of the paged list.
class _MoreFooter extends StatelessWidget {
  const _MoreFooter({required this.list});
  final ApprovalsListController list;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final total = list.summary?.totalFor(list.filter);
    final shown = list.items.length;
    final text = total == null
        ? l.f('apv.shownUnknown', {'shown': shown})
        : l.f('apv.shown',
            {'shown': shown, 'total': total < shown ? shown : total});
    return Column(children: [
      Semantics(
          liveRegion: true,
          child: Text(text,
              style: TextStyle(color: p.muted, fontSize: AnumType.sm))),
      if (list.pagingError != null) ...[
        const SizedBox(height: 6),
        Semantics(
            liveRegion: true,
            child: Text(l.f('apv.loadMoreError', {'reason': list.pagingError!}),
                style: TextStyle(color: p.warn, fontSize: AnumType.sm))),
      ],
      if (list.hasMore) ...[
        const SizedBox(height: 8),
        OutlinedButton(
          key: const Key('approvals-load-more'),
          onPressed: list.paging ? null : list.loadMore,
          child: list.paging
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2))
              : Text(l.t('apv.loadMore')),
        ),
      ],
    ]);
  }
}

/// One pending approval with everything needed to decide it: the exact
/// arguments, the payload hash that is sent back, the reason and the
/// Approve (with a confirm) and Reject buttons. Shown in Approvals and on the
/// task that is waiting for it.
class PendingApprovalCard extends StatefulWidget {
  const PendingApprovalCard(
      {required this.approval,
      required this.controller,
      this.showTask = true,
      this.onDecided,
      super.key});
  final WorkspaceApproval approval;
  final WorkspaceController controller;

  /// Whether to link to the task that asked (off on that task's own screen).
  final bool showTask;

  /// Called after a decision was sent, whatever the API answered.
  final VoidCallback? onDecided;

  @override
  State<PendingApprovalCard> createState() => _PendingCardState();
}

class _PendingCardState extends State<PendingApprovalCard> {
  final _reason = TextEditingController();

  WorkspaceApproval get approval => widget.approval;
  WorkspaceController get controller => widget.controller;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final tone = riskTone(approval.risk);
    final task =
        controller.tasks.where((t) => t.id == approval.taskId).firstOrNull;
    final alreadyApproved = approval.approvedBy(controller.currentUserId);
    return AnumSurface(
      accent: anumToneColor(context, tone),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // Wraps on a narrow phone or with large text instead of overflowing.
        Wrap(
            spacing: 8,
            runSpacing: 4,
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              AnumPill(
                  label: '${approval.risk} risk',
                  tone: tone,
                  icon: Icons.shield_outlined),
              Text(relativeTime(approval.createdAt),
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            ]),
        const SizedBox(height: 10),
        Text(approvalTitle(approval, controller.tasks),
            style: TextStyle(
                color: p.text,
                fontSize: AnumType.lgPlus,
                fontWeight: FontWeight.w700)),
        const SizedBox(height: 2),
        Text('Tool: ${approval.action}',
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        if (approval.progressLabel != null) ...[
          const SizedBox(height: 8),
          ApprovalProgress(approval: approval),
          if (alreadyApproved)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                  'You approved this. It needs ${_remaining(approval)} more '
                  'approval${_remaining(approval) == 1 ? '' : 's'} from other '
                  'people before it runs.',
                  style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
            ),
        ],
        if (approval.target != null) ...[
          const SizedBox(height: 2),
          Text('Sends to ${approval.target}',
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        ],
        const SizedBox(height: 8),
        Text('Exactly what it will send',
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        const SizedBox(height: 4),
        ApprovalArgumentsTable(approval: approval),
        const SizedBox(height: 6),
        Text(
            'Payload hash ${shortHash(approval.payloadHash)}'
            '${expiryLabel(approval).isEmpty ? '' : ' · ${expiryLabel(approval)}'}',
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        if (approval.payloadHash == null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
                'Not bound to a payload hash; it cannot be approved. Run the task again.',
                style: TextStyle(color: p.stop, fontSize: AnumType.xs)),
          ),
        const SizedBox(height: 6),
        Text('Why it stopped',
            style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        const SizedBox(height: 2),
        Text(
            approvalReason(approval, approvalTitle(approval, controller.tasks)),
            style: TextStyle(color: p.muted, fontSize: AnumType.md)),
        if (task != null && widget.showTask) ...[
          const SizedBox(height: 10),
          InkWell(
            borderRadius: BorderRadius.circular(10),
            onTap: () => openTaskDetail(context, controller, task),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(children: [
                Icon(Icons.task_alt, size: 16, color: p.sky),
                const SizedBox(width: 6),
                Expanded(
                  child: Text('From task: ${task.title}',
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          color: p.sky,
                          fontSize: AnumType.sm,
                          fontWeight: FontWeight.w600)),
                ),
              ]),
            ),
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          controller: _reason,
          maxLength: approvalReasonMaxChars,
          minLines: 1,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'Reason (optional)',
            hintText: 'Why you approve or reject this; kept in the audit trail',
          ),
        ),
        if (controller.decisionRefusals[approval.id] case final refusal?)
          Container(
            key: Key('approval-refusal-${approval.id}'),
            margin: const EdgeInsets.only(top: 4, bottom: 4),
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: p.stop.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: p.stop.withValues(alpha: 0.45)),
            ),
            child: Semantics(
              liveRegion: true,
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Icon(Icons.person_off_outlined, size: 18, color: p.stop),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Decision refused',
                            style: TextStyle(
                                color: p.stop,
                                fontSize: AnumType.sm,
                                fontWeight: FontWeight.w700)),
                        const SizedBox(height: 2),
                        Text(refusal,
                            style: TextStyle(
                                color: p.text, fontSize: AnumType.sm)),
                      ]),
                ),
              ]),
            ),
          ),
        const SizedBox(height: 8),
        Wrap(
            spacing: 10,
            runSpacing: 8,
            alignment: WrapAlignment.end,
            children: [
              OutlinedButton.icon(
                onPressed:
                    controller.mutating ? null : () => _decide(approve: false),
                icon: const Icon(Icons.close),
                label: const Text('Reject'),
              ),
              FilledButton.icon(
                onPressed: controller.mutating ||
                        !approval.canApprove() ||
                        alreadyApproved
                    ? null
                    : () => _confirmApprove(context),
                icon: const Icon(Icons.check),
                label: const Text('Approve'),
              ),
            ]),
      ]),
    );
  }

  Future<void> _confirmApprove(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Approve this action?'),
        content: Text(
            'The agent will now call ${approval.action} with the arguments shown '
            '(payload hash ${shortHash(approval.payloadHash)})'
            '${approval.target == null ? '' : ' and send them to ${approval.target}'}.\n\n'
            'Risk: ${approval.risk}. If the call changes before it runs, it is '
            'not executed. This is recorded in the audit log.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Not yet')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Approve')),
        ],
      ),
    );
    if (ok == true) await _decide(approve: true);
  }

  Future<void> _decide({required bool approve}) async {
    await controller.decide(approval,
        approve: approve, reason: normalizeReason(_reason.text));
    widget.onDecided?.call();
  }
}

int _remaining(WorkspaceApproval approval) {
  final left = approval.requiredApprovals - approval.approvers.length;
  return left < 1 ? 1 : left;
}

/// "1 of 3 approvals" with a bar and who approved, for approvals that need
/// several people (approval chains, docs/approvals-and-risk.md).
class ApprovalProgress extends StatelessWidget {
  const ApprovalProgress({required this.approval, super.key});
  final WorkspaceApproval approval;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final label = approval.progressLabel;
    if (label == null) return const SizedBox.shrink();
    final names = approval.approvers.map((a) => a.userId).toList();
    final total = approval.requiredApprovals > names.length
        ? approval.requiredApprovals
        : names.length;
    return Semantics(
      key: Key('approval-progress-${approval.id}'),
      label: 'Approval progress: $label',
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.groups_outlined, size: 16, color: p.sky),
          const SizedBox(width: 6),
          Flexible(
            child: Text(label,
                style: TextStyle(
                    color: p.text,
                    fontSize: AnumType.sm,
                    fontWeight: FontWeight.w700)),
          ),
        ]),
        const SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
              value: total == 0 ? 0 : names.length / total, minHeight: 6),
        ),
        const SizedBox(height: 4),
        Text(
            names.isEmpty
                ? 'No approvals yet'
                : 'Approved by ${joinNames(names)}',
            style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
      ]),
    );
  }
}

/// The exact tool arguments as key/value rows; redacted values are marked.
class ApprovalArgumentsTable extends StatelessWidget {
  const ApprovalArgumentsTable({required this.approval, super.key});
  final WorkspaceApproval approval;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final rows = approvalArguments(approval.arguments);
    if (rows.isEmpty) {
      return Text('No arguments.',
          style: TextStyle(color: p.muted, fontSize: AnumType.sm));
    }
    return Container(
      decoration: BoxDecoration(
        border: Border.all(color: p.line),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(children: [
        for (final (index, row) in rows.indexed)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
            decoration: index == 0
                ? null
                : BoxDecoration(border: Border(top: BorderSide(color: p.line))),
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(row.key,
                  style: TextStyle(
                      color: p.faint,
                      fontSize: AnumType.xxs,
                      fontFamily: 'monospace')),
              const SizedBox(height: 2),
              SelectableText(row.value,
                  style: TextStyle(
                      color: row.redacted ? p.warn : p.text,
                      fontSize: AnumType.sm,
                      fontFamily: 'monospace')),
            ]),
          ),
      ]),
    );
  }
}

/// Short form of the payload hash for display.
String shortHash(String? hash) => hash == null || hash.length < 18
    ? (hash ?? 'not bound')
    : '${hash.substring(0, 12)}…${hash.substring(hash.length - 6)}';

/// "Expires in 3 h" for a pending approval, "Expired" once it lapsed.
String expiryLabel(WorkspaceApproval approval, {DateTime? now}) {
  final expiresAt = approval.expiresAt;
  if (approval.status != 'pending' || expiresAt == null) return '';
  final remaining = expiresAt.difference(now ?? DateTime.now());
  if (remaining <= Duration.zero) return 'Expired';
  if (remaining.inMinutes < 60) {
    return 'Expires in ${remaining.inMinutes + 1} min';
  }
  return remaining.inHours < 48
      ? 'Expires in ${remaining.inHours} h'
      : 'Expires in ${remaining.inDays} d';
}

/// History line: who decided and when, or how it ended without a decision.
String decisionSummary(WorkspaceApproval approval, {DateTime? now}) {
  final status = approval.effectiveStatus(now);
  final at = approval.decidedAt;
  if (status == 'approved' || status == 'rejected') {
    final verb = status == 'approved' ? 'Approved' : 'Rejected';
    final names = approval.approvers.map((a) => a.userId).toList();
    final who = status == 'approved' && names.length > 1
        ? joinNames(names)
        : approval.decidedBy ?? 'unknown user';
    return '$verb by $who'
        '${at == null ? '' : ' · ${exactTime(at)}'}';
  }
  if (status == 'expired') {
    final when = at ?? approval.expiresAt;
    return 'Expired without a decision'
        '${when == null ? '' : ' · ${exactTime(when)}'}';
  }
  return '';
}
