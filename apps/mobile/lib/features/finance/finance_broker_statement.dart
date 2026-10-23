import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import '../../data/api_client.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/anum_components.dart';

/// Read-only statement validation and reconciliation. It never stores a
/// brokerage password, sends an order or initiates a deposit/withdrawal.
class FinanceBrokerStatement extends StatefulWidget {
  const FinanceBrokerStatement({required this.api, super.key});
  final AnumApiClient api;

  @override
  State<FinanceBrokerStatement> createState() => _FinanceBrokerStatementState();
}

class _FinanceBrokerStatementState extends State<FinanceBrokerStatement> {
  final _earlier = TextEditingController();
  final _latest = TextEditingController();
  Map<String, dynamic>? _baseline;
  Map<String, dynamic>? _observed;
  Map<String, dynamic>? _comparison;
  String? _error;
  bool _busy = false, _expanded = false;
  int _version = 0;

  @override
  void dispose() {
    _earlier.dispose();
    _latest.dispose();
    super.dispose();
  }

  void _clearResult() {
    _version++;
    setState(() {
      _baseline = null;
      _observed = null;
      _comparison = null;
      _error = null;
    });
  }

  Future<void> _upload(bool baseline) async {
    try {
      final file = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: const ['csv'],
        withData: true,
      );
      if (!mounted || file == null || file.files.isEmpty) return;
      final bytes = file.files.single.bytes;
      if (bytes == null || bytes.length > 512000) {
        setState(() => _error = 'Choose a UTF-8 CSV statement smaller than 500 KB.');
        return;
      }
      final data = utf8.decode(bytes);
      (baseline ? _earlier : _latest).text = data;
      _clearResult();
    } on FormatException {
      if (mounted) setState(() => _error = 'The CSV file must use UTF-8.');
    } catch (_) {
      if (mounted) setState(() => _error = 'Could not open the statement file.');
    }
  }

  Future<void> _validate(bool baseline) async {
    final content = (baseline ? _earlier : _latest).text;
    if (content.trim().isEmpty) return;
    final version = ++_version;
    setState(() { _busy = true; _error = null; _comparison = null; });
    try {
      final result = await widget.api.request(
        'POST', '/api/v1/finance/broker/statement-preview',
        body: {'statement': content},
      );
      if (!mounted || version != _version) return;
      setState(() {
        if (baseline) {
          _baseline = Map<String, dynamic>.from(result);
        } else {
          _observed = Map<String, dynamic>.from(result);
        }
      });
    } on ApiException catch (error) {
      if (mounted && version == _version) {
        setState(() => _error = error.readable);
      }
    } catch (_) {
      if (mounted && version == _version) {
        setState(() => _error = 'Could not validate this statement.');
      }
    } finally {
      if (mounted && version == _version) setState(() => _busy = false);
    }
  }

  Future<void> _compare() async {
    if (_baseline == null || _observed == null) return;
    final version = ++_version;
    setState(() { _busy = true; _error = null; _comparison = null; });
    try {
      final result = await widget.api.request(
        'POST', '/api/v1/finance/broker/reconcile',
        body: {'baseline': _baseline, 'observed': _observed},
      );
      if (mounted && version == _version) {
        setState(() => _comparison = Map<String, dynamic>.from(result));
      }
    } on ApiException catch (error) {
      if (mounted && version == _version) {
        setState(() => _error = error.readable);
      }
    } catch (_) {
      if (mounted && version == _version) {
        setState(() => _error = 'Could not compare the broker statements.');
      }
    } finally {
      if (mounted && version == _version) setState(() => _busy = false);
    }
  }

  Widget _statement({
    required bool baseline,
    required TextEditingController controller,
    required Map<String, dynamic>? result,
  }) {
    final label = baseline ? 'Earlier statement' : 'Latest statement';
    final cash = result?['cash'] as List<dynamic>? ?? const [];
    final positions = result?['positions'] as List<dynamic>? ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(label, style: Theme.of(context).textTheme.titleMedium),
        TextButton.icon(
          onPressed: _busy ? null : () => _upload(baseline),
          icon: const Icon(Icons.upload_file_outlined),
          label: const Text('Import CSV file'),
        ),
        TextField(
          controller: controller,
          minLines: 2,
          maxLines: 5,
          maxLength: 512000,
          onChanged: (_) => _clearResult(),
          decoration: const InputDecoration(
            labelText: 'Or paste CSV statement',
            border: OutlineInputBorder(),
          ),
        ),
        OutlinedButton(
          onPressed: _busy || controller.text.trim().isEmpty
              ? null : () => _validate(baseline),
          child: Text('Validate $label'),
        ),
        if (result != null) ...[
          Text('Account: ${result['account_id'] ?? ''}'),
          Text('Broker: ${result['broker_id'] ?? ''}'),
          Text('As of: ${result['as_of'] ?? ''}'),
          Text('${cash.length} currencies, ${positions.length} positions'),
          for (final raw in cash)
            if (raw is Map)
              Text('${raw['currency']}: settled ${raw['settled']}, '
                  'available ${raw['available']}'),
        ],
      ],
    );
  }

  Widget _results() {
    final result = _comparison!;
    final differences = result['discrepancies'] as List<dynamic>? ?? const [];
    final exposure = result['currency_exposure'] as Map? ?? const {};
    final missing = result['unpriced_positions'] as List<dynamic>? ?? const [];
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(result['matches'] == true
          ? 'Balances and quantities match' : '${differences.length} differences found',
          style: Theme.of(context).textTheme.titleMedium),
      if (result['stale'] == true)
        const Text('Statement is stale. Do not use this as current market data.'),
      if (missing.isNotEmpty) Text('Missing prices: ${missing.join(', ')}'),
      for (final row in differences)
        if (row is Map)
          ListTile(
            dense: true,
            title: Text('${row['kind']}: ${row['key']}'),
            subtitle: Text('Earlier ${row['expected'] ?? 'missing'}; '
                'latest ${row['actual'] ?? 'missing'} '
                '(${row['currency'] ?? ''})'),
          ),
      if (differences.isEmpty) const Text('No quantity or cash differences detected.'),
      const Text('Recorded position values by currency:'),
      for (final entry in exposure.entries) Text('${entry.key}: ${entry.value}'),
      const Text('These values come from uploaded statements, not a live broker. '
          'This module cannot execute trades or transfer money.'),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    return AnumSurface(child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text('Broker statement review', style: TextStyle(fontWeight: FontWeight.w700)),
        const Text('Read-only. Money stays with your broker. No live trades.'),
        TextButton(
          onPressed: () => setState(() => _expanded = !_expanded),
          child: Text(_expanded ? 'Close broker review' : 'Open broker review'),
        ),
        if (_expanded) ...[
          const Text('CSV columns: row_type, broker_id, account_id, as_of, '
              'source_reference, currency, symbol, side, quantity, last_price, '
              'settled, available. Do not upload passwords or broker API keys.'),
          _statement(baseline: true, controller: _earlier, result: _baseline),
          const SizedBox(height: AnumSpacing.md),
          _statement(baseline: false, controller: _latest, result: _observed),
          const SizedBox(height: AnumSpacing.md),
          FilledButton(
            onPressed: _busy || _baseline == null || _observed == null
                ? null : _compare,
            child: Text(_busy ? 'Working...' : 'Compare statements'),
          ),
          TextButton(onPressed: _busy ? null : () {
            _earlier.clear();
            _latest.clear();
            _clearResult();
          }, child: const Text('Clear statements')),
          if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          if (_comparison != null) _results(),
        ],
      ],
    ));
  }
}
