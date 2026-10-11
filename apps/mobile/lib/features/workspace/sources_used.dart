import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'resources_screen.dart';
import 'retrieval_sources.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'workspace_sheets.dart';

/// Which workspace memories and files the run's prompt used, from the run's
/// `retrieval` step (ids and scores only; the API never returns retrieved
/// text on a run). Renders nothing for a run without a retrieval step once
/// the run has loaded; while the latest run is loading it says so, and when
/// it failed to load it says why and offers [onRetry].
///
/// A source is labelled with the file's name or the memory's text when the
/// loaded workspace lists it (else with its type and id); a listed file opens
/// its detail sheet and a listed memory opens in the Memory tab. Ids are
/// always shown, isolated left-to-right so they read correctly in Arabic.
class SourcesUsed extends StatelessWidget {
  const SourcesUsed(
      {required this.run,
      required this.controller,
      this.loading = false,
      this.error,
      this.onRetry,
      super.key});
  final WorkspaceRun? run;
  final WorkspaceController controller;

  /// The latest run is still being fetched.
  final bool loading;

  /// Why the latest run could not be fetched, if it could not.
  final Object? error;
  final VoidCallback? onRetry;

  /// Whether the task detail should show this panel at all.
  static bool shows(WorkspaceRun? run, {bool loading = false, Object? error}) =>
      runSources(run) != null || loading || error != null;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final record = runSources(run);
    if (record != null) return _Record(record, controller, l);
    if (error != null) return _Failed(error!, onRetry, l);
    if (loading) return _Loading(l);
    return const SizedBox.shrink();
  }
}

AnumHeader _header(AnumLocalizations l, {Widget? trailing}) => AnumHeader(
    eyebrow: l.t('sourcesEyebrow'),
    title: l.t('sourcesTitle'),
    trailing: trailing);

class _Loading extends StatelessWidget {
  const _Loading(this.l);
  final AnumLocalizations l;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return AnumSurface(
      key: const Key('sources-used'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _header(l),
        const SizedBox(height: AnumSpacing.xs),
        Semantics(
          liveRegion: true,
          child: Row(children: [
            const SizedBox.square(
                dimension: 16,
                child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
                child: Text(l.t('sourcesLoading'),
                    style: TextStyle(color: p.muted, fontSize: 13))),
          ]),
        ),
      ]),
    );
  }
}

class _Failed extends StatelessWidget {
  const _Failed(this.error, this.onRetry, this.l);
  final Object error;
  final VoidCallback? onRetry;
  final AnumLocalizations l;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final failure = error;
    final denied =
        failure is ApiException && {401, 403}.contains(failure.statusCode);
    final raw = failure is ApiException ? failure.message : '$failure';
    final message = raw.endsWith('.') ? raw : '$raw.';
    return AnumSurface(
      key: const Key('sources-used'),
      accent: denied ? p.warn : p.stop,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        _header(l),
        const SizedBox(height: AnumSpacing.xs),
        Semantics(
          liveRegion: true,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(l.t('sourcesErrorTitle'),
                style: TextStyle(
                    color: p.text, fontSize: 14, fontWeight: FontWeight.w600)),
            const SizedBox(height: AnumSpacing.xxs),
            Text(
                l.f(denied ? 'sourcesDeniedHint' : 'sourcesErrorHint',
                    {'message': message}),
                style: TextStyle(color: p.muted, fontSize: 13)),
          ]),
        ),
        // Retrying cannot grant access, so a refusal offers no retry.
        if (onRetry != null && !denied) ...[
          const SizedBox(height: AnumSpacing.sm),
          FilledButton.tonalIcon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: Text(l.t('retry'))),
        ],
      ]),
    );
  }
}

class _Record extends StatelessWidget {
  const _Record(this.record, this.controller, this.l);
  final RunSources record;
  final WorkspaceController controller;
  final AnumLocalizations l;

