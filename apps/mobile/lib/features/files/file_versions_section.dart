import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../workspace/files_screen.dart' show FilePickerFn, pickDeviceFiles;
import '../workspace/work_status.dart';
import 'file_strings.dart';
import 'file_versions_controller.dart';
import 'file_versions_models.dart';

/// The versions of one file on its detail screen (docs/files.md#versions):
/// the current version, an upload button, and the older versions with
/// download, restore and delete. Every state is handled: loading, an empty
/// list, a failed or denied load, a quarantined version and long lists.
class FileVersionsSection extends StatefulWidget {
  const FileVersionsSection(
      {required this.controller,
      this.currentUserId,
      this.pick = pickDeviceFiles,
      super.key});
  final FileVersionsController controller;
  final String? currentUserId;
  final FilePickerFn pick;

  @override
  State<FileVersionsSection> createState() => _FileVersionsSectionState();
}

class _FileVersionsSectionState extends State<FileVersionsSection> {
  FileStrings? _strings;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final first = _strings == null;
    _strings = FileStrings.of(context);
    if (first) widget.controller.load(_strings!);
  }

  @override
  void didUpdateWidget(FileVersionsSection old) {
    super.didUpdateWidget(old);
    if (old.controller != widget.controller) widget.controller.load(s);
  }

  FileStrings get s => _strings ?? FileStrings.english;

  Future<void> _upload() async {
    final picked = await widget.pick();
    if (picked.isEmpty) return;
    final (path, name, size) = picked.first;
    await widget.controller.uploadVersion(path, name, size, s);
  }

  Future<void> _confirmDelete(FileVersion version) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('ver.confirmTitle', {'version': version.version})),
        content: Text(s.t('ver.confirmText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(s.t('ver.keep'))),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(s.t('ver.confirmDelete'))),
        ],
      ),
    );
    if (ok == true) await widget.controller.delete(version, s);
  }

  String _who(String user) =>
      widget.currentUserId != null && user == widget.currentUserId
          ? s.t('you')
          : user;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => _build(context),
      );

  Widget _build(BuildContext context) {
    final c = widget.controller;
    final p = context.palette;
    final upload = c.upload;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(s.t('ver.title'),
                style: TextStyle(
                    color: p.text, fontWeight: FontWeight.w700, fontSize: 16)),
            AnumPill(
                label: s.t('ver.current', {'version': c.file.version}),
                tone: AnumTone.neutral),
          ],
        ),
        const SizedBox(height: 4),
        Text(s.t('ver.help'), style: TextStyle(color: p.muted, fontSize: 13)),
        if (c.canChange) ...[
          const SizedBox(height: 8),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              onPressed: c.busy ? null : _upload,
              icon: const Icon(Icons.upload_rounded),
              label: Text(
                  upload != null ? s.t('ver.uploading') : s.t('ver.upload')),
            ),
          ),
        ],
        if (upload != null) ...[
          const SizedBox(height: 8),
          LinearProgressIndicator(
              value: upload.fraction,
              semanticsLabel: s.t('ver.uploading'),
              semanticsValue: '${(upload.fraction * 100).round()}%'),
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: TextButton(
                onPressed: c.cancelUpload,
                child:
                    Text(MaterialLocalizations.of(context).cancelButtonLabel)),
          ),
        ],
        if (c.error != null) ...[
          const SizedBox(height: 8),
          Semantics(
              liveRegion: true,
              child: Text(c.error!, style: TextStyle(color: p.stop))),
        ],
        if (c.note != null) ...[
          const SizedBox(height: 8),
          Semantics(
              liveRegion: true,
              child: Text(c.note!, style: TextStyle(color: p.muted))),
        ],
        const SizedBox(height: 8),
        ..._body(c, p),
        if (!c.canChange && c.phase == VersionsPhase.ready) ...[
          const SizedBox(height: 8),
          Text(s.t('ver.manageDenied'),
              style: TextStyle(color: p.faint, fontSize: 12)),
        ],
      ]),
    );
  }

  List<Widget> _body(FileVersionsController c, AnumPalette p) {
    switch (c.phase) {
      case VersionsPhase.loading:
        return [
          Center(
              child:
                  CircularProgressIndicator(semanticsLabel: s.t('ver.loading')))
        ];
      case VersionsPhase.denied:
        return [Text(s.t('ver.denied'), style: TextStyle(color: p.muted))];
      case VersionsPhase.error:
        return [
          AnumEmpty(
              icon: Icons.error_outline,
              title: s.t('ver.title'),
              message: c.loadError ?? '',
              action: s.t('stor.retry'),
              onAction: () => c.load(s))
        ];
      case VersionsPhase.ready:
        if (c.items.isEmpty) {
          return [
            AnumEmpty(
                icon: Icons.history,
                title: s.t('ver.empty'),
                message: c.canChange
                    ? s.t('ver.emptyText', {'version': c.file.version})
                    : s.t('ver.emptyReadOnly'))
          ];
        }
        return [
          for (final v in c.items) _row(c, p, v),
          if (c.items.length < c.total)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: OutlinedButton(
                onPressed: c.paging ? null : () => c.loadMore(s),
                child: Text(c.paging
                    ? s.t('ver.loadingMore')
                    : s.t('ver.loadMore',
                        {'shown': c.items.length, 'total': c.total})),
              ),
            ),
        ];
    }
  }

  Widget _row(FileVersionsController c, AnumPalette p, FileVersion v) {
    final working = c.working?.id == v.id;
    final meta = s.t('ver.meta', {
      'size': formatBytes(v.sizeBytes),
      'uploaded': exactTime(v.createdAt),
      'who': _who(v.createdBy),
      'replaced': exactTime(v.replacedAt),
    });
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
          border: Border.all(color: p.line),
          borderRadius: BorderRadius.circular(AnumRadius.md)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(s.t('ver.row', {'version': v.version}),
            style: TextStyle(color: p.text, fontWeight: FontWeight.w600)),
        Text(meta, style: TextStyle(color: p.muted, fontSize: 12)),
        if (!v.downloadable)
          Text(
              s.t('ver.quarantined', {
                'signature': v.scanSignature ?? s.t('ver.unknownSignature')
              }),
              style: TextStyle(color: p.stop, fontSize: 12)),
        const SizedBox(height: 4),
        if (working)
          const LinearProgressIndicator()
        else
          Wrap(spacing: 4, runSpacing: 4, children: [
            if (v.downloadable)
              IconButton(
                  tooltip: s.t('ver.download', {'version': v.version}),
                  onPressed: c.busy ? null : () => c.download(v, s),
                  icon: const Icon(Icons.download)),
            if (c.canChange && v.downloadable)
              OutlinedButton.icon(
                  onPressed: c.busy ? null : () => c.restore(v, s),
                  icon: const Icon(Icons.restore),
                  label: Text(s.t('ver.restore'),
                      semanticsLabel:
                          s.t('ver.restoreOf', {'version': v.version}))),
            if (c.canChange)
              IconButton(
                  tooltip: s.t('ver.delete', {'version': v.version}),
                  onPressed: c.busy ? null : () => _confirmDelete(v),
                  icon: const Icon(Icons.delete_outline)),
          ]),
      ]),
    );
  }
}
