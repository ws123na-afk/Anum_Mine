import '../../data/api_models.dart';
import 'admin_models.dart';

/// Per-workspace approval policy (docs/approvals-and-risk.md, Workspace
/// Approval Policy). Any member reads it; only owners change it.
class ApprovalPolicy {
  const ApprovalPolicy({
    required this.twoPersonRule,
    required this.mediumRiskRequiresApproval,
    this.updatedBy,
    this.updatedAt,
  });

  factory ApprovalPolicy.fromJson(JsonMap json) => ApprovalPolicy(
        twoPersonRule: json['two_person_rule'] == true,
        mediumRiskRequiresApproval:
            json['medium_risk_requires_approval'] == true,
        updatedBy: JsonReader(json).optString('updated_by'),
        updatedAt: switch (json['updated_at']) {
          final String value => DateTime.parse(value),
          _ => null,
        },
      );

  final bool twoPersonRule, mediumRiskRequiresApproval;
  final String? updatedBy;
  final DateTime? updatedAt;

  bool value(PolicySwitch key) => switch (key) {
        PolicySwitch.twoPersonRule => twoPersonRule,
        PolicySwitch.mediumRiskRequiresApproval => mediumRiskRequiresApproval,
      };

  ApprovalPolicy copyWith(PolicySwitch key, bool on) => ApprovalPolicy(
        twoPersonRule: key == PolicySwitch.twoPersonRule ? on : twoPersonRule,
        mediumRiskRequiresApproval:
            key == PolicySwitch.mediumRiskRequiresApproval
                ? on
                : mediumRiskRequiresApproval,
        updatedBy: updatedBy,
        updatedAt: updatedAt,
      );

  bool sameSwitches(ApprovalPolicy other) =>
      twoPersonRule == other.twoPersonRule &&
      mediumRiskRequiresApproval == other.mediumRiskRequiresApproval;

  /// The owner-only `PUT /api/v1/approval-policy` body: both switches.
  JsonMap toJson() => {
        'two_person_rule': twoPersonRule,
        'medium_risk_requires_approval': mediumRiskRequiresApproval,
      };
}

enum PolicySwitch { twoPersonRule, mediumRiskRequiresApproval }

/// What each switch does, in the words the screens show (same text as web).
const policySwitchText =
    <PolicySwitch, ({String title, String on, String off})>{
  PolicySwitch.twoPersonRule: (
    title: 'Two-person rule for high-risk actions',
    on: 'Whoever created a task or started its run cannot approve its high-risk actions. Another owner must approve them. Anyone may still reject their own.',
    off:
        'The owner who started a task may approve its high-risk actions themselves.',
  ),
  PolicySwitch.mediumRiskRequiresApproval: (
    title: 'Approval for medium-risk actions',
    on: 'Medium-risk tools pause and wait for an owner, like high-risk ones.',
    off:
        'Medium-risk tools run without waiting. High-risk tools always wait for approval.',
  ),
};

String policyUpdatedLabel(
    ApprovalPolicy policy, String Function(DateTime) format) {
  final at = policy.updatedAt;
  if (at == null) return 'Never changed: both switches are off by default.';
  final by = policy.updatedBy;
  return 'Last changed ${format(at)}${by == null ? '' : ' by $by'}.';
}

/// Only owners decide approvals, so the two-person rule needs two active
/// owners. Null when fine or when the member list is unknown.
String? twoPersonWarning(ApprovalPolicy draft, List<WorkspaceMember>? members) {
  if (!draft.twoPersonRule || members == null) return null;
  final owners = activeOwnerCount(members);
  if (owners >= 2) return null;
  return owners == 1
      ? 'This workspace has one active owner. With the two-person rule on, nobody can approve the high-risk actions of tasks that owner starts. Invite a second owner first.'
      : 'This workspace has no active owner who could approve high-risk actions.';
}
