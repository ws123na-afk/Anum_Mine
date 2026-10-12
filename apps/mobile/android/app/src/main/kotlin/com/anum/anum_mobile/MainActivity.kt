package com.anum.anum_mobile

import android.os.Bundle
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity

class MainActivity : FlutterActivity() {
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
}
