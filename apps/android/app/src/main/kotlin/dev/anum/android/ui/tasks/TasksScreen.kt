package dev.anum.android.ui.tasks

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.SuggestionChipDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import dev.anum.android.data.model.Task
import dev.anum.android.data.model.TaskStatus
import dev.anum.android.data.repository.TaskRepository

/** Fast task capture + status, per docs/android.md's product role ("focus
 * on fast capture... task status"). Deliberately no run-step/timeline
 * detail here - that stays a web/desktop-workbench concern; this screen is
 * capture-and-glance, not a full agent-run inspector. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(repository: TaskRepository) {
    val viewModel: TasksViewModel = viewModel(factory = TasksViewModel.Factory(repository))
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    var prompt by remember { mutableStateOf("") }

    Scaffold(topBar = { TopAppBar(title = { Text("Tasks") }) }) { padding ->
        Column(modifier = Modifier.fillMaxSize().padding(padding).padding(16.dp)) {
            TextField(
                value = prompt,
                onValueChange = { prompt = it },
                label = { Text("Describe what the agent should do") },
                modifier = Modifier.fillMaxWidth(),
                enabled = !uiState.isSubmitting,
            )
            Button(
                onClick = {
                    viewModel.submitTask(prompt)
                    prompt = ""
                },
                enabled = prompt.isNotBlank() && !uiState.isSubmitting,
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            ) {
                Text(if (uiState.isSubmitting) "Running…" else "Run task")
            }

            uiState.errorMessage?.let { message ->
                Text(text = message, modifier = Modifier.padding(top = 8.dp))
            }

            if (uiState.isLoading && uiState.tasks.isEmpty()) {
                Column(
                    modifier = Modifier.fillMaxSize(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator()
                }
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize().padding(top = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(bottom = 16.dp),
                ) {
                    items(uiState.tasks, key = { it.id }) { task -> TaskRow(task) }
                }
            }
        }
    }
}

@Composable
private fun TaskRow(task: Task) {
    Column(modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Text(text = task.prompt)
        SuggestionChip(
            onClick = {},
            label = { Text(statusLabel(task.status)) },
            colors = SuggestionChipDefaults.suggestionChipColors(),
        )
    }
}

private fun statusLabel(status: TaskStatus): String = when (status) {
    TaskStatus.CREATED -> "Created"
    TaskStatus.QUEUED -> "Queued"
    TaskStatus.RUNNING -> "Running"
    TaskStatus.WAITING_APPROVAL -> "Waiting approval"
    TaskStatus.COMPLETED -> "Completed"
    TaskStatus.FAILED -> "Failed"
    TaskStatus.CANCELLED -> "Cancelled"
}
