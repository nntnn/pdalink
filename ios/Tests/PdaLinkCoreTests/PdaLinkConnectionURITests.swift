import XCTest
@testable import PdaLinkCore

final class PdaLinkConnectionURITests: XCTestCase {
    private let validURI = "pdalink://connect?v=1&transport=local-hotspot&ssid=PdaLink&security=wpa2-psk&password=password123&proxy=http%3A%2F%2F10.0.0.1%3A8001"

    func testParsesAndCanonicalizesReservedCharacters() throws {
        let connection = try PdaLinkConnectionURI.parse(
            "pdalink://connect?v=1&transport=local-hotspot&ssid=Pda+Link%2B1&security=wpa2%2Fwpa3+transition&password=pass%26word%3D123&proxy=http%3A%2F%2F10.111.107.245%3A8001"
        )

        XCTAssertEqual(connection.transport, .localHotspot)
        XCTAssertEqual(connection.ssid, "Pda Link+1")
        XCTAssertEqual(connection.security, .transition)
        XCTAssertEqual(connection.password, "pass&word=123")
        XCTAssertEqual(connection.proxyHost, "10.111.107.245")
        XCTAssertEqual(connection.proxyPort, 8001)
        XCTAssertEqual(try PdaLinkConnectionURI.parse(connection.canonicalURI), connection)
    }

    func testAcceptsBothTransportsAndPrivateIPv4Ranges() throws {
        for (transport, host) in [
            ("local-hotspot", "10.255.0.1"),
            ("wifi-direct", "172.16.0.1"),
            ("wifi-direct", "172.31.255.254"),
            ("wifi-direct", "192.168.49.1"),
        ] {
            let uri = "pdalink://connect?v=1&transport=\(transport)&ssid=PdaLink&security=wpa3-sae&password=password123&proxy=http%3A%2F%2F\(host)%3A8001"
            XCTAssertNoThrow(try PdaLinkConnectionURI.parse(uri))
        }
    }

    func testRejectsPublicAndAmbiguousProxyAddresses() {
        for host in ["8.8.8.8", "172.32.0.1", "192.169.0.1", "010.0.0.1", "10.0.0.256"] {
            let uri = validURI.replacingOccurrences(of: "10.0.0.1", with: host)
            XCTAssertThrowsError(try PdaLinkConnectionURI.parse(uri)) {
                XCTAssertEqual($0 as? ConnectionURIError, .invalidProxy)
            }
        }
    }

    func testRejectsDuplicateUnknownAndMissingParameters() {
        XCTAssertThrowsError(try PdaLinkConnectionURI.parse(validURI + "&v=1")) {
            XCTAssertEqual($0 as? ConnectionURIError, .duplicateParameter("v"))
        }
        XCTAssertThrowsError(try PdaLinkConnectionURI.parse(validURI + "&token=x")) {
            XCTAssertEqual($0 as? ConnectionURIError, .unknownParameter("token"))
        }
        XCTAssertThrowsError(try PdaLinkConnectionURI.parse(validURI.replacingOccurrences(of: "&ssid=PdaLink", with: ""))) {
            XCTAssertEqual($0 as? ConnectionURIError, .missingParameter("ssid"))
        }
    }

    func testRejectsMalformedEncodingAndNonASCIIPassword() {
        XCTAssertThrowsError(try PdaLinkConnectionURI.parse(validURI.replacingOccurrences(of: "PdaLink", with: "%GG"))) {
            XCTAssertEqual($0 as? ConnectionURIError, .invalidEncoding)
        }
        let unicodePassword = validURI.replacingOccurrences(of: "password123", with: "password%E2%98%83")
        XCTAssertThrowsError(try PdaLinkConnectionURI.parse(unicodePassword)) {
            XCTAssertEqual($0 as? ConnectionURIError, .invalidPassword)
        }
    }

    func testRejectsInvalidTargetsAndProxyShapes() {
        let invalidValues = [
            validURI.replacingOccurrences(of: "pdalink://connect", with: "https://connect"),
            validURI.replacingOccurrences(of: "pdalink://connect", with: "pdalink://connect/path"),
            validURI.replacingOccurrences(of: "8001", with: "8001%2Fproxy.pac"),
            validURI.replacingOccurrences(of: "http%3A", with: "https%3A"),
        ]
        for value in invalidValues {
            XCTAssertThrowsError(try PdaLinkConnectionURI.parse(value))
        }
    }
}
