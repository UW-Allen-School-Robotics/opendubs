package org.opendubs.kiosk

import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * The whole app: the robot's dashboard, full screen, awake, locked in front. A shell, never a UI of
 * its own (tablet-device.md, "The scope rule").
 *
 * The dashboard address is res/values/strings.xml `default_url`, overridden per tablet by starting
 * this activity with a string extra `url` (remembered). An extra `ca_b64` installs this robot's
 * local CA (device owner only). An extra `unlock=true` leaves lock task, for maintenance over adb.
 */
class KioskActivity : Activity() {
    private lateinit var web: WebView
    private var maintenance = false     // after `unlock=true`: stay out of lock task until restart

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        DeviceOwner.apply(this)

        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false      // show music starts on cue
            webViewClient = WebViewClient()                        // stay inside the app
            webChromeClient = object : WebChromeClient() {
                // The microphone, and only the microphone, and only for the dashboard's own origin.
                override fun onPermissionRequest(request: PermissionRequest) {
                    val wanted = request.resources.filter { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE }
                    if (wanted.isNotEmpty() && sameOrigin(request.origin, dashboardUrl()))
                        request.grant(wanted.toTypedArray())
                    else
                        request.deny()
                }
            }
            addJavascriptInterface(KioskBridge(this@KioskActivity), "OpenDubsKiosk")
        }
        setContentView(web)
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    override fun onResume() {
        super.onResume()
        hideSystemBars()
        enterLockTaskIfAllowed()
    }

    /** Loads the dashboard on first start or when given a new `url`; otherwise leaves the page
     *  alone — bridge `wake()` brings this activity forward and must not reload the dashboard. */
    private fun handle(intent: Intent?) {
        val newUrl = intent?.getStringExtra("url")
        newUrl?.let { getPreferences(MODE_PRIVATE).edit().putString("url", it).apply() }
        intent?.getStringExtra("ca_b64")?.let { DeviceOwner.installCa(this, it) }
        if (intent?.getBooleanExtra("unlock", false) == true) {
            maintenance = true
            runCatching { stopLockTask() }
            Log.i("OpenDubsKiosk", "left lock task for maintenance, until the app restarts")
        }
        if (web.url == null || newUrl != null) web.loadUrl(dashboardUrl())
    }

    private fun dashboardUrl(): String =
        getPreferences(MODE_PRIVATE).getString("url", null) ?: getString(R.string.default_url)

    private fun sameOrigin(a: Uri, b: String): Boolean {
        val u = Uri.parse(b)
        return a.scheme == u.scheme && a.host == u.host && a.port == u.port
    }

    private fun enterLockTaskIfAllowed() {
        val am = getSystemService(ActivityManager::class.java)
        if (!maintenance && DeviceOwner.isOwner(this) &&
            am.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE)
            runCatching { startLockTask() }
    }

    private fun hideSystemBars() {
        window.insetsController?.let {
            it.hide(WindowInsets.Type.systemBars())
            it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } ?: run {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }
    }

    @Deprecated("Back would leave the dashboard; in a kiosk it goes back in the page instead.")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack()
    }
}
