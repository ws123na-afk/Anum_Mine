package com.anum.mobile.data

import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path

interface AnumApi {
    @POST("api/v1/tasks") suspend fun createTask(@Body request: TaskCreate): Task
    @GET("api/v1/tasks/{id}") suspend fun getTask(@Path("id") id: String): Task
    @POST("api/v1/tasks/{id}/run") suspend fun runTask(@Path("id") id: String): RunTaskResponse
    @POST("api/v1/tasks/{id}/cancel") suspend fun cancelTask(@Path("id") id: String): Task
    @GET("api/v1/agent-runs/{id}") suspend fun getRun(@Path("id") id: String): AgentRun
    @GET("api/v1/approvals") suspend fun approvals(): List<Approval>
    @POST("api/v1/approvals/{id}/approve") suspend fun approve(@Path("id") id: String): ApprovalDecisionResponse
    @POST("api/v1/approvals/{id}/reject") suspend fun reject(@Path("id") id: String): ApprovalDecisionResponse
}

data class SessionContext(val tenantId: String, val workspaceId: String, val userId: String, val roles: Set<String>)
interface AccessTokenProvider { fun accessToken(): String? }
