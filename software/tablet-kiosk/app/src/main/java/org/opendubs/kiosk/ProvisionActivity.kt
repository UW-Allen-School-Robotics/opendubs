package org.opendubs.kiosk

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.util.Log

/**
 * The provisioning door, for adb only: `adb shell am start -n org.opendubs.kiosk/.ProvisionActivity`
 * with any of these extras (README, steps 4 and Maintenance):
 *
 *   url       string   the dashboard's address, remembered
 *   ca_b64    string   install this robot's local CA (device owner only)
 *   ca_clear  boolean  first remove every CA installed before (send with ca_b64 to replace one)
 *   unlock    boolean  leave lock task, for maintenance
 *   lock      boolean  end maintenance, back into lock task
 *
 * WHY A SEPARATE, GUARDED COMPONENT. These extras used to go to KioskActivity, which must be
 * exported (it is the launcher), so any app on the tablet could start it with them: point the
 * kiosk at another page, install a CA of its choosing, or leave lock task. This activity requires
 * `android.permission.DUMP`, which the adb shell holds and no ordinary app can be granted. It does
 * the work itself and hands unlock/lock to KioskActivity in-process ([Provision.pending]), so
 * nothing outside the app can reach them. KioskActivity ignores these extras (Hygiene S7).
 */
class ProvisionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val i = intent
        val url = i.getStringExtra("url")
        url?.let { Provision.saveUrl(this, it) }
        // Clear before install, so `ca_clear` + `ca_b64` in one start replaces the CA.
        if (i.getBooleanExtra("ca_clear", false)) DeviceOwner.clearCas(this)
        i.getStringExtra("ca_b64")?.let { DeviceOwner.installCa(this, it) }
        Provision.pending = Provision.Request(
            reload = url != null,
            unlock = i.getBooleanExtra("unlock", false),
            lock = i.getBooleanExtra("lock", false))
        Log.i(TAG, "provisioning: url=${url ?: "(unchanged)"} ca_clear=${i.getBooleanExtra("ca_clear", false)} " +
            "ca_b64=${i.hasExtra("ca_b64")} unlock=${Provision.pending?.unlock} lock=${Provision.pending?.lock}")
        startActivity(Intent(this, KioskActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        finish()
    }

    private companion object { const val TAG = "OpenDubsKiosk" }
}

/** What a provisioning start asks of KioskActivity, and where the dashboard's address is kept. */
object Provision {
    data class Request(val reload: Boolean, val unlock: Boolean, val lock: Boolean)

    /** Set by ProvisionActivity, taken once by KioskActivity. Main thread only. */
    var pending: Request? = null

    fun take(): Request? = pending.also { pending = null }

    /** The extras KioskActivity used to accept, so it can say where they go now. */
    val EXTRAS = setOf("url", "ca_b64", "ca_clear", "unlock", "lock")

    // The file KioskActivity.getPreferences() wrote before this split, so a tablet keeps the
    // address it was given across the update.
    private const val PREFS = "KioskActivity"

    fun url(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString("url", null)

    fun saveUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString("url", url).apply()
    }

    /** The Head & face page's "keep the screen on" (default on), remembered so a restart honours it. */
    fun screenOn(ctx: Context): Boolean =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("screen_on", true)

    fun saveScreenOn(ctx: Context, on: Boolean) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("screen_on", on).apply()
    }
}
