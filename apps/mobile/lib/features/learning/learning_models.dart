import '../../data/json_read.dart';

/// What ANUM learned (docs/learning.md), as `/api/v1/learning` sends it.

/// Whether ANUM learns from the caller now, and if not, why.
enum LearningState { on, offByUser, offChatMemory, offOnServer, unknown }

LearningState learningStateOf(String? value) => switch (value) {
      'on' => LearningState.on,
      'off_by_user' => LearningState.offByUser,
      'off_chat_memory' => LearningState.offChatMemory,
      'off_on_server' => LearningState.offOnServer,
      _ => LearningState.unknown,
    };

/// A short badge and a sentence that says who can change it.
(String, String) describeState(LearningState state) => switch (state) {
      LearningState.on => (
          'Learning',
          'ANUM learns from your feedback and edits, and uses your style and preferences when it drafts for you.',
        ),
      LearningState.offByUser => (
          'Off',
          'You turned learning off. Nothing new is learned and your style is not used in drafts. What was learned stays until you delete it.',
        ),
      LearningState.offChatMemory => (
          'Off with chat memory',
          'Learning follows Remember things from my chats, which is off. Turn it on in Settings to learn again. What was learned stays until you delete it.',
        ),
      LearningState.offOnServer => (
          'Off on this server',
          'The server administrator turned learning off (ANUM_LEARNING_ENABLED). What was learned stays until you delete it.',
        ),
      LearningState.unknown => (
          'Unknown',
          'This server did not say whether learning is on. Update the server, then reload.',
        ),
    };

const toneLabels = {
  'formal': 'Formal',
  'neutral': 'Neutral',
  'casual': 'Casual',
};
const lengthLabels = {'short': 'Short', 'medium': 'Medium', 'long': 'Long'};
const languageLabels = {
  'ar': 'Arabic',
  'en': 'English',
  'mixed': 'Arabic and English mixed',
};
const kindLabels = {
  'rating_up': 'Helpful',
  'rating_down': 'Not helpful',
  'draft_accepted': 'Draft sent as is',
  'draft_edited': 'Draft edited',
  'approval_approved': 'Approved',
  'approval_rejected': 'Rejected',
  'retry': 'Retried',
};
const surfaceLabels = {
  'chat': 'Assistant',
  'email': 'Email',
  'whatsapp': 'WhatsApp',
  'task': 'Task',
  'approval': 'Approval',
  'automation': 'Automation',
};

/// A preference scope in words.
String scopeLabel(String scope) {
  if (scope == 'all') return 'Everything';
  final index = scope.indexOf(':');
  if (index < 0) return scope;
  final kind = scope.substring(0, index), value = scope.substring(index + 1);
  return switch (kind) {
    'surface' => switch (value) {
        'chat' => 'Assistant chat',
        'email' => 'Email drafts',
        'whatsapp' => 'WhatsApp drafts',
        'task' => 'Task drafts',
        _ => scope,
      },
    'contact' => 'Contact: $value',
    'tool' => 'Tool: $value',
    'skill' => 'Skill: $value',
    _ => scope,
  };
}

class LearningStyle {
  const LearningStyle({
    this.tone,
    this.length,
    this.targetWords,
    this.greeting,
    this.signOff,
    this.language,
    this.contactLanguages = const {},
    this.samples = 0,
  });

  factory LearningStyle.fromJson(JsonReader json) => LearningStyle(
        tone: json.optString('tone'),
        length: json.optString('length'),
        targetWords: json.optInt('target_words'),
        greeting: json.optString('greeting'),
        signOff: json.optString('sign_off'),
        language: json.optString('language'),
        contactLanguages: {
          for (final entry
              in (json.optMap('contact_languages') ?? const {}).entries)
            if (entry.value is String) entry.key: entry.value as String,
        },
        samples: json.optInt('samples') ?? 0,
      );

  final String? tone, length, greeting, signOff, language;
  final int? targetWords;
  final Map<String, String> contactLanguages;
  final int samples;
}

/// The user's own choices; null keeps what was learned, '' means none.
class StyleOverrides {
  const StyleOverrides({
    this.tone,
    this.length,
    this.greeting,
    this.signOff,
    this.language,
    this.notes,
    this.contactLanguages,
  });

  factory StyleOverrides.fromJson(JsonReader json) => StyleOverrides(
        tone: json.optString('tone'),
        length: json.optString('length'),
        greeting: json.optString('greeting'),
        signOff: json.optString('sign_off'),
        language: json.optString('language'),
        notes: json.optString('notes'),
        contactLanguages: json.optMap('contact_languages')?.map(
            (key, value) => MapEntry(key, value is String ? value : 'en')),
      );

  final String? tone, length, greeting, signOff, language, notes;
  final Map<String, String>? contactLanguages;

  Map<String, Object?> toJson() => {
        'tone': tone,
        'length': length,
        'greeting': greeting,
        'sign_off': signOff,
        'language': language,
        'notes': notes,
        'contact_languages': contactLanguages,
      };
}

class LearningOverview {
  const LearningOverview({
    required this.state,
    required this.enabled,
    required this.derived,
    required this.overrides,
    required this.signals,
    required this.preferences,
    required this.routinesProposed,
    required this.suggestionsOpen,
    required this.canManageAutomations,
    required this.canApplyAutonomy,
  });

