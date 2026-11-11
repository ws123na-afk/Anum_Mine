import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/readable_error.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/localization/money_format.dart';
import 'admin_models.dart' show configuredWebAppUrl;
import 'admin_repository.dart';
import 'approval_policy.dart';

/// The Approvals settings sections beside the two policy switches
/// (docs/approvals-and-risk.md#approvals-settings): the organization's
/// approval rules (read only here; an organization administrator edits them
/// on the web), the routing worked out from the rules and the policy, and the
/// approval-ageing reminder. Each section loads, saves and refuses on its own.

class RuleConditions {
  const RuleConditions(
      {this.amountAbove, this.amountAtMost, this.percentAtMost, this.disputed});
  factory RuleConditions.fromJson(JsonReader? j) => j == null
      ? const RuleConditions()
      : RuleConditions(
          amountAbove: j.optNumber('amount_above'),
          amountAtMost: j.optNumber('amount_at_most'),
          percentAtMost: j.optNumber('percent_of_order_at_most'),
          disputed: j.optString('disputed'));
  final double? amountAbove, amountAtMost, percentAtMost;

  /// `any`, `yes` or `no`.
  final String? disputed;
}

class ApprovalRuleView {
  const ApprovalRuleView({
    required this.id,
    required this.name,
    required this.actionPattern,
    required this.enabled,
    required this.minimumApprovers,
    required this.requiredRoles,
    required this.conditions,
    required this.steps,
  });
  factory ApprovalRuleView.fromJson(JsonReader j) => ApprovalRuleView(
        id: j.string('id'),
        name: j.string('name'),
        actionPattern: j.optString('action_pattern') ?? '*',
        enabled: j.optBool('enabled') ?? true,
        minimumApprovers: j.optInt('minimum_approvers') ?? 1,
        requiredRoles: j.optStrings('required_roles') ?? const [],
        conditions: RuleConditions.fromJson(j.optObject('conditions')),
        steps: j.optObjects(
            'approver_steps', (s) => s.optStrings('approvers') ?? const []),
      );
  final String id, name, actionPattern;
  final bool enabled;
  final int minimumApprovers;
  final List<String> requiredRoles;
  final RuleConditions conditions;

  /// Who approves each step (`owner`, `org_admin`, `role:member`,
  /// `user:<id>`); empty for a rule from before steps existed.
  final List<List<String>> steps;
}

String _aed(double value, AnumLocalizations l) =>
    formatMoney(value, 'AED', locale: l.locale);

/// The band a rule applies to, in words, or null for every amount.
String? ruleBand(RuleConditions c, AnumLocalizations l) {
  final parts = <String>[];
  if (c.amountAbove != null && c.amountAtMost != null) {
    parts.add(l.f('ad.apr.band.between', {
      'above': _aed(c.amountAbove!, l),
      'atMost': _aed(c.amountAtMost!, l)
    }));
  } else if (c.amountAbove != null) {
    parts.add(l.f('ad.apr.band.above', {'amount': _aed(c.amountAbove!, l)}));
  } else if (c.amountAtMost != null) {
    parts.add(l.f('ad.apr.band.atMost', {'amount': _aed(c.amountAtMost!, l)}));
  }
  if (c.percentAtMost != null && c.percentAtMost! > 0) {
    final pct = c.percentAtMost!;
    parts.add(l.f('ad.apr.band.percent', {
      'percent': pct == pct.roundToDouble() ? pct.toInt() : pct,
    }));
  }
  if (c.disputed == 'yes') parts.add(l.t('ad.apr.band.disputed'));
  if (c.disputed == 'no') parts.add(l.t('ad.apr.band.notDisputed'));
  return parts.isEmpty ? null : parts.join(' · ');
}

String approverLabel(String token, AnumLocalizations l) {
  if (token == 'owner' || token == 'role:owner')
    return l.t('ad.apr.who.owners');
  if (token == 'org_admin') return l.t('ad.apr.who.orgAdmins');
  if (token == 'role:member') return l.t('ad.apr.who.members');
  return token.startsWith('user:') ? token.substring(5) : token;
}

/// Anyone of a list: "Owners or care_lead".
String approverSetLabel(List<String> set, AnumLocalizations l) {
  final labels = <String>[];
  for (final token in set) {
    final label = approverLabel(token, l);
    if (!labels.contains(label)) labels.add(label);
  }
  if (labels.length <= 1) return labels.firstOrNull ?? '';
  final separator = l.isArabic ? '، ' : ', ';
  return l.f('ad.apr.or', {
    'head': labels.sublist(0, labels.length - 1).join(separator),
    'last': labels.last,
  });
}

/// Who approves a rule, step by step.
List<String> ruleSteps(ApprovalRuleView rule, AnumLocalizations l) {
  if (rule.steps.isEmpty) {
    return [
      l.f('ad.apr.legacy', {
        'count': rule.minimumApprovers,
        'roles':
            rule.requiredRoles.isEmpty ? 'owner' : rule.requiredRoles.join(', ')
      })
    ];
  }
  return [
    for (var i = 0; i < rule.steps.length; i++)
      rule.steps.length > 1
          ? l.f('ad.apr.step',
              {'step': i + 1, 'who': approverSetLabel(rule.steps[i], l)})
          : approverSetLabel(rule.steps[i], l)
  ];
}

