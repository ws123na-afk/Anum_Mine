# In-App Calls Between Teammates

Free voice and video calls between people of the same ANUM workspace, on the web and in the Flutter app. Media goes peer to peer over WebRTC; ANUM's API only rings, carries the call setup messages and keeps a small record of the call. No paid provider is involved: the relay for difficult networks is a self-hosted coturn on the owner's VM.

This is not the phone dialer or provider calling ([Phone calls](calls.md), under `/api/v1/calls` with its own call log) and not the voice assistant ([Voice](voice.md#call-mode)).

## What a person can do

- **Call a teammate.** Web: Team, Directory, then **Call** or **Video** in a person's row (or in their member panel); the **Team calls** tab holds the history. Flutter: Settings or the menu, Team work, **Team calls**, People. Every active member of the workspace can call and be called, viewers included.
- **Answer or decline.** An incoming call shows the caller's name, voice or video, and **Answer**, **Answer with video** (video calls) and **Decline**, above whatever screen is open. Every device of the person rings; the one that answers takes the call and the others stop ("You answered this call on another device").
- **During the call:** the timer, **Mute**, **Speaker** (web: the other person's audio on or off; phone: loudspeaker or earpiece), **Turn camera off/on** (video calls) and **Hang up**. On the web the call can be minimised to a small bar while you keep working.
- **Group calls (up to four people):** **Add person** during a call rings another teammate into it (a searchable list of the workspace's people, without you and anyone already in the call); on the web, Team, **Team calls**, **New group call** rings two or three people at once, by voice or video. Everyone in the group hears (and sees) everyone else; each person has a tile with their name and state. Hanging up leaves the group: the others stay connected, and the call ends when one person is left with nobody being rung. Details in [Group calls](#group-calls).
- **Video mid-call and screen sharing:** **Turn on video** in a voice call (web, desktop and phone) starts your camera without hanging up; **Share screen** (web and desktop, where the browser or web view can share a screen) sends a screen or window instead of the camera, and **Stop sharing** goes back. The phone shows a shared screen it receives but cannot share its own. See [Video mid-call](#video-mid-call).
- **After the call:** what happened ("Call ended, call lasted 2:10", "Ahmed declined the call", "No answer", "On another call", "Could not connect", "Call dropped") with **Call again**.
- **Recent and missed calls.** Web: Team, **Team calls** (All or Missed, Load older, Call back). Flutter: Team calls, Recent. A missed call also leaves an in-app notification ("Missed call") and, with phone push on, a push.

Every state has its message: calls off on the server (and who can turn them on), a refused microphone or camera (and how to allow it), no device, the device offline, a call that could not connect or dropped, and a person already in another call. English and Arabic, right to left in Arabic.

## Who may call whom

Both people must be active members of the request's workspace (and so of the same tenant). The API looks the callee up among the workspace's active memberships inside the caller's own RLS context; an id from another workspace or tenant gets the same 404 as an unknown id ("This person is not an active member of this workspace"). Nobody can call themselves. `call:use` is granted to owners, members and viewers.

## How a call works

1. `POST /api/v1/app-calls {callee_user_id, media: audio|video, client_id}` creates the call as `ringing` and records the `app_call.ringing` event, which rings the callee's phones through push. If the callee is already in a call, the call is recorded as `busy` and nobody is rung. A caller already in a call gets 409.
2. Each open web tab and each running app keeps one **call stream**: `GET /api/v1/app-calls/stream?client_id=<device id>` (server-sent events). It sends `call` events (a call's current state, as this person sees it) and `signal` events (setup messages for this device). On every connection it sends the calls that are ringing or in progress, so a phone opened from the push shows the incoming screen at once.
3. The callee's device answers: `POST /api/v1/app-calls/{id}/accept {client_id}` binds that device to the call.
4. The caller's device then sends the WebRTC offer, the callee's device the answer, and both their ICE candidates in small batches: `POST /api/v1/app-calls/{id}/signals {kind: offer|answer, sdp}` or `{kind: ice, candidates: [...]}`. Each signal is addressed to the other side's bound device only. Only the caller sends the offer and only the callee the answer (409 otherwise); a signal before the call is answered is refused.
5. While connected, each device sends `POST /api/v1/app-calls/{id}/heartbeat` every 15 seconds.
6. `POST /api/v1/app-calls/{id}/end {reason: hangup|decline|failed|busy}` ends it. Ending an ended call returns it unchanged.

Group calls, adding people and video mid-call add `POST /api/v1/app-calls/rooms`, `POST /api/v1/app-calls/{id}/invite`, `POST /api/v1/app-calls/rooms/{room_id}/leave`, `GET /api/v1/app-calls/rooms/{room_id}` and `POST /api/v1/app-calls/{id}/media` ([Group calls](#group-calls), [Video mid-call](#video-mid-call)); the stream also sends `room` events.

All routes are under `/api/v1/app-calls` (the phone calls feature owns `/api/v1/calls`). Other routes: `GET /api/v1/app-calls/config` (whether calls work here and how), `GET /api/v1/app-calls/ice-servers`, `GET /api/v1/app-calls?limit=&cursor=&missed=` (the caller's own calls, newest first) and `GET /api/v1/app-calls/{id}`.

### Outcomes

| Outcome | When | Caller sees | Callee sees |
| --- | --- | --- | --- |
| `completed` | answered, then hung up by either side | Call ended, with the duration | Call ended |
| `declined` | the callee declined | Declined | Declined |
| `cancelled` | the caller hung up while it rang | Cancelled | Missed call |
| `missed` | nobody answered within `ANUM_CALLS_RING_TIMEOUT_SECONDS` (45) | No answer | Missed call |
| `busy` | the callee was in another call | On another call | Missed call |
| `failed` | a device could not connect or had no microphone | Could not connect | Could not connect |
| `dropped` | no heartbeat from either device for 60 seconds | Call dropped | Call dropped |

Rings past their timeout and silent calls are settled lazily by the next read of either participant (any call route or the stream), so no background job is needed. Missed, cancelled and busy rings create an in-app notification of kind `missed_call` for the callee.

## Signaling

Setup messages never travel on the workspace event stream: they are rows in `app_call_signals` that only the two participants can read, and the call stream delivers each one to the device it is addressed to. Following the rule for streams ("Streaming responses outlive request dependencies", CLAUDE.md), the stream route checks the membership with `Depends(repository_context, scope="function")` (a short unit of work, closed before the body starts), then every read opens its own short session in a worker thread with the participant's tenant, workspace and user set (`read_snapshot`, `asyncio.to_thread`). No session is opened or held on the event loop. It sends `retry: 1000` and a keep-alive comment every 15 s; clients reconnect with backoff (0.5 s after a clean end, then 1 s doubling to 15 s).

**When the stream reads: bus wake-ups, poll as fallback** (`services/api/anum_api/app_call_wake.py`). The database stays the source of truth; the event bus says when to read it. Every change a stream must show records an event in the same transaction as the change, so with PostgreSQL it is a durable outbox row that the relay publishes:

| Event | Recorded when | Payload |
| --- | --- | --- |
| `app_call.ringing` | a call is placed (and is not busy) | the call id and the person rung |
| `app_call.updated` | a call is answered, ended, settled as missed or dropped, recorded as busy, or ended as failed by Delete my data | the call id |
| `app_call.signaled` | an offer, answer or ICE batch is stored | the call id |

Each open call stream registers with the process's call wake hub (keyed by tenant and workspace) before its first read. A setup message (`app_call.signaled`) makes the stream re-read at once only when it names a call the stream already shows. A ring or a state change (`app_call.ringing`, `app_call.updated`) makes every call stream of the workspace re-read, because the stream may not know the call yet: the callee and the caller's other devices learn a new call id from the ring, and the caller's other devices learn a busy call, which never rang, from its `app_call.updated`. That is a few reads per stream per call; setup messages, many per call, stay targeted. A wake-up that reaches a stream it does not concern costs one read and shows nothing, because the read runs under the participant's row policies; no call data travels in the events.

- `ANUM_EVENT_BUS=nats` (PostgreSQL + NATS): the process's single JetStream consumer feeds the realtime hub ([Realtime](realtime.md)), which validates each message's subject against its event and hands call events to the wake hub, so a change committed on any replica wakes the streams on every replica. While NATS delivers, the stream's fallback poll slows to `ANUM_CALLS_BUS_FALLBACK_POLL_SECONDS` (10 s).
- `ANUM_EVENT_BUS=memory` (local, and the Oracle VM's single API process), or while NATS is down: committed events reach the wake hub in process (the in-memory bus), and the stream also keeps polling every `ANUM_CALLS_POLL_SECONDS` (1 s) while idle and every `ANUM_CALLS_ACTIVE_POLL_SECONDS` (0.25 s) while the person has a call ringing or in progress, so a change committed by another replica still shows. The in-process feed is skipped while NATS is live, so a change is not read twice.
- Either way the wait never runs past the next ring timeout or heartbeat timeout of a call the stream shows, so missed and dropped calls are still settled on time. A lost wake-up delays a frame only until the next poll.

### Events

The workspace event stream only sees `app_call.ringing` (the call id and the person rung, as `notification.created` names its recipient) and the `missed_call` notification event ([Events](events.md)). `app_call.updated` and `app_call.signaled` are published to the bus and stored in the event history like every event, but `GET /api/v1/events` and `/api/v1/events/stream` leave them out: they carry nothing a workspace member needs, and open tabs would otherwise reload for every ICE batch. A call stores a handful of these events (one per answer, end and setup message batch).

`app_call_wake.py` does not use `list_events_for_stream`: the call stream never replays event history, it reads the current calls and signals, so a missed or duplicated event cannot lose or repeat a frame (signals are de-duplicated by id). Delete my data records `app_call.updated` for each call it ends (one still ringing or in progress, now `failed`), in the same transaction as the rewrite, inside the other participant's RLS context, so that person's devices show the end at once; calls that had already ended record nothing.

## Group calls

A group call holds at most **four people at once** (`ROOM_MAX_PEOPLE`), you included. It is a **mesh**: every two people of the group have their own pair call (an `app_calls` row with the group's `room_id`) with its own WebRTC connection, signaling, heartbeats and record, through the same TURN relay as any call. Why a mesh and not a media server (SFU): there is none in ANUM's stack, the owner's single free VM should not carry everyone's media, and with four people each device sends three streams, which a phone on mobile data still manages with video. A larger group would need an SFU; the cap is checked on the server.

- `POST /app-calls/rooms {user_ids: [2 or 3 teammates], media, client_id}` creates the group (you joined, the others invited) and rings each person from your device, exactly as `POST /app-calls` does (a busy person is recorded as busy). 409 when you are already in a call.
- `POST /app-calls/{id}/invite {user_id, client_id}` adds a teammate to a call in progress, from the device that is in it. A call between two people becomes a group call: the group row is created around it and the call gets its `room_id`. 409 while the call still rings, when the person is already in it or being rung, or when four people are in it.
- **Answering a group ring** needs `"group": true` in `POST /app-calls/{id}/accept`. The server then creates, in the same transaction and under the group row's lock, a connected pair call from the person who answered to each person already in (bound to their joined devices), so two people answering at once still see each other. The person who answered sends those offers. An app without the flag (older phones) gets 409 "This is a group call. Update ANUM on this device to join it."
- `POST /app-calls/rooms/{room_id}/leave {reason}` ends every pair call of yours in the group (connected ones as completed, your rings as cancelled, a ring to you as declined) and marks you as left. Leaving twice changes nothing.
- A person whose devices stop sending heartbeats for 60 seconds is marked as left when their pairs are settled. With at most one person left in it and nobody being rung, the group call is over (`status: ended`).
- `GET /app-calls/rooms/{room_id}` and the stream's `room` event show the group as one of its people sees it: `members` (name at the time, `invited`, `joined`, `left`, `declined`, `missed`, `busy`, `cancelled` or `failed`, `is_you`), `media`, `status`, `max_people`. Devices are never shown.

Who may call whom does not change: every invitee is looked up among the workspace's active members in the caller's RLS context (the same 404 for another workspace or tenant), and `call:use` is required. Signals still go only to the bound device of the other side of their pair. The audit log adds `app_call.joined` (a pair made when someone answered) and `app_call.left` (with the group id), never naming the other people.

History lists each pair call; the web shows the pairs of one group call as one entry ("Group call" with the names); the phone marks each row "Group call".

## Video mid-call

Every connection negotiates one audio and one video section, even for a voice call, plus a small data channel (`anum-state`, negotiated id 0). Turning video on, sharing a screen or stopping it only replaces the track on the video sender, so the call never renegotiates and the offer/answer rule above stays as it is. Each side sends `{camera, screen, muted}` over the data channel, so the other side shows the picture, the shared screen or the initials. The channel carries nothing else and passes no server.

- `POST /app-calls/{id}/media {media: "video", source: "camera" | "screen"}` records the change before the camera or screen is sent: the call (and in a group call every connected pair of yours and the group) becomes `media: video`, the stream shows it, and the audit log records `app_call.video_on` with the source. 422 while `ANUM_CALLS_VIDEO_ENABLED` is off ("Screen sharing needs video calls, which are turned off on this server" for a screen); 409 when the call is not in progress.
- **Screen sharing** uses the browser's own picker (`getDisplayMedia`). The button appears only where the browser or desktop web view offers it: Chromium, Edge, Firefox and Safari; in the desktop app WebView2 on Windows and WKWebView on recent macOS (which asks for the Screen Recording permission). Where it is missing (Linux WebKitGTK, phones) the button is not shown.
- A peer whose app negotiated no video section (a phone or tab from before this change) cannot receive the picture; the person turning video on is told so, and the call continues by voice for that peer.

## System call screens

The phone's own incoming-call screen (CallKit on iOS, a self-managed `ConnectionService` on Android) sits behind `SystemCallScreen` (`apps/mobile/lib/features/team_calls/system_call_screen.dart`). The call controller already uses it: a ring that arrives while the app is in the background is shown there, it is dismissed when the ring stops (answered elsewhere, cancelled, missed), the call is reported connected and ended, and its Answer, Decline and End act on the call. The build ships the no-op `NoSystemCallScreen`, so behaviour is as before: the app rings while open and the push "Incoming call" opens it otherwise. What remains before a native screen ships is in [Pending work](pending-work.md) ("Team calls"): the plugin implementation, a data push that wakes the app (Android) and a PushKit VoIP push from the API with an APNs VoIP key (iOS), the store declarations, and real-device checks.

## Storage

Migration `0073_app_calls` (expand only: two new tables):

- `app_calls`: caller and callee (user ids and their display names at the time of the call), the device bound on each side, audio or video, the status (`ringing`, `accepted`, `ended`), the outcome, who ended it, the times (created, answered, ended, last heartbeat) and the duration in seconds. Checks: two different people, known values only, an outcome exactly when ended.
- `app_call_signals`: the offers, answers and ICE batches of a call in progress, each with its sender, recipient and recipient device, at most 40 kB each. Deleted when the call ends (and with the call).

Both tables have forced RLS: `tenant_isolation_*` on `anum.tenant_id` and `anum.workspace_id`, a restrictive `participants_only_*` that admits a row only when `anum.user_id` is one of its two participants, and a restrictive insert policy: only the caller inserts a call and only the sender inserts a signal. The busy check is the one read made outside the caller's view: it runs in the callee's own user context and reads only whether the callee has a call ringing or in progress (the same pattern as the push sender reading a recipient's devices). One narrow grant serves retention: `anum_maintenance` may read `id`, `tenant_id`, `workspace_id`, `caller_user_id` and `ended_at` of calls that ended more than seven days ago (a column grant and a select policy for that role only; the participants-only policy admits that role's reads, never its writes). Grant `anum_maintenance` `WITH INHERIT FALSE, SET TRUE`, as the bootstrap does.

The audit log records `app_call.placed`, `app_call.answered` and `app_call.ended` with the actor, the call id, audio or video, the outcome and the duration; it never names the other participant. Calls that ended more than `ANUM_CALLS_RETENTION_DAYS` (365) ago are deleted, with their signals, by the daily retention job (`python -m anum_api.voice_retention`, reported under `app_calls`): it discovers them as `anum_maintenance` and deletes each as the application role inside the caller's RLS context. A participant placing a new call also purges their own old calls.

Migration `0179_app_call_rooms` (expand only: a new table and a nullable column) adds group calls:

- `app_call_rooms`: the host, audio or video, `live` or `ended`, `member_user_ids` (everyone ever invited, at most 12) and `members` (a JSON list: user id, name at the time, state, the joined device, who invited them, joined, left and last seen times), created and ended times. Forced RLS: `tenant_isolation_app_call_rooms`, a restrictive `members_only_app_call_rooms` (`anum.user_id` must be in `member_user_ids`, for reads and writes, so nobody can drop themselves out of the row either) and a restrictive insert policy (only the host inserts). Deleting the organisation or the workspace purges it through `anum_tenant_purger` (`lifecycle_purge_app_call_rooms`; listed in `tenant_lifecycle_tables.py`).
- `app_calls.room_id`: the group a pair call belongs to, foreign key `on delete set null`.

Retention deletes a group row together with its pair calls, as the pair's caller inside that context (a member, so the row policy admits it); no new grant. Delete my data rewrites the person's place in each group inside another member's context (one tombstone per group call, shared with its pairs) and removes a group nobody else is left in. Download my data leaves `room_id` out.

**Migration chain.** `0073_app_calls` follows `0072_push_device_language` (main's head after the phone calls feature, `0071_calls`, and the finance push language change merged). A branch landing first with the same number would need this re-chained (the migration test reads `down_revision` from the file).

### Privacy

No audio or video passes through or is stored by ANUM: media is end to end encrypted by WebRTC (DTLS-SRTP), and a TURN relay forwards encrypted packets it cannot read. ANUM keeps who called whom, when, audio or video, the outcome and the duration, visible only to the two participants, for `ANUM_CALLS_RETENTION_DAYS`.

- **Download my data** ([Privacy](privacy.md)) has an `app_calls` section: each of the person's calls as they see it (direction, the other person's display name at the time, audio or video, status, outcome, times, duration). Never device ids, the other person's user id or setup messages. The summary counts `app_calls` and `app_call_signals`.
- **Delete my data** deletes the person's setup messages and rewrites their side of every call: their user id becomes an unlinkable tombstone (`deleted:` and a random id, a new one per call), their name "Deleted user" and their device id `deleted`; a call still ringing or in progress is ended as failed. The other participant keeps the call in their history, shown as "Deleted user". The rewrite runs inside the other participant's RLS context (the person's own context may not write a row they are no longer part of). A call whose other side was already deleted is removed instead.

### Call log mapping

A separate branch adds a call log with an entry kind `in_app`. Once both are merged, an `app_calls` row maps to that log as: kind `in_app`, direction from the viewer (`caller_user_id` = viewer is outgoing), peer = the other participant (`*_user_id`, `*_name`), started at `created_at`, answered at `answered_at`, ended at `ended_at`, duration `duration_seconds`, outcome as in the table above (`missed`, `cancelled` and `busy` read as missed for the callee). Either the log reads `app_calls` as a source or the call end writes one entry per participant; `app_calls` stays the signaling record.

## Relay (TURN) and STUN

WebRTC connects directly when it can. Behind strict NATs and firewalls (many office and some mobile networks) it needs a TURN relay. ANUM uses coturn's REST credential scheme (`use-auth-secret`): `GET /api/v1/app-calls/ice-servers` mints, per request, a username `<expiry unix time>:<random id>` and a password, the base64 HMAC-SHA1 of the username under the shared secret. coturn checks the HMAC and refuses the username after its expiry. The id is random for every issue and nothing maps it back, so coturn's logs name nobody. Credentials last `ANUM_CALLS_TURN_TTL_SECONDS` (default 7200, 300 to 86400): a client fetches fresh ones for every call, and an allocation cannot be refreshed after expiry, so a single relayed call is limited to that time.

| Setting | Meaning |
| --- | --- |
| `ANUM_CALLS_TURN_URLS` | Comma-separated `turn:` or `turns:` URLs of the relay. Empty: no relay. |
| `ANUM_CALLS_TURN_SECRET` | coturn's `static-auth-secret`, 32 or more characters. Never in source: `.env` on the VM (generated by `install.sh`), a secret store elsewhere. |
| `ANUM_CALLS_STUN_URLS` | Comma-separated `stun:` URLs. Default with a relay: the relay hosts on port 3478. |
| `ANUM_CALLS_ALLOW_STUN_ONLY` | `true` turns calls on without a relay. |
| `ANUM_CALLS_TURN_TTL_SECONDS`, `ANUM_CALLS_RING_TIMEOUT_SECONDS`, `ANUM_CALLS_VIDEO_ENABLED`, `ANUM_CALLS_POLL_SECONDS`, `ANUM_CALLS_ACTIVE_POLL_SECONDS`, `ANUM_CALLS_BUS_FALLBACK_POLL_SECONDS`, `ANUM_CALLS_RETENTION_DAYS` | Limits described above. |

Modes (`GET /api/v1/app-calls/config`):

- **off** (the default): no relay is configured, or its secret is missing or shorter than 32 characters. Placing a call answers 503 and the apps say "Calls are off on this server. Ask the person who runs ANUM to set up the calls relay."
- **stun_only**: `ANUM_CALLS_ALLOW_STUN_ONLY=true` without a relay. Calls connect on many home and mobile networks but fail behind strict firewalls; the apps say so on the Team calls screen. With no STUN URLs only devices on the same network connect.
- **turn**: a relay with a valid secret. The recommended production mode.

## Deploying the relay on the Oracle VM

The relay is the opt-in compose profile `calls` (service `coturn`, image `coturn/coturn:4.18.0-alpine`) in `infra/oracle/compose.yaml`. It is the only service with host networking (a relay needs the VM's real UDP ports; publishing a large range through docker-proxy is slow) and publishes nothing else. Its configuration, secret included, is written inside the container at start, never on a command line. It refuses to relay to private, loopback, link-local, carrier-grade NAT and multicast addresses (so it cannot reach the VM's own services or Oracle's metadata address), allows its own private address (so two relayed devices can meet), turns off its CLI, TLS and DTLS, and caps allocations per user (12) and in total (200).

Ports:

| Protocol | Port | Why |
| --- | --- | --- |
| TCP | 3478 | TURN over TCP (networks that block UDP) |
| UDP | 3478 | STUN and TURN |
| UDP | 49160 to 49200 (`ANUM_TURN_MIN_PORT`, `ANUM_TURN_MAX_PORT`) | relayed media: about one port per relayed device, so about 20 relayed calls at once |

Owner steps (details in [Deploying on Oracle](deploy-oracle.md#in-app-calls)):

1. Oracle console: Networking, Virtual cloud networks, the VCN, the subnet's security list, **Add ingress rules** (source `0.0.0.0/0`, stateful): TCP 3478, UDP 3478, UDP 49160-49200.
2. On the VM: `bash infra/oracle/calls.sh enable` (it asks for the VM's public IPv4 address, suggesting the address the API host name resolves to). It writes `ANUM_TURN_EXTERNAL_IP`, `ANUM_TURN_PRIVATE_IP` and `ANUM_CALLS_TURN_URLS` (`turn:<API host>:3478?transport=udp,turn:<API host>:3478?transport=tcp`) into `.env`, generates `ANUM_TURN_SECRET` if missing, opens the three ports in the host firewall, adds `calls` to `COMPOSE_PROFILES`, starts coturn and restarts the API.
3. Check: `bash infra/oracle/calls.sh status`; in the web app the Team calls tab no longer says calls are off; a call between a phone on mobile data and a laptop on office Wi-Fi connects.

Host firewall: Oracle's Ubuntu images end the INPUT chain with a REJECT rule, so `calls.sh` inserts ACCEPT rules before it, live (`iptables -I INPUT <position of REJECT>`) and in `/etc/iptables/rules.v4` before `-A INPUT -j REJECT`:

```
-A INPUT -p tcp -m state --state NEW -m tcp --dport 3478 -j ACCEPT
-A INPUT -p udp -m udp --dport 3478 -j ACCEPT
-A INPUT -p udp -m udp --dport 49160:49200 -j ACCEPT
```

It never uses ufw or `netfilter-persistent save` (that would store Docker's chains). `calls.sh disable` stops coturn, clears `ANUM_CALLS_TURN_URLS` (calls off), removes the rules and reminds you to remove the security-list rules.

Other hosts (Render, Kubernetes): run coturn anywhere with a public address and the same ports, `use-auth-secret` and the denied-peer ranges above, then set `ANUM_CALLS_TURN_URLS` and `ANUM_CALLS_TURN_SECRET` on the API.

## Clients

**Web** (`apps/web/src/AppCalls.tsx`, `CallHistory.tsx`, `lib/appCalls*.ts`): one stream per tab while calls are on, the incoming screen with a soft ring (Web Audio; silent when the browser blocks autoplay) and a desktop notification when the tab is hidden and notifications are allowed, the call screen as a modal dialog (focus kept inside, Escape minimises), the Calls tab with paging. The microphone (and camera) is requested before the call rings anyone, so a refusal never leaves someone ringing.

**Flutter** (`apps/mobile/lib/features/team_calls/`): `flutter_webrtc` 1.6.3. Its Android plugin is Java plus one Kotlin file and applies `kotlin-android` itself while `android.builtInKotlin=false` (checked in its `android/build.gradle`, the AGP 9 note in CLAUDE.md); it pulls the WebRTC Android library from Maven Central and `audioswitch` from JitPack, and adds about 10 MB per ABI to the APK. The app declares `CAMERA`, `MODIFY_AUDIO_SETTINGS` and `ACCESS_NETWORK_STATE` on Android (with `tool/configure_native.dart`) and `NSCameraUsageDescription` on iOS (with `PERMISSION_CAMERA` for permission_handler). The call layer sits above the navigator, so a ring shows on any screen while the app is open. A push (`incoming_call`, `missed_call`) opens the Team calls screen (route `/team-calls`); the stream then shows the ringing call.

The web call layer keeps one connection per other person (`AppCalls.tsx`, `lib/appCallEngine.ts`) and the people picker (`CallPeoplePicker.tsx`); the phone does the same in `call_controller.dart` (`CallLink`, one forked `CallMediaSession` per person sharing the microphone and camera) with the picker drawn inside the call layer. The phone joins group calls, adds people and turns video on; it starts a group call by calling one person and adding the others, and it does not share its screen.

**Not built yet:** the native system call screens (the boundary is built, see [System call screens](#system-call-screens)) and recording of team calls (media never passes ANUM, so recording would need a recording client or an SFU). Without a system call screen an incoming call rings in the app while it is open, and otherwise the phone shows a normal push ("Incoming call") that opens the app, which shows the ring while it is still ringing.

## Phone push

`app_call.ringing` becomes the push kind `incoming_call` ("Incoming call", "A teammate is calling you. Open ANUM to answer.") for the person rung, and a `missed_call` notification becomes the push kind `missed_call` ("Missed call", "A teammate tried to call you in ANUM."). Fixed text, ids only (`app_call_id`); both follow the person's phone push switch ([Notifications](notifications.md)).

## Tests

- API: `services/api/tests/test_app_calls.py` (configuration modes, TURN credentials minted and expired, same-workspace only, the lifecycle, signals reaching the bound device only, the stream, push intents) and `test_postgres_app_calls.py` (row policies: nobody rings a person of another workspace or tenant, non-participants see and write nothing, forged rows refused, the busy check in the callee's context). `test_app_call_wake.py` (wake-ups reach only their own tenant and workspace, in process and through the realtime hub; the in-process feed is skipped while the bus is live; NATS mode end to end on the in-memory bus), `test_app_calls_off_event_loop.py` (the stream route and its woken reads, polls and settles open no store on the event loop) and `test_nats_integration.py::test_call_events_wake_call_streams_through_nats_only_in_their_scope` (a real JetStream wakes the call stream long before its poll, not other tenants or workspaces; CI's API integration job, no skip). `test_migrations.py` checks the migration's shape; `test_oracle_compose.py` the relay service and `calls.sh`.
- Web: `apps/web/test/appCalls.test.ts` (unit) and `apps/web/e2e/app-calls.spec.ts`: two browser contexts with Chromium's fake microphone and camera make real WebRTC calls through a signaling stand-in (voice with mute and hang up, video with the remote picture, decline, cancel and the missed call, a refused microphone, calls off, offline, Arabic, and the call screens at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844 without horizontal scroll).
- Group calls and video mid-call: `services/api/tests/test_app_call_groups.py` (the mesh on answering, the update message for older apps, two or three members of this workspace only, adding people up to four, leaving and the end of the group, silent members leaving, video and screen recorded and refused while video is off, the stream's `room` frames, Delete my data) and the group tests in `test_postgres_app_calls.py` (members-only rows, forged groups refused, a member cannot drop out of the row, Delete my data and retention); `test_migrations.py::test_app_call_rooms_migration_is_expand_only_and_members_only`. Web: `apps/web/test/appCallGroups.test.ts` and, in `e2e/app-calls.spec.ts`, three real browsers in one group call (mesh, leaving, axe), video and screen sharing in a voice call without a new offer, and New group call with a decline. Flutter: the group, add person, video, system call screen and layout (four people, 320x568 and 1024x768 at 200% text, Arabic) tests in `apps/mobile/test/team_calls/team_calls_test.dart`.
- Flutter: `apps/mobile/test/team_calls/team_calls_test.dart` (strings, outcomes, push routing, incoming, answer, mute, hang up, decline, answered elsewhere, placing a call, a refused microphone, calls off, history, Arabic, and the call screen at four sizes at 200% text).
