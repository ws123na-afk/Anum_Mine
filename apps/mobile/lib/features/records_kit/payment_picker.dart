import 'package:flutter/material.dart';

import '../../src/localization/money_format.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/depth.dart';
import 'records_kit.dart';
import 'records_widgets.dart';

/// One of the caller's own Finance transactions offered for linking.
class PaymentChoice {
  const PaymentChoice({
    required this.id,
    required this.postedOn,
    required this.merchant,
    required this.amount,
    required this.currency,
    required this.remaining,
    required this.alreadyLinked,
  });
  final String id, postedOn, merchant, amount, currency, remaining;
  final bool alreadyLinked;
}

/// The words of a [PaymentPickerSheet], in the module's language.
class PaymentPickerText {
  const PaymentPickerText({
    required this.title,
    required this.help,
    required this.search,
    required this.empty,
    required this.remaining,
    required this.already,
  });
  final String title, help, search, empty, already;

  /// With `{amount}`.
  final String remaining;
}

/// Picks one of the person's own Finance payments (or refunds) and links
/// it: linking adds no money, the payment already is the cost. One
/// idempotency key covers every attempt in this sheet. Pops `true` once
/// linked.
class PaymentPickerSheet extends StatefulWidget {
  const PaymentPickerSheet({
    required this.text,
    required this.load,
    required this.link,
    super.key,
  });
  final PaymentPickerText text;
  final Future<List<PaymentChoice>> Function(String query) load;
  final Future<void> Function(PaymentChoice choice, String idempotencyKey) link;

  @override
  State<PaymentPickerSheet> createState() => _PaymentPickerSheetState();
}

class _PaymentPickerSheetState extends State<PaymentPickerSheet> {
  RecordsPhase _phase = RecordsPhase.loading;
  RecordsFailure? _failure;
  List<PaymentChoice> _items = const [];
  String _query = '';
  String? _linking, _error;
  final _key = newIdempotencyKey('pay');

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _phase = RecordsPhase.loading);
    try {
      _items = await widget.load(_query);
      _phase = RecordsPhase.ready;
    } on Object catch (error) {
      _failure = RecordsFailure.from(error);
      _phase = _failure!.denied ? RecordsPhase.denied : RecordsPhase.error;
    }
    if (mounted) setState(() {});
  }

  Future<void> _pick(PaymentChoice choice) async {
    setState(() {
      _linking = choice.id;
      _error = null;
    });
    try {
      await widget.link(choice, _key);
      if (mounted) Navigator.of(context).pop(true);
    } on Object catch (error) {
      if (mounted) {
        setState(() {
          _linking = null;
          _error = RecordsFailure.from(error).message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = widget.text;
    final locale = Localizations.localeOf(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Semantics(
            header: true,
            child: Text(text.title,
                style: Theme.of(context).textTheme.titleLarge)),
        const SizedBox(height: AnumSpacing.xs),
        Text(text.help, style: TextStyle(color: context.palette.muted)),
        const SizedBox(height: AnumSpacing.sm),
        RecordsSearchField(
          initial: _query,
          hint: text.search,
          onSearch: (q) {
            _query = q.trim();
            _load();
          },
        ),
        const SizedBox(height: AnumSpacing.sm),
        if (_error != null)
          Text(_error!,
              key: const Key('link-error'),
              style: TextStyle(color: context.palette.stop)),
        if (_phase != RecordsPhase.ready)
          RecordsStateView(phase: _phase, failure: _failure, onRetry: _load)
        else if (_items.isEmpty)
          Text(text.empty, key: const Key('link-empty'))
        else
          for (final item in _items)
            AnumRow(
              key: Key('candidate-${item.id}'),
              icon: Icons.payments_outlined,
              title: '${item.merchant} · '
                  '${formatMoneyText(item.amount, item.currency, locale: locale)}',
              subtitle: recordsDate(context, item.postedOn),
              detail: item.alreadyLinked
                  ? text.already
                  : text.remaining.replaceAll(
                      '{amount}',
                      formatMoneyText(item.remaining, item.currency,
                          locale: locale)),
              trailing: _linking == item.id
                  ? const SizedBox.square(
                      dimension: 24, child: CircularProgressIndicator())
                  : const Icon(Icons.link),
              onTap: _linking == null ? () => _pick(item) : null,
            ),
      ],
    );
  }
}