  factory LearningOverview.fromJson(Map<String, Object?> body) {
    final json = JsonReader.of(body);
    final profile = json.object('profile'), counts = json.object('counts');
    final signals = counts.optMap('signals') ?? const {};
    return LearningOverview(
      state: learningStateOf(json.optString('state')),
      enabled: profile.optBool('enabled') ?? true,
      derived: LearningStyle.fromJson(profile.object('derived')),
      overrides: profile.optObject('overrides') == null
          ? const StyleOverrides()
          : StyleOverrides.fromJson(profile.object('overrides')),
      signals: signals.values.fold(
        0,
        (sum, v) => sum + (v is num ? v.toInt() : 0),
      ),
      preferences: counts.optInt('preferences') ?? 0,
      routinesProposed: counts.optInt('routines_proposed') ?? 0,
      suggestionsOpen: counts.optInt('suggestions_open') ?? 0,
      canManageAutomations: json.optBool('can_manage_automations') ?? false,
      canApplyAutonomy: json.optBool('can_apply_autonomy') ?? false,
    );
  }

  final LearningState state;
  final bool enabled;
  final LearningStyle derived;
  final StyleOverrides overrides;
  final int signals, preferences, routinesProposed, suggestionsOpen;
  final bool canManageAutomations, canApplyAutonomy;
}

class LearnedPreference {
  const LearnedPreference({
    required this.id,
    required this.scope,
    required this.text,
    required this.fromSuggestion,
  });

  factory LearnedPreference.fromJson(JsonReader json) => LearnedPreference(
        id: json.string('id'),
        scope: json.optString('scope') ?? 'all',
        text: json.string('text'),
        fromSuggestion: json.optString('source') == 'suggestion',
      );

  final String id, scope, text;
  final bool fromSuggestion;
}

class LearnedRoutine {
  const LearnedRoutine({
    required this.id,
    required this.title,
    required this.status,
    required this.cron,
    required this.timezone,
    required this.steps,
    required this.occurrences,
  });

  factory LearnedRoutine.fromJson(JsonReader json) => LearnedRoutine(
        id: json.string('id'),
        title: json.string('title'),
        status: json.string('status'),
        cron: json.optString('cron') ?? '',
        timezone: json.optString('timezone') ?? 'UTC',
        steps: json.optObjects('steps', (step) => step.string('label')),
        occurrences: json.optInt('occurrences') ?? 0,
      );

  final String id, title, status, cron, timezone;
  final List<String> steps;
  final int occurrences;

  String get statusLabel => switch (status) {
        'proposed' => 'Proposed',
        'accepted' => 'Paused automation',
        'confirmed' => 'Scheduled',
        'dismissed' => 'Dismissed',
        _ => status,
      };
}

class ImprovementSuggestion {
  const ImprovementSuggestion({
    required this.id,
    required this.kind,
    required this.title,
    required this.body,
    required this.status,
    required this.needsPermission,
    required this.changesPolicy,
  });

  factory ImprovementSuggestion.fromJson(JsonReader json) =>
      ImprovementSuggestion(
        id: json.string('id'),
        kind: json.optString('kind') ?? 'instruction',
        title: json.string('title'),
        body: json.optString('body') ?? '',
        status: json.optString('status') ?? 'open',
        needsPermission: json.optString('needs_permission'),
        changesPolicy: json.optMap('change')?['type'] == 'approval_policy',
      );

  final String id, kind, title, body, status;
  final String? needsPermission;
  final bool changesPolicy;

  String get kindLabel => switch (kind) {
        'template' => 'Template change',
        'autonomy' => 'Autonomy change',
        _ => 'New instruction',
      };
}

class ImprovementNote {
  const ImprovementNote({
    required this.week,
    required this.summary,
    required this.suggestions,
  });

  factory ImprovementNote.fromJson(JsonReader json) {
    final note = json.object('note');
    final summary = note.optMap('summary') ?? const {};
    return ImprovementNote(
      week: note.string('week'),
      summary: [
        for (final (key, label) in const [
          ('ratings_up', 'Helpful'),
          ('ratings_down', 'Not helpful'),
          ('drafts_edited', 'Drafts edited'),
          ('approvals_rejected', 'Rejected'),
          ('retries', 'Retries'),
          ('run_failures', 'Failed runs'),
        ])
          (label, (summary[key] as num?)?.toInt() ?? 0),
      ],
      suggestions: json.optObjects(
        'suggestions',
        ImprovementSuggestion.fromJson,
      ),
    );
  }

  final String week;
  final List<(String, int)> summary;
  final List<ImprovementSuggestion> suggestions;
}

class LearningSignal {
  const LearningSignal({
    required this.id,
    required this.kind,
    required this.surface,
    required this.createdAt,
    this.tool,
    this.contact,
    this.reason,
    this.finalText,
  });

  factory LearningSignal.fromJson(JsonReader json) => LearningSignal(
        id: json.string('id'),
        kind: json.string('kind'),
        surface: json.string('surface'),
        createdAt: json.date('created_at'),
        tool: json.optString('tool'),
        contact: json.optString('contact'),
        reason: json.optString('reason'),
        finalText: json.optString('final_text'),
      );

  final String id, kind, surface;
  final DateTime createdAt;
  final String? tool, contact, reason, finalText;

  String get title =>
      '${kindLabels[kind] ?? kind} · ${surfaceLabels[surface] ?? surface}';
}

/// One page of a list and how many there are in all.
class LearningPage<T> {
  const LearningPage(this.items, this.total);
  final List<T> items;
  final int total;
}
