import UIKit
import WebKit
import SafariServices
import AuthenticationServices
import CryptoKit
import CoreLocation
import Security
import StoreKit

final class ScoutViewController: UIViewController, WKNavigationDelegate, WKUIDelegate,
    WKScriptMessageHandlerWithReply, ASWebAuthenticationPresentationContextProviding,
    CLLocationManagerDelegate, WKDownloadDelegate {
    private var storeUpdates: Task<Void, Never>?
    private let pointProductIds = Set(["points_500", "points_1000", "points_2500", "monthly_1200", "monthly_3250", "monthly_7000"].map { "net.aisoup.photoscout." + $0 })
    private var purchaseInProgress = false
    private var webView: WKWebView!
    private var authentication: ASWebAuthenticationSession?
    private let locationManager = CLLocationManager()
    private var locationReplies: [(Any?, String?) -> Void] = []
    private var locationTimer: Timer?
    private let loading = UIStackView()
    private let status = UILabel()
    private let spinner = UIActivityIndicatorView(style: .large)
    private let retry = UIButton(type: .system)
    private var loadTimer: Timer?
    private var downloadFiles: [ObjectIdentifier: URL] = [:]

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 0.94, green: 0.93, blue: 0.91, alpha: 1)
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.applicationNameForUserAgent = "PhotoScout-iOS/1.0"
        // Avoid a retain cycle: the controller owns the web view, which owns handlers.
        configuration.userContentController.addScriptMessageHandler(WeakBridge(self), contentWorld: .page, name: "scout")
        if let path = Bundle.main.url(forResource: "NativeBridge", withExtension: "js"),
           let script = try? String(contentsOf: path, encoding: .utf8) {
            configuration.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = view.backgroundColor
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.scrollView.bounces = false
        webView.allowsBackForwardNavigationGestures = true
        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            webView.topAnchor.constraint(equalTo: view.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
        loading.axis = .vertical
        loading.alignment = .center
        loading.spacing = 18
        loading.translatesAutoresizingMaskIntoConstraints = false
        status.text = "Photo Scout"
        status.font = .preferredFont(forTextStyle: .title2)
        status.textAlignment = .center
        status.numberOfLines = 0
        status.adjustsFontForContentSizeCategory = true
        retry.setTitle(NSLocalizedString("Try again", comment: "Reload website"), for: .normal)
        retry.addTarget(self, action: #selector(loadHome), for: .touchUpInside)
        [spinner, status, retry].forEach { loading.addArrangedSubview($0) }
        view.addSubview(loading)
        NSLayoutConstraint.activate([
            loading.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            loading.centerYAnchor.constraint(equalTo: view.centerYAnchor),
            loading.widthAnchor.constraint(lessThanOrEqualTo: view.safeAreaLayoutGuide.widthAnchor, constant: -48)
        ])
        locationManager.delegate = self
        NotificationCenter.default.addObserver(self, selector: #selector(resumed), name: UIApplication.didBecomeActiveNotification, object: nil)
        loadHome()
        storeUpdates = Task { [weak self] in
            for await transaction in StoreKit.Transaction.updates {
                guard let self else { return }
                _ = await self.deliverPointTransaction(transaction)
            }
        }
    }

    @objc private func loadHome() {
        loading.isHidden = false
        webView.isHidden = true
        retry.isHidden = true
        status.text = "Photo Scout"
        spinner.startAnimating()
        loadTimer?.invalidate()
        loadTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
            self?.webView.stopLoading()
            self?.showLoadError()
        }
        webView.load(URLRequest(url: ScoutPolicy.home, cachePolicy: .reloadIgnoringLocalCacheData))
    }

    @objc private func resumed() {
        // The site's own task recovery handles background jobs; never reload an
        // in-progress editor or lose a selected selfie on foreground transitions.
        guard ScoutPolicy.isFirstParty(webView.url) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('pageshow'))", completionHandler: nil)
    }

    private func showLoadError() {
        loadTimer?.invalidate()
        spinner.stopAnimating()
        loading.isHidden = false
        webView.isHidden = true
        status.text = NSLocalizedString("Photo Scout couldn’t connect. Check your connection and try again.", comment: "Network error")
        retry.isHidden = false
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadTimer?.invalidate()
        spinner.stopAnimating()
        loading.isHidden = true
        webView.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { showLoadError() }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { showLoadError() }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { loadHome() }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        // Subframes (Street View etc.) may use third-party origins, but have no bridge access.
        if action.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if ScoutPolicy.isLogin(url) {
            decisionHandler(.cancel)
            signIn()
        } else if ScoutPolicy.isFirstParty(url) || url.scheme == "blob" {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            decisionHandler(.cancel)
            if action.navigationType == .linkActivated || action.targetFrame == nil { openExternal(url) }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse, http.statusCode >= 400 {
            decisionHandler(.cancel)
            showLoadError()
            return
        }
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true
        decisionHandler(response.canShowMIMEType && !attachment ? .allow : .download)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url, ScoutPolicy.isFirstParty(url) { webView.load(action.request) }
        return nil
    }

    private func openExternal(_ url: URL) {
        guard presentedViewController == nil else { return }
        if url.scheme == "https" { present(SFSafariViewController(url: url), animated: true) }
        else if ["mailto", "tel"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
    }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        let trusted = frame.isMainFrame && origin.protocol == "https" && origin.host == ScoutPolicy.home.host
            && (origin.port == 0 || origin.port == 443)
        decisionHandler(trusted && type == .camera ? .prompt : .deny)
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor { view.window! }

    private func signIn() {
        guard authentication == nil else { return }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            alert("Sign-in could not start. Please try again."); return
        }
        let encode: (Data) -> String = { $0.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "") }
        let verifier = encode(Data(bytes))
        let proof = encode(Data(SHA256.hash(data: Data(verifier.utf8))))
        let url = URL(string: "api/v1/auth/login?native_challenge=\(proof)", relativeTo: ScoutPolicy.home)!.absoluteURL
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: ScoutPolicy.callbackScheme) { [weak self] callback, error in
            DispatchQueue.main.async {
                guard let self else { return }
                self.authentication = nil
                if let error = error as? ASWebAuthenticationSessionError, error.code == .canceledLogin { return }
                if callback?.query == "error=cancelled" { return }
                guard error == nil, let callback, let code = ScoutPolicy.callbackCode(callback), ScoutPolicy.isFirstParty(self.webView.url) else {
                    self.alert("Sign-in could not finish. Please try again."); return
                }
                // Arguments never become source code. The same-origin fetch sets
                // an HttpOnly cookie inside WebKit and attaches guest work there.
                self.webView.callAsyncJavaScript("""
                    return await window.PhotoScoutNative.finishSignIn(code, verifier);
                    """, arguments: ["code": code, "verifier": verifier], in: nil, in: .page) { [weak self] result in
                        switch result {
                        case .success: break
                        case .failure: self?.alert("Sign-in could not finish. Please try again.")
                        }
                    }
            }
        }
        session.presentationContextProvider = self
        authentication = session
        if !session.start() { authentication = nil; alert("Sign-in could not start. Please try again.") }
    }

    private func deliverPointTransaction(_ verification: VerificationResult<StoreKit.Transaction>) async -> Bool {
        guard case .verified(let transaction) = verification, pointProductIds.contains(transaction.productID),
              ScoutPolicy.isFirstParty(webView.url) else { return false }
        do {
            _ = try await webView.callAsyncJavaScript("return await window.PhotoScoutNative.acceptTransaction(signedTransaction);",
                arguments: ["signedTransaction": verification.jwsRepresentation], in: nil, in: .page)
            await transaction.finish()
            return true
        } catch { return false }
    }

    private func alert(_ message: String) {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(title: "Photo Scout", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        present(alert, animated: true)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.protocol == "https", origin.host == ScoutPolicy.home.host,
              (origin.port == 0 || origin.port == 443), ScoutPolicy.isFirstParty(webView.url),
              let body = message.body as? [String: Any], let action = body["action"] as? String else {
            replyHandler(nil, "Untrusted request"); return
        }
        switch action {
        case "signIn":
            signIn()
            replyHandler(["started": authentication != nil], nil)
        case "pointProducts":
            Task {
                do {
                    let products = try await Product.products(for: pointProductIds)
                    replyHandler(["prices": Dictionary(uniqueKeysWithValues: products.map { ($0.id, $0.displayPrice) })], nil)
                } catch { replyHandler(nil, "Apple point products could not load.") }
            }
        case "purchasePoints":
            guard !purchaseInProgress, let productId = body["productId"] as? String,
                  pointProductIds.contains(productId), let token = body["appAccountToken"] as? String,
                  let accountToken = UUID(uuidString: token) else { replyHandler(nil, "Invalid or pending point purchase."); return }
            purchaseInProgress = true
            Task {
                defer { purchaseInProgress = false }
                do {
                    guard let product = try await Product.products(for: [productId]).first else {
                        replyHandler(nil, "This Apple product is not available yet."); return
                    }
                    switch try await product.purchase(options: [.appAccountToken(accountToken)]) {
                    case .success(let transaction):
                        let delivered = await deliverPointTransaction(transaction)
                        replyHandler(delivered ? ["state":"paid"] : nil, delivered ? nil : "Payment received; point verification is pending. Restore purchases after signing in to the purchasing account.")
                    case .pending: replyHandler(["state":"pending"], nil)
                    case .userCancelled: replyHandler(nil, "Purchase cancelled.")
                    @unknown default: replyHandler(nil, "Unknown Apple purchase result.")
                    }
                } catch { replyHandler(nil, "Apple purchase could not complete. Please retry.") }
            }
        case "restorePoints":
            Task {
                do {
                    try await AppStore.sync()
                    for await transaction in StoreKit.Transaction.unfinished { _ = await deliverPointTransaction(transaction) }
                    for await transaction in StoreKit.Transaction.currentEntitlements { _ = await deliverPointTransaction(transaction) }
                    replyHandler(["state":"restored"], nil)
                } catch { replyHandler(nil, "Purchases could not be restored. Please retry.") }
            }
        case "managePoints":
            Task {
                guard let scene = view.window?.windowScene else { replyHandler(nil, "Subscription management is unavailable."); return }
                do { try await AppStore.showManageSubscriptions(in: scene); replyHandler(["state":"opened"], nil) }
                catch { replyHandler(nil, "Subscription management could not open.") }
            }
        case "location":
            guard locationReplies.count < 8 else { replyHandler(nil, "Location request already pending"); return }
            locationReplies.append(replyHandler)
            locationManager.desiredAccuracy = (body["precise"] as? Bool == true) ? kCLLocationAccuracyBest : kCLLocationAccuracyHundredMeters
            if locationTimer == nil {
                locationTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in
                    self?.finishLocation(["error": "Location timed out", "code": 3])
                }
            }
            requestLocation()
        case "share":
            let extensions = ["image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"]
            guard let mime = body["mime"] as? String, let ext = extensions[mime],
                  let encoded = body["data"] as? String, encoded.utf8.count <= 28 * 1024 * 1024,
                  let data = Data(base64Encoded: encoded), !data.isEmpty, data.count <= 20 * 1024 * 1024 else {
                replyHandler(nil, "Invalid image"); return
            }
            do {
                let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
                try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
                let file = directory.appendingPathComponent("Photo-Scout.\(ext)")
                try data.write(to: file, options: .atomic)
                share(file, reply: replyHandler)
            } catch { replyHandler(nil, "Could not prepare photo") }
        default: replyHandler(nil, "Unknown request")
        }
    }

    private func requestLocation() {
        switch locationManager.authorizationStatus {
        case .notDetermined: locationManager.requestWhenInUseAuthorization()
        case .authorizedAlways, .authorizedWhenInUse: locationManager.requestLocation()
        case .denied, .restricted: finishLocation(["error": "Location access is disabled. Enable it in Settings, or choose a point on the map.", "code": 1])
        @unknown default: finishLocation(["error": "Location unavailable", "code": 2])
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if !locationReplies.isEmpty { requestLocation() }
    }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last, location.horizontalAccuracy >= 0 else { return }
        finishLocation(["coords": ["latitude": location.coordinate.latitude, "longitude": location.coordinate.longitude,
                                   "accuracy": location.horizontalAccuracy], "timestamp": location.timestamp.timeIntervalSince1970 * 1000])
    }
    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        finishLocation(["error": "Location unavailable. Try again or choose a point on the map.", "code": 2])
    }
    private func finishLocation(_ value: [String: Any]) {
        locationTimer?.invalidate(); locationTimer = nil
        let replies = locationReplies; locationReplies.removeAll()
        replies.forEach { $0(value, nil) }
    }

    private func share(_ file: URL, reply: @escaping (Any?, String?) -> Void) {
        guard presentedViewController == nil else {
            try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
            reply(nil, "Close the current dialog and try again"); return
        }
        let sheet = UIActivityViewController(activityItems: [file], applicationActivities: nil)
        sheet.popoverPresentationController?.sourceView = view
        sheet.popoverPresentationController?.sourceRect = CGRect(x: view.bounds.midX, y: view.bounds.midY, width: 1, height: 1)
        sheet.completionWithItemsHandler = { _, completed, _, error in
            try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
            reply(error == nil ? ["completed": completed] : nil, error == nil ? nil : "Sharing failed")
        }
        present(sheet, animated: true)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        guard response.expectedContentLength <= 30 * 1024 * 1024 else { completionHandler(nil); alert("This file is too large to share."); return }
        do {
            let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let filename = (suggestedFilename as NSString).lastPathComponent
            let file = directory.appendingPathComponent(filename.isEmpty || filename == "." || filename == ".." ? "Photo-Scout.png" : filename)
            downloadFiles[ObjectIdentifier(download)] = file
            completionHandler(file)
        } catch { completionHandler(nil); alert("Could not save this file.") }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let file = downloadFiles.removeValue(forKey: ObjectIdentifier(download)) else { return }
        share(file) { [weak self] _, error in if let error { self?.alert(error) } }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let file = downloadFiles.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        alert("Download failed. Please try again.")
    }
}

@MainActor
private final class WeakBridge: NSObject, WKScriptMessageHandlerWithReply {
    weak var owner: ScoutViewController?
    init(_ owner: ScoutViewController) { self.owner = owner; super.init() }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard let owner else { replyHandler(nil, "App closed"); return }
        owner.userContentController(userContentController, didReceive: message, replyHandler: replyHandler)
    }
}
