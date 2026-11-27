import 'package:flutter/material.dart';

import '../localization/anum_localizations.dart';
import '../theme/anum_theme.dart';

/// "AI answers are placeholders" (docs/model-gateway.md#placeholder-answers):
/// shown above the screens where AI answers appear while the workspace's model
/// is the built-in placeholder, with one action to set up a real model. Shows
/// nothing when [placeholder] is not true: an unknown status never claims the
/// answers are fake.
class AnumModelPlaceholderBanner extends StatelessWidget {
  const AnumModelPlaceholderBanner(
      {required this.placeholder,
      required this.workspaceSource,
      required this.onSetUp,
      super.key});

  final bool? placeholder;

  /// The workspace saved the placeholder itself (else it is the server's).
  final bool workspaceSource;
  final VoidCallback onSetUp;

  @override
  Widget build(BuildContext context) {
    if (placeholder != true) return const SizedBox.shrink();
    final p = context.palette;
    final l = context.anum;
    return Semantics(
      container: true,
      liveRegion: true,
      child: Container(
        key: const ValueKey('model-placeholder-banner'),
        width: double.infinity,
        // With large text on a small phone the banner scrolls inside a third
        // of the screen instead of pushing the page below it out of view.
        constraints:
            BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.35),
        margin: const EdgeInsets.fromLTRB(
            AnumSpacing.md, AnumSpacing.xs, AnumSpacing.md, 0),
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.sm, AnumSpacing.sm, AnumSpacing.sm, AnumSpacing.xxs),
        decoration: BoxDecoration(
          color: p.surface2,
          borderRadius: BorderRadius.circular(AnumRadius.lg),
          border: Border.all(color: p.warn),
        ),
        child: SingleChildScrollView(
            child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Icon(Icons.warning_amber_rounded, size: 20, color: p.warn),
            ),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                      l.t(workspaceSource
                          ? 'model.placeholder.workspace'
                          : 'model.placeholder.server'),
                      style: TextStyle(
                          color: p.text,
                          fontSize: AnumType.smPlus,
                          fontWeight: FontWeight.w600,
                          height: 1.35)),
                  const SizedBox(height: AnumSpacing.xxs),
                  Text(l.t('model.placeholder.fixPhone'),
                      style: TextStyle(
                          color: p.muted,
                          fontSize: AnumType.smPlus,
                          height: 1.35)),
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton(
                        onPressed: onSetUp,
                        child: Text(l.t('model.placeholder.setUp'))),
                  ),
                ],
              ),
            ),
          ],
        )),
      ),
    );
  }
}
