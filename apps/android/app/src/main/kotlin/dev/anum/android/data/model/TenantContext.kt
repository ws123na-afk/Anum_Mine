package dev.anum.android.data.model

/**
 * Display/routing identity derived from the validated ID token's claims -
 * mirrors apps/web/src/lib/auth.ts's getTenantContext() exactly (same
 * tenant_id/workspace_id/roles claim names, same "not a trust boundary"
 * caveat: the API independently derives and enforces its own TenantContext
 * from the Bearer access token server-side, this is for UI purposes only).
 */
data class TenantContext(
    val tenantId: String,
    val workspaceId: String,
    val userId: String,
    val roles: List<String>,
)
