import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import 'records_kit.dart';
import 'records_widgets.dart';

enum RecordsInputKind { text, number, date, time, choice, toggle, multiline }

/// One field of a [RecordsForm].
class RecordsInput {
  const RecordsInput._(this.kind, this.name, this.label,
      {this.required = false,
      this.initial,
      this.options = const {},
      this.help,
      this.maxLength});

  const RecordsInput.text(String name, String label,
      {bool required = false, String? initial, String? help, int? maxLength})
      : this._(RecordsInputKind.text, name, label,
            required: required, initial: initial, help: help, maxLength: maxLength);
  const RecordsInput.multiline(String name, String label,
      {bool required = false, String? initial, String? help, int? maxLength})
      : this._(RecordsInputKind.multiline, name, label,
            required: required, initial: initial, help: help, maxLength: maxLength);

  /// A decimal: Arabic-Indic digits and a comma are accepted and sent as
  /// a plain decimal string.
  const RecordsInput.number(String name, String label,
      {bool required = false, String? initial, String? help})
      : this._(RecordsInputKind.number, name, label,
            required: required, initial: initial, help: help);
  const RecordsInput.date(String name, String label,
      {bool required = false, String? initial, String? help})
      : this._(RecordsInputKind.date, name, label,
            required: required, initial: initial, help: help);

  /// A time of day, `HH:MM`.
  const RecordsInput.time(String name, String label,
      {bool required = false, String? initial, String? help})
      : this._(RecordsInputKind.time, name, label,
            required: required, initial: initial, help: help);
  const RecordsInput.choice(String name, String label,
      {required Map<String, String> options,
      bool required = true,
      String? initial,
      String? help})
      : this._(RecordsInputKind.choice, name, label,
            options: options, required: required, initial: initial, help: help);
  const RecordsInput.toggle(String name, String label,
      {bool initial = false, String? help})
      : this._(RecordsInputKind.toggle, name, label,
            initial: initial ? 'true' : null, help: help);

  final RecordsInputKind kind;
  final String name, label;
  final bool required;
  final String? initial, help;
  final Map<String, String> options;
  final int? maxLength;
}

/// The form's values: text, number, date, time and choice fields as
/// trimmed strings (null when empty), toggles as booleans.
typedef RecordsValues = Map<String, Object?>;

/// A form in a bottom sheet: fields, validation, Save with a busy state,
/// and the API's refusal under the fields. [onError] may add an action for
/// a known refusal (the odometer correction). Pops `true` once saved.
class RecordsForm extends StatefulWidget {
  const RecordsForm({
    required this.title,
    required this.fields,
    required this.submitLabel,
    required this.onSubmit,
    this.intro,
    this.onError,
    super.key,
  });

  final String title;
  final String? intro;
  final List<RecordsInput> fields;
  final String submitLabel;
  final Future<void> Function(RecordsValues values) onSubmit;
  final Widget? Function(Object error, RecordsValues values)? onError;

  @override
  State<RecordsForm> createState() => _RecordsFormState();
}

class _RecordsFormState extends State<RecordsForm> {
  final _form = GlobalKey<FormState>();
  late final Map<String, TextEditingController> _text = {
    for (final field in widget.fields)
      if (field.kind != RecordsInputKind.toggle &&
          field.kind != RecordsInputKind.choice)
        field.name: TextEditingController(text: field.initial ?? ''),
  };
  late final Map<String, String?> _choice = {
    for (final field in widget.fields)
      if (field.kind == RecordsInputKind.choice) field.name: field.initial,
  };
  late final Map<String, bool> _toggle = {
    for (final field in widget.fields)
      if (field.kind == RecordsInputKind.toggle)
        field.name: field.initial == 'true',
  };
  bool _saving = false;
  Object? _error;
  RecordsValues _sent = const {};

  @override
  void dispose() {
    for (final controller in _text.values) {
      controller.dispose();
    }
    super.dispose();
  }

  RecordsValues _values() => {
        for (final field in widget.fields)
          field.name: switch (field.kind) {
            RecordsInputKind.toggle => _toggle[field.name] ?? false,
            RecordsInputKind.choice => _choice[field.name],
            RecordsInputKind.number =>
              decimalText(_text[field.name]!.text),
            _ => _text[field.name]!.text.trim().isEmpty
                ? null
                : _text[field.name]!.text.trim(),
          },
      };

