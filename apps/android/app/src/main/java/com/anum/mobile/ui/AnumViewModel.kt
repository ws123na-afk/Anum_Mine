package com.anum.mobile.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.anum.mobile.data.Approval
import com.anum.mobile.data.RunTaskResponse
import com.anum.mobile.data.TaskRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class AnumUiState(
    val prompt: String = "",
    val activeRun: RunTaskResponse? = null,
    val approvals: List<Approval> = emptyList(),
    val loading: Boolean = false,
    val message: String? = null,
)

class AnumViewModel(private val repository: TaskRepository) : ViewModel() {
    private val mutableState = MutableStateFlow(AnumUiState())
    val state: StateFlow<AnumUiState> = mutableState.asStateFlow()

    init { refreshApprovals() }
    fun updatePrompt(value: String) { mutableState.value = mutableState.value.copy(prompt = value) }
    fun submit() {
        val prompt = state.value.prompt.trim()
        if (prompt.isEmpty() || state.value.loading) return
        execute {
            val result = repository.createAndRun(prompt)
            mutableState.value.copy(prompt = "", activeRun = result, message = "Task submitted")
        }
    }
    fun refreshApprovals() = execute { mutableState.value.copy(approvals = repository.pendingApprovals()) }
    fun decide(approval: Approval, approve: Boolean) = execute {
        val result = repository.decide(approval.id, approve)
        val active = state.value.activeRun?.takeIf { it.task.id != result.task.id }
            ?: result.run?.let { RunTaskResponse(result.task, it, result.approval) }
        mutableState.value.copy(
            activeRun = active,
            approvals = repository.pendingApprovals(),
            message = if (approve) "Action approved" else "Action rejected",
        )
    }
    fun dismissMessage() { mutableState.value = mutableState.value.copy(message = null) }

    private fun execute(block: suspend () -> AnumUiState) {
        viewModelScope.launch {
            mutableState.value = mutableState.value.copy(loading = true, message = null)
            mutableState.value = try { block().copy(loading = false) }
            catch (error: Exception) { mutableState.value.copy(loading = false, message = error.message ?: "Request failed") }
        }
    }
}
