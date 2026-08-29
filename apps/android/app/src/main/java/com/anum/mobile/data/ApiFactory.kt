package com.anum.mobile.data

import com.anum.mobile.BuildConfig
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory

object ApiFactory {
    fun create(tokenProvider: AccessTokenProvider, context: SessionContext): AnumApi {
        val auth = Interceptor { chain ->
            val builder = chain.request().newBuilder()
                .header("x-tenant-id", context.tenantId)
                .header("x-workspace-id", context.workspaceId)
                .header("x-user-id", context.userId)
                .header("x-user-roles", context.roles.joinToString(","))
            tokenProvider.accessToken()?.let { builder.header("Authorization", "Bearer $it") }
            chain.proceed(builder.build())
        }
        val client = OkHttpClient.Builder().addInterceptor(auth).apply {
            if (BuildConfig.DEBUG) addInterceptor(HttpLoggingInterceptor().setLevel(HttpLoggingInterceptor.Level.BASIC))
        }.build()
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
        return Retrofit.Builder().baseUrl(BuildConfig.ANUM_API_URL).client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build().create(AnumApi::class.java)
    }
}
