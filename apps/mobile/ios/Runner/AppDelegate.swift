import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  private let privacyCover = PrivacyCover()

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    privacyCover.start()
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
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
