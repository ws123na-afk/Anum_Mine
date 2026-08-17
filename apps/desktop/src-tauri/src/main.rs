// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri_plugin_dialog::DialogExt;

/// The one native capability this "Now"-scoped shell adds beyond wrapping
/// the web app: a native, user-initiated file picker. Per docs/desktop.md's
/// security model - "Local filesystem access should be user-selected and
/// scoped" - this never grants ANUM broad filesystem access; it only
/// returns the single path the user explicitly chose in a native dialog.
/// What the web app does with that path (e.g. attach it via
/// POST /api/v1/files) still goes through the normal API/policy/audit path,
/// same as any other client - this command does not read file contents.
#[tauri::command]
async fn pick_local_file(app: tauri::AppHandle) -> Option<String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_file(move |file_path| {
        let _ = tx.send(file_path.map(|path| path.to_string()));
    });
    rx.recv().unwrap_or(None)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![pick_local_file])
        .run(tauri::generate_context!())
        .expect("error while running the ANUM desktop shell");
}
