import '../../data/readable_error.dart';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';

import '../../src/infrastructure/picked_file_size.dart';
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
  final picked = await FilePicker.pickFiles();
  return [
    for (final f in picked)
      if (f.path != null) (f.path!, f.name, await pickedFileSize(f) ?? 0)
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
                if (c.phase == FilesPhase.ready)
                  OutlinedButton.icon(
                    onPressed: c.toggleSelecting,
                    icon: Icon(c.selecting
                        ? Icons.check_box_outlined
                        : Icons.check_box_outline_blank),
                    label: Text(c.selecting
                        ? FileStrings.of(context).t('org.done')
                        : FileStrings.of(context).t('org.select')),
                  ),
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
            Text(usage.summary,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            Text(
                'Up to ${formatBytes(usage.maxUploadBytes)} per file. '
                'Accepted: ${usage.accepted}.',
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
          ],
          if (!c.canUpload) ...[
            const SizedBox(height: 8),
            Text(
                'Uploading needs a member or owner role in this workspace. '
                'You can still open and download files.',
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
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
      if (c.phase == FilesPhase.ready) _FolderStrip(controller: c),
      if (c.phase == FilesPhase.ready && (c.tags.isNotEmpty))
        _TagStrip(controller: c),
      if (c.phase == FilesPhase.ready && c.selected.isNotEmpty)
        _BulkBar(controller: c),
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
          child: c.query.isNotEmpty || c.tagFilter != null
              ? AnumEmpty(
                  icon: Icons.search_off,
                  title: c.query.isNotEmpty
                      ? 'No files match "${c.query}"'
                      : 'No files carry this tag',
                  message: 'Try another part of the name.',
                  action: 'Clear search',
                  onAction: () {
                    _search.clear();
                    c.tagFilter = null;
                    c.search('');
                  })
              : c.currentFolder != null
                  ? AnumEmpty(
                      icon: Icons.folder_open,
                      title: FileStrings.of(context).t('org.emptyFolder'),
                      message: c.canUpload
                          ? FileStrings.of(context).t('org.emptyFolderText')
                          : FileStrings.of(context)
                              .t('org.emptyFolderReadOnly'),
                      action: c.canUpload
                          ? FileStrings.of(context).t(
                              'org.uploadHere', {'name': c.currentFolder!.name})
                          : null,
                      onAction: c.canUpload ? _upload : null)
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
              c.selecting
                  ? CheckboxListTile(
                      value: c.selected.contains(f.id),
                      onChanged: (_) => c.toggleSelected(f),
                      controlAffinity: ListTileControlAffinity.leading,
                      title: Text(f.name,
                          semanticsLabel: FileStrings.of(context)
                              .t('org.selectRow', {'name': f.name})),
                      subtitle: Text(
                          '${fileTypeLabel(f.contentType)} · ${formatBytes(f.sizeBytes)}'
                          '${c.searching && f.folderPath != null ? ' · ${FileStrings.of(context).t('org.inFolder', {
                                  'path': f.folderPath!
                                })}' : ''}'),
                    )
                  : AnumRow(
                      icon: _icon(f),
                      title: f.name,
                      subtitle:
                          '${fileTypeLabel(f.contentType)} · ${formatBytes(f.sizeBytes)} · '
                          '${uploaderLabel(f.createdBy, widget.currentUserId)}'
                          '${c.searching && f.folderPath != null ? ' · ${FileStrings.of(context).t('org.inFolder', {
                                  'path': f.folderPath!
                                })}' : ''}',
                      detail:
                          '${exactTime(f.createdAt)} · ${indexDescription(f.index).$1}'
                          '${f.tags.isEmpty ? '' : ' · ${f.tags.map((t) => t.name).join(', ')}'}',
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
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
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
          usageOf: () => widget.controller.usage,
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
          error = 'Could not download: ${readableError(e)}';
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
            Text(name, style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
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
            meta(FileStrings.of(context).t('org.folderMeta'),
                file.folderPath ?? FileStrings.of(context).t('org.root')),
            _FileTags(
                controller: widget.controller,
                file: file,
                canUpload: canUpload,
                onChanged: (updated) => setState(() {
                      file = updated;
                      error = null;
                    }),
                onError: (message) => setState(() => error = message)),
            const SizedBox(height: 8),
            Text('Retrieval',
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            const SizedBox(height: 4),
            Wrap(spacing: 8, runSpacing: 4, children: [
              AnumPill(label: label, tone: _tone(tone)),
              Text(detail,
                  style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
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
                  style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
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
        message: readableError(e),
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
                        color: p.text,
                        fontFamily: 'monospace',
                        fontSize: AnumType.sm)),
                if (value.truncated)
                  Text(
                      'Showing the first 256 KB. Download the file for the rest.',
                      style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
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

/// The folder trail (All documents, then the open folder's ancestors), the
/// open folder's subfolders as chips, and New folder (docs/files.md#folders).
class _FolderStrip extends StatelessWidget {
  const _FolderStrip({required this.controller});
  final FilesController controller;

  Future<void> _newFolder(BuildContext context) async {
    final strings = FileStrings.of(context);
    final name = await showDialog<String>(
        context: context,
        builder: (_) => _FolderNameDialog(
            strings: strings, siblings: controller.subfolders));
    if (name == null) return;
    final problem = await controller.createFolder(name);
    if (problem != null && context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(problem)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final s = FileStrings.of(context);
    final p = context.palette;
    final trail = c.trail;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Semantics(
          container: true,
          label: s.t('org.trail'),
          child: Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 4,
            runSpacing: 4,
            children: [
              if (c.folder == null)
                Text(s.t('org.root'),
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w600))
              else
                TextButton(
                    onPressed: () => c.openFolder(null),
                    child: Text(s.t('org.root'))),
              for (final (i, f) in trail.indexed) ...[
                Icon(Icons.chevron_right, size: 16, color: p.faint),
                if (i == trail.length - 1)
                  Text(f.name,
                      style:
                          TextStyle(color: p.text, fontWeight: FontWeight.w600))
                else
                  TextButton(
                      onPressed: () => c.openFolder(f.id), child: Text(f.name)),
              ],
              if (c.canUpload)
                TextButton.icon(
                    onPressed: () => _newFolder(context),
                    icon: const Icon(Icons.create_new_folder_outlined),
                    label: Text(s.t('org.newFolder'))),
              if (trail.isNotEmpty && trail.last.canManage && c.canUpload)
                _FolderMenu(controller: c, folder: trail.last),
            ],
          ),
        ),
        if (trail.isNotEmpty && !trail.last.canManage && c.canUpload)
          Text(s.t('org.folder.manageNote'),
              key: const Key('folder-manage-note'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        if (c.searching)
          Text(s.t('org.searchEverywhere'),
              style: TextStyle(color: p.muted, fontSize: AnumType.sm))
        else if (c.subfolders.isNotEmpty) ...[
          const SizedBox(height: 8),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final f in c.subfolders)
              ActionChip(
                avatar: Icon(Icons.folder_outlined, size: 18, color: p.violet),
                label:
                    Text('${f.name} · ${s.count('org.filesIn', f.fileCount)}'),
                tooltip: s.t('org.openFolder', {'name': f.name}),
                onPressed: () => c.openFolder(f.id),
              ),
          ]),
        ],
      ]),
    );
  }
}

/// The workspace's tags as filter chips (docs/files.md#tags).
class _TagStrip extends StatelessWidget {
  const _TagStrip({required this.controller});
  final FilesController controller;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final s = FileStrings.of(context);
    return Semantics(
      container: true,
      label: s.t('org.tags'),
      child: Wrap(spacing: 8, runSpacing: 8, children: [
        for (final tag in c.tags)
          FilterChip(
            label: Text('${tag.name} · ${tag.fileCount}'),
            tooltip: s.t('org.filterBy', {'name': tag.name}),
            selected: c.tagFilter == tag.id,
            onSelected: (on) => c.setTagFilter(on ? tag.id : null),
          ),
        if (c.tagFilter != null)
          ActionChip(
              label: Text(s.t('org.clearTag')),
              onPressed: () => c.setTagFilter(null)),
        ActionChip(
            key: const Key('tags-manage'),
            avatar: const Icon(Icons.sell_outlined, size: 18),
            label: Text(s.t('org.tags.manage')),
            onPressed: () => showModalBottomSheet<void>(
                context: context,
                isScrollControlled: true,
                showDragHandle: true,
                builder: (_) => _ManageTagsSheet(controller: c))),
      ]),
    );
  }
}

/// Over a selection: how many, Move, Delete and Clear (docs/files.md#bulk-actions).
class _BulkBar extends StatelessWidget {
  const _BulkBar({required this.controller});
  final FilesController controller;

  Future<void> _report(BuildContext context, Future<String?> work) async {
    final messenger = ScaffoldMessenger.of(context);
    final problem = await work;
    if (problem != null) {
      messenger.showSnackBar(SnackBar(content: Text(problem)));
    }
  }

  Future<void> _move(BuildContext context) async {
    final s = FileStrings.of(context);
    final target = await showModalBottomSheet<(String?,)>(
      context: context,
      showDragHandle: true,
      builder: (context) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          ListTile(
              title: Text(s.t('org.moveTitle'),
                  style: const TextStyle(fontWeight: FontWeight.w700))),
          ListTile(
              leading: const Icon(Icons.home_outlined),
              title: Text(s.t('org.root')),
              onTap: () => Navigator.pop(context, (null,))),
          for (final f in folderChoices(controller.folders))
            ListTile(
                leading: Padding(
                    padding:
                        EdgeInsetsDirectional.only(start: 16.0 * (f.depth - 1)),
                    child: const Icon(Icons.folder_outlined)),
                title: Text(f.name),
                onTap: () => Navigator.pop(context, (f.id,))),
        ]),
      ),
    );
    if (target == null || !context.mounted) return;
    await _report(context, controller.moveSelected(target.$1));
  }

  Future<void> _delete(BuildContext context) async {
    final s = FileStrings.of(context);
    final n = controller.selected.length;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('org.deleteTitle', {'n': n})),
        content: Text(s.t('org.deleteText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(s.t('org.keep'))),
          FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(s.t('org.delete'))),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _report(context, controller.deleteSelected());
  }

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final s = FileStrings.of(context);
    final p = context.palette;
    final allowed = c.selectionManageable;
    return AnumSurface(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Semantics(
                liveRegion: true,
                child: Text(s.count('org.selected', c.selected.length),
                    style:
                        TextStyle(color: p.text, fontWeight: FontWeight.w700))),
            TextButton(
                onPressed: c.selectAll, child: Text(s.t('org.selectAll'))),
            Semantics(
              button: true,
              label: s.t('org.downloadLabel'),
              excludeSemantics: true,
              child: OutlinedButton.icon(
                  key: const Key('files-download-zip'),
                  onPressed: c.selected.isEmpty || c.downloading
                      ? null
                      : () => _report(context, c.downloadSelected(s)),
                  icon: const Icon(Icons.download_outlined),
                  label: Text(
                      s.t(c.downloading ? 'org.downloading' : 'org.download'))),
            ),
            OutlinedButton.icon(
                onPressed: allowed ? () => _move(context) : null,
                icon: const Icon(Icons.drive_file_move_outline),
                label: Text(s.t('org.move'))),
            OutlinedButton.icon(
                onPressed: allowed ? () => _delete(context) : null,
                icon: const Icon(Icons.delete_outline),
                label: Text(s.t('org.delete'))),
            TextButton(
                onPressed: c.clearSelection, child: Text(s.t('org.clear'))),
          ],
        ),
        if (!allowed) ...[
          const SizedBox(height: 4),
          Text(c.canUpload ? s.t('org.notYours') : s.t('org.denied'),
              style: TextStyle(color: p.muted, fontSize: AnumType.xs)),
        ],
      ]),
    );
  }
}

