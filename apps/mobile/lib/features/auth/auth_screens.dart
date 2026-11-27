import 'package:flutter/material.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/localization/auth_strings.dart';
import '../../src/widgets/brand.dart';
import '../../src/widgets/depth.dart';

import '../../src/theme/anum_theme.dart';
import '../../data/api_client.dart';
import '../../data/api_models.dart';
import '../settings/model_choice.dart';
import '../portal/portal_screens.dart' show PortalEntryBar;
import 'auth_controller.dart';
import 'account_screens.dart';
import 'intro.dart';
import 'welcome_screens.dart';

class AuthFlow extends StatelessWidget {
  const AuthFlow(
      {required this.controller, this.intro, this.onOpenPortal, super.key});
  final AuthController controller;

  /// Opens the customer portal (docs/business-portal.md): a customer pastes
  /// the link a business sent. Null uses the app's `PortalEntryScope`, and
  /// without one the entry is hidden.
  final VoidCallback? onOpenPortal;

  /// The onboarding pages, shown once per device before the first sign-in;
  /// without it sign-in comes straight after the splash.
  final IntroController? intro;

  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: Listenable.merge([controller, intro]),
      builder: (_, __) => switch (controller.phase) {
            AuthPhase.restoring => const SplashScreen(),
            AuthPhase.signedOut when intro?.show ?? false => OnboardingScreen(
                onSignIn: () => intro!.finish(),
                onGetStarted: () =>
                    intro!.finish(register: controller.oidcEnabled)),
            AuthPhase.signedOut => PortalEntryBar(
                onOpen: onOpenPortal,
                child: SignInScreen(
                    controller: controller,
                    startWithRegistration: intro?.takeRegistration() ?? false)),
            AuthPhase.workspacePicker =>
              WorkspacePickerScreen(controller: controller),
            AuthPhase.noWorkspace => NoWorkspaceScreen(controller: controller),
            AuthPhase.accountPending =>
              PendingAccountScreen(controller: controller),
            AuthPhase.onboarding =>
              WorkspaceSetupScreen(controller: controller),
            AuthPhase.modelSetup =>
              ModelConnectionScreen(controller: controller),
            AuthPhase.busy => const _Busy(),
            AuthPhase.error => _Failure(controller),
            AuthPhase.ready => const SizedBox.shrink(),
          });
}

class _Page extends StatelessWidget {
  const _Page(
      {required this.eyebrow,
      required this.title,
      required this.body,
      this.subtitle});
  final String eyebrow, title;
  final String? subtitle;
  final Widget body;
  @override
  Widget build(BuildContext context) => Scaffold(
      body: AnumBackdrop(
          child: SafeArea(
              child: Center(
                  child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 480),
                      child: ListView(
                          padding: const EdgeInsetsDirectional.fromSTEB(
                              AnumSpacing.lg,
                              AnumSpacing.xl,
                              AnumSpacing.lg,
                              AnumSpacing.lg),
                          children: [
                            const Align(
                                alignment: AlignmentDirectional.centerStart,
                                child: AnumWordmark(markSize: 36)),
                            const SizedBox(height: AnumSpacing.xl),
                            Text(eyebrow.toUpperCase(),
                                style: Theme.of(context)
                                    .textTheme
                                    .labelMedium
                                    ?.copyWith(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .primary)),
                            const SizedBox(height: 4),
                            Text(title,
                                style: Theme.of(context)
                                    .textTheme
                                    .headlineMedium
                                    ?.copyWith(fontWeight: FontWeight.w700)),
                            if (subtitle != null) ...[
                              const SizedBox(height: 8),
                              Text(subtitle!)
                            ],
                            const SizedBox(height: AnumSpacing.lg),
                            body,
                          ]))))));
}

/// Splash (ANUM 360 mockup 01) while the session is restored.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});
  @override
  Widget build(BuildContext context) => const AnumSplash();
}

class SignInScreen extends StatefulWidget {
  const SignInScreen(
      {required this.controller,
      this.startWithRegistration = false,
      super.key});
  final AuthController controller;

  /// Open Create account instead of Login (onboarding's "Get Started").
  final bool startWithRegistration;
  @override
  State<SignInScreen> createState() => _SignInState();
}

