import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'marketing_assist.dart' show mkWeekdays;
import 'marketing_controller.dart';
import 'marketing_models.dart';
import 'marketing_ops_controller.dart';
import 'marketing_ops_models.dart';

const mkTimezones = [
  'Asia/Riyadh',
  'Asia/Dubai',
  'Asia/Qatar',
  'Asia/Kuwait',
  'Asia/Bahrain',
  'Asia/Muscat',
  'UTC'
];

/// A card with a loading, denied or error-with-retry state around [ready].
Widget _sectionState(BuildContext context,
    {required MkLoad phase,
    required String title,
    required String? error,
    required VoidCallback load,
    required Widget Function() ready}) {
  final l = context.anum;
  final text = Theme.of(context).textTheme;
  return Card(
      child: Padding(
          padding: const EdgeInsets.all(AnumSpacing.md),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Semantics(header: true, child: Text(title, style: text.titleSmall)),
            const SizedBox(height: AnumSpacing.xs),
            switch (phase) {
              MkLoad.idle || MkLoad.loading => Center(
                  child: Semantics(
                      label: l.t('mk.loading'),
                      child: const CircularProgressIndicator())),
              MkLoad.denied =>
                Text(l.f('mkOps.denied', {'message': error ?? ''})),
              MkLoad.error =>
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(l.f('mkOps.sectionError', {'message': error ?? ''})),
                  const SizedBox(height: AnumSpacing.xs),
                  OutlinedButton(onPressed: load, child: Text(l.t('retry'))),
                ]),
              MkLoad.ready => ready(),
            },
          ])));
}

/// Brand voice, time zone and default hashtags (`/preferences`): owners edit,
/// members read.
class PreferencesCard extends StatefulWidget {
  const PreferencesCard({required this.controller, super.key});
  final MarketingController controller;
  @override
  State<PreferencesCard> createState() => _PreferencesCardState();
}

class _PreferencesCardState extends State<PreferencesCard> {
  MarketingOps get ops => widget.controller.ops;

  @override
  void initState() {
    super.initState();
    if (ops.prefsPhase == MkLoad.idle) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) ops.loadPreferences();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
        listenable: ops,
        builder: (context, _) => _sectionState(context,
            phase: ops.prefsPhase,
            title: l.t('mkOps.prefs.title'),
            error: ops.prefsError,
            load: ops.loadPreferences,
            ready: () => _PrefsForm(
                key: ValueKey(ops.prefs),
                controller: widget.controller,
                prefs: ops.prefs!)));
  }
}

class _PrefsForm extends StatefulWidget {
  const _PrefsForm({required this.controller, required this.prefs, super.key});
  final MarketingController controller;
  final MkPreferences prefs;
  @override
  State<_PrefsForm> createState() => _PrefsFormState();
}

class _PrefsFormState extends State<_PrefsForm> {
  late final _voice = TextEditingController(text: widget.prefs.brandVoice);
  late final _en =
      TextEditingController(text: widget.prefs.hashtagsEn.join(' '));
  late final _ar =
      TextEditingController(text: widget.prefs.hashtagsAr.join(' '));
  late String _zone = widget.prefs.timezone;
  String? _problem;

  @override
  void dispose() {
    for (final c in [_voice, _en, _ar]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final l = context.anum;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final failure = await widget.controller.ops.savePreferences(MkPreferences(
        brandVoice: _voice.text.trim(),
        timezone: _zone,
        hashtagsEn: parseHashtags(_en.text),
        hashtagsAr: parseHashtags(_ar.text)));
    if (failure == null) {
      messenger
          ?.showSnackBar(SnackBar(content: Text(l.t('mkOps.prefs.saved'))));
      return;
    }
    if (mounted) {
      setState(() => _problem = MarketingController.describe(failure));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final owner = widget.controller.canManage;
    final zones = {...mkTimezones, _zone}.toList();
    final saving = widget.controller.ops.prefsSaving;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (!owner)
        Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Text(l.t('mkOps.prefs.ownerOnly'))),
      TextField(
          controller: _voice,
          readOnly: !owner,
          minLines: 2,
          maxLines: 5,
          maxLength: 2000,
          decoration: InputDecoration(
              labelText: l.t('mkOps.prefs.voice'),
              hintText: l.t('mkOps.prefs.voiceHint'))),
      InputDecorator(
          decoration: InputDecoration(labelText: l.t('mkOps.prefs.timezone')),
          child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                  isExpanded: true,
                  value: _zone,
                  onChanged:
                      owner ? (v) => setState(() => _zone = v ?? _zone) : null,
                  items: [
                for (final zone in zones)
                  DropdownMenuItem(
                      value: zone,
                      child: Text(zone, textDirection: TextDirection.ltr))
              ]))),
      const SizedBox(height: AnumSpacing.xs),
      TextField(
          controller: _en,
          readOnly: !owner,
          textDirection: TextDirection.ltr,
          decoration: InputDecoration(labelText: l.t('mkOps.prefs.tagsEn'))),
      const SizedBox(height: AnumSpacing.xs),
      TextField(
          controller: _ar,
          readOnly: !owner,
          textDirection: TextDirection.rtl,
          decoration: InputDecoration(labelText: l.t('mkOps.prefs.tagsAr'))),
      if (_problem != null)
        Semantics(
            liveRegion: true,
            child: Padding(
                padding: const EdgeInsets.only(top: AnumSpacing.xs),
                child: Text(_problem!,
                    style: TextStyle(
                        color: Theme.of(context).colorScheme.error)))),
      if (owner)
        Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.sm),
            child: Align(
                alignment: AlignmentDirectional.centerStart,
                child: FilledButton(
                    onPressed: saving ? null : _save,
                    child: Text(l.t('mkOps.prefs.save'))))),
    ]);
  }
}

/// The best hours to post, from this workspace's history or the Gulf
/// defaults (`/analytics/best-times`).
class BestTimesCard extends StatefulWidget {
  const BestTimesCard({required this.controller, super.key});
  final MarketingController controller;
  @override
  State<BestTimesCard> createState() => _BestTimesCardState();
}

class _BestTimesCardState extends State<BestTimesCard> {
  MarketingOps get ops => widget.controller.ops;

  @override
  void initState() {
    super.initState();
    if (ops.timesPhase == MkLoad.idle) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) ops.loadBestTimes();
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    return ListenableBuilder(
        listenable: ops,
        builder: (context, _) => _sectionState(context,
                phase: ops.timesPhase,
                title: l.t('mkOps.times.title'),
                error: ops.timesError,
                load: ops.loadBestTimes, ready: () {
              if (ops.slots.isEmpty) {
                return AnumEmpty(
                    icon: Icons.schedule,
                    title: l.t('mkOps.times.none'),
                    message: l.t('mkOps.times.noneHint'));
              }
              final days = mkWeekdays(l);
              return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    for (final slot in ops.slots)
                      MergeSemantics(
                          child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: const Icon(Icons.schedule),
                              title: Text(mkSlotLabel(slot, days),
                                  textDirection: TextDirection.ltr),
                              subtitle: Text(
                                  slot.fromHistory
                                      ? l.f('mkOps.times.history', {
                                          'rate': slot.rate == null
                                              ? '-'
                                              : '${(slot.rate! * 100).toStringAsFixed(1)}%',
                                          'count': slot.posts
                                        })
                                      : l.t('mkOps.times.default'),
                                  style: text.bodySmall))),
                  ]);
            }));
  }
}
