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
import android.util.Base64
import android.util.Log

/**
 * What being device owner buys (tablet-device.md, TD-3), applied every time the app starts so a
 * tablet converges on it. Every call is a no-op when the app is not device owner, so a debug build
 * also runs as an ordinary app on any tablet.
 */
object DeviceOwner {
    private const val TAG = "OpenDubsKiosk"

    /** "Stay awake while plugged in", on or off (device owner only; a no-op otherwise). The Head &
     *  face page's "keep the screen on" switches it with the window flag: with only the flag
     *  cleared, a tablet on the mount's power never slept. */
    fun setStayOn(ctx: Context, on: Boolean) {
        val dpm = ctx.getSystemService(DevicePolicyManager::class.java)
        if (!dpm.isDeviceOwnerApp(ctx.packageName)) return
        val admin = ComponentName(ctx, KioskAdminReceiver::class.java)
        val plugged = BatteryManager.BATTERY_PLUGGED_AC or BatteryManager.BATTERY_PLUGGED_USB or
            BatteryManager.BATTERY_PLUGGED_WIRELESS
        dpm.setGlobalSetting(admin, Settings.Global.STAY_ON_WHILE_PLUGGED_IN, (if (on) plugged else 0).toString())
    }

    fun isOwner(ctx: Context): Boolean =
        ctx.getSystemService(DevicePolicyManager::class.java).isDeviceOwnerApp(ctx.packageName)

    fun apply(activity: Activity) {
        val dpm = activity.getSystemService(DevicePolicyManager::class.java)
        if (!dpm.isDeviceOwnerApp(activity.packageName)) {
            Log.i(TAG, "not device owner: running as an ordinary app")
            return
        }
        val admin = ComponentName(activity, KioskAdminReceiver::class.java)

        // Lock task: no status bar, no notifications, no Home or Overview. One thing re-enabled: the
        // power menu (P-1, power-and-shutdown.md §9), so holding the side key offers Power off and
        // Restart. With NONE the long press did nothing at all. Holding it opens the menu only once
        // the setup step `power_button_long_press 1` is done (README, step 5); the keyguard stays off.
        dpm.setLockTaskPackages(admin, arrayOf(activity.packageName))
        dpm.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_GLOBAL_ACTIONS)

        // Updates never install or prompt on their own: postpone (30 days per update — Android's
        // limit). Updates are taken deliberately, between shows.
        dpm.setSystemUpdatePolicy(admin, SystemUpdatePolicy.createPostponeInstallPolicy())

        // The dashboard's microphone without a dialog — lock task hides permission dialogs.
        // And the cameras and location, for the same reason (the robot's own origin only: KioskActivity).
        for (p in listOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA,
                         Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            dpm.setPermissionGrantState(admin, activity.packageName, p,
                DevicePolicyManager.PERMISSION_GRANT_STATE_GRANTED)

        // Stay awake whenever plugged in, as a second layer under FLAG_KEEP_SCREEN_ON, unless the
        // Head & face page said to let the screen sleep (remembered across restarts).
        setStayOn(activity, Provision.screenOn(activity))

        // This app is the launcher, so a reboot comes straight back to the dashboard.
        val home = IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        dpm.addPersistentPreferredActivity(admin, home,
            ComponentName(activity, KioskActivity::class.java))
        Log.i(TAG, "device owner policies applied")
    }

    /**
     * Trust this robot's local CA (TD-1). As device owner the app installs it itself — installing a
     * CA by hand needs a lock-screen PIN, which a kiosk tablet does not want. `b64` is the CA
     * certificate (PEM or DER), base64-encoded, from deploy/make-robot-ca.sh. Returns whether it
     * is now installed.
     */
    /**
     * Remove every CA this device owner installed — a bench CA before the tablet goes to a robot, or
     * a robot's old CA before its new one. Returns how many were installed before.
     */
    fun clearCas(ctx: Context): Int {
        val dpm = ctx.getSystemService(DevicePolicyManager::class.java)
        if (!dpm.isDeviceOwnerApp(ctx.packageName)) {
            Log.w(TAG, "CAs not cleared: only the device owner can remove them")
            return 0
        }
        val admin = ComponentName(ctx, KioskAdminReceiver::class.java)
        val n = runCatching { dpm.getInstalledCaCerts(admin).size }.getOrDefault(-1)
        runCatching { dpm.uninstallAllUserCaCerts(admin) }
            .onFailure { Log.w(TAG, "CA clear FAILED: $it") }
        Log.i(TAG, "cleared installed CAs (there were $n)")
        return n
    }

    fun installCa(ctx: Context, b64: String): Boolean {
        val dpm = ctx.getSystemService(DevicePolicyManager::class.java)
        if (!dpm.isDeviceOwnerApp(ctx.packageName)) {
            Log.w(TAG, "CA not installed: only the device owner can install one")
            return false
        }
        val admin = ComponentName(ctx, KioskAdminReceiver::class.java)
        val cert = runCatching { Base64.decode(b64.trim(), Base64.DEFAULT) }.getOrNull()
        if (cert == null || cert.isEmpty()) {
            Log.w(TAG, "CA not installed: ca_b64 is not base64")
            return false
        }
        val ok = runCatching { dpm.installCaCert(admin, cert) }.getOrDefault(false)
        Log.i(TAG, if (ok) "robot CA installed" else "robot CA install FAILED")
        return ok
    }
}
