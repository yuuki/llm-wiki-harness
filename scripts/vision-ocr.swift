// vision-ocr.swift — Apple Vision によるローカル OCR(scripts/pdf-ocr.sh が使う)。
// 出典: ~/src/github.com/yuuki/kindle-ai-export/src/ocr/vision-ocr.swift の写し。
// 精度の根拠は同リポジトリの src/ocr/benchmark.md(英文の文字誤り率 0.23%)。
// ビルド: swiftc -parse-as-library -O vision-ocr.swift -o vision-ocr
// Vision はサンドボックス内では CRImageReaderError で失敗する。

// Local OCR helper backed by Apple's Vision framework.
//
// usage: vision-ocr [--langs ja-JP,en-US] <image>...
// Writes one JSON object per line to stdout: {"path": "...", "text": "..."}
//
// On macOS 26+ it uses RecognizeDocumentsRequest, which groups lines into
// paragraphs and follows the reading order of vertical (tategaki) Japanese.
// VNRecognizeTextRequest is only a fallback for older systems: it returns
// isolated lines and garbles vertical text.

import Foundation
import Vision

@main
struct VisionOCR {
  static func main() async throws {
    var args = Array(CommandLine.arguments.dropFirst())
    var langs = ["ja-JP", "en-US"]
    if args.first == "--langs", args.count >= 2 {
      langs = args[1].split(separator: ",").map(String.init)
      args.removeFirst(2)
    }

    let encoder = JSONEncoder()
    for path in args {
      let url = URL(fileURLWithPath: path)
      let text: String
      if #available(macOS 26.0, *) {
        text = try await recognizeDocument(url, langs: langs)
      } else {
        text = try recognizeLines(url, langs: langs)
      }
      let line = try encoder.encode(["path": path, "text": text])
      FileHandle.standardOutput.write(line)
      FileHandle.standardOutput.write("\n".data(using: .utf8)!)
    }
  }

  @available(macOS 26.0, *)
  static func recognizeDocument(_ url: URL, langs: [String]) async throws -> String {
    var req = RecognizeDocumentsRequest()
    req.textRecognitionOptions.recognitionLanguages = langs.map { Locale.Language(identifier: $0) }
    req.textRecognitionOptions.useLanguageCorrection = true
    let observations = try await req.perform(on: url)
    return observations
      .map { $0.document.paragraphs.map(\.transcript).joined(separator: "\n") }
      .joined(separator: "\n")
  }

  static func recognizeLines(_ url: URL, langs: [String]) throws -> String {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = langs
    req.usesLanguageCorrection = true
    try VNImageRequestHandler(url: url).perform([req])
    return (req.results ?? [])
      .compactMap { $0.topCandidates(1).first?.string }
      .joined(separator: "\n")
  }
}
