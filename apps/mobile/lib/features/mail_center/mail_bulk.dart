import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_screens.dart';

/// Several messages at once on the phone (docs/email.md#the-email-center-on-the-phone):
/// read or unread, star, the moves the folder allows, and, for the mailbox's
/// owner, archive (up to 200), export as .mbox (up to 100) and delete for good
/// (Trash and Junk), each behind an approval of exactly the selection.
class MailSelectionBar extends StatelessWidget {
  const MailSelectionBar({super.key, required this.controller});
  final MailCenterController controller;

  Future<void> _confirmDelete(BuildContext context) async {
    final l = AnumLocalizations.maybeOf(context);
    final count = controller.selected.length;
    final yes = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(l.f('mc.b.deleteTitle', {'count': count})),
        content: Text(l.t('mc.deleteForGoodText')),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(l.t('mc.cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(l.t('mc.askToDelete'))),
        ],
      ),
    );
    if (yes == true) await controller.askBulk('delete');
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    final theme = Theme.of(context);
    final count = c.selected.length;
    final role = c.folder?.role;
    final idle = c.busy == null && c.bulkApproval == null;
    final some = count > 0 && idle;
    final routine = [
      for (final m in c.messages)
        if (const {'newsletter', 'notification'}
                .contains(c.labels[m.uid]?.category) &&
            !c.selected.contains(m.uid))
          m.uid
    ];
    Widget action(String label, IconData icon, VoidCallback? onPressed) =>
        OutlinedButton.icon(
            onPressed: onPressed, icon: Icon(icon), label: Text(label));
    final approval = c.bulkApproval;
    final ready = c.exportReady;
    return Card(
      margin: const EdgeInsets.only(bottom: AnumSpacing.sm),
      child: Padding(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Semantics(
              liveRegion: true,
              child: Text(
                count == 0
                    ? l.t('mc.b.none')
                    : l.f('mc.b.count', {'count': count}),
                style: theme.textTheme.titleSmall,
              ),
            ),
            const SizedBox(height: AnumSpacing.xs),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                TextButton(
                  onPressed: idle ? c.selectAll : null,
                  child:
                      Text(l.f('mc.b.selectAll', {'count': c.messages.length})),
                ),
                if (count > 0)
                  TextButton(
                      onPressed: idle ? c.clearSelection : null,
                      child: Text(l.t('mc.b.clear'))),
                if (routine.isNotEmpty)
                  TextButton(
                    onPressed: idle
                        ? () {
                            for (final m in c.messages) {
                              if (routine.contains(m.uid)) c.toggleSelected(m);
                            }
                          }
                        : null,
                    child: Text(l.f('mc.b.routine', {'count': routine.length})),
                  ),
              ],
            ),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                action(l.t('mc.b.read'), Icons.drafts_outlined,
                    some ? () => c.flagSelected('seen', true) : null),
                action(l.t('mc.b.unread'), Icons.mark_email_unread_outlined,
                    some ? () => c.flagSelected('seen', false) : null),
                action(l.t('mc.star'), Icons.star_border,
                    some ? () => c.flagSelected('flagged', true) : null),
                action(l.t('mc.unstar'), Icons.star_outline,
                    some ? () => c.flagSelected('flagged', false) : null),
                if (role != 'archive' && c.folderWithRole('archive') != null)
                  action(l.t('mc.archive'), Icons.archive_outlined,
                      some ? () => c.moveSelected('archive') : null),
                if (role != 'junk' && c.folderWithRole('junk') != null)
                  action(l.t('mc.junk'), Icons.report_gmailerrorred_outlined,
                      some ? () => c.moveSelected('junk') : null),
                if (role != 'inbox' && c.folderWithRole('inbox') != null)
                  action(
                      role == 'trash'
                          ? l.t('mc.b.restore')
                          : role == 'junk'
                              ? l.t('mc.b.notJunk')
                              : l.t('mc.toInbox'),
                      Icons.inbox_outlined,
                      some ? () => c.moveSelected('inbox') : null),
                if (role != 'trash' && c.folderWithRole('trash') != null)
                  action(l.t('mc.trash'), Icons.delete_outline,
                      some ? () => c.moveSelected('trash') : null),
              ],
            ),
            if (c.ownsAccount) ...[
              const SizedBox(height: AnumSpacing.xs),
              Text(l.t('mc.b.withApproval'), style: theme.textTheme.bodySmall),
              const SizedBox(height: AnumSpacing.xs),
              Wrap(
                spacing: AnumSpacing.xs,
                runSpacing: AnumSpacing.xs,
                children: [
                  if (role != 'archive')
                    action(
                        l.f('mc.b.archiveN', {'count': count}),
                        Icons.inventory_2_outlined,
                        some && count <= MailCenterController.bulkLimit
                            ? () => c.askBulk('archive')
                            : null),
                  action(
                      l.f('mc.b.exportN', {'count': count}),
                      Icons.file_download_outlined,
                      some && count <= MailCenterController.exportLimit
                          ? () => c.askBulk('export')
                          : null),
                  if (role == 'trash' || role == 'junk')
                    action(
                        l.f('mc.b.deleteN', {'count': count}),
                        Icons.delete_forever_outlined,
                        some && count <= MailCenterController.bulkLimit
                            ? () => _confirmDelete(context)
                            : null),
                ],
              ),
              if (count > MailCenterController.exportLimit)
                Text(
                    l.f('mc.b.tooMany', {
                      'export': MailCenterController.exportLimit,
                      'archive': MailCenterController.bulkLimit,
                    }),
                    style: theme.textTheme.bodySmall),
            ],
            if (approval != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              MailApprovalCard(
                busy: c.busy != null,
                approval: approval,
                title: l.t('mc.b.approvalTitle.${c.bulkKind}'),
                lines: [
                  l.f('mc.b.approvalCount', {
                    'count':
                        (approval.arguments['uids'] as List?)?.length ?? count,
                    'folder': c.folder?.name ?? '',
                  }),
                ],
                approveLabel: l.t('mc.b.approve.${c.bulkKind}'),
                onApprove: c.approveBulk,
              ),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: TextButton(
                    onPressed: c.busy == null ? c.dismissBulk : null,
                    child: Text(l.t('mc.b.later'))),
              ),
            ],
            if (ready != null) ...[
              const SizedBox(height: AnumSpacing.sm),
              Text(l.t('mc.b.exportReady')),
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton.icon(
                  onPressed: c.busy == null ? c.downloadExport : null,
                  icon: const Icon(Icons.download_outlined),
                  label: Text(l.t('mc.b.downloadMbox')),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
