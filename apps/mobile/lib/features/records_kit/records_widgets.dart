import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/depth.dart';
import 'records_kit.dart';

/// English and Arabic strings shared by the record modules' screens.
class KitStrings {
  const KitStrings(this.arabic);
  final bool arabic;

  static KitStrings of(BuildContext context) =>
      KitStrings(AnumLocalizations.maybeOf(context).isArabic);

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? _ar : _en)[key] ?? _en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  static const _en = <String, String>{
    'loading': 'Loading…',
    'retry': 'Try again',
    'loadMore': 'Load more',
    'loadingMore': 'Loading more…',
    'moreFailed': 'Could not load more: {message}',
    'errorTitle': 'This did not load',
    'errorHint':
        '{message} Check the connection and try again. If it keeps failing, the server may be down.',
    'offlineTitle': 'No connection',
    'deniedTitle': 'You cannot open this',
    'search': 'Search',
    'clearSearch': 'Clear search',
    'noMatch': 'Nothing matches',
    'noMatchText': 'Try other words or clear the filter.',
    'clearFilters': 'Clear filters',
    'cancel': 'Cancel',
    'save': 'Save',
    'saving': 'Saving…',
    'delete': 'Delete',
    'close': 'Close',
    'saved': 'Saved',
    'none': '-',
    'shownOf': '{shown} of {total}',
    'required': 'Required',
    'invalidNumber': 'Enter a number such as 12.5',
    'invalidDate': 'Enter a date as YYYY-MM-DD',
    'pickDate': 'Pick a date',
    'conflict':
        'Someone changed this while you were editing. It was reloaded: check it and save again.',
  };

  static const _ar = <String, String>{
    'loading': 'جارٍ التحميل…',
    'retry': 'أعد المحاولة',
    'loadMore': 'تحميل المزيد',
    'loadingMore': 'جارٍ تحميل المزيد…',
    'moreFailed': 'تعذر تحميل المزيد: {message}',
    'errorTitle': 'تعذر التحميل',
    'errorHint':
        '{message} تحقق من الاتصال ثم أعد المحاولة. إذا استمر الخطأ فقد يكون الخادم متوقفًا.',
    'offlineTitle': 'لا يوجد اتصال',
    'deniedTitle': 'لا يمكنك فتح هذه الصفحة',
    'search': 'بحث',
    'clearSearch': 'مسح البحث',
    'noMatch': 'لا توجد نتائج مطابقة',
    'noMatchText': 'جرّب كلمات أخرى أو امسح عامل التصفية.',
    'clearFilters': 'مسح عوامل التصفية',
    'cancel': 'إلغاء',
    'save': 'حفظ',
    'saving': 'جارٍ الحفظ…',
    'delete': 'حذف',
    'close': 'إغلاق',
    'saved': 'تم الحفظ',
    'none': '-',
    'shownOf': '{shown} من {total}',
    'required': 'مطلوب',
    'invalidNumber': 'أدخل رقمًا مثل 12.5',
    'invalidDate': 'أدخل التاريخ بالشكل YYYY-MM-DD',
    'pickDate': 'اختر تاريخًا',
    'conflict':
        'غيّر شخص آخر هذا السجل أثناء تعديلك. أعيد تحميله: راجعه ثم احفظ مرة أخرى.',
  };

  static bool get catalogsMatch =>
      _en.keys.toSet().difference(_ar.keys.toSet()).isEmpty &&
      _ar.keys.toSet().difference(_en.keys.toSet()).isEmpty;
}

/// The body of a list or page that is not ready: loading, permission
/// denied (the API's sentence says who may do it, [deniedHint] adds the
/// module's rule), or failed (what failed and how to fix it, with Retry).
class RecordsStateView extends StatelessWidget {
  const RecordsStateView({
    required this.phase,
    required this.onRetry,
    this.failure,
    this.deniedHint,
    super.key,
  });

  final RecordsPhase phase;
  final RecordsFailure? failure;
  final String? deniedHint;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    switch (phase) {
      case RecordsPhase.loading:
        return AnumFeedback(
            kind: AnumFeedbackKind.loading, message: k.t('loading'));
      case RecordsPhase.denied:
        return AnumEmpty(
          key: const Key('records-denied'),
          icon: Icons.lock_outline,
          title: k.t('deniedTitle'),
          message: [failure?.message, deniedHint]
              .whereType<String>()
              .where((text) => text.isNotEmpty)
              .join('\n\n'),
        );
      case RecordsPhase.error:
        final offline = failure?.offline ?? false;
        return AnumEmpty(
          key: const Key('records-error'),
          icon: offline ? Icons.cloud_off_outlined : Icons.error_outline,
          title: k.t(offline ? 'offlineTitle' : 'errorTitle'),
          message: k.t('errorHint', {'message': failure?.message ?? ''}).trim(),
          action: k.t('retry'),
          onAction: onRetry,
        );
      case RecordsPhase.ready:
        return const SizedBox.shrink();
    }
  }
}

