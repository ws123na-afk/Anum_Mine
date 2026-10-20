import '../../data/json_read.dart';
import '../../src/widgets/depth.dart';
import 'agents_models.dart';

/// The skills an agent loads, as `GET /api/v1/agents/{id}/skills` returns them
/// (docs/agents.md#skills), and the pure helpers behind the Skills tab. The
/// helpers are ports of `apps/web/src/lib/agents.ts` (skillStateTone,
/// sortLoadout, allowableMissingTools, attachSkillChanges, detachSkillChanges,
/// allowToolsChanges, addableSkills, filterLoadout).

/// A loaded list longer than this gets a search field (as on the web).
const skillSearchFrom = 6;

class AgentSkillTool {
  const AgentSkillTool({
    required this.name,
    required this.registered,
    required this.allowed,
    this.granted,
  });
  factory AgentSkillTool.fromReader(JsonReader json) => AgentSkillTool(
        name: json.string('name'),
        registered: json.optBool('registered') ?? false,
        allowed: json.optBool('allowed') ?? false,
        granted: json.optBool('granted'),
      );

  /// The tool exists in this deployment.
  final bool registered;

  /// The agent may use it.
  final bool allowed;

  /// Granted by the workspace installation; null for built-in skills.
  final bool? granted;
  final String name;

  /// Registered, allowed on the agent and not withheld by the installation.
  bool get ok => registered && allowed && granted != false;
}

class AgentSkillState {
  const AgentSkillState({
    required this.id,
    required this.name,
    required this.description,
    required this.source,
    required this.state,
    required this.activation,
    required this.tools,
    this.version,
    this.latestVersion,
    this.riskLevel,
    this.reason,
    this.triggers = const [],
    this.loadsInstructions = false,
    this.upgradeAvailable = false,
  });
  factory AgentSkillState.fromReader(JsonReader json) => AgentSkillState(
        id: json.string('id'),
        name: json.optString('name') ?? json.string('id'),
        description: json.optString('description') ?? '',
        source: json.optString('source') ?? 'unknown',
        version: json.optString('version'),
        latestVersion: json.optString('latest_version'),
        riskLevel: json.optString('risk_level'),
        state: json.optString('state') ?? 'unavailable',
        reason: json.optString('reason'),
        activation: json.optString('activation') ?? 'on_request',
        triggers: json.optStrings('triggers') ?? const [],
        loadsInstructions: json.optBool('loads_instructions') ?? false,
        upgradeAvailable: json.optBool('upgrade_available') ?? false,
        tools: json.optObjects('tools', AgentSkillTool.fromReader),
      );

  final String id, name, description;

  /// `builtin`, `installed` or `unknown`.
  final String source;

  /// `ready`, `needs_tools`, `disabled`, `not_installed`, `blocked`,
  /// `unavailable`.
  final String state;

  /// `on_request` or `always`.
  final String activation;
  final String? version, latestVersion, riskLevel, reason;
  final List<String> triggers;
  final bool loadsInstructions, upgradeAvailable;
  final List<AgentSkillTool> tools;
}

class AgentSkillLoadout {
  const AgentSkillLoadout({
    required this.agentId,
    required this.agentVersion,
    required this.canEdit,
    required this.items,
    required this.ready,
    required this.attention,
  });
  factory AgentSkillLoadout.fromReader(JsonReader json) => AgentSkillLoadout(
        agentId: json.string('agent_id'),
        agentVersion: json.optInt('agent_version') ?? 1,
        canEdit: json.optBool('can_edit') ?? false,
        items: json.optObjects('items', AgentSkillState.fromReader),
        ready: json.optInt('ready') ?? 0,
        attention: json.optInt('attention') ?? 0,
      );
  final String agentId;
  final int agentVersion;
  final bool canEdit;
  final List<AgentSkillState> items;
  final int ready, attention;
}

/// Badge tone for a loadout state (always shown with its text label).
AnumTone skillStateTone(String state) => switch (state) {
      'ready' => AnumTone.ok,
      'needs_tools' || 'disabled' => AnumTone.warn,
      'blocked' || 'unavailable' => AnumTone.stop,
      _ => AnumTone.neutral,
    };

int _byName(String a, String b) {
  final order = a.toLowerCase().compareTo(b.toLowerCase());
  return order != 0 ? order : a.compareTo(b);
}

/// Ready skills first, then the ones that need attention; by name within each
/// group (the id breaks ties so the order is stable).
List<AgentSkillState> sortLoadout(Iterable<AgentSkillState> items) {
  int rank(AgentSkillState s) => s.state == 'ready' ? 0 : 1;
  return [...items]..sort((a, b) {
      final byRank = rank(a) - rank(b);
      if (byRank != 0) return byRank;
      final byName = _byName(a.name, b.name);
      return byName != 0 ? byName : a.id.compareTo(b.id);
    });
}

Set<String> _usableTools(AgentCatalog catalog) => {
      for (final tool in catalog.tools)
        if (tool.availableToYou && !tool.alwaysOn) tool.name,
    };

