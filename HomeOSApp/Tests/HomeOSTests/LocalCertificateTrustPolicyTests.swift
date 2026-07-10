@testable import HomeOS
import XCTest

final class LocalCertificateTrustPolicyTests: XCTestCase {
    func testAllowsPrivateLocalHosts() {
        let hosts = [
            "localhost",
            "homeos.local",
            "192.168.0.8",
            "10.0.0.5",
            "172.16.0.1",
            "172.31.255.255",
            "127.0.0.1",
            "169.254.1.2",
            "100.64.0.1",
            "100.127.255.255",
            "[192.168.0.8]",
        ]

        for host in hosts {
            XCTAssertTrue(LocalCertificateTrustPolicy.allowsSelfSignedCertificate(forHost: host), host)
        }
    }

    func testRejectsPublicAndSpoofedHosts() {
        let hosts = [
            "",
            "petershomenet.co.uk",
            "example.com",
            "8.8.8.8",
            "172.32.0.1",
            "100.128.0.1",
            "192.168.0.8.evil.com",
            "homeos.local.evil.com",
        ]

        for host in hosts {
            XCTAssertFalse(LocalCertificateTrustPolicy.allowsSelfSignedCertificate(forHost: host), host)
        }
    }
}
