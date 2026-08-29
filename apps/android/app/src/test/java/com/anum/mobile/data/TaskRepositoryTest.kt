package com.anum.mobile.data

import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

class TaskRepositoryTest {
    @Test fun createAndRunUsesCreatedTaskId() = runTest {
        val api = FakeApi()
        val result = TaskRepository(api).createAndRun("Prepare the release summary")
        assertEquals("task_1", api.runId)
        assertEquals("Prepare the release summary", api.created?.title)
        assertEquals(TaskStatus.COMPLETED, result.task.status)
    }

    @Test fun approvalQueueContainsOnlyPendingDecisions() = runTest {
        val api = FakeApi()
        api.approvalItems = listOf(approval("a1", ApprovalStatus.PENDING), approval("a2", ApprovalStatus.APPROVED))
        assertEquals(listOf("a1"), TaskRepository(api).pendingApprovals().map { it.id })
    }

    private class FakeApi : AnumApi {
        var created: TaskCreate? = null
        var runId: String? = null
        var approvalItems = emptyList<Approval>()
        override suspend fun createTask(request: TaskCreate): Task { created = request; return task(TaskStatus.CREATED) }
        override suspend fun runTask(id: String): RunTaskResponse { runId = id; return RunTaskResponse(task(TaskStatus.COMPLETED), run()) }
        override suspend fun approvals() = approvalItems
        override suspend fun approve(id: String) = decision(id, ApprovalStatus.APPROVED)
        override suspend fun reject(id: String) = decision(id, ApprovalStatus.REJECTED)
        override suspend fun getTask(id: String) = task(TaskStatus.CREATED)
        override suspend fun cancelTask(id: String) = task(TaskStatus.CANCELLED)
        override suspend fun getRun(id: String) = run()
    }

    companion object {
        private fun task(status: TaskStatus) = Task("task_1", "Title", "Prompt", status, "tenant", "workspace", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
        private fun run() = AgentRun("run_1", "task_1", TaskStatus.COMPLETED, createdAt = "2026-01-01T00:00:00Z", updatedAt = "2026-01-01T00:00:00Z")
        private fun approval(id: String, status: ApprovalStatus) = Approval(id, "task_1", "external.action", "high", status, "Review", "2026-01-01T00:00:00Z")
        private fun decision(id: String, status: ApprovalStatus) = ApprovalDecisionResponse(approval(id, status), task(TaskStatus.COMPLETED), run())
    }
}
