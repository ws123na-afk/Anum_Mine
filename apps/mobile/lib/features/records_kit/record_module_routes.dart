import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../admin/owned_controller.dart';
import '../attendance/attendance_controller.dart';
import '../attendance/attendance_data.dart';
import '../attendance/attendance_screen.dart';
import '../books/books_controller.dart';
import '../books/books_data.dart';
import '../books/books_screen.dart';
import '../meetings/meetings_repository.dart' show MeetingUploadTransfer;
import '../purchases/purchases_controller.dart';
import '../purchases/purchases_data.dart';
import '../purchases/purchases_screen.dart';
import '../purchases/vault_receipts.dart';
import '../attendance/attendance_files.dart';
import '../vault/vault_controller.dart';
import '../vault/vault_data.dart';
import '../vault/vault_screen.dart';
import '../vehicles/vehicles_controller.dart';
import '../vehicles/vehicles_data.dart';
import '../vehicles/vehicles_screen.dart';

/// The routes of the five record modules on the phone, each opened from
/// the Menu with a fresh controller that lives as long as its page:
///
/// * `/vault`: the Document vault (docs/documents.md#mobile)
/// * `/attendance`: work attendance and daily reports (docs/attendance.md#mobile)
/// * `/vehicles`: vehicles, fuel and mileage (docs/vehicles.md#mobile)
/// * `/purchases`: purchases, deliveries and returns (docs/purchases.md#mobile)
/// * `/business/books`: the P&L and bills (docs/business-books.md#mobile)
class RecordModuleRoutes {
  RecordModuleRoutes({required this.api, this.uploads, this.saver});

  final AnumApiClient api;

  /// Streams files from the phone to the vault.
  final MeetingUploadTransfer? uploads;

  /// Saves a vault document where the person chooses.
  final VaultFileSaver? saver;

  static const vault = '/vault';
  static const attendance = '/attendance';
  static const vehicles = '/vehicles';
  static const purchases = '/purchases';
  static const books = '/business/books';
  static const all = [vault, attendance, vehicles, purchases, books];

  /// The route for [settings], wrapped by [guard] (the signed-in check), or
  /// null when the name is not one of these.
  Route<void>? route(
      RouteSettings settings, Widget Function(Widget child) guard) {
    final Widget Function()? build = switch (settings.name) {
      vault => () => OwnedController<VaultController>(
            create: () => VaultController(
                ApiVaultRepository(api, transfer: uploads, saver: saver)),
            builder: (_, c) => VaultScreen(controller: c),
          ),
      attendance => () => OwnedController<AttendanceController>(
            create: () => AttendanceController(ApiAttendanceRepository(api)),
            builder: (_, c) => AttendanceScreen(
                controller: c,
                files:
                    uploads == null ? null : UploadAttendanceFiles(uploads!)),
          ),
      vehicles => () => OwnedController<VehiclesController>(
            create: () => VehiclesController(ApiVehiclesRepository(api)),
            builder: (_, c) => VehiclesScreen(controller: c),
          ),
      purchases => () => OwnedController<PurchasesController>(
            create: () => PurchasesController(ApiPurchasesRepository(api)),
            builder: (_, c) => PurchasesScreen(
                controller: c,
                receipts: VaultPurchaseReceipts(
                    ApiVaultRepository(api, transfer: uploads, saver: saver))),
          ),
      books => () => OwnedController<BooksController>(
            create: () => BooksController(ApiBooksRepository(api)),
            builder: (_, c) => BooksScreen(controller: c),
          ),
      _ => null,
    };
    if (build == null) return null;
    return MaterialPageRoute<void>(
        settings: settings, builder: (_) => guard(build()));
  }
}
