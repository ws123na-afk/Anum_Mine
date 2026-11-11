import 'dart:convert';

import '../../data/api_models.dart';

class GovernanceSummary {
  const GovernanceSummary(
      {required this.policyPacks,
      required this.activePolicyPacks,
      required this.roleTemplates,
      required this.approvalRules,
      this.canManageOrganizationPolicy = false});
  final int policyPacks, activePolicyPacks, roleTemplates, approvalRules;

  /// The API's answer for the caller (`can_manage_organization_policy`): only an
  /// organization administrator (`org_admin`) changes tenant-wide policy and routing.
  /// The screen never guesses this from the workspace role.
  final bool canManageOrganizationPolicy;
}

/// Shown instead of the controls when the caller is not an organization administrator.
const organizationReadOnlyNotice =
    'Read only: policy packs and routing targets apply to every workspace in this '
    'organization, so only an organization administrator can change them. Owning '
    'this workspace is not enough. Ask your organization administrator to make the '
    'change, or to have the org_admin role granted to you in your identity provider.';

class PolicyPack {
  const PolicyPack(
      {required this.id,
      required this.name,
      required this.version,
      required this.active,
      required this.rules,
      this.description = '',
      this.ruleItems = const [],
      this.createdAt});
  final String id, name, description;
  final int version;
  final bool active;

  /// Each rule as `action: effect`, for a compact list.
  final List<String> rules;

  /// The rules as the API stores them (docs/governance-and-scale.md#policy-packs).
  final List<PackRule> ruleItems;
  final DateTime? createdAt;
}

const policyEffects = ['allow', 'require_approval', 'deny'];

/// One rule of a policy pack: an action pattern, its effect and optional
/// conditions (a JSON object).
class PackRule {
  const PackRule(
      {required this.action, required this.effect, this.conditions = const {}});
  final String action, effect;
  final Map<String, Object?> conditions;

  JsonMap toJson() =>
      {'action': action, 'effect': effect, 'conditions': conditions};

  /// `{"risk":"high"}`, or empty for no conditions.
  String get conditionsText => conditions.isEmpty ? '' : jsonEncode(conditions);

  bool same(PackRule other) =>
      effect == other.effect && conditionsText == other.conditionsText;

  @override
  String toString() =>
      '$action: $effect${conditions.isEmpty ? '' : ' $conditionsText'}';
}

/// The versions of one pack name, newest first, and the active one.
class PackGroup {
  const PackGroup(
      {required this.name, required this.versions, required this.active});
  final String name;
  final List<PolicyPack> versions;
  final PolicyPack? active;
}

/// One group per pack name (case-insensitive), groups A to Z.
List<PackGroup> packGroups(List<PolicyPack> packs) {
  final byName = <String, List<PolicyPack>>{};
  for (final pack in packs) {
    byName.putIfAbsent(pack.name.trim().toLowerCase(), () => []).add(pack);
  }
  final groups = [
    for (final versions in byName.values)
      () {
        final sorted = [...versions]
          ..sort((a, b) => b.version.compareTo(a.version));
        return PackGroup(
            name: sorted.first.name,
            versions: sorted,
            active: sorted.where((p) => p.active).firstOrNull);
      }()
  ]..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
  return groups;
}

/// Case-insensitive match on a pack's name, description or a rule's action
/// or effect (the API's `q`).
List<PolicyPack> filterPacks(List<PolicyPack> packs, String query) {
  final needle = query.trim().toLowerCase();
  if (needle.isEmpty) return packs;
  return packs
      .where((p) =>
          p.name.toLowerCase().contains(needle) ||
          p.description.toLowerCase().contains(needle) ||
          p.ruleItems.any((r) =>
              r.action.toLowerCase().contains(needle) ||
              r.effect.toLowerCase().contains(needle)))
      .toList();
}

enum RuleChangeKind { added, removed, changed, same }

class RuleChange {
  const RuleChange(this.action, this.kind, this.before, this.after);
  final String action;
  final RuleChangeKind kind;
  final PackRule? before, after;
}

/// What changed between two versions, rule by rule (matched on the action),
/// like the web's compare view.
List<RuleChange> diffRules(List<PackRule> before, List<PackRule> after) {
  final changes = <RuleChange>[];
  final seen = <String>{};
  for (final rule in after) {
    final previous = before
        .where((b) => b.action == rule.action && !seen.contains(b.action))
        .firstOrNull;
    seen.add(rule.action);
    changes.add(previous == null
        ? RuleChange(rule.action, RuleChangeKind.added, null, rule)
        : RuleChange(
            rule.action,
            previous.same(rule) ? RuleChangeKind.same : RuleChangeKind.changed,
            previous,
            rule));
  }
  for (final rule in before) {
    if (!after.any((a) => a.action == rule.action)) {
      changes.add(RuleChange(rule.action, RuleChangeKind.removed, rule, null));
    }
  }
  return changes;
}

