package dev.anum.android

import android.app.Application
import dev.anum.android.auth.AuthManager
import dev.anum.android.data.api.AnumApiService
import dev.anum.android.data.api.NetworkModule
import dev.anum.android.data.repository.ApprovalRepository
import dev.anum.android.data.repository.TaskRepository

/** The app's composition root - see NetworkModule's note on why this is a
 * plain object graph instead of a DI framework. */
class AnumApplication : Application() {
    lateinit var authManager: AuthManager
        private set

    private lateinit var apiService: AnumApiService

    lateinit var taskRepository: TaskRepository
        private set

    lateinit var approvalRepository: ApprovalRepository
        private set

    override fun onCreate() {
        super.onCreate()
        authManager = AuthManager(this)
        apiService = NetworkModule.buildApiService(authManager)
        taskRepository = TaskRepository(apiService)
        approvalRepository = ApprovalRepository(apiService)
    }
}
