import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// D42: any still image macOS can open is saved, as a file Claude can read.
///
/// Claude reads four formats (PNG, JPEG, GIF, WebP) and refuses an image over 8,000 pixels
/// on a side or over 10 MB once base64-encoded. The backend stores what it is sent and has
/// no image library, so the Mac makes the file fit before upload, with ImageIO (part of
/// macOS):
/// - A PNG, JPEG, GIF or WebP within the limits is sent byte for byte, unchanged.
/// - Any other image (HEIC from an iPhone, a TIFF scan, BMP, a camera's RAW) becomes a JPEG.
/// - An image over a limit is scaled down, keeping its shape, until it fits.
///
/// Like a photo lab that prints every order on the same machine with the same settings:
/// hand it the same negative twice and you get the same print. The content hash (D34)
/// then finds the second drop as "Already saved". So: fixed settings, a fixed quality,
/// and nothing written that changes from run to run (no date, no copied metadata).
///
/// Everything here is synchronous and slow (a 12-megapixel HEIC takes a moment), so it
/// runs off the main thread (`DropModel.save`).
enum ImagePrep {
    /// Claude's limits (Vision docs, checked 2026-11-27), as `describe.py` checks them.
    static let maxSide = 8000
    /// 10 MB of base64 is 4 characters for every 3 bytes: 10 * 1024 * 1024 * 3 / 4.
    static let maxBytes = 7_864_320
    /// High enough that text in a photo stays sharp and no blocks show, low enough that a
    /// 12-megapixel photo comes out at a few MB rather than ten. Claude scales a large
    /// image down before it reads it, so more quality would buy nothing.
    static let jpegQuality = 0.85

    /// The four formats Claude reads, as ImageIO names them. The backend accepts exactly
    /// these by their first bytes (`uploads.py`).
    private static let claudeReads: Set<String> = [
        UTType.png.identifier, UTType.jpeg.identifier, UTType.gif.identifier, UTType.webP.identifier,
    ]

    /// Every still image type ImageIO can decode on this Mac (HEIC, TIFF, BMP, RAW, ...).
    private static let decodable: [UTType] = ((CGImageSourceCopyTypeIdentifiers() as? [String]) ?? [])
        .compactMap { UTType($0) }
        .filter { $0.conforms(to: .image) }

    /// Can this Mac open a file of this type as an image? Judged from the type only, so it
    /// is cheap enough to ask while a drag moves.
    static func canOpen(_ type: UTType) -> Bool {
        decodable.contains { type.conforms(to: $0) }
    }

    /// What will be uploaded for an image file.
    struct Prepared: Sendable {
        let body: APIClient.UploadBody
        /// The name to save it under: the file's own, or with `.jpg` when it became a JPEG.
        let name: String
        /// Before and after, for the debug log: nil when the file is sent unchanged.
        let change: Change?
    }

    struct Change: Sendable, CustomStringConvertible {
        let fromType: String, fromWidth: Int, fromHeight: Int, fromBytes: Int
        let toType: String, toWidth: Int, toHeight: Int, toBytes: Int

        var description: String {
            "\(fromType) \(fromWidth)x\(fromHeight) \(fromBytes) bytes -> \(toType) \(toWidth)x\(toHeight) \(toBytes) bytes"
        }
    }

    enum Failure: Error, Equatable {
        /// ImageIO could not read it as an image (a damaged file, or not an image at all).
        case unreadable
    }

    /// The upload for one image file: the file itself, or a JPEG or PNG made from it.
    /// Throws `Failure.unreadable` when the file cannot be decoded; nothing is uploaded then.
    static func prepare(_ url: URL, name: String) throws -> Prepared {
        // The drag granted this file to the app (see `DropModel.payload(from:)`); open its
        // security scope too, in case the URL carries one, for as long as we read it.
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }

        // No cache: the decoded pixels are used once, then thrown away.
        let noCache = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithURL(url as CFURL, noCache),
            CGImageSourceGetCount(source) > 0,
            let type = CGImageSourceGetType(source) as String?
        else { throw Failure.unreadable }
        // A HEIC can hold several images (a burst, a dynamic wallpaper); this is the one
        // Photos shows. For a GIF, the first frame, which is also what Claude reads.
        let index = CGImageSourceGetPrimaryImageIndex(source)
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [CFString: Any],
            let width = properties[kCGImagePropertyPixelWidth] as? Int,
            let height = properties[kCGImagePropertyPixelHeight] as? Int,
            width > 0, height > 0
        else { throw Failure.unreadable }
        let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? Int.max

