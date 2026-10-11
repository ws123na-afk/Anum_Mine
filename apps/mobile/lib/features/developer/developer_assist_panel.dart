import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'developer_assist.dart';
import 'developer_models.dart';
import 'developer_screen.dart';

/// The developer card near the chat composer and in the voice view: the
/// "Start?" confirmation, progress sentences, and the merge approval with
/// its payload hash. Confirm merge is always a tap.
class DeveloperAssistPanel extends StatelessWidget {
  const DeveloperAssistPanel({
    required this.assist,
    this.onOpenDeveloper,
    this.openLink = openInBrowser,
    super.key,
  });

  final DeveloperAssist assist;
  final VoidCallback? onOpenDeveloper;
  final LinkOpener openLink;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final pending = assist.pending;
    final review = assist.review;
    final ready = assist.ready;
    return AnumSurface(
      key: const Key('developer-assist'),
      accent: p.sky,
      padding: const EdgeInsetsDirectional.fromSTEB(
          AnumSpacing.md, AnumSpacing.sm, AnumSpacing.xs, AnumSpacing.sm),
      child: Semantics(
        container: true,
        liveRegion: true,
        label: 'Developer',
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Icon(Icons.construction, size: 18, color: p.sky),
            const SizedBox(width: AnumSpacing.xs),
            Expanded(
              child: Text('Developer',
                  style: TextStyle(color: p.text, fontWeight: FontWeight.w700)),
            ),
            if (pending == null && review == null)
              IconButton(
                  tooltip: 'Dismiss',
                  onPressed: assist.busy ? null : assist.dismiss,
                  icon: const Icon(Icons.close, size: 20)),
          ]),
          const SizedBox(height: AnumSpacing.xxs),
          if (pending != null && assist.choosing) ...[
            Text(whichProjectPrompt(assist.choices),
                key: const Key('developer-which-project'),
                style: TextStyle(color: p.text)),
            const SizedBox(height: AnumSpacing.xs),
            Semantics(
              container: true,
              label: 'Choose the project',
              child: Wrap(
                  spacing: AnumSpacing.xs,
                  runSpacing: AnumSpacing.xs,
                  children: [
                    for (final choice in assist.choices)
                      OutlinedButton.icon(
                        key: Key('developer-choose-${choice.id}'),
                        onPressed:
                            assist.busy ? null : () => assist.choose(choice),
                        icon: const Icon(Icons.folder_outlined),
                        label: Text(choice.name),
                      ),
                    OutlinedButton.icon(
                      key: const Key('developer-decline'),
                      onPressed: assist.busy ? null : assist.decline,
                      icon: const Icon(Icons.close),
                      label: const Text('Cancel'),
                    ),
                  ]),
            ),
          ] else if (pending != null) ...[
            Text(confirmPrompt(pending, assist.pendingProject!.name),
                key: const Key('developer-confirm-prompt'),
                style: TextStyle(color: p.text)),
            Directionality(
              textDirection: TextDirection.ltr,
              child: Text(assist.pendingProject!.repository,
                  style: TextStyle(
                      color: p.muted, fontSize: 12, fontFamily: 'monospace')),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton.icon(
                    key: const Key('developer-start'),
                    onPressed: assist.busy
                        ? null
                        : () => unawaited(assist.start(pending)),
                    icon: const Icon(Icons.check),
                    label: const Text('Start'),
                  ),
                  OutlinedButton.icon(
                    key: const Key('developer-decline'),
                    onPressed: assist.busy ? null : assist.decline,
                    icon: const Icon(Icons.close),
                    label: const Text('Cancel'),
                  ),
                ]),
          ] else if (assist.message != null)
            Text(assist.message!,
                key: const Key('developer-assist-message'),
                style: TextStyle(color: p.text)),
          if (review != null) ...[
            const SizedBox(height: AnumSpacing.xs),
            MergeReviewCard(
              approval: review.approval,
              request: review.request,
              busy: assist.busy,
              compact: true,
              onConfirm: () => unawaited(assist.confirmMerge()),
              onDismiss: assist.dismissReview,
            ),
          ],
          if (pending == null &&
              review == null &&
              ready != null &&
              ready.status == DevStatus.waitingApproval) ...[
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  FilledButton.icon(
                    key: const Key('developer-assist-approve'),
                    onPressed: assist.busy
                        ? null
                        : () => unawaited(assist.openReview(ready)),
                    icon: const Icon(Icons.verified_user_outlined),
                    label: const Text('Approve & merge'),
                  ),
                  if (ready.prUrl != null)
                    TextButton.icon(
                      onPressed: () => unawaited(
                          openDeveloperLink(context, openLink, ready.prUrl)),
                      icon: const Icon(Icons.open_in_new, size: 18),
                      label: const Text('Open pull request'),
                    ),
                ]),
          ],
          if (pending == null && onOpenDeveloper != null)
            Align(
              alignment: AlignmentDirectional.centerStart,
              child: TextButton(
                  onPressed: onOpenDeveloper,
                  child: const Text('Open Developer')),
            ),
        ]),
      ),
    );
  }
}