class _SignInState extends State<SignInScreen> {
  final form = GlobalKey<FormState>(),
      tenant = TextEditingController(text: 'local'),
      workspace = TextEditingController(text: 'default'),
      user = TextEditingController(text: 'owner'),
      password = TextEditingController();
  bool revealPassword = false;
  @override
  void dispose() {
    tenant.dispose();
    workspace.dispose();
    user.dispose();
    password.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.controller.oidcEnabled
      ? OidcSignInView(
          controller: widget.controller,
          startWithRegistration: widget.startWithRegistration)
      : _localSignIn(context);

  Widget _localSignIn(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.signIn.eyebrow'),
        title: l.t('auth.signIn.title'),
        subtitle: l.t('auth.signIn.subtitle'),
        body: Form(
            key: form,
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  if (widget.controller.sessionEnded) ...[
                    const SessionEndedNotice(),
                    const SizedBox(height: AnumSpacing.md),
                  ],
                  _id(tenant, l.t('auth.signIn.organizationId'),
                      Icons.apartment_outlined, l),
                  const SizedBox(height: AnumSpacing.md),
                  _id(workspace, l.t('auth.signIn.workspaceId'),
                      Icons.workspaces_outlined, l),
                  const SizedBox(height: AnumSpacing.md),
                  _id(user, l.t('auth.signIn.userId'), Icons.person_outline, l),
                  const SizedBox(height: AnumSpacing.md),
                  TextFormField(
                      controller: password,
                      obscureText: !revealPassword,
                      decoration: InputDecoration(
                          labelText: l.t('auth.signIn.password'),
                          prefixIcon: const Icon(Icons.lock_outline),
                          suffixIcon: IconButton(
                              tooltip: l.t(revealPassword
                                  ? 'auth.signIn.hidePassword'
                                  : 'auth.signIn.showPassword'),
                              onPressed: () => setState(
                                  () => revealPassword = !revealPassword),
                              icon: Icon(revealPassword
                                  ? Icons.visibility_off
                                  : Icons.visibility)))),
                  const SizedBox(height: AnumSpacing.lg),
                  FilledButton.icon(
                      onPressed: () {
                        if (form.currentState!.validate()) {
                          widget.controller.signIn(
                              tenant.text, workspace.text, user.text,
                              password: password.text);
                        }
                      },
                      icon: const Icon(Icons.login),
                      label: Text(l.t('auth.continue'))),
                  const SizedBox(height: 8),
                  Wrap(alignment: WrapAlignment.center, children: [
                    TextButton(
                        onPressed: _otp, child: Text(l.t('auth.signIn.otp'))),
                    TextButton(
                        onPressed: _forgotPassword,
                        child: Text(l.t('auth.signIn.forgot')))
                  ])
                ])));
  }

  Widget _id(TextEditingController c, String label, IconData icon,
          AnumLocalizations l) =>
      TextFormField(
          controller: c,
          textInputAction: TextInputAction.next,
          decoration: InputDecoration(labelText: label, prefixIcon: Icon(icon)),
          validator: (v) => (v?.trim().length ?? 0) < 3
              ? l.t('auth.signIn.minLength')
              : null);

  Future<void> _otp() async {
    if (!form.currentState!.validate()) return;
    final String challenge;
    try {
      challenge = await widget.controller.repository.requestOtp(
          tenantId: tenant.text.trim(),
          workspaceId: workspace.text.trim(),
          userId: user.text.trim());
    } on Object catch (error) {
      // Say why no code was sent (unknown user, codes off, offline) instead
      // of failing silently.
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(AnumLocalizations.maybeOf(context).f(
              'auth.signIn.noCode',
              {'reason': error is ApiException ? error.readable : error}))));
      return;
    }
    if (!mounted) return;
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => OtpVerificationScreen(
                controller: widget.controller, challengeId: challenge)));
  }

  Future<void> _forgotPassword() async {
    if (!form.currentState!.validate()) return;
    if (!mounted) return;
    await Navigator.push(
        context,
        MaterialPageRoute<void>(
            builder: (_) => PasswordRecoveryScreen(
                controller: widget.controller,
                tenantId: tenant.text.trim(),
                workspaceId: workspace.text.trim(),
                userId: user.text.trim())));
  }
}

/// First run without a default workspace: the token names none and the
/// caller has several memberships (docs/mobile.md#first-run).
class WorkspacePickerScreen extends StatelessWidget {
  const WorkspacePickerScreen({required this.controller, super.key});
  final AuthController controller;

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.picker.eyebrow'),
        title: l.t('auth.picker.title'),
        subtitle: l.t('auth.picker.subtitle'),
        body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          for (final membership in controller.workspaceChoices) ...[
            AccountPanel(
                key: ValueKey('choose-${membership.workspaceId}'),
                title: membership.workspaceName ?? membership.workspaceId,
                subtitle: _membershipLine(l, membership),
                onTap: () =>
                    controller.chooseWorkspace(membership.workspaceId)),
            const SizedBox(height: AnumSpacing.sm),
          ],
          const SizedBox(height: AnumSpacing.sm),
          TextButton(
              onPressed: controller.signOut,
              child: Text(l.t('auth.otherAccount'))),
        ]));
  }

  static String _membershipLine(
          AnumLocalizations l, CallerMembership membership) =>
      [
        if (membership.workspaceName != null) membership.workspaceId,
        l.f('auth.role', {'role': authRoleLabel(l, membership.role)}),
      ].join(' · ');
}

