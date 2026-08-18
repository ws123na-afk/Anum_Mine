package dev.anum.android.auth

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import net.openid.appauth.AuthState

/**
 * Persists AppAuth's [AuthState] (which bundles the refresh token needed
 * for silent renewal) in an at-rest-encrypted preferences file, per
 * docs/android.md: "Sensitive local state should use Android secure
 * storage and avoid long-lived raw provider tokens." The access token
 * inside AuthState is short-lived and only ever read back in memory via
 * [AuthManager.withFreshAccessToken] - nothing here exposes it as a
 * standalone long-lived value the way a naive "just save the access
 * token" implementation would.
 */
class TokenStore(context: Context) {
    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "anum_auth_state",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun readAuthState(): AuthState? {
        val json = prefs.getString(KEY_AUTH_STATE, null) ?: return null
        return AuthState.jsonDeserialize(json)
    }

    fun writeAuthState(state: AuthState) {
        prefs.edit().putString(KEY_AUTH_STATE, state.jsonSerializeString()).apply()
    }

    fun clear() {
        prefs.edit().remove(KEY_AUTH_STATE).apply()
    }

    private companion object {
        const val KEY_AUTH_STATE = "auth_state"
    }
}
