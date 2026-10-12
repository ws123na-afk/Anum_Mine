import '../../data/api_models.dart';
import '../auth/oidc.dart';

/// Who is signed in, for the header and Profile: the name and email from the
/// Keycloak ID token (display only; the API verifies every token), else the
/// user id of a local development session.
class AccountIdentity {
  const AccountIdentity({
    required this.userId,
    required this.displayName,
    this.email,
    this.roles = const [],
    this.workspaceId = '',
  });

  final String userId, displayName, workspaceId;
  final String? email;
  final List<String> roles;

  /// The highest workspace role in the session: owner, member or viewer.
  String? get role => const ['owner', 'member', 'viewer']
      .where(roles.map((r) => r.toLowerCase()).contains)
      .firstOrNull;

  factory AccountIdentity.fromSession(LocalSession session) {
    final claims = <String, Object?>{
      ...decodeJwtClaims(session.accessToken),
      if (session.idToken != null) ...decodeJwtClaims(session.idToken!),
    };
    String? claim(String name) => switch (claims[name]) {
          final String value when value.trim().isNotEmpty => value.trim(),
          _ => null,
        };
    final given = claim('given_name');
    final family = claim('family_name');
    final joined = [given, family].whereType<String>().join(' ');
    final name = claim('name') ??
        (joined.isEmpty ? null : joined) ??
        claim('preferred_username') ??
        session.context.userId;
    return AccountIdentity(
      userId: session.context.userId,
      displayName: name,
      email: claim('email'),
      roles: session.context.roles,
      workspaceId: session.context.workspaceId,
    );
  }
}
