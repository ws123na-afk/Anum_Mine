package dev.anum.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import dev.anum.android.auth.AuthManager
import dev.anum.android.data.model.TenantContext
import dev.anum.android.ui.login.LoginScreen
import dev.anum.android.ui.navigation.AnumNavHost
import dev.anum.android.ui.theme.AnumTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

private data class SessionUiState(
    val tenantContext: TenantContext? = null,
    val isSigningIn: Boolean = false,
    val errorMessage: String? = null,
)

private class SessionViewModel(private val authManager: AuthManager) : ViewModel() {
    private val _uiState = MutableStateFlow(SessionUiState(tenantContext = authManager.currentTenantContext()))
    val uiState: StateFlow<SessionUiState> = _uiState.asStateFlow()

    fun onSignInResultReceived(result: Result<TenantContext>) {
        result.fold(
            onSuccess = { context -> _uiState.value = SessionUiState(tenantContext = context) },
            onFailure = { error ->
                _uiState.value = SessionUiState(errorMessage = error.message ?: "Sign-in failed")
            },
        )
    }

    fun onSignInStarted() {
        _uiState.value = _uiState.value.copy(isSigningIn = true, errorMessage = null)
    }

    class Factory(private val authManager: AuthManager) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = SessionViewModel(authManager) as T
    }
}

class MainActivity : ComponentActivity() {
    private val app: AnumApplication by lazy { application as AnumApplication }

    private val sessionViewModel: SessionViewModel by viewModels { SessionViewModel.Factory(app.authManager) }

    private val signInLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val data = result.data
        if (data == null) {
            sessionViewModel.onSignInResultReceived(Result.failure(IllegalStateException("Sign-in was cancelled")))
            return@registerForActivityResult
        }
        lifecycleScope.launch {
            sessionViewModel.onSignInResultReceived(app.authManager.handleSignInResult(data))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AnumTheme {
                val uiState by sessionViewModel.uiState.collectAsStateWithLifecycle()
                if (uiState.tenantContext != null) {
                    AnumNavHost(app.taskRepository, app.approvalRepository)
                } else {
                    LoginScreen(
                        isSigningIn = uiState.isSigningIn,
                        errorMessage = uiState.errorMessage,
                        onSignIn = {
                            sessionViewModel.onSignInStarted()
                            lifecycleScope.launch {
                                signInLauncher.launch(app.authManager.buildSignInIntent())
                            }
                        },
                    )
                }
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        app.authManager.dispose()
    }
}
