import '../../src/localization/anum_localizations.dart';
import 'autonomy_models.dart';

/// One editable daily-cap row: a category or tool name and a whole number.
class CapRow {
  CapRow([this.key = '', this.value = '']);
  String key, value;
}

/// The edits to quiet hours, daily caps and the blocked lists, shared by the
/// workspace settings and the organization guardrails (same fields, same
/// server rules). Pure data: the controllers own notifying listeners.
class PolicyDraft {
  bool quietOn = false;
  String quietStart = '22:00', quietEnd = '07:00', quietZone = 'UTC';
  List<CapRow> caps = [];
  List<String> recipients = [], domains = [];

  void load({
    QuietHours? quiet,
    Map<String, int> dailyCaps = const {},
    List<String> blockedRecipients = const [],
    List<String> blockedDomains = const [],
  }) {
    quietOn = quiet != null;
    if (quiet != null) {
      quietStart = quiet.start;
      quietEnd = quiet.end;
      quietZone = quiet.timezone;
    } else {
      quietStart = '22:00';
      quietEnd = '07:00';
      quietZone = 'UTC';
    }
    caps = [for (final e in dailyCaps.entries) CapRow(e.key, '${e.value}')];
    recipients = [...blockedRecipients];
    domains = [...blockedDomains];
  }

  void addCap() => caps = [...caps, CapRow()];

  void removeCap(CapRow row) => caps = [
        for (final c in caps)
          if (!identical(c, row)) c,
      ];

  /// Adds a blocked recipient; returns the problem (null when added).
  String? addRecipient(String raw, AnumLocalizations l) {
    final value = normalizeRecipient(raw);
    if (!plausibleRecipient(value)) return l.t('ap.err.recipient');
    return _add(recipients, value, l, (v) => recipients = v);
  }

  /// Adds a blocked domain; returns the problem (null when added).
  String? addDomain(String raw, AnumLocalizations l) {
    final value = normalizeDomain(raw);
    if (!plausibleDomain(value)) return l.t('ap.err.domain');
    return _add(domains, value, l, (v) => domains = v);
  }

  String? _add(List<String> list, String value, AnumLocalizations l,
      void Function(List<String>) set) {
    if (list.contains(value)) return l.f('ap.err.dup', {'item': value});
    if (list.length >= maxListItems) return l.t('ap.err.listFull');
    set([...list, value]);
    return null;
  }

  void removeRecipient(String value) => recipients = [
        for (final v in recipients)
          if (v != value) v,
      ];

  void removeDomain(String value) => domains = [
        for (final v in domains)
          if (v != value) v,
      ];

  QuietHours? get quiet => quietOn
      ? QuietHours(
          start: quietStart.trim(),
          end: quietEnd.trim(),
          timezone: quietZone.trim().isEmpty ? 'UTC' : quietZone.trim(),
        )
      : null;

  /// The caps as the server stores them (names lower-cased and trimmed).
  Map<String, int> get capsMap => {
        for (final c in caps)
          c.key.trim().toLowerCase(): int.tryParse(c.value.trim()) ?? 0,
      };

  /// The first problem the server would refuse, or null.
  String? validate(AnumLocalizations l) {
    if (quietOn) {
      if (!validHhmm(quietStart) || !validHhmm(quietEnd)) {
        return l.t('ap.err.quietTime');
      }
      if (quietZone.trim().isNotEmpty && !plausibleZone(quietZone)) {
        return l.t('ap.err.quietZone');
      }
    }
    if (caps.length > 100) return l.t('ap.err.capsMany');
    final seen = <String>{};
    for (final c in caps) {
      final key = c.key.trim().toLowerCase();
      if (key.isEmpty || key.length > 120) return l.t('ap.err.capKey');
      if (!seen.add(key)) return l.f('ap.err.capDup', {'key': key});
      final n = int.tryParse(c.value.trim());
      if (n == null || !validCap(n)) {
        return l.f('ap.err.capValue', {'key': key});
      }
    }
    return null;
  }
}
