import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../src/localization/anum_localizations.dart';
import '../../src/theme/anum_theme.dart';
import '../../src/theme/theme_controller.dart';
import '../../src/widgets/anum_components.dart';
import '../../src/widgets/brand.dart';
import '../../src/widgets/depth.dart';
import '../../src/widgets/navigation.dart';
import '../privacy/privacy_controller.dart';
import '../privacy/privacy_screen.dart';
import 'account_identity.dart';
import 'settings_controller.dart';
import 'settings_screen.dart';

/// The web app's address (`--dart-define=ANUM_WEB_APP_URL=...`), for Help.
const configuredWebAppUrl = String.fromEnvironment('ANUM_WEB_APP_URL');

/// Profile and settings (ANUM 360 mockup 13): who is signed in, the plan, and
/// the way into each part of Settings. Everything is read from the API
/// through [SettingsController]; language and theme apply at once.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({
    required this.settings,
    required this.locale,
    required this.theme,
    this.workspaceName,
    this.onAskAnum,
    this.webAppUrl = configuredWebAppUrl,
    super.key,
  });

  final SettingsController settings;
  final LocaleController locale;
  final ThemeController theme;
  final String? workspaceName;

  /// Opens the Assistant (Help > Ask ANUM); hidden when null.
  final VoidCallback? onAskAnum;
  final String webAppUrl;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  @override
  void initState() {
    super.initState();
    final phase = widget.settings.phase;
    if (phase == SettingsPhase.initial ||
        phase == SettingsPhase.error ||
        phase == SettingsPhase.offline) {
      widget.settings.load();
    }
  }

  void _push(Widget page) =>
      Navigator.push(context, MaterialPageRoute<void>(builder: (_) => page));

  void _settings([SettingsSection? section]) => _push(
      SettingsScreen(controller: widget.settings, initialSection: section));

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return ListenableBuilder(
      listenable:
          Listenable.merge([widget.settings, widget.locale, widget.theme]),
      builder: (context, _) => Scaffold(
        appBar: AppBar(title: Text(l.t('profileTitle'))),
        body: AnumBackdrop(child: _body(context)),
      ),
    );
  }

  Widget _body(BuildContext context) {
    final l = context.anum;
    final settings = widget.settings;
    final session = settings.session;
    final rtl = Directionality.of(context) == TextDirection.rtl;
    final chevron = Icon(rtl ? Icons.chevron_left : Icons.chevron_right);
    final themeLabel = switch (widget.theme.mode) {
      ThemeMode.system => l.t('themeSystem'),
      ThemeMode.light => l.t('themeLight'),
      ThemeMode.dark => l.t('themeDark'),
    };
    final languageLabel = l.isArabic ? l.t('arabic') : l.t('english');
    // Each row gets its own Material, so ink shows on the raised surface.
    Widget row(String key, IconData icon, VoidCallback onTap,
            {String? detail, String? value}) =>
        Material(
            type: MaterialType.transparency,
            child: ListTile(
              key: Key('profile-$key'),
              leading: Icon(icon),
              title: Text(l.t(key)),
              subtitle: detail == null ? null : Text(detail),
              trailing: value == null
                  ? chevron
                  : Row(mainAxisSize: MainAxisSize.min, children: [
                      ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 120),
                        child: Text(value,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(color: context.palette.muted)),
                      ),
                      chevron,
                    ]),
              onTap: onTap,
            ));
    // Language and theme live on this device: they stay reachable while the
    // profile cannot load (offline, a refused or failing API call).
    Widget failed(AnumFeedback feedback) => AnumPage(
          onRefresh: settings.load,
          children: [
            feedback,
            AnumSurface(
              padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
              child: Column(children: [
                row('language', Icons.translate, () => _pickLanguage(context),
                    value: languageLabel),
                row('profileTheme', Icons.brightness_6_outlined,
                    () => _pickTheme(context),
                    value: themeLabel),
              ]),
            ),
          ],
        );
    switch (settings.phase) {
      case SettingsPhase.initial || SettingsPhase.loading:
        if (session == null) {
          return AnumFeedback(
              kind: AnumFeedbackKind.loading, message: l.t('profileLoading'));
        }
      case SettingsPhase.offline:
        return failed(AnumFeedback(
            kind: AnumFeedbackKind.offline,
            message: l.t('profileOffline'),
            onRetry: settings.load));
      case SettingsPhase.permissionDenied:
        return failed(AnumFeedback(
            kind: AnumFeedbackKind.permission,
            message: l.t('profileDenied'),
            onRetry: settings.load));
      case SettingsPhase.error:
        return failed(AnumFeedback(
            kind: AnumFeedbackKind.error,
            message: l.f('profileError', {'message': settings.message ?? ''}),
            onRetry: settings.load));
      case SettingsPhase.ready || SettingsPhase.saving:
        break;
    }
    final identity = AccountIdentity.fromSession(session!);
    return AnumPage(
      onRefresh: settings.load,
      children: [
        _ProfileCard(identity: identity, workspaceName: widget.workspaceName),
        AnumSurface(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          child: Column(children: [
            row('profileMy', Icons.person_outline,
                () => _settings(SettingsSection.profile),
                detail: l.t('profileMyDetail')),
            row(
                'profileSubscription',
                Icons.workspace_premium_outlined,
                () => _push(
                    SubscriptionScreen(workspaceName: widget.workspaceName)),
                value: l.t('planFree')),
            row('profileSecurity', Icons.shield_outlined,
                () => _settings(SettingsSection.security),
                detail: l.t('profileSecurityDetail')),
            row('profileNotifications', Icons.notifications_none,
                () => _settings(SettingsSection.notifications)),
            row('language', Icons.translate, () => _pickLanguage(context),
                value: languageLabel),
            row('profileTheme', Icons.brightness_6_outlined,
                () => _pickTheme(context),
                value: themeLabel),
            row('profileData', Icons.privacy_tip_outlined, () {
              final privacy = settings.privacy;
              if (privacy == null) {
                _settings(SettingsSection.privacy);
              } else {
                _push(PrivacyScreen(controller: PrivacyController(privacy)));
              }
            }),
            row(
                'profileHelp',
                Icons.help_outline,
                () => _push(HelpScreen(
                    onAskAnum: widget.onAskAnum,
                    onOpenPrivacy: settings.privacy == null
                        ? null
                        : () => _push(PrivacyScreen(
                            controller: PrivacyController(settings.privacy!))),
                    webAppUrl: widget.webAppUrl))),
            row('profileAbout', Icons.info_outline, () => _about(context)),
          ]),
        ),
        AnumSurface(
          padding: const EdgeInsets.symmetric(vertical: AnumSpacing.xs),
          child: row(
              'profileAllSettings', Icons.settings_outlined, () => _settings(),
              detail: l.t('profileAllSettingsDetail')),
        ),
      ],
    );
  }

  Future<void> _pickLanguage(BuildContext context) async {
    final l = context.anum;
    final current = l.isArabic ? 'ar' : 'en';
    final picked = await showModalBottomSheet<String>(
        context: context,
        builder: (context) => SafeArea(
              child: RadioGroup<String>(
                groupValue: current,
                onChanged: (value) => Navigator.pop(context, value),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  RadioListTile<String>(
                      value: 'en', title: Text(l.t('english'))),
                  RadioListTile<String>(
                      value: 'ar', title: Text(l.t('arabic'))),
                ]),
              ),
            ));
    if (picked != null && picked != current) {
      await widget.locale.select(picked);
    }
  }

  Future<void> _pickTheme(BuildContext context) async {
    final l = context.anum;
    final picked = await showModalBottomSheet<ThemeMode>(
        context: context,
        builder: (context) => SafeArea(
              child: RadioGroup<ThemeMode>(
                groupValue: widget.theme.mode,
                onChanged: (value) => Navigator.pop(context, value),
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  RadioListTile<ThemeMode>(
                      value: ThemeMode.dark, title: Text(l.t('themeDark'))),
                  RadioListTile<ThemeMode>(
                      value: ThemeMode.light, title: Text(l.t('themeLight'))),
                  RadioListTile<ThemeMode>(
                      value: ThemeMode.system, title: Text(l.t('themeSystem'))),
                ]),
              ),
            ));
    if (picked != null) await widget.theme.select(picked);
  }

  void _about(BuildContext context) => showAboutDialog(
        context: context,
        applicationName: 'ANUM 360',
        applicationVersion: appVersion,
        applicationIcon: const AnumLogoMark(size: 48),
        applicationLegalese: context.anum.t('aboutLegalese'),
      );
}

