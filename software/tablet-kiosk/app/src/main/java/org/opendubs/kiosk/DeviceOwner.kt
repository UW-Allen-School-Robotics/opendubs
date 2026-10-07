package org.opendubs.kiosk

import android.Manifest
import android.app.Activity
import android.app.admin.DevicePolicyManager
import android.app.admin.SystemUpdatePolicy
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.provider.Settings
import android.util.Log

/**
 * What being device owner buys (tablet-device.md, TD-3), applied every time the app starts so a
 * tablet converges on it. Every call is a no-op when the app is not device owner, so a debug build
 * also runs as an ordinary app on any tablet.
 */
object DeviceOwner {
    private const val TAG = "OpenDubsKiosk"

    fun isOwner(ctx: Context): Boolean =
        ctx.getSystemService(DevicePolicyManager::class.java).isDeviceOwnerApp(ctx.packageName)

    fun apply(activity: Activity) {
        val dpm = activity.getSystemService(DevicePolicyManager::class.java)
        if (!dpm.isDeviceOwnerApp(activity.packageName)) {
            Log.i(TAG, "not device owner: running as an ordinary app")
            return
        }
        val admin = ComponentName(activity, KioskAdminReceiver::class.java)

        // Lock task: no status bar, no notifications, no Home or Overview. Nothing re-enabled.
        dpm.setLockTaskPackages(admin, arrayOf(activity.packageName))
        dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)

        // Updates never install or prompt on their own: postpone (30 days per update — Android's
        // limit). Updates are taken deliberately, between shows.
        dpm.setSystemUpdatePolicy(admin, SystemUpdatePolicy.createPostponeInstallPolicy())

        // The dashboard's microphone without a dialog — lock task hides permission dialogs.
        dpm.setPermissionGrantState(admin, activity.packageName, Manifest.permission.RECORD_AUDIO,
            DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)

        // Stay awake whenever plugged in, as a second layer under FLAG_KEEP_SCREEN_ON.
        dpm.setGlobalSetting(admin, Settings.Global.STAY_ON_WHILE_PLUGGED_IN,
            (BatteryManager.BATTERY_PLUGGED_AC or BatteryManager.BATTERY_PLUGGED_USB or
                BatteryManager.BATTERY_PLUGGED_WIRELESS).toString())

        // This app is the launcher, so a reboot comes straight back to the dashboard.
        val home = IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        dpm.addPersistentPreferredActivity(admin, home,
            ComponentName(activity, KioskActivity::class.java))
        Log.i(TAG, "device owner policies applied")
    }
}
