import 'package:flutter/material.dart';

import '../../data/api_models.dart';
import '../../src/theme/anum_theme.dart';

/// Model providers a workspace can choose (docs/model-gateway.md), shared by
/// first-run setup and Settings. The ANUM API server calls the provider, so
/// keys are write-only and never come back.
const anthropicFastModel = 'claude-haiku-5-5';
const anthropicCapableModel = 'claude-sonnet-5-5';
const anthropicBaseUrl = 'https://api.anthropic.com';

/// Model and base URL defaults per provider.
const modelPresets = <String, (String, String)>{
  'anthropic': (anthropicCapableModel, anthropicBaseUrl),
  'openai_compatible': ('gpt-4.1-mini', 'https://api.openai.com/v1'),
  'ollama': ('llama3.2', 'http://localhost:11434/v1'),
  'mock': ('mock', 'http://localhost'),
};

const modelProviderLabels = <String, String>{
  'anthropic': 'Anthropic (Claude)',
  'openai_compatible': 'OpenAI compatible',
  'ollama': 'Ollama (free, runs on your computer)',
  'mock': 'Local mock',
};

/// `output_config.effort` choices; '' sends none (the model's default).
const modelEfforts = <String, String>{
  '': "Model's default",
  'low': 'Low (quickest)',
  'medium': 'Medium',
  'high': 'High',
  'xhigh': 'Extra high',
  'max': 'Max',
};

bool providerNeedsKey(String provider) =>
    provider == 'openai_compatible' || provider == 'anthropic';

String providerLabel(String provider) =>
    modelProviderLabels[provider] ?? provider;

List<DropdownMenuItem<String>> providerItems() => [
      for (final entry in modelProviderLabels.entries)
        DropdownMenuItem(value: entry.key, child: Text(entry.value)),
    ];

/// The base URL as the API stores it: no trailing slash; for Claude an empty
/// value is api.anthropic.com and an OpenAI-style `/v1` suffix is dropped.
String normalizedBaseUrl(String provider, String baseUrl) {
  var url = baseUrl.trim().replaceAll(RegExp(r'/+$'), '');
  if (provider == 'anthropic') {
    if (url.isEmpty) url = anthropicBaseUrl;
    if (url.endsWith('/v1')) url = url.substring(0, url.length - 3);
  }
  return url;
}

/// What an empty key field does to the saved key when the form is saved.
enum SavedKeyState {
  /// Same provider and base URL: the API keeps the saved key.
  keep,

  /// Same provider, new base URL: a saved key is never sent to a new
  /// address, so a key is needed.
  moved,

  /// No saved key for this provider.
  none,
}

SavedKeyState savedKeyState(
    String provider, String baseUrl, ModelConfiguration? saved) {
  if (saved == null ||
      !saved.credentialConfigured ||
      saved.provider != provider) {
    return SavedKeyState.none;
  }
  return normalizedBaseUrl(provider, baseUrl) ==
          normalizedBaseUrl(saved.provider, saved.baseUrl)
      ? SavedKeyState.keep
      : SavedKeyState.moved;
}

/// The key field's validator: empty is fine only while the saved key is kept.
String? validateApiKey(String? value, SavedKeyState state) {
  if ((value ?? '').trim().isNotEmpty || state == SavedKeyState.keep) {
    return null;
  }
  return state == SavedKeyState.moved
      ? 'The base URL changed: enter the API key for this address'
      : 'Enter the API key';
}

/// The base URL is optional for Claude (empty means api.anthropic.com).
String? validateBaseUrl(String provider, String? value) {
  final text = (value ?? '').trim();
  if (text.isEmpty && provider == 'anthropic') return null;
  final uri = Uri.tryParse(text);
  return uri != null && uri.isAbsolute ? null : 'Enter an absolute URL';
}

/// Claude's routing fields: the quick-replies and planning models, each with
/// its effort. The planning model is also the workspace's default model.
class ClaudeRoutingFields extends StatelessWidget {
  const ClaudeRoutingFields({
    required this.fastModel,
    required this.capableModel,
    required this.fastEffort,
    required this.capableEffort,
    required this.onFastEffort,
    required this.onCapableEffort,
    this.enabled = true,
    super.key,
  });

  final TextEditingController fastModel;
  final TextEditingController capableModel;
  final String fastEffort;
  final String capableEffort;
  final ValueChanged<String> onFastEffort;
  final ValueChanged<String> onCapableEffort;
  final bool enabled;

  Widget _effort(String label, String value, ValueChanged<String> onChanged) =>
      DropdownButtonFormField<String>(
        initialValue: modelEfforts.containsKey(value) ? value : '',
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: [
          for (final entry in modelEfforts.entries)
            DropdownMenuItem(value: entry.key, child: Text(entry.value)),
        ],
        onChanged: enabled
            ? (v) {
                if (v != null) onChanged(v);
              }
            : null,
      );

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextFormField(
            controller: fastModel,
            enabled: enabled,
            textDirection: TextDirection.ltr,
            decoration: const InputDecoration(
              labelText: 'Quick replies model',
              helperText:
                  'Chat, voice and memory. Empty uses the planning model.',
              helperMaxLines: 2,
              hintText: anthropicFastModel,
            ),
          ),
          const SizedBox(height: AnumSpacing.sm),
          _effort('Quick replies effort', fastEffort, onFastEffort),
          const SizedBox(height: AnumSpacing.sm),
          TextFormField(
            controller: capableModel,
            enabled: enabled,
            textDirection: TextDirection.ltr,
            decoration: const InputDecoration(
              labelText: 'Planning model',
              helperText: 'Tasks and agent runs.',
              hintText: anthropicCapableModel,
            ),
            validator: (v) => (v ?? '').trim().isEmpty
                ? 'Enter the planning model, for example $anthropicCapableModel'
                : null,
          ),
          const SizedBox(height: AnumSpacing.sm),
          _effort('Planning effort', capableEffort, onCapableEffort),
          const SizedBox(height: AnumSpacing.xs),
          Text(
            'Anthropic has no embeddings: memory search uses the '
            'local embedder on the ANUM server.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      );
}
