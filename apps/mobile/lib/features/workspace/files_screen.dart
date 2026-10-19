import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../files/file_strings.dart';
import '../files/file_versions_controller.dart';
import '../files/file_versions_section.dart';
import '../files/storage_screen.dart';
import '../../src/widgets/depth.dart';
import 'files_controller.dart';
import 'files_models.dart';
import 'work_status.dart';
import 'workspace_models.dart';

/// Picks files on the device: (path, name, size) per file, or empty.
typedef FilePickerFn = Future<List<(String, String, int)>> Function();

Future<List<(String, String, int)>> pickDeviceFiles() async {
  final picked =
      await FilePicker.platform.pickFiles(allowMultiple: true, withData: false);
  return [
    for (final f in picked?.files ?? const <PlatformFile>[])
      if (f.path != null) (f.path!, f.name, f.size)
  ];
}

AnumTone _tone(AnumIndexTone tone) => switch (tone) {
      AnumIndexTone.ok => AnumTone.ok,
      AnumIndexTone.warn => AnumTone.warn,
      AnumIndexTone.stop => AnumTone.stop,
      AnumIndexTone.neutral => AnumTone.neutral,
    };

IconData _icon(WorkspaceFile f) => switch (f.kind) {
      'image' => Icons.image_outlined,
      'pdf' => Icons.picture_as_pdf_outlined,
      'text' || 'markdown' || 'data' => Icons.description_outlined,
      'archive' => Icons.folder_zip_outlined,
      _ => Icons.insert_drive_file_outlined,
    };

String uploaderLabel(String createdBy, String? me) =>
    me != null && createdBy == me ? 'You' : createdBy;

/// The Files tab (docs/files.md#mobile): storage, uploads with progress and
/// cancel, search, sort and paging; a row opens [FileDetailScreen].
class FilesTab extends StatefulWidget {
  const FilesTab(
      {required this.controller,
      this.currentUserId,
      this.pick = pickDeviceFiles,
      super.key});
  final FilesController controller;
  final String? currentUserId;
  final FilePickerFn pick;

  @override
  State<FilesTab> createState() => _FilesTabState();
}

class _FilesTabState extends State<FilesTab> {
  final _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    if (widget.controller.phase == FilesPhase.loading) {
      widget.controller.load();
    }
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _upload() async {
    final picked = await widget.pick();
    for (final (path, name, size) in picked) {
      await widget.controller.upload(path: path, name: name, size: size);
    }
  }

  /// A file picked on the storage screen: close it and open the file.
  Future<void> _openFromStorage(String fileId) async {
    final navigator = Navigator.of(context);
    final known = widget.controller.items.where((f) => f.id == fileId);
    if (known.isEmpty) {
      await widget.controller.load();
    }
    final file = widget.controller.items.where((f) => f.id == fileId);
    if (file.isEmpty || !mounted) return;
    navigator.pop();
    // ignore: use_build_context_synchronously
    await openFileDetail(context, widget.controller, file.first,
        currentUserId: widget.currentUserId);
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => _build(context),
      );

