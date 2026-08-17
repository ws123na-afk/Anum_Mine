package dev.anum.android.ui.login

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
fun LoginScreen(isSigningIn: Boolean, errorMessage: String?, onSignIn: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("ANUM")
        Text("Sign in to capture and approve tasks on the go.")
        if (isSigningIn) {
            CircularProgressIndicator(modifier = Modifier.padding(top = 16.dp))
        } else {
            Button(onClick = onSignIn, modifier = Modifier.padding(top = 16.dp)) {
                Text("Sign in")
            }
        }
        errorMessage?.let { message -> Text(text = message, modifier = Modifier.padding(top = 8.dp)) }
    }
}
