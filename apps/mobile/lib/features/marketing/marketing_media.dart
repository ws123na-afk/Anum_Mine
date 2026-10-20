import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'marketing_models.dart';
import 'marketing_ops_controller.dart';

/// "1.2 MB" for [bytes].
String mkSize(int bytes) {
  if (bytes < 1024) return '$bytes B';
  if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(0)} KB';
  return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
}

/// Picks images and videos from the workspace's Files (`GET /media`) for a
/// post and returns the chosen set. Uploading is not a Marketing route: new
/// files are added in Files, then picked here.
Future<List<MkMedia>?> pickMarketingMedia(
        BuildContext context, MarketingOps ops, List<MkMedia> current) =>
    showModalBottomSheet<List<MkMedia>>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        builder: (_) => MediaPickerSheet(ops: ops, current: current));

class MediaPickerSheet extends StatefulWidget {
  const MediaPickerSheet({required this.ops, required this.current, super.key});
  final MarketingOps ops;
  final List<MkMedia> current;
  @override
  State<MediaPickerSheet> createState() => _MediaPickerSheetState();
}

class _MediaPickerSheetState extends State<MediaPickerSheet> {
  final _search = TextEditingController();
  late final Map<String, MkMedia> _chosen = {
    for (final item in widget.current) item.fileId: item
  };

  MarketingOps get ops => widget.ops;

  @override
  void initState() {
    super.initState();
    _search.text = ops.mediaQuery;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) ops.loadMedia(query: ops.mediaQuery);
    });
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    return Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(context).bottom,
            left: AnumSpacing.md,
            right: AnumSpacing.md,
            top: AnumSpacing.md),
        child: ListenableBuilder(
            listenable: ops,
            builder: (context, _) => Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Semantics(
                          header: true,
                          child: Text(l.t('mkOps.media.title'),
                              style: text.titleLarge)),
                      const SizedBox(height: AnumSpacing.xs),
                      Text(l.t('mkOps.media.hint'), style: text.bodySmall),
                      const SizedBox(height: AnumSpacing.sm),
                      TextField(
                          controller: _search,
                          textInputAction: TextInputAction.search,
                          onSubmitted: (value) => ops.loadMedia(query: value),
                          decoration: InputDecoration(
                              labelText: l.t('mkOps.media.search'),
                              prefixIcon: const Icon(Icons.search))),
                      const SizedBox(height: AnumSpacing.xs),
                      Flexible(child: _body(context)),
                      const SizedBox(height: AnumSpacing.sm),
                      Row(children: [
                        Expanded(
                            child: OutlinedButton(
                                onPressed: () => Navigator.pop(context),
                                child: Text(l.t('mk.dismiss')))),
                        const SizedBox(width: AnumSpacing.xs),
                        Expanded(
                            child: FilledButton(
                                onPressed: () => Navigator.pop(
                                    context, _chosen.values.toList()),
                                child: Text(l.f('mkOps.media.done',
                                    {'count': _chosen.length})))),
                      ]),
                      const SizedBox(height: AnumSpacing.md),
                    ])));
  }

  Widget _body(BuildContext context) {
    final l = context.anum;
    switch (ops.mediaPhase) {
      case MkLoad.idle:
      case MkLoad.loading when ops.mediaFiles.isEmpty:
        return Padding(
            padding: const EdgeInsets.all(AnumSpacing.lg),
            child: Center(
                child: Semantics(
                    label: l.t('mkOps.media.loading'),
                    child: const CircularProgressIndicator())));
      case MkLoad.denied:
        return _note(
            context, l.f('mkOps.denied', {'message': ops.mediaError ?? ''}),
            icon: Icons.lock_outline);
      case MkLoad.error:
        return _note(context,
            l.f('mkOps.media.error', {'message': ops.mediaError ?? ''}),
            icon: Icons.error_outline,
            action: l.t('retry'),
            onAction: () => ops.loadMedia());
      default:
        break;
    }
    if (ops.mediaFiles.isEmpty) {
      return _note(
          context,
          ops.mediaQuery.isEmpty
              ? l.t('mkOps.media.empty')
              : l.t('mkOps.media.noMatch'),
          icon: Icons.photo_library_outlined);
    }
    return ListView(shrinkWrap: true, children: [
      for (final file in ops.mediaFiles)
        CheckboxListTile(
            value: _chosen.containsKey(file.id),
            onChanged: (on) => setState(() => on ?? false
                ? _chosen[file.id] = file.toMedia()
                : _chosen.remove(file.id)),
            secondary: Icon(file.kind == 'video'
                ? Icons.videocam_outlined
                : Icons.image_outlined),
            title: Text(file.name, textDirection: TextDirection.ltr),
            subtitle: Text(
                '${l.t('mkOps.media.${file.kind}')} · ${mkSize(file.sizeBytes)}')),
      if (ops.moreMedia)
        Center(
            child: OutlinedButton(
                onPressed: ops.mediaPhase == MkLoad.loading
                    ? null
                    : () => ops.loadMedia(more: true),
                child: Text(l.t('mk.loadMore')))),
    ]);
  }

  Widget _note(BuildContext context, String message,
      {required IconData icon, String? action, VoidCallback? onAction}) {
    return Padding(
        padding: const EdgeInsets.all(AnumSpacing.md),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(icon),
          const SizedBox(height: AnumSpacing.xs),
          Text(message, textAlign: TextAlign.center),
          if (action != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            OutlinedButton(onPressed: onAction, child: Text(action)),
          ],
        ]));
  }
}
