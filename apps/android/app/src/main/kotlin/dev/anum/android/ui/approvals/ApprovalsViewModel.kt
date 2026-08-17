package dev.anum.android.ui.approvals

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import dev.anum.android.data.model.Approval
import dev.anum.android.data.repository.ApprovalRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class ApprovalsUiState(
    val approvals: List<Approval> = emptyList(),
    val isLoading: Boolean = true,
    val errorMessage: String? = null,
    val decidingApprovalId: String? = null,
)

class ApprovalsViewModel(private val repository: ApprovalRepository) : ViewModel() {
    private val _uiState = MutableStateFlow(ApprovalsUiState())
    val uiState: StateFlow<ApprovalsUiState> = _uiState.asStateFlow()

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(isLoading = true, errorMessage = null)
            runCatching { repository.listApprovals() }
                .onSuccess { approvals ->
                    _uiState.value = _uiState.value.copy(approvals = approvals, isLoading = false)
                }
                .onFailure { error ->
                    _uiState.value = _uiState.value.copy(
                        isLoading = false,
                        errorMessage = error.message ?: "Unable to load approvals",
                    )
                }
        }
    }

    fun decide(approvalId: String, approve: Boolean) {
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(decidingApprovalId = approvalId, errorMessage = null)
            runCatching { repository.decide(approvalId, approve) }
                .onSuccess { refresh() }
                .onFailure { error ->
                    _uiState.value = _uiState.value.copy(
                        errorMessage = error.message ?: "Unable to record decision",
                    )
                }
            _uiState.value = _uiState.value.copy(decidingApprovalId = null)
        }
    }

    class Factory(private val repository: ApprovalRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = ApprovalsViewModel(repository) as T
    }
}
