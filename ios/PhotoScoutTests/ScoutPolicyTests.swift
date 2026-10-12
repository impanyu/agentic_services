import XCTest
import WebKit
@testable import PhotoScout

final class ScoutPolicyTests: XCTestCase {
    func testOriginBoundary() {
        XCTAssertTrue(ScoutPolicy.isFirstParty(URL(string: "https://photo-scout.aisoup.net/?photo=123")))
        XCTAssertTrue(ScoutPolicy.isFirstParty(URL(string: "https://photo-scout.aisoup.net:443/")))
        for value in ["http://photo-scout.aisoup.net/", "https://photo-scout.aisoup.net.evil.test/", "https://photo-scout.aisoup.net:8443/", "https://user@photo-scout.aisoup.net/", "file:///tmp/a"] {
            XCTAssertFalse(ScoutPolicy.isFirstParty(URL(string: value)), value)
        }
    }
    func testCallbackHasOneBoundedCode() {
        let code = String(repeating: "a", count: 43)
        XCTAssertEqual(ScoutPolicy.callbackCode(URL(string: "net.aisoup.photoscout://auth?code=\(code)")!), code)
        for value in ["net.aisoup.photoscout://other?code=\(code)", "net.aisoup.photoscout://auth?code=short", "net.aisoup.photoscout://auth?code=\(code)&code=\(code)", "https://auth?code=\(code)"] {
            XCTAssertNil(ScoutPolicy.callbackCode(URL(string: value)!))
        }
    }
    func testBridgeIsBundled() {
        XCTAssertNotNil(Bundle(for: ScoutViewController.self).url(forResource: "NativeBridge", withExtension: "js"))
    }
    @MainActor
    func testConnectionFailureOffersRetry() throws {
        let controller = ScoutViewController()
        controller.loadViewIfNeeded()
        let webView = try XCTUnwrap(controller.view.subviews.compactMap { $0 as? WKWebView }.first)
        webView.stopLoading()
        controller.webView(webView, didFailProvisionalNavigation: nil,
                           withError: URLError(.notConnectedToInternet))
        let overlay = try XCTUnwrap(controller.view.subviews.compactMap { $0 as? UIStackView }.first)
        let retry = try XCTUnwrap(overlay.arrangedSubviews.compactMap { $0 as? UIButton }.first)
        XCTAssertFalse(overlay.isHidden)
        XCTAssertFalse(retry.isHidden)
        XCTAssertTrue(webView.isHidden)
        retry.sendActions(for: .touchUpInside)
        XCTAssertTrue(retry.isHidden)
        XCTAssertTrue(overlay.arrangedSubviews.compactMap { $0 as? UIActivityIndicatorView }.first?.isAnimating == true)
        webView.stopLoading()
    }
    @MainActor
    func testProductionWebsiteLoadsWithNativeBridge() async throws {
        let controller = ScoutViewController()
        controller.loadViewIfNeeded()
        let webView = try XCTUnwrap(controller.view.subviews.compactMap { $0 as? WKWebView }.first)
        var loaded = false
        for _ in 0..<60 {
            if let ready = try? await webView.evaluateJavaScript("document.title.includes('Photo Scout') && typeof openPhotoStudio === 'function' && !!window.webkit.messageHandlers.scout"), ready as? Bool == true {
                loaded = true
                break
            }
            try await Task.sleep(nanoseconds: 1_000_000_000)
        }
        XCTAssertTrue(loaded, "Production Photo Scout and its native bridge must load in WKWebView")
    }

}
