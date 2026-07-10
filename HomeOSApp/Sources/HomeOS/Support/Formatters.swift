import Foundation

enum Formatters {
    static func byteString(_ bytes: Int64?) -> String {
        guard let bytes else { return "—" }
        return ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }

    static func percent(_ value: Double) -> String {
        "\(Int(value.rounded()))%"
    }
}
