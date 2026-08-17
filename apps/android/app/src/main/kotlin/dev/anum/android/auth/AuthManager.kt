package dev.anum.android.auth

import android.content.Context
import android.content.Intent
import dev.anum.android.data.model.TenantContext
import kotlinx.coroutines.suspendCancellableCoroutine
import net.openid.appauth.AuthState
import net.openid.appauth.AuthorizationException
import net.openid.appauth.AuthorizationRequest
import net.openid.appauth.AuthorizationResponse
import net.openid.appauth.AuthorizationService
import net.openid.appauth.AuthorizationServiceConfiguration
import net.openid.appauth.ResponseTypeValues
import net.openid.appauth.TokenResponse
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Authorization Code + PKCE against Keycloak via AppAuth - the Android
 * equivalent of apps/web/src/lib/auth.ts's keycloak-js integration.
 * AppAuth generates and verifies the PKCE code_verifier/code_challenge
 * itself (S256 by default), matching the realm's
 * pkce.code.challenge.method=S256 enforcement (see
 * infra/docker/keycloak/anum-realm.json) the same way keycloak-js does on
 * web - see that file's own comment on choosing an audited library over
 * hand-rolled PKCE for the reasoning, which applies equally here.
 */
class AuthManager(context: Context) {
    private val appContext = context.applicationContext
    private val tokenStore = TokenStore(appContext)
    private val authService = AuthorizationService(appContext)

    private var authState: AuthState? = tokenStore.readAuthState()

    val isSignedIn: Boolean
        get() = authState?.isAuthorized == true

    fun currentTenantContext(): TenantContext? {
        val idToken = authState?.idToken ?: return null
        return JwtClaims.tenantContextFrom(idToken)
    }

    private suspend fun discoverServiceConfig(): AuthorizationServiceConfiguration =
        suspendCancellableCoroutine { continuation ->
            AuthorizationServiceConfiguration.fetchFromIssuer(AuthConfig.issuerUri) { config, exception ->
                when {
                    config != null -> continuation.resume(config)
                    exception != null -> continuation.resumeWithException(exception)
                    else -> continuation.resumeWithException(IllegalStateException("OIDC discovery returned neither a config nor an error"))
                }
            }
        }

    /** Builds the sign-in intent; the caller launches it via
     * `registerForActivityResult(ActivityResultContracts.StartActivityForResult())`
     * (see MainActivity.kt) since AppAuth hands control to the system
     * browser/Custom Tabs for the actual login UI - the app never sees the
     * user's credentials. */
    suspend fun buildSignInIntent(): Intent {
        val serviceConfig = discoverServiceConfig()
        val request = AuthorizationRequest.Builder(
            serviceConfig,
            AuthConfig.CLIENT_ID,
            ResponseTypeValues.CODE,
            AuthConfig.redirectUri,
        )
            .setScopes(AuthConfig.scopes)
            .build()
        return authService.getAuthorizationRequestIntent(request)
    }

    /** Call from the activity-result callback that received [buildSignInIntent]'s result. */
    suspend fun handleSignInResult(data: Intent): Result<TenantContext> {
        val response = AuthorizationResponse.fromIntent(data)
        val exception = AuthorizationException.fromIntent(data)
        if (response == null) {
            return Result.failure(exception ?: IllegalStateException("Sign-in was cancelled"))
        }

        val tokenResponse = exchangeCodeForTokens(response)
        val newState = AuthState(response, exception).apply { update(tokenResponse, null) }
        authState = newState
        tokenStore.writeAuthState(newState)

        val context = currentTenantContext()
            ?: return Result.failure(IllegalStateException("ID token is missing expected ANUM claims"))
        return Result.success(context)
    }

    private suspend fun exchangeCodeForTokens(response: AuthorizationResponse): TokenResponse =
        suspendCancellableCoroutine { continuation ->
            authService.performTokenRequest(response.createTokenExchangeRequest()) { tokenResponse, exception ->
                when {
                    tokenResponse != null -> continuation.resume(tokenResponse)
                    exception != null -> continuation.resumeWithException(exception)
                    else -> continuation.resumeWithException(IllegalStateException("Token exchange returned neither a response nor an error"))
                }
            }
        }

    /** Returns a valid (refreshed if necessary) access token, or null if signed out.
     * AuthInterceptor calls this per request - see that class. */
    suspend fun freshAccessToken(): String? {
        val state = authState ?: return null
        return suspendCancellableCoroutine { continuation ->
            state.performActionWithFreshTokens(authService) { accessToken, _, exception ->
                if (exception != null) {
                    continuation.resume(null)
                } else {
                    tokenStore.writeAuthState(state)
                    continuation.resume(accessToken)
                }
            }
        }
    }

    fun signOut() {
        authState = null
        tokenStore.clear()
    }

    fun dispose() {
        authService.dispose()
    }
}
