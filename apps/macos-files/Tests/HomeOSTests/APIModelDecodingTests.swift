import Foundation
@testable import HomeOS
import XCTest

final class APIModelDecodingTests: XCTestCase {
    private let decoder = JSONDecoder()

    func testLoginResponseDecodesHostedShape() throws {
        let json = #"""
        {
          "ok": true,
          "data": {
            "token": "hidden-test-token",
            "user": {"username": "Peter", "role": "admin"}
          }
        }
        """#.data(using: .utf8)!

        let response = try decoder.decode(LoginResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.user.username, "Peter")
        XCTAssertEqual(response.data?.user.role, "admin")
        XCTAssertEqual(response.data?.token, "hidden-test-token")
    }

    func testMetricsResponseDecodesBackendShape() throws {
        let json = #"""
        {
          "ok": true,
          "data": {
            "cpu_percent": 12.5,
            "cpu_count": 8,
            "memory": {"total_gb": 16.0, "used_gb": 7.3, "percent": 45.6},
            "disk": {"total_gb": 512.0, "used_gb": 220.0, "percent": 43.0},
            "network": {"sent_gb": 1.2, "recv_gb": 3.4},
            "uptime": "2d 4h",
            "uptime_seconds": 187200,
            "platform": "Linux",
            "hostname": "home-server",
            "python_version": "3.11.0"
          }
        }
        """#.data(using: .utf8)!

        let response = try decoder.decode(MetricsResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.cpuPercent, 12.5)
        XCTAssertEqual(response.data?.memory.usedGB, 7.3)
        XCTAssertEqual(response.data?.hostname, "home-server")
        XCTAssertEqual(response.data?.pythonVersion, "3.11.0")
    }

    func testNetworkSpeedResponseDecodesBackendShape() throws {
        let json = #"{"ok":true,"data":{"bytes_sent":1200,"bytes_recv":3400,"timestamp":42.5}}"#.data(using: .utf8)!
        let response = try decoder.decode(NetworkSpeedResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.bytesSent, 1200)
        XCTAssertEqual(response.data?.bytesReceived, 3400)
        XCTAssertEqual(response.data?.timestamp, 42.5)
    }

    func testStorageResponseDecodesExternalDriveWithoutRemovable() throws {
        let json = #"""
        {
          "ok": true,
          "data": {
            "main": {
              "total_bytes": 1000,
              "used_bytes": 400,
              "free_bytes": 600,
              "percent_used": 40.0
            },
            "drives": [{
              "name": "USB",
              "device": "/dev/sda1",
              "mount_point": "/media/USB",
              "filesystem": "ext4",
              "total_bytes": 2000,
              "used_bytes": 500,
              "free_bytes": 1500,
              "percent_used": 25.0
            }]
          }
        }
        """#.data(using: .utf8)!

        let response = try decoder.decode(StorageResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.main.percentUsed, 40.0)
        XCTAssertEqual(response.data?.drives.first?.name, "USB")
        XCTAssertNil(response.data?.drives.first?.removable)
    }

    func testFileListResponseDecodesBackendShape() throws {
        let json = #"""
        {
          "ok": true,
          "data": {
            "path": "/",
            "entries": [{
              "name": "photo.jpg",
              "path": "/photo.jpg",
              "is_dir": false,
              "size": 12345,
              "modified": "2026-07-08T12:00:00+00:00",
              "extension": "jpg"
            }]
          }
        }
        """#.data(using: .utf8)!

        let response = try decoder.decode(FileListResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.entries.first?.name, "photo.jpg")
        XCTAssertEqual(response.data?.entries.first?.extensionType, "jpg")
        XCTAssertEqual(response.data?.entries.first?.isDirectory, false)
    }

    func testConnectionInfoResponseDecodesBackendShape() throws {
        let json = #"""
        {
          "ok": true,
          "data": {
            "hostname": "homeos",
            "port": 5000,
            "local_urls": [
              "http://homeos.local:5000",
              "http://192.168.1.20:5000"
            ]
          }
        }
        """#.data(using: .utf8)!

        let response = try decoder.decode(ConnectionInfoResponse.self, from: json)

        XCTAssertTrue(response.ok)
        XCTAssertEqual(response.data?.hostname, "homeos")
        XCTAssertEqual(response.data?.port, 5000)
        XCTAssertEqual(response.data?.localURLs.first, "http://homeos.local:5000")
    }
}
