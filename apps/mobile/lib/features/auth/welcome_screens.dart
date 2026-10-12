import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/widgets/brand.dart';
import '../../src/widgets/depth.dart';
import 'auth_controller.dart';
import 'oidc.dart';

/// Optional public addresses of the deployment's terms and privacy policy
/// (`--dart-define=ANUM_TERMS_URL=...`, `ANUM_PRIVACY_POLICY_URL`), linked
/// from Create account.
const configuredTermsUrl = String.fromEnvironment('ANUM_TERMS_URL');
const configuredPrivacyPolicyUrl =
    String.fromEnvironment('ANUM_PRIVACY_POLICY_URL');

/// A centred, scrollable column on the aurora, 480 px wide at most: the
/// frame of every sign-in screen.
class AuthFrame extends StatelessWidget {
  const AuthFrame({required this.children, this.top, super.key});
  final List<Widget> children;

  /// Pinned above the scrolling content (Skip, Back).
  final Widget? top;

  @override
  Widget build(BuildContext context) => Scaffold(
        body: AnumBackdrop(
          child: SafeArea(
            child: Column(children: [
              if (top != null) top!,
              Expanded(
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 480),
                    child: ListView(
                      padding: const EdgeInsetsDirectional.fromSTEB(
                          AnumSpacing.lg,
                          AnumSpacing.lg,
                          AnumSpacing.lg,
                          AnumSpacing.lg),
                      children: children,
                    ),
                  ),
                ),
              ),
            ]),
          ),
        ),
      );
}

/// Splash (mockup 01): the orb, the wordmark and the motto while the
/// encrypted session is restored.
class AnumSplash extends StatelessWidget {
  const AnumSplash({super.key});

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    return AuthFrame(children: [
      const SizedBox(height: AnumSpacing.xl),
      const Center(child: AnumOrb(size: 180, active: true)),
      const SizedBox(height: AnumSpacing.lg),
      AnumWordmark(
          vertical: true, showMark: false, tagline: l.t('authTagline')),
      const SizedBox(height: AnumSpacing.xl),
      Text(l.t('authMotto'),
          textAlign: TextAlign.center,
          style: Theme.of(context)
              .textTheme
              .titleMedium
              ?.copyWith(color: p.muted, fontWeight: FontWeight.w600)),
      const SizedBox(height: AnumSpacing.xl),
      Semantics(
        liveRegion: true,
        child: Column(children: [
          const Center(child: CircularProgressIndicator()),
          const SizedBox(height: AnumSpacing.sm),
          Text(l.t('authRestoring'),
              textAlign: TextAlign.center, style: TextStyle(color: p.faint)),
        ]),
      ),
    ]);
  }
}

