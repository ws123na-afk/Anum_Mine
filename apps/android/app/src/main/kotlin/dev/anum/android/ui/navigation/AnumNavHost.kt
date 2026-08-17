package dev.anum.android.ui.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.List
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import dev.anum.android.data.repository.ApprovalRepository
import dev.anum.android.data.repository.TaskRepository
import dev.anum.android.ui.approvals.ApprovalsScreen
import dev.anum.android.ui.tasks.TasksScreen

private object Destinations {
    const val TASKS = "tasks"
    const val APPROVALS = "approvals"
}

/** The signed-in app shell: two tabs, matching docs/android.md's narrow
 * "Now" scope (fast capture, mobile approvals) rather than mirroring every
 * web view. */
@Composable
fun AnumNavHost(taskRepository: TaskRepository, approvalRepository: ApprovalRepository) {
    val navController = rememberNavController()
    val destinations = listOf(
        Destinations.TASKS to "Tasks",
        Destinations.APPROVALS to "Approvals",
    )

    Scaffold(
        bottomBar = {
            val backStackEntry by navController.currentBackStackEntryAsState()
            val currentDestination = backStackEntry?.destination
            NavigationBar {
                destinations.forEach { (route, label) ->
                    NavigationBarItem(
                        selected = currentDestination?.hierarchy?.any { it.route == route } == true,
                        onClick = {
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = {
                            Icon(
                                if (route == Destinations.TASKS) Icons.Filled.List else Icons.Filled.CheckCircle,
                                contentDescription = label,
                            )
                        },
                        label = { Text(label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Destinations.TASKS,
            modifier = Modifier.padding(padding),
        ) {
            composable(Destinations.TASKS) { TasksScreen(taskRepository) }
            composable(Destinations.APPROVALS) { ApprovalsScreen(approvalRepository) }
        }
    }
}
