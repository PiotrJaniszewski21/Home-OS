import SwiftUI

struct AIChatView: View {
    @EnvironmentObject private var connection: ConnectionManager

    @State private var messages: [ChatMessage] = []
    @State private var input = ""
    @State private var isSending = false
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        if messages.isEmpty {
                            ContentUnavailableView("Ask Home OS", systemImage: "sparkles", description: Text("Search files, inspect storage, or ask questions about your server."))
                                .padding(.top, 80)
                        }
                        ForEach(messages) { message in
                            ChatBubble(message: message)
                                .id(message.id)
                        }
                    }
                    .padding(20)
                }
                .onChange(of: messages) { _, newValue in
                    guard let last = newValue.last else { return }
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .padding(.horizontal)
                    .padding(.bottom, 6)
            }

            HStack(alignment: .bottom, spacing: 10) {
                TextField("Message Home OS…", text: $input, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                    .onSubmit(send)
                Button("Send", systemImage: "paperplane.fill", action: send)
                    .buttonStyle(.borderedProminent)
                    .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSending)
            }
            .padding(14)
            .background(.regularMaterial)
        }
        .navigationTitle("AI Assistant")
    }

    private func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let client = connection.client else { return }
        input = ""
        errorMessage = nil
        messages.append(ChatMessage(role: .user, content: text))
        isSending = true

        let history = messages.dropLast().map { ["role": $0.role.rawValue, "content": $0.content] }
        Task {
            do {
                let response = try await client.sendAIMessage(message: text, history: Array(history))
                if let answer = response.data?.response {
                    messages.append(ChatMessage(role: .assistant, content: answer))
                } else {
                    errorMessage = response.error ?? "No AI response received."
                }
            } catch {
                errorMessage = error.localizedDescription
            }
            isSending = false
        }
    }
}

private struct ChatBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.role == .assistant { bubble; Spacer(minLength: 48) }
            else { Spacer(minLength: 48); bubble }
        }
    }

    private var bubble: some View {
        Group {
            if message.role == .assistant {
                Text(message.content)
                    .textSelection(.enabled)
                    .padding(12)
                    .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            } else {
                Text(message.content)
                    .textSelection(.enabled)
                    .padding(12)
                    .background(.blue.opacity(0.18), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
        }
    }
}
