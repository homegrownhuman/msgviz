// Liest ein Bild und gibt erkannten Text als JSON aus.
// Nutzung: ocr <path-to-image>
// Stdout: {"lines": <int>, "text": "...", "blocks": [{"text":"...","conf":0..1}]}
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count >= 2 else {
    fputs("usage: ocr <image>\n", stderr); exit(2)
}
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    let err: [String: Any] = ["error": "cannot load image", "path": path]
    if let d = try? JSONSerialization.data(withJSONObject: err) {
        FileHandle.standardOutput.write(d)
    }
    exit(1)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["de-DE", "en-US"]
req.usesLanguageCorrection = true
do {
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
} catch {
    let err: [String: Any] = ["error": "recognize failed: \(error)"]
    if let d = try? JSONSerialization.data(withJSONObject: err) {
        FileHandle.standardOutput.write(d)
    }
    exit(1)
}
var blocks: [[String: Any]] = []
var lines: [String] = []
for obs in (req.results ?? []) {
    if let cand = obs.topCandidates(1).first {
        lines.append(cand.string)
        blocks.append(["text": cand.string, "conf": Double(cand.confidence)])
    }
}
let out: [String: Any] = [
    "lines": lines.count,
    "text": lines.joined(separator: "\n"),
    "blocks": blocks
]
if let d = try? JSONSerialization.data(withJSONObject: out, options: []) {
    FileHandle.standardOutput.write(d)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