/// One editable rule row.
class RuleDraft {
  RuleDraft(
      {this.action = '',
      this.effect = 'require_approval',
      this.conditions = ''});
  String action, effect, conditions;
}

/// The `POST /policy-packs` body, or the first problem as a message key and
/// the rule's row (`ad.gov.editor.err.*`).
({JsonMap? body, String? error, int? row}) packBody(
    String name, String description, List<RuleDraft> rules) {
  final n = name.trim();
  if (n.isEmpty) {
    return (body: null, error: 'ad.gov.editor.err.name', row: null);
  }
  if (n.length > 160) {
    return (body: null, error: 'ad.gov.editor.err.nameLength', row: null);
  }
  if (description.length > 1000) {
    return (body: null, error: 'ad.gov.editor.err.description', row: null);
  }
  if (rules.isEmpty) {
    return (body: null, error: 'ad.gov.editor.err.noRules', row: null);
  }
  if (rules.length > 100) {
    return (body: null, error: 'ad.gov.editor.err.tooManyRules', row: null);
  }
  final out = <JsonMap>[];
  for (var i = 0; i < rules.length; i++) {
    final r = rules[i];
    final action = r.action.trim();
    if (action.isEmpty || action.length > 160) {
      return (body: null, error: 'ad.gov.editor.err.action', row: i + 1);
    }
    Map<String, Object?> conditions = const {};
    if (r.conditions.trim().isNotEmpty) {
      try {
        final parsed = jsonDecode(r.conditions);
        if (parsed is! Map<String, Object?>) throw const FormatException();
        conditions = parsed;
      } on FormatException {
        return (body: null, error: 'ad.gov.editor.err.conditions', row: i + 1);
      }
    }
    out.add({'action': action, 'effect': r.effect, 'conditions': conditions});
  }
  return (
    body: {'name': n, 'description': description.trim(), 'rules': out},
    error: null,
    row: null
  );
}

/// The organization baseline as a starting point: approval-first defaults.
List<RuleDraft> baselineRules() => [
      RuleDraft(
          action: 'integration.*.write',
          effect: 'require_approval',
          conditions: '{"risk": "high"}'),
      RuleDraft(action: 'payment.*', effect: 'require_approval'),
      RuleDraft(action: 'memory.delete', effect: 'deny'),
    ];

/// A role template: a named set of permissions a workspace owner gives a
/// member to narrow their role (docs/governance-and-scale.md#role-templates).
class RoleTemplate {
  const RoleTemplate(
      {required this.id, required this.name, required this.permissions});
  final String id, name;
  final List<String> permissions;
}

class MarketplaceItem {
  const MarketplaceItem(
      {required this.id,
      required this.name,
      required this.kind,
      required this.version,
      required this.verified,
      required this.permissions,
      required this.installed,
      this.publisher = 'ANUM',
      this.regions = const ['global']});
  final String id, name, kind, version, publisher;
  final bool verified, installed;
  final List<String> permissions, regions;
}

class RoutingTarget {
  const RoutingTarget(
      {required this.id,
      required this.region,
      required this.provider,
      required this.model,
      required this.status,
      required this.latencyMs,
      required this.cost,
      this.modalities = const ['text'],
      this.sensitivity = const ['standard'],
      this.enabled = true,
      this.lastCheckedAt,
      this.measuredLatencyMs,
      this.lastError});
  final String id, region, provider, model, status;
  final int latencyMs;
  final double cost;
  final List<String> modalities, sensitivity;

  /// In routing; off: decisions skip it and it can be deleted.
  final bool enabled;

  /// What the health probe measured (docs/governance-and-scale.md#routing-targets);
  /// all null until the first probe, so a typed status is never shown as health.
  final DateTime? lastCheckedAt;
  final int? measuredLatencyMs;
  final String? lastError;

  bool get probed => lastCheckedAt != null;

  RoutingTarget copyWith({bool? enabled}) => RoutingTarget(
      id: id,
      region: region,
      provider: provider,
      model: model,
      status: status,
      latencyMs: latencyMs,
      cost: cost,
      modalities: modalities,
      sensitivity: sensitivity,
      enabled: enabled ?? this.enabled,
      lastCheckedAt: lastCheckedAt,
      measuredLatencyMs: measuredLatencyMs,
      lastError: lastError);
}

class EnterpriseOperations {
  const EnterpriseOperations(
      {required this.activeRegions,
      required this.healthyTargets,
      required this.degradedTargets,
      required this.installedPackages,
      required this.failoverReady});
  final int activeRegions, healthyTargets, degradedTargets, installedPackages;
  final bool failoverReady;
}

class GovernanceSnapshot {
  const GovernanceSnapshot(
      {required this.summary,
      required this.policies,
      required this.marketplace,
      required this.targets,
      required this.operations});
  final GovernanceSummary summary;
  final List<PolicyPack> policies;
  final List<MarketplaceItem> marketplace;
  final List<RoutingTarget> targets;
  final EnterpriseOperations operations;
}
