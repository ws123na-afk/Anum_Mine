import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'governance_controller.dart';
import 'governance_models.dart';
import 'policy_packs.dart';

enum GovernanceSection { policies, marketplace, routing }

/// Organization controls: policies, marketplace, model routing and audit.
/// Everything shown comes from the API; unmeasured numbers are labelled as such.
class GovernanceScreen extends StatefulWidget {
  const GovernanceScreen({
    required this.controller,
    this.initialSection = GovernanceSection.policies,
    super.key,
  });
  final GovernanceController controller;
  final GovernanceSection initialSection;

  @override
  State<GovernanceScreen> createState() => _GovernanceState();
}

class _GovernanceState extends State<GovernanceScreen>
    with SingleTickerProviderStateMixin {
  late final _tabs = TabController(
      length: 3, vsync: this, initialIndex: widget.initialSection.index);

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == GovernancePhase.initial) {
      widget.controller.load();
    }
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final c = widget.controller;
          final s = c.snapshot;
          final p = context.palette;
          return Scaffold(
            appBar: AppBar(
              title: const Text('Organization'),
              actions: [
                PopupMenuButton<String>(
                  tooltip: 'Export audit log',
                  icon: const Icon(Icons.download_outlined),
                  onSelected: c.exportAudit,
                  itemBuilder: (_) => const [
                    PopupMenuItem(
                        value: 'json', child: Text('Export audit log (JSON)')),
                    PopupMenuItem(
                        value: 'csv', child: Text('Export audit log (CSV)')),
                  ],
                ),
                IconButton(
                  tooltip: 'Refresh',
                  onPressed: c.mutating ? null : c.load,
                  icon: const Icon(Icons.refresh),
                ),
              ],
              bottom: TabBar(
                controller: _tabs,
                labelColor: p.text,
                unselectedLabelColor: p.muted,
                indicatorColor: p.violet,
                dividerColor: p.line,
                tabs: const [
                  Tab(text: 'Policies'),
                  Tab(text: 'Marketplace'),
                  Tab(text: 'Routing')
                ],
              ),
            ),
            body: AnumBackdrop(
              child: Column(children: [
                if ((c.phase == GovernancePhase.offline ||
                        c.phase == GovernancePhase.error) &&
                    c.message != null)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                    child: AnumSurface(
                      accent: p.stop,
                      child: Row(children: [
                        Icon(Icons.cloud_off, color: p.stop),
                        const SizedBox(width: 10),
                        Expanded(
                            child: Text(c.message!,
                                style: TextStyle(color: p.text))),
                      ]),
                    ),
                  ),
                Expanded(
                  child: s == null
                      ? (c.phase == GovernancePhase.loading
                          ? const Center(child: CircularProgressIndicator())
                          : AnumEmpty(
                              icon: Icons.policy_outlined,
                              title: 'Organization data unavailable',
                              message:
                                  'Pull to refresh or check your connection.',
                              action: 'Retry',
                              onAction: c.load,
                            ))
                      : TabBarView(controller: _tabs, children: [
                          _PoliciesTab(controller: c, snapshot: s),
                          _MarketplaceTab(controller: c, snapshot: s),
                          _RoutingTab(controller: c, snapshot: s),
                        ]),
                ),
              ]),
            ),
          );
        },
      );
}

/// The read-only notice for callers who are not organization administrators.
class _ReadOnlyNotice extends StatelessWidget {
  const _ReadOnlyNotice();
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      key: const Key('organization-read-only'),
      accent: p.sky,
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(Icons.lock_outline, color: p.sky),
        const SizedBox(width: 10),
        Expanded(
            child: Text(organizationReadOnlyNotice,
                style: TextStyle(color: p.text, fontSize: AnumType.smPlus))),
      ]),
    );
  }
}

Widget _list(Future<void> Function() refresh, List<Widget> children) =>
    RefreshIndicator(
      onRefresh: refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        children: [
          for (var i = 0; i < children.length; i++) ...[
            children[i],
            if (i < children.length - 1) const SizedBox(height: 12),
          ]
        ],
      ),
    );

