import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'business_controller.dart';
import 'support_controller.dart';
import 'support_models.dart';
import 'support_screen.dart';

/// "Create ticket" from an inbound email or a WhatsApp conversation
/// (docs/business.md#tickets-from-an-inbox): asks for an optional title and
/// the severity, creates the linked ticket and says its reference, with an
/// Open action to the ticket. A refusal (an open ticket already linked, a
/// viewer, a private mailbox) stays in the dialog as the API wrote it.
Future<TicketDetail?> createTicketFromMessage(
  BuildContext context, {
  required SupportController support,
  required String source,
  required String refId,
  String? title,
}) async {
  final messenger = ScaffoldMessenger.of(context);
  final navigator = Navigator.of(context);
  final l = AnumLocalizations.maybeOf(context);
  final detail = await showDialog<TicketDetail>(
    context: context,
    builder: (_) => TicketFromMessageDialog(
      support: support,
      source: source,
      refId: refId,
      title: title,
    ),
  );
  if (detail == null) return null;
  if (support.state == BusinessLoad.ready) unawaited(support.refresh());
  messenger.showSnackBar(
    SnackBar(
      content: Text(
        l.f('biz.fromMessage.done', {'reference': detail.ticket.reference}),
      ),
      action: SnackBarAction(
        label: l.t('biz.fromMessage.open'),
        onPressed: () async {
          // The ticket screen reads the permissions from the controller.
          if (support.setup == null) await support.load();
          await navigator.push(
            MaterialPageRoute<void>(
              builder: (_) => TicketScreen(
                controller: support,
                ticketId: detail.ticket.id,
                initial: detail,
              ),
            ),
          );
        },
      ),
    ),
  );
  return detail;
}

class TicketFromMessageDialog extends StatefulWidget {
  const TicketFromMessageDialog({
    required this.support,
    required this.source,
    required this.refId,
    this.title,
    super.key,
  });
  final SupportController support;

  /// `email` or `whatsapp`.
  final String source;
  final String refId;
  final String? title;

  @override
  State<TicketFromMessageDialog> createState() =>
      _TicketFromMessageDialogState();
}

class _TicketFromMessageDialogState extends State<TicketFromMessageDialog> {
  // The API takes a title of at most 200 characters.
  late final _title = TextEditingController(
    text: (widget.title ?? '').length > 200
        ? widget.title!.substring(0, 200)
        : widget.title ?? '',
  );
  String severity = 'normal';
  bool busy = false;
  String? titleError;
  String? error;

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    final l = AnumLocalizations.maybeOf(context);
    final title = _title.text.trim();
    if (title.isNotEmpty && title.length < 3) {
      setState(() => titleError = l.t('biz.support.form.titleNeeded'));
      return;
    }
    setState(() {
      busy = true;
      titleError = null;
      error = null;
    });
    try {
      final detail = await widget.support.repository.fromMessage(
        source: widget.source,
        refId: widget.refId,
        title: title.isEmpty ? null : title,
        severity: severity,
      );
      if (mounted) Navigator.of(context).pop(detail);
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          error = SupportController.messageOf(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final scheme = Theme.of(context).colorScheme;
    final email = widget.source == 'email';
    return AlertDialog(
      scrollable: true,
      title: Text(l.t('biz.fromMessage.title')),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(l.t(email
              ? 'biz.fromMessage.intro.email'
              : 'biz.fromMessage.intro.whatsapp')),
          const SizedBox(height: AnumSpacing.sm),
          TextField(
            key: const Key('ticket-from-message-title'),
            controller: _title,
            enabled: !busy,
            maxLength: 200,
            decoration: InputDecoration(
              labelText: l.t('biz.support.form.title'),
              helperText: l.t(email
                  ? 'biz.fromMessage.titleHint.email'
                  : 'biz.fromMessage.titleHint.whatsapp'),
              helperMaxLines: 3,
              errorText: titleError,
              errorMaxLines: 3,
            ),
          ),
          const SizedBox(height: AnumSpacing.xs),
          DropdownButtonFormField<String>(
            key: const Key('ticket-from-message-severity'),
            initialValue: severity,
            isExpanded: true,
            decoration:
                InputDecoration(labelText: l.t('biz.support.form.severity')),
            items: [
              for (final value in ticketSeverities)
                DropdownMenuItem(
                  value: value,
                  child: Text(
                    l.t('biz.ticket.severity.$value'),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
            ],
            onChanged:
                busy ? null : (value) => setState(() => severity = value!),
          ),
          if (error != null) ...[
            const SizedBox(height: AnumSpacing.sm),
            Semantics(
              liveRegion: true,
              child: Text(error!, style: TextStyle(color: scheme.error)),
            ),
          ],
        ],
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.of(context).pop(),
          child: Text(l.t('cancel')),
        ),
        FilledButton(
          onPressed: busy ? null : _create,
          child: Text(
            busy
                ? l.t('biz.fromMessage.creating')
                : l.t('biz.fromMessage.create'),
          ),
        ),
      ],
    );
  }
}
