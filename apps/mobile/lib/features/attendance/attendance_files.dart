import '../../data/json_read.dart';
import '../meetings/meetings_repository.dart';
import '../vault/vault_transfer.dart';
import '../workspace/files_repository.dart' show UploadCancellation;
import 'attendance_data.dart';

/// Files for a daily report: picks a photo or document on the phone and
/// uploads it to the workspace's Files (`POST /api/v1/files`, the app's
/// streaming upload), as the web report does (docs/attendance.md#daily-reports).
class UploadAttendanceFiles implements AttendanceFiles {
  const UploadAttendanceFiles(this.transfer, {this.picker = pickVaultFile});
  final MeetingUploadTransfer transfer;
  final VaultFilePicker picker;

  @override
  Future<({String id, String name})?> pickAndUpload() async {
    final picked = await picker(const []);
    if (picked == null) return null;
    final (path, name, _) = picked;
    final body = JsonReader(await transfer.uploadTo(
      'api/v1/files',
      {'name': name},
      path,
      onProgress: (_, __) {},
      cancellation: UploadCancellation(),
    ));
    return (id: body.string('id'), name: body.optString('name') ?? name);
  }
}
