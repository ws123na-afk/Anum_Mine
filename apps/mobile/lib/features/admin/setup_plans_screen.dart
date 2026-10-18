import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'org_admin_models.dart';
import 'org_admin_repository.dart';
import 'org_admin_widgets.dart';

enum SetupPlansPhase { loading, ready, empty, denied, error }

/// The checked-in setup plans (docs/setup-plan.md) and what each would do
/// here. Owners and organization administrators only; read-only on the
/// phone: applying a plan is on the web, behind its confirmation.
class SetupPlansController extends ChangeNotifier {
  SetupPlansController(this.repository);
  final OrgAdminRepository repository;

  SetupPlansPhase phase = SetupPlansPhase.loading;
  String? error;
  List<SetupPlanSummary> plans = const [];

  Future<void> load() async {
    phase = SetupPlansPhase.loading;
    error = null;
    notifyListeners();
    try {
      plans = await repository.setupPlans();
      phase = plans.isEmpty ? SetupPlansPhase.empty : SetupPlansPhase.ready;
    } on ApiException catch (e) {
      phase =
          e.isPermissionDenied ? SetupPlansPhase.denied : SetupPlansPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = SetupPlansPhase.error;
      error = '$e';
    }
    notifyListeners();
  }
}

/// One plan's preview: phase, counts, access notes, the week-by-week
/// rollout, every item (searched, filtered by section and paged) and what
/// ANUM cannot do yet.
class SetupPlanDetailController extends ChangeNotifier {
  SetupPlanDetailController(this.repository, this.planId);
  final OrgAdminRepository repository;
  final String planId;
  static const pageSize = 12;

  SetupPlansPhase phase = SetupPlansPhase.loading;
  String? error;
  SetupPlanPreview? preview;
  String planPhase = 'pilot';
  String query = '';
  String? section;
  bool changesOnly = false;
  int shown = pageSize;
  int _request = 0;

  List<SetupPlanItem> get visibleItems =>
      filterPlanItems(preview?.items ?? const [],
          section: section, query: query, changesOnly: changesOnly);

  /// Sections that have items, in plan order.
  List<String> get sections => [
        for (final name in setupPlanSections)
          if ((preview?.items ?? const []).any((i) => i.section == name)) name
      ];

