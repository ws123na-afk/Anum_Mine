import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Owner agent controls (docs/agent-controls.md): the organization's
/// emergency stop, what it holds, and per-agent monthly cost and token caps.
/// Who may do what is the API's answer (`can_engage`, `can_release`,
/// `can_manage`, or its 403).
class EmergencyStopState {
  const EmergencyStopState(
      {required this.engaged,
      required this.canEngage,
      required this.canRelease,
      this.reason,
      this.changedBy,
      this.changedByYou = false,
      this.changedAt,
      this.cancelledTasks = 0});
  final bool engaged, canEngage, canRelease, changedByYou;
  final String? reason, changedBy;
  final DateTime? changedAt;
  final int cancelledTasks;

  factory EmergencyStopState.fromJson(JsonReader json) {
    final stop = json.object('emergency_stop');
    return EmergencyStopState(
      engaged: stop.boolean('engaged'),
      reason: stop.optString('reason'),
      changedBy: stop.optString('changed_by'),
      changedByYou: stop.optBool('changed_by_you') ?? false,
      changedAt: stop.optDate('changed_at'),
      canEngage: json.optBool('can_engage') ?? false,
      canRelease: json.optBool('can_release') ?? false,
      cancelledTasks: json.optInt('cancelled_tasks') ?? 0,
    );
  }

  /// Whether this person may flip the switch from its current position.
  bool get canChange => engaged ? canRelease : canEngage;
}

class AgentCapItem {
  const AgentCapItem(
      {required this.agentId,
      required this.name,
      required this.status,
      required this.sharing,
      required this.costUsd,
      required this.calls,
      required this.exceeded,
      this.isDefault = false,
      this.limitUsd,
      this.tokenLimit,
      this.tokens = 0});
  final String agentId, name, status, sharing;
  final bool isDefault, exceeded;
  final double? limitUsd;
  final int? tokenLimit;
  final double costUsd;
  final int calls, tokens;

  factory AgentCapItem.fromJson(JsonReader json) {
    final usage = json.object('usage');
    final input = usage.optInt('input_tokens') ?? 0;
    final output = usage.optInt('output_tokens') ?? 0;
    return AgentCapItem(
      agentId: json.string('agent_id'),
      name: json.optString('name') ?? '',
      status: json.optString('status') ?? 'active',
      sharing: json.optString('sharing') ?? 'workspace',
      isDefault: json.optBool('is_default') ?? false,
      limitUsd: json.optNumber('monthly_cost_limit_usd'),
      tokenLimit: json.optInt('monthly_token_limit'),
      costUsd: usage.optNumber('estimated_cost_usd') ?? 0,
      tokens: json.optInt('total_tokens') ?? input + output,
      calls: usage.optInt('calls') ?? 0,
      exceeded: json.optBool('exceeded') ?? false,
    );
  }

  static double? _fraction(num used, num? limit) {
    if (limit == null) return null;
    if (limit <= 0) return 1;
    return (used / limit).clamp(0, 1).toDouble();
  }

  static int? _percent(num used, num? limit) {
    if (limit == null) return null;
    if (limit <= 0) return 100;
    return (used / limit * 100).round();
  }

  /// Share of the cost cap used, 0 to 1 for the bar; null without one.
  double? get fraction => _fraction(costUsd, limitUsd);
  int? get percent => _percent(costUsd, limitUsd);
  double? get tokenFraction => _fraction(tokens, tokenLimit);
  int? get tokenPercent => _percent(tokens, tokenLimit);

  /// The highest share used of either cap, for the "near the cap" badge.
  int get worstPercent =>
      [percent ?? 0, tokenPercent ?? 0].reduce((a, b) => a > b ? a : b);
}

class AgentCapsPage {
  const AgentCapsPage(
      {required this.items,
      required this.total,
      required this.canManage,
      required this.periodStart,
      required this.resetsOn});
  final List<AgentCapItem> items;
  final int total;
  final bool canManage;
  final String periodStart, resetsOn;

  factory AgentCapsPage.fromJson(JsonReader json) => AgentCapsPage(
        items: json.objects('items', AgentCapItem.fromJson),
        total: json.integer('total'),
        canManage: json.optBool('can_manage') ?? false,
        periodStart: json.optString('period_start') ?? '',
        resetsOn: json.optString('resets_on') ?? '',
      );
}

/// One send or action the stop holds: a pending approval, the caller's own
/// scheduled email, or a scheduled social post. Titles come from the API as
/// plain text.
class HeldItem {
  const HeldItem(
      {required this.kind,
      required this.id,
      required this.title,
      required this.detail,
      this.at});
  final String kind, id, title, detail;
  final DateTime? at;

  factory HeldItem.fromJson(JsonReader json) => HeldItem(
        kind: json.optString('kind') ?? 'approval',
        id: json.string('id'),
        title: json.optString('title') ?? '',
        detail: json.optString('detail') ?? '',
        at: json.optDate('at'),
      );
}

class HeldPage {
  const HeldPage(
      {required this.items, required this.total, required this.engaged});
  final List<HeldItem> items;
  final int total;
  final bool engaged;

