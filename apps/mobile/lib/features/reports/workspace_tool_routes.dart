import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../calendar/calendar_controller.dart';
import '../calendar/calendar_repository.dart';
import '../calendar/calendar_screen.dart';
import '../calendar/event_screen.dart';
import '../email/email_controller.dart';
import '../email/email_summary.dart';
import '../email/email_summary_screen.dart';
import '../library/library_repository.dart';
import '../mail_center/mail_center_controller.dart';
import '../mail_center/mail_center_repository.dart';
import '../mail_center/mail_center_screens.dart';
import '../mail_center/mail_center_transfer.dart';
import '../library/library_screens.dart';

/// The routes of the ANUM 360 command-result screens: reports (the
/// library's screens, which draw typed sections), the calendar with next
/// actions and the email summary. The voice call is the Voice tab's Call.
///
/// * `/reports`, `/reports/<id>`
/// * `/calendar`, `/calendar/events/<id>`
/// * `/email/summary` (`?tab=urgent|replied|other`)
/// * `/mail`: the Email Center on the workspace's own mail server
///   (docs/email.md#email-center)
class WorkspaceToolRoutes {
  WorkspaceToolRoutes(
      {required this.api,
      required this.library,
      required this.email,
      this.mailTransfer = const NoMailFileTransfer()});

  final AnumApiClient api;

  /// Email Center attachments: picking, uploading, downloading, saving.
  final MailFileTransfer mailTransfer;
  final LibraryRepository library;
  final EmailController email;

  static const reports = '/reports';
  static const calendar = '/calendar';
  static const emailSummary = '/email/summary';
  static const mailCenter = '/mail';

  late final calendarController =
      CalendarController(ApiCalendarRepository(api));
  late final mailCenterController = MailCenterController(
      ApiMailCenterRepository(api),
      transfer: mailTransfer);

  /// The route for [settings], wrapped by [guard] (the signed-in check), or
  /// null when the name is not one of these.
  Route<void>? route(
      RouteSettings settings, Widget Function(Widget child) guard) {
    final uri = Uri.tryParse(settings.name ?? '');
    if (uri == null) return null;
    final segments = uri.pathSegments;
    Widget? screen;
    if (uri.path == reports) {
      screen = ReportsScreen(repository: library);
    } else if (segments.length == 2 && segments.first == 'reports') {
      screen = ReportDetailScreen(repository: library, id: segments[1]);
    } else if (uri.path == calendar) {
      screen = CalendarScreen(controller: calendarController);
    } else if (segments.length == 3 &&
        segments[0] == 'calendar' &&
        segments[1] == 'events') {
      screen = EventScreen(
          controller: calendarController,
          eventId: segments[2],
          fromCalendar: false);
    } else if (uri.path == emailSummary) {
      screen = EmailSummaryScreen(
        controller: EmailSummaryController(ApiEmailSummaryRepository(api),
            tab: SummaryTabQuery.parse(uri.queryParameters['tab'])),
        inbox: email,
      );
    } else if (uri.path == mailCenter) {
      screen = MailCenterScreen(controller: mailCenterController);
    }
    if (screen == null) return null;
    final child = screen;
    return MaterialPageRoute<void>(
        settings: settings, builder: (_) => guard(child));
  }

  void reset() {
    calendarController.events = const [];
    calendarController.steps = const [];
    mailCenterController.reset();
  }

  void dispose() {
    calendarController.dispose();
    mailCenterController.dispose();
  }
}
