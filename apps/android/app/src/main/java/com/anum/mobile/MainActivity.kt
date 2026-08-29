package com.anum.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognizerIntent
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.viewmodel.compose.viewModel
import com.anum.mobile.data.ApiFactory
import com.anum.mobile.data.SessionContext
import com.anum.mobile.data.TaskRepository
import com.anum.mobile.data.TokenVault
import com.anum.mobile.ui.AnumScreen
import com.anum.mobile.ui.AnumViewModel
import java.util.Locale

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val vault = TokenVault(applicationContext)
        val context = SessionContext("tenant_local", "workspace_foundation", "user_local", setOf("owner", "member"))
        val repository = TaskRepository(ApiFactory.create(vault, context))
        setContent {
            MaterialTheme {
                val model: AnumViewModel = viewModel { AnumViewModel(repository) }
                val state by model.state.collectAsState()
                val speech = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
                    result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.let(model::updatePrompt)
                }
                fun launchSpeech() = speech.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
                    putExtra(RecognizerIntent.EXTRA_PROMPT, "Describe the task")
                })
                val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
                    if (granted) launchSpeech()
                }
                AnumScreen(state, model::updatePrompt, model::submit, onVoice = {
                    if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) launchSpeech()
                    else permission.launch(Manifest.permission.RECORD_AUDIO)
                }, onRefresh = model::refreshApprovals, onDecision = model::decide)
            }
        }
    }
}
