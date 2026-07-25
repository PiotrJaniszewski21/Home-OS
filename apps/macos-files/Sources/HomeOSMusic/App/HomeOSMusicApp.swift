import SwiftUI

@main
struct HomeOSMusicApp: App {
    @StateObject private var session = HomeOSMusicSession()
    @StateObject private var library = MusicLibraryStore()
    @StateObject private var player = MusicPlayerManager()
    @StateObject private var radio = MusicRadioStore()

    var body: some Scene {
        WindowGroup("HomeOS-Music") {
            HomeOSMusicRootView()
                .environmentObject(session)
                .environmentObject(library)
                .environmentObject(player)
                .environmentObject(radio)
                .frame(minWidth: 980, minHeight: 680)
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("Play or Pause") {
                    player.togglePlayback()
                }
                .keyboardShortcut(.space, modifiers: [])
                .disabled(player.currentTrack == nil)
            }
            CommandMenu("Playback") {
                Button("Previous") {
                    player.playPrevious()
                }
                .keyboardShortcut(.leftArrow, modifiers: [.command])
                Button(player.isPlaying ? "Pause" : "Play") {
                    player.togglePlayback()
                }
                .keyboardShortcut(.space, modifiers: [])
                Button("Next") {
                    player.playNext()
                }
                .keyboardShortcut(.rightArrow, modifiers: [.command])
            }
        }

        Settings {
            HomeOSMusicSettingsView()
                .environmentObject(session)
        }
    }
}