/// Onboarding (mockup 02): skippable pages, shown once per device.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({
    required this.onSignIn,
    required this.onGetStarted,
    super.key,
  });

  /// "Skip" and "Sign In".
  final VoidCallback onSignIn;

  /// "Get Started" on the last page (create an account when sign-up exists).
  final VoidCallback onGetStarted;

  static const pages = [
    (Icons.graphic_eq, 'onboarding1Title', 'onboarding1Body'),
    (Icons.mic_none, 'onboarding2Title', 'onboarding2Body'),
    (Icons.hub_outlined, 'onboarding3Title', 'onboarding3Body'),
    (Icons.verified_user_outlined, 'onboarding4Title', 'onboarding4Body'),
  ];

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _pages = PageController();
  int _page = 0;

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  bool get _last => _page == OnboardingScreen.pages.length - 1;

  void _next() {
    final reduce = MediaQuery.maybeDisableAnimationsOf(context) ?? false;
    final target = _page + 1;
    if (reduce) {
      _pages.jumpToPage(target);
    } else {
      _pages.animateToPage(target,
          duration: AnumMotion.page, curve: Curves.easeOutCubic);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final total = OnboardingScreen.pages.length;
    final rtl = Directionality.of(context) == TextDirection.rtl;
    return Scaffold(
      body: AnumBackdrop(
        child: SafeArea(
          child: Column(children: [
            Align(
              alignment: AlignmentDirectional.centerEnd,
              child: Padding(
                padding: const EdgeInsets.all(AnumSpacing.xs),
                child: TextButton(
                    key: const Key('onboarding-skip'),
                    onPressed: widget.onSignIn,
                    child: Text(l.t('onboardingSkip'))),
              ),
            ),
            Expanded(
              child: PageView.builder(
                controller: _pages,
                itemCount: total,
                onPageChanged: (value) => setState(() => _page = value),
                itemBuilder: (context, index) {
                  final (icon, title, body) = OnboardingScreen.pages[index];
                  return Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 480),
                      child: ListView(
                        padding: const EdgeInsets.symmetric(
                            horizontal: AnumSpacing.lg),
                        children: [
                          const SizedBox(height: AnumSpacing.md),
                          _Illustration(icon: icon),
                          const SizedBox(height: AnumSpacing.xl),
                          Semantics(
                            header: true,
                            child: Text(l.t(title),
                                style: Theme.of(context)
                                    .textTheme
                                    .headlineMedium
                                    ?.copyWith(fontWeight: FontWeight.w800)),
                          ),
                          const SizedBox(height: AnumSpacing.sm),
                          Text(l.t(body),
                              style: Theme.of(context)
                                  .textTheme
                                  .bodyLarge
                                  ?.copyWith(color: p.muted)),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 480),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(AnumSpacing.lg,
                      AnumSpacing.md, AnumSpacing.lg, AnumSpacing.lg),
                  child: _last
                      ? Column(mainAxisSize: MainAxisSize.min, children: [
                          AnumGradientButton(
                              key: const Key('onboarding-start'),
                              label: l.t('onboardingStart'),
                              icon:
                                  rtl ? Icons.arrow_back : Icons.arrow_forward,
                              onPressed: widget.onGetStarted),
                          const SizedBox(height: AnumSpacing.xs),
                          TextButton(
                              key: const Key('onboarding-sign-in'),
                              onPressed: widget.onSignIn,
                              child: Text(l.t('onboardingSignIn'))),
                        ])
                      : Row(children: [
                          Expanded(
                            child: Semantics(
                              label: l.f('onboardingPage',
                                  {'n': _page + 1, 'total': total}),
                              excludeSemantics: true,
                              child: Row(children: [
                                for (var i = 0; i < total; i++)
                                  AnimatedContainer(
                                    duration: AnumMotion.standard,
                                    margin: const EdgeInsetsDirectional.only(
                                        end: AnumSpacing.xs),
                                    width: i == _page ? 24 : 8,
                                    height: 8,
                                    decoration: BoxDecoration(
                                      gradient:
                                          i == _page ? p.accentGradient : null,
                                      color: i == _page ? null : p.line,
                                      borderRadius: BorderRadius.circular(
                                          AnumRadius.pill),
                                    ),
                                  ),
                              ]),
                            ),
                          ),
                          DecoratedBox(
                            decoration: BoxDecoration(
                                shape: BoxShape.circle,
                                gradient: p.accentGradient),
                            child: IconButton(
                              key: const Key('onboarding-next'),
                              tooltip: l.t('onboardingNext'),
                              color: p.onAccent,
                              iconSize: 26,
                              constraints: const BoxConstraints(
                                  minWidth: 56, minHeight: 56),
                              onPressed: _next,
                              icon: Icon(
                                  rtl ? Icons.arrow_back : Icons.arrow_forward),
                            ),
                          ),
                        ]),
                ),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Illustration extends StatelessWidget {
  const _Illustration({required this.icon});
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return ExcludeSemantics(
      child: SizedBox(
        height: 220,
        child: Stack(alignment: Alignment.center, children: [
          const AnumOrb(size: 200),
          PositionedDirectional(
            end: 0,
            bottom: 12,
            child: DecoratedBox(
              decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: p.accentGradient,
                  boxShadow: [
                    BoxShadow(
                        color: p.glow.withValues(alpha: 0.5), blurRadius: 18)
                  ]),
              child: Padding(
                padding: const EdgeInsets.all(AnumSpacing.sm),
                child: Icon(icon, color: p.onAccent, size: 28),
              ),
            ),
          ),
        ]),
      ),
    );
  }
}

enum _IdKind { email, mobile }

final _emailPattern = RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$');
final _mobilePattern = RegExp(r'^\+?[0-9]{7,15}$');

/// Login and Create account (mockups 03 and 04) when the build signs in
/// through Keycloak. The app never collects a password: every button starts
/// the authorization code + PKCE flow in the system browser with hints
/// (`login_hint`, `kc_idp_hint=google|apple`, `prompt=create`), and Forgot
/// password opens Keycloak's reset page.
class OidcSignInView extends StatefulWidget {
  const OidcSignInView({
    required this.controller,
    this.startWithRegistration = false,
    this.termsUrl = configuredTermsUrl,
    this.privacyPolicyUrl = configuredPrivacyPolicyUrl,
    this.openUrl = openExternalUrl,
    super.key,
  });

  final AuthController controller;
  final bool startWithRegistration;
  final String termsUrl, privacyPolicyUrl;

  /// Opens a browser page; false when it could not. Replaced in tests.
  final Future<bool> Function(Uri uri) openUrl;

  @override
  State<OidcSignInView> createState() => _OidcSignInViewState();
}

/// [launchUrl] in the system browser, false on any failure.
Future<bool> openExternalUrl(Uri uri) async {
  try {
    return await launchUrl(uri, mode: LaunchMode.externalApplication);
  } on Object {
    return false;
  }
}

class _OidcSignInViewState extends State<OidcSignInView> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _mobile = TextEditingController();
  _IdKind _kind = _IdKind.email;
  late bool _register = widget.startWithRegistration;
  bool _terms = false;
  bool _termsError = false;

  @override
  void dispose() {
    _email.dispose();
    _mobile.dispose();
    super.dispose();
  }

  String? get _hint {
    final value = _kind == _IdKind.email
        ? _email.text.trim()
        : _mobile.text.replaceAll(RegExp(r'[\s()-]'), '');
    return value.isEmpty ? null : value;
  }

  void _start({OidcIdentityProvider? provider}) {
    if (_register && !_terms) {
      setState(() => _termsError = true);
      return;
    }
    if (provider == null && !(_form.currentState?.validate() ?? true)) return;
    widget.controller.signInWithOidc(OidcSignInOptions(
        loginHint: provider == null ? _hint : null,
        identityProvider: provider,
        register: _register));
  }

  Future<void> _open(Uri? uri) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    final failed = AnumLocalizations.maybeOf(context).t('linkOpenFailed');
    final opened = uri != null && await widget.openUrl(uri);
    if (!opened) messenger?.showSnackBar(SnackBar(content: Text(failed)));
  }

  Future<void> _forgot() async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    final failed = AnumLocalizations.maybeOf(context).t('loginForgotFailed');
    final uri = widget.controller.passwordResetUri;
    final opened = uri != null && await widget.openUrl(uri);
    if (!opened) messenger?.showSnackBar(SnackBar(content: Text(failed)));
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final p = context.palette;
    final text = Theme.of(context).textTheme;
    return PopScope(
      canPop: !_register,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && _register) setState(() => _register = false);
      },
      child: AuthFrame(children: [
        const Center(child: AnumLogoMark(size: 56, semanticLabel: 'ANUM 360')),
        const SizedBox(height: AnumSpacing.lg),
        Semantics(
          header: true,
          child: Text(l.t(_register ? 'signupTitle' : 'loginTitle'),
              style:
                  text.headlineMedium?.copyWith(fontWeight: FontWeight.w800)),
        ),
        const SizedBox(height: AnumSpacing.xxs),
        Text(l.t(_register ? 'signupSubtitle' : 'loginSubtitle'),
            style: text.bodyMedium?.copyWith(color: p.muted)),
        const SizedBox(height: AnumSpacing.lg),
        Form(
          key: _form,
          child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: _register ? _signupFields(l) : _loginFields(l)),
        ),
        const SizedBox(height: AnumSpacing.lg),
        AnumGradientButton(
            key: const Key('auth-primary'),
            label: l.t(_register ? 'signupCreate' : 'loginSignIn'),
            onPressed: _start),
        if (!_register)
          Align(
            alignment: AlignmentDirectional.centerEnd,
            child: TextButton(
                key: const Key('auth-forgot'),
                onPressed: _forgot,
                child: Text(l.t('loginForgot'))),
          ),
        const SizedBox(height: AnumSpacing.sm),
        Row(children: [
          Expanded(child: Divider(color: p.line)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AnumSpacing.sm),
            child: Text(l.t('loginOr'), style: TextStyle(color: p.faint)),
          ),
          Expanded(child: Divider(color: p.line)),
        ]),
        const SizedBox(height: AnumSpacing.sm),
        _SocialButton(
            key: const Key('auth-google'),
            label: l.t('loginGoogle'),
            mark: const _GoogleMark(),
            onPressed: () => _start(provider: OidcIdentityProvider.google)),
        const SizedBox(height: AnumSpacing.sm),
        _SocialButton(
            key: const Key('auth-apple'),
            label: l.t('loginApple'),
            mark: const Icon(Icons.apple, size: 22, color: Colors.black),
            onPressed: () => _start(provider: OidcIdentityProvider.apple)),
        const SizedBox(height: AnumSpacing.md),
        if (_register) ...[
          Text(l.t('signupWorkspaceNote'),
              textAlign: TextAlign.center,
              style: text.bodySmall?.copyWith(color: p.faint)),
          const SizedBox(height: AnumSpacing.xs),
          Wrap(
              alignment: WrapAlignment.center,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(l.t('signupHaveAccount'),
                    style: TextStyle(color: p.muted)),
                TextButton(
                    key: const Key('auth-to-login'),
                    onPressed: () => setState(() {
                          _register = false;
                          _termsError = false;
                        }),
                    child: Text(l.t('signupSignIn'))),
              ]),
        ] else
          Center(
            child: TextButton(
                key: const Key('auth-to-register'),
                onPressed: () => setState(() => _register = true),
                child: Text(l.t('loginCreate'))),
          ),
      ]),
    );
  }

  List<Widget> _loginFields(AnumLocalizations l) => [
        SegmentedButton<_IdKind>(
          key: const Key('auth-kind'),
          showSelectedIcon: false,
          segments: [
            ButtonSegment(
                value: _IdKind.email,
                icon: const Icon(Icons.mail_outline),
                label: Text(l.t('loginEmailTab'))),
            ButtonSegment(
                value: _IdKind.mobile,
                icon: const Icon(Icons.smartphone),
                label: Text(l.t('loginMobileTab'))),
          ],
          selected: {_kind},
          onSelectionChanged: (value) => setState(() => _kind = value.first),
        ),
        const SizedBox(height: AnumSpacing.md),
        if (_kind == _IdKind.email)
          _emailField(l)
        else
          TextFormField(
            key: const Key('auth-mobile'),
            controller: _mobile,
            keyboardType: TextInputType.phone,
            textDirection: TextDirection.ltr,
            autofillHints: const [AutofillHints.telephoneNumber],
            textInputAction: TextInputAction.done,
            onFieldSubmitted: (_) => _start(),
            decoration: InputDecoration(
                labelText: l.t('loginMobileLabel'),
                helperText: l.t('loginMobileHelper'),
                prefixIcon: const Icon(Icons.phone_outlined)),
            validator: (value) {
              final v = (value ?? '').replaceAll(RegExp(r'[\s()-]'), '');
              return v.isEmpty || _mobilePattern.hasMatch(v)
                  ? null
                  : l.t('loginMobileInvalid');
            },
          ),
        const SizedBox(height: AnumSpacing.sm),
        _Note(icon: Icons.lock_outline, text: l.t('loginPasswordNote')),
      ];

  Widget _emailField(AnumLocalizations l, {String? helper}) => TextFormField(
        key: const Key('auth-email'),
        controller: _email,
        keyboardType: TextInputType.emailAddress,
        textDirection: TextDirection.ltr,
        autofillHints: const [AutofillHints.email],
        textInputAction: TextInputAction.done,
        onFieldSubmitted: (_) => _start(),
        decoration: InputDecoration(
            labelText: l.t('loginEmailLabel'),
            helperText: helper,
            helperMaxLines: 2,
            prefixIcon: const Icon(Icons.alternate_email)),
        validator: (value) {
          final v = (value ?? '').trim();
          return v.isEmpty || _emailPattern.hasMatch(v)
              ? null
              : l.t('loginEmailInvalid');
        },
      );

  List<Widget> _signupFields(AnumLocalizations l) {
    final p = context.palette;
    final terms = Uri.tryParse(widget.termsUrl);
    final privacy = Uri.tryParse(widget.privacyPolicyUrl);
    return [
      _emailField(l, helper: l.t('signupEmailHelper')),
      const SizedBox(height: AnumSpacing.md),
      AnumSurface(
        padding: const EdgeInsets.all(AnumSpacing.sm),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(l.t('signupPasswordTitle'),
              style: TextStyle(color: p.muted, fontSize: 13)),
          const SizedBox(height: AnumSpacing.xs),
          for (final rule in const [
            'signupRuleLength',
            'signupRuleUpper',
            'signupRuleNumber',
            'signupRuleSpecial'
          ])
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: Row(children: [
                Icon(Icons.check_circle_outline, size: 18, color: p.ok),
                const SizedBox(width: AnumSpacing.xs),
                Expanded(child: Text(l.t(rule))),
              ]),
            ),
        ]),
      ),
      const SizedBox(height: AnumSpacing.sm),
      // Its own Material, so the tile's ink shows over the aurora.
      Material(
          type: MaterialType.transparency,
          child: CheckboxListTile(
            key: const Key('auth-terms'),
            value: _terms,
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            onChanged: (value) => setState(() {
              _terms = value ?? false;
              if (_terms) _termsError = false;
            }),
            title: Text(l.t('signupTerms')),
            subtitle: _termsError
                ? Semantics(
                    liveRegion: true,
                    child: Text(l.t('signupTermsRequired'),
                        style: TextStyle(color: p.stop)))
                : null,
          )),
      if (widget.termsUrl.isNotEmpty || widget.privacyPolicyUrl.isNotEmpty)
        Wrap(spacing: AnumSpacing.xs, children: [
          if (widget.termsUrl.isNotEmpty)
            TextButton(
                onPressed: () => _open(terms),
                child: Text(l.t('signupTermsLink'))),
          if (widget.privacyPolicyUrl.isNotEmpty)
            TextButton(
                onPressed: () => _open(privacy),
                child: Text(l.t('signupPrivacyLink'))),
        ]),
    ];
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Icon(icon, size: 18, color: p.sky),
      const SizedBox(width: AnumSpacing.xs),
      Expanded(
          child: Text(text, style: TextStyle(color: p.muted, fontSize: 13))),
    ]);
  }
}

