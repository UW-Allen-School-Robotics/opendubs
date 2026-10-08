package org.opendubs.kiosk

import android.app.Activity
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import android.view.WindowManager
import android.webkit.JavascriptInterface

/**
 * The dashboard's handle on the tablet, as `window.OpenDubsKiosk`. Deliberately small: what the
 * page cannot do for itself (tablet-device.md, "In the dashboard"). A page in a plain browser sees
 * no `OpenDubsKiosk` and falls back to the Screen Wake Lock API.
 */
class KioskBridge(private val activity: Activity) {

    /** Keep the screen on (true, the default) or let it sleep normally (false). */
    @JavascriptInterface
    fun keepAwake(on: Boolean) = activity.runOnUiThread {
        if (on) activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    /** Turn the screen on and bring the dashboard to the front, e.g. when a show is armed. */
    @JavascriptInterface
    fun wake() = activity.runOnUiThread {
        activity.setTurnScreenOn(true)
        activity.setShowWhenLocked(true)
        activity.startActivity(Intent(activity, KioskActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    }

    /** "on" or "off": whether the screen is interactive. */
    @JavascriptInterface
    fun screenState(): String =
        if (activity.getSystemService(PowerManager::class.java).isInteractive) "on" else "off"

    /** Whether lock task and the update policy are in force (the app is device owner). */
    @JavascriptInterface
    fun isDeviceOwner(): Boolean = DeviceOwner.isOwner(activity)

    @JavascriptInterface
    fun version(): String = BuildConfig.VERSION_NAME

    /** The tablet's own battery, as JSON `{"level": 0..100, "charging": bool, "plugged": source}` —
     *  shown beside the robot's pack in the dashboard's top bar, each with its own icon so they are
     *  not confused. `plugged` is `"ac"`, `"usb"`, `"wireless"`, `"dock"` or `null`: whether power
     *  is CONNECTED, which is not the same as charging — with a charge limit set, a tablet on the
     *  mount's power stops charging and is still plugged (workplan-tablet.md 0.3). */
    @JavascriptInterface
    fun battery(): String {
        val bm = activity.getSystemService(BatteryManager::class.java)
        val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val sticky = activity.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = when (sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) {
            BatteryManager.BATTERY_PLUGGED_AC -> "\"ac\""
            BatteryManager.BATTERY_PLUGGED_USB -> "\"usb\""
            BatteryManager.BATTERY_PLUGGED_WIRELESS -> "\"wireless\""
            8 -> "\"dock\""                         // BATTERY_PLUGGED_DOCK, API 33; min SDK is 28
            else -> "null"
        }
        return """{"level": $level, "charging": ${bm.isCharging}, "plugged": $plugged}"""
    }
}