/// The tags on one file: chips with remove, and Add tag (pick or create).
class _FileTags extends StatelessWidget {
  const _FileTags(
      {required this.controller,
      required this.file,
      required this.canUpload,
      required this.onChanged,
      required this.onError});
  final FilesController controller;
  final WorkspaceFile file;
  final bool canUpload;
  final void Function(WorkspaceFile file) onChanged;
  final void Function(String message) onError;

  WorkspaceFile _current() =>
      controller.items.firstWhere((f) => f.id == file.id, orElse: () => file);

  Future<void> _add(BuildContext context) async {
    final s = FileStrings.of(context);
    final free = controller.tags
        .where((t) => !file.tags.any((x) => x.id == t.id))
        .toList();
    final choice = await showModalBottomSheet<(String?, String?)>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => _AddTagSheet(strings: s, free: free),
    );
    if (choice == null) return;
    final problem =
        await controller.addTag(file, tagId: choice.$1, newName: choice.$2);
    if (problem != null) {
      onError(problem);
    } else {
      onChanged(_current());
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = FileStrings.of(context);
    final p = context.palette;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(s.t('org.tags'),
          style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
      const SizedBox(height: 4),
      Wrap(spacing: 8, runSpacing: 4, children: [
        if (file.tags.isEmpty)
          Text(s.t('org.noTags'), style: TextStyle(color: p.muted)),
        for (final tag in file.tags)
          InputChip(
            label: Text(tag.name),
            onDeleted: canUpload
                ? () async {
                    final problem = await controller.removeTag(file, tag);
                    if (problem != null) {
                      onError(problem);
                    } else {
                      onChanged(_current());
                    }
                  }
                : null,
            deleteButtonTooltipMessage: s.t('org.removeTag', {'tag': tag.name}),
          ),
        if (canUpload)
          ActionChip(
              avatar: const Icon(Icons.add, size: 18),
              label: Text(s.t('org.addTag')),
              onPressed: () => _add(context)),
      ]),
    ]);
  }
}

