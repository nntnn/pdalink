// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "PdaLinkCore",
    platforms: [
        .iOS(.v17),
        .macOS(.v13),
    ],
    products: [
        .library(name: "PdaLinkCore", targets: ["PdaLinkCore"]),
    ],
    targets: [
        .target(name: "PdaLinkCore"),
        .testTarget(name: "PdaLinkCoreTests", dependencies: ["PdaLinkCore"]),
    ]
)
