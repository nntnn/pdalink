import Foundation

public struct PdaLinkConnection: Equatable, Sendable {
    public enum Transport: String, CaseIterable, Sendable {
        case localHotspot = "local-hotspot"
        case wifiDirect = "wifi-direct"
    }

    public enum SecurityMode: String, CaseIterable, Sendable {
        case wpa2 = "wpa2-psk"
        case wpa3 = "wpa3-sae"
        case transition = "wpa2-wpa3"
    }

    public static let version = 1

    public let transport: Transport
    public let ssid: String
    public let security: SecurityMode
    public let password: String
    public let proxyHost: String
    public let proxyPort: Int

    public var proxyEndpoint: String {
        "http://\(proxyHost):\(proxyPort)"
    }

    public var canonicalURI: String {
        let fields = [
            "v=\(Self.version)",
            "transport=\(FormURLCodec.encode(transport.rawValue))",
            "ssid=\(FormURLCodec.encode(ssid))",
            "security=\(FormURLCodec.encode(security.rawValue))",
            "password=\(FormURLCodec.encode(password))",
            "proxy=\(FormURLCodec.encode(proxyEndpoint))",
        ]
        return "pdalink://connect?" + fields.joined(separator: "&")
    }

    public var tunnelSettings: TunnelSettings {
        // Parsing has already applied the same validation used by this initializer.
        try! TunnelSettings(proxyHost: proxyHost, proxyPort: proxyPort)
    }
}

public struct TunnelSettings: Equatable, Sendable {
    public let proxyHost: String
    public let proxyPort: Int

    public init(proxyHost: String, proxyPort: Int) throws {
        guard PrivateIPv4.parse(proxyHost) != nil, (1...65_535).contains(proxyPort) else {
            throw ConnectionURIError.invalidProxy
        }
        self.proxyHost = proxyHost
        self.proxyPort = proxyPort
    }

    public var proxyEndpoint: String {
        "http://\(proxyHost):\(proxyPort)"
    }
}

public enum ConnectionURIError: Error, Equatable, LocalizedError {
    case empty
    case malformed
    case invalidTarget
    case missingParameters
    case malformedParameter
    case invalidEncoding
    case unknownParameter(String)
    case duplicateParameter(String)
    case missingParameter(String)
    case unsupportedVersion
    case unsupportedTransport
    case invalidSSID
    case invalidPassword
    case unsupportedSecurity
    case invalidProxy

    public var errorDescription: String? {
        switch self {
        case .empty:
            return "Connection URI is empty."
        case .malformed:
            return "Connection URI is malformed."
        case .invalidTarget:
            return "Connection URI target is invalid."
        case .missingParameters:
            return "Connection URI has no parameters."
        case .malformedParameter:
            return "Connection URI parameter is malformed."
        case .invalidEncoding:
            return "Connection URI encoding is invalid."
        case let .unknownParameter(key):
            return "Unknown connection URI parameter: \(key)."
        case let .duplicateParameter(key):
            return "Duplicate connection URI parameter: \(key)."
        case let .missingParameter(key):
            return "Missing connection URI parameter: \(key)."
        case .unsupportedVersion:
            return "Unsupported connection URI version."
        case .unsupportedTransport:
            return "Unsupported Wi-Fi transport."
        case .invalidSSID:
            return "SSID must be between 1 and 32 UTF-8 bytes."
        case .invalidPassword:
            return "Wi-Fi password must be 8-63 printable ASCII characters."
        case .unsupportedSecurity:
            return "Unsupported Wi-Fi security mode."
        case .invalidProxy:
            return "Proxy must be an HTTP endpoint on a private IPv4 address."
        }
    }
}

public enum PdaLinkConnectionURI {
    private static let allowedKeys: Set<String> = [
        "v", "transport", "ssid", "security", "password", "proxy",
    ]

    public static func parse(_ rawValue: String) throws -> PdaLinkConnection {
        let raw = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else {
            throw ConnectionURIError.empty
        }
        guard let components = URLComponents(string: raw) else {
            throw ConnectionURIError.malformed
        }
        guard components.scheme?.lowercased() == "pdalink",
              components.host?.lowercased() == "connect",
              components.user == nil,
              components.password == nil,
              components.port == nil,
              components.percentEncodedPath.isEmpty,
              components.fragment == nil else {
            throw ConnectionURIError.invalidTarget
        }

        let values = try parseQuery(components.percentEncodedQuery)
        guard values["v"] == String(PdaLinkConnection.version) else {
            throw ConnectionURIError.unsupportedVersion
        }

        let transportValue = try required("transport", in: values).lowercased()
        guard let transport = PdaLinkConnection.Transport(rawValue: transportValue) else {
            throw ConnectionURIError.unsupportedTransport
        }

        let ssid = try required("ssid", in: values)
        guard !ssid.contains("\0"), (1...32).contains(ssid.utf8.count) else {
            throw ConnectionURIError.invalidSSID
        }

        let password = try required("password", in: values)
        guard (8...63).contains(password.count),
              password.utf8.allSatisfy({ (0x20...0x7e).contains($0) }) else {
            throw ConnectionURIError.invalidPassword
        }

        let security = try normalizeSecurity(required("security", in: values))
        let proxy = try parseProxy(required("proxy", in: values))

        return PdaLinkConnection(
            transport: transport,
            ssid: ssid,
            security: security,
            password: password,
            proxyHost: proxy.proxyHost,
            proxyPort: proxy.proxyPort
        )
    }

