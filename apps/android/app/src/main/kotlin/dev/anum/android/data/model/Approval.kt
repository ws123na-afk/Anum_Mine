package dev.anum.android.data.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
enum class RiskLevel {
    @SerialName("low") LOW,
    @SerialName("medium") MEDIUM,
    @SerialName("high") HIGH,
    @SerialName("blocked") BLOCKED,
}

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
    @SerialName("risk_level") val riskLevel: RiskLevel,
    val status: ApprovalStatus,
    val reason: String,
    @SerialName("created_at") val createdAt: String,
)

@Serializable
data class ApprovalDecisionResponse(
    val approval: Approval,
    val task: Task,
    val run: AgentRun? = null,
)
