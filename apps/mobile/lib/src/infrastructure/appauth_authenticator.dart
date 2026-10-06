import 'package:flutter/foundation.dart';
import 'package:flutter_appauth/flutter_appauth.dart';

import '../../features/auth/oidc.dart';

/// [OidcAuthenticator] backed by AppAuth (flutter_appauth): the system browser
/// runs the Keycloak login, AppAuth adds PKCE (S256), state and nonce, and the
/// redirect returns through `com.anum.app:/oauth2redirect`.
class AppAuthOidcAuthenticator implements OidcAuthenticator {
  AppAuthOidcAuthenticator(this.config, {FlutterAppAuth? appAuth})
      : _appAuth = appAuth ?? const FlutterAppAuth();

  @override
  final OidcConfig config;
  final FlutterAppAuth _appAuth;

  /// Plain-HTTP issuers (a local Keycloak) are allowed only in debug and
  /// profile builds; release builds require HTTPS.
  bool get _allowInsecure =>
      !kReleaseMode && Uri.parse(config.issuer).scheme == 'http';

  @override
  Future<OidcTokens> signIn() async {
    try {
      final response =
          await _appAuth.authorizeAndExchangeCode(AuthorizationTokenRequest(
        config.clientId,
        config.redirectUrl,
        issuer: config.issuer,
        scopes: config.scopes,
        allowInsecureConnections: _allowInsecure,
      ));
      return _tokens(response);
    } on FlutterAppAuthUserCancelledException {
      throw const OidcCancelled();
    }
  }

  @override
  Future<OidcTokens> refresh(String refreshToken) async {
    try {
      final response = await _appAuth.token(TokenRequest(
        config.clientId,
        config.redirectUrl,
        issuer: config.issuer,
        refreshToken: refreshToken,
        grantType: GrantType.refreshToken,
        scopes: config.scopes,
        allowInsecureConnections: _allowInsecure,
      ));
      return _tokens(response);
    } on FlutterAppAuthPlatformException catch (error) {
      if (error.platformErrorDetails.error ==
          FlutterAppAuthOAuthError.invalidGrant) {
        throw const OidcRefreshRejected();
      }
      rethrow;
    }
  }

  @override
  Future<void> endSession({String? idToken}) async {
    try {
      await _appAuth.endSession(EndSessionRequest(
        idTokenHint: idToken,
        postLogoutRedirectUrl: config.redirectUrl,
        issuer: config.issuer,
        allowInsecureConnections: _allowInsecure,
      ));
    } on FlutterAppAuthUserCancelledException {
      throw const OidcCancelled();
    }
  }

  OidcTokens _tokens(TokenResponse response) {
    final accessToken = response.accessToken;
    if (accessToken == null || accessToken.isEmpty) {
      throw StateError('The identity provider returned no access token');
    }
    final exp = decodeJwtClaims(accessToken)['exp'];
    return OidcTokens(
      accessToken: accessToken,
      refreshToken: response.refreshToken,
      idToken: response.idToken,
      expiresAt: response.accessTokenExpirationDateTime?.toUtc() ??
          (exp is int
              ? DateTime.fromMillisecondsSinceEpoch(exp * 1000, isUtc: true)
              : DateTime.now().toUtc().add(const Duration(minutes: 5))),
    );
  }
}
