# ANUM Android

Native Kotlin client for mobile task capture, agent-run review, voice entry, and guarded approvals. Agent execution remains on the ANUM API.

## Run

1. Open `apps/android` in Android Studio (JDK 17, Android SDK 35).
2. Optionally set `ANUM_API_URL=https://anum.example.com/` in `local.properties`. The emulator default is `http://10.0.2.2:8000/`.
3. Run the `app` configuration on an API 26+ device or emulator.

The debug build supports the development tenant headers used by the local API. Release builds disable cleartext HTTP and are designed to receive an OIDC access token through `TokenVault`; raw model or provider credentials are never stored on-device.

Microphone access is requested only after the user starts voice capture. Recognized text remains an editable draft until explicitly submitted.
