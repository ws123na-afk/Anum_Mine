package dev.anum.android.data.api

import dev.anum.android.BuildConfig
import dev.anum.android.auth.AuthManager
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

/** No dependency-injection framework here (Hilt/Koin) - this is a small
 * enough app that one manually-wired composition root is clearer than
 * adding a DI framework's learning curve/build-time cost for its own sake.
 * Revisit if the object graph grows past a handful of classes. */
object NetworkModule {
    private val json = Json { ignoreUnknownKeys = true }

    fun buildApiService(authManager: AuthManager): AnumApiService {
        val loggingInterceptor = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) HttpLoggingInterceptor.Level.BASIC else HttpLoggingInterceptor.Level.NONE
        }
        val client = OkHttpClient.Builder()
            .addInterceptor(AuthInterceptor(authManager))
            .addInterceptor(loggingInterceptor)
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(BuildConfig.API_BASE_URL.let { if (it.endsWith("/")) it else "$it/" })
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()

        return retrofit.create(AnumApiService::class.java)
    }
}