class _PoliciesTab extends StatelessWidget {
  const _PoliciesTab({required this.controller, required this.snapshot});
  final GovernanceController controller;
  final GovernanceSnapshot snapshot;

  @override
  Widget build(BuildContext context) {
    final s = snapshot;
    final canManage = s.summary.canManageOrganizationPolicy;
    return _list(controller.load, [
      if (!canManage) const _ReadOnlyNotice(),
      AnumMetricGrid(children: [
        AnumMetric(
            label: 'Policy packs',
            value: '${s.summary.policyPacks}',
            icon: Icons.policy_outlined),
        AnumMetric(
            label: 'Active',
            value: '${s.summary.activePolicyPacks}',
            icon: Icons.verified_user_outlined),
        AnumMetric(
            label: 'Role templates',
            value: '${s.summary.roleTemplates}',
            icon: Icons.badge_outlined),
        AnumMetric(
            label: 'Approval rules',
            value: '${s.summary.approvalRules}',
            icon: Icons.front_hand_outlined),
      ]),
      PolicyPacksSection(controller: controller, canManage: canManage),
    ]);
  }
}

class _MarketplaceTab extends StatelessWidget {
  const _MarketplaceTab({required this.controller, required this.snapshot});
  final GovernanceController controller;
  final GovernanceSnapshot snapshot;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final items = snapshot.marketplace;
    return _list(controller.load, [
      AnumSurface(
        child: AnumHeader(
          eyebrow: 'Marketplace',
          title:
              '${items.where((x) => x.installed).length} of ${items.length} installed',
          subtitle:
              'Only packages your organization has published appear here. Each one lists exactly what it can access.',
        ),
      ),
      if (items.isEmpty)
        const AnumSurface(
          child: AnumEmpty(
            icon: Icons.storefront_outlined,
            title: 'Nothing published yet',
            message:
                'When an owner publishes a skill or integration package, it shows here for review and install.',
          ),
        )
      else
        for (final x in items)
          AnumSurface(
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                AnumIconChip(
                    icon: x.kind == 'integration'
                        ? Icons.hub_outlined
                        : Icons.extension_outlined),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(x.name,
                            style: TextStyle(
                                color: p.text,
                                fontSize: AnumType.lg,
                                fontWeight: FontWeight.w700)),
                        Text('${x.kind} · v${x.version} · by ${x.publisher}',
                            style: TextStyle(
                                color: p.faint, fontSize: AnumType.xs)),
                      ]),
                ),
                if (x.verified)
                  const AnumPill(
                      label: 'Verified',
                      tone: AnumTone.ok,
                      icon: Icons.verified),
              ]),
              if (x.permissions.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text('Can access',
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
                const SizedBox(height: 6),
                Wrap(spacing: 6, runSpacing: 6, children: [
                  for (final perm in x.permissions)
                    AnumPill(
                      label: perm,
                      tone: perm.endsWith(':write')
                          ? AnumTone.warn
                          : AnumTone.neutral,
                      icon: perm.endsWith(':write')
                          ? Icons.edit_outlined
                          : Icons.visibility_outlined,
                    ),
                ]),
              ],
              if (x.regions.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text('Regions: ${x.regions.join(', ')}',
                    style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
              ],
              const SizedBox(height: 12),
              Align(
                alignment: AlignmentDirectional.centerEnd,
                child: x.installed
                    ? OutlinedButton(
                        onPressed: controller.mutating
                            ? null
                            : () => controller.uninstall(x.id),
                        child: const Text('Uninstall'),
                      )
                    : FilledButton(
                        onPressed: controller.mutating
                            ? null
                            : () => controller.install(x.id),
                        child: const Text('Install'),
                      ),
              ),
            ]),
          ),
    ]);
  }
}

class _RoutingTab extends StatelessWidget {
  const _RoutingTab({required this.controller, required this.snapshot});
  final GovernanceController controller;
  final GovernanceSnapshot snapshot;

  AnumTone _tone(String status) => switch (status) {
        'healthy' => AnumTone.ok,
        'degraded' => AnumTone.warn,
        'offline' => AnumTone.stop,
        _ => AnumTone.neutral,
      };