class _AddTagSheet extends StatefulWidget {
  const _AddTagSheet({required this.strings, required this.free});
  final FileStrings strings;
  final List<FileTag> free;
  @override
  State<_AddTagSheet> createState() => _AddTagSheetState();
}

class _AddTagSheetState extends State<_AddTagSheet> {
  final _field = TextEditingController();
  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(context).bottom,
            left: 16,
            right: 16),
        child: ListView(shrinkWrap: true, children: [
          Text(s.t('org.pickTag'),
              style: const TextStyle(fontWeight: FontWeight.w700)),
          for (final tag in widget.free)
            ListTile(
                leading: const Icon(Icons.label_outline),
                title: Text(tag.name),
                onTap: () => Navigator.pop(context, (tag.id, null))),
          const SizedBox(height: 8),
          TextField(
            controller: _field,
            maxLength: 64,
            decoration: InputDecoration(labelText: s.t('org.newTag')),
            onChanged: (_) => setState(() {}),
            onSubmitted: (value) => value.trim().isEmpty
                ? null
                : Navigator.pop(context, (null, value.trim())),
          ),
          FilledButton(
              onPressed: _field.text.trim().isEmpty
                  ? null
                  : () => Navigator.pop(context, (null, _field.text.trim())),
              child: Text(s.t('org.addTag'))),
          const SizedBox(height: 16),
        ]),
      ),
    );
  }
}

