import SwiftUI
import WebKit

struct MainWindowView: View {
    @EnvironmentObject var connection: ConnectionManager
    @EnvironmentObject var appState: AppState
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        if !appState.isConfigured {
            VStack(spacing: 16) {
                Image(systemName: "externaldrive.badge.xmark")
                    .font(.system(size: 40))
                    .foregroundStyle(.secondary)
                Text("Not connected")
                    .font(.title2)
                Text("Open Settings to connect to your server")
                    .foregroundStyle(.secondary)
                Button("Settings") {
                    openSettings()
                }
                .buttonStyle(.borderedProminent)
            }
            .frame(minWidth: 800, minHeight: 600)
        } else {
            WebView(url: appState.serverURL)
                .frame(minWidth: 800, minHeight: 600)
        }
    }
}

struct WebView: NSViewRepresentable {
    let url: String

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.customUserAgent = "HomeOS-Mac/1.0"

        if let requestURL = URL(string: url) {
            webView.load(URLRequest(url: requestURL))
        }
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}
}