  @override
  Widget build(BuildContext context) {
    final groups = groupSources(record.sources);
    final note = sourcesNote(record, l);
    final count = sourcesCountLabel(groups.length, l);
    final pill = Semantics(
      container: true,
      label: groups.isEmpty
          ? l.t('sourcesNoneSpoken')
          : l.f('sourcesCountSpoken', {'count': count}),
      excludeSemantics: true,
      child: AnumPill(
        label: count,
        tone: switch (record.state) {
          RetrievalState.ok => AnumTone.ok,
          RetrievalState.unavailable => AnumTone.warn,
          _ => AnumTone.neutral,
        },
      ),
    );
    final scale = MediaQuery.textScalerOf(context).scale(1);
    return AnumSurface(
      key: const Key('sources-used'),
      child: LayoutBuilder(
        builder: (context, constraints) {
          // Beside the title when there is room; below it on a narrow phone
          // or at large text, where it would squeeze the title.
          final inline = constraints.maxWidth >= 240 * scale;
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _header(l, trailing: inline ? pill : null),
                if (!inline) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  pill,
                ],
                ..._body(context, groups, note),
              ]);
        },
      ),
    );
  }

  List<Widget> _body(
      BuildContext context, List<SourceGroup> groups, String? note) {
    final p = context.palette;
    return [
      for (final group in groups) _SourceRow(group, controller, l),
      if (note != null) ...[
        const SizedBox(height: AnumSpacing.xs),
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.menu_book_outlined, size: 16, color: p.faint),
          const SizedBox(width: 6),
          Expanded(
              child:
                  Text(note, style: TextStyle(color: p.muted, fontSize: 13))),
        ]),
      ],
      if (groups.isNotEmpty) ...[
        const SizedBox(height: AnumSpacing.xs),
        Text(l.t('sourcesUntrusted'),
            style: TextStyle(color: p.faint, fontSize: 12)),
      ],
    ];
  }
}

/// First-strong isolate so a Latin id keeps its order inside RTL text.
String _ltr(String value) => '\u2068$value\u2069';

class _SourceRow extends StatelessWidget {
  const _SourceRow(this.group, this.controller, this.l);
  final SourceGroup group;
  final WorkspaceController controller;
  final AnumLocalizations l;

  @override
  Widget build(BuildContext context) {
    WorkspaceFile? file;
    WorkspaceMemory? memory;
    if (group.isMemory) {
      memory =
          controller.memories.where((m) => m.id == group.sourceId).firstOrNull;
    } else {
      file = controller.files.where((f) => f.id == group.sourceId).firstOrNull;
    }
    final type = sourceTypeLabel(group.sourceType, l);
    final name = file?.name ?? memory?.content;
    final passages = group.passages
        .map((passage) => passageLabel(passage, l))
        .join(l.isArabic ? '\u061b ' : '; ');
    VoidCallback? open;
    String? opens;
    if (file != null) {
      final listed = file;
      opens = l.t('sourcesOpensFile');
      open = () => showModalBottomSheet<void>(
            context: context,
            builder: (_) => FileDetailSheet(
              file: listed,
              onDownload: () => controller.repository.downloadFile(listed),
              onDelete: () => controller.deleteFile(listed.id),
            ),
          );
    } else if (memory != null) {
      final listed = memory;
      opens = l.t('sourcesOpensMemory');
      open = () => openMemoryInResources(context, controller, listed.id);
    }
    final spoken = [
      '$type ${name ?? group.sourceId}',
      passages,
      if (opens != null) opens,
    ].join(l.isArabic ? '\u060c ' : ', ');
    return Semantics(
      container: true,
      button: open != null,
      label: spoken,
      excludeSemantics: true,
      onTap: open,
      child: AnumRow(
        icon: group.isMemory
            ? Icons.psychology_outlined
            : Icons.description_outlined,
        title: name ?? '$type ${_ltr(group.sourceId)}',
        subtitle: name == null ? null : '$type · ${_ltr(group.sourceId)}',
        detail: passages,
        trailing: open == null ? null : const Icon(Icons.chevron_right),
        onTap: open,
      ),
    );
  }
}