/// "Load more" under a paged list, with its own error line.
class RecordsMore extends StatelessWidget {
  const RecordsMore({required this.list, super.key});
  final RecordsList<Object?> list;

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (list.moreError != null)
          Padding(
            padding: const EdgeInsets.only(bottom: AnumSpacing.xs),
            child: Text(k.t('moreFailed', {'message': list.moreError!}),
                style: TextStyle(color: context.palette.stop)),
          ),
        if (list.hasMore)
          Align(
            alignment: AlignmentDirectional.center,
            child: OutlinedButton.icon(
              key: const Key('records-more'),
              onPressed: list.loadingMore ? null : list.more,
              icon: const Icon(Icons.expand_more),
              label: Text(k.t(list.loadingMore ? 'loadingMore' : 'loadMore')),
            ),
          ),
        if (list.total != null && list.items.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: AnumSpacing.xs),
            child: Text(
              k.t('shownOf',
                  {'shown': list.items.length, 'total': list.total!}),
              textAlign: TextAlign.center,
              style: TextStyle(color: context.palette.muted),
            ),
          ),
      ],
    );
  }
}

/// Room under the last item of a page with a floating button, so the
/// button never covers "Load more" or the last row.
class RecordsFabSpace extends StatelessWidget {
  const RecordsFabSpace({super.key});
  @override
  Widget build(BuildContext context) => const SizedBox(height: 72);
}

/// A search field that runs [onSearch] when the person submits.
class RecordsSearchField extends StatefulWidget {
  const RecordsSearchField(
      {required this.initial, required this.onSearch, this.hint, super.key});
  final String initial;
  final String? hint;
  final ValueChanged<String> onSearch;

  @override
  State<RecordsSearchField> createState() => _RecordsSearchFieldState();
}

class _RecordsSearchFieldState extends State<RecordsSearchField> {
  late final _text = TextEditingController(text: widget.initial);

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    return TextField(
      key: const Key('records-search'),
      controller: _text,
      textInputAction: TextInputAction.search,
      onSubmitted: widget.onSearch,
      decoration: InputDecoration(
        labelText: widget.hint ?? k.t('search'),
        prefixIcon: const Icon(Icons.search),
        suffixIcon: ValueListenableBuilder(
          valueListenable: _text,
          builder: (_, value, __) => value.text.isEmpty
              ? const SizedBox.shrink()
              : IconButton(
                  tooltip: k.t('clearSearch'),
                  icon: const Icon(Icons.clear),
                  onPressed: () {
                    _text.clear();
                    widget.onSearch('');
                  },
                ),
        ),
      ),
    );
  }
}

/// A horizontally scrolling row of choice chips for one filter.
class RecordsFilterChips extends StatelessWidget {
  const RecordsFilterChips({
    required this.options,
    required this.selected,
    required this.onSelected,
    super.key,
  });

  /// Value to label, in order; the empty value means "all".
  final Map<String, String> options;
  final String selected;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: AnumSpacing.xs,
        runSpacing: AnumSpacing.xs,
        children: [
          for (final entry in options.entries)
            ChoiceChip(
              key: Key('filter-${entry.key.isEmpty ? 'all' : entry.key}'),
              label: Text(entry.value),
              selected: selected == entry.key,
              onSelected: (_) => onSelected(entry.key),
            ),
        ],
      );
}

/// A label and its value, wrapping at large text.
class RecordsField extends StatelessWidget {
  const RecordsField(this.label, this.value, {super.key});
  final String label;
  final String? value;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xxs),
      child: MergeSemantics(
        child: Wrap(
          spacing: AnumSpacing.xs,
          children: [
            Text(label,
                style: TextStyle(color: p.muted, fontSize: AnumType.sm)),
            Text(
                value == null || value!.trim().isEmpty
                    ? KitStrings.of(context).t('none')
                    : value!,
                style: TextStyle(color: p.text, fontSize: AnumType.sm)),
          ],
        ),
      ),
    );
  }
}