        // ImageIO's type comes from the file's bytes, not its name, so a HEIC renamed .jpg
        // is still converted. The backend judges the same way, so these always pass it.
        if claudeReads.contains(type), max(width, height) <= maxSide, bytes <= maxBytes {
            return Prepared(body: .file(url), name: name, change: nil)
        }

        // A PNG stays a PNG if only its pixel size was too big: it is usually a screenshot
        // or a diagram, where JPEG would blur the text and drop the transparency. If its
        // bytes are the problem, it is photo-like, and a JPEG is far smaller.
        var side = min(max(width, height), maxSide)
        if type == UTType.png.identifier {
            let image = try decode(source, index: index, side: side)
            let data = try encode(image, as: .png)
            if data.count <= maxBytes {
                return made(data, image, from: (type, width, height, bytes), as: .png, name: name)
            }
        }
        // A JPEG's size grows with its area, so shrink by the square root of how far over
        // the limit it is, with 5% to spare. Usually one step is enough; each step starts
        // from the original again, so the result depends only on the file.
        for _ in 0..<8 {
            let image = try flattened(decode(source, index: index, side: side))
            let data = try encode(image, as: .jpeg)
            if data.count <= maxBytes {
                return made(data, image, from: (type, width, height, bytes), as: .jpeg, name: name)
            }
            let scale = (Double(maxBytes) / Double(data.count)).squareRoot() * 0.95
            side = max(1, Int(Double(side) * scale))
        }
        throw Failure.unreadable  // not reached for any real image: 8 steps shrink it 1,000 times over
    }

    private static func made(
        _ data: Data, _ image: CGImage, from: (type: String, width: Int, height: Int, bytes: Int),
        as format: UTType, name: String
    ) -> Prepared {
        let change = Change(
            fromType: from.type, fromWidth: from.width, fromHeight: from.height, fromBytes: from.bytes,
            toType: format.identifier, toWidth: image.width, toHeight: image.height, toBytes: data.count
        )
        let newName = format == .jpeg ? renamed(name, to: "jpg") : name
        return Prepared(body: .data(data), name: newName, change: change)
    }

    /// `IMG_0042.HEIC` becomes `IMG_0042.jpg`, so the saved name says what the bytes are.
    static func renamed(_ name: String, to ext: String) -> String {
        let base = (name as NSString).deletingPathExtension
        return (base.isEmpty ? name : base) + "." + ext
    }

    /// The image at most `side` pixels on its longer side, turned upright. A photo is
    /// stored as the sensor saw it plus an orientation tag ("turn me 90°"); the transform
    /// applies the tag, so nothing is saved sideways, and no tag is written afterwards.
    private static func decode(_ source: CGImageSource, index: Int, side: Int) throws -> CGImage {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,  // never the small preview inside
            kCGImageSourceCreateThumbnailWithTransform: true,  // honour the orientation
            kCGImageSourceThumbnailMaxPixelSize: side,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, index, options as CFDictionary) else {
            throw Failure.unreadable
        }
        // A damaged file can still give an image whose pixels never arrive: a HEIC cut off
        // after its header gave a full-size image that drew as nothing (blank white once
        // flattened). Asking for the decoded pixels proves they exist.
        guard image.dataProvider?.data != nil else { throw Failure.unreadable }
        return image
    }

    /// A JPEG has no transparency: drawn as it is, a transparent pixel turns black. So
    /// draw the image on white, in sRGB (what Claude expects; it also makes HDR and wide
    /// colour photos plain 8-bit), and encode that.
    private static func flattened(_ image: CGImage) throws -> CGImage {
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
            let context = CGContext(
                data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
                space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
            )
        else { throw Failure.unreadable }
        let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        context.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
        context.fill(rect)
        context.interpolationQuality = .high
        context.draw(image, in: rect)
        guard let flat = context.makeImage() else { throw Failure.unreadable }
        return flat
    }

    /// The image as JPEG or PNG bytes. Only the pixels and a fixed quality go in: no
    /// metadata is copied from the original (no date, no place, no camera), so the same
    /// pixels always give the same bytes.
    private static func encode(_ image: CGImage, as format: UTType) throws -> Data {
        let data = NSMutableData()
        guard let destination = CGImageDestinationCreateWithData(data, format.identifier as CFString, 1, nil)
        else { throw Failure.unreadable }
        let options: [CFString: Any] = format == .jpeg ? [kCGImageDestinationLossyCompressionQuality: jpegQuality] : [:]
        CGImageDestinationAddImage(destination, image, options as CFDictionary)
        guard CGImageDestinationFinalize(destination) else { throw Failure.unreadable }
        return data as Data
    }
}