  Widget _build(BuildContext context) {
    final c = widget.controller;
    final p = context.palette;
    final usage = c.usage;
    final children = <Widget>[
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(
            spacing: 12,
            runSpacing: 12,
            alignment: WrapAlignment.spaceBetween,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              const AnumHeader(
                  title: 'Files',
                  subtitle: 'Text files are indexed so tasks can use them.'),
              Wrap(spacing: 8, runSpacing: 8, children: [
                if (c.versions != null)
                  OutlinedButton.icon(
                    onPressed: () => openStorageScreen(context, c.versions!,
                        onOpenFile: _openFromStorage),
                    icon: const Icon(Icons.storage_outlined),
                    label: Text(FileStrings.of(context).t('stor.open')),
                  ),
                FilledButton.icon(
                  onPressed: c.canUpload ? _upload : null,
                  icon: const Icon(Icons.upload_rounded),
                  label: const Text('Upload'),
                ),
              ]),
            ],
          ),
          if (usage != null) ...[
            const SizedBox(height: 12),
            Semantics(
              label: 'Workspace storage used',
              value: usage.summary,
              child: ExcludeSemantics(
                child: LinearProgressIndicator(
                  value: usage.fraction,
                  minHeight: 8,
                  borderRadius: BorderRadius.circular(8),
                  color: usage.fraction >= .95
                      ? p.stop
                      : usage.fraction >= .8
                          ? p.warn
                          : p.violet,
                ),
              ),
            ),
            const SizedBox(height: 6),
            Text(usage.summary, style: TextStyle(color: p.muted, fontSize: 13)),
            Text(
                'Up to ${formatBytes(usage.maxUploadBytes)} per file. '
                'Accepted: ${usage.accepted}.',
                style: TextStyle(color: p.faint, fontSize: 12)),
          ],
          if (!c.canUpload) ...[
            const SizedBox(height: 8),
            Text(
                'Uploading needs a member or owner role in this workspace. '
                'You can still open and download files.',
                style: TextStyle(color: p.muted, fontSize: 13)),
          ],
        ]),
      ),
      for (final u in c.uploads) _UploadRow(controller: c, upload: u),
      if (c.notice != null)
        Semantics(
            liveRegion: true,
            child: Text(c.notice!, style: TextStyle(color: p.muted))),
      if (c.phase == FilesPhase.denied)
        const AnumSurface(
            child: AnumEmpty(
                icon: Icons.lock_outline,
                title: 'No access to files',
                message: filesReadDenied)),
      if (c.phase == FilesPhase.error)
        AnumSurface(
            child: AnumEmpty(
                icon: Icons.cloud_off_outlined,
                title: 'Could not load files',
                message:
                    '${c.error ?? 'The request failed.'} Check the connection, then retry.',
                action: 'Retry',
                onAction: c.load)),
      if (c.phase == FilesPhase.ready &&
          (c.items.isNotEmpty || c.query.isNotEmpty))
        _toolbar(c),
      if (c.phase == FilesPhase.loading)
        const Center(
            child: Padding(
                padding: EdgeInsets.all(24),
                child: CircularProgressIndicator(
                    semanticsLabel: 'Loading files'))),
      if (c.phase == FilesPhase.ready && c.items.isEmpty)
        AnumSurface(
          child: c.query.isNotEmpty
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: 'No files match "${c.query}"',
                  message: 'Try another part of the name.',
                  action: 'Clear search',
                  onAction: () {
                    _search.clear();
                    c.search('');
                  })
              : AnumEmpty(
                  icon: Icons.folder_open,
                  title: 'No files yet',
                  message: c.canUpload
                      ? 'Upload documents, data or images. Text files are indexed so tasks can use them.'
                      : 'Nobody has uploaded a file to this workspace yet.',
                  action: c.canUpload ? 'Upload a file' : null,
                  onAction: c.canUpload ? _upload : null),
        ),
      if (c.items.isNotEmpty)
        AnumSurface(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(children: [
            for (final f in c.items)
              AnumRow(
                icon: _icon(f),
                title: f.name,
                subtitle:
                    '${fileTypeLabel(f.contentType)} · ${formatBytes(f.sizeBytes)} · '
                    '${uploaderLabel(f.createdBy, widget.currentUserId)}',
                detail:
                    '${exactTime(f.createdAt)} · ${indexDescription(f.index).$1}',
                trailing: const Icon(Icons.chevron_right),
                onTap: () => openFileDetail(context, c, f,
                    currentUserId: widget.currentUserId),
              ),
          ]),
        ),
      if (c.hasMore && c.items.isNotEmpty)
        Center(
          child: OutlinedButton(
            onPressed: c.paging ? null : c.loadMore,
            child: Text(c.paging ? 'Loading...' : 'Load more files'),
          ),
        ),
    ];
    return RefreshIndicator(
      onRefresh: c.load,
      child: ListView.separated(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
        itemCount: children.length,
        separatorBuilder: (_, __) => const SizedBox(height: 12),
        itemBuilder: (_, i) => children[i],
      ),
    );
  }

  Widget _toolbar(FilesController c) {
    final search = TextField(
      controller: _search,
      textInputAction: TextInputAction.search,
      decoration: InputDecoration(
        labelText: 'Search files by name',
        prefixIcon: const Icon(Icons.search),
        suffixIcon: _search.text.isEmpty
            ? null
            : IconButton(
                tooltip: 'Clear search',
                icon: const Icon(Icons.close),
                onPressed: () {
                  _search.clear();
                  c.search('');
                }),
      ),
      onChanged: (_) => setState(() {}),
      onSubmitted: c.search,
    );
    final sort = DropdownButtonFormField<String>(
      initialValue: c.sort,
      isExpanded: true,
      decoration: const InputDecoration(labelText: 'Sort'),
      onChanged: (v) {
        if (v != null) c.setSort(v);
      },
      items: [
        for (final (value, label) in fileSortOptions)
          DropdownMenuItem(
              value: value,
              child: Text(label, overflow: TextOverflow.ellipsis)),
      ],
    );
    // Side by side when there is room, stacked on phones and at large text.
    return LayoutBuilder(
        builder: (context, box) => box.maxWidth >= 560
            ? Row(children: [
                Expanded(flex: 3, child: search),
                const SizedBox(width: 8),
                Expanded(flex: 2, child: sort),
              ])
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [search, const SizedBox(height: 8), sort]));
  }
}

