package dev.anum.android.data.api

import dev.anum.android.auth.AuthManager
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response

/** Attaches a fresh Bearer token to every request - the Android equivalent
 * of apps/web/src/lib/api.ts's authHeaders() in its OIDC branch. There is
 * no stub-header branch here: unlike the web client (which still supports
 * ANUM_AUTH_MODE=stub_headers for local dev), this app only ever speaks
 * OIDC, since a real device is never "local dev trusted" the way
 * `localhost` traffic can be. */
class AuthInterceptor(private val authManager: AuthManager) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = runBlocking { authManager.freshAccessToken() }
        val request = chain.request().newBuilder().apply {
            if (token != null) {
                addHeader("Authorization", "Bearer $token")
            }
        }.build()
        return chain.proceed(request)
    }
}
