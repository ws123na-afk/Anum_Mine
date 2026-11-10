/// Reads every page of a list the API pages by offset (`items` with `total`),
/// for the short lists a screen needs whole: a chooser, or approvals that
/// must not be hidden behind a first page. [page] fetches from an offset;
/// reading stops at the total, at an empty page, or at [maxItems].
Future<List<T>> readAllPages<T>(
    Future<({List<T> items, int total})> Function(int offset) page,
    {int maxItems = 2000}) async {
  final all = <T>[];
  while (all.length < maxItems) {
    final next = await page(all.length);
    all.addAll(next.items);
    if (next.items.isEmpty || all.length >= next.total) break;
  }
  return all;
}