class _UploadRow extends StatelessWidget {
  const _UploadRow({required this.controller, required this.upload});
  final FilesController controller;
  final FileUpload upload;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final busy = upload.state == UploadState.uploading;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Row(children: [
          Expanded(
              child: Text(upload.name,
                  style:
                      TextStyle(color: p.text, fontWeight: FontWeight.w600))),
          busy
              ? TextButton(
                  onPressed: () => controller.cancelUpload(upload),
                  child: Text('Cancel',
                      semanticsLabel: 'Cancel upload of ${upload.name}'))
              : IconButton(
                  tooltip: 'Dismiss ${upload.name}',
                  icon: const Icon(Icons.close),
                  onPressed: () => controller.dismissUpload(upload)),
        ]),
        if (busy) ...[
          LinearProgressIndicator(
              value: upload.fraction,
              semanticsLabel: 'Uploading ${upload.name}',
              semanticsValue: '${(upload.fraction * 100).round()}%'),
          const SizedBox(height: 4),
          Text(
              '${(upload.fraction * 100).round()}% of ${formatBytes(upload.size)}',
              style: TextStyle(color: p.muted, fontSize: 12)),
        ] else
          Semantics(
            liveRegion: true,
            child: Text(upload.error ?? '',
                style: TextStyle(
                    color:
                        upload.state == UploadState.failed ? p.stop : p.muted)),
          ),
      ]),
    );
  }
}

Future<void> openFileDetail(
        BuildContext context, FilesController controller, WorkspaceFile file,
        {String? currentUserId, FilePickerFn pick = pickDeviceFiles}) =>
    Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => FileDetailScreen(
                controller: controller,
                file: file,
                currentUserId: currentUserId,
                pick: pick)));

/// One file: what it is, who uploaded it, its index state, a preview (text
/// or image), and download, rename, re-index and delete for those allowed.
class FileDetailScreen extends StatefulWidget {
  const FileDetailScreen(
      {required this.controller,
      required this.file,
      this.currentUserId,
      this.pick = pickDeviceFiles,
      super.key});
  final FilesController controller;
  final WorkspaceFile file;
  final String? currentUserId;
  final FilePickerFn pick;

  @override
  State<FileDetailScreen> createState() => _FileDetailScreenState();
}