/// Owns its text field, so the field outlives the dialog's closing animation.
class _FolderNameDialog extends StatefulWidget {
  const _FolderNameDialog({required this.strings, required this.siblings});
  final FileStrings strings;
  final List<FileFolder> siblings;
  @override
  State<_FolderNameDialog> createState() => _FolderNameDialogState();
}

class _FolderNameDialogState extends State<_FolderNameDialog> {
  final _field = TextEditingController();
  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final s = widget.strings;
    final problem = folderNameProblem(_field.text, widget.siblings);
    return AlertDialog(
      title: Text(s.t('org.newFolder')),
      content: TextField(
        controller: _field,
        autofocus: true,
        maxLength: 255,
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(
            labelText: s.t('org.folderName'),
            errorText: _field.text.isEmpty ? null : problem,
            helperText: s.t('org.folderHint'),
            helperMaxLines: 3),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel')),
        FilledButton(
            onPressed: problem == null
                ? () => Navigator.pop(context, _field.text.trim())
                : null,
            child: Text(s.t('org.createFolder'))),
      ],
    );
  }
}

/// Rename, move and delete the open folder (docs/files.md#folders): the
/// person who created it or an owner.
class _FolderMenu extends StatelessWidget {
  const _FolderMenu({required this.controller, required this.folder});
  final FilesController controller;
  final FileFolder folder;

