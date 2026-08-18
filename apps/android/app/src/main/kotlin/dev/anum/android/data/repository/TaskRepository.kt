package dev.anum.android.data.repository

import dev.anum.android.data.api.AnumApiService
import dev.anum.android.data.model.RunTaskResponse
import dev.anum.android.data.model.Task
import dev.anum.android.data.model.TaskCreateRequest

class TaskRepository(private val api: AnumApiService) {
    suspend fun listTasks(): List<Task> = api.listTasks()

    /** Mirrors apps/web/src/lib/api.ts's createAndRunTask: capture is a single
     * user action (docs/android.md's "fast capture"), so this composes
     * create + run into one call rather than exposing two round trips to
     * every screen that wants to launch a task. */
    suspend fun createAndRunTask(title: String, prompt: String): RunTaskResponse {
        val created = api.createTask(TaskCreateRequest(title = title, prompt = prompt))
        return api.runTask(created.id)
    }
}
