import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'home_controller.dart';
import 'home_models.dart';
import 'home_widgets.dart';

/// Dialogs and sheets of Home: the greeting name, quick commands and pins.
/// A failed save is said inside the sheet (`HomeController.lastError`).

Widget _error(BuildContext context, HomeController controller) {
  final message = controller.lastError;
  if (message == null) return const SizedBox.shrink();
  return Padding(
    padding: const EdgeInsets.only(top: AnumSpacing.xs),
    child: Semantics(
      liveRegion: true,
      child: Text(message,
          style: TextStyle(color: Theme.of(context).colorScheme.error)),
    ),
  );
}

Future<void> showNameDialog(BuildContext context, HomeController controller) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  final field =
      TextEditingController(text: controller.summary?.displayName ?? '');
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(t.t('homeNameTitle')),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(
          controller: field,
          autofocus: true,
          maxLength: 80,
          textCapitalization: TextCapitalization.words,
          decoration: InputDecoration(
              labelText: t.t('homeNameLabel'),
              helperText: t.t('homeNameHint'),
              helperMaxLines: 3),
        ),
        ListenableBuilder(
            listenable: controller,
            builder: (context, _) => _error(context, controller)),
      ]),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(t.t('homeCancel'))),
        FilledButton(
          onPressed: () async {
            final ok = await controller.setDisplayName(field.text);
            if (ok && context.mounted) Navigator.pop(context);
          },
          child: Text(t.t('homeSave')),
        ),
      ],
    ),
  );
}

String modeLabel(AnumLocalizations t, CommandMode mode) => switch (mode) {
      CommandMode.work => t.t('homeModeWork'),
      CommandMode.personal => t.t('homeModePersonal'),
      CommandMode.custom => t.t('homeModeCustom'),
    };

/// New or edit ([command] given) quick command.
Future<void> showCommandEditor(BuildContext context, HomeController controller,
    {QuickCommand? command}) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  final label = TextEditingController(text: command?.label ?? '');
  final prompt = TextEditingController(text: command?.prompt ?? '');
  var mode = command?.mode ?? controller.mode;
  final form = GlobalKey<FormState>();
  String? required(String? v) =>
      (v ?? '').trim().isEmpty ? t.t('homeRequired') : null;
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => Padding(
        padding: EdgeInsets.fromLTRB(AnumSpacing.lg, 0, AnumSpacing.lg,
            AnumSpacing.lg + MediaQuery.viewInsetsOf(context).bottom),
        child: Form(
          key: form,
          child: ListView(shrinkWrap: true, children: [
            Semantics(
              header: true,
              child: Text(
                  t.t(command == null ? 'homeCommandNew' : 'homeCommandEdit'),
                  style: Theme.of(context).textTheme.titleLarge),
            ),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
              controller: label,
              maxLength: 60,
              validator: required,
              decoration: InputDecoration(labelText: t.t('homeCommandLabel')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            TextFormField(
              controller: prompt,
              maxLength: 1000,
              minLines: 2,
              maxLines: 5,
              validator: required,
              decoration: InputDecoration(labelText: t.t('homeCommandPrompt')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Text(t.t('homeCommandMode')),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  for (final m in CommandMode.values)
                    ChoiceChip(
                      label: Text(modeLabel(t, m)),
                      selected: mode == m,
                      onSelected: (_) => setState(() => mode = m),
                    ),
                ]),
            ListenableBuilder(
                listenable: controller,
                builder: (context, _) => _error(context, controller)),
            const SizedBox(height: AnumSpacing.lg),
            Wrap(
                alignment: WrapAlignment.end,
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  if (command != null)
                    TextButton.icon(
                      onPressed: () async {
                        final ok = await controller.removeCommand(command);
                        if (ok && context.mounted) Navigator.pop(context);
                      },
                      icon: const Icon(Icons.delete_outline),
                      label: Text(t.t('homeDelete')),
                    ),
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: Text(t.t('homeCancel'))),
                  FilledButton(
                    onPressed: () async {
                      if (!form.currentState!.validate()) return;
                      final ok = command == null
                          ? await controller.addCommand(
                              mode: mode,
                              label: label.text.trim(),
                              prompt: prompt.text.trim())
                          : await controller.updateCommand(command,
                              mode: mode,
                              label: label.text.trim(),
                              prompt: prompt.text.trim());
                      if (ok && context.mounted) Navigator.pop(context);
                    },
                    child: Text(t.t('homeSave')),
                  ),
                ]),
          ]),
        ),
      ),
    ),
  );
}

