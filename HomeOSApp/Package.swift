// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "HomeOS",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "HomeOS",
            path: "Sources/HomeOS"
        ),
    ]
)