  Future<void> load({String? phase}) async {
    if (phase != null) planPhase = phase;
    final id = ++_request;
    this.phase = SetupPlansPhase.loading;
    error = null;
    notifyListeners();
    try {
      final next = await repository.setupPlanPreview(planId, phase: planPhase);
      if (id != _request) return;
      preview = next;
      this.phase = SetupPlansPhase.ready;
    } on ApiException catch (e) {
      if (id != _request) return;
      this.phase =
          e.isPermissionDenied ? SetupPlansPhase.denied : SetupPlansPhase.error;
      error = e.readable;
    } on Object catch (e) {
      if (id != _request) return;
      this.phase = SetupPlansPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  void filter(
      {String? query,
      String? section,
      bool clearSection = false,
      bool? changesOnly}) {
    if (query != null) this.query = query;
    if (section != null || clearSection) this.section = section;
    if (changesOnly != null) this.changesOnly = changesOnly;
    shown = pageSize;
    notifyListeners();
  }

  void showMore() {
    shown += pageSize;
    notifyListeners();
  }
}

String planSectionName(AnumLocalizations l, String section) =>
    setupPlanSections.contains(section)
        ? l.t('oa.plan.section.$section')
        : section;

String planActionName(AnumLocalizations l, String action) => const {
      'create',
      'update',
      'present',
      'kept',
      'skipped',
      'later',
      'manual',
      'builtin',
      'unsupported'
    }.contains(action)
        ? l.t('oa.plan.action.$action')
        : action;

AnumTone planActionTone(String action) => switch (action) {
      'create' || 'update' => AnumTone.ok,
      'skipped' || 'kept' || 'manual' => AnumTone.warn,
      'unsupported' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

String planReason(AnumLocalizations l, SetupPlanItem item) =>
    localizedPlanReasons.contains(item.reasonCode)
        ? l.t('oa.plan.reason.${item.reasonCode}')
        : item.reason;

Widget _planDenied(AnumLocalizations l, String? error, VoidCallback retry) =>
    AdminStateView(
        icon: Icons.lock_outline,
        title: l.t('oa.plan.deniedTitle'),
        message: l.t('oa.plan.denied'),
        detail: l.f('oa.apiSaid', {'message': error ?? ''}),
        action: l.t('retry'),
        onAction: retry);

Widget _planError(AnumLocalizations l, String? error, VoidCallback retry) =>
    AdminStateView(
        icon: Icons.error_outline,
        title: l.t('oa.plan.errorTitle'),
        message: l.f('oa.errorHint', {'message': error ?? ''}),
        action: l.t('retry'),
        onAction: retry);

class SetupPlansScreen extends StatefulWidget {
  const SetupPlansScreen({required this.controller, super.key});
  final SetupPlansController controller;
  @override
  State<SetupPlansScreen> createState() => _SetupPlansScreenState();
}

class _SetupPlansScreenState extends State<SetupPlansScreen> {
  SetupPlansController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.load();
  }

  void _open(SetupPlanSummary plan) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => _OwnedDetail(repository: c.repository, plan: plan)));

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('oa.plan.title')), actions: [
          IconButton(
              onPressed: c.phase == SetupPlansPhase.loading ? null : c.load,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh)),
        ]),
        body: switch (c.phase) {
          SetupPlansPhase.loading =>
            AdminLoading(label: l.t('oa.plan.loading')),
          SetupPlansPhase.denied => _planDenied(l, c.error, c.load),
          SetupPlansPhase.error => _planError(l, c.error, c.load),
          SetupPlansPhase.empty => AdminStateView(
              icon: Icons.assignment_outlined,
              title: l.t('oa.plan.empty'),
              message: l.t('oa.plan.emptyHint'),
              action: l.t('refresh'),
              onAction: c.load),
          SetupPlansPhase.ready => AnumPage(onRefresh: c.load, children: [
              Text(l.t('oa.plan.intro'),
                  style: Theme.of(context).textTheme.bodyMedium),
              for (final plan in c.plans)
                AnumSurface(
                  semanticLabel: plan.name,
                  onTap: () => _open(plan),
                  child: AnumRow(
                    icon: Icons.assignment_outlined,
                    title: plan.name,
                    subtitle: plan.summary,
                    detail: plan.lastAppliedAt == null
                        ? l.f('oa.plan.notApplied', {'total': plan.totalItems})
                        : l.f('oa.plan.applied', {
                            'applied': plan.appliedItems,
                            'total': plan.totalItems,
                            'when': adminDate(context, plan.lastAppliedAt)
                          }),
                    trailing: const Icon(Icons.chevron_right),
                  ),
                ),
              Text(l.t('oa.plan.webOnly'),
                  style: Theme.of(context).textTheme.bodySmall),
            ]),
        },
      ),
    );
  }
}

/// Owns the detail controller for as long as the page is open.
class _OwnedDetail extends StatefulWidget {
  const _OwnedDetail({required this.repository, required this.plan});
  final OrgAdminRepository repository;
  final SetupPlanSummary plan;
  @override
  State<_OwnedDetail> createState() => _OwnedDetailState();
}

class _OwnedDetailState extends State<_OwnedDetail> {
  late final controller =
      SetupPlanDetailController(widget.repository, widget.plan.id);

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      SetupPlanDetailScreen(controller: controller, title: widget.plan.name);
}

class SetupPlanDetailScreen extends StatefulWidget {
  const SetupPlanDetailScreen(
      {required this.controller, required this.title, super.key});
  final SetupPlanDetailController controller;
  final String title;
  @override
  State<SetupPlanDetailScreen> createState() => _SetupPlanDetailScreenState();
}

