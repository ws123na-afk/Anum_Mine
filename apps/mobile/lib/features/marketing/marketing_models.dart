import '../../data/json_read.dart';
import '../workspace/api_workspace_repository.dart';
import '../workspace/workspace_models.dart';

/// Social media marketing (docs/marketing.md): what the Marketing screen reads
/// from `/api/v1/marketing`.

const marketingPlatforms = [
  'facebook',
  'instagram',
  'linkedin',
  'x',
  'tiktok',
  'youtube'
];
const marketingPostStatuses = [
  'draft',
  'pending_approval',
  'scheduled',
  'publishing',
  'published',
  'sent_to_inbox',
  'failed',
  'cancelled'
];
const marketingCampaignStatuses = [
  'draft',
  'active',
  'paused',
  'completed',
  'archived'
];

class MkError {
  const MkError({required this.code, required this.message, required this.fix});
  final String code, message, fix;
  static MkError? read(JsonReader? j) => j == null
      ? null
      : MkError(
          code: j.optString('code') ?? '',
          message: j.optString('message') ?? '',
          fix: j.optString('fix') ?? '');
}

class MkAccount {
  const MkAccount(
      {required this.id,
      required this.platform,
      required this.provider,
      required this.displayName,
      required this.status,
      this.handle,
      this.lastError});
  final String id, platform, provider, displayName, status;
  final String? handle;
  final MkError? lastError;
  bool get connected => status == 'connected';

  factory MkAccount.fromJson(JsonReader j) => MkAccount(
      id: j.string('id'),
      platform: j.string('platform'),
      provider: j.string('provider'),
      displayName: j.string('display_name'),
      status: j.string('status'),
      handle: j.optString('handle'),
      lastError: MkError.read(j.optObject('last_error')));
}

class MkProvider {
  const MkProvider(
      {required this.provider,
      required this.label,
      required this.configured,
      required this.missing});
  final String provider, label;
  final bool configured;
  final List<String> missing;
}

class MkSetup {
  const MkSetup(
      {required this.providers,
      required this.canManage,
      required this.canWrite,
      required this.limits});
  final List<MkProvider> providers;
  final bool canManage, canWrite;

  /// Platform -> text limit, for the length counter in the composer.
  final Map<String, int> limits;

  factory MkSetup.fromJson(JsonReader j) => MkSetup(
          providers: j.objects(
              'providers',
              (p) => MkProvider(
                  provider: p.string('provider'),
                  label: p.string('label'),
                  configured: p.boolean('configured'),
                  missing: p.strings('missing'))),
          canManage: j.optBool('can_manage') ?? false,
          canWrite: j.optBool('can_write') ?? false,
          limits: {
            for (final p in j.objects('platforms', (p) => p))
              p.string('platform'): p.integer('text_limit')
          });
}

class MkIssue {
  const MkIssue(this.code, this.severity, this.message);
  final String code, severity, message;
}

class MkPost {
  const MkPost(
      {required this.id,
      required this.platform,
      required this.language,
      required this.status,
      required this.text,
      required this.hashtags,
      required this.accountName,
      required this.length,
      required this.limit,
      required this.issues,
      required this.createdAt,
      this.scheduledAt,
      this.publishedAt,
      this.externalUrl,
      this.approvedHash,
      this.lastError,
      this.approval,
      this.untrusted = false,
      this.reach,
      this.engagement});
  final String id, platform, language, status, text, accountName;
  final List<String> hashtags;
  final int length, limit;
  final List<MkIssue> issues;
  final DateTime createdAt;
  final DateTime? scheduledAt, publishedAt;
  final String? externalUrl, approvedHash;
  final MkError? lastError;
  final WorkspaceApproval? approval;
  final bool untrusted;
  final int? reach, engagement;

  bool get hasErrors => issues.any((issue) => issue.severity == 'error');
  bool get canEdit => const [
        'draft',
        'pending_approval',
        'scheduled',
        'failed',
        'cancelled'
      ].contains(status);
  bool get canSchedule =>
      const ['draft', 'failed', 'cancelled'].contains(status) && !hasErrors;
  bool get canCancel => status == 'scheduled' || status == 'pending_approval';
  bool get canPublishNow => status == 'scheduled' && approvedHash != null;
  bool get canRetry => status == 'failed' && approvedHash != null;

  /// The text as the platform receives it: copy, then missing hashtags.
  String get composed {
    final present = RegExp(r'#([\p{L}\p{N}_]{1,140})', unicode: true)
        .allMatches(text)
        .map((m) => m.group(1)!.toLowerCase())
        .toSet();
    final extra = hashtags
        .where((tag) => !present.contains(tag.toLowerCase()))
        .map((tag) => '#$tag');
    return extra.isEmpty ? text : '$text\n\n${extra.join(' ')}';
  }