  factory HeldPage.fromJson(JsonReader json) => HeldPage(
        items: json.objects('items', HeldItem.fromJson),
        total: json.integer('total'),
        engaged: json.optBool('engaged') ?? false,
      );
}

/// A reason as the API wants it: whitespace collapsed, 3 to 300 characters.
/// Returns the cleaned text, or null with the message key in [error].
({String? value, String? error}) parseStopReason(String text) {
  final value = text.split(RegExp(r'\s+')).where((x) => x.isNotEmpty).join(' ');
  if (value.length < 3) return (value: null, error: 'ac.reasonShort');
  if (value.length > 300) return (value: null, error: 'ac.reasonLong');
  return (value: value, error: null);
}

/// A cap typed in USD: empty clears it; otherwise 0 or more.
({double? value, bool ok}) parseCapUsd(String text) {
  final cleaned = text.trim().replaceAll(r'$', '').replaceAll(',', '');
  if (cleaned.isEmpty) return (value: null, ok: true);
  final value = double.tryParse(cleaned);
  if (value == null || value.isNaN || value < 0 || value > 1e9) {
    return (value: null, ok: false);
  }
  return (value: (value * 100).roundToDouble() / 100, ok: true);
}

/// A token cap: empty clears it; otherwise a whole number from 0 up.
({int? value, bool ok}) parseTokenCap(String text) {
  final cleaned = text.trim().replaceAll(',', '').replaceAll(' ', '');
  if (cleaned.isEmpty) return (value: null, ok: true);
  if (!RegExp(r'^\d+$').hasMatch(cleaned)) return (value: null, ok: false);
  final value = int.tryParse(cleaned);
  if (value == null || value > 1000000000000000) {
    return (value: null, ok: false);
  }
  return (value: value, ok: true);
}

abstract interface class AgentControlsRepository {
  Future<EmergencyStopState> status();
  Future<EmergencyStopState> setStop(
      {required bool engaged, required String reason});
  Future<AgentCapsPage> caps(
      {String query = '', int offset = 0, int limit = 20});
  Future<AgentCapItem> setCap(String agentId, double? limitUsd,
      {int? tokenLimit});
  Future<HeldPage> held({int offset = 0, int limit = 20});
}

class ApiAgentControlsRepository implements AgentControlsRepository {
  const ApiAgentControlsRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/agent-controls';

  @override
  Future<EmergencyStopState> status() async =>
      EmergencyStopState.fromJson(JsonReader(await api.request('GET', _base)));

  @override
  Future<EmergencyStopState> setStop(
          {required bool engaged, required String reason}) async =>
      EmergencyStopState.fromJson(JsonReader(await api.request(
          'PUT', '$_base/emergency-stop',
          body: {'engaged': engaged, 'reason': reason})));

  @override
  Future<AgentCapsPage> caps(
      {String query = '', int offset = 0, int limit = 20}) async {
    final params = {
      'limit': '$limit',
      'offset': '$offset',
      if (query.trim().isNotEmpty) 'q': query.trim(),
    };
    final path = Uri(path: '$_base/agent-caps', queryParameters: params);
    return AgentCapsPage.fromJson(
        JsonReader(await api.request('GET', path.toString())));
  }

  @override
  Future<AgentCapItem> setCap(String agentId, double? limitUsd,
          {int? tokenLimit}) async =>
      AgentCapItem.fromJson(JsonReader(await api.request(
          'PUT', '$_base/agent-caps/${Uri.encodeComponent(agentId)}',
          body: {
            'monthly_cost_limit_usd': limitUsd,
            'monthly_token_limit': tokenLimit,
          })));

  @override
  Future<HeldPage> held({int offset = 0, int limit = 20}) async =>
      HeldPage.fromJson(JsonReader(
          await api.request('GET', '$_base/held?limit=$limit&offset=$offset')));
}

/// A message for the screen: a catalog key with values, or the API's own
/// sentence ([raw]).
class ControlsNotice {
  const ControlsNotice(
      {this.key,
      this.values = const {},
      this.raw,
      this.count,
      this.failed = false});
  final String? key, raw;
  final Map<String, Object> values;

  /// For plural keys (`key.one`, `key.other`).
  final int? count;
  final bool failed;
}

enum AgentControlsPhase { loading, ready, error }

enum CapsPhase { loading, ready, forbidden, error }

enum HeldPhase { idle, loading, ready, forbidden, error }

/// State of the agent controls screen: the switch, what it holds and one page
/// of caps.
class AgentControlsController extends ChangeNotifier {
  AgentControlsController(this.repository);
  final AgentControlsRepository repository;
  static const pageSize = 20;

  AgentControlsPhase phase = AgentControlsPhase.loading;
  String? error;
  EmergencyStopState? stop;
  bool changing = false;
  ControlsNotice? stopNotice;

  CapsPhase capsPhase = CapsPhase.loading;
  String? capsError;
  AgentCapsPage? caps;
  String query = '';
  int offset = 0;
  String? savingAgent;
  final Map<String, ControlsNotice> capNotices = {};

