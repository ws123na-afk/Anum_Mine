package dev.anum.android.data.repository

import dev.anum.android.data.api.AnumApiService
import dev.anum.android.data.model.Approval
import dev.anum.android.data.model.ApprovalDecisionResponse

class ApprovalRepository(private val api: AnumApiService) {
    suspend fun listApprovals(): List<Approval> = api.listApprovals()

    suspend fun decide(approvalId: String, approve: Boolean): ApprovalDecisionResponse =
        if (approve) api.approveApproval(approvalId) else api.rejectApproval(approvalId)
}
