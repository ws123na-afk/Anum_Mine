import 'package:flutter/foundation.dart';

import '../records_kit/records_kit.dart';
import 'finance_depth_models.dart';

/// One card's load state: where it is, the data once read and what failed.
class DepthPart<T> {
  RecordsPhase phase = RecordsPhase.loading;
  T? value;
  RecordsFailure? failure;

  bool get ready => phase == RecordsPhase.ready && value != null;
}

/// Finance settings on the phone (docs/finance.md#finance-settings): the
/// main currency and time zone, the Safe to spend buffer, the emergency
/// reserve and the exception notices. Each card loads and fails on its own,
/// so a failed reserve keeps the currency card. Every save goes through the
/// existing endpoint and reloads what it changed; a refusal is thrown to the
/// form, which shows the API's sentence.
class FinanceDepthController extends ChangeNotifier {
  FinanceDepthController(this.repository);
  final FinanceDepthRepository repository;

  final settings = DepthPart<FinanceSettingsData>();
  final plan = DepthPart<FinancePlanData>();
  final reserves = DepthPart<ReserveBook>();
  final automation = DepthPart<AutomationPolicy>();

  /// The goals the reserve update keeps the name, date and notes of.
  List<GoalRecord> goals = const [];
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> _read<T>(DepthPart<T> part, Future<T> Function() load) async {
    part.phase = part.value == null ? RecordsPhase.loading : part.phase;
    _notify();
    try {
      part.value = await load();
      part.failure = null;
      part.phase = RecordsPhase.ready;
    } on Object catch (error) {
      part.failure = RecordsFailure.from(error);
      part.phase =
          part.failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    _notify();
  }

  Future<void> load() => Future.wait([
        loadSettings(),
        loadPlan(),
        loadReserves(),
        loadAutomation(),
      ]);

  Future<void> loadSettings() => _read(settings, repository.settings);
  Future<void> loadPlan() => _read(plan, repository.plan);
  Future<void> loadAutomation() => _read(automation, repository.automation);

  Future<void> loadReserves() => _read(reserves, () async {
        final found =
            await Future.wait([repository.reserves(), repository.goals()]);
        goals = found[1] as List<GoalRecord>;
        return found[0] as ReserveBook;
      });

  /// The main currency and time zone; the keyword rules are kept.
  Future<void> saveGeneral(
      {required String currency, required String timezone}) async {
    final current = settings.value ??
        const FinanceSettingsData(baseCurrency: 'AED', timezone: 'Asia/Dubai');
    await repository
        .saveSettings(current.body(currency: currency, timezone: timezone));
    await Future.wait([loadSettings(), loadReserves()]);
  }

  /// Replaces the statement keyword rules (docs/finance.md#finance-settings)
  /// and keeps the currency and time zone; reloads what was saved.
  Future<void> saveRules(List<Map<String, Object?>> rules) async {
    final current = settings.value ??
        const FinanceSettingsData(baseCurrency: 'AED', timezone: 'Asia/Dubai');
    await repository.saveSettings(current.body(rules: rules));
    await loadSettings();
  }

  /// [currency]'s buffer set to [amount], or taken out when null.
  Future<void> saveBuffer(String currency, String? amount) async {
    final current = plan.value ?? const FinancePlanData();
    await repository.savePlan(current.withBuffer(currency, amount));
    await loadPlan();
  }

  /// Creates or updates the reserve in [currency]: sized in [months] of
  /// essential spending (the target worked out from `GET /reserve`) or as a
  /// fixed [amount], kept in [accountId] or in the goal with [saved].
  Future<void> saveReserve({
    required String currency,
    required int? months,
    required String target,
    required String? accountId,
    required String saved,
    required String? monthly,
    required String defaultName,
  }) async {
    final reserve = reserves.value?.reserveIn(currency);
    final goal = reserve == null
        ? null
        : goals.where((g) => g.id == reserve.goalId).firstOrNull;
    await repository.saveReserve(reserve?.goalId, {
      'name': goal?.name ??
          (reserve?.name.isNotEmpty ?? false ? reserve!.name : defaultName),
      'target_amount': target,
      'saved_amount': accountId == null ? saved : '0',
      'currency': currency,
      'target_on': goal?.targetOn,
      'account_id': accountId,
      'monthly_contribution': monthly,
      'purpose': 'emergency',
      'reserve_months': months ?? 0,
      'notes': goal?.notes,
    });
    await loadReserves();
  }

  /// Turns the exception notices in [on] on and the rest off, with their
  /// thresholds; every other automation switch is kept.
  Future<void> saveNotices(List<String> on,
      {required int days, required String uncategorizedMin}) async {
    final current = automation.value ?? const AutomationPolicy({});
    automation.value = await repository.saveAutomation(current.withNotices(on,
        days: days, uncategorizedMin: uncategorizedMin));
    automation.phase = RecordsPhase.ready;
    _notify();
  }

  void reset() {
    for (final part in [settings, plan, reserves, automation]) {
      part
        ..phase = RecordsPhase.loading
        ..value = null
        ..failure = null;
    }
    goals = const [];
    _notify();
  }

  @override
  void dispose() {
    _disposed = true;
    super.dispose();
  }
}
