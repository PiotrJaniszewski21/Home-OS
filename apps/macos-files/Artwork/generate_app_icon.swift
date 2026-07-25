import AppKit
import CoreText
import Foundation

guard CommandLine.arguments.count == 4 else {
    fputs("Usage: generate_app_icon.swift <base.png> <font.ttf> <output.png>\n", stderr)
    exit(2)
}

let baseURL = URL(fileURLWithPath: CommandLine.arguments[1])
let fontURL = URL(fileURLWithPath: CommandLine.arguments[2])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[3])

guard let baseImage = NSImage(contentsOf: baseURL) else {
    fputs("Could not open base icon.\n", stderr)
    exit(1)
}

CTFontManagerRegisterFontsForURL(fontURL as CFURL, .process, nil)

guard let font = NSFont(name: "BebasNeue-Bold", size: 205) ?? NSFont(name: "Bebas Neue", size: 205) else {
    fputs("Could not load Bebas Neue.\n", stderr)
    exit(1)
}

guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: 1024,
    pixelsHigh: 1024,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
) else {
    fputs("Could not create app icon canvas.\n", stderr)
    exit(1)
}

let previousContext = NSGraphicsContext.current
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSGraphicsContext.current?.imageInterpolation = .high
baseImage.draw(in: NSRect(x: 0, y: 0, width: 1024, height: 1024))

let text = NSAttributedString(
    string: "OS",
    attributes: [
        .font: font,
        .foregroundColor: NSColor(calibratedRed: 41 / 255, green: 151 / 255, blue: 245 / 255, alpha: 1),
        .kern: 5,
    ]
)
let textSize = text.size()
text.draw(at: NSPoint(x: (1024 - textSize.width) / 2, y: 318))
NSGraphicsContext.current?.flushGraphics()
NSGraphicsContext.current = previousContext

guard let pngData = bitmap.representation(using: .png, properties: [:]) else {
    fputs("Could not render app icon.\n", stderr)
    exit(1)
}

try pngData.write(to: outputURL)
