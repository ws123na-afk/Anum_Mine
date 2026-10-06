import 'dart:async';
import 'dart:convert';

import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../../data/session_store.dart';

/// Keycloak sign-in settings, supplied at build time:
///
/// ```sh
/// flutter run --dart-define=ANUM_OIDC_ISSUER=http://localhost:8080/realms/anum
/// ```
///
/// Without `ANUM_OIDC_ISSUER` the app keeps the local development sign-in.
class OidcConfig {
  const OidcConfig({
    required this.issuer,
    this.clientId = 'anum-flutter',
    this.redirectUrl = 'com.anum.app:/oauth2redirect',
    this.scopes = const ['openid', 'profile', 'email'],
    this.defaultWorkspaceId = '',
  });

  final String issuer;
  final String clientId;

  /// Registered for `anum-flutter` in infra/keycloak/anum-realm.json and in the
  /// native projects by tool/configure_native.dart.
  final String redirectUrl;
  final List<String> scopes;

  /// Sent as `x-workspace-id` when the token has no `workspace_id` claim.
  final String defaultWorkspaceId;

  static OidcConfig? fromEnvironment() {
    const issuer = String.fromEnvironment('ANUM_OIDC_ISSUER');
    if (issuer.trim().isEmpty) return null;
    return OidcConfig(
      issuer: issuer.trim().replaceFirst(RegExp(r'/+$'), ''),
      clientId: const String.fromEnvironment('ANUM_OIDC_CLIENT_ID',
          defaultValue: 'anum-flutter'),
      redirectUrl: const String.fromEnvironment('ANUM_OIDC_REDIRECT_URL',
          defaultValue: 'com.anum.app:/oauth2redirect'),
      defaultWorkspaceId: const String.fromEnvironment('ANUM_WORKSPACE_ID'),
    );
  }
}

class OidcTokens {
  const OidcTokens({
    required this.accessToken,
    required this.expiresAt,
    this.refreshToken,
    this.idToken,
  });

  final String accessToken;
  final DateTime expiresAt;
  final String? refreshToken;
  final String? idToken;
}

/// The person closed the browser before finishing sign-in or sign-out.
class OidcCancelled implements Exception {
  const OidcCancelled();
  @override
  String toString() => 'Sign-in was cancelled';
}

/// The provider rejected the refresh token (`invalid_grant`): the session is over.
class OidcRefreshRejected implements Exception {
  const OidcRefreshRejected();
  @override
  String toString() => 'The identity provider ended this session';
}

/// The platform half of sign-in (authorization code + PKCE in the system
/// browser, refresh, RP-initiated logout). The app uses flutter_appauth; tests
/// use fakes.
abstract interface class OidcAuthenticator {
  OidcConfig get config;
  Future<OidcTokens> signIn();
  Future<OidcTokens> refresh(String refreshToken);
  Future<void> endSession({String? idToken});
}

/// Reads a JWT payload without verifying it. The API verifies every token; the
/// client only needs the claims to pick the tenant and workspace.
JsonMap decodeJwtClaims(String token) {
  final parts = token.split('.');
  if (parts.length != 3) return const {};
  try {
    final decoded = jsonDecode(
        utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))));
    return decoded is Map<String, Object?> ? decoded : const {};
  } on FormatException {
    return const {};
  }
}

const _anumRoles = {'owner', 'member', 'viewer'};

/// Turns provider tokens into the session the API client uses. The tenant is
/// the token's `tenant_id`; the workspace is the one already selected, else the
/// token's `workspace_id`, else [OidcConfig.defaultWorkspaceId].
LocalSession sessionFromOidcTokens(
  OidcTokens tokens, {
  LocalSession? previous,
  String defaultWorkspaceId = '',
}) {
  final claims = decodeJwtClaims(tokens.accessToken);
  final tenant = claims['tenant_id'];
  if (tenant is! String || tenant.isEmpty) {
    throw const ApiException(
        403, 'Your account has no ANUM organization. Ask an administrator.');
  }
  final realmAccess = claims['realm_access'];
  final realmRoles = realmAccess is Map ? realmAccess['roles'] : null;
  final roles = <String>{
    for (final role in [
      if (realmRoles is List) ...realmRoles,
      if (claims['roles'] is List) ...claims['roles']! as List,
    ])
      if (role is String && _anumRoles.contains(role.toLowerCase()))
        role.toLowerCase(),
  }.toList();
  final claimWorkspace = claims['workspace_id'];
  final previousWorkspace =
      previous?.isOidc == true && previous!.context.tenantId == tenant
          ? previous.context.workspaceId
          : '';
  final workspace = previousWorkspace.isNotEmpty
      ? previousWorkspace
      : claimWorkspace is String && claimWorkspace.isNotEmpty
          ? claimWorkspace
          : defaultWorkspaceId;
  return LocalSession(
    accessToken: tokens.accessToken,
    tokenType: 'Bearer',
    expiresAt: tokens.expiresAt.toUtc(),
    source: SessionSource.oidc,
    // Keycloak may not rotate the refresh token or resend the ID token.
    refreshToken: tokens.refreshToken ?? previous?.refreshToken,
    idToken: tokens.idToken ?? previous?.idToken,
    context: TenantContext(
      tenantId: tenant,
      workspaceId: workspace,
      userId: claims['sub'] is String ? claims['sub']! as String : '',
      roles: roles,
    ),
  );
}

/// Session store that keeps OIDC sessions fresh: a read within [refreshSkew]
/// of expiry refreshes the tokens first, so every API call, file transfer and
/// audit export sends a valid access token. Local sessions pass through.
class OidcSessionStore implements SessionStore {
  OidcSessionStore({
    required this.inner,
    required this.authenticator,
    this.refreshSkew = const Duration(seconds: 60),
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  final SessionStore inner;
  final OidcAuthenticator authenticator;
  final Duration refreshSkew;
  final DateTime Function() _clock;
  Future<LocalSession?>? _refreshing;

  bool _needsRefresh(LocalSession session) =>
      !session.expiresAt.subtract(refreshSkew).isAfter(_clock().toUtc());

  @override
  Future<LocalSession?> read() async {
    final session = await inner.read();
    if (session == null ||
        !session.isOidc ||
        session.refreshToken == null ||
        !_needsRefresh(session)) {
      return session;
    }
    return _refreshing ??= _refresh(session).whenComplete(() {
      _refreshing = null;
    });
  }

  Future<LocalSession?> _refresh(LocalSession session) async {
    try {
      final tokens = await authenticator.refresh(session.refreshToken!);
      final next = sessionFromOidcTokens(tokens,
          previous: session,
          defaultWorkspaceId: authenticator.config.defaultWorkspaceId);
      await inner.write(next);
      return next;
    } on OidcRefreshRejected {
      await inner.clear();
      return null;
    }
    // Other failures (offline, provider down) propagate and keep the stored
    // refresh token so the next attempt can succeed.
  }

  @override
  Future<void> write(LocalSession session) => inner.write(session);

  @override
  Future<void> clear() => inner.clear();
}
