package dev.anum.android.data.api

import dev.anum.android.data.model.Approval
import dev.anum.android.data.model.ApprovalDecisionResponse
import dev.anum.android.data.model.RunTaskResponse
import dev.anum.android.data.model.Task
import dev.anum.android.data.model.TaskCreateRequest
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

/** Matches services/api/anum_api/main.py's routes exactly - see that file
 * for the canonical contract. Only the endpoints this "Now"-scoped app's
 * screens actually use are declared here (fast task capture + mobile
 * approvals, per docs/android.md's product role); it is not meant to be a
 * complete client for every ANUM endpoint. */
interface AnumApiService {
    @POST("api/v1/tasks")
    suspend fun createTask(@Body payload: TaskCreateRequest): Task

    @POST("api/v1/tasks/{taskId}/run")
    suspend fun runTask(@Path("taskId") taskId: String): RunTaskResponse

    @GET("api/v1/tasks")
    suspend fun listTasks(): List<Task>

    @GET("api/v1/approvals")
    suspend fun listApprovals(): List<Approval>

    @POST("api/v1/approvals/{approvalId}/approve")
    suspend fun approveApproval(@Path("approvalId") approvalId: String): ApprovalDecisionResponse

    @POST("api/v1/approvals/{approvalId}/reject")
    suspend fun rejectApproval(@Path("approvalId") approvalId: String): ApprovalDecisionResponse
}
