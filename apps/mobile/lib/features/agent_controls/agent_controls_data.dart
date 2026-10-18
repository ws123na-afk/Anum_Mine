import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Owner agent controls (docs/agent-controls.md): the organization's
/// emergency stop and per-agent monthly cost caps. Who may do what is the
/// API's answer (`can_engage`, `can_release`, `can_manage`, or its 403).
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
      this.limitUsd});
  final String agentId, name, status, sharing;
  final double? limitUsd;
  final double costUsd;
  final int calls;
  final bool exceeded;

  factory AgentCapItem.fromJson(JsonReader json) {
    final usage = json.object('usage');
    return AgentCapItem(
      agentId: json.string('agent_id'),
      name: json.optString('name') ?? '',
      status: json.optString('status') ?? 'active',
      sharing: json.optString('sharing') ?? 'workspace',
      limitUsd: json.optNumber('monthly_cost_limit_usd'),
      costUsd: usage.optNumber('estimated_cost_usd') ?? 0,
      calls: usage.optInt('calls') ?? 0,
      exceeded: json.optBool('exceeded') ?? false,
    );
  }

  /// Share of the cap used, 0 to 1 for the bar; null without a cap.
  double? get fraction {
    final limit = limitUsd;
    if (limit == null) return null;
    if (limit <= 0) return 1;
    return (costUsd / limit).clamp(0, 1).toDouble();
  }

  /// Uncapped percentage for the text; null without a cap.
  int? get percent {
    final limit = limitUsd;
    if (limit == null) return null;
    if (limit <= 0) return 100;
    return (costUsd / limit * 100).round();
  }
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

/// A reason as the API wants it: whitespace collapsed, 3 to 300 characters.
/// Returns the cleaned text, or null with [error] set.
({String? value, String? error}) parseStopReason(String text) {
  final value = text.split(RegExp(r'\s+')).where((x) => x.isNotEmpty).join(' ');
  if (value.length < 3) {
    return (value: null, error: 'Say why in at least 3 characters.');
  }
  if (value.length > 300) {
    return (value: null, error: 'Keep the reason under 300 characters.');
  }
  return (value: value, error: null);
}

/// A cap typed in USD: empty clears it; otherwise 0 or more.
({double? value, bool ok}) parseCapUsd(String text) {
  final cleaned = text.trim().replaceAll(r'$', '').replaceAll(',', '');
  if (cleaned.isEmpty) return (value: null, ok: true);
  final value = double.tryParse(cleaned);
  if (value == null || value < 0 || value > 1e9 || value.isNaN) {
    return (value: null, ok: false);
  }
  return (value: (value * 100).roundToDouble() / 100, ok: true);
}

abstract interface class AgentControlsRepository {
  Future<EmergencyStopState> status();
  Future<EmergencyStopState> setStop(
      {required bool engaged, required String reason});
  Future<AgentCapsPage> caps(
      {String query = '', int offset = 0, int limit = 20});
  Future<AgentCapItem> setCap(String agentId, double? limitUsd);
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
  Future<AgentCapItem> setCap(String agentId, double? limitUsd) async =>
      AgentCapItem.fromJson(JsonReader(await api.request(
          'PUT', '$_base/agent-caps/${Uri.encodeComponent(agentId)}',
          body: {'monthly_cost_limit_usd': limitUsd})));
}

enum AgentControlsPhase { loading, ready, error }

enum CapsPhase { loading, ready, forbidden, error }

/// State of the agent controls screen: the switch and one page of caps.
class AgentControlsController extends ChangeNotifier {
  AgentControlsController(this.repository);
  final AgentControlsRepository repository;
  static const pageSize = 20;

  AgentControlsPhase phase = AgentControlsPhase.loading;
  String? error;
  EmergencyStopState? stop;
  bool changing = false;
  String? stopNotice;
  bool stopNoticeFailed = false;

  CapsPhase capsPhase = CapsPhase.loading;
  String? capsError;
  AgentCapsPage? caps;
  String query = '';
  int offset = 0;
  String? savingAgent;
  final Map<String, String> capNotices = {};
  final Set<String> failedCaps = {};

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
    await loadCaps();
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
      stopNotice = parsed.error;
      stopNoticeFailed = true;
      notifyListeners();
      return false;
    }
    changing = true;
    stopNotice = null;
    notifyListeners();
    try {
      final next = await repository.setStop(
          engaged: !current.engaged, reason: parsed.value!);
      stop = next;
      stopNoticeFailed = false;
      stopNotice = next.engaged
          ? 'The emergency stop is on. ${next.cancelledTasks == 1 ? '1 running task was' : '${next.cancelledTasks} running tasks were'} cancelled.'
          : 'The emergency stop is off. Agents are running again.';
      return true;
    } on ApiException catch (e) {
      stopNotice = e.message;
      stopNoticeFailed = true;
    } on Object catch (e) {
      stopNotice = '$e';
      stopNoticeFailed = true;
    } finally {
      changing = false;
      notifyListeners();
    }
    return false;
  }

  Future<void> saveCap(AgentCapItem item, String text) async {
    final parsed = parseCapUsd(text);
    if (!parsed.ok) {
      capNotices[item.agentId] = 'Enter an amount in US dollars, 0 or more.';
      failedCaps.add(item.agentId);
      notifyListeners();
      return;
    }
    savingAgent = item.agentId;
    capNotices.remove(item.agentId);
    notifyListeners();
    try {
      final saved = await repository.setCap(item.agentId, parsed.value);
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
      failedCaps.remove(item.agentId);
      capNotices[item.agentId] = parsed.value == null
          ? 'Removed the cap for ${item.name}.'
          : 'Saved the cap for ${item.name}.';
    } on ApiException catch (e) {
      failedCaps.add(item.agentId);
      capNotices[item.agentId] = "Couldn't save: ${e.message}";
    } on Object catch (e) {
      failedCaps.add(item.agentId);
      capNotices[item.agentId] = "Couldn't save: $e";
    } finally {
      savingAgent = null;
      notifyListeners();
    }
  }
}
