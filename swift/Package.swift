// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "AppleSTT",
    platforms: [
        .macOS(.v15)
    ],
    targets: [
        .executableTarget(
            name: "apple-stt",
            path: "Sources/AppleSTT",
            linkerSettings: [
                .linkedFramework("Speech"),
                .linkedFramework("AVFoundation"),
                .unsafeFlags([
                    "-Xlinker", "-sectcreate",
                    "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist",
                    "-Xlinker", "Info.plist",
                ]),
            ]
        ),
        .testTarget(
            name: "AppleSTTTests",
            dependencies: ["apple-stt"],
            path: "Tests/AppleSTTTests"
        ),
    ]
)
