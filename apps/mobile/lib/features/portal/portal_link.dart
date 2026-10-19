/// Reads the link a business sent a customer (docs/business-portal.md#access-model).
///
/// The link is `https://host/portal#access=TOKEN`; the token is
/// `anum_portal.<tenant>.<workspace>.<access>.<secret>`. Either the whole link
/// or the bare token may be pasted. Only the token is taken from it: the host
/// in a pasted link is never used, so the token is only ever sent to the API
/// this app is configured for. The token is a credential: it is never logged
/// or stored.
sealed class PortalLinkResult {
  const PortalLinkResult();
}

class PortalLinkOk extends PortalLinkResult {
  const PortalLinkOk(this.token);
  final String token;

  // The credential never appears in a log or a crash report.
  @override
  String toString() => 'PortalLinkOk(<hidden>)';
}

/// Why the text is not a portal link: a localization key suffix
/// (`portal.link.empty`, `.noToken`, `.malformed`).
class PortalLinkInvalid extends PortalLinkResult {
  const PortalLinkInvalid(this.reason);
  final String reason;
}

final _tokenShape = RegExp(
    r'^anum_portal\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{1,80}\.[A-Za-z0-9_-]{20,100}$');

PortalLinkResult parsePortalLink(String input) {
  // A pasted link often carries quotes, brackets or line breaks.
  final text = input.trim().replaceAll(RegExp(r'''^[<"'\s]+|[>"'\s]+$'''), '');
  if (text.isEmpty) return const PortalLinkInvalid('empty');

  String? candidate;
  if (text.contains('#') || text.contains('://') || text.contains('/')) {
    final hash = text.indexOf('#');
    final fragment = hash >= 0 ? text.substring(hash + 1) : '';
    candidate = _accessValue(fragment);
    if (candidate == null) {
      // A link without its fragment (a mail client cut it off), or a link to
      // something else.
      return const PortalLinkInvalid('noToken');
    }
  } else if (text.startsWith('access=')) {
    candidate = _accessValue(text);
  } else {
    candidate = text;
  }
  if (candidate == null) return const PortalLinkInvalid('noToken');
  try {
    candidate = Uri.decodeComponent(candidate);
  } on ArgumentError {
    // Uri.decodeComponent reports a bad %-escape as an ArgumentError.
    return const PortalLinkInvalid('malformed');
  } on FormatException {
    return const PortalLinkInvalid('malformed');
  }
  candidate = candidate.trim();
  if (!_tokenShape.hasMatch(candidate)) {
    return const PortalLinkInvalid('malformed');
  }
  return PortalLinkOk(candidate);
}

String? _accessValue(String fragment) {
  for (final part in fragment.split('&')) {
    if (part.startsWith('access=')) {
      final value = part.substring('access='.length);
      return value.isEmpty ? null : value;
    }
  }
  return null;
}
