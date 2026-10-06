import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'retrieval_sources.dart';
import 'workspace_controller.dart';
import 'workspace_models.dart';
import 'workspace_sheets.dart';

/// Which workspace memories and files the run's prompt used, from the run's
/// `retrieval` step (ids and scores only; the API never returns retrieved
/// text on a run). Renders nothing for a run without a retrieval step.
///
/// A source is labelled with the file's name or the memory's text when the
/// loaded workspace lists it (else with its type and id); a listed file opens
/// its detail sheet. Ids are
/// always shown, isolated left-to-right so they read correctly in Arabic.
class SourcesUsed extends StatelessWidget {
  const SourcesUsed({required this.run, required this.controller, super.key});
  final WorkspaceRun? run;
  final WorkspaceController controller;

  @override
  Widget build(BuildContext context) {
    final record = runSources(run);
    if (record == null) return const SizedBox.shrink();
    final p = context.palette;
    final groups = groupSources(record.sources);
    final note = sourcesNote(record);
    final count = groups.isEmpty
        ? 'none'
        : '${groups.length} ${groups.length == 1 ? 'source' : 'sources'}';
    return AnumSurface(
      key: const Key('sources-used'),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        AnumHeader(
          eyebrow: 'Retrieval',
          title: 'Sources used',
          trailing: Semantics(
            label:
                groups.isEmpty ? 'No sources used' : '$count used by this run',
            excludeSemantics: true,
            child: AnumPill(
              label: count,
              tone: switch (record.state) {
                RetrievalState.ok => AnumTone.ok,
                RetrievalState.unavailable => AnumTone.warn,
                _ => AnumTone.neutral,
              },
            ),
          ),
        ),
        for (final group in groups) _SourceRow(group, controller),
        if (note != null) ...[
          const SizedBox(height: 8),
          Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Icon(Icons.menu_book_outlined, size: 16, color: p.faint),
            const SizedBox(width: 6),
            Expanded(
                child:
                    Text(note, style: TextStyle(color: p.muted, fontSize: 13))),
          ]),
        ],
        if (groups.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(
              'Retrieved text is passed to the model as labeled, untrusted '
              'data; it cannot change tools or approvals.',
              style: TextStyle(color: p.faint, fontSize: 12)),
        ],
      ]),
    );
  }
}

/// First-strong isolate so a Latin id keeps its order inside RTL text.
String _ltr(String value) => '\u2068$value\u2069';

class _SourceRow extends StatelessWidget {
  const _SourceRow(this.group, this.controller);
  final SourceGroup group;
  final WorkspaceController controller;

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
    final type = sourceTypeLabel(group.sourceType);
    final name = file?.name ?? memory?.content;
    final passages = group.passages.map(passageLabel).join('; ');
    final open = file == null
        ? null
        : () => showModalBottomSheet<void>(
              context: context,
              builder: (_) => FileDetailSheet(
                file: file!,
                onDownload: () => controller.repository.downloadFile(file!),
                onDelete: () => controller.deleteFile(file!.id),
              ),
            );
    final spoken = [
      '$type ${name ?? group.sourceId}',
      passages,
      if (open != null) 'opens file details',
    ].join(', ');
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
