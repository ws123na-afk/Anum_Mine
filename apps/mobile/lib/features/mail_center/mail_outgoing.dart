import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'mail_center_controller.dart';
import 'mail_center_models.dart';
import 'mail_center_screens.dart';

/// Attachments, send later and the Scheduled list on the phone
/// (docs/email.md#attachments, docs/email.md#scheduled-send). The API checks
/// every file again and runs the malware scanner; the payload hash of the
/// send approval covers the files and the time.

/// The files of the draft in the composer: Attach files, each upload's
/// progress with Stop, then each file's name, size, Not scanned, Download
/// and Remove.
class MailAttachmentsSection extends StatelessWidget {
  const MailAttachmentsSection({
    super.key,
    required this.controller,
    required this.draft,
    required this.editable,
  });
  final MailCenterController controller;
  final MailDraft draft;
  final bool editable;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    final theme = Theme.of(context);
    final setup = c.setup;
    final full = setup != null &&
        draft.attachments.length + c.uploads.length >=
            setup.attachmentsPerMessage;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Semantics(
          header: true,
          child: Text(l.t('mc.attachments'), style: theme.textTheme.titleSmall),
        ),
        if (setup != null)
          Text(
            l.f('mc.f.limits', {
              'count': setup.attachmentsPerMessage,
              'size': mailSize(setup.attachmentMaxBytes),
              'total': mailSize(setup.attachmentsTotalBytes),
            }),
            style: theme.textTheme.bodySmall,
          ),
        if (draft.attachments.isEmpty && c.uploads.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Text(l.t('mc.f.none')),
          ),
        for (final upload in c.uploads)
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.upload_file_outlined),
            title:
                Text(upload.name, maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Semantics(
              label: l.f('mc.f.uploading', {
                'percent':
                    upload.total == 0 ? 0 : (upload.sent * 100 ~/ upload.total),
              }),
              child: LinearProgressIndicator(
                value: upload.total == 0 ? null : upload.sent / upload.total,
              ),
            ),
            trailing: IconButton(
              tooltip: l.f('mc.f.stop', {'name': upload.name}),
              onPressed: () => c.stopUpload(upload),
              icon: const Icon(Icons.stop_circle_outlined),
            ),
          ),
        for (final file in draft.attachments)
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.attach_file),
            title: Text(file.filename,
                maxLines: 1, overflow: TextOverflow.ellipsis),
            subtitle: Text([
              mailSize(file.size),
              if (!file.scanned) l.t('mc.f.notScanned'),
            ].join(' · ')),
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                IconButton(
                  tooltip: l.f('mc.download', {'name': file.filename}),
                  onPressed: c.busy == null ? () => c.downloadFile(file) : null,
                  icon: const Icon(Icons.download_outlined),
                ),
                if (editable)
                  IconButton(
                    tooltip: l.f('mc.f.remove', {'name': file.filename}),
                    onPressed: c.busy == null && c.uploads.isEmpty
                        ? () => c.removeFile(file)
                        : null,
                    icon: const Icon(Icons.close),
                  ),
              ],
            ),
          ),
        if (draft.attachmentNote != null)
          Text(draft.attachmentNote!, style: theme.textTheme.bodySmall),
        if (c.fileProblems.isNotEmpty)
          Semantics(
            liveRegion: true,
            child: Padding(
              padding: const EdgeInsets.only(top: AnumSpacing.xs),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (final problem in c.fileProblems)
                    Text(
                      l.f('mc.f.${problem.key}', {
                        'name': problem.name,
                        'size': mailSize(setup?.attachmentMaxBytes ?? 0),
                        'count': setup?.attachmentsPerMessage ?? 0,
                        'total': mailSize(setup?.attachmentsTotalBytes ?? 0),
                        'accepted': setup?.attachmentsAccepted ?? '',
                      }),
                      style: TextStyle(color: theme.colorScheme.error),
                    ),
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton(
                      onPressed: c.clearFileProblems,
                      child: Text(l.t('mc.f.dismiss')),
                    ),
                  ),
                ],
              ),
            ),
          ),
        if (editable)
          Align(
            alignment: AlignmentDirectional.centerStart,
            child: OutlinedButton.icon(
              onPressed: c.busy == null && c.uploads.isEmpty && !full
                  ? c.attachFiles
                  : null,
              icon: const Icon(Icons.attach_file),
              label: Text(l.t('mc.f.attach')),
            ),
          ),
        if (draft.attachments.isNotEmpty && editable)
          Text(l.t('mc.f.withdraws'), style: theme.textTheme.bodySmall),
      ],
    );
  }
}

/// Asks for a send time: tomorrow or Monday at 08:00 in the person's own time
/// zone, or a date and a time. A time too soon or past the limit is said
/// before anything is asked; null when the person cancels.
Future<DateTime?> pickSendTime(BuildContext context, MailCenterController c,
        {DateTime? initial}) =>
    showModalBottomSheet<DateTime>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _SchedulePicker(controller: c, initial: initial),
    );

class _SchedulePicker extends StatefulWidget {
  const _SchedulePicker({required this.controller, this.initial});
  final MailCenterController controller;
  final DateTime? initial;

  @override
  State<_SchedulePicker> createState() => _SchedulePickerState();
}

class _SchedulePickerState extends State<_SchedulePicker> {
  late DateTime? _picked = _future(widget.initial);
  String? _problem;

  DateTime? _future(DateTime? value) {
    if (value == null) return null;
    final local = value.toLocal();
    return local.isAfter(widget.controller.now()) ? local : null;
  }

  DateTime _at8(DateTime day) => DateTime(day.year, day.month, day.day, 8);