    private static func parseQuery(_ rawQuery: String?) throws -> [String: String] {
        guard let rawQuery, !rawQuery.isEmpty else {
            throw ConnectionURIError.missingParameters
        }

        var values: [String: String] = [:]
        for item in rawQuery.split(separator: "&", omittingEmptySubsequences: false) {
            guard let separator = item.firstIndex(of: "="), separator != item.startIndex else {
                throw ConnectionURIError.malformedParameter
            }
            let rawKey = String(item[..<separator])
            let rawValue = String(item[item.index(after: separator)...])
            let key = try FormURLCodec.decode(rawKey)
            let value = try FormURLCodec.decode(rawValue)

            guard allowedKeys.contains(key) else {
                throw ConnectionURIError.unknownParameter(key)
            }
            guard values[key] == nil else {
                throw ConnectionURIError.duplicateParameter(key)
            }
            values[key] = value
        }
        return values
    }

    private static func required(_ key: String, in values: [String: String]) throws -> String {
        guard let value = values[key], !value.isEmpty else {
            throw ConnectionURIError.missingParameter(key)
        }
        return value
    }

    private static func normalizeSecurity(_ raw: String) throws -> PdaLinkConnection.SecurityMode {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "wpa2-psk":
            return .wpa2
        case "wpa3-sae":
            return .wpa3
        case "wpa2-wpa3", "wpa2/wpa3 transition":
            return .transition
        default:
            throw ConnectionURIError.unsupportedSecurity
        }
    }

    private static func parseProxy(_ raw: String) throws -> TunnelSettings {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: value),
              components.scheme?.lowercased() == "http",
              let host = components.host,
              let port = components.port,
              components.user == nil,
              components.password == nil,
              components.percentEncodedPath.isEmpty,
              components.percentEncodedQuery == nil,
              components.fragment == nil else {
            throw ConnectionURIError.invalidProxy
        }
        return try TunnelSettings(proxyHost: host, proxyPort: port)
    }
}

private enum PrivateIPv4 {
    static func parse(_ value: String) -> [UInt8]? {
        let parts = value.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else {
            return nil
        }

        var octets: [UInt8] = []
        for part in parts {
            guard !part.isEmpty,
                  !(part.count > 1 && part.first == "0"),
                  part.utf8.allSatisfy({ (0x30...0x39).contains($0) }),
                  let number = UInt8(part) else {
                return nil
            }
            octets.append(number)
        }

        guard octets[0] == 10
                || (octets[0] == 172 && (16...31).contains(octets[1]))
                || (octets[0] == 192 && octets[1] == 168) else {
            return nil
        }
        return octets
    }
}

private enum FormURLCodec {
    static func encode(_ value: String) -> String {
        var output = ""
        for byte in value.utf8 {
            switch byte {
            case 0x41...0x5a, 0x61...0x7a, 0x30...0x39, 0x2d, 0x5f, 0x2e, 0x2a:
                output.unicodeScalars.append(UnicodeScalar(Int(byte))!)
            case 0x20:
                output.append("+")
            default:
                output.append(String(format: "%%%02X", byte))
            }
        }
        return output
    }

    static func decode(_ value: String) throws -> String {
        let input = Array(value.utf8)
        var bytes: [UInt8] = []
        var index = 0
        while index < input.count {
            switch input[index] {
            case 0x2b:
                bytes.append(0x20)
                index += 1
            case 0x25:
                guard index + 2 < input.count,
                      let high = hex(input[index + 1]),
                      let low = hex(input[index + 2]) else {
                    throw ConnectionURIError.invalidEncoding
                }
                bytes.append((high << 4) | low)
                index += 3
            default:
                bytes.append(input[index])
                index += 1
            }
        }
        guard let decoded = String(bytes: bytes, encoding: .utf8) else {
            throw ConnectionURIError.invalidEncoding
        }
        return decoded
    }

    private static func hex(_ byte: UInt8) -> UInt8? {
        switch byte {
        case 0x30...0x39:
            return byte - 0x30
        case 0x41...0x46:
            return byte - 0x41 + 10
        case 0x61...0x66:
            return byte - 0x61 + 10
        default:
            return nil
        }
    }
}