/// "Continue with Google / Apple": white buttons with dark text, as both
/// providers' guidelines ask, in either theme.
class _SocialButton extends StatelessWidget {
  const _SocialButton(
      {required this.label,
      required this.mark,
      required this.onPressed,
      super.key});
  final String label;
  final Widget mark;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => OutlinedButton(
        onPressed: onPressed,
        style: OutlinedButton.styleFrom(
          backgroundColor: Colors.white,
          foregroundColor: const Color(0xFF111111),
          side: BorderSide(color: context.palette.line),
          minimumSize: const Size(48, 52),
          padding: const EdgeInsets.symmetric(
              horizontal: AnumSpacing.md, vertical: AnumSpacing.sm),
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(AnumRadius.md)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
        ),
        child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            mainAxisSize: MainAxisSize.min,
            children: [
              ExcludeSemantics(child: mark),
              const SizedBox(width: AnumSpacing.sm),
              Flexible(child: Text(label, textAlign: TextAlign.center)),
            ]),
      );
}

/// A plain four-colour "G" for the Google button (text, no image asset).
class _GoogleMark extends StatelessWidget {
  const _GoogleMark();
  @override
  Widget build(BuildContext context) => ShaderMask(
        shaderCallback: (rect) => const SweepGradient(colors: [
          Color(0xFF4285F4),
          Color(0xFF34A853),
          Color(0xFFFBBC05),
          Color(0xFFEA4335),
          Color(0xFF4285F4),
        ]).createShader(rect),
        child: const Text('G',
            textScaler: TextScaler.noScaling,
            style: TextStyle(
                color: Colors.white,
                fontSize: 20,
                fontWeight: FontWeight.w900)),
      );
}