  DateTime get _tomorrow =>
      _at8(widget.controller.now().add(const Duration(days: 1)));

  DateTime get _monday {
    final now = widget.controller.now();
    final days = (DateTime.monday - now.weekday + 7) % 7;
    return _at8(now.add(Duration(days: days == 0 ? 7 : days)));
  }

  void _choose(DateTime value) => setState(() {
        _picked = value;
        _problem = widget.controller.scheduleProblem(value);
      });

  Future<void> _pickDate() async {
    final now = widget.controller.now();
    final max = widget.controller.setup?.scheduleMaxDays ?? 30;
    final start = _picked ?? _tomorrow;
    final date = await showDatePicker(
      context: context,
      initialDate: start.isBefore(now) ? now : start,
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: now.add(Duration(days: max)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(start),
    );
    if (time == null) return;
    _choose(DateTime(date.year, date.month, date.day, time.hour, time.minute));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final picked = _picked;
    final zone = widget.controller.now().timeZoneName;
    return Padding(
      padding: EdgeInsets.only(
          bottom: MediaQuery.viewInsetsOf(context).bottom,
          left: AnumSpacing.md,
          right: AnumSpacing.md,
          top: AnumSpacing.md),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(l.t('mc.s.pickTitle'),
                style: Theme.of(context).textTheme.titleMedium),
            Text(l.f('mc.s.zone', {'zone': zone}),
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              spacing: AnumSpacing.xs,
              runSpacing: AnumSpacing.xs,
              children: [
                ChoiceChip(
                  label: Text(l.f('mc.s.tomorrow',
                      {'time': mailDateTime(context, _tomorrow)})),
                  selected: picked == _tomorrow,
                  onSelected: (_) => _choose(_tomorrow),
                ),
                ChoiceChip(
                  label: Text(l.f(
                      'mc.s.monday', {'time': mailDateTime(context, _monday)})),
                  selected: picked == _monday,
                  onSelected: (_) => _choose(_monday),
                ),
                ActionChip(
                  avatar: const Icon(Icons.event_outlined, size: 18),
                  label: Text(l.t('mc.s.pickDate')),
                  onPressed: _pickDate,
                ),
              ],
            ),
            const SizedBox(height: AnumSpacing.sm),
            Semantics(
              liveRegion: true,
              child: Text(
                picked == null
                    ? l.t('mc.s.noneYet')
                    : l.f(
                        'mc.s.picked', {'time': mailDateTime(context, picked)}),
              ),
            ),
            if (_problem != null)
              Text(
                l.f(_problem!, {
                  'days': widget.controller.setup?.scheduleMaxDays ?? 30,
                }),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            const SizedBox(height: AnumSpacing.xs),
            Text(l.t('mc.s.approveBefore'),
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: AnumSpacing.sm),
            Wrap(
              alignment: WrapAlignment.end,
              spacing: AnumSpacing.xs,
              children: [
                TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: Text(l.t('mc.cancel'))),
                FilledButton(
                  onPressed: picked == null || _problem != null
                      ? null
                      : () {
                          final problem =
                              widget.controller.scheduleProblem(picked);
                          if (problem != null) {
                            setState(() => _problem = problem);
                            return;
                          }
                          Navigator.pop(context, picked);
                        },
                  child: Text(l.t('mc.s.ask')),
                ),
              ],
            ),
            const SizedBox(height: AnumSpacing.md),
          ],
        ),
      ),
    );
  }
}

/// The person's scheduled messages in the open mailbox, soonest first.
class MailScheduledScreen extends StatelessWidget {
  const MailScheduledScreen({super.key, required this.controller});
  final MailCenterController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final c = controller;
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) => Scaffold(
        appBar: AppBar(
          title: Text(l.t('mc.s.title')),
          actions: [
            IconButton(
              onPressed: c.scheduledPhase == MailPhase.loading
                  ? null
                  : c.loadScheduled,
              tooltip: l.t('refresh'),
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: MailNotices(controller: c, child: mailPage(_body(context, l))),
      ),
    );
  }

  Widget _body(BuildContext context, AnumLocalizations l) {
    final c = controller;
    final state = stateFor(context, c.scheduledPhase, c.scheduledError,
        onRetry: c.loadScheduled, hasData: c.scheduledList.isNotEmpty);
    if (state != null) return state;
    if (c.scheduledList.isEmpty) {
      return MailState(
        icon: Icons.schedule_send_outlined,
        title: l.t('mc.s.empty'),
        text: l.t('mc.s.emptyText'),
      );
    }
    return RefreshIndicator(
      onRefresh: c.loadScheduled,
      child: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          for (final draft in c.scheduledList)
            Card(
              margin: const EdgeInsets.only(bottom: AnumSpacing.xs),
              child: Padding(
                padding: const EdgeInsets.all(AnumSpacing.sm),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(mailDraftIcon(draft.status)),
                      title: Text(
                        draft.subject.isEmpty
                            ? l.t('mc.noSubject')
                            : draft.subject,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        [
                          mailDraftStatus(context, draft),
                          if (draft.to.isNotEmpty) draft.to.join(', '),
                        ].join(' · '),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                      onTap: () {
                        c.editDraft(draft);
                        Navigator.push(
                          context,
                          MaterialPageRoute<void>(
                              builder: (_) => MailComposeScreen(controller: c)),
                        );
                      },
                    ),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: OutlinedButton.icon(
                        onPressed: c.busy == null
                            ? () => c.cancelSchedule(draft)
                            : null,
                        icon: const Icon(Icons.cancel_schedule_send_outlined),
                        label: Text(l.t('mc.s.cancel')),
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }
}
