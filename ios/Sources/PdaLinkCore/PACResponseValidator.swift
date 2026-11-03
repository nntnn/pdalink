import Foundation

public enum PACResponseValidationError: Error, Equatable {
    case invalidResponse
    case unexpectedStatus(Int)
    case endpointMismatch
}

/// Validates the small HTTP/PAC response returned by the Android host.
/// A `false` result means more bytes are required; a `true` result means the
/// complete response names the exact proxy endpoint from the QR payload.
public enum PACResponseValidator {
    public static func validate(
        _ response: Data,
        streamEnded: Bool,
        expectedEndpoint: String
    ) throws -> Bool {
        guard let separator = response.range(of: Data("\r\n\r\n".utf8)) else {
            if streamEnded {
                throw PACResponseValidationError.invalidResponse
            }
            return false
        }

        let headerData = Data(response[..<separator.lowerBound])
        guard let headers = String(data: headerData, encoding: .utf8),
              let statusLine = headers.components(separatedBy: "\r\n").first else {
            throw PACResponseValidationError.invalidResponse
        }
        let statusParts = statusLine.split(separator: " ", omittingEmptySubsequences: true)
        guard statusParts.count >= 2,
              (statusParts[0] == "HTTP/1.0" || statusParts[0] == "HTTP/1.1"),
              let status = Int(statusParts[1]) else {
            throw PACResponseValidationError.invalidResponse
        }
        guard status == 200 else {
            throw PACResponseValidationError.unexpectedStatus(status)
        }
        guard streamEnded else { return false }

        let body = Data(response[separator.upperBound...])
        guard let pac = String(data: body, encoding: .utf8) else {
            throw PACResponseValidationError.invalidResponse
        }
        let separators = CharacterSet.whitespacesAndNewlines.union(
            CharacterSet(charactersIn: "\"'`;,")
        )
        let tokens = pac.components(separatedBy: separators).filter { !$0.isEmpty }
        let hasExactEndpoint = zip(tokens, tokens.dropFirst()).contains { pair in
            pair.0 == "PROXY" && pair.1 == expectedEndpoint
        }
        guard hasExactEndpoint else {
            throw PACResponseValidationError.endpointMismatch
        }
        return true
    }
}
