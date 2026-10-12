import '../../data/json_read.dart';

/// User-built agents (docs/agents.md), as `GET /api/v1/agents` returns them.
/// One agent-profile model serves custom agents and department assistants.
class AgentAutonomy {
  const AgentAutonomy({
    this.read = 'auto',
    this.write = 'ask',
    this.external = 'ask',
  });

  factory AgentAutonomy.fromReader(JsonReader json) => AgentAutonomy(
        read: json.optString('read') ?? 'auto',
        write: json.optString('write') ?? 'ask',
        external: json.optString('external') ?? 'ask',
      );

  final String read, write, external;

  String level(String category) => switch (category) {
        'read' => read,
        'write' => write,
        _ => external,
      };

  AgentAutonomy withLevel(String category, String level) => AgentAutonomy(
        read: category == 'read' ? level : read,
        write: category == 'write' ? level : write,
        external: category == 'external' ? level : external,
      );

  Map<String, Object?> toJson() => {
        'read': read,
        'write': write,
        'external': external,
      };
}

/// Outward actions can never be automatic (docs/agents.md#autonomy).
List<String> autonomyLevelsFor(String category) => category == 'external'
    ? const ['off', 'ask']
    : const ['off', 'ask', 'auto'];

const toolCategories = ['read', 'write', 'external'];
const agentCategories = ['work', 'personal', 'custom'];
const alwaysOnTool = 'anum.respond';

class AgentProfile {
  const AgentProfile({
    required this.id,
    required this.name,
    required this.icon,
    required this.color,
    required this.category,
    required this.description,
    required this.instructions,
    required this.modelRoute,
    required this.allowedSkills,
    required this.allowedTools,
    required this.autonomy,
    required this.knowledgeMode,
    required this.knowledgeSources,
    required this.sharing,
    required this.status,
    required this.ownerUserId,
    required this.version,
    required this.canEdit,
    required this.canRun,
    required this.updatedAt,
    this.department,
    this.templateId,
    this.unavailableTools = const [],
    this.unavailableSkills = const [],
  });

  factory AgentProfile.fromReader(JsonReader json) => AgentProfile(
        id: json.string('id'),
        name: json.string('name'),
        icon: json.optString('icon') ?? 'bot',
        color: json.optString('color') ?? '#4f46e5',
        category: json.optString('category') ?? 'custom',
        department: json.optString('department'),
        description: json.optString('description') ?? '',
        instructions: json.optString('instructions') ?? '',
        modelRoute: json.optString('model_route') ?? 'capable',
        allowedSkills: json.optStrings('allowed_skills') ?? const [],
        allowedTools: json.optStrings('allowed_tools') ?? const [],
        autonomy: json.optObject('autonomy') == null
            ? const AgentAutonomy()
            : AgentAutonomy.fromReader(json.object('autonomy')),
        knowledgeMode: json.optString('knowledge_mode') ?? 'none',
        knowledgeSources: json.optObjects(
          'knowledge_sources',
          (s) => (type: s.string('type'), id: s.string('id')),
        ),
        sharing: json.optString('sharing') ?? 'private',
        status: json.optString('status') ?? 'active',
        ownerUserId: json.optString('owner_user_id') ?? '',
        version: json.optInt('version') ?? 1,
        canEdit: json.optBool('can_edit') ?? false,
        canRun: json.optBool('can_run') ?? false,
        updatedAt: json.optDate('updated_at') ?? DateTime.now(),
        templateId: json.optString('template_id'),
        unavailableTools: json.optStrings('unavailable_tools') ?? const [],
        unavailableSkills: json.optStrings('unavailable_skills') ?? const [],
      );

  final String id, name, icon, color, category, description, instructions;
  final String modelRoute, knowledgeMode, sharing, status, ownerUserId;
  final String? department, templateId;
  final List<String> allowedSkills, allowedTools;
  final List<String> unavailableTools, unavailableSkills;
  final List<({String type, String id})> knowledgeSources;
  final AgentAutonomy autonomy;
  final int version;
  final bool canEdit, canRun;
  final DateTime updatedAt;

  bool get paused => status == 'paused';
  bool get shared => sharing == 'workspace';
}

class AgentPage {
  const AgentPage({required this.items, required this.counts, this.nextCursor});
  factory AgentPage.fromReader(JsonReader json) {
    final counts = json.optMap('counts') ?? const {};
    return AgentPage(
      items: json.optObjects('items', AgentProfile.fromReader),
      nextCursor: json.optString('next_cursor'),
      counts: {
        for (final entry in counts.entries)
          if (entry.value is num) entry.key: (entry.value! as num).toInt(),
      },
    );
  }
  final List<AgentProfile> items;
  final String? nextCursor;
  final Map<String, int> counts;
}

