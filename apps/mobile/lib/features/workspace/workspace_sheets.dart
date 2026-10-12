import 'package:flutter/material.dart';
import 'workspace_models.dart';
import 'work_status.dart';
import '../../src/widgets/depth.dart';

class FileDetailSheet extends StatelessWidget {
  const FileDetailSheet(
      {super.key,
      required this.file,
      required this.onDownload,
      required this.onDelete});
  final WorkspaceFile file;
  final VoidCallback onDownload, onDelete;
  @override
  Widget build(BuildContext context) => SafeArea(
      child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
          child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(file.name, style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                Text('${file.contentType} · ${_bytes(file.sizeBytes)}'),
                Text('Created ${_when(file.createdAt)}'),
                const SizedBox(height: 18),
                FilledButton.icon(
                    onPressed: () {
                      Navigator.pop(context);
                      onDownload();
                    },
                    icon: const Icon(Icons.download),
                    label: const Text('Download')),
                OutlinedButton.icon(
                    onPressed: () {
                      Navigator.pop(context);
                      onDelete();
                    },
                    icon: const Icon(Icons.delete_outline),
                    label: const Text('Delete'))
              ])));
}

String _when(DateTime value) => relativeTime(value);
String _bytes(int value) => formatBytes(value);
