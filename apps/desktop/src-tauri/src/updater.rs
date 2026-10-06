//! In-app updates (docs/desktop.md, "Updates").
//!
//! The updater plugin is registered only when the build carries an updater configuration:
//! `plugins.updater` with a public key and at least one endpoint, which
//! `scripts/release-config.mjs` writes for release builds from `ANUM_TAURI_UPDATER_PUBKEY` and
//! `ANUM_TAURI_UPDATER_ENDPOINT`. Without it the app never contacts an update server and the tray
//! has no "Check for updates" item. Checks run from Rust only, so the webview gets no updater
//! permission.

use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Update, UpdaterExt};

/// Tray menu item id for a user-requested check.
pub const MENU_ID: &str = "check-for-updates";

/// Whether the build-time `plugins.updater` entry has a public key and an endpoint.
pub fn is_configured(updater: Option<&Value>) -> bool {
    let Some(updater) = updater else {
        return false;
    };
    let has_pubkey = updater
        .get("pubkey")
        .and_then(Value::as_str)
        .is_some_and(|key| !key.trim().is_empty());
    let has_endpoint = updater
        .get("endpoints")
        .and_then(Value::as_array)
        .is_some_and(|endpoints| !endpoints.is_empty());
    has_pubkey && has_endpoint
}

/// Registers the updater plugin when configured. Returns whether updates are enabled.
pub fn register(app: &AppHandle) -> tauri::Result<bool> {
    if !is_configured(app.config().plugins.0.get("updater")) {
        return Ok(false);
    }
    app.plugin(tauri_plugin_updater::Builder::new().build())?;
    Ok(true)
}

/// Who asked for the check: the silent startup check only speaks up when an update exists.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Trigger {
    Startup,
    User,
}

/// Checks for an update in the background and offers to install it.
pub fn check(app: AppHandle, trigger: Trigger) {
    tauri::async_runtime::spawn(async move {
        if let Err(error) = check_and_offer(&app, trigger).await {
            eprintln!("ANUM update check failed: {error}");
            if trigger == Trigger::User {
                message(
                    &app,
                    MessageDialogKind::Error,
                    format!("Could not check for updates: {error}"),
                );
            }
        }
    });
}

async fn check_and_offer(app: &AppHandle, trigger: Trigger) -> tauri_plugin_updater::Result<()> {
    let Some(update) = app.updater()?.check().await? else {
        if trigger == Trigger::User {
            message(
                app,
                MessageDialogKind::Info,
                format!("ANUM {} is up to date.", app.package_info().version),
            );
        }
        return Ok(());
    };
    let handle = app.clone();
    app.dialog()
        .message(format!(
            "ANUM {} is available (you have {}). Install it and restart now?",
            update.version, update.current_version
        ))
        .title("Update available")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install and restart".into(),
            "Later".into(),
        ))
        .show(move |install| {
            if install {
                tauri::async_runtime::spawn(install_update(handle, update));
            }
        });
    Ok(())
}

async fn install_update(app: AppHandle, update: Update) {
    // The signature is verified against the build's public key before anything is installed.
    match update.download_and_install(|_, _| {}, || {}).await {
        Ok(()) => app.restart(),
        Err(error) => message(
            &app,
            MessageDialogKind::Error,
            format!("The update could not be installed: {error}"),
        ),
    }
}

fn message(app: &AppHandle, kind: MessageDialogKind, text: String) {
    app.dialog()
        .message(text)
        .title("ANUM updates")
        .kind(kind)
        .show(|_| {});
}

#[cfg(test)]
mod tests {
    use super::is_configured;
    use serde_json::json;

    #[test]
    fn disabled_without_updater_config() {
        assert!(!is_configured(None));
    }

    #[test]
    fn needs_both_pubkey_and_endpoint() {
        assert!(!is_configured(Some(&json!({ "pubkey": "key" }))));
        assert!(!is_configured(Some(
            &json!({ "endpoints": ["https://u.example/latest.json"] })
        )));
        assert!(!is_configured(Some(
            &json!({ "pubkey": "  ", "endpoints": ["https://u.example/latest.json"] })
        )));
        assert!(!is_configured(Some(
            &json!({ "pubkey": "key", "endpoints": [] })
        )));
    }

    #[test]
    fn enabled_with_pubkey_and_endpoint() {
        assert!(is_configured(Some(
            &json!({ "pubkey": "key", "endpoints": ["https://u.example/latest.json"] })
        )));
    }
}
