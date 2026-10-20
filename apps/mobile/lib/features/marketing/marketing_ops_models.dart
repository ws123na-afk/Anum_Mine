import '../../data/json_read.dart';
import 'marketing_models.dart';

/// What the Marketing screen reads for AI assist, brand voice, media, live
/// limit checks, link tracking and best times (docs/marketing.md).

/// A file of the workspace's Files that a post may carry (`GET /media`).
class MkMediaFile {
  const MkMediaFile(
      {required this.id,
      required this.name,
      required this.kind,
      required this.contentType,
      required this.sizeBytes});
  final String id, name, kind, contentType;
  final int sizeBytes;

  MkMedia toMedia() => MkMedia(
      fileId: id,
      kind: kind,
      name: name,
      contentType: contentType,
      sizeBytes: sizeBytes);

  factory MkMediaFile.fromJson(JsonReader j) => MkMediaFile(
      id: j.string('id'),
      name: j.optString('name') ?? '',
      kind: j.optString('kind') ?? 'image',
      contentType: j.optString('content_type') ?? '',
      sizeBytes: j.optInt('size_bytes') ?? 0);
}

/// One suggested post: English and Arabic copy with hashtags.
class MkIdea {
  const MkIdea(
      {required this.title,
      required this.angle,
      required this.platform,
      this.en,
      this.ar,
      this.hashtagsEn = const [],
      this.hashtagsAr = const []});
  final String title, angle, platform;
  final String? en, ar;
  final List<String> hashtagsEn, hashtagsAr;

  factory MkIdea.fromJson(JsonReader j) => MkIdea(
      title: j.optString('title') ?? '',
      angle: j.optString('angle') ?? '',
      platform: j.optString('platform') ?? '',
      en: j.optString('en'),
      ar: j.optString('ar'),
      hashtagsEn: j.optStrings('hashtags_en') ?? const [],
      hashtagsAr: j.optStrings('hashtags_ar') ?? const []);
}

/// The ideas and where they came from: `generator` is `model` (written by the
/// connected model) or `template` (built from a template, no model used), and
/// `untrustedSource` is set when pasted outside text shaped the copy.
class MkIdeas {
  const MkIdeas(
      {required this.ideas,
      required this.generator,
      required this.untrustedSource,
      required this.brandVoiceUsed,
      this.note,
      this.bestTimes = const []});
  final List<MkIdea> ideas;
  final String generator;
  final bool untrustedSource, brandVoiceUsed;
  final String? note;
  final List<MkTimeSlot> bestTimes;
  bool get fromModel => generator == 'model';

  factory MkIdeas.fromJson(JsonReader j) => MkIdeas(
      ideas: j.optObjects('ideas', MkIdea.fromJson),
      generator: j.optString('generator') ?? 'template',
      untrustedSource: j.optBool('untrusted_source') ?? false,
      brandVoiceUsed: j.optBool('brand_voice_used') ?? false,
      note: j.optString('note'),
      bestTimes: j.optObjects('best_times', MkTimeSlot.fromJson));
}

class MkHashtags {
  const MkHashtags(
      {required this.hashtags, required this.generator, this.note});
  final List<String> hashtags;
  final String generator;
  final String? note;
  bool get fromModel => generator == 'model';

  factory MkHashtags.fromJson(JsonReader j) => MkHashtags(
      hashtags: j.optStrings('hashtags') ?? const [],
      generator: j.optString('generator') ?? 'template',
      note: j.optString('note'));
}

/// Brand voice and defaults (`/preferences`): owners change them.
class MkPreferences {
  const MkPreferences(
      {required this.brandVoice,
      required this.timezone,
      required this.hashtagsEn,
      required this.hashtagsAr});
  final String brandVoice, timezone;
  final List<String> hashtagsEn, hashtagsAr;

  factory MkPreferences.fromJson(JsonReader j) => MkPreferences(
      brandVoice: j.optString('brand_voice') ?? '',
      timezone: j.optString('timezone') ?? 'Asia/Riyadh',
      hashtagsEn: j.optStrings('default_hashtags_en') ?? const [],
      hashtagsAr: j.optStrings('default_hashtags_ar') ?? const []);
}

/// A good hour to post. `weekday` 0 is Monday; `basis` is `history` (this
/// workspace's own posts) or `default` (Gulf audience defaults).
class MkTimeSlot {
  const MkTimeSlot(
      {required this.weekday,
      required this.hour,
      required this.posts,
      required this.basis,
      this.platform,
      this.rate});
  final int weekday, hour, posts;
  final String basis;
  final String? platform;
  final double? rate;
  bool get fromHistory => basis == 'history';

  factory MkTimeSlot.fromJson(JsonReader j) => MkTimeSlot(
      weekday: j.optInt('weekday') ?? 0,
      hour: j.optInt('hour') ?? 0,
      posts: j.optInt('posts') ?? 0,
      basis: j.optString('basis') ?? 'default',
      platform: j.optString('platform'),
      rate: j.optNumber('engagement_rate'));
}

/// The server's check of a draft against one platform's limits.
class MkValidation {
  const MkValidation(
      {required this.length,
      required this.limit,
      required this.hashtags,
      required this.ok,
      required this.issues});
  final int length, limit, hashtags;
  final bool ok;
  final List<MkIssue> issues;

  factory MkValidation.fromJson(JsonReader j) => MkValidation(
      length: j.optInt('length') ?? 0,
      limit: j.optInt('limit') ?? 0,
      hashtags: j.optInt('hashtags') ?? 0,
      ok: j.optBool('ok') ?? true,
      issues: j.optObjects(
          'issues',
          (i) => MkIssue(
              i.string('code'), i.string('severity'), i.string('message'))));
}

/// What the campaign form sends (create and edit).
class MkCampaignInput {
  const MkCampaignInput(
      {required this.name,
      this.product = '',
      this.objective = '',
      this.brief = '',
      this.startDate,
      this.endDate,
      this.utmCampaign = ''});
  final String name, product, objective, brief, utmCampaign;
  final String? startDate, endDate;

  Map<String, Object?> toJson() => {
        'name': name,
        'product': product,
        'objective': objective,
        'brief': brief,
        'start_date': startDate,
        'end_date': endDate,
        'utm_campaign': utmCampaign,
      };
}

/// The words for [slot] such as "Sunday 21:00"; [weekdays] is Monday first.
String mkSlotLabel(MkTimeSlot slot, List<String> weekdays) {
  final day = weekdays[slot.weekday.clamp(0, weekdays.length - 1)];
  return '$day ${slot.hour.toString().padLeft(2, '0')}:00';
}

/// `2026-11-01` for [value], the shape the API reads for a date.
String mkIsoDate(DateTime value) => '${value.year.toString().padLeft(4, '0')}-'
    '${value.month.toString().padLeft(2, '0')}-'
    '${value.day.toString().padLeft(2, '0')}';
