import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'governance_controller.dart';
import 'governance_models.dart';

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
    final p = context.palette;
    return _list(controller.load, [
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
      if (s.policies.isEmpty)
        AnumSurface(
          child: AnumEmpty(
            icon: Icons.policy_outlined,
            title: 'No policy packs yet',
            message:
                'A baseline pack sets the default rules every agent must follow.',
            action: 'Add baseline policy',
            onAction: controller.mutating
                ? null
                : () => controller.baseline('Organization baseline'),
          ),
        )
      else ...[
        Align(
          alignment: AlignmentDirectional.centerEnd,
          child: TextButton.icon(
            onPressed: controller.mutating
                ? null
                : () => controller.baseline('Organization baseline'),
            icon: const Icon(Icons.add),
            label: const Text('Add baseline policy'),
          ),
        ),
        for (final policy in s.policies)
          AnumSurface(
            accent: policy.active ? p.ok : null,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                AnumIconChip(
                  icon: policy.active
                      ? Icons.verified_user_outlined
                      : Icons.policy_outlined,
                  tone: policy.active ? AnumTone.ok : null,
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(policy.name,
                            style: TextStyle(
                                color: p.text,
                                fontSize: 16,
                                fontWeight: FontWeight.w700)),
                        Text(
                            'Version ${policy.version} · ${policy.rules.length} rules',
                            style: TextStyle(color: p.faint, fontSize: 12)),
                      ]),
                ),
                Switch(
                  value: policy.active,
                  onChanged: controller.mutating
                      ? null
                      : (value) =>
                          controller.setPolicyActive(policy.id, active: value),
                ),
              ]),
              if (policy.rules.isNotEmpty) ...[
                const SizedBox(height: 10),
                for (final rule in policy.rules)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(Icons.rule, size: 16, color: p.sky),
                          const SizedBox(width: 8),
                          Expanded(
                              child: Text(rule,
                                  style: TextStyle(
                                      color: p.muted, fontSize: 13.5))),
                        ]),
                  ),
              ],
            ]),
          ),
      ],
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
                                fontSize: 16,
                                fontWeight: FontWeight.w700)),
                        Text('${x.kind} · v${x.version} · by ${x.publisher}',
                            style: TextStyle(color: p.faint, fontSize: 12)),
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
                    style: TextStyle(color: p.faint, fontSize: 12)),
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
                    style: TextStyle(color: p.faint, fontSize: 12)),
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

  String _label(String status) => switch (status) {
        'unverified' => 'Not measured',
        _ => '${status[0].toUpperCase()}${status.substring(1)}',
      };

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final ops = snapshot.operations;
    return _list(controller.load, [
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
          const AnumHeader(
            eyebrow: 'Model routing',
            title: 'Where AI requests go',
            subtitle: 'Owners can mark a target healthy, degraded or offline.',
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
                tone: _tone(x.status),
                title: '${x.provider} · ${x.model}',
                subtitle: 'Region: ${x.region}',
                detail: x.status == 'unverified'
                    ? 'Cost and latency not measured yet'
                    : '${x.latencyMs} ms · \$${x.cost.toStringAsFixed(3)} per 1k tokens',
                trailing: PopupMenuButton<String>(
                  tooltip: 'Change status',
                  onSelected: (status) => controller.updateTarget(RoutingTarget(
                    id: x.id,
                    region: x.region,
                    provider: x.provider,
                    model: x.model,
                    status: status,
                    latencyMs: x.latencyMs,
                    cost: x.cost,
                    modalities: x.modalities,
                    sensitivity: x.sensitivity,
                  )),
                  itemBuilder: (_) => const [
                    PopupMenuItem(
                        value: 'healthy', child: Text('Mark healthy')),
                    PopupMenuItem(
                        value: 'degraded', child: Text('Mark degraded')),
                    PopupMenuItem(
                        value: 'offline', child: Text('Mark offline')),
                  ],
                  child:
                      AnumPill(label: _label(x.status), tone: _tone(x.status)),
                ),
              ),
        ]),
      ),
    ]);
  }
}