/// The app's version, from `--dart-define=ANUM_APP_VERSION` (the release
/// pipeline sets it) or pubspec's version.
const appVersion =
    String.fromEnvironment('ANUM_APP_VERSION', defaultValue: '0.1.0');

class _ProfileCard extends StatelessWidget {
  const _ProfileCard({required this.identity, this.workspaceName});
  final AccountIdentity identity;
  final String? workspaceName;

  @override
  Widget build(BuildContext context) {
    final p = context.palette;
    final l = context.anum;
    final text = Theme.of(context).textTheme;
    final role = switch (identity.role) {
      'owner' => l.t('roleOwner'),
      'member' => l.t('roleMember'),
      'viewer' => l.t('roleViewer'),
      _ => null,
    };
    return AnumSurface(
      child: Column(children: [
        AnumAvatar(name: identity.displayName, size: 76),
        const SizedBox(height: AnumSpacing.sm),
        Semantics(
          header: true,
          child: Text(identity.displayName,
              textAlign: TextAlign.center,
              style: text.titleLarge?.copyWith(fontWeight: FontWeight.w800)),
        ),
        Text(identity.email ?? identity.userId,
            textAlign: TextAlign.center,
            textDirection: TextDirection.ltr,
            style: text.bodyMedium?.copyWith(color: p.muted)),
        if (workspaceName != null && workspaceName!.isNotEmpty)
          Text(workspaceName!,
              textAlign: TextAlign.center,
              style: text.bodySmall?.copyWith(color: p.faint)),
        const SizedBox(height: AnumSpacing.sm),
        Wrap(
          alignment: WrapAlignment.center,
          spacing: AnumSpacing.xs,
          runSpacing: AnumSpacing.xs,
          children: [
            AnumPill(
                label: l.t('planFree'),
                tone: AnumTone.accent,
                icon: Icons.workspace_premium_outlined),
            if (role != null)
              AnumPill(
                  label: role, tone: AnumTone.info, icon: Icons.badge_outlined),
          ],
        ),
      ]),
    );
  }
}

