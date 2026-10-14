import '../../data/json_read.dart';
import 'finance_models.dart';

/// Finance stages 6 to 9 on the phone (docs/finance.md): SIMULATED paper and
/// shadow portfolios, requests waiting for the person's approval with their
/// code, the kill switch, and limited and wider live use, which are off.
/// Nothing here buys, sells, pays or sends anything.

class PortfolioSummary {
  const PortfolioSummary(
      {required this.id,
      required this.name,
      required this.mode,
      required this.cash,
      required this.realised,
      required this.unrealised,
      required this.positions,
      required this.waiting,
      required this.feeBps,
      required this.slippageBps});
  final String id, name, mode, feeBps, slippageBps;
  final List<CurrencyAmount> cash, realised, unrealised;
  final int positions, waiting;

  factory PortfolioSummary.fromJson(JsonReader r) {
    final costs = r.optObject('assumptions');
    return PortfolioSummary(
      id: r.string('id'),
      name: r.string('name'),
      mode: r.string('mode'),
      cash: r.optObjects('cash', CurrencyAmount.fromJson),
      realised: r.optObjects('realised', CurrencyAmount.fromJson),
      unrealised: r.optObjects('unrealised', CurrencyAmount.fromJson),
      positions: r.optInt('positions') ?? 0,
      waiting: r.optInt('waiting') ?? 0,
      feeBps: costs?.optString('fee_bps') ?? '0',
      slippageBps: costs?.optString('slippage_bps') ?? '0',
    );
  }
}

/// A request through the execution gateway. It runs only when the person
/// approves exactly this [payloadHash], once, before [expiresAt].
class TradeIntent {
  const TradeIntent(
      {required this.id,
      required this.mode,
      required this.kind,
      required this.status,
      required this.payloadHash,
      required this.expiresAt,
      required this.requestedBy,
      this.symbol,
      this.side,
      this.quantity,
      this.currency,
      this.portfolioName,
      this.outcome,
      this.reasons = const [],
      this.notional,
      this.fee,
      this.slippage,
      this.refusal});
  final String id, mode, kind, status, payloadHash, requestedBy;
  final DateTime expiresAt;
  final String? symbol, side, quantity, currency, portfolioName, outcome;
  final String? notional, fee, slippage, refusal;
  final List<String> reasons;

  bool get pending => status == 'pending_approval';

  factory TradeIntent.fromJson(JsonReader r) {
    final decision = r.optObject('decision');
    final estimate = r.optObject('estimate');
    return TradeIntent(
      id: r.string('id'),
      mode: r.string('mode'),
      kind: r.string('kind'),
      status: r.string('status'),
      payloadHash: r.string('payload_hash'),
      expiresAt: r.date('expires_at'),
      requestedBy: r.optString('requested_by') ?? 'person',
      symbol: r.optString('symbol'),
      side: r.optString('side'),
      quantity: r.optString('quantity'),
      currency: r.optString('currency'),
      portfolioName: r.optString('portfolio_name'),
      outcome: decision?.optString('outcome'),
      reasons: [
        for (final reason in decision?.optObjects(
                'reasons', (item) => item.optString('code') ?? '') ??
            const <String>[])
          reason
      ],
      notional: estimate?.optString('notional'),
      fee: estimate?.optString('fee'),
      slippage: estimate?.optString('slippage'),
      refusal: r.optString('refusal'),
    );
  }
}

/// A decimal string without trailing zeros: "10.00000000" to "10".
String trimDecimal(String? value) {
  if (value == null || !value.contains('.')) return value ?? '-';
  final trimmed = value.replaceFirst(RegExp(r'0+$'), '');
  return trimmed.endsWith('.')
      ? trimmed.substring(0, trimmed.length - 1)
      : trimmed;
}

class ExecutionState {
  const ExecutionState(
      {required this.personSwitch,
      required this.globalSwitch,
      required this.canChangeGlobal});
  final bool personSwitch, globalSwitch, canChangeGlobal;

  factory ExecutionState.fromJson(JsonReader r) => ExecutionState(
        personSwitch: r.optObject('person_switch')?.optBool('engaged') ?? false,
        globalSwitch: r.optObject('global_switch')?.optBool('engaged') ?? false,
        canChangeGlobal:
            r.optObject('global_switch')?.optBool('can_change') ?? false,
      );
}

/// One condition live use needs, and whether it holds now.
class LiveCheck {
  const LiveCheck(
      {required this.code, required this.met, this.value, this.needed});
  final String code;
  final bool met;
  final String? value, needed;

  factory LiveCheck.fromJson(JsonReader r) => LiveCheck(
      code: r.string('code'),
      met: r.optBool('met') ?? false,
      value: r.optString('value'),
      needed: r.optString('needed'));
}

class LiveState {
  const LiveState(
      {required this.preconditions,
      required this.wider,
      required this.tier,
      required this.maxTier,
      required this.frozen,
      this.frozenReason});
  final List<LiveCheck> preconditions, wider;
  final int tier, maxTier;
  final bool frozen;
  final String? frozenReason;

  factory LiveState.from(JsonReader live, JsonReader wider) => LiveState(
        preconditions: live.optObjects('preconditions', LiveCheck.fromJson),
        wider: wider.optObjects('criteria', LiveCheck.fromJson),
        tier: wider.optInt('tier') ?? 0,
        maxTier: wider.optObject('ceilings')?.optInt('max_tier') ?? 0,
        frozen: wider.optBool('frozen') ?? false,
        frozenReason: wider.optString('frozen_reason'),
      );
}
