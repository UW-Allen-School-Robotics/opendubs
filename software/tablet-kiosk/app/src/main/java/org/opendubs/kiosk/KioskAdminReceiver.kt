package org.opendubs.kiosk

import android.app.admin.DeviceAdminReceiver

/** Exists so the app can be the tablet's device owner. The policies are applied by [DeviceOwner]. */
class KioskAdminReceiver : DeviceAdminReceiver()
