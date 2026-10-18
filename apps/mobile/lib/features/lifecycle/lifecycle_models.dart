import '../../data/json_read.dart';
import 'lifecycle_strings.dart';

/// One organisation export or deletion job (docs/organisation-lifecycle.md).
class LifecycleJob {
  const LifecycleJob({
    required this.id,
    required this.kind,
    required this.status,
    this.workspaceId,
    this.requestedBy,
    this.requestedAt,
    this.purgeAfter,
    this.startedAt,
    this.finishedAt,
    this.cancelledAt,
    this.cancelledBy,
    this.expiresAt,
    this.archiveBytes,
    this.archiveSha256,
    this.rowCount,
    this.fileCount,
    this.error,
  });

  factory LifecycleJob.fromJson(JsonReader json) => LifecycleJob(
        id: json.string('id'),
        kind: json.optString('kind') ?? '',
        status: json.optString('status') ?? '',
        workspaceId: json.optString('workspace_id'),
        requestedBy: json.optString('requested_by'),
        requestedAt: json.optDate('requested_at'),
        purgeAfter: json.optDate('purge_after'),
        startedAt: json.optDate('started_at'),
        finishedAt: json.optDate('finished_at'),
        cancelledAt: json.optDate('cancelled_at'),
        cancelledBy: json.optString('cancelled_by'),
        expiresAt: json.optDate('expires_at'),
        archiveBytes: json.optInt('archive_bytes'),
        archiveSha256: json.optString('archive_sha256'),
        rowCount: json.optInt('row_count'),
        fileCount: json.optInt('file_count'),
        error: json.optString('error'),
      );

  final String id;

  /// organisation_export, organisation_deletion or workspace_deletion.
  final String kind;

  /// Exports: queued, running, ready, failed, expired. Deletions: scheduled,
  /// cancelled, purging, completed, failed.
  final String status;
  final String? workspaceId;
  final String? requestedBy;
  final DateTime? requestedAt;
  final DateTime? purgeAfter;
  final DateTime? startedAt;
  final DateTime? finishedAt;
  final DateTime? cancelledAt;
  final String? cancelledBy;
  final DateTime? expiresAt;
  final int? archiveBytes;
  final String? archiveSha256;
  final int? rowCount;
  final int? fileCount;
  final String? error;

  bool get inProgress => status == 'queued' || status == 'running';
  bool get isReady => status == 'ready';
  bool get isScheduled => status == 'scheduled';
  bool get isPurging => status == 'purging';
  bool get isFailed => status == 'failed';

  /// The status in words, for the export list and the deletion banners.
  String get statusLabel => statusLabelIn(LifecycleStrings.english);

  String statusLabelIn(LifecycleStrings s) => switch (status) {
        'queued' ||
        'running' ||
        'ready' ||
        'failed' ||
        'expired' ||
        'scheduled' ||
        'cancelled' ||
        'purging' ||
        'completed' =>
          s.t('status.$status'),
        '' => '-',
        _ => status,
      };
}

/// `GET /api/v1/organization/lifecycle`: what the caller may do, the texts to
/// type, the newest exports and the open deletions.
class LifecycleSummary {
  const LifecycleSummary({
    required this.canManageOrganisation,
    required this.canDeleteWorkspace,
    required this.organisationConfirmation,
    required this.workspaceConfirmation,
    required this.coolingOffHours,
    required this.exportLinkTtlSeconds,
    required this.exportRetentionHours,
    required this.exports,
    this.organisationDeletion,
    this.workspaceDeletion,
  });

  factory LifecycleSummary.fromJson(Map<String, Object?> body) {
    final json = JsonReader(body);
    return LifecycleSummary(
      canManageOrganisation: json.optBool('can_manage_organisation') ?? false,
      canDeleteWorkspace: json.optBool('can_delete_workspace') ?? false,
      organisationConfirmation: json.optString('organisation_confirmation'),
      workspaceConfirmation: json.optString('workspace_confirmation'),
      coolingOffHours: json.optInt('cooling_off_hours') ?? 168,
      exportLinkTtlSeconds: json.optInt('export_link_ttl_seconds') ?? 900,
      exportRetentionHours: json.optInt('export_retention_hours') ?? 168,
      exports: json.optObjects('exports', LifecycleJob.fromJson),
      organisationDeletion: json.optObject('organisation_deletion') == null
          ? null
          : LifecycleJob.fromJson(json.object('organisation_deletion')),
      workspaceDeletion: json.optObject('workspace_deletion') == null
          ? null
          : LifecycleJob.fromJson(json.object('workspace_deletion')),
    );
  }

  final bool canManageOrganisation;
  final bool canDeleteWorkspace;

  /// The organisation id, typed to schedule deleting the organisation.
  final String? organisationConfirmation;

  /// The workspace name, typed to schedule deleting the workspace.
  final String? workspaceConfirmation;
  final int coolingOffHours;
  final int exportLinkTtlSeconds;
  final int exportRetentionHours;
  final List<LifecycleJob> exports;
  final LifecycleJob? organisationDeletion;
  final LifecycleJob? workspaceDeletion;

  /// True while an export is queued or running: the page polls.
  bool get exportInProgress => exports.any((job) => job.inProgress);
}

/// A signed download link for a ready export.
class LifecycleExportLink {
  const LifecycleExportLink({required this.url, this.expiresAt});
  final Uri url;
  final DateTime? expiresAt;
}

/// The typed text must match exactly: no trimming, no case folding.
bool confirmationMatches(String? expected, String typed) =>
    expected != null && expected.isNotEmpty && typed == expected;

/// "168 hours" reads as "7 days"; other values stay in hours.
String formatHours(int hours, [LifecycleStrings s = LifecycleStrings.english]) {
  if (hours >= 24 && hours % 24 == 0) return s.count('day', hours ~/ 24);
  return s.count('hour', hours);
}

/// "25 Oct 2026, 09:05" in the device's time zone (Western digits in both
/// languages); `-` when unknown.
String formatWhen(DateTime? value,
    [LifecycleStrings s = LifecycleStrings.english]) {
  if (value == null) return '-';
  final local = value.toLocal();
  final months = s.t('months').split(',');
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.day} ${months[local.month - 1]} ${local.year}, '
      '${two(local.hour)}:${two(local.minute)}';
}

String formatArchiveSize(int? value) {
  if (value == null) return '-';
  if (value < 1024) return '$value B';
  if (value < 1024 * 1024) return '${(value / 1024).toStringAsFixed(1)} KB';
  if (value < 1024 * 1024 * 1024) {
    return '${(value / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  return '${(value / (1024 * 1024 * 1024)).toStringAsFixed(2)} GB';
}