  Future<void> _report(BuildContext context, Future<String?> work) async {
    final messenger = ScaffoldMessenger.of(context);
    final problem = await work;
    if (problem != null) {
      messenger.showSnackBar(SnackBar(content: Text(problem)));
    }
  }

  Future<void> _rename(BuildContext context) async {
    final s = FileStrings.of(context);
    final name = await showDialog<String>(
        context: context,
        builder: (_) => _NameDialog(
            title: s.t('org.folder.rename'),
            label: s.t('org.folderName'),
            initial: folder.name,
            maxLength: 255,
            save: s.t('org.save'),
            cancel: s.t('org.cancel')));
    if (name == null || !context.mounted) return;
    await _report(context, controller.renameFolder(folder, name, s));
  }

  Future<void> _move(BuildContext context) async {
    final s = FileStrings.of(context);
    final blocked = controller.folderBranch(folder);
    final target = await showModalBottomSheet<(String?,)>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => SafeArea(
        child: ListView(shrinkWrap: true, children: [
          ListTile(
              title: Text(s.t('org.folder.moveTitle', {'name': folder.name}),
                  style: const TextStyle(fontWeight: FontWeight.w700))),
          if (folder.parentId != null)
            ListTile(
                key: const Key('folder-move-root'),
                leading: const Icon(Icons.home_outlined),
                title: Text(s.t('org.root')),
                onTap: () => Navigator.pop(context, (null,))),
          for (final f in folderChoices(controller.folders))
            if (!blocked.contains(f.id) && f.id != folder.parentId)
              ListTile(
                  key: Key('folder-move-${f.id}'),
                  leading: Padding(
                      padding: EdgeInsetsDirectional.only(
                          start: 16.0 * (f.depth - 1)),
                      child: const Icon(Icons.folder_outlined)),
                  title: Text(f.name),
                  onTap: () => Navigator.pop(context, (f.id,))),
        ]),
      ),
    );
    if (target == null || !context.mounted) return;
    await _report(context, controller.moveFolder(folder, target.$1, s));
  }

  Future<void> _delete(BuildContext context) async {
    final s = FileStrings.of(context);
    final inside = controller.folderContents(folder);
    final parent = folder.parentId == null
        ? s.t('org.root')
        : controller.folders
                .where((f) => f.id == folder.parentId)
                .firstOrNull
                ?.name ??
            s.t('org.root');
    final contents = await showDialog<String>(
        context: context,
        builder: (_) => _DeleteFolderDialog(
            name: folder.name,
            files: inside.files,
            folders: inside.folders,
            parent: parent));
    if (contents == null || !context.mounted) return;
    await _report(context, controller.deleteFolder(folder, contents, s));
  }

  @override
  Widget build(BuildContext context) {
    final s = FileStrings.of(context);
    return PopupMenuButton<String>(
      key: const Key('folder-menu'),
      tooltip: s.t('org.folder.actions', {'name': folder.name}),
      icon: const Icon(Icons.more_vert),
      onSelected: (value) => switch (value) {
        'rename' => _rename(context),
        'move' => _move(context),
        _ => _delete(context),
      },
      itemBuilder: (_) => [
        PopupMenuItem(
            key: const Key('folder-rename'),
            value: 'rename',
            child: Text(s.t('org.folder.rename'))),
        PopupMenuItem(
            key: const Key('folder-move'),
            value: 'move',
            child: Text(s.t('org.folder.move'))),
        PopupMenuItem(
            key: const Key('folder-delete'),
            value: 'delete',
            child: Text(s.t('org.folder.delete'))),
      ],
    );
  }
}

