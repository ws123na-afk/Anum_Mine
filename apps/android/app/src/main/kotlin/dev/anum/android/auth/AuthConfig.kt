package dev.anum.android.auth

import android.net.Uri
import dev.anum.android.BuildConfig

/**
 * Mirrors apps/web/src/lib/auth.ts's Keycloak configuration and claim
 * mapping exactly - both clients trust the same realm and expect the same
 * custom claims (tenant_id, workspace_id, roles) from the same token
 * mappers (see infra/docker/keycloak/anum-realm.json).
 *
 * Values are build-config-injected (see app/build.gradle.kts) rather than
 * hardcoded, the same reasoning as the web client's VITE_ANUM_KEYCLOAK_*
 * build-time env vars: different environments (local dev realm vs.
 * production) point at different issuers/clients without a code change.
 */
object AuthConfig {
    val issuerUri: Uri = Uri.parse(BuildConfig.KEYCLOAK_ISSUER)
    const val CLIENT_ID: String = BuildConfig.KEYCLOAK_CLIENT_ID

    // Must match AndroidManifest.xml's RedirectUriReceiverActivity
    // intent-filter and build.gradle.kts's appAuthRedirectScheme exactly.
    val redirectUri: Uri = Uri.parse("dev.anum.android://oauth2redirect")

    val scopes = listOf("openid", "profile", "email")
}
