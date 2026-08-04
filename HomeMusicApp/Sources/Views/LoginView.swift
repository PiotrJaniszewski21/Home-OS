import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var session: AppSession
    @State private var username = ""
    @State private var password = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                Spacer()
                Image(systemName: "music.note.house.fill")
                    .font(.system(size: 72, weight: .semibold))
                    .foregroundStyle(Color.homeMusicRed)
                    .symbolRenderingMode(.hierarchical)
                VStack(spacing: 8) {
                    Text("HomeMusic").font(.largeTitle.bold())
                    Text("Your music. Your server.")
                        .foregroundStyle(.secondary)
                }
                VStack(spacing: 14) {
                    TextField("Home OS address", text: $session.serverText)
                        .textContentType(.URL).textInputAutocapitalization(.never)
                    TextField("Username", text: $username)
                        .textContentType(.username).textInputAutocapitalization(.never)
                    SecureField("Password", text: $password)
                        .textContentType(.password)
                }
                .textFieldStyle(.roundedBorder)
                Button {
                    Task { await session.signIn(username: username, password: password) }
                } label: {
                    HStack {
                        if session.isWorking { ProgressView().tint(.white) }
                        Text("Sign In").fontWeight(.semibold)
                    }
                    .frame(maxWidth: .infinity).padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
                .disabled(session.isWorking || username.isEmpty || password.isEmpty)
                if let error = session.errorMessage {
                    Text(error).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
                }
                Spacer()
            }
            .padding(28)
        }
    }
}
