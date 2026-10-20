import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import 'marketing_ops_models.dart';
import 'marketing_repository.dart';

/// Why an AI assist call did not give an answer, so the panel can say what to
/// do about it.
enum MkAssistProblem { budget, denied, unavailable, other }

enum MkLoad { idle, loading, ready, denied, error }

/// AI assist, brand voice, the media library and best times for the
/// Marketing screen (docs/marketing.md). Each section has its own state so
/// one failing call never blanks the rest.
class MarketingOps extends ChangeNotifier {
  MarketingOps(this.repository);
  final MarketingRepository repository;
  static const mediaPage = 50;
  static const mediaMax = 500;

  // -- AI assist --------------------------------------------------------------
  MkIdeas? ideas;
  bool assistBusy = false;
  MkAssistProblem? assistProblem;
  String? assistMessage;

  /// Which call the problem belongs to, so it shows beside that control.
  bool assistProblemIsTags = false;

  void _assistFailed(Object error) {
    if (error is ApiException) {
      assistMessage = error.readable;
      assistProblem = error.isBudgetExceeded
          ? MkAssistProblem.budget
          : error.isPermissionDenied
              ? MkAssistProblem.denied
              : error.statusCode == 503
                  ? MkAssistProblem.unavailable
                  : MkAssistProblem.other;
    } else {
      assistMessage = null;
      assistProblem = MkAssistProblem.other;
    }
  }

  void clearAssistProblem() {
    assistProblem = null;
    assistMessage = null;
    notifyListeners();
  }

  /// Ideas for [brief] (the API needs a brief, product or feature: the
  /// campaign's own are used when [campaignId] is set).
  Future<bool> suggestIdeas(
      {required String brief,
      required List<String> platforms,
      required List<String> languages,
      String? campaignId,
      String source = ''}) async {
    assistProblemIsTags = false;
    assistBusy = true;
    assistProblem = null;
    assistMessage = null;
    notifyListeners();
    try {
      ideas = await repository.ideas(
          brief: brief,
          platforms: platforms,
          languages: languages,
          campaignId: campaignId,
          source: source);
      return true;
    } catch (e) {
      _assistFailed(e);
      return false;
    } finally {
      assistBusy = false;
      notifyListeners();
    }
  }

  /// Hashtags for [text]; null (with [assistProblem] set) when refused.
  Future<MkHashtags?> suggestHashtags(
      {required String text,
      required String platform,
      required String language}) async {
    assistProblemIsTags = true;
    assistBusy = true;
    assistProblem = null;
    assistMessage = null;
    notifyListeners();
    try {
      return await repository.suggestHashtags(
          text: text, platform: platform, language: language);
    } catch (e) {
      _assistFailed(e);
      return null;
    } finally {
      assistBusy = false;
      notifyListeners();
    }
  }

  // -- Brand voice and defaults -----------------------------------------------
  MkLoad prefsPhase = MkLoad.idle;
  MkPreferences? prefs;
  String? prefsError;
  bool prefsSaving = false;

  Future<void> loadPreferences() async {
    prefsPhase = MkLoad.loading;
    notifyListeners();
    try {
      prefs = await repository.preferences();
      prefsPhase = MkLoad.ready;
      prefsError = null;
    } on ApiException catch (e) {
      prefsPhase = e.isPermissionDenied ? MkLoad.denied : MkLoad.error;
      prefsError = e.readable;
    } catch (_) {
      prefsPhase = MkLoad.error;
      prefsError = null;
    }
    notifyListeners();
  }

  /// Saves; returns null on success or the error (an unknown time zone, a
  /// member without the right) for `MarketingController.describe`.
  Future<Object?> savePreferences(MkPreferences value) async {
    prefsSaving = true;
    notifyListeners();
    try {
      prefs = await repository.savePreferences(value);
      return null;
    } catch (e) {
      return e;
    } finally {
      prefsSaving = false;
      notifyListeners();
    }
  }

  // -- Media library ------------------------------------------------------------
  MkLoad mediaPhase = MkLoad.idle;
  List<MkMediaFile> mediaFiles = const [];
  String mediaQuery = '';
  int mediaLimit = mediaPage;
  String? mediaError;

  /// More files may exist than are shown (the API pages by a growing limit).
  bool get moreMedia =>
      mediaFiles.length >= mediaLimit && mediaLimit < mediaMax;

  Future<void> loadMedia({String? query, bool more = false}) async {
    if (query != null && query != mediaQuery) {
      mediaQuery = query;
      mediaLimit = mediaPage;
    }
    if (more) mediaLimit = (mediaLimit + mediaPage).clamp(mediaPage, mediaMax);
    mediaPhase = MkLoad.loading;
    notifyListeners();
    try {
      mediaFiles = await repository.media(query: mediaQuery, limit: mediaLimit);
      mediaPhase = MkLoad.ready;
      mediaError = null;
    } on ApiException catch (e) {
      mediaPhase = e.isPermissionDenied ? MkLoad.denied : MkLoad.error;
      mediaError = e.readable;
    } catch (_) {
      mediaPhase = MkLoad.error;
      mediaError = null;
    }
    notifyListeners();
  }

  // -- Best times -----------------------------------------------------------------
  MkLoad timesPhase = MkLoad.idle;
  List<MkTimeSlot> slots = const [];
  String? timesError;

  Future<void> loadBestTimes() async {
    timesPhase = MkLoad.loading;
    notifyListeners();
    try {
      slots = await repository.bestTimes();
      timesPhase = MkLoad.ready;
      timesError = null;
    } on ApiException catch (e) {
      timesPhase = e.isPermissionDenied ? MkLoad.denied : MkLoad.error;
      timesError = e.readable;
    } catch (_) {
      timesPhase = MkLoad.error;
      timesError = null;
    }
    notifyListeners();
  }

  /// Forget everything (a workspace switch).
  void reset() {
    ideas = null;
    assistProblem = null;
    assistMessage = null;
    prefsPhase = mediaPhase = timesPhase = MkLoad.idle;
    prefs = null;
    mediaFiles = const [];
    mediaQuery = '';
    mediaLimit = mediaPage;
    slots = const [];
    notifyListeners();
  }
}
