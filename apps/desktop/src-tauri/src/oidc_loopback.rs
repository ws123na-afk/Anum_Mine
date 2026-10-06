//! System-browser sign-in with an RFC 8252 loopback redirect.
//!
//! The web client (apps/web/src/lib/auth.ts) builds the authorization request and validates the
//! response (state, PKCE, nonce). This module only provides what a webview cannot: a one-shot
//! HTTP listener on `127.0.0.1` with an ephemeral port, and opening the system browser.
//!
//! 1. `oidc_loopback_listen` binds `127.0.0.1:0` and returns the port, so the client can use
//!    `http://127.0.0.1:<port>/callback` as the redirect URI of this request.
//! 2. `oidc_loopback_authorize` opens the authorization URL in the system browser and waits for
//!    the browser's `GET /callback?...`, answers it with a static page, and returns the full
//!    callback URL to the client. It gives up after ten minutes (the client's pending-request
//!    lifetime) or when a newer sign-in starts.
//!
//! The listener binds the loopback interface only, serves exactly one callback, and never
//! interprets the query: a forged request from another local process fails the client's state
//! check. See docs/desktop.md, "Sign-In".

use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, State, Url};
use tauri_plugin_opener::OpenerExt;

pub const CALLBACK_PATH: &str = "/callback";
const WAIT_LIMIT: Duration = Duration::from_secs(10 * 60);
const REQUEST_LIMIT: usize = 16 * 1024;

#[derive(Default)]
pub struct LoopbackState {
    /// Bumped by every new sign-in; a waiting listener with an older value stops.
    generation: Arc<AtomicU64>,
    listener: Mutex<Option<(u64, TcpListener)>>,
}

#[tauri::command]
pub fn oidc_loopback_listen(state: State<'_, LoopbackState>) -> Result<u16, String> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("Cannot open the sign-in listener: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| error.to_string())?
        .port();
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    *state
        .listener
        .lock()
        .map_err(|_| "Sign-in state is unavailable")? = Some((generation, listener));
    Ok(port)
}

#[tauri::command]
pub async fn oidc_loopback_authorize(
    app: AppHandle,
    state: State<'_, LoopbackState>,
    authorization_url: String,
) -> Result<String, String> {
    validate_authorization_url(&authorization_url)?;
    let (generation, listener) = state
        .listener
        .lock()
        .map_err(|_| "Sign-in state is unavailable")?
        .take()
        .ok_or("No sign-in listener is open")?;
    let port = listener
        .local_addr()
        .map_err(|error| error.to_string())?
        .port();
    listener
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    app.opener()
        .open_url(authorization_url, None::<&str>)
        .map_err(|error| format!("Cannot open the system browser: {error}"))?;

    let current = state.generation.clone();
    let target = tauri::async_runtime::spawn_blocking(move || {
        wait_for_callback(&listener, generation, &current)
    })
    .await
    .map_err(|error| error.to_string())??;

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
    Ok(format!("http://127.0.0.1:{port}{target}"))
}

/// Opens a provider page that needs no callback, such as RP-initiated logout.
#[tauri::command]
pub fn oidc_open_browser(app: AppHandle, url: String) -> Result<(), String> {
    validate_authorization_url(&url)?;
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|error| format!("Cannot open the system browser: {error}"))
}

/// Only web URLs go to the browser; plain HTTP only for a local identity provider.
pub fn validate_authorization_url(value: &str) -> Result<(), String> {
    let url = Url::parse(value).map_err(|_| "The authorization URL is invalid".to_string())?;
    let local = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    match url.scheme() {
        "https" => Ok(()),
        "http" if local => Ok(()),
        _ => Err("The authorization URL must use HTTPS".into()),
    }
}

fn wait_for_callback(
    listener: &TcpListener,
    generation: u64,
    current: &AtomicU64,
) -> Result<String, String> {
    let deadline = Instant::now() + WAIT_LIMIT;
    loop {
        if current.load(Ordering::SeqCst) != generation {
            return Err("Sign-in was restarted".into());
        }
        if Instant::now() >= deadline {
            return Err("Sign-in timed out".into());
        }
        match listener.accept() {
            Ok((stream, _)) => {
                if let Some(target) = serve(stream) {
                    return Ok(target);
                }
            }
            Err(error) if error.kind() == ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(100))
            }
            Err(error) => return Err(format!("Sign-in listener failed: {error}")),
        }
    }
}

