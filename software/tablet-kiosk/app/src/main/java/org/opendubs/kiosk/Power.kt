package org.opendubs.kiosk

import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.View
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * The tablet's power (notes/design/power-and-shutdown.md §9, decided 2026-10-21). The tablet lives on
 * the robot and is powered from the drive bus, so losing power means the robot is off or e-stopped.
 * Driven by ACTION_POWER_CONNECTED / ACTION_POWER_DISCONNECTED:
 *
 *  - Power lost: BATTERY MODE at once. The face reads it from the bridge's `power()` and saves what it
 *    can (half brightness, half the frame rate, camera tracking off). Then the screen SLEEPS after
 *    ROBOT_OFF_MS if the robot's dashboard is not answering (the robot is off), or after ROBOT_ON_MS if it
 *    is (e-stopped: the stopped face stays visible for a while). A touch, or a wake by hand, starts the
 *    count again, so the screen never goes off under someone's finger.
 *  - Power back: the screen WAKES by itself and the face returns.
 *  - Low battery (under LOW_PCT on battery): asleep at once, and kept asleep until power returns. Woken
 *    by hand, it shows a "charge me" screen for CHARGE_ME_MS, then sleeps again.
 *  - From the dashboard: a deliberate sleep, and a restart (guarded there: never during a show). Asked
 *    for through `/api/tablet/command`, which this polls every POLL_S from native code, so a restart
 *    reaches a tablet whose page is dead; the page passes the same command on through the bridge
 *    when it is alive. Each command runs once, by its id, whichever way it came first.
 *
 * Sleeping is the device owner's lockNow(); lock task keeps the keyguard off, so a wake lands on the
 * face. Not built for the display role (a projector has no battery and is no device owner).
 */
class Power(private val activity: KioskActivity, private val origin: () -> String, private val chargeMe: View) {
    private val dpm = activity.getSystemService(DevicePolicyManager::class.java)
    private val pm = activity.getSystemService(PowerManager::class.java)
    private val prefs = activity.getSharedPreferences("power", Context.MODE_PRIVATE)
    private val main = Handler(Looper.getMainLooper())
    private val net = Executors.newSingleThreadScheduledExecutor()