class RoutingSummary {
  const RoutingSummary(
      {required this.bands, required this.off, required this.policy});

  /// The rules that are on, each as "{name}: {band}, {who}".
  final List<String> bands;

  /// How many stored rules are switched off.
  final int off;

  /// The switches' effect in words; empty when the policy is unknown.
  final List<String> policy;
}

/// What the Routing section says, from the rules and the policy (as drafted,
/// so an owner sees the effect before saving), like the web.
RoutingSummary routingSummary(
    List<ApprovalRuleView> rules, ApprovalPolicy? policy, AnumLocalizations l) {
  final enabled = rules.where((r) => r.enabled).toList();
  final separator = l.isArabic ? '، ' : ', ';
  return RoutingSummary(
    bands: [
      for (final rule in enabled)
        l.f('ad.apr.routing.band', {
          'name': rule.name,
          'band':
              ruleBand(rule.conditions, l) ?? l.t('ad.apr.rules.everyAmount'),
          'who': ruleSteps(rule, l).join(separator),
        })
    ],
    off: rules.length - enabled.length,
    policy: policy == null
        ? const []
        : [
            l.t(policy.twoPersonRule
                ? 'ad.apr.routing.twoPersonOn'
                : 'ad.apr.routing.twoPersonOff'),
            l.t(policy.mediumRiskRequiresApproval
                ? 'ad.apr.routing.mediumOn'
                : 'ad.apr.routing.mediumOff'),
          ],
  );
}

/// Where "Edit rules on the web" opens: the web app's Governance page, or
/// null when this build does not know the web app's address.
Uri? governanceWebLink([String appUrl = configuredWebAppUrl]) {
  final base = appUrl.trim().split('#').first;
  final uri = Uri.tryParse(base);
  if (uri == null ||
      !uri.hasScheme ||
      !(uri.scheme == 'https' || uri.scheme == 'http') ||
      uri.host.isEmpty) {
    return null;
  }
  return uri.replace(fragment: 'governance');
}

class ReminderSetting {
  const ReminderSetting({this.hours, this.updatedBy, this.updatedAt});
  factory ReminderSetting.fromJson(JsonReader j) => ReminderSetting(
      hours: j.optInt('reminder_hours'),
      updatedBy: j.optString('updated_by'),
      updatedAt: j.optDate('updated_at'));

  /// Hours a pending approval waits before its reminder; null is off.
  final int? hours;
  final String? updatedBy;
  final DateTime? updatedAt;
}

enum SectionPhase { loading, ready, denied, error }

/// The organization's approval rules, read only on the phone.
class ApprovalRulesController extends ChangeNotifier {
  ApprovalRulesController(this.repository);
  final AdminRepository repository;
  SectionPhase phase = SectionPhase.loading;
  List<ApprovalRuleView> rules = const [];
  String? error;
  bool _disposed = false;

  Future<void> load() async {
    phase = SectionPhase.loading;
    error = null;
    _notify();
    try {
      rules = await repository.approvalRules();
      phase = SectionPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? SectionPhase.denied : SectionPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = SectionPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}

/// The approval-ageing reminder: any member reads it, owners change it.
class ApprovalReminderController extends ChangeNotifier {
  ApprovalReminderController(this.repository);
  final AdminRepository repository;
  SectionPhase phase = SectionPhase.loading;
  ReminderSetting? setting;

  /// The hours as chosen (null is off), sent by [save].
  int? draft;
  String? error;

  /// The API's 403 to a save; the section turns read only with it.
  String? denied;
  String? notice;
  bool noticeFailed = false;
  bool saving = false;
  bool _disposed = false;

  bool get changed => setting != null && draft != setting!.hours;

  Future<void> load() async {
    phase = SectionPhase.loading;
    error = notice = null;
    _notify();
    try {
      setting = await repository.approvalReminders();
      draft = setting!.hours;
      phase = SectionPhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? SectionPhase.denied : SectionPhase.error;
      error = e.readable;
    } on Object catch (e) {
      phase = SectionPhase.error;
      error = readableError(e);
    }
    _notify();
  }

  void choose(int? hours) {
    if (saving) return;
    draft = hours;
    notice = null;
    _notify();
  }

  Future<void> save(AnumLocalizations l) async {
    if (saving || !changed) return;
    saving = true;
    notice = null;
    noticeFailed = false;
    _notify();
    try {
      setting = await repository.setApprovalReminders(draft);
      draft = setting!.hours;
      notice = draft == null
          ? l.t('ad.apr.rem.savedOff')
          : l.f('ad.apr.rem.saved', {'hours': draft!});
    } on ApiException catch (e) {
      if (e.isPermissionDenied) {
        denied = e.readable;
        draft = setting?.hours;
      } else {
        noticeFailed = true;
        notice = l.f('ad.apr.rem.saveFailed', {'message': e.readable});
      }
    } on Object catch (e) {
      noticeFailed = true;
      notice = l.f('ad.apr.rem.saveFailed', {'message': readableError(e)});
    } finally {
      saving = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
