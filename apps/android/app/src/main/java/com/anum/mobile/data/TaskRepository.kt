package com.anum.mobile.data

class TaskRepository(private val api: AnumApi) {
    suspend fun createAndRun(prompt: String): RunTaskResponse {
        val title = prompt.lineSequence().first().trim().take(80).ifEmpty { "Mobile task" }
        return api.runTask(api.createTask(TaskCreate(title, prompt)).id)
    }
    suspend fun pendingApprovals(): List<Approval> = api.approvals().filter { it.status == ApprovalStatus.PENDING }
    suspend fun decide(id: String, approve: Boolean): ApprovalDecisionResponse =
        if (approve) api.approve(id) else api.reject(id)
}