class _FileDetailScreenState extends State<FileDetailScreen> {
  late WorkspaceFile file = widget.file;
  String? error, note;
  bool busy = false;
  Future<FilePreviewText>? _text;
  Future<Uint8List>? _image;
  FileVersionsController? versions;

  @override
  void initState() {
    super.initState();
    final repository = widget.controller.versions;
    if (repository != null) {
      versions = FileVersionsController(repository, file,
          usage: widget.controller.usage,
          onFileChanged: _versionChangedFile,
          onUsageChanged: widget.controller.loadUsage);
    }
    _loadPreview();
  }

  /// A new version or restore: the file, its size, index state and preview change.
  void _versionChangedFile(WorkspaceFile updated) {
    widget.controller.fileChanged(updated);
    if (!mounted) return;
    setState(() {
      file = updated;
      _text = null;
      _image = null;
      _loadPreview();
    });
  }

  @override
  void dispose() {
    versions?.dispose();
    super.dispose();
  }

  void _loadPreview() {
    switch (previewModeOf(file)) {
      case FilePreviewMode.text:
        _text = widget.controller.repository.previewText(file.id);
      case FilePreviewMode.image:
        _image = widget.controller.repository.bytes(file.id);
      case null:
        break;
    }
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() {
      busy = true;
      error = null;
      note = null;
    });
    try {
      await work();
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> _rename() async {
    final name = await showDialog<String>(
        context: context, builder: (_) => _RenameDialog(current: file.name));
    if (name == null) return;
    await _run(() async {
      final problem = await widget.controller.rename(file, name);
      if (problem != null) {
        error = problem;
      } else {
        file = widget.controller.items
            .firstWhere((f) => f.id == file.id, orElse: () => file);
        note = 'Renamed to ${file.name}.';
      }
    });
  }

  Future<void> _reindex() => _run(() async {
        final (updated, problem) = await widget.controller.reindex(file);
        if (updated != null) {
          file = updated;
          note = indexDescription(updated.index).$3;
        } else {
          error = problem;
        }
      });

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete this file?'),
        content: Text('${file.name} is removed from the workspace and tasks '
            'stop using it. This cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep')),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (ok != true) return;
    await _run(() async {
      final problem = await widget.controller.delete(file);
      if (problem == null) {
        if (mounted) Navigator.pop(context);
      } else {
        error = problem;
      }
    });
  }

  Future<void> _download() => _run(() async {
        try {
          await widget.controller.repository.download(file);
        } catch (e) {
          error = 'Could not download: $e';
        }
      });

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final (label, tone, detail) = indexDescription(file.index);
    final canUpload = widget.controller.canUpload;
    Widget meta(String name, String value) => Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(name, style: TextStyle(color: p.faint, fontSize: 12)),
            SelectableText(value, style: TextStyle(color: p.text)),
          ]),
        );
    return Scaffold(
      appBar: AppBar(title: Text(file.name, overflow: TextOverflow.ellipsis)),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            meta('Kind',
                '${fileTypeLabel(file.contentType)} (${file.contentType})'),
            if (widget.controller.versions != null)
              meta(FileStrings.of(context).t('detail.version'),
                  '${file.version}'),
            meta('Size', formatBytes(file.sizeBytes)),
            meta('Uploaded', exactTime(file.createdAt)),
            meta('Uploaded by',
                uploaderLabel(file.createdBy, widget.currentUserId)),
            if (file.updatedAt != null)
              meta('Renamed', exactTime(file.updatedAt!)),
            Text('Retrieval', style: TextStyle(color: p.faint, fontSize: 12)),
            const SizedBox(height: 4),
            Wrap(spacing: 8, runSpacing: 4, children: [
              AnumPill(label: label, tone: _tone(tone)),
              Text(detail, style: TextStyle(color: p.muted, fontSize: 13)),
            ]),
            const SizedBox(height: 16),
            if (error != null)
              Semantics(
                  liveRegion: true,
                  child: Text(error!, style: TextStyle(color: p.stop))),
            if (note != null)
              Semantics(
                  liveRegion: true,
                  child: Text(note!, style: TextStyle(color: p.muted))),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 8, children: [
              FilledButton.icon(
                  onPressed: busy ? null : _download,
                  icon: const Icon(Icons.download),
                  label: const Text('Download')),
              if (file.canManage)
                OutlinedButton.icon(
                    onPressed: busy ? null : _rename,
                    icon: const Icon(Icons.edit_outlined),
                    label: const Text('Rename')),
              if (canUpload && canReindex(file.index))
                OutlinedButton.icon(
                    onPressed: busy ? null : _reindex,
                    icon: const Icon(Icons.refresh),
                    label: const Text('Re-index')),
              if (file.canManage)
                OutlinedButton.icon(
                    onPressed: busy ? null : _delete,
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('Delete')),
            ]),
            if (!file.canManage) ...[
              const SizedBox(height: 8),
              Text(filesManageNote,
                  style: TextStyle(color: p.faint, fontSize: 12)),
            ],
          ]),
        ),
        if (versions case final versionsController?) ...[
          const SizedBox(height: 12),
          FileVersionsSection(
              controller: versionsController,
              currentUserId: widget.currentUserId,
              pick: widget.pick),
        ],
        const SizedBox(height: 12),
        AnumSurface(child: _preview(p)),
      ]),
    );
  }

  Widget _preview(AnumPalette p) {
    Widget failed(Object? e) => AnumEmpty(
        icon: Icons.error_outline,
        title: 'Could not load the preview',
        message: '$e',
        action: 'Retry',
        onAction: () => setState(_loadPreview));
    final text = _text, image = _image;
    if (text != null) {
      return FutureBuilder<FilePreviewText>(
        future: text,
        builder: (context, snap) {
          if (snap.hasError) return failed(snap.error);
          final value = snap.data;
          if (value == null) {
            return const Center(
                child: CircularProgressIndicator(
                    semanticsLabel: 'Loading preview'));
          }
          return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text('Preview',
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w700)),
                const SizedBox(height: 8),
                SelectableText(value.text,
                    style: TextStyle(
                        color: p.text, fontFamily: 'monospace', fontSize: 13)),
                if (value.truncated)
                  Text(
                      'Showing the first 256 KB. Download the file for the rest.',
                      style: TextStyle(color: p.faint, fontSize: 12)),
              ]);
        },
      );
    }
    if (image != null) {
      return FutureBuilder<Uint8List>(
        future: image,
        builder: (context, snap) {
          if (snap.hasError) return failed(snap.error);
          final bytes = snap.data;
          if (bytes == null) {
            return const Center(
                child: CircularProgressIndicator(
                    semanticsLabel: 'Loading preview'));
          }
          return Image.memory(bytes,
              semanticLabel: 'Preview of ${file.name}',
              fit: BoxFit.contain,
              errorBuilder: (_, __, ___) =>
                  const Text('This image could not be shown.'));
        },
      );
    }
    return Text(
        '${fileTypeLabel(file.contentType)} files have no preview in the app. '
        'Download the file to open it in another app.',
        style: TextStyle(color: p.muted));
  }
}

/// Owns its text field, so the field outlives the dialog's closing animation.
class _RenameDialog extends StatefulWidget {
  const _RenameDialog({required this.current});
  final String current;
  @override
  State<_RenameDialog> createState() => _RenameDialogState();
}

class _RenameDialogState extends State<_RenameDialog> {
  late final _field = TextEditingController(text: widget.current);

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final problem = renameProblem(widget.current, _field.text);
    return AlertDialog(
      title: const Text('Rename file'),
      content: TextField(
        controller: _field,
        autofocus: true,
        maxLength: 255,
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(
            labelText: 'New name',
            errorText: _field.text == widget.current ? null : problem,
            helperText: 'The extension stays the same.'),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel')),
        FilledButton(
            onPressed: problem == null
                ? () => Navigator.pop(context, _field.text)
                : null,
            child: const Text('Save name')),
      ],
    );
  }
}