  String _label(RoutingTarget x) {
    if (!x.enabled) return 'Out of routing';
    if (!x.probed) return 'Not yet probed';
    return '${x.status[0].toUpperCase()}${x.status.substring(1)}';
  }

  AnumTone _rowTone(RoutingTarget x) =>
      !x.enabled || !x.probed ? AnumTone.neutral : _tone(x.status);

  /// What the probe measured, never a typed status.
  String _detail(RoutingTarget x) {
    if (!x.enabled) return 'Taken out of routing; decisions skip it.';
    if (!x.probed) {
      return 'Never probed. The scheduled check probes it once a workspace has '
          'a model configuration for ${x.provider}; Probe now measures it at once.';
    }
    final when = x.lastCheckedAt!.toLocal();
    final stamp =
        '${when.year}-${when.month.toString().padLeft(2, '0')}-${when.day.toString().padLeft(2, '0')} '
        '${when.hour.toString().padLeft(2, '0')}:${when.minute.toString().padLeft(2, '0')}';
    if (x.lastError != null) return '$stamp: ${x.lastError}';
    return '${x.measuredLatencyMs ?? 0} ms measured $stamp · '
        '\$${x.cost.toStringAsFixed(3)} per 1k tokens';
  }

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final ops = snapshot.operations;
    final canManage = snapshot.summary.canManageOrganizationPolicy;
    return _list(controller.load, [
      if (!canManage) const _ReadOnlyNotice(),
      AnumMetricGrid(children: [
        AnumMetric(
            label: 'Active regions',
            value: '${ops.activeRegions}',
            icon: Icons.public),
        AnumMetric(
            label: 'Healthy targets',
            value: '${ops.healthyTargets}',
            icon: Icons.favorite_outline),
        AnumMetric(
          label: 'Degraded',
          value: '${ops.degradedTargets}',
          icon: Icons.warning_amber,
          accent: ops.degradedTargets > 0 ? p.warn : null,
        ),
        AnumMetric(
          label: 'Failover',
          value: ops.failoverReady ? 'Ready' : 'Not set',
          caption: ops.failoverReady
              ? 'Two or more healthy regions'
              : 'Needs two healthy regions',
          icon: Icons.swap_horiz,
        ),
      ]),
      AnumSurface(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          AnumHeader(
            eyebrow: 'Model routing',
            title: 'Where AI requests go',
            subtitle: canManage
                ? 'Health is measured by a probe through this workspace\'s model configuration, never typed. Probe a target, or take it out of routing.'
                : 'Health is measured by a probe, never typed. Organization administrators probe targets and take them out of routing.',
          ),
          const SizedBox(height: 6),
          if (snapshot.targets.isEmpty)
            const AnumEmpty(
              icon: Icons.route,
              title: 'No routing targets',
              message:
                  'Configure a model in Settings to create the first target.',
            )
          else
            for (final x in snapshot.targets)
              AnumRow(
                icon: Icons.route,
                tone: _rowTone(x),
                title: '${x.provider} · ${x.model}',
                subtitle: 'Region: ${x.region}',
                detail: _detail(x),
                trailing: !canManage
                    ? AnumPill(label: _label(x), tone: _rowTone(x))
                    : PopupMenuButton<String>(
                        tooltip: 'Target actions',
                        onSelected: (action) => switch (action) {
                          'probe' => controller.probeTarget(x.id),
                          'disable' =>
                            controller.updateTarget(x.copyWith(enabled: false)),
                          _ =>
                            controller.updateTarget(x.copyWith(enabled: true)),
                        },
                        itemBuilder: (_) => [
                          if (x.enabled)
                            const PopupMenuItem(
                                value: 'probe', child: Text('Probe now')),
                          if (x.enabled)
                            const PopupMenuItem(
                                value: 'disable',
                                child: Text('Take out of routing'))
                          else
                            const PopupMenuItem(
                                value: 'enable',
                                child: Text('Put back in routing')),
                        ],
                        child: AnumPill(label: _label(x), tone: _rowTone(x)),
                      ),
              ),
        ]),
      ),
    ]);
  }
}
