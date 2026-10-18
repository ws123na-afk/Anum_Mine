import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'org_admin_models.dart';
import 'org_admin_repository.dart';
import 'org_admin_widgets.dart';

enum OrgChartPhase { loading, ready, empty, denied, error }

/// The AI team org chart, read-only (docs/org-chart.md): departments with
/// their agents, department assistants and people. There is no org chart
/// route; like the web chart it is built from the agents, assistants and
/// team people routes, and assistants or people that fail to load are said
/// instead of hiding the chart.
class OrgChartController extends ChangeNotifier {
  OrgChartController(this.repository);
  final OrgAdminRepository repository;

  /// Members shown per department before "Show more".
  static const pageSize = 8;

  OrgChartPhase phase = OrgChartPhase.loading;
  String? error;
  OrgChartData? data;
  String query = '';
  final Map<String, int> _shown = {};

  List<OrgDepartment> get departments =>
      groupOrgChart(filterOrgMembers(data?.members ?? const [], query));

  int shownIn(String department) => _shown[department] ?? pageSize;

  Future<void> load() async {
    phase = OrgChartPhase.loading;
    error = null;
    notifyListeners();
    try {
      data = await repository.orgChart();
      phase = data!.members.isEmpty ? OrgChartPhase.empty : OrgChartPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? OrgChartPhase.denied : OrgChartPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = OrgChartPhase.error;
      error = '$e';
    }
    notifyListeners();
  }

  void search(String value) {
    query = value;
    _shown.clear();
    notifyListeners();
  }

  void showMore(String department) {
    _shown[department] = shownIn(department) + pageSize;
    notifyListeners();
  }
}

String orgDepartmentName(AnumLocalizations l, OrgDepartment d) {
  if (d.known != null) return l.t('oa.org.dept.${d.known}');
  if (d.key == 'none') return l.t('oa.org.dept.none');
  return d.name ?? d.key;
}

class OrgChartScreen extends StatefulWidget {
  const OrgChartScreen({required this.controller, super.key});
  final OrgChartController controller;
  @override
  State<OrgChartScreen> createState() => _OrgChartScreenState();
}