/// The caller's own quick commands with edit; built-in ones are not listed.
Future<void> showCommandManager(
    BuildContext context, HomeController controller) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final own = controller.commands.where((c) => !c.builtIn).toList();
        return Padding(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.lg, 0, AnumSpacing.lg, AnumSpacing.lg),
          child: ListView(shrinkWrap: true, children: [
            Semantics(
                header: true,
                child: Text(t.t('homeQuickCommands'),
                    style: Theme.of(context).textTheme.titleLarge)),
            _error(context, controller),
            const SizedBox(height: AnumSpacing.xs),
            if (own.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.md),
                child: Text(t.t('homeOwnCommandsNone')),
              ),
            for (final c in own)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(c.label),
                subtitle: Text('${modeLabel(t, c.mode)} · ${c.prompt}',
                    maxLines: 2, overflow: TextOverflow.ellipsis),
                trailing: IconButton(
                  tooltip: t.f('homeCommandOptions', {'name': c.label}),
                  icon: const Icon(Icons.edit_outlined),
                  onPressed: () =>
                      showCommandEditor(context, controller, command: c),
                ),
              ),
            const SizedBox(height: AnumSpacing.md),
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: FilledButton.icon(
                onPressed: () => showCommandEditor(context, controller),
                icon: const Icon(Icons.add),
                label: Text(t.t('homeAddCommand')),
              ),
            ),
          ]),
        );
      },
    ),
  );
}

/// Pin a web app, one of the caller's workspaces or an ANUM feature.
Future<void> showPinEditor(BuildContext context, HomeController controller,
    {required Set<String>? availableFeatures}) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  final form = GlobalKey<FormState>();
  final label = TextEditingController();
  final url = TextEditingController(text: 'https://');
  var kind = PinKind.link;
  String? icon;
  String? workspace;
  final features = [...homeShortcuts, ...homeExtraFeatures]
      .where((f) =>
          f.key != 'more' &&
          (availableFeatures == null || availableFeatures.contains(f.key)))
      .toList();
  var feature = features.isEmpty ? null : features.first.key;
  final workspaces = controller.workspaces();

  String? validUrl(String? value) {
    final uri = Uri.tryParse((value ?? '').trim());
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty) {
      return t.t('homePinUrlInvalid');
    }
    return null;
  }

  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => Padding(
        padding: EdgeInsets.fromLTRB(AnumSpacing.lg, 0, AnumSpacing.lg,
            AnumSpacing.lg + MediaQuery.viewInsetsOf(context).bottom),
        child: Form(
          key: form,
          child: ListView(shrinkWrap: true, children: [
            Semantics(
                header: true,
                child: Text(t.t('homePinNew'),
                    style: Theme.of(context).textTheme.titleLarge)),
            const SizedBox(height: AnumSpacing.md),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  for (final k in PinKind.values)
                    ChoiceChip(
                      label: Text(t.t(switch (k) {
                        PinKind.link => 'homePinKindLink',
                        PinKind.workspace => 'homePinKindWorkspace',
                        PinKind.feature => 'homePinKindFeature',
                      })),
                      selected: kind == k,
                      onSelected: (_) => setState(() => kind = k),
                    ),
                ]),
            const SizedBox(height: AnumSpacing.md),
            TextFormField(
              controller: label,
              maxLength: 60,
              validator: (v) =>
                  (v ?? '').trim().isEmpty ? t.t('homeRequired') : null,
              decoration: InputDecoration(labelText: t.t('homePinLabel')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            if (kind == PinKind.link)
              TextFormField(
                controller: url,
                keyboardType: TextInputType.url,
                validator: validUrl,
                decoration: InputDecoration(labelText: t.t('homePinUrl')),
              ),
            if (kind == PinKind.workspace)
              FutureBuilder<List<PinnableWorkspace>>(
                future: workspaces,
                builder: (context, snap) {
                  if (snap.hasError) {
                    return Text(t.t('homePinWorkspacesFailed'));
                  }
                  if (!snap.hasData) return const LinearProgressIndicator();
                  final items = snap.data!;
                  return DropdownButtonFormField<String>(
                    initialValue: workspace,
                    isExpanded: true,
                    validator: (v) => v == null ? t.t('homeRequired') : null,
                    decoration:
                        InputDecoration(labelText: t.t('homePinWorkspace')),
                    items: [
                      for (final w in items)
                        DropdownMenuItem(
                            value: w.id, child: Text(w.name ?? w.id)),
                    ],
                    onChanged: (v) => setState(() {
                      workspace = v;
                      if (label.text.trim().isEmpty) {
                        label.text =
                            items.firstWhere((w) => w.id == v).name ?? '';
                      }
                    }),
                  );
                },
              ),
            if (kind == PinKind.feature)
              DropdownButtonFormField<String>(
                initialValue: feature,
                isExpanded: true,
                validator: (v) => v == null ? t.t('homeRequired') : null,
                decoration: InputDecoration(labelText: t.t('homePinFeature')),
                items: [
                  for (final f in features)
                    DropdownMenuItem(
                        value: f.key, child: Text(t.t(f.labelKey))),
                ],
                onChanged: (v) => setState(() => feature = v),
              ),
            const SizedBox(height: AnumSpacing.md),
            Text(t.t('homePinIcon')),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  for (final entry in homePinIcons.entries)
                    ChoiceChip(
                      avatar: Icon(entry.value, size: 18),
                      label: Text(t.t('homeIcon_${entry.key}')),
                      selected: icon == entry.key,
                      onSelected: (on) =>
                          setState(() => icon = on ? entry.key : null),
                    ),
                ]),
            ListenableBuilder(
                listenable: controller,
                builder: (context, _) => _error(context, controller)),
            const SizedBox(height: AnumSpacing.lg),
            Wrap(
                alignment: WrapAlignment.end,
                spacing: AnumSpacing.xs,
                children: [
                  TextButton(
                      onPressed: () => Navigator.pop(context),
                      child: Text(t.t('homeCancel'))),
                  FilledButton(
                    onPressed: () async {
                      if (!form.currentState!.validate()) return;
                      final target = switch (kind) {
                        PinKind.link => url.text.trim(),
                        PinKind.workspace => workspace ?? '',
                        PinKind.feature => feature ?? '',
                      };
                      final ok = await controller.addPin(
                          kind: kind,
                          label: label.text.trim(),
                          target: target,
                          icon: icon);
                      if (ok && context.mounted) Navigator.pop(context);
                    },
                    child: Text(t.t('homeSave')),
                  ),
                ]),
          ]),
        ),
      ),
    ),
  );
}