class _SetupPlanDetailScreenState extends State<SetupPlanDetailScreen> {
  SetupPlanDetailController get c => widget.controller;
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    c.load();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(widget.title), actions: [
          IconButton(
              onPressed: c.phase == SetupPlansPhase.loading ? null : c.load,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh)),
        ]),
        body: switch (c.phase) {
          SetupPlansPhase.loading when c.preview == null =>
            AdminLoading(label: l.t('oa.plan.previewing')),
          SetupPlansPhase.denied => _planDenied(l, c.error, c.load),
          SetupPlansPhase.error => _planError(l, c.error, c.load),
          _ => _ready(context, l, c.preview!),
        },
      ),
    );
  }

  Widget _ready(
      BuildContext context, AnumLocalizations l, SetupPlanPreview preview) {
    final text = Theme.of(context).textTheme;
    final items = c.visibleItems;
    final filtered =
        c.query.trim().isNotEmpty || c.section != null || c.changesOnly;
    final plan = preview.plan;
    return AnumPage(onRefresh: c.load, children: [
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(plan.summary, style: text.bodyMedium),
          const SizedBox(height: AnumSpacing.xs),
          Text(
              [plan.currency, plan.timezone, plan.businessHours]
                  .where((s) => s.isNotEmpty)
                  .join(', '),
              style: text.bodySmall),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('oa.plan.phase.label'), style: text.titleSmall),
          const SizedBox(height: AnumSpacing.xs),
          SegmentedButton<String>(
            segments: [
              ButtonSegment(
                  value: 'pilot', label: Text(l.t('oa.plan.phase.pilot'))),
              ButtonSegment(
                  value: 'all', label: Text(l.t('oa.plan.phase.all'))),
            ],
            selected: {c.planPhase},
            onSelectionChanged: c.phase == SetupPlansPhase.loading
                ? null
                : (value) => c.load(phase: value.first),
          ),
          const SizedBox(height: AnumSpacing.xs),
          Text(
              l.t(c.planPhase == 'pilot'
                  ? 'oa.plan.phase.pilotHint'
                  : 'oa.plan.phase.allHint'),
              style: text.bodySmall),
        ]),
      ),
      if (c.phase == SetupPlansPhase.loading)
        AdminLoading(label: l.t('oa.plan.previewing')),
      AnumMetricGrid(children: [
        AnumMetric(
            label: l.t('oa.plan.metric.changes'),
            icon: Icons.add_task,
            value: '${preview.changes}',
            caption: l.t('oa.plan.metric.changesDetail')),
        AnumMetric(
            label: l.t('oa.plan.metric.present'),
            icon: Icons.check_circle_outline,
            value: '${preview.present}',
            caption: l.t('oa.plan.metric.presentDetail')),
        AnumMetric(
            label: l.t('oa.plan.metric.skipped'),
            icon: Icons.skip_next_outlined,
            value: '${preview.skipped}',
            caption: l.t('oa.plan.metric.skippedDetail')),
        AnumMetric(
            label: l.t('oa.plan.metric.budget'),
            icon: Icons.payments_outlined,
            value: 'AED ${preview.budgetAed.round()}',
            caption: l.f('oa.plan.metric.budgetDetail',
                {'usd': preview.budgetUsd.round()})),
      ]),
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('oa.plan.access.title'), style: text.titleMedium),
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t(preview.canApply
              ? 'oa.plan.access.can'
              : 'oa.plan.access.cannot')),
          for (final note in preview.accessNotes)
            Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Text(note, style: text.bodySmall),
            ),
          const SizedBox(height: AnumSpacing.xs),
          Text(l.t('oa.plan.webOnly'), style: text.bodySmall),
        ]),
      ),
      if (plan.weeks.isNotEmpty) _rollout(context, l, preview),
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text(l.t('oa.plan.items.title'), style: text.titleMedium),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('plan-search'),
            controller: _search,
            onChanged: (value) => c.filter(query: value),
            decoration: InputDecoration(
                labelText: l.t('oa.plan.items.search'),
                prefixIcon: const Icon(Icons.search)),
          ),
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xs, children: [
            ChoiceChip(
                label: Text(l.t('oa.plan.items.all')),
                selected: c.section == null,
                onSelected: (_) => c.filter(clearSection: true)),
            for (final section in c.sections)
              ChoiceChip(
                  label: Text(planSectionName(l, section)),
                  selected: c.section == section,
                  onSelected: (_) => c.filter(section: section)),
            FilterChip(
                label: Text(l.t('oa.plan.items.changesOnly')),
                selected: c.changesOnly,
                onSelected: (value) => c.filter(changesOnly: value)),
          ]),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.f('oa.plan.items.count', {'count': items.length}),
              style: text.bodySmall),
          if (items.isEmpty)
            AnumEmpty(
                icon: Icons.search_off,
                title: l.t('oa.plan.items.noneTitle'),
                message: l.t('oa.plan.items.none'),
                action: filtered ? l.t('oa.plan.items.clear') : null,
                onAction: filtered
                    ? () {
                        _search.clear();
                        c.filter(
                            query: '', clearSection: true, changesOnly: false);
                      }
                    : null)
          else ...[
            for (final item in items.take(c.shown)) _item(context, l, item),
            if (items.length > c.shown)
              Center(
                child: OutlinedButton(
                    onPressed: c.showMore,
                    child: Text(l.f('oa.plan.items.more', {
                      'count': (items.length - c.shown)
                          .clamp(0, SetupPlanDetailController.pageSize)
                    }))),
              ),
          ],
        ]),
      ),
      if (preview.approvalThresholds.isNotEmpty ||
          preview.memoryRules.isNotEmpty)
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l.t('oa.plan.gaps.title'), style: text.titleMedium),
            const SizedBox(height: AnumSpacing.xxs),
            Text(l.t('oa.plan.gaps.intro'), style: text.bodySmall),
            _notes(context, l, l.t('oa.plan.gaps.thresholds'),
                preview.approvalThresholds),
            _notes(context, l, l.t('oa.plan.gaps.memory'), preview.memoryRules),
          ]),
        ),
    ]);
  }

  Widget _rollout(
      BuildContext context, AnumLocalizations l, SetupPlanPreview preview) {
    final text = Theme.of(context).textTheme;
    final weeks = [...preview.plan.weeks]..sort((a, b) => a.week - b.week);
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(l.t('oa.plan.rollout.title'), style: text.titleMedium),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t('oa.plan.rollout.intro'), style: text.bodySmall),
        for (final week in weeks) ...[
          const SizedBox(height: AnumSpacing.sm),
          Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
            Text(l.f('oa.plan.rollout.week', {'week': week.week}),
                style: text.titleSmall),
            if (week.week == 1)
              AnumPill(
                  label: l.t('oa.plan.phase.pilot'), tone: AnumTone.accent),
          ]),
          Text(week.title, style: text.bodyMedium),
          Builder(builder: (context) {
            final names = [
              for (final item in preview.items)
                if ((item.section == 'agents' ||
                        item.section == 'assistants') &&
                    item.week == week.week)
                  item.name
            ];
            return Text(
                names.isEmpty ? l.t('oa.plan.rollout.none') : names.join(', '),
                style: text.bodySmall);
          }),
        ],
      ]),
    );
  }

  Widget _item(BuildContext context, AnumLocalizations l, SetupPlanItem item) {
    final text = Theme.of(context).textTheme;
    final header =
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(spacing: AnumSpacing.xs, runSpacing: AnumSpacing.xxs, children: [
        AnumPill(
            label: planActionName(l, item.action),
            tone: planActionTone(item.action)),
        AnumPill(label: planSectionName(l, item.section)),
        if (item.week != null)
          AnumPill(
              label: l.f('oa.plan.items.week', {'week': item.week!}),
              tone: AnumTone.info),
      ]),
      const SizedBox(height: AnumSpacing.xxs),
      Text(item.name, style: text.titleSmall),
      Text(planReason(l, item), style: text.bodySmall),
    ]);
    if (item.facts.isEmpty) {
      return Padding(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          child: header);
    }
    // Its own Material so the tile's ink shows on the surface.
    return Material(
        type: MaterialType.transparency,
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          childrenPadding:
              const EdgeInsetsDirectional.only(bottom: AnumSpacing.xs),
          expandedCrossAxisAlignment: CrossAxisAlignment.start,
          title: header,
          subtitle: Text(l.t('oa.plan.items.details'), style: text.labelSmall),
          children: [
            for (final fact in item.facts)
              Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xxs),
                child: Text('• $fact', style: text.bodySmall),
              ),
          ],
        ));
  }

  Widget _notes(BuildContext context, AnumLocalizations l, String title,
      List<SetupPlanNote> notes) {
    if (notes.isEmpty) return const SizedBox.shrink();
    final text = Theme.of(context).textTheme;
    // Its own Material so the tile's ink shows on the surface.
    return Material(
        type: MaterialType.transparency,
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: Text(title, style: text.titleSmall),
          subtitle: Text(l.f('oa.plan.gaps.count', {'count': notes.length}),
              style: text.bodySmall),
          expandedCrossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final note in notes)
              Padding(
                padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      AnumPill(
                          label: l.t(const {
                            'supported',
                            'partial',
                            'unsupported'
                          }.contains(note.support)
                              ? 'oa.plan.support.${note.support}'
                              : 'oa.plan.support.unsupported'),
                          tone: switch (note.support) {
                            'supported' => AnumTone.ok,
                            'partial' => AnumTone.warn,
                            _ => AnumTone.stop,
                          }),
                      const SizedBox(height: AnumSpacing.xxs),
                      Text(note.label, style: text.titleSmall),
                      if (note.plan.isNotEmpty)
                        Text(note.plan, style: text.bodySmall),
                      if (note.anum.isNotEmpty)
                        Text(note.anum, style: text.bodyMedium),
                    ]),
              ),
          ],
        ));
  }
}
