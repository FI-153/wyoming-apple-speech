// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "AppleSTT",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        .executableTarget(
            name: "apple-stt",
            path: "Sources/AppleSTT",
            linkerSettings: [
                .linkedFramework("Speech"),
                .linkedFramework("AVFoundation"),
            ]
        ),
        .testTarget(
            name: "AppleSTTTests",
            dependencies: ["apple-stt"],
            path: "Tests/AppleSTTTests"
        ),
    ]
)