class AgentMemory {
  const AgentMemory({
    required this.id,
    required this.content,
    required this.source,
    required this.createdAt,
  });
  factory AgentMemory.fromReader(JsonReader json) => AgentMemory(
        id: json.string('id'),
        content: json.string('content'),
        source: json.optString('source') ?? 'manual',
        createdAt: json.optDate('created_at') ?? DateTime.now(),
      );
  final String id, content, source;
  final DateTime createdAt;
}

class AgentMemoryPage {
  const AgentMemoryPage({
    required this.items,
    required this.total,
    this.nextCursor,
  });
  factory AgentMemoryPage.fromReader(JsonReader json) => AgentMemoryPage(
        items: json.optObjects('items', AgentMemory.fromReader),
        total: json.optInt('total') ?? 0,
        nextCursor: json.optString('next_cursor'),
      );
  final List<AgentMemory> items;
  final int total;
  final String? nextCursor;
}

class AgentTool {
  const AgentTool({
    required this.name,
    required this.description,
    required this.category,
    required this.alwaysOn,
    required this.availableToYou,
    this.reason,
  });
  factory AgentTool.fromReader(JsonReader json) => AgentTool(
        name: json.string('name'),
        description: json.optString('description') ?? '',
        category: json.optString('category') ?? 'read',
        alwaysOn: json.optBool('always_on') ?? false,
        availableToYou: json.optBool('available_to_you') ?? false,
        reason: json.optString('reason'),
      );
  final String name, description, category;
  final bool alwaysOn, availableToYou;
  final String? reason;
}

class AgentSkill {
  const AgentSkill({
    required this.id,
    required this.name,
    required this.description,
    required this.source,
    required this.requiredTools,
  });
  factory AgentSkill.fromReader(JsonReader json) => AgentSkill(
        id: json.string('id'),
        name: json.optString('name') ?? json.string('id'),
        description: json.optString('description') ?? '',
        source: json.optString('source') ?? 'builtin',
        requiredTools: json.optStrings('required_tools') ?? const [],
      );
  final String id, name, description, source;
  final List<String> requiredTools;
}

class AgentTemplate {
  const AgentTemplate({
    required this.id,
    required this.icon,
    required this.color,
    required this.category,
    required this.instructions,
    required this.modelRoute,
    required this.allowedTools,
    required this.allowedSkills,
    required this.knowledgeMode,
    required this.missing,
  });
  factory AgentTemplate.fromReader(JsonReader json) => AgentTemplate(
        id: json.string('id'),
        icon: json.optString('icon') ?? 'bot',
        color: json.optString('color') ?? '#4f46e5',
        category: json.optString('category') ?? 'work',
        instructions: json.optString('instructions') ?? '',
        modelRoute: json.optString('model_route') ?? 'capable',
        allowedTools: json.optStrings('allowed_tools') ?? const [],
        allowedSkills: json.optStrings('allowed_skills') ?? const [],
        knowledgeMode: json.optString('knowledge_mode') ?? 'none',
        missing: json.optStrings('missing_capabilities') ?? const [],
      );
  final String id, icon, color, category, instructions, modelRoute;
  final String knowledgeMode;
  final List<String> allowedTools, allowedSkills, missing;
}

class AgentCatalog {
  const AgentCatalog({
    required this.tools,
    required this.skills,
    required this.templates,
    required this.floor,
  });
  factory AgentCatalog.fromReader(JsonReader json) => AgentCatalog(
        tools: json.optObjects('tools', AgentTool.fromReader),
        skills: json.optObjects('skills', AgentSkill.fromReader),
        templates: json.optObjects('templates', AgentTemplate.fromReader),
        floor: json.optObject('autonomy_floor') == null
            ? const AgentAutonomy()
            : AgentAutonomy.fromReader(json.object('autonomy_floor')),
      );
  final List<AgentTool> tools;
  final List<AgentSkill> skills;
  final List<AgentTemplate> templates;
  final AgentAutonomy floor;
}

/// One of the agent's recent tasks (`GET /api/v1/tasks?agent_id=`).
class AgentTask {
  const AgentTask({
    required this.id,
    required this.title,
    required this.status,
    required this.createdAt,
  });
  factory AgentTask.fromReader(JsonReader json) => AgentTask(
        id: json.string('id'),
        title: json.optString('title') ?? '',
        status: json.optString('status') ?? 'created',
        createdAt: json.optDate('created_at') ?? DateTime.now(),
      );
  final String id, title, status;
  final DateTime createdAt;
}