/// Signed in, but no active membership in any workspace of the tenant and no
/// default workspace to create one in.
class NoWorkspaceScreen extends StatelessWidget {
  const NoWorkspaceScreen({required this.controller, super.key});
  final AuthController controller;

  @override
  Widget build(BuildContext context) {
    final existing = controller.workspaceName;
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
      eyebrow: l.t('auth.none.eyebrow'),
      title: l.t('auth.none.title'),
      subtitle: existing != null
          ? l.f('auth.none.subtitleExisting', {'name': existing})
          : l.t('auth.none.subtitle'),
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(existing != null
            ? l.f('auth.none.askExisting', {'name': existing})
            : l.t('auth.none.ask')),
        const SizedBox(height: AnumSpacing.lg),
        FilledButton.icon(
            onPressed: controller.checkWorkspacesAgain,
            icon: const Icon(Icons.refresh),
            label: Text(l.t('auth.checkAgain'))),
        const SizedBox(height: AnumSpacing.sm),
        TextButton(
            onPressed: controller.signOut,
            child: Text(l.t('auth.otherAccount'))),
      ]),
    );
  }
}

class WorkspaceSetupScreen extends StatefulWidget {
  const WorkspaceSetupScreen({required this.controller, super.key});
  final AuthController controller;
  @override
  State<WorkspaceSetupScreen> createState() => _WorkspaceState();
}

class _WorkspaceState extends State<WorkspaceSetupScreen> {
  final form = GlobalKey<FormState>(),
      organization = TextEditingController(),
      workspace = TextEditingController();
  @override
  void dispose() {
    organization.dispose();
    workspace.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.setup.eyebrow'),
        title: l.t('auth.setup.title'),
        subtitle: l.t('auth.setup.subtitle'),
        body: Form(
            key: form,
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextFormField(
                      controller: organization,
                      textCapitalization: TextCapitalization.words,
                      decoration: InputDecoration(
                          labelText: l.t('auth.setup.organization'),
                          prefixIcon: const Icon(Icons.apartment_outlined)),
                      validator: (v) => _required(l, v)),
                  const SizedBox(height: AnumSpacing.md),
                  TextFormField(
                      controller: workspace,
                      textCapitalization: TextCapitalization.words,
                      decoration: InputDecoration(
                          labelText: l.t('auth.setup.workspace'),
                          prefixIcon: const Icon(Icons.workspaces_outlined)),
                      validator: (v) => _required(l, v)),
                  const SizedBox(height: AnumSpacing.lg),
                  FilledButton.icon(
                      onPressed: () {
                        if (form.currentState!.validate()) {
                          widget.controller.createWorkspace(
                              organization.text, workspace.text);
                        }
                      },
                      icon: const Icon(Icons.arrow_forward),
                      label: Text(l.t('auth.setup.create')))
                ])));
  }
}

class ModelConnectionScreen extends StatefulWidget {
  const ModelConnectionScreen({required this.controller, super.key});
  final AuthController controller;
  @override
  State<ModelConnectionScreen> createState() => _ModelState();
}

class _ModelState extends State<ModelConnectionScreen> {
  final form = GlobalKey<FormState>(),
      model = TextEditingController(text: 'gpt-4.1-mini'),
      url = TextEditingController(text: 'https://api.openai.com/v1'),
      key = TextEditingController(),
      fastModel = TextEditingController(text: anthropicFastModel),
      capableModel = TextEditingController(text: anthropicCapableModel);
  String provider = 'openai_compatible';
  String fastEffort = 'low', capableEffort = '';
  bool reveal = false;

  /// Defaults per provider (model_choice.dart). Ollama is free and runs on the
  /// user's computer. The ANUM API server (not the phone) calls this URL, so it
  /// is the address of Ollama as seen from the server: localhost when both run
  /// on one machine.
  static const _presets = modelPresets;

  bool get _needsKey => providerNeedsKey(provider);
  bool get _claude => provider == 'anthropic';

