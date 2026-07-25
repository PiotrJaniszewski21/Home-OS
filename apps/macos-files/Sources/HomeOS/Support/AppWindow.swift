import AppKit
import SwiftUI

enum AppWindow {
    @MainActor
    static func openMainWindow(openWindow: OpenWindowAction) {
        AppIcon.install()
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        openWindow(id: "main")
    }
}
