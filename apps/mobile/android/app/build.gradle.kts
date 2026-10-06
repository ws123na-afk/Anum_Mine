plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing (docs/mobile.md, "Release builds"). Values come from the
// environment or from the gitignored android/key.properties; neither the
// keystore nor its passwords are ever committed. When nothing is configured,
// release builds fall back to the debug key with a warning so CI can still
// build an unsigned-for-store bundle; set ANUM_ANDROID_REQUIRE_RELEASE_SIGNING
// to true to make a missing key fail the build instead.
val keystoreProperties =
    java.util.Properties().apply {
        val source = rootProject.file("key.properties")
        if (source.exists()) source.inputStream().use { load(it) }
    }

fun releaseSigningValue(environment: String, property: String): String? =
    System.getenv(environment)?.takeIf { it.isNotBlank() }
        ?: keystoreProperties.getProperty(property)?.takeIf { it.isNotBlank() }

val releaseKeystorePath = releaseSigningValue("ANUM_ANDROID_KEYSTORE_PATH", "storeFile")
val releaseKeystorePassword = releaseSigningValue("ANUM_ANDROID_KEYSTORE_PASSWORD", "storePassword")
val releaseKeyAlias = releaseSigningValue("ANUM_ANDROID_KEY_ALIAS", "keyAlias")
val releaseKeyPassword = releaseSigningValue("ANUM_ANDROID_KEY_PASSWORD", "keyPassword")
val releaseSigningValues =
    listOf(releaseKeystorePath, releaseKeystorePassword, releaseKeyAlias, releaseKeyPassword)
val hasReleaseSigning = releaseSigningValues.all { it != null }
if (!hasReleaseSigning && releaseSigningValues.any { it != null }) {
    throw GradleException(
        "Android release signing is partly configured: set the keystore path, " +
            "keystore password, key alias and key password together.",
    )
}
gradle.taskGraph.whenReady {
    val releaseTask = allTasks.any { it.project == project && it.name.contains("Release") }
    if (releaseTask && !hasReleaseSigning) {
        if (System.getenv("ANUM_ANDROID_REQUIRE_RELEASE_SIGNING") == "true") {
            throw GradleException("ANUM_ANDROID_REQUIRE_RELEASE_SIGNING is set but no release keystore is configured.")
        }
        logger.warn(
            "warning: no Android release keystore configured " +
                "(ANUM_ANDROID_KEYSTORE_PATH or android/key.properties); " +
                "signing the release build with the debug key. Do not upload it to Play.",
        )
    }
}

android {
    namespace = "com.anum.anum_mobile"
    compileSdk = 37
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        manifestPlaceholders["appAuthRedirectScheme"] = "com.anum.app"
        applicationId = "com.anum.app"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            if (hasReleaseSigning) {
                storeFile = rootProject.file(releaseKeystorePath!!)
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        release {
            // The release key when configured, else the debug key (see the warning above).
            signingConfig = signingConfigs.getByName(if (hasReleaseSigning) "release" else "debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
