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
      required this.rules});
  final String id, name;
  final int version;
  final bool active;
  final List<String> rules;
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
      this.sensitivity = const ['standard']});
  final String id, region, provider, model, status;
  final int latencyMs;
  final double cost;
  final List<String> modalities, sensitivity;
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
