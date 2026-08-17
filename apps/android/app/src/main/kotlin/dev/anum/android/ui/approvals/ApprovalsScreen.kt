package dev.anum.android.ui.approvals

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.anum.android.data.model.Approval
import dev.anum.android.data.repository.ApprovalRepository

/** Mobile approvals - "supervise agents while away from the main
 * workspace" per docs/android.md. Every prompt shows the concrete action
 * and risk level (docs/approvals-and-risk.md: "avoid vague prompts such as
 * 'approve this action'") rather than a bare accept/reject on an opaque id. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ApprovalsScreen(repository: ApprovalRepository) {
    val viewModel: ApprovalsViewModel = viewModel(factory = ApprovalsViewModel.Factory(repository))
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    Scaffold(topBar = { TopAppBar(title = { Text("Approvals") }) }) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            uiState.errorMessage?.let { message -> Text(text = message) }

            if (uiState.isLoading && uiState.approvals.isEmpty()) {
                Column(
                    modifier = Modifier.fillMaxSize(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator()
                }
            } else if (uiState.approvals.isEmpty()) {
                Text("No approvals waiting.")
            } else {
                LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    contentPadding = PaddingValues(bottom = 16.dp),
                ) {
                    items(uiState.approvals, key = { it.id }) { approval ->
                        ApprovalRow(
                            approval = approval,
                            isDeciding = uiState.decidingApprovalId == approval.id,
                            onDecide = { approve -> viewModel.decide(approval.id, approve) },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ApprovalRow(approval: Approval, isDeciding: Boolean, onDecide: (Boolean) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(text = approval.action)
        Text(text = "Risk: ${approval.riskLevel.name.lowercase()}")
        Text(text = approval.reason)
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            modifier = Modifier.padding(top = 8.dp),
        ) {
            Button(onClick = { onDecide(true) }, enabled = !isDeciding) { Text("Approve") }
            OutlinedButton(onClick = { onDecide(false) }, enabled = !isDeciding) { Text("Reject") }
        }
    }
}
