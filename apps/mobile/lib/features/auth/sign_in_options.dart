import '../../data/api_models.dart';

/// `GET /api/v1/auth/sign-in-options` (public): which sign-in buttons exist
/// (docs/identity.md#google-and-apple-sign-in). Keycloak still decides every
/// sign-in; these only choose what the screen offers.
class SignInOptions {
  const SignInOptions(
      {this.providers = const [],
      this.selfSignUp = false,
      this.privacyPolicyUrl});

  static const none = SignInOptions();
  static const _order = ['google', 'apple'];
  static const labels = {'google': 'Google', 'apple': 'Apple'};

  /// Keycloak identity provider aliases, Google first.
  final List<String> providers;
  final bool selfSignUp;
  final Uri? privacyPolicyUrl;

  factory SignInOptions.fromJson(JsonMap json) {
    final raw = json['identity_providers'];
    final ids = <String>{
      if (raw is List)
        for (final item in raw)
          if (item is Map && item['id'] is String) item['id'] as String,
    };
    final url = json['privacy_policy_url'];
    final uri = url is String ? Uri.tryParse(url.trim()) : null;
    return SignInOptions(
      providers: [
        for (final id in _order)
          if (ids.contains(id)) id
      ],
      selfSignUp: json['self_sign_up'] == true,
      privacyPolicyUrl: uri != null &&
              (uri.scheme == 'https' || uri.scheme == 'http') &&
              uri.host.isNotEmpty
          ? uri
          : null,
    );
  }

  /// The provider buttons to show. On iOS, App Store Review Guideline 4.8
  /// requires "Sign in with Apple" wherever another social sign-in is offered,
  /// so without Apple configured the Google button is not shown on iOS.
  List<String> visibleProviders({required bool isIOS}) =>
      isIOS && providers.contains('google') && !providers.contains('apple')
          ? [
              for (final id in providers)
                if (id != 'google') id
            ]
          : providers;

  /// "Create account" needs a way to create one and a policy to accept.
  bool canCreateAccount({required bool isIOS}) =>
      privacyPolicyUrl != null &&
      (selfSignUp || visibleProviders(isIOS: isIOS).isNotEmpty);
}
