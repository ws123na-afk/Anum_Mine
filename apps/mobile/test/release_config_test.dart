import 'package:anum_mobile/features/auth/oidc.dart';
import 'package:anum_mobile/src/anum_app.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('release builds refuse a non-https API address', () {
    expect(
        releaseApiUrlError('https://api.anum.invalid/', release: true), isNull);
    expect(releaseApiUrlError('http://10.0.2.2:8000/', release: true),
        contains('https://'));
    expect(releaseApiUrlError('not a url', release: true), isNotNull);
    expect(releaseApiUrlError('http://10.0.2.2:8000/', release: false), isNull);
  });

  test(
      'without defines a release build uses the production API, debug the '
      'development machine', () {
    expect(resolveApiUrl('', release: true), productionApiUrl);
    expect(productionApiUrl, 'https://api.anum360os.com/');
    expect(resolveApiUrl('  ', release: false), developmentApiUrl);
    expect(resolveApiUrl(' https://api.example.invalid/ ', release: true),
        'https://api.example.invalid/');
    // With or without the trailing slash the repository variable may carry.
    expect(resolveApiUrl('https://api.anum360os.com', release: true),
        productionApiUrl);
    expect(resolveApiUrl('https://api.example.invalid/base//', release: true),
        'https://api.example.invalid/base/');
  });

  test(
      'a release build always signs in through Keycloak, never the '
      'development sign-in', () {
    final release = OidcConfig.resolve('', release: true)!;
    expect(release.issuer, productionOidcIssuer);
    expect(productionOidcIssuer, 'https://auth.anum360os.com/realms/anum');
    expect(release.clientId, 'anum-flutter');
    expect(release.redirectUrl, 'com.anum.app:/oauth2redirect');
    // Debug and profile builds keep the local development sign-in.
    expect(OidcConfig.resolve('', release: false), isNull);
    expect(
        OidcConfig.resolve('https://id.example.invalid/realms/anum/',
                release: true)!
            .issuer,
        'https://id.example.invalid/realms/anum');
  });

  test('the production defaults pass the release configuration check', () {
    expect(
        releaseConfigError(
            apiUrl: resolveApiUrl('', release: true),
            oidc: OidcConfig.resolve('', release: true),
            release: true),
        isNull);
  });

  test('a release build refuses development hosts and a missing issuer', () {
    const issuer = OidcConfig(issuer: productionOidcIssuer);
    expect(
        releaseConfigError(
            apiUrl: 'https://localhost/', oidc: issuer, release: true),
        contains('development machine'));
    expect(
        releaseConfigError(apiUrl: productionApiUrl, oidc: null, release: true),
        contains('ANUM_OIDC_ISSUER'));
    expect(
        releaseConfigError(
            apiUrl: productionApiUrl,
            oidc: const OidcConfig(issuer: 'http://localhost:8080/realms/anum'),
            release: true),
        contains('https://'));
    // Debug builds may use all of them.
    expect(
        releaseConfigError(
            apiUrl: developmentApiUrl, oidc: null, release: false),
        isNull);
  });
}
