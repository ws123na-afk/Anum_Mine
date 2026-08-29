package com.anum.mobile.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable data class TaskCreate(val title: String, val prompt: String)

@Serializable
data class Task(
    val id: String,
    val title: String,
    val prompt: String,
    val status: TaskStatus,
    @SerialName("tenant_id") val tenantId: String,
    @SerialName("workspace_id") val workspaceId: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
enum class TaskStatus {
    @SerialName("created") CREATED,
    @SerialName("queued") QUEUED,
    @SerialName("running") RUNNING,
    @SerialName("waiting_approval") WAITING_APPROVAL,
    @SerialName("completed") COMPLETED,
    @SerialName("failed") FAILED,
    @SerialName("cancelled") CANCELLED,
}

@Serializable
data class AgentRunStep(
    val id: String,
    val type: String,
    val summary: String,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class AgentRun(
    val id: String,
    @SerialName("task_id") val taskId: String,
    val status: TaskStatus,
    val steps: List<AgentRunStep> = emptyList(),
    val result: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
enum class ApprovalStatus {
    @SerialName("pending") PENDING,
    @SerialName("approved") APPROVED,
    @SerialName("rejected") REJECTED,
    @SerialName("expired") EXPIRED,
}

@Serializable
data class Approval(
    val id: String,
    @SerialName("task_id") val taskId: String,
    val action: String,
    @SerialName("risk_level") val riskLevel: String,
    val status: ApprovalStatus,
    val reason: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("decided_at") val decidedAt: String? = null,
)

@Serializable data class RunTaskResponse(val task: Task, val run: AgentRun, val approval: Approval? = null)
@Serializable data class ApprovalDecisionResponse(val approval: Approval, val task: Task, val run: AgentRun? = null)