/// What a run as the agent came back with.
class AgentRunOutcome {
  const AgentRunOutcome({
    required this.taskTitle,
    required this.status,
    required this.waitingApproval,
    this.result,
  });
  final String taskTitle, status;
  final bool waitingApproval;
  final String? result;
}

/// The editable fields: the body of `POST /api/v1/agents` and, as a diff,
/// of `PATCH /api/v1/agents/{id}`.
class AgentDraft {
  AgentDraft({
    this.name = '',
    this.icon = 'bot',
    this.color = '#4f46e5',
    this.category = 'custom',
    this.department = '',
    this.description = '',
    this.instructions = '',
    this.modelRoute = 'capable',
    List<String>? allowedSkills,
    List<String>? allowedTools,
    this.autonomy = const AgentAutonomy(),
    this.knowledgeMode = 'none',
    this.sharing = 'private',
    this.templateId,
  })  : allowedSkills = allowedSkills ?? [],
        allowedTools = allowedTools ?? [];

  factory AgentDraft.fromTemplate(AgentTemplate t, String name) => AgentDraft(
        name: name,
        icon: t.icon,
        color: t.color,
        category: t.category,
        instructions: t.instructions,
        modelRoute: t.modelRoute,
        allowedSkills: [...t.allowedSkills],
        allowedTools: [...t.allowedTools],
        knowledgeMode: t.knowledgeMode == 'selected' ? 'none' : t.knowledgeMode,
        templateId: t.id,
      );

  factory AgentDraft.fromAgent(AgentProfile a) => AgentDraft(
        name: a.name,
        icon: a.icon,
        color: a.color,
        category: a.category,
        department: a.department ?? '',
        description: a.description,
        instructions: a.instructions,
        modelRoute: a.modelRoute,
        allowedSkills: [...a.allowedSkills],
        allowedTools: [...a.allowedTools],
        autonomy: a.autonomy,
        knowledgeMode: a.knowledgeMode,
        sharing: a.sharing,
        templateId: a.templateId,
      );

  String name, icon, color, category, department, description, instructions;
  String modelRoute, knowledgeMode, sharing;
  String? templateId;
  List<String> allowedSkills, allowedTools;
  AgentAutonomy autonomy;

  /// The first problem with the draft, as a string key, or null.
  String? problem() {
    if (name.trim().isEmpty) return 'errNameRequired';
    if (name.trim().length > 80) return 'errNameLong';
    if (description.length > 500) return 'errDescriptionLong';
    if (instructions.length > 8000) return 'errInstructionsLong';
    return null;
  }

  Map<String, Object?> toJson() => {
        'name': name.trim(),
        'icon': icon,
        'color': color,
        'category': category,
        'department': department.trim().isEmpty ? null : department.trim(),
        'description': description.trim(),
        'instructions': instructions.trim(),
        'model_route': modelRoute,
        'allowed_skills': [...allowedSkills]..sort(),
        'allowed_tools': [...allowedTools]..sort(),
        'autonomy': autonomy.toJson(),
        // Selected sources are picked on the web; the app keeps the setting.
        'knowledge_mode': knowledgeMode,
        'sharing': sharing,
        if (templateId != null) 'template_id': templateId,
      };

  /// Only what changed against [agent], with the version the edit started from.
  Map<String, Object?> changesFrom(AgentProfile agent) {
    final before = AgentDraft.fromAgent(agent).toJson();
    final after = toJson()..remove('template_id');
    final changes = <String, Object?>{};
    for (final entry in after.entries) {
      if (entry.key == 'knowledge_mode' && agent.knowledgeMode == 'selected') {
        continue;
      }
      if ('${entry.value}' != '${before[entry.key]}') {
        changes[entry.key] = entry.value;
      }
    }
    return {...changes, 'version': agent.version};
  }

  /// Turning a built-in skill on also allows the tools it needs.
  void toggleSkill(AgentSkill skill, bool on, AgentCatalog catalog) {
    if (on) {
      if (!allowedSkills.contains(skill.id)) allowedSkills.add(skill.id);
      if (skill.source == 'builtin') {
        for (final tool in skill.requiredTools) {
          final usable = catalog.tools.any(
            (t) => t.name == tool && t.availableToYou && !t.alwaysOn,
          );
          if (usable && !allowedTools.contains(tool)) allowedTools.add(tool);
        }
      }
    } else {
      allowedSkills.remove(skill.id);
    }
  }
}
