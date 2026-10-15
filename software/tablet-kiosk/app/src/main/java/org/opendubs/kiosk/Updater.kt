package org.opendubs.kiosk

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * The app updates itself from the robot (workplan-tablet.md DT-22, 5.9): the robot's dashboard serves
 * the build it wants the tablet to run (`/api/kiosk/latest` and `/api/kiosk/apk`, signed on the Thor),
 * and the app, as device owner, installs it silently. Checked 30 s after start and every 10 minutes.
 *
 * Installed ONLY if every check holds, so nothing but the team's own next build can get in:
 *  - the robot says now is a good time (`install_ok`: no show loaded or armed);
 *  - its versionCode is higher than this one's (never a downgrade, never a reinstall);
 *  - the download's sha256 is the one the robot announced;
 *  - it is this package, at that versionCode;
 *  - its signing history contains THIS app's current certificate: the same key, or a key rotated from
 *    it with a proof-of-rotation lineage (APK Signature Scheme v3). Android checks the same again.
 * Every decision is logged and kept for the page (`OpenDubsKiosk.updateStatus()`).
 *
 * There is no automatic rollback (an app cannot downgrade itself): a bad build is replaced by a newer
 * fixed one, served the same way. This updater is native code, independent of the page, so a broken
 * page can still be updated; only a build that breaks this file needs the cable.
 */
class Updater(private val ctx: Context, private val origin: () -> String) {
    private val pool = Executors.newSingleThreadScheduledExecutor()
    @Volatile var status: JSONObject = JSONObject().put("state", "idle")
        private set

    fun start() {
        pool.scheduleWithFixedDelay({ runCatching { check() }.onFailure { report("error", it.message) } },
            FIRST_CHECK_S, EVERY_S, TimeUnit.SECONDS)
    }

    /** Check now (the page can ask, e.g. right after a deploy). */
    fun checkSoon() { pool.execute { runCatching { check() }.onFailure { report("error", it.message) } } }

    internal fun report(state: String, detail: String? = null, extra: Map<String, Any?> = emptyMap()) {
        val s = JSONObject().put("state", state).put("at", System.currentTimeMillis()).put("installed", ownVersion())
        detail?.let { s.put("detail", it) }
        extra.forEach { (k, v) -> s.put(k, v) }
        status = s
        Log.i(TAG, "update: $state${detail?.let { " ($it)" } ?: ""}")
    }

    private fun ownInfo(): PackageInfo =
        ctx.packageManager.getPackageInfo(ctx.packageName, PackageManager.GET_SIGNING_CERTIFICATES)

    private fun ownVersion(): Long = ownInfo().longVersionCode

    private fun latest(): JSONObject {
        val c = URL(origin().trimEnd('/') + "/api/kiosk/latest").openConnection() as HttpURLConnection
        c.connectTimeout = 5000; c.readTimeout = 5000
        try {
            if (c.responseCode == 404) return JSONObject()           // the robot serves no build
            return JSONObject(c.inputStream.bufferedReader().readText())
        } finally { c.disconnect() }
    }

    private fun check() {
        val l = latest()
        if (!l.has("versionCode")) { report("none", "the robot serves no kiosk build"); return }
        val want = l.getLong("versionCode")
        val have = ownVersion()
        if (want <= have) { report("current", "the robot serves $want; this is $have"); return }
        if (!l.optBoolean("install_ok", false)) {
            report("waiting", "a newer build ($want) waits: ${l.optString("why", "not now")}", mapOf("offered" to want)); return
        }
        report("downloading", "build $want", mapOf("offered" to want))
        val apk = File(ctx.cacheDir, "update.apk")
        download(apk)
        val sha = sha256(apk)
        if (!sha.equals(l.optString("sha256"), ignoreCase = true)) { refuse(apk, "sha256 $sha is not the one announced"); return }
        val why = verify(apk, want)
        if (why != null) { refuse(apk, why); return }
        if (!latest().optBoolean("install_ok", false)) {            // a show may have started meanwhile
            apk.delete(); report("waiting", "a show started before the install", mapOf("offered" to want)); return
        }
        install(apk, want)
    }

    private fun refuse(apk: File, why: String) { apk.delete(); report("refused", why) }

    private fun download(to: File) {
        val c = URL(origin().trimEnd('/') + "/api/kiosk/apk").openConnection() as HttpURLConnection
        c.connectTimeout = 5000; c.readTimeout = 30000
        try {
            if (c.responseCode != 200) throw IllegalStateException("apk: HTTP ${c.responseCode}")
            c.inputStream.use { i -> to.outputStream().use { o -> i.copyTo(o) } }
        } finally { c.disconnect() }
    }

    /** Why `apk` may not be installed, or null if it may. */
    private fun verify(apk: File, want: Long): String? {
        val pm = ctx.packageManager
        val info = pm.getPackageArchiveInfo(apk.path, PackageManager.GET_SIGNING_CERTIFICATES)
            ?: return "not an APK this tablet can read"
        if (info.packageName != ctx.packageName) return "it is ${info.packageName}, not ${ctx.packageName}"
        if (info.longVersionCode != want) return "it is version ${info.longVersionCode}, announced $want"
        val theirs = info.signingInfo ?: return "it carries no signature this tablet can read"
        if (theirs.hasMultipleSigners()) return "it has several signers: refused"
        val mine = ownInfo().signingInfo?.apkContentsSigners?.firstOrNull() ?: return "this app's own certificate is unreadable"
        val history = theirs.signingCertificateHistory ?: return "its signing history is unreadable"
        if (history.none { it == mine }) return "it is not signed by this app's key, nor by a key rotated from it"
        return null
    }

    private fun install(apk: File, want: Long) {
        report("installing", "build $want", mapOf("offered" to want))
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { s ->
            apk.inputStream().use { i -> s.openWrite("base.apk", 0, apk.length()).use { o -> i.copyTo(o); s.fsync(o) } }
            val result = PendingIntent.getBroadcast(ctx, id, Intent(ctx, InstallResult::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE)
            s.commit(result.intentSender)                   // the app is replaced and UpdateReceiver brings it back
        }
    }

    /** The install's outcome. A success never arrives here: the app is stopped and replaced first. */
    class InstallResult : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            val code = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
            val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
            if (code == PackageInstaller.STATUS_PENDING_USER_ACTION)
                Log.w(TAG, "update: Android asked for a confirmation; not given (is this app still device owner?)")
            else
                Log.i(TAG, "update: install finished, status $code ${msg ?: ""}")
            current?.let { it.report(if (code == PackageInstaller.STATUS_SUCCESS) "installed" else "failed", "status $code ${msg ?: ""}".trim()) }
        }
    }

    companion object {
        private const val TAG = "OpenDubsKiosk"
        private const val FIRST_CHECK_S = 30L          // let the dashboard come up first
        private const val EVERY_S = 600L
        @Volatile var current: Updater? = null

        fun sha256(f: File): String {
            val d = MessageDigest.getInstance("SHA-256")
            f.inputStream().use { i -> val b = ByteArray(1 shl 16); while (true) { val n = i.read(b); if (n < 0) break; d.update(b, 0, n) } }
            return d.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