  factory MkPost.fromJson(JsonReader j) {
    final metrics = j.optObject('metrics');
    final approval = j.optObject('approval');
    return MkPost(
        id: j.string('id'),
        platform: j.string('platform'),
        language: j.string('language'),
        status: j.string('status'),
        text: j.optString('text') ?? '',
        hashtags: j.optStrings('hashtags') ?? const [],
        accountName: j.optString('account_name') ?? '',
        length: j.optInt('length') ?? 0,
        limit: j.optInt('limit') ?? 0,
        issues: j.optObjects(
            'issues',
            (i) => MkIssue(
                i.string('code'), i.string('severity'), i.string('message'))),
        createdAt: j.date('created_at'),
        scheduledAt: j.optDate('scheduled_at'),
        publishedAt: j.optDate('published_at'),
        externalUrl: j.optString('external_url'),
        approvedHash: j.optString('approved_hash'),
        lastError: MkError.read(j.optObject('last_error')),
        approval: approval == null ? null : workspaceApprovalFromJson(approval),
        untrusted: j.optBool('untrusted_source') ?? false,
        reach: metrics?.optInt('reach') ?? metrics?.optInt('views'),
        engagement: metrics?.optInt('engagement'));
  }
}

class MkCampaign {
  const MkCampaign(
      {required this.id,
      required this.name,
      required this.status,
      required this.autoPost,
      required this.counts,
      this.product = '',
      this.approval});
  final String id, name, status, product;
  final bool autoPost;
  final Map<String, int> counts;
  final WorkspaceApproval? approval;

  factory MkCampaign.fromJson(JsonReader j) {
    final counts = j.optMap('counts') ?? const {};
    final approval = j.optObject('auto_post_approval');
    return MkCampaign(
        id: j.string('id'),
        name: j.string('name'),
        status: j.string('status'),
        product: j.optString('product') ?? '',
        autoPost: j.optBool('auto_post') ?? false,
        counts: {
          for (final entry in counts.entries)
            if (entry.value is num) entry.key: (entry.value as num).toInt()
        },
        approval:
            approval == null ? null : workspaceApprovalFromJson(approval));
  }
}

class MkTotals {
  const MkTotals(
      {required this.posts,
      required this.reach,
      required this.impressions,
      required this.engagement,
      required this.clicks,
      this.rate});
  final int posts, reach, impressions, engagement, clicks;
  final double? rate;
  factory MkTotals.fromJson(JsonReader j) => MkTotals(
      posts: j.optInt('posts') ?? 0,
      reach: (j.optInt('reach') ?? 0) == 0
          ? j.optInt('views') ?? 0
          : j.optInt('reach') ?? 0,
      impressions: j.optInt('impressions') ?? 0,
      engagement: j.optInt('engagement') ?? 0,
      clicks: j.optInt('clicks') ?? 0,
      rate: j.optNumber('engagement_rate'));
}

class MkSummary {
  const MkSummary(
      {required this.totals,
      required this.platforms,
      required this.upcoming,
      required this.pending,
      required this.failed});
  final MkTotals totals;
  final Map<String, MkTotals> platforms;
  final int upcoming, pending, failed;
  factory MkSummary.fromJson(JsonReader j) => MkSummary(
      totals: MkTotals.fromJson(j.object('totals')),
      platforms: {
        for (final p in j.objects('platforms', (p) => p))
          p.string('platform'): MkTotals.fromJson(p)
      },
      upcoming: j.optInt('upcoming') ?? 0,
      pending: j.optInt('pending_approval') ?? 0,
      failed: j.optInt('failed') ?? 0);
}

class MkReport {
  const MkReport(
      {required this.headline,
      required this.lines,
      this.language = 'en',
      this.emailAvailable = false});
  final String headline;
  final List<String> lines;
  final String language;

  /// A connected mailbox can carry the report (sending still waits for approval).
  final bool emailAvailable;
  factory MkReport.fromJson(JsonReader j) => MkReport(
      headline: j.string('headline'),
      lines: j.strings('lines'),
      language: j.optString('language') ?? 'en',
      emailAvailable: j.optMap('delivery')?['email'] == 'available');
}

/// One channel's answer to "send the weekly report": `requested` means it
/// waits for approval; anything else carries the reason in [detail].
class MkDelivery {
  const MkDelivery({required this.outcome, this.detail = ''});
  final String outcome;
  final String detail;
  bool get requested => outcome == 'requested';
  factory MkDelivery.fromJson(JsonReader j) => MkDelivery(
      outcome: j.string('outcome'), detail: j.optString('detail') ?? '');
}

/// Hashtags typed with or without #, separated by spaces or commas.
List<String> parseHashtags(String value) {
  final tags = <String>[];
  for (final raw in value.split(RegExp(r'[\s,،]{1,50}'))) {
    final tag = raw.trim().replaceFirst(RegExp(r'^#{1,20}'), '');
    if (tag.isNotEmpty &&
        !tags.any((existing) => existing.toLowerCase() == tag.toLowerCase())) {
      tags.add(tag);
    }
  }
  return tags;
}