class _OrgChartScreenState extends State<OrgChartScreen> {
  OrgChartController get c => widget.controller;
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
        appBar: AppBar(title: Text(l.t('oa.org.title')), actions: [
          IconButton(
              onPressed: c.phase == OrgChartPhase.loading ? null : c.load,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh)),
        ]),
        body: switch (c.phase) {
          OrgChartPhase.loading => AdminLoading(label: l.t('oa.org.loading')),
          OrgChartPhase.denied => AdminStateView(
              icon: Icons.lock_outline,
              title: l.t('oa.org.deniedTitle'),
              message: l.t('oa.org.denied'),
              detail: l.f('oa.apiSaid', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          OrgChartPhase.error => AdminStateView(
              icon: Icons.error_outline,
              title: l.t('oa.org.errorTitle'),
              message: l.f('oa.errorHint', {'message': c.error ?? ''}),
              action: l.t('retry'),
              onAction: c.load),
          OrgChartPhase.empty => AdminStateView(
              icon: Icons.account_tree_outlined,
              title: l.t('oa.org.empty'),
              message: l.t('oa.org.emptyHint'),
              action: l.t('refresh'),
              onAction: c.load),
          OrgChartPhase.ready => _ready(context, l),
        },
      ),
    );
  }

  Widget _ready(BuildContext context, AnumLocalizations l) {
    final text = Theme.of(context).textTheme;
    final data = c.data!;
    final departments = c.departments;
    final all = data.members;
    int count(OrgMemberKind kind) => all.where((m) => m.kind == kind).length;
    return AnumPage(onRefresh: c.load, children: [
      Text(l.t('oa.org.intro'), style: text.bodyMedium),
      AnumMetricGrid(children: [
        AnumMetric(
            label: l.t('oa.org.metric.departments'),
            value: '${groupOrgChart(all).length}',
            icon: Icons.account_tree_outlined),
        AnumMetric(
            label: l.t('oa.org.metric.agents'),
            value: '${count(OrgMemberKind.agent)}',
            icon: Icons.smart_toy_outlined),
        AnumMetric(
            label: l.t('oa.org.metric.assistants'),
            value: '${count(OrgMemberKind.assistant)}',
            icon: Icons.work_outline),
        AnumMetric(
            label: l.t('oa.org.metric.people'),
            value: '${count(OrgMemberKind.person)}',
            icon: Icons.person_outline),
      ]),
      if (data.assistantsFailed || data.peopleFailed || data.truncated)
        AnumSurface(
          accent: anumToneColor(context, AnumTone.warn),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (data.assistantsFailed) Text(l.t('oa.org.partialAssistants')),
            if (data.peopleFailed) Text(l.t('oa.org.partialPeople')),
            if (data.truncated) Text(l.t('oa.org.truncated')),
          ]),
        ),
      TextField(
        key: const Key('org-search'),
        controller: _search,
        onChanged: c.search,
        decoration: InputDecoration(
            labelText: l.t('oa.org.search'),
            prefixIcon: const Icon(Icons.search)),
      ),
      if (departments.isEmpty)
        AnumEmpty(
            icon: Icons.search_off,
            title: l.t('oa.org.noMatchTitle'),
            message: l.t('oa.org.noMatch'),
            action: l.t('oa.org.clear'),
            onAction: () {
              _search.clear();
              c.search('');
            })
      else
        LayoutBuilder(builder: (context, box) {
          // Two columns of departments from tablet width, one on phones.
          final columns = box.maxWidth >= AnumBreakpoints.expanded ? 2 : 1;
          const gap = AnumSpacing.md;
          final width = (box.maxWidth - gap * (columns - 1)) / columns;
          return Wrap(spacing: gap, runSpacing: gap, children: [
            for (final d in departments)
              SizedBox(width: width, child: _department(context, l, d)),
          ]);
        }),
    ]);
  }

  Widget _department(
      BuildContext context, AnumLocalizations l, OrgDepartment d) {
    final text = Theme.of(context).textTheme;
    final shown = c.shownIn(d.key);
    final counts = [
      l.f('oa.org.count.agents', {'count': d.count(OrgMemberKind.agent)}),
      l.f('oa.org.count.assistants',
          {'count': d.count(OrgMemberKind.assistant)}),
      l.f('oa.org.count.people', {'count': d.count(OrgMemberKind.person)}),
    ].join(' · ');
    return AnumSurface(
      semanticLabel: orgDepartmentName(l, d),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Semantics(
          header: true,
          child: Text(orgDepartmentName(l, d), style: text.titleMedium),
        ),
        Text(counts, style: text.bodySmall),
        const SizedBox(height: AnumSpacing.xs),
        for (final m in d.members.take(shown)) _member(context, l, m),
        if (d.members.length > shown)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton(
                onPressed: () => c.showMore(d.key),
                child: Text(l.f('oa.org.more', {
                  'count': (d.members.length - shown)
                      .clamp(0, OrgChartController.pageSize)
                }))),
          ),
      ]),
    );
  }

  Widget _member(BuildContext context, AnumLocalizations l, OrgMember m) {
    final (icon, kind) = switch (m.kind) {
      OrgMemberKind.agent => (
          Icons.smart_toy_outlined,
          l.t('oa.org.kind.agent')
        ),
      OrgMemberKind.assistant => (
          Icons.work_outline,
          l.t('oa.org.kind.assistant')
        ),
      OrgMemberKind.person => (Icons.person_outline, l.t('oa.org.kind.person')),
    };
    final (status, tone) = switch (m) {
      OrgMember(attention: true) => (
          l.t('oa.org.status.attention'),
          AnumTone.stop
        ),
      OrgMember(kind: OrgMemberKind.agent, active: false) => (
          l.t('oa.org.status.paused'),
          AnumTone.warn
        ),
      OrgMember(kind: OrgMemberKind.assistant, active: false) => (
          l.t('oa.org.status.off'),
          AnumTone.neutral
        ),
      OrgMember(kind: OrgMemberKind.person) => (
          m.role != null && const {'owner', 'member', 'viewer'}.contains(m.role)
              ? l.t('oa.ws.role.${m.role}')
              : kind,
          AnumTone.info
        ),
      _ => (l.t('oa.org.status.active'), AnumTone.ok),
    };
    final subtitle = switch (m.kind) {
      OrgMemberKind.person => m.jobTitle,
      OrgMemberKind.agent => m.description,
      OrgMemberKind.assistant => null,
    };
    return AnumRow(
      icon: icon,
      tone: tone,
      title: m.name,
      subtitle:
          subtitle == null || subtitle.isEmpty ? kind : '$kind · $subtitle',
      // Bounded so a long status wraps at 200% text instead of overflowing.
      trailing: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 112),
          child: AnumPill(label: status, tone: tone)),
    );
  }
}
