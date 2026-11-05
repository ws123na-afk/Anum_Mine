package com.anum.mobile.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

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
    /** SHA-256 of the canonical tool call; approving sends it back (docs/approvals-and-risk.md). */
    @SerialName("payload_hash") val payloadHash: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("decided_by") val decidedBy: String? = null,
    /** The exact arguments, secret-looking values redacted by the API. */
    val arguments: Map<String, JsonElement> = emptyMap(),
) {
    /**
     * An action browser step that types a card's security code. ANUM never saves the code: the owner types it on
     * the web approval card, so the app does not approve it (docs/action-browser.md#the-card-security-code).
     */
    val needsCardCodeOnWeb: Boolean
        get() = action == "web.act" && (arguments["cvc"] as? JsonPrimitive)?.let { it.isString && it.content == "entered at approval" } == true
}

@Serializable data class RunTaskResponse(val task: Task, val run: AgentRun, val approval: Approval? = null)
@Serializable data class ApprovalDecisionRequest(@SerialName("payload_hash") val payloadHash: String)
@Serializable data class ApprovalDecisionResponse(val approval: Approval, val task: Task, val run: AgentRun? = null)
