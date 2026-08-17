plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "dev.anum.android"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.anum.android"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        // AppAuth needs a custom URI scheme registered as an intent-filter
        // for the OAuth/OIDC redirect to come back into this app after the
        // system browser completes login - see AndroidManifest.xml's
        // RedirectUriReceiverActivity and AuthConfig.kt's REDIRECT_URI,
        // which must use this same scheme.
        manifestPlaceholders["appAuthRedirectScheme"] = "dev.anum.android"

        // Mirrors apps/web's VITE_ANUM_API_URL/VITE_ANUM_KEYCLOAK_* build-time
        // env vars - these point at the local dev realm/API by default so a
        // debug build works against `docker compose up` out of the box (see
        // infra/docker/keycloak/README.md). Override per build type/flavor
        // for a real deployment rather than editing these defaults in place.
        buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000\"")
        buildConfigField("String", "KEYCLOAK_ISSUER", "\"http://10.0.2.2:8080/realms/anum\"")
        // NOTE: infra/docker/keycloak/anum-realm.json does not yet define
        // an "anum-android" client - see apps/android/README.md for the
        // realm change this needs before a debug build can actually
        // complete a login against the local dev realm.
        buildConfigField("String", "KEYCLOAK_CLIENT_ID", "\"anum-android\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            buildConfigField("String", "API_BASE_URL", "\"https://api.anum.example\"")
            buildConfigField("String", "KEYCLOAK_ISSUER", "\"https://auth.anum.example/realms/anum\"")
            buildConfigField("String", "KEYCLOAK_CLIENT_ID", "\"anum-android\"")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.core)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.security.crypto)
    implementation(libs.appauth)
    implementation(libs.retrofit)
    implementation(libs.retrofit.converter.kotlinx.serialization)
    implementation(libs.okhttp)
    implementation(libs.okhttp.logging.interceptor)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    debugImplementation(libs.androidx.ui.tooling)
}
