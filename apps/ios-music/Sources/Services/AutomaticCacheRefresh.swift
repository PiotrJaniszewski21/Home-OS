import BackgroundTasks
import Foundation

enum AutomaticCacheRefresh {
    static let identifier = "uk.co.petershomenet.homemusic.cache-refresh"

    static func schedule() {
#if !targetEnvironment(macCatalyst)
        BGTaskScheduler.shared.cancel(
            taskRequestWithIdentifier: identifier
        )
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date().addingTimeInterval(24 * 60 * 60)
        try? BGTaskScheduler.shared.submit(request)
#endif
    }
}