/// Rename, reorder (move up/down, so it works without dragging) and remove.
Future<void> showPinManager(BuildContext context, HomeController controller) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final pins = controller.pins;
        return Padding(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.lg, 0, AnumSpacing.lg, AnumSpacing.lg),
          child: ListView(shrinkWrap: true, children: [
            Semantics(
                header: true,
                child: Text(t.t('homePinsTitle'),
                    style: Theme.of(context).textTheme.titleLarge)),
            _error(context, controller),
            const SizedBox(height: AnumSpacing.xs),
            if (pins.isEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.md),
                child: Text(t.t('homePinNone')),
              ),
            for (var i = 0; i < pins.length; i++)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
                child: Row(children: [
                  Icon(pinIcon(pins[i])),
                  const SizedBox(width: AnumSpacing.sm),
                  Expanded(child: Text(pins[i].label)),
                  IconButton(
                    tooltip: t.f('homeMoveUp', {'name': pins[i].label}),
                    icon: const Icon(Icons.arrow_upward),
                    onPressed:
                        i == 0 ? null : () => controller.movePin(pins[i], -1),
                  ),
                  IconButton(
                    tooltip: t.f('homeMoveDown', {'name': pins[i].label}),
                    icon: const Icon(Icons.arrow_downward),
                    onPressed: i == pins.length - 1
                        ? null
                        : () => controller.movePin(pins[i], 1),
                  ),
                  IconButton(
                    tooltip: t.f('homeRename', {'name': pins[i].label}),
                    icon: const Icon(Icons.edit_outlined),
                    onPressed: () => _renamePin(context, controller, pins[i]),
                  ),
                  IconButton(
                    tooltip: t.f('homeRemove', {'name': pins[i].label}),
                    icon: const Icon(Icons.delete_outline),
                    onPressed: () => controller.removePin(pins[i]),
                  ),
                ]),
              ),
          ]),
        );
      },
    ),
  );
}

Future<void> _renamePin(
    BuildContext context, HomeController controller, HomePin pin) {
  final t = AnumLocalizations.maybeOf(context);
  controller.lastError = null;
  final field = TextEditingController(text: pin.label);
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(t.f('homeRename', {'name': pin.label})),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        TextField(
            controller: field,
            autofocus: true,
            maxLength: 60,
            decoration: InputDecoration(labelText: t.t('homePinLabel'))),
        ListenableBuilder(
            listenable: controller,
            builder: (context, _) => _error(context, controller)),
      ]),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(t.t('homeCancel'))),
        FilledButton(
          onPressed: () async {
            if (field.text.trim().isEmpty) return;
            final ok = await controller.renamePin(pin, field.text.trim());
            if (ok && context.mounted) Navigator.pop(context);
          },
          child: Text(t.t('homeSave')),
        ),
      ],
    ),
  );
}
