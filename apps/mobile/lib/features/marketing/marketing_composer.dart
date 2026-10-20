import 'dart:async';

import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'marketing_assist.dart';
import 'marketing_controller.dart';
import 'marketing_media.dart';
import 'marketing_models.dart';
import 'marketing_ops_models.dart';
import 'marketing_screen.dart' show mkPickTime, mkWhen;

bool _isLink(String value) {
  final uri = Uri.tryParse(value.trim());
  return uri != null &&
      (uri.scheme == 'https' || uri.scheme == 'http') &&
      uri.host.isNotEmpty;
}

/// A draft for the chosen accounts, or the edit of one post that is not out
/// yet. Checks the draft against each platform's limits as you type (the
/// server's `/posts/validate`), tracks links per platform, attaches media
/// from Files and offers AI ideas and hashtags. Scheduling (and approval)
/// happens from the post afterwards.
class ComposeSheet extends StatefulWidget {
  const ComposeSheet({required this.controller, this.post, super.key});
  final MarketingController controller;

  /// The post being edited; null composes new ones.
  final MkPost? post;
  @override
  State<ComposeSheet> createState() => _ComposeSheetState();
}

class _ComposeSheetState extends State<ComposeSheet> {
  final _en = TextEditingController(), _ar = TextEditingController();
  final _tagsEn = TextEditingController(), _tagsAr = TextEditingController();
  final _link = TextEditingController(), _title = TextEditingController();
  final Set<String> _accounts = {};
  List<MkMedia> _media = [];
  String _campaignId = '';
  DateTime? _at;
  bool _track = true, _aiUsed = false, _untrusted = false;
  String? _problem, _inserted, _tagsNote;

  final Map<String, MkValidation> _checks = {};
  String? _checkError;
  Timer? _debounce;
  int _seq = 0;

  MarketingController get c => widget.controller;
  MkPost? get post => widget.post;
  bool get editing => post != null;

  @override
  void initState() {
    super.initState();
    final p = post;
    if (p == null) {
      final connected = c.connected;
      if (connected.isNotEmpty) _accounts.add(connected.first.id);
    } else {
      final text = p.language == 'ar' ? _ar : _en;
      final tags = p.language == 'ar' ? _tagsAr : _tagsEn;
      text.text = p.text;
      tags.text = p.hashtags.join(' ');
      _link.text = p.linkUrl ?? '';
      _title.text = p.title;
      _media = [...p.media];
      _campaignId = p.campaignId ?? '';
      _at = p.scheduledAt;
      _aiUsed = p.aiGenerated;
      _untrusted = p.untrusted;
      _track = false;
    }
    for (final field in [_en, _ar, _tagsEn, _tagsAr, _link, _title]) {
      field.addListener(_changed);
    }
    _debounce = Timer(const Duration(milliseconds: 600), _validate);
  }

  @override
  void dispose() {
    _debounce?.cancel();
    for (final field in [_en, _ar, _tagsEn, _tagsAr, _link, _title]) {
      field.dispose();
    }
    super.dispose();
  }

  List<String> get _languages =>
      editing ? [post!.language] : const ['en', 'ar'];
  TextEditingController _textOf(String language) =>
      language == 'ar' ? _ar : _en;
  TextEditingController _tagsOf(String language) =>
      language == 'ar' ? _tagsAr : _tagsEn;

  List<String> get _platforms {
    if (editing) return [post!.platform];
    return {
      for (final a in c.connected)
        if (_accounts.contains(a.id)) a.platform
    }.toList();
  }

