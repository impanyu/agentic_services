import Foundation

/// Keep the bridge restricted to the production first-party origin.
/// No runtime URL setting: arbitrary pages must never gain native capabilities.
enum ScoutPolicy {
    static let home = URL(string: "https://photo-scout.aisoup.net/")!
    static let callbackScheme = "net.aisoup.photoscout"

    static func isFirstParty(_ url: URL?) -> Bool {
        guard let url else { return false }
        return url.scheme == "https" && url.host == home.host && (url.port == nil || url.port == 443)
            && url.user == nil && url.password == nil
    }

    static func isLogin(_ url: URL) -> Bool {
        isFirstParty(url) && url.path == "/api/v1/auth/login"
    }

    static func callbackCode(_ url: URL) -> String? {
        guard url.scheme == callbackScheme, url.host == "auth", url.path.isEmpty,
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              items.filter({ $0.name == "code" }).count == 1,
              let code = items.first(where: { $0.name == "code" })?.value,
              code.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil else { return nil }
        return code
    }
}
