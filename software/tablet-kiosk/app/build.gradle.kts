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
        versionCode = 4
        versionName = "0.3.1"
    }

    buildTypes {
        debug {
            // A debug build is testOnly, so a device owner provisioned with it can be removed again
            // with `adb shell dpm remove-active-admin` instead of a factory reset.
        }
        release {
            // UNSIGNED here, on purpose: the robot's Thor signs it with the release key (and the
            // proof-of-rotation lineage from the old debug key) and serves it to the tablet, which
            // installs it itself (Updater.kt). Never a key in this repository.
            isMinifyEnabled = false
        }
    }
    buildFeatures {
        buildConfig = true
    }
}

// What the Thor's signing script needs besides the APK, so it reads no Android tooling: written next to
// app-release-unsigned.apk by assembleRelease.
val kioskVersionJson by tasks.registering {
    val out = layout.buildDirectory.file("outputs/apk/release/kiosk-version.json")
    val code = android.defaultConfig.versionCode
    val name = android.defaultConfig.versionName
    outputs.file(out)
    doLast { out.get().asFile.writeText("""{"versionCode": $code, "versionName": "$name"}""" + "\n") }
}
tasks.matching { it.name == "assembleRelease" }.configureEach { finalizedBy(kioskVersionJson) }