  Future<void> _submit() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final values = _values();
    setState(() {
      _saving = true;
      _error = null;
      _sent = values;
    });
    try {
      await widget.onSubmit(values);
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _field(RecordsInput field, KitStrings k) {
    final key = Key('field-${field.name}');
    switch (field.kind) {
      case RecordsInputKind.toggle:
        return SwitchListTile(
          key: key,
          contentPadding: EdgeInsets.zero,
          title: Text(field.label),
          subtitle: field.help == null ? null : Text(field.help!),
          value: _toggle[field.name] ?? false,
          onChanged: _saving
              ? null
              : (value) => setState(() => _toggle[field.name] = value),
        );
      case RecordsInputKind.choice:
        return DropdownButtonFormField<String>(
          key: key,
          initialValue: _choice[field.name],
          isExpanded: true,
          decoration:
              InputDecoration(labelText: field.label, helperText: field.help),
          items: [
            for (final option in field.options.entries)
              DropdownMenuItem(
                  value: option.key,
                  child: Text(option.value, overflow: TextOverflow.ellipsis)),
          ],
          onChanged: _saving
              ? null
              : (value) => setState(() => _choice[field.name] = value),
          validator: (value) =>
              field.required && (value == null || value.isEmpty)
                  ? k.t('required')
                  : null,
        );
      case RecordsInputKind.date:
        return RecordsDateField(
          fieldKey: key,
          controller: _text[field.name]!,
          label: field.label,
          required: field.required,
        );
      case RecordsInputKind.time:
        final controller = _text[field.name]!;
        return TextFormField(
          key: key,
          controller: controller,
          keyboardType: TextInputType.datetime,
          decoration: InputDecoration(
            labelText: field.label,
            hintText: 'HH:MM',
            helperText: field.help,
            suffixIcon: IconButton(
              tooltip: field.label,
              icon: const Icon(Icons.schedule),
              onPressed: () async {
                final picked = await showTimePicker(
                    context: context,
                    initialTime: parseTime(controller.text) ?? TimeOfDay.now());
                if (picked != null) {
                  controller.text = '${picked.hour.toString().padLeft(2, '0')}:'
                      '${picked.minute.toString().padLeft(2, '0')}';
                }
              },
            ),
          ),
          validator: (value) {
            final text = value?.trim() ?? '';
            if (text.isEmpty) return field.required ? k.t('required') : null;
            return parseTime(text) == null ? 'HH:MM' : null;
          },
        );
      case RecordsInputKind.number:
        return TextFormField(
          key: key,
          controller: _text[field.name],
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration:
              InputDecoration(labelText: field.label, helperText: field.help),
          validator: (value) {
            final text = value?.trim() ?? '';
            if (text.isEmpty) return field.required ? k.t('required') : null;
            return decimalText(text) == null ? k.t('invalidNumber') : null;
          },
        );
      case RecordsInputKind.text:
      case RecordsInputKind.multiline:
        final multi = field.kind == RecordsInputKind.multiline;
        return TextFormField(
          key: key,
          controller: _text[field.name],
          minLines: multi ? 2 : 1,
          maxLines: multi ? 6 : 1,
          maxLength: field.maxLength,
          decoration:
              InputDecoration(labelText: field.label, helperText: field.help),
          validator: (value) => field.required && (value?.trim() ?? '').isEmpty
              ? k.t('required')
              : null,
        );
    }
  }

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    final error = _error;
    final extra = error == null ? null : widget.onError?.call(error, _sent);
    return Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Semantics(
            header: true,
            child: Text(widget.title,
                style: Theme.of(context).textTheme.titleLarge),
          ),
          if (widget.intro != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            Text(widget.intro!,
                style: TextStyle(color: context.palette.muted)),
          ],
          for (final field in widget.fields) ...[
            const SizedBox(height: AnumSpacing.sm),
            _field(field, k),
          ],
          if (error != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Semantics(
              liveRegion: true,
              child: Text(
                RecordsFailure.from(error).message,
                key: const Key('form-error'),
                style: TextStyle(color: context.palette.stop),
              ),
            ),
            if (extra != null) ...[
              const SizedBox(height: AnumSpacing.xs),
              extra,
            ],
          ],
          const SizedBox(height: AnumSpacing.md),
          Wrap(
            alignment: WrapAlignment.end,
            spacing: AnumSpacing.xs,
            runSpacing: AnumSpacing.xs,
            children: [
              TextButton(
                onPressed:
                    _saving ? null : () => Navigator.of(context).pop(false),
                child: Text(k.t('cancel')),
              ),
              FilledButton(
                key: const Key('form-submit'),
                onPressed: _saving ? null : _submit,
                child: Text(_saving ? k.t('saving') : widget.submitLabel),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// `HH:MM` as a time of day, or null.
TimeOfDay? parseTime(String text) {
  final match = RegExp(r'^(\d{1,2}):(\d{2})$').firstMatch(text.trim());
  if (match == null) return null;
  final hour = int.parse(match.group(1)!), minute = int.parse(match.group(2)!);
  if (hour > 23 || minute > 59) return null;
  return TimeOfDay(hour: hour, minute: minute);
}

/// A day (`YYYY-MM-DD`) and an optional time (`HH:MM`) as an ISO instant
/// in UTC. Without a time: now when the day is today, else noon.
String instantOf(String day, String? time, {DateTime? now}) {
  final current = now ?? DateTime.now();
  final date = parseDay(day) ?? current;
  final at = time == null ? null : parseTime(time);
  final DateTime local;
  if (at != null) {
    local = DateTime(date.year, date.month, date.day, at.hour, at.minute);
  } else if (isoDay(date) == isoDay(current)) {
    local = current;
  } else {
    local = DateTime(date.year, date.month, date.day, 12);
  }
  return local.toUtc().toIso8601String();
}

/// Opens [form] in a sheet; true when it saved.
Future<bool> openRecordsForm(BuildContext context, RecordsForm form) async =>
    await showRecordsSheet<bool>(context, (_) => form) ?? false;
