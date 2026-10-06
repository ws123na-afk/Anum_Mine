mod oidc_loopback;

use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

fn launcher_shortcut() -> Shortcut {
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopContext {
    platform: &'static str,
    arch: &'static str,
    version: &'static str,
}

#[tauri::command]
fn desktop_context() -> DesktopContext {
    DesktopContext {
        platform: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        version: env!("CARGO_PKG_VERSION"),
    }
}

fn show_launcher(window: &WebviewWindow) {
    let _ = window.show();
    let _ = window.unminimize();
    let _ = window.set_focus();
    let _ = window.emit("anum://open-task-launcher", ());
}

fn install_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open ANUM", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;

    TrayIconBuilder::new()
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    show_launcher(&window);
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut == &launcher_shortcut() && event.state() == ShortcutState::Pressed {
                        if let Some(window) = app.get_webview_window("main") {
                            show_launcher(&window);
                        }
                    }
                })
                .build(),
        )
        .manage(oidc_loopback::LoopbackState::default())
        .invoke_handler(tauri::generate_handler![
            desktop_context,
            oidc_loopback::oidc_loopback_listen,
            oidc_loopback::oidc_loopback_authorize,
            oidc_loopback::oidc_open_browser
        ])
        .setup(|app| {
            install_tray(app.handle())?;
            app.global_shortcut().register(launcher_shortcut())?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running ANUM desktop");
}
