# Store data safety and app privacy answers

How to answer Google Play's **Data safety** form and Apple's **App Privacy** ("nutrition label") questions for the Flutter app (`com.anum.app`, [Flutter mobile](mobile.md)). The answers follow from what the app and the API actually do ([Privacy](privacy.md)); review them against your deployment's configuration, because the owner of the deployment is the one declaring. This is guidance, not legal advice.

Before submitting:

1. Publish the deployment's policy from the [privacy policy template](privacy-policy.md) and set `ANUM_PRIVACY_POLICY_URL`.
2. Enter that URL as the privacy policy URL in Play Console (App content, Privacy policy) and App Store Connect (App Privacy).
3. Account deletion: both stores need an in-app path and Google Play also a web link. In-app: **Settings → Privacy → Delete my data**. Web: the web app's `#privacy` page (`https://<web host>/#privacy`), which needs sign-in; Play also accepts a page explaining how to ask, so add `<owner contact>` for closing the sign-in account itself (ANUM deletes app data; the Keycloak account is closed by an administrator, [Privacy](privacy.md#what-deletion-removes-and-keeps)).

## What the app does and does not do

- No advertising, no ad identifiers, no analytics or crash-reporting SDK in the app. Server-side error reporting (`ANUM_SENTRY_DSN`) is optional and strips user identity and content ([Observability](observability.md#error-reporting)).
- No tracking across other companies' apps or websites (Apple: "Data Used to Track You": **none**).
- No location, contacts, photos, calendar, health or payment data.
- The microphone is used only while the user holds push-to-talk. Speech is converted to text by the platform's speech recognizer (on-device or the platform vendor's service, depending on the device and language); the app sends **text** to the API, never audio.
- All traffic is HTTPS (App Transport Security default, cleartext off on Android).
- Data goes to the deployment owner's own API. Text is passed on to the configured model provider to produce answers ([privacy policy template, section 4](privacy-policy.md#4-who-it-is-sent-to)). Under both stores' rules, data sent to a service provider that processes it on the developer's behalf is not "sharing"; if the owner's provider terms allow the provider to use the data for its own purposes, declare it as shared.

## Google Play: Data safety

Overall answers:

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | Yes |
| Is all of the user data collected by your app encrypted in transit? | Yes |
| Do you provide a way for users to request that their data is deleted? | Yes (in-app: Settings → Privacy → Delete my data; plus the web link above) |

Data types (collected, not shared, unless the note says otherwise):

| Play data type | Collected | Purpose | Optional? | Notes |
|---|---|---|---|---|
| Personal info: Name | Yes | Account management | No | From the sign-in token when the identity provider supplies it. |
| Personal info: Email address | Yes | Account management | No | Used to match invitations. |
| Personal info: User IDs | Yes | Account management, App functionality | No | Keycloak subject and workspace membership. |
| Messages: Other in-app messages | Yes | App functionality | No | Assistant conversations; the core feature. |
| Audio: Voice or sound recordings | No | | | Only text transcripts reach the API. If you prefer to be conservative, declare it as collected for App functionality and processed ephemerally. |
| Files and docs | Yes | App functionality | Yes | Files the user uploads. |
| App activity: Other user-generated content | Yes | App functionality | No | Tasks, memories, voice transcripts, approvals, the user's settings (notification and memory preferences). |
| App activity: App interactions | Yes | App functionality, Fraud prevention, security and compliance | No | Audit records (ids and counts). |
| App info and performance: Crash logs, Diagnostics | Only if `ANUM_SENTRY_DSN` is set | App functionality | No | Server-side, scrubbed. |
| Location, Financial info, Health, Photos and videos, Contacts, Calendar, Web browsing, Installed apps, Device or other IDs | No | | | |

"Is this data processed ephemerally?": No for all collected types (they are stored; voice text follows the transcript retention the user picks).

## Apple: App Privacy

"Do you or your third-party partners collect data from this app?": **Yes**.

Data Linked to You (none used for tracking):

| Apple data type | Purposes |
|---|---|
| Contact Info: Name | App Functionality |
| Contact Info: Email Address | App Functionality |
| Identifiers: User ID | App Functionality |
| User Content: Other User Content (conversations, tasks, memories, voice transcripts) | App Functionality |
| User Content: Audio Data | Declare only if you treat transcribed speech as audio; the app uploads text only. |
| User Content: Other User Content (files) | App Functionality |
| Usage Data: Product Interaction (audit records) | App Functionality |
| Diagnostics: Crash Data | Only if server error reporting is on: App Functionality |

Data Not Collected: location, health and fitness, financial info, contacts, browsing history, search history (unless the deployment turns on web search for tasks: then declare User Content, App Functionality), sensitive info, purchases.

Data Used to Track You: **none**.

Also in App Store Connect:

- Account deletion (guideline 5.1.1(v)): point reviewers at Settings → Privacy → Delete my data, and give a review account.
- Export compliance: the app uses only standard HTTPS and the keychain ([Flutter mobile](mobile.md#owner-checklist-windows)).

## Keep these answers true

Re-check this page when any of these change: a new SDK in `apps/mobile/pubspec.yaml`, a new data type stored by the API, a new model provider or integration, error reporting turned on, or retention defaults changed. The form answers are the owner's declaration and must match the published policy.