  void _selectProvider(String next) {
    final previous = _presets[provider];
    final preset = _presets[next];
    setState(() {
      // Only replace values the user has not edited away from the old preset.
      if (preset != null) {
        if (previous == null || model.text.trim() == previous.$1) {
          model.text = preset.$1;
        }
        if (previous == null || url.text.trim() == previous.$2) {
          url.text = preset.$2;
        }
      }
      provider = next;
    });
  }

  @override
  void dispose() {
    model.dispose();
    url.dispose();
    key.dispose();
    fastModel.dispose();
    capableModel.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.controller.canConfigureModel
      ? _ownerSetup(context)
      : _memberWait(context);

  /// Members and viewers may not save the workspace's model (owners only).
  Widget _memberWait(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.model.eyebrow'),
        title: l.t('auth.model.title'),
        subtitle: l.t('auth.model.memberSubtitle'),
        body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _SkipStatus(controller: widget.controller, owner: false),
          const SizedBox(height: AnumSpacing.sm),
          TextButton(
              onPressed: widget.controller.signOut,
              child: Text(l.t('auth.otherAccount'))),
        ]));
  }

  Widget _ownerSetup(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.model.eyebrow'),
        title: l.t('auth.model.title'),
        subtitle: l.t('auth.model.ownerSubtitle'),
        body: Form(
            key: form,
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  DropdownButtonFormField<String>(
                      initialValue: provider,
                      decoration: InputDecoration(
                          labelText: l.t('auth.model.provider'),
                          prefixIcon: const Icon(Icons.hub_outlined)),
                      isExpanded: true,
                      items: providerItems(),
                      onChanged: (v) {
                        if (v != null) _selectProvider(v);
                      }),
                  if (!_claude) ...[
                    const SizedBox(height: AnumSpacing.md),
                    TextFormField(
                        controller: model,
                        decoration: InputDecoration(
                            labelText: l.t('auth.model.model'),
                            prefixIcon: const Icon(Icons.smart_toy_outlined)),
                        validator: (v) => _required(l, v)),
                  ],
                  const SizedBox(height: AnumSpacing.md),
                  TextFormField(
                      controller: url,
                      textDirection: TextDirection.ltr,
                      keyboardType: TextInputType.url,
                      decoration: InputDecoration(
                          labelText: l.t(_claude
                              ? 'auth.model.baseUrlOptional'
                              : 'auth.model.baseUrl'),
                          prefixIcon: const Icon(Icons.link),
                          helperMaxLines: 3,
                          helperText: provider == 'ollama'
                              ? l.t('auth.model.ollamaHelp')
                              : _claude
                                  ? l.t('auth.model.claudeHelp')
                                  : null),
                      validator: (v) => validateBaseUrl(provider, v)),
                  if (_needsKey) ...[
                    const SizedBox(height: AnumSpacing.md),
                    TextFormField(
                        controller: key,
                        textDirection: TextDirection.ltr,
                        obscureText: !reveal,
                        decoration: InputDecoration(
                            labelText: l.t(_claude
                                ? 'auth.model.anthropicKey'
                                : 'auth.model.apiKey'),
                            prefixIcon: const Icon(Icons.key_outlined),
                            suffixIcon: IconButton(
                                tooltip: l.t(reveal
                                    ? 'auth.model.hideKey'
                                    : 'auth.model.showKey'),
                                onPressed: () =>
                                    setState(() => reveal = !reveal),
                                icon: Icon(reveal
                                    ? Icons.visibility_off
                                    : Icons.visibility))),
                        validator: (v) => _required(l, v))
                  ],
                  if (_claude) ...[
                    const SizedBox(height: AnumSpacing.md),
                    ClaudeRoutingFields(
                        fastModel: fastModel,
                        capableModel: capableModel,
                        fastEffort: fastEffort,
                        capableEffort: capableEffort,
                        onFastEffort: (v) => setState(() => fastEffort = v),
                        onCapableEffort: (v) =>
                            setState(() => capableEffort = v)),
                  ],
                  const SizedBox(height: AnumSpacing.lg),
                  FilledButton.icon(
                      onPressed: () {
                        if (form.currentState!.validate()) {
                          widget.controller.connectModel(
                              provider: provider,
                              // Claude's planning model is the workspace default.
                              model: _claude
                                  ? capableModel.text.trim()
                                  : model.text.trim(),
                              baseUrl: url.text.trim(),
                              apiKey: _needsKey ? key.text.trim() : '',
                              routing: _claude
                                  ? ModelRouting(
                                      fastModel: fastModel.text,
                                      capableModel: capableModel.text,
                                      fastEffort: fastEffort,
                                      capableEffort: capableEffort)
                                  : null);
                        }
                      },
                      icon: const Icon(Icons.cable),
                      label: Text(l.t('auth.model.save'))),
                  const SizedBox(height: AnumSpacing.md),
                  _SkipStatus(controller: widget.controller, owner: true),
                ])));
  }
}

