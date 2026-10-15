package org.opendubs.kiosk

import android.app.Activity
import android.content.Intent
import android.content.IntentFilter
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.view.WindowManager
import android.webkit.JavascriptInterface

/**
 * The dashboard's handle on the tablet, as `window.OpenDubsKiosk`. Deliberately small: what the
 * page cannot do for itself (tablet-device.md, "In the dashboard"). A page in a plain browser sees
 * no `OpenDubsKiosk` and falls back to the Screen Wake Lock API.
 */
class KioskBridge(private val activity: Activity) {

    // ---- sensors the WebView does not expose (workplan-tablet.md DT-4) -------------------------
    // Started and stopped with the activity (onResume / onPause): nothing is read while the kiosk is
    // not in front. Each reading keeps its age, so a page can tell a stale value from a live one.
    private val sensors = activity.getSystemService(SensorManager::class.java)
    private val cameraManager = activity.getSystemService(CameraManager::class.java)
    @Volatile private var lux: Float = Float.NaN
    @Volatile private var luxAt: Long = 0                  // elapsedRealtime ms of the last reading
    private val openCameras = java.util.Collections.synchronizedSet(mutableSetOf<String>())
    private var started = false

    private val lightListener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) { lux = e.values[0]; luxAt = SystemClock.elapsedRealtime() }
        override fun onAccuracyChanged(s: Sensor?, a: Int) {}
    }
    // Any camera opened by anyone (this page, a stream, another app): the face's camera indicator is
    // driven from the hardware's own state, so it cannot say "off" while a camera is in fact open.
    private val cameraWatch = object : CameraManager.AvailabilityCallback() {
        override fun onCameraUnavailable(id: String) { openCameras.add(id) }
        override fun onCameraAvailable(id: String) { openCameras.remove(id) }
    }

    fun start() {
        if (started) return
        started = true
        sensors.getDefaultSensor(Sensor.TYPE_LIGHT)?.let {
            sensors.registerListener(lightListener, it, SensorManager.SENSOR_DELAY_NORMAL)
        }
        cameraManager.registerAvailabilityCallback(cameraWatch, Handler(Looper.getMainLooper()))
    }

    fun stop() {
        if (!started) return
        started = false
        sensors.unregisterListener(lightListener)
        cameraManager.unregisterAvailabilityCallback(cameraWatch)
        openCameras.clear()
    }

    /** Ambient light: JSON `{"lux": n, "age_ms": n}`, or `null` with no light sensor or no reading yet. */
    @JavascriptInterface
    fun light(): String {
        if (lux.isNaN()) return "null"
        return """{"lux": $lux, "age_ms": ${SystemClock.elapsedRealtime() - luxAt}}"""
    }

    /** The cameras, as JSON `[{"id": "0", "facing": "screen"|"back"|"external", "open": bool}]`.
     *  Named by the tablet's own sides (workplan-tablet.md DT-5); with the tablet as the head,
     *  screen-side is the robot's front and back-side its rear. `open` is true while ANY app has
     *  that camera open. */
    @JavascriptInterface
    fun cameras(): String = cameraManager.cameraIdList.joinToString(",", "[", "]") { id ->
        val facing = when (cameraManager.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)) {
            CameraCharacteristics.LENS_FACING_FRONT -> "screen"
            CameraCharacteristics.LENS_FACING_BACK -> "back"
            else -> "external"
        }
        """{"id": "$id", "facing": "$facing", "open": ${id in openCameras}}"""
    }

    /** The tablet's thermal state: JSON `{"status": "none"|"light"|"moderate"|"severe"|"critical"|
     *  "emergency"|"shutdown"|null, "battery_c": n|null}`. `status` is Android's own throttling level
     *  (null before Android 10); `battery_c` is the battery's temperature, the one the tablet reports. */
    @JavascriptInterface
    fun thermal(): String {
        val status = if (Build.VERSION.SDK_INT >= 29) {
            when (activity.getSystemService(PowerManager::class.java).currentThermalStatus) {
                PowerManager.THERMAL_STATUS_NONE -> "none"
                PowerManager.THERMAL_STATUS_LIGHT -> "light"
                PowerManager.THERMAL_STATUS_MODERATE -> "moderate"
                PowerManager.THERMAL_STATUS_SEVERE -> "severe"
                PowerManager.THERMAL_STATUS_CRITICAL -> "critical"
                PowerManager.THERMAL_STATUS_EMERGENCY -> "emergency"
                PowerManager.THERMAL_STATUS_SHUTDOWN -> "shutdown"
                else -> null
            }
        } else null
        val sticky = activity.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val tenths = sticky?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE
        val batteryC = if (tenths == Int.MIN_VALUE) "null" else (tenths / 10.0).toString()
        return """{"status": ${status?.let { "\"$it\"" } ?: "null"}, "battery_c": $batteryC}"""
    }

    /** Keep the screen on (true, the default) or let it sleep normally (false): the window flag AND,
     *  as device owner, "stay awake while plugged in", remembered so a restart honours it. */
    @JavascriptInterface
    fun keepAwake(on: Boolean) = activity.runOnUiThread {
        if (on) activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        else activity.window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Provision.screenOn(activity) != on) Provision.saveScreenOn(activity, on)
        DeviceOwner.setStayOn(activity, on)
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

    /** This build's versionCode, which the robot compares with the build it serves. */
    @JavascriptInterface
    fun versionCode(): Int = BuildConfig.VERSION_CODE

    /** The self-updater's last word, JSON: {"state": "idle"|"none"|"current"|"waiting"|"downloading"|
     *  "refused"|"installing"|"installed"|"failed"|"error", "detail", "at", "installed", "offered"}. */
    @JavascriptInterface
    fun updateStatus(): String = Updater.current?.status?.toString() ?: "{\"state\": \"off\"}"

    /** Check the robot for a newer build now, rather than at the next 10-minute check. */
    @JavascriptInterface
    fun updateNow() { Updater.current?.checkSoon() }

    /** This screen's brightness, 0..1, for head protocol v5's BRIGHT (the face page fades it and
     *  applies the robot's floor). It sets the WINDOW's brightness, so it holds while the kiosk is in
     *  front and needs no permission; the system setting is untouched. */
    @JavascriptInterface
    fun setBrightness(level: Double) = activity.runOnUiThread {
        val lp = activity.window.attributes
        lp.screenBrightness = level.coerceIn(0.01, 1.0).toFloat()
        activity.window.attributes = lp
    }

    /** The media (music) stream's volume, 0..1, for head protocol v5's VOLUME. It scales show music,
     *  ambient and earcons together. Returns what the device applied: the stream has whole steps. */
    @JavascriptInterface
    fun setMediaVolume(level: Double): Double {
        val am = activity.getSystemService(AudioManager::class.java)
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val index = Math.round(level.coerceIn(0.0, 1.0) * max).toInt()
        am.setStreamVolume(AudioManager.STREAM_MUSIC, index, 0)
        return am.getStreamVolume(AudioManager.STREAM_MUSIC).toDouble() / max
    }

    @JavascriptInterface
    fun getMediaVolume(): Double {
        val am = activity.getSystemService(AudioManager::class.java)
        return am.getStreamVolume(AudioManager.STREAM_MUSIC).toDouble() / am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
    }

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
