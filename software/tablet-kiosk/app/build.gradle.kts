plugins {
    id("com.android.application")
}

android {
    namespace = "org.opendubs.kiosk"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.opendubs.kiosk"
        minSdk = 28          // lock task features (setLockTaskFeatures) arrived in Android 9
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        debug {
            // A debug build is testOnly, so a device owner provisioned with it can be removed again
            // with `adb shell dpm remove-active-admin` instead of a factory reset.
        }
        release {
            isMinifyEnabled = false
        }
    }
    buildFeatures {
        buildConfig = true
    }
}
