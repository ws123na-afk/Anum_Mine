package dev.anum.android.auth

import android.util.Base64
import dev.anum.android.data.model.TenantContext
import org.json.JSONObject

/**
 * Decodes the (already-signature-verified-by-Keycloak, HTTPS-delivered) ID
 * token's payload claims client-side, purely for display/routing. This
 * mirrors apps/web/src/lib/auth.ts's use of keycloak-js's parsed token: the
 * app trusts the claims because it just received them directly from
 * Keycloak's token endpoint over TLS, not because it re-verifies the
 * signature itself - see TenantContext's own docstring on why this is not
 * a trust boundary.
 */
object JwtClaims {
    fun tenantContextFrom(idToken: String): TenantContext? {
        val claims = decodePayload(idToken) ?: return null
        val subject = claims.optString("sub").ifBlank { return null }
        val tenantId = claims.optString("tenant_id").ifBlank { return null }
        val workspaceId = claims.optString("workspace_id").ifBlank { return null }
        val roles = claims.optJSONArray("roles")?.let { array ->
            (0 until array.length()).map { index -> array.getString(index) }
        } ?: emptyList()
        return TenantContext(tenantId = tenantId, workspaceId = workspaceId, userId = subject, roles = roles)
    }

    private fun decodePayload(jwt: String): JSONObject? {
        val segments = jwt.split(".")
        if (segments.size != 3) return null
        return try {
            val payload = Base64.decode(segments[1], Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
            JSONObject(String(payload, Charsets.UTF_8))
        } catch (_: IllegalArgumentException) {
            null
        }
    }
}
