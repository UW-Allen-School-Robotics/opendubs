package org.opendubs.kiosk

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Brings the dashboard back after the app is updated. Updating stops the app, and nothing else would
 * restart it until someone pressed Home — found on the tablet: after an update the screen slept and
 * a Settings screen from an earlier maintenance visit was what lay behind. A device-owner app may
 * start an activity from the background, so it simply comes back to the front, and locks.
 */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        Log.i("OpenDubsKiosk", "updated: back to the dashboard")
        ctx.startActivity(Intent(ctx, KioskActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
