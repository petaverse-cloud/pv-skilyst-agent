// Print CGWindowIDs for a process by name — used to capture the Tauri window
// without stealing the user's focus (screencapture -l takes a CGWindowID).
import CoreGraphics
import Foundation

let owner = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "skilyst-agent"
guard let list = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] else {
    exit(1)
}
for w in list {
    let name = w[kCGWindowOwnerName as String] as? String ?? ""
    let layer = w[kCGWindowLayer as String] as? Int ?? -1
    let num = w[kCGWindowNumber as String] as? Int ?? 0
    let bounds = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let title = w[kCGWindowName as String] as? String ?? ""
    if name == owner && layer == 0 {
        print("\(num)\t\(title)\t\(bounds["X"] ?? 0),\(bounds["Y"] ?? 0),\(bounds["Width"] ?? 0),\(bounds["Height"] ?? 0)")
    }
}
