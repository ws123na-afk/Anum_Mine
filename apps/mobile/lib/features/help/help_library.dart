import 'package:flutter/services.dart';

/// In-app help articles (docs/help.md): the Markdown files of docs/help,
/// copied into `assets/help` (`node scripts/sync-help.mjs`) and bundled with
/// the app, so help works offline. Same format and rules as the web panel
/// (apps/web/src/lib/help.ts).
const helpDocsBaseUrl =
    'https://github.com/ws123na-afk/Anum_Mine/blob/main/docs/';
const helpAssetDir = 'assets/help/';

class HelpArticle {
  const HelpArticle({
    required this.id,
    required this.locale,
    required this.title,
    required this.summary,
    required this.keywords,
    required this.docsUrl,
    required this.body,
  });

  final String id;

  /// `en` or `ar`.
  final String locale;
  final String title;
  final String summary;
  final List<String> keywords;

  /// The full documentation page, as an absolute URL, or null.
  final String? docsUrl;

  /// The Markdown body without the frontmatter and the title heading.
  final String body;
}

final _fileName = RegExp(r'^([a-z0-9-]+)(?:\.(ar))?\.md$');
final _frontmatter = RegExp(r'^---\r?\n([\s\S]*?)\r?\n---\r?\n?');
final _safeRelative = RegExp(r'^[A-Za-z0-9_./#-]+$');

/// The docs URL for a link written relative to docs/help (`../tasks.md`), an
/// http(s) link as is, or null for anything else.
String? helpDocsHref(String target) {
  final value = target.trim();
  if (value.startsWith('https://') || value.startsWith('http://')) {
    return value;
  }
  final relative = value.replaceFirst(RegExp(r'^\.\./'), '');
  if (relative.isEmpty ||
      relative.startsWith('/') ||
      relative.contains('..') ||
      !_safeRelative.hasMatch(relative)) {
    return null;
  }
  return '$helpDocsBaseUrl$relative';
}

/// Reads one article; [fileName] is `<id>.md` or `<id>.ar.md`.
HelpArticle? parseHelpArticle(String fileName, String source) {
  final name = fileName.split('/').last;
  final file = _fileName.firstMatch(name);
  final front = _frontmatter.firstMatch(source);
  if (file == null || front == null) return null;
  final meta = <String, String>{};
  for (final line in front.group(1)!.split(RegExp(r'\r?\n'))) {
    final at = line.indexOf(':');
    if (at > 0) {
      meta[line.substring(0, at).trim()] = line.substring(at + 1).trim();
    }
  }
  final title = meta['title'];
  if (title == null || title.isEmpty || meta['id'] != file.group(1)) {
    return null;
  }
  final body = source
      .substring(front.end)
      .replaceFirst(RegExp(r'^\s*#\s+[^\n]*\n'), '')
      .trim()
      .replaceAllMapped(RegExp(r'\]\(([^)\s]+)\)'), (m) {
    final href = helpDocsHref(m.group(1)!);
    return href == null ? m.group(0)! : ']($href)';
  });
  final docs = meta['docs'];
  return HelpArticle(
    id: file.group(1)!,
    locale: file.group(2) == 'ar' ? 'ar' : 'en',
    title: title,
    summary: meta['summary'] ?? '',
    keywords: (meta['keywords'] ?? '')
        .split(',')
        .map((k) => k.trim())
        .where((k) => k.isNotEmpty)
        .toList(),
    docsUrl: docs == null ? null : helpDocsHref(docs),
    body: body,
  );
}

/// Every article in [locale], English where no translation exists, by title.
List<HelpArticle> helpArticlesFor(List<HelpArticle> all, String locale) {
  final byId = <String, HelpArticle>{
    for (final a in all.where((a) => a.locale == 'en')) a.id: a,
  };
  if (locale != 'en') {
    for (final a in all.where((a) => a.locale == locale)) {
      byId[a.id] = a;
    }
  }
  return byId.values.toList()..sort((a, b) => a.title.compareTo(b.title));
}

String _fold(String text) => text.toLowerCase().replaceAll(RegExp('[ً-ٟ]'), '');

/// Articles matching every word of [query]: title hits first, then keyword
/// and summary hits, then body hits. An empty query returns [articles].
List<HelpArticle> searchHelpArticles(List<HelpArticle> articles, String query) {
  final words =
      _fold(query).split(RegExp(r'\s+')).where((w) => w.isNotEmpty).toList();
  if (words.isEmpty) return List.of(articles);
  final scored = <(HelpArticle, int)>[];
  for (final article in articles) {
    final title = _fold(article.title);
    final tags = _fold('${article.keywords.join(' ')} ${article.summary}');
    final body = _fold(article.body);
    var score = 0;
    for (final word in words) {
      if (title.contains(word)) {
        score += 10;
      } else if (tags.contains(word)) {
        score += 5;
      } else if (body.contains(word)) {
        score += 1;
      } else {
        score = 0;
        break;
      }
    }
    if (score > 0) scored.add((article, score));
  }
  scored.sort((a, b) =>
      b.$2 != a.$2 ? b.$2.compareTo(a.$2) : a.$1.title.compareTo(b.$1.title));
  return [for (final s in scored) s.$1];
}

/// Loads every bundled article from the asset bundle.
Future<List<HelpArticle>> loadHelpArticles([AssetBundle? bundle]) async {
  final source = bundle ?? rootBundle;
  final manifest = await AssetManifest.loadFromAssetBundle(source);
  final paths = manifest
      .listAssets()
      .where((p) => p.startsWith(helpAssetDir) && p.endsWith('.md'));
  final articles = <HelpArticle>[];
  for (final path in paths) {
    final article = parseHelpArticle(path, await source.loadString(path));
    if (article != null) articles.add(article);
  }
  return articles;
}
