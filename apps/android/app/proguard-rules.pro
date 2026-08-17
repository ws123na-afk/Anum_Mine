# AppAuth uses reflection for its browser-matcher classes.
-keep class net.openid.appauth.** { *; }

# kotlinx.serialization generates synthetic serializer classes per @Serializable type.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class dev.anum.android.data.model.** {
    *** Companion;
}
-keepclasseswithmembers class dev.anum.android.data.model.** {
    kotlinx.serialization.KSerializer serializer(...);
}
