package org.opendubs.kiosk

import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.content.Intent
import android.net.Uri
import android.net.http.SslError
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Html
import android.util.Log
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import java.text.DateFormat
import java.util.Date

/**
 * The whole app: the robot's dashboard, full screen, awake, locked in front. A shell, never a UI of
 * its own (tablet-device.md, "The scope rule").
 *
 * The dashboard address is res/values/strings.xml `default_url`, overridden per tablet through
 * [ProvisionActivity] (remembered), which also installs the robot's CA and starts and ends
 * maintenance. This activity takes no settings from an intent: it is exported, being the launcher,
 * so anything that could start it could have changed them. `unlock` leaves lock task, for maintenance
 * over adb, and `lock` ends that maintenance — a device-owner app cannot be force-stopped, so
 * "until the app restarts" alone would never come.
 */
class KioskActivity : Activity() {
    private lateinit var web: WebView
    private lateinit var bridge: KioskBridge
    private var maintenance = false     // after `unlock`: stay out of lock task until `lock` or a restart

    // WAITING FOR THE ROBOT. The tablet often starts before the Thor (both come up at RUN, and the
    // Thor is slower), and a WebView that cannot reach its page shows "Webpage not available" and
    // never tries again — so the dashboard stayed dead until someone restarted the app. Now a failed
    // load shows a waiting page and retries, backing off from 2 s to 10 s, until the dashboard loads.
    private val retry = Handler(Looper.getMainLooper())
    private var retryDelayMs = RETRY_FIRST_MS
    private var failingSince: Long = 0
    private var loadFailed = false
    private val retryLoad = Runnable { loadFailed = false; web.loadUrl(dashboardUrl()) }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        DeviceOwner.apply(this)

        web = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.mediaPlaybackRequiresUserGesture = false      // show music starts on cue
            settings.setGeolocationEnabled(true)                   // the robot's GPS (granted below)
            webViewClient = object : WebViewClient() {             // stay inside the app
                override fun onReceivedError(v: WebView, req: WebResourceRequest, err: WebResourceError) {
                    if (req.isForMainFrame) waitAndRetry(err.description?.toString() ?: "unreachable")
                }
                override fun onReceivedHttpError(v: WebView, req: WebResourceRequest, res: WebResourceResponse) {
                    if (req.isForMainFrame && res.statusCode >= 500) waitAndRetry("HTTP ${res.statusCode}")
                }
                // An untrusted certificate is not "still starting": say what is wrong, and try less often.
                override fun onReceivedSslError(v: WebView, h: SslErrorHandler, e: SslError) {
                    h.cancel()
                    waitAndRetry("the robot's certificate is not trusted — install its CA (README, step 4)",
                                 RETRY_SLOW_MS)
                }
                override fun onPageFinished(v: WebView, url: String) {
                    if (!loadFailed && sameOrigin(Uri.parse(url), dashboardUrl())) {
                        retry.removeCallbacks(retryLoad)
                        retryDelayMs = RETRY_FIRST_MS
                        failingSince = 0
                        v.clearHistory()                           // Back never returns to the waiting page
                    }
                }
            }
            webChromeClient = object : WebChromeClient() {
                // The microphone and the cameras, and only for the robot's own origin (the dashboard and
                // the face it serves). Anything else, or any other origin, is refused.
                override fun onPermissionRequest(request: PermissionRequest) {
                    val allowed = setOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE, PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                    val wanted = request.resources.filter { it in allowed }
                    if (wanted.isNotEmpty() && sameOrigin(request.origin, dashboardUrl()))
                        request.grant(wanted.toTypedArray())
                    else
                        request.deny()
                }
                // Location, likewise: the robot's origin only, and not remembered by the WebView (the
                // app's own grant is what persists).
                override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                    callback.invoke(origin, sameOrigin(Uri.parse(origin), dashboardUrl()), false)
                }
            }
            bridge = KioskBridge(this@KioskActivity)
            addJavascriptInterface(bridge, "OpenDubsKiosk")
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
        bridge.start()
        hideSystemBars()
        enterLockTaskIfAllowed()
    }

    override fun onPause() {
        bridge.stop()
        super.onPause()
    }

    /** Loads the dashboard on first start or after a new `url`; otherwise leaves the page alone —
     *  bridge `wake()` brings this activity forward and must not reload the dashboard. */
    private fun handle(intent: Intent?) {
        val stale = intent?.extras?.keySet()?.filter { it in Provision.EXTRAS }.orEmpty()
        if (stale.isNotEmpty())
            Log.w("OpenDubsKiosk", "ignored $stale: provisioning goes to .ProvisionActivity (README)")
        val req = Provision.take()
        if (req?.unlock == true) {
            maintenance = true
            runCatching { stopLockTask() }
            Log.i("OpenDubsKiosk", "left lock task for maintenance, until lock or a restart")
        }
        if (req?.lock == true) {
            maintenance = false
            enterLockTaskIfAllowed()
            Log.i("OpenDubsKiosk", "maintenance over: back in lock task")
        }
        if (web.url == null || req?.reload == true) web.loadUrl(dashboardUrl())
    }

    /** Show what is happening, then try the dashboard again. Called on every failed attempt. */
    private fun waitAndRetry(why: String, delayMs: Long = retryDelayMs) {
        loadFailed = true
        if (failingSince == 0L) failingSince = System.currentTimeMillis()
        val url = Html.escapeHtml(dashboardUrl())
        val since = DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(failingSince))
        web.loadDataWithBaseURL(null, """<!doctype html><meta name=viewport content="width=device-width">
            <body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
                         background:#000;color:#9aa;font:22px system-ui,sans-serif;text-align:center">
            <div><div style="font-size:30px;color:#ccd">Waiting for the robot's computer…</div>
            <p>The dashboard at <b>$url</b> is not answering yet (since $since).<br>
               If RUN was just switched on, the robot's computer may still be starting.</p>
            <p style="color:#677">${Html.escapeHtml(why)} — trying again in ${delayMs / 1000} s</p></div>""",
            "text/html", "utf-8", null)
        retry.removeCallbacks(retryLoad)
        retry.postDelayed(retryLoad, delayMs)
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(RETRY_MAX_MS)
    }

    private fun dashboardUrl(): String = Provision.url(this) ?: getString(R.string.default_url)

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

    companion object {
        const val RETRY_FIRST_MS = 2_000L
        const val RETRY_MAX_MS = 10_000L
        const val RETRY_SLOW_MS = 30_000L
    }

    @Deprecated("Back would leave the dashboard; in a kiosk it goes back in the page instead.")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack()
    }
}