/// Registered tools a skill needs that the agent does not allow yet but the
/// caller may give it.
List<String> allowableMissingTools(
  AgentSkillState item,
  AgentCatalog? catalog,
) {
  if (catalog == null) return const [];
  final usable = _usableTools(catalog);
  return [
    for (final tool in item.tools)
      if (tool.registered &&
          !tool.allowed &&
          tool.granted != false &&
          usable.contains(tool.name))
        tool.name,
  ];
}

/// The tools a built-in skill brings along when it is added: the ones it
/// requires that the caller may give the agent (the always-on text tool never).
List<String> toolsWithSkill(AgentSkill skill, AgentCatalog catalog) {
  if (skill.source != 'builtin') return const [];
  final usable = _usableTools(catalog);
  return [
    for (final tool in skill.requiredTools)
      if (tool != alwaysOnTool && usable.contains(tool)) tool,
  ];
}

List<String> _sortedUnique(Iterable<String> values) =>
    ({...values}.toList()..sort());

/// The PATCH that adds a skill (and, for a built-in one, the tools it needs
/// that the caller may give), with the version the page read.
Map<String, Object?> attachSkillChanges(
  AgentProfile agent,
  AgentSkill skill,
  AgentCatalog catalog,
) =>
    {
      'allowed_skills': _sortedUnique([...agent.allowedSkills, skill.id]),
      'allowed_tools': _sortedUnique([
        ...agent.allowedTools,
        ...toolsWithSkill(skill, catalog),
      ]),
      'version': agent.version,
    };

/// The PATCH that removes a skill; the tools stay (other skills or the agent
/// may use them).
Map<String, Object?> detachSkillChanges(AgentProfile agent, String skillId) => {
      'allowed_skills': [
        for (final id in agent.allowedSkills)
          if (id != skillId) id,
      ],
      'version': agent.version,
    };

/// The PATCH that allows more tools on the agent.
Map<String, Object?> allowToolsChanges(
  AgentProfile agent,
  Iterable<String> tools,
) =>
    {
      'allowed_tools': _sortedUnique([...agent.allowedTools, ...tools]),
      'version': agent.version,
    };

List<String> _words(String query) => query
    .toLowerCase()
    .split(RegExp(r'\s+'))
    .where((w) => w.isNotEmpty)
    .toList();

/// Catalog skills the agent does not load yet, matching a search over name,
/// id, description and tools; installed skills first, then by name.
List<AgentSkill> addableSkills(
  AgentCatalog? catalog,
  Iterable<String> loaded,
  String query,
) {
  if (catalog == null) return const [];
  final words = _words(query);
  final have = loaded.toSet();
  return catalog.skills.where((skill) => !have.contains(skill.id)).where((s) {
    final haystack =
        '${s.name} ${s.id} ${s.description} ${s.requiredTools.join(' ')}'
            .toLowerCase();
    return words.every(haystack.contains);
  }).toList()
    ..sort((a, b) {
      if (a.source != b.source) return a.source == 'installed' ? -1 : 1;
      return _byName(a.name, b.name);
    });
}

/// Loaded skills matching a search (shown once the list is long).
List<AgentSkillState> filterLoadout(
  Iterable<AgentSkillState> items,
  String query,
) {
  final words = _words(query);
  return [
    for (final item in items)
      if (words.every(
        '${item.name} ${item.id} ${item.description} ${item.tools.map((t) => t.name).join(' ')}'
            .toLowerCase()
            .contains,
      ))
        item,
  ];
}

/// Why a skill does not load: the message key and the tools it names, or null
/// when no case fits (then the API's own sentence is shown). Ready skills have
/// no reason.
({String key, String tools})? skillReasonKey(AgentSkillState item) {
  String names(bool Function(AgentSkillTool) pick) =>
      item.tools.where(pick).map((t) => t.name).join(', ');
  final missing = names((t) => !t.registered);
  final ungranted = names((t) => t.registered && t.granted == false);
  final notAllowed =
      names((t) => t.registered && t.granted != false && !t.allowed);
  switch (item.state) {
    case 'disabled':
      return (key: 'agentSkills.reason.disabled', tools: '');
    case 'not_installed':
      return (key: 'agentSkills.reason.notInstalled', tools: '');
    case 'blocked':
      return (key: 'agentSkills.reason.blocked', tools: '');
    case 'needs_tools':
      if (ungranted.isNotEmpty) {
        return (key: 'agentSkills.reason.notGranted', tools: ungranted);
      }
      if (notAllowed.isNotEmpty) {
        return (key: 'agentSkills.reason.notAllowed', tools: notAllowed);
      }
    case 'unavailable':
      if (missing.isNotEmpty) {
        return (key: 'agentSkills.reason.missingTool', tools: missing);
      }
      if (item.source == 'unknown') {
        return (key: 'agentSkills.reason.unknown', tools: '');
      }
  }
  return null;
}