/// How to go on without this step: use the workspace's or the server's model
/// when one answers, else continue without a model (AI answers are then
/// placeholders, with a banner on the screens where they appear). Nobody is
/// kept out of the app for want of a model.
class _SkipStatus extends StatelessWidget {
  const _SkipStatus({required this.controller, required this.owner});
  final AuthController controller;
  final bool owner;

  @override
  Widget build(BuildContext context) {
    final availability = controller.modelAvailability;
    final l = AnumLocalizations.maybeOf(context);
    if (controller.checkingModel) {
      return Semantics(
          liveRegion: true,
          child: Row(children: [
            const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2)),
            const SizedBox(width: AnumSpacing.md),
            Expanded(child: Text(l.t('auth.model.checking')))
          ]));
    }
    final error = controller.modelCheckError;
    if (error != null || availability == null) {
      return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(l.f('auth.model.checkFailed',
            {'reason': error ?? l.t('auth.model.noAnswer')})),
        const SizedBox(height: AnumSpacing.sm),
        OutlinedButton.icon(
            onPressed: controller.checkModelAvailability,
            icon: const Icon(Icons.refresh),
            label: Text(l.t('auth.checkAgain'))),
        TextButton(
            onPressed: controller.continueWithoutModel,
            child: Text(l.t('auth.model.continueWithout'))),
      ]);
    }
    if (availability.canSkip) {
      final label = availability.workspaceConfigured
          ? l.t('auth.model.useWorkspace')
          : l.f(
              'auth.model.useServer', {'model': '${availability.serverModel}'});
      return owner
          ? TextButton(onPressed: controller.useServerModel, child: Text(label))
          : FilledButton(
              onPressed: controller.useServerModel, child: Text(label));
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(l.t(owner ? 'auth.model.noneOwner' : 'auth.model.noneMember')),
      const SizedBox(height: AnumSpacing.sm),
      owner
          ? TextButton(
              onPressed: controller.continueWithoutModel,
              child: Text(l.t('auth.model.continueWithout')))
          : FilledButton(
              onPressed: controller.continueWithoutModel,
              child: Text(l.t('auth.model.continueWithout'))),
      OutlinedButton.icon(
          onPressed: controller.checkModelAvailability,
          icon: const Icon(Icons.refresh),
          label: Text(l.t('auth.checkAgain'))),
    ]);
  }
}

class _Busy extends StatelessWidget {
  const _Busy();
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    return _Page(
        eyebrow: l.t('auth.busy.eyebrow'),
        title: l.t('auth.busy.title'),
        body: Row(children: [
          const CircularProgressIndicator(),
          const SizedBox(width: AnumSpacing.md),
          Expanded(child: Text(l.t('auth.busy.body')))
        ]));
  }
}

class _Failure extends StatelessWidget {
  const _Failure(this.controller);
  final AuthController controller;
  @override
  Widget build(BuildContext context) {
    final l = AnumLocalizations.maybeOf(context);
    final (icon, titleKey) = switch (controller.issue) {
      AuthIssue.offline => (Icons.cloud_off_outlined, 'auth.failure.offline'),
      AuthIssue.permission => (Icons.lock_outline, 'auth.failure.permission'),
      AuthIssue.invalidSession => (
          Icons.no_accounts_outlined,
          'auth.failure.session'
        ),
      AuthIssue.validation => (Icons.edit_outlined, 'auth.failure.validation'),
      _ => (Icons.error_outline, 'auth.failure.other')
    };
    final message = controller.message;
    final messageKey = message == null ? null : authMessageKeys[message];
    return _Page(
        eyebrow: l.t('auth.failure.eyebrow'),
        title: l.t(titleKey),
        body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Icon(icon, size: 42, color: Theme.of(context).colorScheme.error),
          const SizedBox(height: AnumSpacing.md),
          Text(messageKey != null
              ? l.t(messageKey)
              : message ?? l.t('auth.failure.tryAgainText')),
          const SizedBox(height: AnumSpacing.lg),
          FilledButton.icon(
              onPressed: controller.retry,
              icon: const Icon(Icons.refresh),
              label: Text(l.t('auth.tryAgain'))),
          if (controller.issue == AuthIssue.invalidSession)
            TextButton(
                onPressed: controller.signOut,
                child: Text(l.t('auth.failure.backToSignIn')))
        ]));
  }
}

String? _required(AnumLocalizations l, String? value) =>
    value == null || value.trim().isEmpty ? l.t('auth.required') : null;
