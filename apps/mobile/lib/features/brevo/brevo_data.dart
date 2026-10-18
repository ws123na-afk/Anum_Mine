import '../../data/api_client.dart';
import '../../data/json_read.dart';

/// Brevo email campaigns (docs/brevo.md): what the phone reads. The phone is
/// read-only here: connecting, drafting and sending happen on the web, and
/// sending always waits for an owner's approval.
class BvStatus {
  const BvStatus(
      {required this.connected,
      required this.state,
      required this.accountEmail,
      required this.company,
      required this.canManage});
  final bool connected;

  /// `connected`, `needs_key`, `error`, or `none` when no key is saved.
  final String state;
  final String accountEmail, company;
  final bool canManage;

  factory BvStatus.fromJson(JsonReader json) {
    final connection = json.optObject('connection');
    return BvStatus(
      connected: json.boolean('connected'),
      state: connection?.string('status') ?? 'none',
      accountEmail: connection?.optString('account_email') ?? '',
      company: connection?.optString('company') ?? '',
      canManage: json.optBool('can_manage') ?? false,
    );
  }
}

class BvStats {
  const BvStats(
      {this.sent = 0,
      this.delivered = 0,
      this.opens = 0,
      this.clicks = 0,
      this.unsubscribed = 0,
      this.bounced = 0,
      this.complaints = 0,
      this.openRate,
      this.clickRate});
  final int sent, delivered, opens, clicks, unsubscribed, bounced, complaints;
  final double? openRate, clickRate;

  factory BvStats.fromJson(JsonReader json) => BvStats(
        sent: json.optInt('sent') ?? 0,
        delivered: json.optInt('delivered') ?? 0,
        opens: json.optInt('unique_views') ?? 0,
        clicks: json.optInt('unique_clicks') ?? 0,
        unsubscribed: json.optInt('unsubscriptions') ?? 0,
        bounced: (json.optInt('hard_bounces') ?? 0) +
            (json.optInt('soft_bounces') ?? 0),
        complaints: json.optInt('complaints') ?? 0,
        openRate: json.optNumber('open_rate'),
        clickRate: json.optNumber('click_rate'),
      );
}

/// One campaign in Brevo. Its name and subject come from outside ANUM: they
/// are shown as plain text only.
class BvCampaign {
  const BvCampaign(
      {required this.id,
      required this.name,
      required this.subject,
      required this.status,
      required this.stats,
      this.sentAt,
      this.scheduledAt});
  final int id;
  final String name, subject, status;
  final BvStats stats;
  final DateTime? sentAt, scheduledAt;

  factory BvCampaign.fromJson(JsonReader json) => BvCampaign(
        id: json.integer('id'),
        name: json.optString('name') ?? '',
        subject: json.optString('subject') ?? '',
        status: json.optString('status') ?? 'unknown',
        stats: json.optObject('stats') == null
            ? const BvStats()
            : BvStats.fromJson(json.object('stats')),
        sentAt: json.optDate('sent_at'),
        scheduledAt: json.optDate('scheduled_at'),
      );
}

class BvPage {
  const BvPage(this.items, this.total, this.offset);
  final List<BvCampaign> items;
  final int total, offset;
}

/// "48.5%", or "-" when nothing was delivered yet.
String bvPercent(double? value) {
  if (value == null) return '-';
  final text = value.toStringAsFixed(1);
  return '${text.endsWith('.0') ? text.substring(0, text.length - 2) : text}%';
}

abstract interface class BrevoRepository {
  Future<BvStatus> status();
  Future<BvPage> campaigns({int offset = 0, int limit = 20});
  Future<BvCampaign> campaign(int id);
}

class ApiBrevoRepository implements BrevoRepository {
  const ApiBrevoRepository(this.api);
  final AnumApiClient api;
  static const _base = '/api/v1/brevo';

  @override
  Future<BvStatus> status() async =>
      BvStatus.fromJson(JsonReader(await api.request('GET', '$_base/status')));

  @override
  Future<BvPage> campaigns({int offset = 0, int limit = 20}) async {
    final json = JsonReader(await api.request(
        'GET', '$_base/campaigns?limit=$limit&offset=$offset'));
    return BvPage(json.objects('items', BvCampaign.fromJson),
        json.integer('total'), json.integer('offset'));
  }

  @override
  Future<BvCampaign> campaign(int id) async => BvCampaign.fromJson(
      JsonReader(await api.request('GET', '$_base/campaigns/$id')));
}