  void _changed() {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 600), _validate);
    if (mounted) setState(() {});
  }

  Future<void> _validate() async {
    final seq = ++_seq;
    final jobs = <String, ({String platform, String language})>{};
    for (final platform in _platforms) {
      for (final language in _languages) {
        if (_textOf(language).text.trim().isEmpty && _media.isEmpty) continue;
        jobs['$platform|$language'] = (platform: platform, language: language);
      }
    }
    final results = <String, MkValidation>{};
    String? failure;
    for (final entry in jobs.entries) {
      try {
        results[entry.key] = await c.repository.validate(
            platform: entry.value.platform,
            text: _textOf(entry.value.language).text.trim(),
            hashtags: parseHashtags(_tagsOf(entry.value.language).text),
            link: _link.text.trim().isEmpty ? null : _link.text.trim(),
            title: _title.text.trim(),
            media: _media);
      } catch (e) {
        failure = MarketingController.describe(e);
        break;
      }
    }
    if (!mounted || seq != _seq) return;
    setState(() {
      _checks
        ..clear()
        ..addAll(results);
      _checkError = failure;
    });
  }

  bool get _blocked => _checks.values.any((check) => !check.ok);

  Future<void> _attach() async {
    final picked = await pickMarketingMedia(context, c.ops, _media);
    if (picked == null) return;
    setState(() => _media = picked);
    _changed();
  }

  Future<void> _pickTime() async {
    final at = await mkPickTime(context, _at);
    if (at != null) setState(() => _at = at);
  }

  void _useIdea(MkIdea idea, MkIdeas result) {
    final l = context.anum;
    setState(() {
      if (_languages.contains('en') && idea.en != null) _en.text = idea.en!;
      if (_languages.contains('ar') && idea.ar != null) _ar.text = idea.ar!;
      if (_languages.contains('en') && idea.hashtagsEn.isNotEmpty) {
        _tagsEn.text = idea.hashtagsEn.join(' ');
      }
      if (_languages.contains('ar') && idea.hashtagsAr.isNotEmpty) {
        _tagsAr.text = idea.hashtagsAr.join(' ');
      }
      _aiUsed = result.fromModel;
      _untrusted = result.untrustedSource;
      _inserted = l.f('mkOps.ai.inserted', {'title': idea.title});
    });
  }

  Future<void> _suggestTags(String language) async {
    final l = context.anum;
    final text = _textOf(language).text.trim();
    if (text.isEmpty) {
      return setState(() => _problem = l.t('mkOps.ai.writeFirst'));
    }
    setState(() => _problem = null);
    final result = await c.ops.suggestHashtags(
        text: text,
        platform: _platforms.isEmpty ? 'instagram' : _platforms.first,
        language: language);
    if (result == null || !mounted) return;
    final field = _tagsOf(language);
    setState(() {
      field.text =
          parseHashtags('${field.text} ${result.hashtags.join(' ')}').join(' ');
      _aiUsed = _aiUsed || result.fromModel;
      _tagsNote = result.fromModel
          ? l.t('mkOps.ai.tagsFromModel')
          : l.t('mkOps.ai.tagsFromTemplate');
    });
  }

  bool _saving = false;

  Future<void> _save() async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      await _saveDraft();
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _saveDraft() async {
    final l = context.anum;
    final texts = {
      for (final language in _languages)
        if (_textOf(language).text.trim().isNotEmpty)
          language: _textOf(language).text.trim(),
    };
    final targets = c.connected.where((a) => _accounts.contains(a.id)).toList();
    if (!editing && _accounts.isEmpty) {
      return setState(() => _problem = l.t('mk.pickAccount'));
    }
    if (texts.isEmpty && _media.isEmpty) {
      return setState(() => _problem = l.t('mk.writeText'));
    }
    final link = _link.text.trim();
    if (link.isNotEmpty && !_isLink(link)) {
      return setState(() => _problem = l.t('mkOps.badLink'));
    }
    if (_blocked) {
      return setState(() => _problem = l.t('mkOps.fixFirst'));
    }
    final limits = c.setup?.limits ?? const {};
    for (final platform
        in editing ? [post!.platform] : targets.map((a) => a.platform)) {
      final limit = limits[platform];
      for (final text in texts.values) {
        if (limit != null && text.length > limit) {
          return setState(() => _problem = l.f('mk.tooLong', {
                'platform': l.t('mk.platform.$platform'),
                'length': text.length,
                'limit': limit
              }));
        }
      }
    }
    final campaign = c.campaigns
        .where((item) => item.id == _campaignId)
        .map((item) => item.utmCampaign)
        .firstOrNull;
    setState(() => _problem = null);
    try {
      Future<String> tracked(String platform) => c.repository
          .trackedLink(url: link, platform: platform, campaign: campaign ?? '');
      if (editing) {
        final p = post!;
        final ok = await c.editPost(p,
            text: texts[p.language] ?? '',
            hashtags: parseHashtags(_tagsOf(p.language).text),
            title: _title.text.trim(),
            link: link.isEmpty
                ? null
                : (_track ? await tracked(p.platform) : link),
            media: _media,
            campaignId: _campaignId.isEmpty ? null : _campaignId,
            at: _at,
            done: l.t('mkOps.updated'));
        if (ok && mounted) Navigator.pop(context);
        if (!ok && mounted) setState(() => _problem = c.notice);
        return;
      }
      final links = <String, String>{};
      if (link.isNotEmpty && _track) {
        for (final account in targets) {
          links[account.id] = await tracked(account.platform);
        }
      }
      final ok = await c.create(
          accountIds: _accounts.toList(),
          texts: texts,
          hashtags: {
            for (final language in texts.keys)
              language: parseHashtags(_tagsOf(language).text)
          },
          link: link.isEmpty ? null : link,
          linksByAccount: links.isEmpty ? null : links,
          title: _title.text.trim(),
          campaignId: _campaignId.isEmpty ? null : _campaignId,
          media: _media,
          aiGenerated: _aiUsed,
          untrustedSource: _untrusted,
          at: _at,
          done: l.t('mk.saved'));
      if (ok && mounted) Navigator.pop(context);
      if (!ok && mounted) setState(() => _problem = c.notice);
    } catch (e) {
      if (mounted) setState(() => _problem = MarketingController.describe(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final error = Theme.of(context).colorScheme.error;
    final needsTitle = _platforms
        .any((platform) => c.setup?.titleRequired.contains(platform) ?? false);
    return Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(context).bottom,
            left: AnumSpacing.md,
            right: AnumSpacing.md,
            top: AnumSpacing.md),
        child: Align(
            alignment: Alignment.topCenter,
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 640),
                child: SingleChildScrollView(
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                      Semantics(
                          header: true,
                          child: Text(
                              editing
                                  ? l.t('mkOps.editTitle')
                                  : l.t('mk.compose'),
                              style: text.titleLarge)),
                      const SizedBox(height: AnumSpacing.sm),
                      if (editing)
                        Text(
                            '${l.t('mk.platform.${post!.platform}')} · ${post!.accountName}',
                            style: text.titleSmall)
                      else ...[
                        Text(l.t('mk.postTo')),
                        Wrap(spacing: AnumSpacing.xs, children: [
                          for (final account in c.connected)
                            FilterChip(
                                selected: _accounts.contains(account.id),
                                onSelected: (on) {
                                  setState(() => on
                                      ? _accounts.add(account.id)
                                      : _accounts.remove(account.id));
                                  _changed();
                                },
                                label: Text(
                                    '${l.t('mk.platform.${account.platform}')} · ${account.displayName}')),
                        ]),
                      ],
                      const SizedBox(height: AnumSpacing.xs),
                      InputDecorator(
                          decoration:
                              InputDecoration(labelText: l.t('mkOps.campaign')),
                          child: DropdownButtonHideUnderline(
                              child: DropdownButton<String>(
                                  isExpanded: true,
                                  value: c.campaigns
                                          .any((x) => x.id == _campaignId)
                                      ? _campaignId
                                      : '',
                                  onChanged: (value) =>
                                      setState(() => _campaignId = value ?? ''),
                                  items: [
                                DropdownMenuItem(
                                    value: '',
                                    child: Text(l.t('mkOps.noCampaign'))),
                                for (final campaign in c.campaigns)
                                  DropdownMenuItem(
                                      value: campaign.id,
                                      child: Text(campaign.name,
                                          overflow: TextOverflow.ellipsis))
                              ]))),
                      const SizedBox(height: AnumSpacing.xs),
                      Row(children: [
                        Expanded(
                            child: OutlinedButton.icon(
                                onPressed: _pickTime,
                                icon: const Icon(Icons.event_outlined),
                                label: Text(
                                    '${l.t('mkOps.when')}: ${mkWhen(context, _at)}',
                                    overflow: TextOverflow.ellipsis))),
                        if (_at != null)
                          IconButton(
                              tooltip: l.t('mkOps.clearTime'),
                              onPressed: () => setState(() => _at = null),
                              icon: const Icon(Icons.close)),
                      ]),
                      const SizedBox(height: AnumSpacing.xs),
                      AssistPanel(
                          ops: c.ops,
                          platforms: _platforms,
                          languages: _languages,
                          fallbackBrief: () => _languages
                              .map((x) => _textOf(x).text.trim())
                              .firstWhere((x) => x.isNotEmpty,
                                  orElse: () => ''),
                          campaignId: _campaignId.isEmpty ? null : _campaignId,
                          onUse: _useIdea),
                      if (_inserted != null)
                        Semantics(
                            liveRegion: true,
                            child: Padding(
                                padding:
                                    const EdgeInsets.only(top: AnumSpacing.xs),
                                child:
                                    Text(_inserted!, style: text.bodySmall))),
                      for (final language in _languages) ...[
                        const SizedBox(height: AnumSpacing.sm),
                        TextField(
                            controller: _textOf(language),
                            minLines: 3,
                            maxLines: 8,
                            textDirection: language == 'ar'
                                ? TextDirection.rtl
                                : TextDirection.ltr,
                            decoration: InputDecoration(
                                labelText: language == 'ar'
                                    ? l.t('mk.arabic')
                                    : l.t('mk.english'))),
                        const SizedBox(height: AnumSpacing.xs),
                        TextField(
                            controller: _tagsOf(language),
                            textDirection: language == 'ar'
                                ? TextDirection.rtl
                                : TextDirection.ltr,
                            decoration: InputDecoration(
                                labelText: language == 'ar'
                                    ? l.t('mkOps.hashtagsAr')
                                    : l.t('mkOps.hashtagsEn'))),
                        Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: TextButton.icon(
                                onPressed: c.ops.assistBusy
                                    ? null
                                    : () => _suggestTags(language),
                                icon: const Icon(Icons.tag),
                                label: Text(language == 'ar'
                                    ? l.t('mkOps.ai.hashtagsAr')
                                    : l.t('mkOps.ai.hashtagsEn')))),
                      ],
                      ListenableBuilder(
                          listenable: c.ops,
                          builder: (context, _) =>
                              c.ops.assistProblem == null ||
                                      !c.ops.assistProblemIsTags
                                  ? const SizedBox.shrink()
                                  : Semantics(
                                      liveRegion: true,
                                      child: Text(mkAssistProblemText(l, c.ops),
                                          style: TextStyle(color: error)))),
                      if (_tagsNote != null)
                        Text(_tagsNote!, style: text.bodySmall),
                      if (needsTitle) ...[
                        const SizedBox(height: AnumSpacing.xs),
                        TextField(
                            controller: _title,
                            maxLength: 100,
                            decoration: InputDecoration(
                                labelText: l.t('mkOps.videoTitle'))),
                      ],
                      const SizedBox(height: AnumSpacing.xs),
                      TextField(
                          controller: _link,
                          keyboardType: TextInputType.url,
                          textDirection: TextDirection.ltr,
                          decoration: InputDecoration(
                              labelText: l.t('mk.link'), hintText: 'https://')),
                      SwitchListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _track,
                          onChanged: (on) => setState(() => _track = on),
                          title: Text(l.t('mkOps.utm'))),
                      _mediaSection(context),
                      _checkSection(context),
                      if (_problem != null)
                        Semantics(
                            liveRegion: true,
                            child: Padding(
                                padding:
                                    const EdgeInsets.only(top: AnumSpacing.sm),
                                child: Text(_problem!,
                                    style: TextStyle(color: error)))),
                      const SizedBox(height: AnumSpacing.md),
                      FilledButton(
                          onPressed:
                              c.busy || _saving || _blocked ? null : _save,
                          child: Text(
                              editing ? l.t('mkOps.update') : l.t('mk.save'))),
                      if (_blocked)
                        Text(l.t('mkOps.fixFirst'), style: text.bodySmall),
                      const SizedBox(height: AnumSpacing.md),
                    ])))));
  }

  Widget _mediaSection(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Align(
          alignment: AlignmentDirectional.centerStart,
          child: OutlinedButton.icon(
              onPressed: _attach,
              icon: const Icon(Icons.attach_file),
              label: Text(l.t('mkOps.media.attach')))),
      if (_media.isEmpty)
        Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xxs),
            child: Text(l.t('mkOps.media.none'), style: text.bodySmall)),
      for (final item in _media)
        ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(item.kind == 'video'
                ? Icons.videocam_outlined
                : Icons.image_outlined),
            title: Text(item.label, textDirection: TextDirection.ltr),
            subtitle: Text(l.t('mkOps.media.${item.kind}')),
            trailing: IconButton(
                tooltip: l.f('mkOps.media.removeOf', {'name': item.label}),
                onPressed: () {
                  setState(() => _media =
                      _media.where((x) => x.fileId != item.fileId).toList());
                  _changed();
                },
                icon: const Icon(Icons.close))),
    ]);
  }

  Widget _checkSection(BuildContext context) {
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final error = Theme.of(context).colorScheme.error;
    if (_checkError != null) {
      return Padding(
          padding: const EdgeInsets.only(top: AnumSpacing.xs),
          child: Text(l.f('mkOps.check.unavailable', {'message': _checkError!}),
              style: text.bodySmall));
    }
    if (_checks.isEmpty) return const SizedBox.shrink();
    return Semantics(
        container: true,
        label: l.t('mkOps.check.title'),
        child: Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(l.t('mkOps.check.title'), style: text.titleSmall),
                  for (final entry in _checks.entries) ...[
                    Text(
                        l.f('mkOps.check.line', {
                          'platform':
                              l.t('mk.platform.${entry.key.split('|').first}'),
                          'language':
                              l.t('mkOps.lang.${entry.key.split('|').last}'),
                          'length': entry.value.length,
                          'limit': entry.value.limit
                        }),
                        style: text.bodySmall?.copyWith(
                            color: entry.value.length > entry.value.limit
                                ? error
                                : null)),
                    for (final issue in entry.value.issues)
                      Text(
                          issue.severity == 'error'
                              ? issue.message
                              : l.f('mkOps.check.warning',
                                  {'message': issue.message}),
                          style: text.bodySmall?.copyWith(
                              color: issue.severity == 'error' ? error : null)),
                  ],
                ])));
  }
}
