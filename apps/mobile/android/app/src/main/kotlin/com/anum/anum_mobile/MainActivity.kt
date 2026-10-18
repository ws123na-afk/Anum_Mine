package com.anum.anum_mobile

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.os.Bundle
import android.provider.ContactsContract
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingPick: MethodChannel.Result? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        // Screen privacy (owner decision, 12 October 2026; docs/mobile.md#screen-privacy):
        // the window is secure, so screenshots, screen recordings, casting to an insecure
        // display and the recent-apps thumbnail show nothing of the workspace.
        window.setFlags(
            WindowManager.LayoutParams.FLAG_SECURE,
            WindowManager.LayoutParams.FLAG_SECURE,
        )
        super.onCreate(savedInstanceState)
    }

    // Phone calls (docs/calls.md#mobile): "Pick from phone" opens the system contact picker
    // for phone numbers. The picker grants read access to the one contact the person picks,
    // so the app needs no contacts permission and reads nothing else.
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "anum/contact_picker").setMethodCallHandler { call, result ->
            if (call.method != "pickContact") {
                result.notImplemented()
                return@setMethodCallHandler
            }
            if (pendingPick != null) {
                result.error("busy", "The contact picker is already open.", null)
                return@setMethodCallHandler
            }
            pendingPick = result
            try {
                startActivityForResult(Intent(Intent.ACTION_PICK, ContactsContract.CommonDataKinds.Phone.CONTENT_URI), PICK_CONTACT)
            } catch (error: ActivityNotFoundException) {
                pendingPick = null
                result.error("unavailable", "This phone has no contacts app.", null)
            }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != PICK_CONTACT) return
        val result = pendingPick ?: return
        pendingPick = null
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) {
            result.success(null)
            return
        }
        val columns = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
        )
        try {
            contentResolver.query(uri, columns, null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) {
                    result.success(mapOf("name" to (cursor.getString(0) ?: ""), "phone" to (cursor.getString(1) ?: "")))
                    return
                }
            }
            result.success(null)
        } catch (error: SecurityException) {
            result.error("denied", "The picked contact could not be read.", null)
        }
    }

    companion object {
        private const val PICK_CONTACT = 4711
    }
}
