import Foundation

struct FileTransferActivity: Identifiable, Equatable, Sendable {
    enum Kind: Hashable, Sendable {
        case upload
        case download

        var title: String {
            switch self {
            case .upload: "Uploading"
            case .download: "Downloading"
            }
        }

        var systemImage: String {
            switch self {
            case .upload: "arrow.up.circle.fill"
            case .download: "arrow.down.circle.fill"
            }
        }
    }

    let id: UUID
    let filename: String
    let kind: Kind
    var fractionCompleted: Double

    var clampedFractionCompleted: Double {
        min(max(fractionCompleted, 0), 1)
    }
}
