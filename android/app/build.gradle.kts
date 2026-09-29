plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "dev.mtbridge.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.mtbridge.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        vectorDrawables { useSupportLibrary = true }
    }

    // release 签名信息从环境变量 / Gradle 属性读取，不写入仓库。
    // CI 上由 GitHub Secrets 注入；本地可从环境变量或 ~/.gradle/gradle.properties 提供。
    signingConfigs {
        create("release") {
            val storePath = (System.getenv("KEYSTORE_PATH")
                ?: providers.gradleProperty("KEYSTORE_PATH").orNull
                ?: "keystore/release.keystore")
            // file() 在 app 模块内是相对 app/ 解析的，这里统一挂到 rootDir 下
            storeFile = file(rootProject.file(storePath))
            storePassword = (System.getenv("KEYSTORE_PASSWORD")
                ?: providers.gradleProperty("KEYSTORE_PASSWORD").orNull)
            keyAlias = (System.getenv("KEY_ALIAS")
                ?: providers.gradleProperty("KEY_ALIAS").orNull)
            keyPassword = (System.getenv("KEY_PASSWORD")
                ?: providers.gradleProperty("KEY_PASSWORD").orNull
                ?: System.getenv("KEYSTORE_PASSWORD"))
            // 签名信息缺失时不要拖垮 debug 构建
            if (storePassword == null || keyAlias == null) {
                logger.lifecycle("[mtbridge] release 签名信息缺失，assembleRelease 不可用")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 仅在签名信息齐备时启用；否则 release 会构建出未签名包
            if (System.getenv("KEYSTORE_PASSWORD") != null ||
                providers.gradleProperty("KEYSTORE_PASSWORD").isPresent
            ) {
                signingConfig = signingConfigs.getByName("release")
            }
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures { compose = true }

    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.documentfile:documentfile:1.0.1")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
