import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'admin_controller.dart';
import 'admin_models.dart';

/// The workspace audit log on the phone (docs/governance-and-scale.md#audit-log):
/// read-only, newest first, filtered by text, action and date range, paged by
/// the API's cursor, with a detail sheet per record. Filtered CSV/JSON export
/// is in the web app; the Organization screen exports the whole log.
class AuditLogScreen extends StatefulWidget {
  const AuditLogScreen({required this.controller, super.key});
  final AuditLogController controller;

  @override
  State<AuditLogScreen> createState() => _AuditLogScreenState();
}

class _AuditLogScreenState extends State<AuditLogScreen> {
  final search = TextEditingController();
  String action = '';
  DateTimeRange? range;

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == AdminPhase.initial) widget.controller.load();
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  AuditFilter get _filter => AuditFilter(
        text: search.text,
        action: action,
        since: range?.start,
        // The picked end day is inclusive; the API's `until` is exclusive.
        until: range == null
            ? null
            : DateTime(range!.end.year, range!.end.month, range!.end.day + 1),
      );

  void _apply() => widget.controller.load(_filter);

  void _reset() {
    search.clear();
    setState(() {
      action = '';
      range = null;
    });
    widget.controller.load(const AuditFilter());
  }

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 10),
      lastDate: DateTime(now.year, now.month, now.day),
      initialDateRange: range,
      helpText: 'Records between',
    );
    if (picked == null) return;
    setState(() => range = picked);
    _apply();
  }

  String _day(DateTime value) =>
      '${value.year}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

  void _open(AuditRecord record) => showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (_) => _AuditDetail(record: record),
      );

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final p = context.palette;
          final actions = {...c.actions, if (action.isNotEmpty) action}.toList()
            ..sort();
          return Scaffold(
            appBar: AppBar(title: const Text('Audit log'), actions: [
              IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.phase == AdminPhase.loading ? null : c.load,
                  icon: const Icon(Icons.refresh)),
            ]),
            body: AnumPage(onRefresh: c.load, children: [
              const AnumHeader(
                eyebrow: 'Security',
                title: 'Audit log',
                subtitle:
                    'Changes to members, invitations, policies, budgets and settings: who, when and to what.',
                large: true,
              ),
              AnumSurface(
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        key: const Key('audit-search'),
                        controller: search,
                        autocorrect: false,
                        textInputAction: TextInputAction.search,
                        onSubmitted: (_) => _apply(),
                        decoration: InputDecoration(
                          labelText: 'Search',
                          hintText: 'Actor, action or target',
                          prefixIcon: const Icon(Icons.search),
                          suffixIcon: IconButton(
                              tooltip: 'Apply search',
                              onPressed: _apply,
                              icon: const Icon(Icons.arrow_forward)),
                        ),
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      DropdownButtonFormField<String>(
                        key: const Key('audit-action'),
                        initialValue: action,
                        isExpanded: true,
                        decoration: const InputDecoration(labelText: 'Action'),
                        items: [
                          const DropdownMenuItem(
                              value: '', child: Text('Any action')),
                          for (final value in actions)
                            DropdownMenuItem(
                                value: value,
                                child: Text(auditActionLabel(value),
                                    overflow: TextOverflow.ellipsis)),
                        ],
                        onChanged: (value) {
                          setState(() => action = value ?? '');
                          _apply();
                        },
                      ),
                      const SizedBox(height: AnumSpacing.sm),
                      Wrap(spacing: 8, runSpacing: 8, children: [
                        OutlinedButton.icon(
                          onPressed: _pickRange,
                          icon: const Icon(Icons.date_range),
                          label: Text(range == null
                              ? 'Any date'
                              : '${_day(range!.start)} to ${_day(range!.end)}'),
                        ),
                        if (!_filter.isEmpty)
                          TextButton(
                              onPressed: _reset,
                              child: const Text('Reset filters')),
                      ]),
                    ]),
              ),
              ...switch (c.phase) {
                AdminPhase.initial || AdminPhase.loading => [
                    const Padding(
                        padding: EdgeInsets.all(AnumSpacing.lg),
                        child: Center(child: CircularProgressIndicator()))
                  ],
                AdminPhase.forbidden => [
                    AnumSurface(
                        accent: p.warn,
                        child: AnumEmpty(
                          icon: Icons.lock_outline,
                          title:
                              'Owner or organization administrator access required',
                          message:
                              'Only this workspace\'s owners and your organization\'s administrators can read the audit log. '
                              'Ask a workspace owner to show you a record or to change your role.\n'
                              'The API answered: ${c.loadMessage}',
                        )),
                  ],
                AdminPhase.offline || AdminPhase.error => [
                    AnumSurface(
                        accent: p.stop,
                        child: AnumEmpty(
                          icon: c.phase == AdminPhase.offline
                              ? Icons.cloud_off_outlined
                              : Icons.error_outline,
                          title: 'The audit log could not be loaded',
                          message: c.loadMessage ?? 'Try again.',
                          action: 'Retry',
                          onAction: c.load,
                        )),
                  ],
                AdminPhase.ready => [
                    AnumSurface(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            AnumHeader(
                                title: c.records.length == 1
                                    ? '1 record'
                                    : '${c.records.length} records${c.nextCursor == null ? '' : ' loaded'}',
                                subtitle: 'Newest first.'),
                            const SizedBox(height: 6),
                            if (c.records.isEmpty)
                              c.filter.isEmpty
                                  ? const AnumEmpty(
                                      icon: Icons.receipt_long_outlined,
                                      title: 'Nothing recorded yet',
                                      message:
                                          'Changes to members, invitations, policies, budgets and settings appear here as they happen.')
                                  : AnumEmpty(
                                      icon: Icons.search_off,
                                      title: 'No records match these filters',
                                      message:
                                          'Widen the date range or clear a filter.',
                                      action: 'Reset filters',
                                      onAction: _reset),
                            for (final record in c.records)
                              AnumRow(
                                key: Key('audit-${record.id}'),
                                icon: Icons.receipt_long_outlined,
                                tone: record.outcome == 'success'
                                    ? AnumTone.info
                                    : AnumTone.warn,
                                title: auditActionLabel(record.action),
                                subtitle: 'By ${record.actor}',
                                detail:
                                    '${exactTime(record.createdAt)} · ${record.target}',
                                trailing: const Icon(Icons.chevron_right),
                                onTap: () => _open(record),
                              ),
                            if (c.nextCursor != null) ...[
                              const SizedBox(height: AnumSpacing.sm),
                              OutlinedButton(
                                  onPressed: c.loadingMore ? null : c.loadMore,
                                  child: Text(c.loadingMore
                                      ? 'Loading...'
                                      : 'Load older records')),
                            ],
                            if (c.moreError != null) ...[
                              const SizedBox(height: AnumSpacing.sm),
                              Semantics(
                                  liveRegion: true,
                                  child: Text(
                                      'Older records could not be loaded. ${c.moreError}',
                                      style: TextStyle(color: p.stop))),
                            ],
                            const SizedBox(height: AnumSpacing.md),
                            Text('Retention. ${c.retention}',
                                key: const Key('audit-retention'),
                                style: TextStyle(
                                    color: p.muted, fontSize: AnumType.xsPlus)),
                          ]),
                    ),
                  ],
              },
            ]),
          );
        },
      );
}

class _AuditDetail extends StatelessWidget {
  const _AuditDetail({required this.record});
  final AuditRecord record;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    Widget field(String label, String value) => Padding(
          padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(label,
                style: TextStyle(
                    color: p.muted,
                    fontSize: AnumType.xs,
                    fontWeight: FontWeight.w700)),
            SelectableText(value,
                textDirection: TextDirection.ltr,
                style: TextStyle(color: p.text)),
          ]),
        );
    final rows = auditMetadataRows(record.metadata);
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsetsDirectional.fromSTEB(
            AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(auditActionLabel(record.action),
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: AnumSpacing.md),
          field('When', exactTime(record.createdAt)),
          field('Actor', record.actor),
          field('Action', record.action),
          field('Target', record.target),
          field('Outcome', record.outcome),
          field('Correlation id', record.correlationId),
          field('Record id', record.id),
          const Divider(),
          Text('Details', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: AnumSpacing.sm),
          if (rows.isEmpty)
            Text('No further details were recorded.',
                style: TextStyle(color: p.muted)),
          for (final (key, value) in rows) field(key, value),
        ]),
      ),
    );
  }
}