/// Answers one connection. Returns the request target when it is the callback.
fn serve(mut stream: TcpStream) -> Option<String> {
    let _ = stream.set_nonblocking(false);
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let mut request = Vec::new();
    let mut buffer = [0_u8; 2048];
    while !request.windows(4).any(|window| window == b"\r\n\r\n") && request.len() < REQUEST_LIMIT {
        match stream.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(read) => request.extend_from_slice(&buffer[..read]),
        }
    }
    let target = callback_target(&String::from_utf8_lossy(&request));
    let (status, body) = match target {
        Some(_) => ("200 OK", "<!doctype html><meta charset=\"utf-8\"><title>ANUM</title><p>Sign-in finished. You can close this tab and return to ANUM.</p>"),
        None => ("404 Not Found", "<!doctype html><meta charset=\"utf-8\"><title>ANUM</title><p>Not found.</p>"),
    };
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
    let _ = stream.flush();
    target
}

/// The target of a `GET /callback` or `GET /callback?...` request line, else None.
pub fn callback_target(request: &str) -> Option<String> {
    let mut parts = request.lines().next()?.split_whitespace();
    let (method, target) = (parts.next()?, parts.next()?);
    let rest = target.strip_prefix(CALLBACK_PATH)?;
    (method == "GET" && (rest.is_empty() || rest.starts_with('?'))).then(|| target.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_the_callback_path() {
        assert_eq!(
            callback_target("GET /callback?code=a&state=b HTTP/1.1\r\nHost: x\r\n\r\n").as_deref(),
            Some("/callback?code=a&state=b")
        );
        assert_eq!(
            callback_target("GET /callback HTTP/1.1\r\n\r\n").as_deref(),
            Some("/callback")
        );
        assert_eq!(callback_target("GET /favicon.ico HTTP/1.1\r\n\r\n"), None);
        assert_eq!(
            callback_target("GET /callbackx?code=a HTTP/1.1\r\n\r\n"),
            None
        );
        assert_eq!(
            callback_target("POST /callback?code=a HTTP/1.1\r\n\r\n"),
            None
        );
        assert_eq!(callback_target(""), None);
    }

    #[test]
    fn opens_only_web_urls() {
        assert!(validate_authorization_url(
            "https://id.example.com/realms/anum/protocol/openid-connect/auth?x=1"
        )
        .is_ok());
        assert!(validate_authorization_url(
            "http://localhost:8080/realms/anum/protocol/openid-connect/auth"
        )
        .is_ok());
        assert!(validate_authorization_url("http://id.example.com/auth").is_err());
        assert!(validate_authorization_url("file:///etc/passwd").is_err());
        assert!(validate_authorization_url("not a url").is_err());
    }

    #[test]
    fn serves_one_callback_on_loopback() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();
        let current = AtomicU64::new(1);
        let client = std::thread::spawn(move || {
            let mut stray = TcpStream::connect(("127.0.0.1", port)).unwrap();
            stray
                .write_all(b"GET /favicon.ico HTTP/1.1\r\n\r\n")
                .unwrap();
            let mut reply = String::new();
            stray.read_to_string(&mut reply).unwrap();
            assert!(reply.starts_with("HTTP/1.1 404"));
            let mut stream = TcpStream::connect(("127.0.0.1", port)).unwrap();
            stream
                .write_all(b"GET /callback?code=c&state=s HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                .unwrap();
            let mut reply = String::new();
            stream.read_to_string(&mut reply).unwrap();
            assert!(reply.starts_with("HTTP/1.1 200 OK"));
        });
        assert_eq!(
            wait_for_callback(&listener, 1, &current).unwrap(),
            "/callback?code=c&state=s"
        );
        client.join().unwrap();
    }

    #[test]
    fn a_newer_sign_in_stops_the_wait() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        assert_eq!(
            wait_for_callback(&listener, 1, &AtomicU64::new(2)).unwrap_err(),
            "Sign-in was restarted"
        );
    }
}