    @Volatile private var answeredAt = 0L      // elapsedRealtime of the dashboard's last answer
    private var plugged = true
    private var level = -1
    private var awakeSince = 0L                // on battery: power lost, or the screen last woken
    private var low = false                    // asleep for a low battery until power returns

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            when (i.action) {
                Intent.ACTION_POWER_CONNECTED -> powerBack()
                Intent.ACTION_POWER_DISCONNECTED -> powerLost()
                Intent.ACTION_SCREEN_ON -> screenOn()
                Intent.ACTION_BATTERY_CHANGED -> {
                    val l = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                    val s = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
                    level = if (l < 0 || s <= 0) -1 else l * 100 / s
                }
            }
        }
    }

    private val tick = object : Runnable {
        override fun run() { check(); main.postDelayed(this, TICK_MS) }
    }

    fun start() {
        val f = IntentFilter().apply {
            addAction(Intent.ACTION_POWER_CONNECTED); addAction(Intent.ACTION_POWER_DISCONNECTED)
            addAction(Intent.ACTION_SCREEN_ON); addAction(Intent.ACTION_BATTERY_CHANGED)
        }
        val sticky = activity.registerReceiver(receiver, f)
        plugged = (sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        if (!plugged) awakeSince = SystemClock.elapsedRealtime()
        main.postDelayed(tick, TICK_MS)
        net.scheduleWithFixedDelay({ runCatching { poll() } }, POLL_S, POLL_S, TimeUnit.SECONDS)
    }

    fun stop() {
        runCatching { activity.unregisterReceiver(receiver) }
        main.removeCallbacksAndMessages(null)
        net.shutdownNow()
    }

    /** For the page (the bridge's `power()`): JSON {"battery_mode", "low", "level", "robot_answers",
     *  "sleep_in_s"}. `sleep_in_s` is null while plugged in. */
    fun state(): String {
        val now = SystemClock.elapsedRealtime()
        val left = if (plugged) null else ((awakeSince + limit(now) - now) / 1000).coerceAtLeast(0)
        return JSONObject().put("battery_mode", !plugged).put("low", low).put("level", level)
            .put("robot_answers", answers(now)).put("sleep_in_s", left ?: JSONObject.NULL).toString()
    }

    private fun answers(now: Long) = answeredAt != 0L && now - answeredAt < ANSWER_STALE_MS
    private fun limit(now: Long) = if (answers(now)) ROBOT_ON_MS else ROBOT_OFF_MS

    private fun powerLost() {
        plugged = false
        awakeSince = SystemClock.elapsedRealtime()
        Log.i(TAG, "power: lost, on battery ($level %)")
        check()
    }

    private fun powerBack() {
        plugged = true
        low = false
        chargeMe.visibility = View.GONE
        Log.i(TAG, "power: back, waking the screen")
        wake()
    }

    private fun screenOn() {
        if (plugged) return
        awakeSince = SystemClock.elapsedRealtime()           // woken by hand: a full period again
        if (level in 0 until LOW_PCT) low = true             // it may have run low while asleep
        if (low) {
            chargeMe.visibility = View.VISIBLE
            main.postDelayed({ if (low && !plugged) { chargeMe.visibility = View.GONE; sleep("battery low") } }, CHARGE_ME_MS)
        }
    }

    /** A touch on the screen: someone is using it, so the count to sleep starts again. */
    fun touched() { if (!plugged) awakeSince = SystemClock.elapsedRealtime() }

    private fun check() {
        if (plugged || !pm.isInteractive) return
        if (level in 0 until LOW_PCT && !low) {
            low = true
            sleep("battery low ($level %) on battery")
            return
        }
        if (low) return                                       // the charge-me screen has its own timer
        val now = SystemClock.elapsedRealtime()
        if (now - awakeSince >= limit(now))
            sleep(if (answers(now)) "on battery with the robot on (e-stopped)" else "on battery with the robot off")
    }

    /** The screen off, now. */
    fun sleep(why: String) {
        Log.i(TAG, "power: sleeping the screen: $why")
        if (!DeviceOwner.isOwner(activity)) return
        runCatching { dpm.lockNow() }.onFailure { Log.w(TAG, "power: lockNow failed: $it") }
    }

    private fun wake() {
        @Suppress("DEPRECATION")
        pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP or
                       PowerManager.ON_AFTER_RELEASE, "OpenDubsKiosk:power").acquire(WAKE_HOLD_MS)
        activity.startActivity(Intent(activity, KioskActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
    }

    /** A command from the dashboard, by its id: run once, whichever way it arrived first. The id is
     *  written down BEFORE a restart, so the restarted app does not run it again. */
    fun run(id: String, cmd: String) = main.post {
        if (id.isEmpty() || id == prefs.getString("last_command", null)) return@post
        prefs.edit().putString("last_command", id).commit()
        Log.i(TAG, "power: command $cmd ($id)")
        when (cmd) {
            "sleep" -> sleep("asked from the dashboard")
            "restart" -> if (DeviceOwner.isOwner(activity)) runCatching {
                dpm.reboot(ComponentName(activity, KioskAdminReceiver::class.java))
            }.onFailure { Log.w(TAG, "power: reboot failed: $it") }
        }
    }

    /** The dashboard's pending command, and whether it answers at all (the robot is on). */
    private fun poll() {
        val c = URL(origin().trimEnd('/') + "/api/tablet/command").openConnection() as HttpURLConnection
        c.connectTimeout = 3000; c.readTimeout = 3000
        try {
            val code = c.responseCode
            answeredAt = SystemClock.elapsedRealtime()               // any answer: the robot is on
            if (code != 200) return
            val j = JSONObject(c.inputStream.bufferedReader().readText())
            val id = j.optString("id"); val cmd = j.optString("cmd")
            if (id.isNotEmpty() && cmd.isNotEmpty()) run(id, cmd)
        } finally { c.disconnect() }
    }

    companion object {
        private const val TAG = "OpenDubsKiosk"
        const val ROBOT_OFF_MS = 2 * 60_000L       // on battery, the dashboard not answering
        const val ROBOT_ON_MS = 10 * 60_000L       // on battery, the dashboard answering (e-stopped)
        const val LOW_PCT = 15
        const val CHARGE_ME_MS = 10_000L
        const val TICK_MS = 5_000L
        const val POLL_S = 5L
        const val ANSWER_STALE_MS = 15_000L        // three missed polls: not answering
        const val WAKE_HOLD_MS = 3_000L
    }
}