/// A titled section card used on detail pages.
class RecordsSection extends StatelessWidget {
  const RecordsSection({
    required this.title,
    required this.children,
    this.trailing,
    super.key,
  });
  final String title;
  final List<Widget> children;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => AnumSurface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(children: [
              Expanded(
                child: Semantics(
                  header: true,
                  child: Text(title,
                      style: Theme.of(context).textTheme.titleMedium),
                ),
              ),
              if (trailing != null) trailing!,
            ]),
            const SizedBox(height: AnumSpacing.xs),
            ...children,
          ],
        ),
      );
}

/// Shows [message] in a snack bar when there is a messenger; the newest
/// message replaces one still showing instead of queueing behind it.
void recordsToast(BuildContext context, String message) =>
    ScaffoldMessenger.maybeOf(context)
      ?..removeCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));

/// Opens a scrollable form in a bottom sheet that rises with the keyboard
/// and keeps a readable width on tablets.
Future<T?> showRecordsSheet<T>(
        BuildContext context, Widget Function(BuildContext) builder) =>
    showModalBottomSheet<T>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      constraints: const BoxConstraints(maxWidth: 640),
      builder: (sheet) => Padding(
        padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(sheet).bottom),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(
              AnumSpacing.md, 0, AnumSpacing.md, AnumSpacing.lg),
          child: builder(sheet),
        ),
      ),
    );

/// A date field: typed as YYYY-MM-DD or picked from the calendar.
class RecordsDateField extends StatelessWidget {
  const RecordsDateField({
    required this.controller,
    required this.label,
    this.required = false,
    this.fieldKey,
    super.key,
  });
  final TextEditingController controller;
  final String label;
  final bool required;
  final Key? fieldKey;

  @override
  Widget build(BuildContext context) {
    final k = KitStrings.of(context);
    return TextFormField(
      key: fieldKey,
      controller: controller,
      keyboardType: TextInputType.datetime,
      decoration: InputDecoration(
        labelText: label,
        hintText: 'YYYY-MM-DD',
        suffixIcon: IconButton(
          tooltip: k.t('pickDate'),
          icon: const Icon(Icons.calendar_today_outlined),
          onPressed: () async {
            final initial = parseDay(controller.text) ?? DateTime.now();
            final picked = await showDatePicker(
              context: context,
              initialDate: initial,
              firstDate: DateTime(2000),
              lastDate: DateTime(2100),
            );
            if (picked != null) controller.text = isoDay(picked);
          },
        ),
      ),
      validator: (value) {
        final text = value?.trim() ?? '';
        if (text.isEmpty) return required ? k.t('required') : null;
        return parseDay(text) == null ||
                !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(text)
            ? k.t('invalidDate')
            : null;
      },
    );
  }
}

/// A short date for a `YYYY-MM-DD` or ISO value in the app's language.
String recordsDate(BuildContext context, String? value) {
  if (value == null || value.isEmpty) return '-';
  final parsed = DateTime.tryParse(value);
  if (parsed == null) return value;
  final local = value.length <= 10 ? parsed : parsed.toLocal();
  return MaterialLocalizations.of(context).formatMediumDate(local);
}

/// A date and time for an ISO value in the app's language.
String recordsDateTime(BuildContext context, String? value) {
  if (value == null || value.isEmpty) return '-';
  final parsed = DateTime.tryParse(value);
  if (parsed == null) return value;
  final local = parsed.toLocal();
  final m = MaterialLocalizations.of(context);
  return '${m.formatMediumDate(local)} ${m.formatTimeOfDay(TimeOfDay.fromDateTime(local))}';
}

/// The page width the module screens lay out against, and whether there is
/// room for a list beside its detail.
bool recordsWide(BuildContext context) =>
    MediaQuery.sizeOf(context).width >= AnumBreakpoints.expanded;

/// A module's English and Arabic catalog with `{name}` placeholders.
class ModuleStrings {
  const ModuleStrings(this.arabic, this.en, this.ar);
  final bool arabic;
  final Map<String, String> en, ar;

  String t(String key, [Map<String, Object> values = const {}]) {
    var text = (arabic ? ar : en)[key] ?? en[key] ?? key;
    values
        .forEach((name, value) => text = text.replaceAll('{$name}', '$value'));
    return text;
  }

  /// A label for an API value (`fuel.petrol_95`), or the value itself.
  String label(String prefix, String value) {
    final key = '$prefix.$value';
    return (arabic ? ar : en)[key] ?? en[key] ?? value.replaceAll('_', ' ');
  }

  static bool match(Map<String, String> en, Map<String, String> ar) =>
      en.keys.toSet().difference(ar.keys.toSet()).isEmpty &&
      ar.keys.toSet().difference(en.keys.toSet()).isEmpty;
}
