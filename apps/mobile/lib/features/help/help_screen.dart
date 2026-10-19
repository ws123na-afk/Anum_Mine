import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import 'help_library.dart';

/// Help screen strings, English and Arabic (docs/help.md).
const helpStringsEn = <String, String>{
  'title': 'Help',
  'search': 'Search help',
  'loading': 'Loading help…',
  'error':
      "Couldn't open the help articles: {message}. Reinstall the app if this keeps happening.",
  'empty':
      'No article matches "{query}". Try another word, or open the full documentation.',
  'docs': 'Full documentation',
  'offline':
      'Help articles are part of the app, so they also work offline. Steps name the web app’s pages; the phone uses the same names where the feature exists.',
  'count.one': '{count} article',
  'count.other': '{count} articles',
};

const helpStringsAr = <String, String>{
  'title': 'المساعدة',
  'search': 'ابحث في المساعدة',
  'loading': 'جارٍ تحميل المساعدة…',
  'error':
      'تعذر فتح مقالات المساعدة: {message}. أعد تثبيت التطبيق إن تكرر ذلك.',
  'empty':
      'لا توجد مقالة تطابق "{query}". جرّب كلمة أخرى، أو افتح التوثيق الكامل.',
  'docs': 'التوثيق الكامل',
  'offline':
      'مقالات المساعدة جزء من التطبيق، لذلك تعمل دون اتصال أيضًا. تذكر الخطوات صفحات تطبيق الويب، ويستخدم الهاتف الأسماء نفسها حيث تتوفر الميزة.',
  'count.one': 'مقالة واحدة',
  'count.other': '{count} مقالة',
};

String _s(BuildContext context, String key,
    [Map<String, Object> values = const {}]) {
  final arabic = AnumLocalizations.maybeOf(context).isArabic;
  var text = (arabic ? helpStringsAr : helpStringsEn)[key] ??
      helpStringsEn[key] ??
      key;
  values.forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
  return text;
}

/// "Help" in the current language, for the button that opens [HelpScreen].
String helpTitle(BuildContext context) => _s(context, 'title');

/// The in-app help, reached from Settings: search, a list of articles per
/// module and the article itself. [loader] defaults to the bundled assets.
class HelpScreen extends StatefulWidget {
  const HelpScreen({this.loader = loadHelpArticles, super.key});
  final Future<List<HelpArticle>> Function() loader;

  @override
  State<HelpScreen> createState() => _HelpScreenState();
}

class _HelpScreenState extends State<HelpScreen> {
  late final Future<List<HelpArticle>> _articles = widget.loader();
  final _query = TextEditingController();

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final locale = AnumLocalizations.maybeOf(context).isArabic ? 'ar' : 'en';
    return Scaffold(
      appBar: AppBar(title: Text(_s(context, 'title'))),
      body: FutureBuilder<List<HelpArticle>>(
        future: _articles,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return _Message(
                icon: Icons.error_outline,
                text: _s(context, 'error', {'message': '${snapshot.error}'}));
          }
          if (!snapshot.hasData) {
            return _Message(
                icon: Icons.help_outline,
                text: _s(context, 'loading'),
                loading: true);
          }
          final all = helpArticlesFor(snapshot.data!, locale);
          return ListenableBuilder(
            listenable: _query,
            builder: (context, _) {
              final shown = searchHelpArticles(all, _query.text);
              return ListView(
                padding: const EdgeInsets.all(AnumSpacing.md),
                children: [
                  TextField(
                    key: const Key('help-search'),
                    controller: _query,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      prefixIcon: const Icon(Icons.search),
                      labelText: _s(context, 'search'),
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  Semantics(
                    liveRegion: true,
                    child: Text(
                      _s(
                          context,
                          shown.length == 1 ? 'count.one' : 'count.other',
                          {'count': shown.length}),
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                  const SizedBox(height: AnumSpacing.xs),
                  if (shown.isEmpty)
                    _Message(
                        icon: Icons.search_off,
                        text: _s(context, 'empty', {'query': _query.text}),
                        action: _DocsButton(url: '${helpDocsBaseUrl}help.md'))
                  else
                    for (final article in shown)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.menu_book_outlined),
                        title: Text(article.title),
                        subtitle: Text(article.summary),
                        onTap: () => Navigator.of(context).push(
                            MaterialPageRoute<void>(
                                builder: (_) =>
                                    HelpArticleScreen(article: article))),
                      ),
                  const SizedBox(height: AnumSpacing.md),
                  Text(_s(context, 'offline'),
                      style: Theme.of(context).textTheme.bodySmall),
                ],
              );
            },
          );
        },
      ),
    );
  }
}

