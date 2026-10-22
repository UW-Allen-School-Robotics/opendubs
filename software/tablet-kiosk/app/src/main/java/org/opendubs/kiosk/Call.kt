package org.opendubs.kiosk

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * CALL MODE (telepresence, decided 2026-10-23): the head tablet joins a meeting in the real Zoom app,
 * started and ended from the dashboard. Zoom is the one other app lock task lets in front
 * (DeviceOwner.apply). Installing it and signing in is done once, by hand, at the tablet.
 *
 *  - `call <link>` opens the meeting link in Zoom. The face page goes to the background, so its camera
 *    and microphone are free for Zoom; the head's tracking and the robot's voice stop with it.
 *  - `endcall` brings the face back to the front and stops Zoom's background process, which ends
 *    the call. A call hung up in Zoom itself waits on Zoom's own screen until END CALL.
 *
 * The robot holds the app's self-update and refuses a show while a call is up (tablet_relay.py,
 * kiosk_update.py), so neither can pull the face over a call. Not in the display role.
 */
object Call {
    const val ZOOM = "us.zoom.videomeetings"
    private const val TAG = "OpenDubsKiosk"
    private const val KILL_AFTER_MS = 1_500L        // once the face is in front, Zoom is in the background
    @Volatile var active = false
        private set

    fun zoomInstalled(ctx: Context): Boolean =
        runCatching { ctx.packageManager.getPackageInfo(ZOOM, 0); true }.getOrDefault(false)

    fun start(activity: KioskActivity, link: String) {
        if (!zoomInstalled(activity)) { Log.w(TAG, "call: Zoom is not installed on this tablet"); return }
        val uri = Uri.parse(link)
        if (uri.scheme != "https" || !(uri.host ?: "").let { it == "zoom.us" || it.endsWith(".zoom.us") }) {
            Log.w(TAG, "call: not a Zoom meeting link"); return
        }
        active = true
        Log.i(TAG, "call: starting")
        runCatching {
            activity.startActivity(Intent(Intent.ACTION_VIEW, uri).setPackage(ZOOM)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }.onFailure { active = false; Log.w(TAG, "call: Zoom would not open the link: $it") }
    }

    fun end(activity: KioskActivity) {
        active = false
        Log.i(TAG, "call: ending")
        activity.startActivity(Intent(activity, KioskActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        Handler(Looper.getMainLooper()).postDelayed({
            runCatching { activity.getSystemService(ActivityManager::class.java).killBackgroundProcesses(ZOOM) }
                .onFailure { Log.w(TAG, "call: could not stop Zoom: $it") }
        }, KILL_AFTER_MS)
    }
}