/// Deleting a folder is a choice when it holds anything: move its contents
/// up, or delete them too. Answers `refuse`, `move_up` or `delete`.
class _DeleteFolderDialog extends StatefulWidget {
  const _DeleteFolderDialog(
      {required this.name,
      required this.files,
      required this.folders,
      required this.parent});
  final String name, parent;
  final int files, folders;
  @override
  State<_DeleteFolderDialog> createState() => _DeleteFolderDialogState();
}

class _DeleteFolderDialogState extends State<_DeleteFolderDialog> {
  String _choice = 'move_up';

  @override
  Widget build(BuildContext context) {
    final s = FileStrings.of(context);
    final p = context.palette;
    final empty = widget.files == 0 && widget.folders == 0;
    return AlertDialog(
      title: Text(s.t('org.folder.deleteTitle', {'name': widget.name})),
      content: SingleChildScrollView(
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (empty)
                Text(s.t('org.folder.deleteEmpty'))
              else ...[
                Text(s.t('org.folder.deleteFull', {
                  'files': s.count('org.filesIn', widget.files),
                  'folders': s.count('org.folder.subfolders', widget.folders),
                })),
                RadioGroup<String>(
                  groupValue: _choice,
                  onChanged: (v) => setState(() => _choice = v ?? _choice),
                  child: Column(children: [
                    RadioListTile<String>(
                        key: const Key('folder-delete-move-up'),
                        contentPadding: EdgeInsets.zero,
                        value: 'move_up',
                        title: Text(s.t('org.folder.keepContents',
                            {'parent': widget.parent}))),
                    RadioListTile<String>(
                        key: const Key('folder-delete-all'),
                        contentPadding: EdgeInsets.zero,
                        value: 'delete',
                        title: Text(s.t('org.folder.deleteContents')),
                        subtitle: Text(s.t('org.folder.deleteContentsHint'),
                            style: TextStyle(color: p.warn))),
                  ]),
                ),
              ],
            ]),
      ),
      actions: [
        TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(s.t('org.cancel'))),
        FilledButton(
            key: const Key('folder-delete-confirm'),
            onPressed: () => Navigator.pop(context, empty ? 'refuse' : _choice),
            child: Text(s.t('org.folder.delete'))),
      ],
    );
  }
}

/// A name in a dialog: the current name selected, Save and Cancel.
class _NameDialog extends StatefulWidget {
  const _NameDialog(
      {required this.title,
      required this.label,
      required this.initial,
      required this.maxLength,
      required this.save,
      required this.cancel});
  final String title, label, initial, save, cancel;
  final int maxLength;
  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final _field = TextEditingController(text: widget.initial)
    ..selection =
        TextSelection(baseOffset: 0, extentOffset: widget.initial.length);

  @override
  void dispose() {
    _field.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(widget.title),
        content: TextField(
          key: const Key('name-dialog-field'),
          controller: _field,
          autofocus: true,
          maxLength: widget.maxLength,
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(labelText: widget.label),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context),
              child: Text(widget.cancel)),
          FilledButton(
              key: const Key('name-dialog-save'),
              onPressed: _field.text.trim().isEmpty ||
                      _field.text.trim() == widget.initial
                  ? null
                  : () => Navigator.pop(context, _field.text.trim()),
              child: Text(widget.save)),
        ],
      );
}

