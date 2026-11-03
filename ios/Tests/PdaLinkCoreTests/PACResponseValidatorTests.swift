import Foundation
import XCTest
@testable import PdaLinkCore

final class PACResponseValidatorTests: XCTestCase {
    private let endpoint = "192.168.49.1:8001"

    func testAcceptsCompleteHTTP11ResponseWithExactProxyDirective() throws {
        let response = Data(
            "HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"
                .appending("function FindProxyForURL(url, host) { return \"PROXY 192.168.49.1:8001; DIRECT\"; }")
                .utf8
        )

        XCTAssertTrue(
            try PACResponseValidator.validate(
                response,
                streamEnded: true,
                expectedEndpoint: endpoint
            )
        )
    }

    func testWaitsForStreamEndBeforeTrustingBody() throws {
        let response = Data("HTTP/1.1 200 OK\r\n\r\nPROXY 192.168.49.1:8001".utf8)

        XCTAssertFalse(
            try PACResponseValidator.validate(
                response,
                streamEnded: false,
                expectedEndpoint: endpoint
            )
        )
    }

    func testRejectsPortPrefixAndInvalidHTTPVersion() {
        let wrongPort = Data("HTTP/1.1 200 OK\r\n\r\nPROXY 192.168.49.1:80010".utf8)
        XCTAssertThrowsError(
            try PACResponseValidator.validate(
                wrongPort,
                streamEnded: true,
                expectedEndpoint: endpoint
            )
        ) { error in
            XCTAssertEqual(error as? PACResponseValidationError, .endpointMismatch)
        }

        let invalidHTTP = Data("NOTHTTP 200 OK\r\n\r\nPROXY 192.168.49.1:8001".utf8)
        XCTAssertThrowsError(
            try PACResponseValidator.validate(
                invalidHTTP,
                streamEnded: true,
                expectedEndpoint: endpoint
            )
        ) { error in
            XCTAssertEqual(error as? PACResponseValidationError, .invalidResponse)
        }
    }

    func testRejectsNonSuccessStatus() {
        let response = Data("HTTP/1.1 404 Not Found\r\n\r\n".utf8)

        XCTAssertThrowsError(
            try PACResponseValidator.validate(
                response,
                streamEnded: true,
                expectedEndpoint: endpoint
            )
        ) { error in
            XCTAssertEqual(error as? PACResponseValidationError, .unexpectedStatus(404))
        }
    }
}
