package com.anum.mobile.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.anum.mobile.data.AgentRunStep
import com.anum.mobile.data.Approval

private val AnumGreen = Color(0xFF176B4D)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AnumScreen(state: AnumUiState, onPrompt: (String) -> Unit, onSubmit: () -> Unit,
    onVoice: () -> Unit, onRefresh: () -> Unit, onDecision: (Approval, Boolean) -> Unit) {
    Scaffold(containerColor = Color(0xFFF7F7F5), topBar = {
        TopAppBar(title = { Column { Text("ANUM", fontWeight = FontWeight.Bold); Text("Mobile supervision", style = MaterialTheme.typography.labelSmall) } },
            colors = TopAppBarDefaults.topAppBarColors(containerColor = Color(0xFFF7F7F5)),
            actions = { IconButton(onClick = onRefresh) { Icon(Icons.Default.Refresh, "Refresh approvals") } })
    }) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(16.dp), contentPadding = PaddingValues(bottom = 32.dp)) {
            state.message?.let { message -> item { Text(message, color = AnumGreen) } }
            item {
                Text("New task", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = state.prompt, onValueChange = onPrompt, modifier = Modifier.fillMaxWidth().heightIn(min = 120.dp),
                    placeholder = { Text("What should the agent handle?") }, enabled = !state.loading,
                    trailingIcon = { IconButton(onClick = onVoice) { Icon(Icons.Default.Mic, "Enter task by voice") } })
                Spacer(Modifier.height(8.dp))
                Button(onClick = onSubmit, enabled = state.prompt.isNotBlank() && !state.loading, modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = AnumGreen)) { Text(if (state.loading) "Working..." else "Run task") }
            }
            state.activeRun?.let { result ->
                item {
                    HorizontalDivider()
                    Spacer(Modifier.height(12.dp))
                    Text(result.task.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(result.task.status.name.replace('_', ' ').lowercase(), color = AnumGreen, style = MaterialTheme.typography.labelLarge)
                }
                items(result.run.steps, key = { it.id }) { TimelineStep(it) }
                result.run.result?.let { value -> item { Text(value, style = MaterialTheme.typography.bodyLarge) } }
            }
            item {
                HorizontalDivider()
                Spacer(Modifier.height(12.dp))
                Text("Pending approvals", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            }
            if (state.approvals.isEmpty()) item { Text("No actions need your decision.", color = Color.Gray) }
            items(state.approvals, key = { it.id }) { approval -> ApprovalRow(approval, state.loading, onDecision) }
        }
    }
}

@Composable private fun TimelineStep(step: AgentRunStep) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Surface(shape = MaterialTheme.shapes.small, color = Color(0xFFE2EEE8), modifier = Modifier.size(32.dp)) {
            Box(contentAlignment = Alignment.Center) { Text("•", color = AnumGreen) }
        }
        Column { Text(step.summary, fontWeight = FontWeight.Medium); Text(step.createdAt, style = MaterialTheme.typography.labelSmall, color = Color.Gray) }
    }
}

@Composable private fun ApprovalRow(approval: Approval, loading: Boolean, onDecision: (Approval, Boolean) -> Unit) {
    Surface(tonalElevation = 1.dp, shape = MaterialTheme.shapes.small) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(approval.action, fontWeight = FontWeight.SemiBold); Text("${approval.riskLevel} risk", color = Color(0xFFA03D2D))
            }
            Text(approval.reason)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { onDecision(approval, true) }, enabled = !loading, colors = ButtonDefaults.buttonColors(containerColor = AnumGreen)) {
                    Icon(Icons.Default.Check, null); Spacer(Modifier.width(4.dp)); Text("Approve")
                }
                OutlinedButton(onClick = { onDecision(approval, false) }, enabled = !loading) {
                    Icon(Icons.Default.Close, null); Spacer(Modifier.width(4.dp)); Text("Reject")
                }
            }
        }
    }
}