/// Every tag of the workspace with its file count; rename and delete for the
/// person who created it or an owner (docs/files.md#tags).
class _ManageTagsSheet extends StatelessWidget {
  const _ManageTagsSheet({required this.controller});
  final FilesController controller;

  Future<void> _report(BuildContext context, Future<String?> work) async {
    final messenger = ScaffoldMessenger.of(context);
    final problem = await work;
    if (problem != null) {
      messenger.showSnackBar(SnackBar(content: Text(problem)));
    }
  }

  Future<void> _rename(BuildContext context, FileTag tag) async {
    final s = FileStrings.of(context);
    final name = await showDialog<String>(
        context: context,
        builder: (_) => _NameDialog(
            title: s.t('org.tag.renameOf', {'name': tag.name}),
            label: s.t('org.tagName'),
            initial: tag.name,
            maxLength: 64,
            save: s.t('org.save'),
            cancel: s.t('org.cancel')));
    if (name == null || !context.mounted) return;
    await _report(context, controller.renameTag(tag, name, s));
  }

  Future<void> _delete(BuildContext context, FileTag tag) async {
    final s = FileStrings.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(s.t('org.tag.deleteOf', {'name': tag.name})),
        content: Text(s.t('org.tag.confirmDelete', {
          'name': tag.name,
          'count': s.count('org.filesIn', tag.fileCount)
        })),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(s.t('org.keep'))),
          FilledButton(
              key: const Key('tag-delete-confirm'),
              onPressed: () => Navigator.pop(context, true),
              child: Text(s.t('org.tag.delete'))),
        ],
      ),
    );
    if (ok != true || !context.mounted) return;
    await _report(context, controller.deleteTag(tag, s));
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final s = FileStrings.of(context);
        final p = context.palette;
        final c = controller;
        return SafeArea(
          child: ConstrainedBox(
            constraints: BoxConstraints(
                maxHeight: MediaQuery.sizeOf(context).height * 0.85),
            child: ListView(
                shrinkWrap: true,
                padding: const EdgeInsets.fromLTRB(
                    AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
                children: [
                  Text(s.t('org.tags.manage'),
                      style: TextStyle(
                          color: p.text,
                          fontSize: AnumType.lgPlus,
                          fontWeight: FontWeight.w700)),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(s.t('org.tags.manageHelp'),
                      style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
                  const SizedBox(height: AnumSpacing.sm),
                  if (c.tags.isEmpty)
                    AnumEmpty(
                        icon: Icons.sell_outlined,
                        title: s.t('org.tags.none'),
                        message: s.t('org.tags.noneText'))
                  else
                    for (final tag in c.tags)
                      Padding(
                        key: Key('tag-row-${tag.id}'),
                        padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
                        child: Wrap(
                            spacing: AnumSpacing.xs,
                            runSpacing: AnumSpacing.xxs,
                            crossAxisAlignment: WrapCrossAlignment.center,
                            children: [
                              Text(
                                  '${tag.name} · ${s.count('org.filesIn', tag.fileCount)}',
                                  style: TextStyle(
                                      color: p.text,
                                      fontWeight: FontWeight.w600)),
                              if (tag.canManage) ...[
                                TextButton(
                                    key: Key('tag-rename-${tag.id}'),
                                    onPressed: () => _rename(context, tag),
                                    child: Semantics(
                                        label: s.t('org.tag.renameOf',
                                            {'name': tag.name}),
                                        excludeSemantics: true,
                                        child: Text(s.t('org.tag.rename')))),
                                TextButton(
                                    key: Key('tag-delete-${tag.id}'),
                                    onPressed: () => _delete(context, tag),
                                    child: Semantics(
                                        label: s.t('org.tag.deleteOf',
                                            {'name': tag.name}),
                                        excludeSemantics: true,
                                        child: Text(s.t('org.tag.delete')))),
                              ] else
                                Text(s.t('org.tag.manageDenied'),
                                    style: TextStyle(
                                        color: p.faint, fontSize: AnumType.xs)),
                            ]),
                      ),
                ]),
          ),
        );
      });
}
