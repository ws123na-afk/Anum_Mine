package com.anum.mobile.data

class TaskRepository(private val api: AnumApi) {
    suspend fun createAndRun(prompt: String): RunTaskResponse {
        val title = prompt.lineSequence().first().trim().take(80).ifEmpty { "Mobile task" }
        return api.runTask(api.createTask(TaskCreate(title, prompt)).id)
    }
    suspend fun pendingApprovals(): List<Approval> = api.approvals().filter { it.status == ApprovalStatus.PENDING }

    /** Approving sends back the payload hash that was displayed; an unbound approval cannot be approved. */
    suspend fun decide(approval: Approval, approve: Boolean): ApprovalDecisionResponse {
        if (!approve) return api.reject(approval.id)
        val hash = requireNotNull(approval.payloadHash) { "This approval is not bound to a payload hash; run the task again." }
        return api.approve(approval.id, ApprovalDecisionRequest(hash))
    }
}
