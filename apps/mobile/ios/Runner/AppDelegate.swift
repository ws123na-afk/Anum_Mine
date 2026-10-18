import ContactsUI
import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private let privacyCover = PrivacyCover()
  private let contactPicker = ContactPicker()

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    privacyCover.start()
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    if let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "AnumContactPicker") {
      contactPicker.attach(to: registrar.messenger())
    }
  }
}

/// Phone calls (docs/calls.md#mobile): "Pick from phone" shows the system contact picker for
/// one phone number. CNContactPickerViewController runs out of process and returns only the
/// contact the person picks, so the app needs no contacts permission and reads nothing else.
final class ContactPicker: NSObject, CNContactPickerDelegate {
  private var pending: FlutterResult?

  func attach(to messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: "anum/contact_picker", binaryMessenger: messenger)
    channel.setMethodCallHandler { [weak self] call, result in
      guard call.method == "pickContact" else {
        result(FlutterMethodNotImplemented)
        return
      }
      self?.present(result)
    }
  }

  private func present(_ result: @escaping FlutterResult) {
    guard pending == nil else {
      result(FlutterError(code: "busy", message: "The contact picker is already open.", details: nil))
      return
    }
    guard let root = UIApplication.shared.connectedScenes
      .compactMap({ ($0 as? UIWindowScene)?.keyWindow?.rootViewController }).first
    else {
      result(FlutterError(code: "unavailable", message: "The app window is not ready.", details: nil))
      return
    }
    pending = result
    let picker = CNContactPickerViewController()
    picker.delegate = self
    picker.displayedPropertyKeys = [CNContactPhoneNumbersKey]
    picker.predicateForSelectionOfProperty = NSPredicate(format: "key == 'phoneNumbers'")
    var top = root
    while let presented = top.presentedViewController { top = presented }
    top.present(picker, animated: true)
  }

  func contactPicker(_ picker: CNContactPickerViewController, didSelect contactProperty: CNContactProperty) {
    let phone = (contactProperty.value as? CNPhoneNumber)?.stringValue ?? ""
    let name = CNContactFormatter.string(from: contactProperty.contact, style: .fullName) ?? ""
    pending?(["name": name, "phone": phone])
    pending = nil
  }

  func contactPickerDidCancel(_ picker: CNContactPickerViewController) {
    pending?(nil)
    pending = nil
  }
}

/// Screen privacy (owner decision, 12 October 2026; docs/mobile.md#screen-privacy).
///
/// iOS offers no supported way to block screenshots. This covers the app's windows with a
/// plain view while the scene is inactive, so the app-switcher snapshot shows no workspace
/// content, and while the screen is being recorded, mirrored or shared (`isCaptured`).
/// It listens to notifications only, so it does not depend on Flutter's scene delegate.
final class PrivacyCover: NSObject {
  private var covers: [ObjectIdentifier: UIView] = [:]
  private var inactive = false

  func start() {
    let center = NotificationCenter.default
    center.addObserver(
      self, selector: #selector(willDeactivate), name: UIScene.willDeactivateNotification, object: nil)
    center.addObserver(
      self, selector: #selector(didActivate), name: UIScene.didActivateNotification, object: nil)
    center.addObserver(
      self, selector: #selector(captureChanged), name: UIScreen.capturedDidChangeNotification,
      object: nil)
  }

  @objc private func willDeactivate(_ notification: Notification) {
    inactive = true
    update()
  }

  @objc private func didActivate(_ notification: Notification) {
    inactive = false
    update()
  }

  @objc private func captureChanged(_ notification: Notification) {
    update()
  }

  private var isCaptured: Bool {
    UIApplication.shared.connectedScenes.contains { scene in
      guard let windowScene = scene as? UIWindowScene else { return false }
      if #available(iOS 17.0, *) {
        return windowScene.traitCollection.sceneCaptureState == .active
      }
      return windowScene.screen.isCaptured
    }
  }

  private func update() {
    let hide = inactive || isCaptured
    for scene in UIApplication.shared.connectedScenes {
      guard let windowScene = scene as? UIWindowScene else { continue }
      for window in windowScene.windows {
        let key = ObjectIdentifier(window)
        if hide {
          guard covers[key] == nil else { continue }
          let cover = UIView(frame: window.bounds)
          cover.autoresizingMask = [.flexibleWidth, .flexibleHeight]
          // AnumPalette.dark.background (#070B1A).
          cover.backgroundColor = UIColor(red: 7 / 255, green: 11 / 255, blue: 26 / 255, alpha: 1)
          cover.isAccessibilityElement = false
          window.addSubview(cover)
          covers[key] = cover
        } else if let cover = covers.removeValue(forKey: key) {
          cover.removeFromSuperview()
        }
      }
    }
    if !hide {
      covers.values.forEach { $0.removeFromSuperview() }
      covers.removeAll()
    }
  }
}