  HeldPhase heldPhase = HeldPhase.idle;
  String? heldError;
  HeldPage? held;
  int heldOffset = 0;

  Future<void> load() async {
    phase = AgentControlsPhase.loading;
    error = null;
    notifyListeners();
    try {
      stop = await repository.status();
      phase = AgentControlsPhase.ready;
    } on ApiException catch (e) {
      phase = AgentControlsPhase.error;
      error = e.message;
    } on Object catch (e) {
      phase = AgentControlsPhase.error;
      error = '$e';
    }
    notifyListeners();
    await Future.wait([loadCaps(), loadHeld()]);
  }

  /// What the stop holds: only while it is on and for people who may stop it.
  Future<void> loadHeld() async {
    final current = stop;
    if (current == null || !current.engaged || !current.canEngage) {
      heldPhase = HeldPhase.idle;
      held = null;
      notifyListeners();
      return;
    }
    heldPhase = HeldPhase.loading;
    heldError = null;
    notifyListeners();
    try {
      held = await repository.held(offset: heldOffset, limit: pageSize);
      heldPhase = HeldPhase.ready;
    } on ApiException catch (e) {
      heldPhase = e.isPermissionDenied ? HeldPhase.forbidden : HeldPhase.error;
      heldError = e.message;
    } on Object catch (e) {
      heldPhase = HeldPhase.error;
      heldError = '$e';
    }
    notifyListeners();
  }

  Future<void> heldPage(int nextOffset) async {
    heldOffset = nextOffset < 0 ? 0 : nextOffset;
    await loadHeld();
  }

  Future<void> loadCaps() async {
    capsPhase = CapsPhase.loading;
    capsError = null;
    notifyListeners();
    try {
      caps =
          await repository.caps(query: query, offset: offset, limit: pageSize);
      capsPhase = CapsPhase.ready;
    } on ApiException catch (e) {
      capsPhase = e.isPermissionDenied ? CapsPhase.forbidden : CapsPhase.error;
      capsError = e.message;
    } on Object catch (e) {
      capsPhase = CapsPhase.error;
      capsError = '$e';
    }
    notifyListeners();
  }

  Future<void> search(String text) async {
    query = text;
    offset = 0;
    await loadCaps();
  }

  Future<void> page(int nextOffset) async {
    offset = nextOffset < 0 ? 0 : nextOffset;
    await loadCaps();
  }

  /// Flip the switch with [reason]; the screen confirms before calling this.
  Future<bool> toggle(String reason) async {
    final current = stop;
    if (current == null || changing) return false;
    final parsed = parseStopReason(reason);
    if (parsed.value == null) {
      stopNotice = ControlsNotice(key: parsed.error, failed: true);
      notifyListeners();
      return false;
    }
    changing = true;
    stopNotice = null;
    notifyListeners();
    var ok = false;
    try {
      final next = await repository.setStop(
          engaged: !current.engaged, reason: parsed.value!);
      stop = next;
      stopNotice = next.engaged
          ? ControlsNotice(key: 'ac.engaged', count: next.cancelledTasks)
          : const ControlsNotice(key: 'ac.released');
      ok = true;
    } on ApiException catch (e) {
      stopNotice = ControlsNotice(raw: e.message, failed: true);
    } on Object catch (e) {
      stopNotice = ControlsNotice(raw: '$e', failed: true);
    } finally {
      changing = false;
      notifyListeners();
    }
    if (ok) {
      heldOffset = 0;
      await loadHeld();
    }
    return ok;
  }

  Future<void> saveCap(AgentCapItem item, String costText,
      [String tokenText = '']) async {
    final cost = parseCapUsd(costText);
    final tokens = parseTokenCap(tokenText);
    if (!cost.ok || !tokens.ok) {
      capNotices[item.agentId] = ControlsNotice(
          key: cost.ok ? 'ac.tokenInvalid' : 'ac.capInvalid', failed: true);
      notifyListeners();
      return;
    }
    savingAgent = item.agentId;
    capNotices.remove(item.agentId);
    notifyListeners();
    try {
      final saved = await repository.setCap(item.agentId, cost.value,
          tokenLimit: tokens.value);
      final current = caps;
      if (current != null) {
        caps = AgentCapsPage(
            items: [
              for (final x in current.items)
                x.agentId == saved.agentId ? saved : x
            ],
            total: current.total,
            canManage: current.canManage,
            periodStart: current.periodStart,
            resetsOn: current.resetsOn);
      }
      capNotices[item.agentId] = ControlsNotice(
          key: cost.value == null && tokens.value == null
              ? 'ac.capCleared'
              : 'ac.capSaved',
          values: {'name': item.name});
    } on ApiException catch (e) {
      capNotices[item.agentId] = ControlsNotice(
          key: 'ac.capFailed', values: {'message': e.message}, failed: true);
    } on Object catch (e) {
      capNotices[item.agentId] = ControlsNotice(
          key: 'ac.capFailed', values: {'message': '$e'}, failed: true);
    } finally {
      savingAgent = null;
      notifyListeners();
    }
  }
}
