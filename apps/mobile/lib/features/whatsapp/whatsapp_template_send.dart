import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'whatsapp_controller.dart';
import 'whatsapp_models.dart';
import 'whatsapp_templates_screen.dart';

/// Opens the template picker for a thread. The outcome (sent, waiting for an
/// owner, or failed with the server's sentence) shows in the thread.
Future<void> showWaTemplateSend(
        BuildContext context, WhatsAppThreadController controller) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (_) => WaTemplateSendSheet(controller: controller),
    );

/// Pick an approved template, fill its `{{n}}` parameters, see the message as
/// the customer will read it and send. Templates are the only way to write
/// to a customer once the 24-hour window is closed. Whether the send goes
/// out at once or waits for an owner's approval is decided by the server
/// (the auto-send list, approval switches and autonomy rules).
class WaTemplateSendSheet extends StatefulWidget {
  const WaTemplateSendSheet({required this.controller, super.key});
  final WhatsAppThreadController controller;

  @override
  State<WaTemplateSendSheet> createState() => _WaTemplateSendSheetState();
}

class _WaTemplateSendSheetState extends State<WaTemplateSendSheet> {
  List<WaTemplate>? _templates;
  String? _loadError;
  bool _denied = false;
  WaTemplate? _chosen;
  List<TextEditingController> _fields = const [];
  final _retired = <TextEditingController>[];
  String? _problem;
  bool _sending = false;

  WhatsAppThreadController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final field in [..._fields, ..._retired]) {
      field.dispose();
    }
    super.dispose();
  }

  Future<void> _load({bool refresh = false}) async {
    setState(() {
      _loadError = null;
      _denied = false;
      if (refresh) _templates = null;
    });
    try {
      final list = await c.repository.templates(refresh: refresh);
      if (!mounted) return;
      setState(() => _templates = [
            for (final t in list.templates)
              if (t.approved) t
          ]);
    } on Object catch (e) {
      if (!mounted) return;
      setState(() {
        _templates = const [];
        _denied = e is ApiException && e.isPermissionDenied;
        _loadError = e is ApiException ? e.readable : '$e';
      });
    }
  }

  void _choose(WaTemplate? template) {
    // Old fields are disposed with the sheet: the tree may still show them
    // until it rebuilds.
    _retired.addAll(_fields);
    setState(() {
      _chosen = template;
      _problem = null;
      _fields = [
        for (var i = 0; i < (template?.parameterCount ?? 0); i++)
          TextEditingController()..addListener(() => setState(() {})),
      ];
    });
  }

  List<String> get _values => [for (final f in _fields) f.text.trim()];

  Future<void> _send() async {
    final template = _chosen;
    if (template == null || _sending) return;
    final l = context.anum;
    if (_values.any((v) => v.isEmpty)) {
      setState(() => _problem = l.t('waOps.send.fillAll'));
      return;
    }
    setState(() {
      _sending = true;
      _problem = null;
    });
    final ok = await c.sendTemplate(template, _values);
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      // The server's sentence (409, 422, 502 or the failure text).
      setState(() {
        _sending = false;
        _problem = c.notice ?? l.t('waOps.send.failed');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    final templates = _templates;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
            AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.md),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Semantics(
                  header: true,
                  child:
                      Text(l.t('waOps.send.title'), style: text.titleMedium)),
              const SizedBox(height: AnumSpacing.xs),
              if (templates == null)
                Semantics(
                    liveRegion: true,
                    label: l.t('waOps.templates.loading'),
                    child: const Center(
                        child: Padding(
                            padding: EdgeInsets.all(AnumSpacing.lg),
                            child: CircularProgressIndicator())))
              else if (_loadError != null) ...[
                Text(
                    _denied
                        ? _loadError!
                        : l.f(
                            'waOps.send.loadFailed', {'message': _loadError!}),
                    style: text.bodyMedium?.copyWith(color: p.stop)),
                if (!_denied)
                  Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: TextButton(
                          onPressed: () => _load(refresh: true),
                          child: Text(l.t('retry')))),
              ] else if (templates.isEmpty) ...[
                Text(l.t('waOps.send.none'), style: text.bodyMedium),
                Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton(
                        onPressed: () => _load(refresh: true),
                        child: Text(l.t('waOps.templates.refresh')))),
              ] else ...[
                DropdownButtonFormField<WaTemplate>(
                  key: const Key('wa-send-template'),
                  initialValue: _chosen,
                  isExpanded: true,
                  decoration: InputDecoration(
                      labelText: l.t('waOps.send.choose'),
                      border: const OutlineInputBorder()),
                  items: [
                    for (final t in templates)
                      DropdownMenuItem(
                          value: t,
                          child: Text(
                              '${t.name} (${t.language})'
                              '${t.autoSend ? ' · ${l.t('waOps.templates.autoShort')}' : ''}',
                              overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: _sending ? null : _choose,
                ),
                if (_chosen case final template?) ...[
                  for (var i = 0; i < _fields.length; i++) ...[
                    const SizedBox(height: AnumSpacing.sm),
                    TextField(
                      key: Key('wa-send-param-$i'),
                      controller: _fields[i],
                      maxLength: 1024,
                      enabled: !_sending,
                      textInputAction: TextInputAction.next,
                      decoration: InputDecoration(
                          labelText:
                              l.f('waOps.send.parameter', {'number': i + 1}),
                          counterText: '',
                          border: const OutlineInputBorder()),
                    ),
                  ],
                  const SizedBox(height: AnumSpacing.sm),
                  Semantics(
                    container: true,
                    label: l.t('waOps.send.preview'),
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                          color: p.surface2,
                          borderRadius: BorderRadius.circular(AnumRadius.md),
                          border: Border.all(color: p.edge)),
                      child: Padding(
                        padding: const EdgeInsets.all(AnumSpacing.sm),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(l.t('waOps.send.preview'),
                                  style: text.bodySmall),
                              const SizedBox(height: AnumSpacing.xxs),
                              Text(
                                  key: const Key('wa-send-preview'),
                                  fillTemplate(template.body, _values),
                                  textDirection: waTextDirection(template.body),
                                  style: text.bodyMedium),
                            ]),
                      ),
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  Text(
                      template.autoSend
                          ? l.t('waOps.send.autoNote')
                          : l.t('waOps.send.approvalNote'),
                      style: text.bodySmall),
                ],
                if (_problem != null) ...[
                  const SizedBox(height: AnumSpacing.xs),
                  Semantics(
                      liveRegion: true,
                      child: Text(_problem!,
                          key: const Key('wa-send-problem'),
                          style: text.bodyMedium?.copyWith(color: p.stop))),
                ],
                const SizedBox(height: AnumSpacing.sm),
                Align(
                    alignment: AlignmentDirectional.centerEnd,
                    child: FilledButton.icon(
                        key: const Key('wa-send-submit'),
                        onPressed: _chosen == null || _sending ? null : _send,
                        icon: const Icon(Icons.send),
                        label: Text(_sending
                            ? l.t('waSending')
                            : (_chosen?.autoSend ?? false)
                                ? l.t('waOps.send.now')
                                : l.t('waSendForApproval')))),
              ],
            ]),
      ),
    );
  }
}