/// The workspace's plan. There is no billing in ANUM yet (no plan endpoint),
/// so this says so plainly and offers nothing to buy.
class SubscriptionScreen extends StatelessWidget {
  const SubscriptionScreen({this.workspaceName, super.key});
  final String? workspaceName;

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    final p = context.palette;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('profileSubscription'))),
      body: AnumPage(children: [
        AnumPlanCard(title: l.t('planFree'), subtitle: l.t('planFreeShort')),
        AnumSurface(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (workspaceName != null && workspaceName!.isNotEmpty) ...[
              Text(l.t('subscriptionWorkspace'),
                  style: TextStyle(color: p.muted, fontSize: AnumType.xsPlus)),
              Text(workspaceName!,
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: AnumSpacing.sm),
            ],
            Text(l.t('subscriptionBody')),
          ]),
        ),
      ]),
    );
  }
}

/// Help and support: where to get answers, each with a real action.
class HelpScreen extends StatelessWidget {
  const HelpScreen({
    this.onAskAnum,
    this.onOpenPrivacy,
    this.webAppUrl = configuredWebAppUrl,
    super.key,
  });

  final VoidCallback? onAskAnum;
  final VoidCallback? onOpenPrivacy;
  final String webAppUrl;

  Future<void> _openWeb(BuildContext context) async {
    final messenger = ScaffoldMessenger.maybeOf(context);
    final failed = context.anum.t('linkOpenFailed');
    final uri = Uri.tryParse(webAppUrl);
    var opened = false;
    try {
      opened = uri != null &&
          await launchUrl(uri, mode: LaunchMode.externalApplication);
    } on Object {
      opened = false;
    }
    if (!opened) messenger?.showSnackBar(SnackBar(content: Text(failed)));
  }

  @override
  Widget build(BuildContext context) {
    final l = context.anum;
    return Scaffold(
      appBar: AppBar(title: Text(l.t('helpTitle'))),
      body: AnumPage(children: [
        AnumSurface(
          child: Column(children: [
            if (onAskAnum != null)
              AnumRow(
                  icon: Icons.auto_awesome,
                  title: l.t('helpAsk'),
                  subtitle: l.t('helpAskDetail'),
                  onTap: () {
                    Navigator.of(context).popUntil((route) => route.isFirst);
                    onAskAnum!();
                  }),
            AnumRow(
                icon: Icons.verified_user_outlined,
                title: l.t('helpApprovals'),
                subtitle: l.t('helpApprovalsDetail')),
            AnumRow(
                icon: Icons.supervisor_account_outlined,
                title: l.t('helpOwner'),
                subtitle: l.t('helpOwnerDetail')),
            if (onOpenPrivacy != null)
              AnumRow(
                  icon: Icons.privacy_tip_outlined,
                  title: l.t('helpPrivacy'),
                  subtitle: l.t('helpPrivacyDetail'),
                  onTap: onOpenPrivacy),
            if (webAppUrl.isNotEmpty)
              AnumRow(
                  icon: Icons.open_in_browser,
                  title: l.t('helpWeb'),
                  subtitle: l.t('helpWebDetail'),
                  onTap: () => _openWeb(context)),
          ]),
        ),
      ]),
    );
  }
}
