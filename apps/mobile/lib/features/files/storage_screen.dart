import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import '../workspace/work_status.dart';
import 'file_strings.dart';
import 'file_versions_models.dart';
import 'file_versions_repository.dart';

enum StoragePhase { loading, ready, denied, error }

/// The storage report's state (docs/files.md#storage-screen).
class StorageController extends ChangeNotifier {
  StorageController(this.repository);
  final FileVersionsRepository repository;

  StoragePhase phase = StoragePhase.loading;
  StorageReport? report;
  String? error;

  Future<void> load() async {
    if (phase != StoragePhase.ready) phase = StoragePhase.loading;
    error = null;
    notifyListeners();
    try {
      report = await repository.storage();
      phase = StoragePhase.ready;
    } on ApiException catch (e) {
      phase = e.isPermissionDenied ? StoragePhase.denied : StoragePhase.error;
      error = e.message;
    } catch (e) {
      phase = StoragePhase.error;
      error = '$e';
    }
    notifyListeners();
  }
}

/// Opens the storage screen; [onOpenFile] gets the id of a file picked from
/// "Largest files" (the Files tab opens its detail screen).
Future<void> openStorageScreen(
        BuildContext context, FileVersionsRepository repository,
        {required void Function(String fileId) onOpenFile}) =>
    Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => StorageScreen(
                controller: StorageController(repository),
                onOpenFile: onOpenFile)));

/// Usage against the per-workspace limit, what older versions take, the
/// split by kind and the files that take the most space. The limit is the
/// operator's; the server enforces it on every upload and new version.
class StorageScreen extends StatefulWidget {
  const StorageScreen(
      {required this.controller, required this.onOpenFile, super.key});
  final StorageController controller;
  final void Function(String fileId) onOpenFile;

  @override
  State<StorageScreen> createState() => _StorageScreenState();
}

class _StorageScreenState extends State<StorageScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.load();
  }

  @override
  void didUpdateWidget(StorageScreen old) {
    super.didUpdateWidget(old);
    if (old.controller != widget.controller) widget.controller.load();
  }

  @override
  Widget build(BuildContext context) {
    final s = FileStrings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('stor.title'))),
      body: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) => RefreshIndicator(
          onRefresh: widget.controller.load,
          child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: _children(context, s)),
        ),
      ),
    );
  }

  List<Widget> _children(BuildContext context, FileStrings s) {
    final c = widget.controller;
    final p = context.palette;
    switch (c.phase) {
      case StoragePhase.loading:
        return [
          Center(
              child: CircularProgressIndicator(
                  semanticsLabel: s.t('stor.loading')))
        ];
      case StoragePhase.denied:
        return [
          AnumSurface(
              child: AnumEmpty(
                  icon: Icons.lock_outline,
                  title: s.t('stor.title'),
                  message: s.t('stor.denied')))
        ];
      case StoragePhase.error:
        return [
          AnumSurface(
              child: AnumEmpty(
                  icon: Icons.cloud_off_outlined,
                  title: s.t('stor.title'),
                  message: s.t('stor.failed', {'message': c.error ?? ''}),
                  action: s.t('stor.retry'),
                  onAction: c.load))
        ];
      case StoragePhase.ready:
        break;
    }
    final report = c.report!;
    final usage = report.usage;
    final level = quotaLevel(usage);
    final color = switch (level) {
      QuotaLevel.ok => p.violet,
      QuotaLevel.warn => p.warn,
      _ => p.stop,
    };
    return [
      AnumSurface(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Wrap(spacing: 12, runSpacing: 4, children: [
            Text(
                s.t('stor.used', {
                  'used': formatBytes(usage.usedBytes),
                  'quota': formatBytes(usage.quotaBytes)
                }),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            Text(s.t('stor.left', {'left': formatBytes(bytesLeft(usage))}),
                style: TextStyle(color: p.muted)),
          ]),
          const SizedBox(height: 8),
          Semantics(
            label: s.t('stor.title'),
            value: s.t('stor.used', {
              'used': formatBytes(usage.usedBytes),
              'quota': formatBytes(usage.quotaBytes)
            }),
            child: ExcludeSemantics(
              child: LinearProgressIndicator(
                value: usage.fraction,
                minHeight: 8,
                borderRadius: BorderRadius.circular(8),
                color: color,
              ),
            ),
          ),
          if (usage.versionCount > 0) ...[
            const SizedBox(height: 8),
            Text(
                s.count('stor.versions', usage.versionCount,
                    {'size': formatBytes(usage.versionBytes)}),
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
          ],
          if (level == QuotaLevel.nearFull || level == QuotaLevel.full) ...[
            const SizedBox(height: 8),
            Semantics(
                liveRegion: true,
                child: Text(
                    level == QuotaLevel.full
                        ? s.t('stor.full')
                        : s.t('stor.nearLimit'),
                    style: TextStyle(color: p.stop))),
          ] else if (level == QuotaLevel.warn) ...[
            const SizedBox(height: 8),
            Text(s.t('stor.nearLimit'), style: TextStyle(color: p.warn)),
          ],
          const SizedBox(height: 8),
          Text(s.t('stor.limitNote'),
              style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
        ]),
      ),
      const SizedBox(height: 12),
      if (report.isEmpty)
        AnumSurface(
            child: AnumEmpty(
                icon: Icons.folder_open,
                title: s.t('stor.empty'),
                message: s.t('stor.emptyText')))
      else ...[
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(s.t('stor.byKind'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            for (final row in report.byKind)
              AnumRow(
                title: _kind(s, row.kind),
                subtitle: s.count('stor.files', row.fileCount),
                trailing: Text(formatBytes(row.bytes),
                    style: TextStyle(color: p.muted)),
              ),
          ]),
        ),
        const SizedBox(height: 12),
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(s.t('stor.largest'),
                style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            Text(s.t('stor.largestHelp'),
                style: TextStyle(color: p.faint, fontSize: AnumType.xs)),
            for (final row in report.largest)
              Semantics(
                label: s.t('stor.openFile', {'name': row.file.name}),
                button: true,
                excludeSemantics: true,
                child: AnumRow(
                  icon: Icons.insert_drive_file_outlined,
                  title: row.file.name,
                  subtitle: row.versionCount == 0
                      ? s.t('stor.noVersions')
                      : s.count('stor.versionsCell', row.versionCount,
                          {'size': formatBytes(row.versionBytes)}),
                  detail:
                      s.t('stor.total', {'size': formatBytes(row.totalBytes)}),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => widget.onOpenFile(row.file.id),
                ),
              ),
          ]),
        ),
      ],
    ];
  }

  String _kind(FileStrings s, String kind) {
    final key = 'kind.$kind';
    return FileStrings.en.containsKey(key) ? s.t(key) : s.t('kind.other');
  }
}
