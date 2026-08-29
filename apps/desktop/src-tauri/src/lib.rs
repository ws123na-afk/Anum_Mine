use serde::Serialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const LAUNCHER_SHORTCUT: Shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);

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
                    if shortcut == &LAUNCHER_SHORTCUT && event.state() == ShortcutState::Pressed {
                        if let Some(window) = app.get_webview_window("main") {
                            show_launcher(&window);
                        }
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![desktop_context])
        .setup(|app| {
            install_tray(app.handle())?;
            app.global_shortcut().register(LAUNCHER_SHORTCUT)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running ANUM desktop");
}