/// One article, rendered from its Markdown.
class HelpArticleScreen extends StatelessWidget {
  const HelpArticleScreen({required this.article, super.key});
  final HelpArticle article;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(article.title)),
      body: ListView(
        padding: const EdgeInsets.all(AnumSpacing.md),
        children: [
          if (article.summary.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: AnumSpacing.sm),
              child: Text(article.summary, style: theme.textTheme.bodyLarge),
            ),
          ..._blocks(context, article.body),
          if (article.docsUrl != null) ...[
            const SizedBox(height: AnumSpacing.md),
            Align(
                alignment: AlignmentDirectional.centerStart,
                child: _DocsButton(url: article.docsUrl!)),
          ],
        ],
      ),
    );
  }
}

/// A small Markdown reader for help articles: headings, numbered and bulleted
/// lists, paragraphs, **bold** and links. Articles use nothing else.
List<Widget> _blocks(BuildContext context, String body) {
  final theme = Theme.of(context);
  final out = <Widget>[];
  for (final raw in body.split(RegExp(r'\n\s*\n'))) {
    final block = raw.trim();
    if (block.isEmpty) continue;
    if (block.startsWith('## ') || block.startsWith('### ')) {
      out.add(Padding(
        padding:
            const EdgeInsets.only(top: AnumSpacing.md, bottom: AnumSpacing.xs),
        child: Semantics(
            header: true,
            child: Text(block.replaceFirst(RegExp(r'^#+\s+'), ''),
                style: theme.textTheme.titleMedium)),
      ));
      continue;
    }
    final lines = block.split('\n');
    final ordered = lines.every((l) => RegExp(r'^\d+\.\s').hasMatch(l.trim()));
    final bullets = lines.every((l) => RegExp(r'^[-*]\s').hasMatch(l.trim()));
    if (ordered || bullets) {
      for (var i = 0; i < lines.length; i++) {
        final text =
            lines[i].trim().replaceFirst(RegExp(r'^(\d+\.|[-*])\s+'), '');
        out.add(Padding(
          padding: const EdgeInsets.only(bottom: AnumSpacing.xxs),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            SizedBox(
                width: AnumSpacing.lg,
                child: Text(ordered ? '${i + 1}.' : '•')),
            Expanded(child: _InlineText(text)),
          ]),
        ));
      }
      continue;
    }
    out.add(Padding(
      padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
      child: _InlineText(lines.join(' ')),
    ));
  }
  return out;
}

final _inline = RegExp(r'\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)');

class _InlineText extends StatefulWidget {
  const _InlineText(this.text);
  final String text;
  @override
  State<_InlineText> createState() => _InlineTextState();
}

class _InlineTextState extends State<_InlineText> {
  final _taps = <TapGestureRecognizer>[];

  @override
  void dispose() {
    for (final tap in _taps) {
      tap.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    for (final tap in _taps) {
      tap.dispose();
    }
    _taps.clear();
    final theme = Theme.of(context);
    final spans = <InlineSpan>[];
    var at = 0;
    for (final m in _inline.allMatches(widget.text)) {
      if (m.start > at) {
        spans.add(TextSpan(text: widget.text.substring(at, m.start)));
      }
      if (m.group(1) != null) {
        spans.add(TextSpan(
            text: m.group(1),
            style: const TextStyle(fontWeight: FontWeight.w600)));
      } else {
        final url = m.group(3)!;
        final tap = TapGestureRecognizer()..onTap = () => _open(url);
        _taps.add(tap);
        spans.add(TextSpan(
            text: m.group(2),
            recognizer: tap,
            style: TextStyle(
                color: theme.colorScheme.primary,
                decoration: TextDecoration.underline)));
      }
      at = m.end;
    }
    if (at < widget.text.length) {
      spans.add(TextSpan(text: widget.text.substring(at)));
    }
    return Text.rich(
        TextSpan(style: theme.textTheme.bodyMedium, children: spans));
  }
}

Future<void> _open(String url) async {
  final uri = Uri.tryParse(url);
  if (uri == null || !(uri.scheme == 'https' || uri.scheme == 'http')) return;
  await launchUrl(uri, mode: LaunchMode.externalApplication);
}

class _DocsButton extends StatelessWidget {
  const _DocsButton({required this.url});
  final String url;
  @override
  Widget build(BuildContext context) => TextButton.icon(
        onPressed: () => _open(url),
        icon: const Icon(Icons.open_in_new),
        label: Text(_s(context, 'docs')),
      );
}

class _Message extends StatelessWidget {
  const _Message(
      {required this.icon,
      required this.text,
      this.loading = false,
      this.action});
  final IconData icon;
  final String text;
  final bool loading;
  final Widget? action;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.all(AnumSpacing.lg),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          if (loading)
            const CircularProgressIndicator()
          else
            Icon(icon, size: AnumSpacing.xl),
          const SizedBox(height: AnumSpacing.sm),
          Text(text, textAlign: TextAlign.center),
          if (action != null) action!,
        ]),
      );
}
