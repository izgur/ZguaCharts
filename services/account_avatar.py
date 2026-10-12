"""Accept only bounded, true-colour PNGs; strip ancillary metadata."""
import base64
import struct
import zlib


def clean_avatar(value):
    if value in (None, ""):
        return None
    prefix = "data:image/png;base64,"
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 120000:
        raise ValueError("Choose an image under 5 MB; the avatar must be resized to 128 pixels")
    try:
        data = base64.b64decode(value[len(prefix):], validate=True)
        if data[:8] != b"\x89PNG\r\n\x1a\n": raise ValueError()
        position, chunks, compressed, dimensions, ended = 8, [], b"", None, False
        while position < len(data):
            size, kind = struct.unpack(">I4s", data[position:position + 8])
            payload = data[position + 8:position + 8 + size]
            checksum = struct.unpack(">I", data[position + 8 + size:position + 12 + size])[0]
            if zlib.crc32(kind + payload) & 0xffffffff != checksum: raise ValueError()
            if kind == b"IHDR":
                if dimensions is not None or position != 8 or size != 13: raise ValueError()
                width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
                if not (1 <= width <= 128 and 1 <= height <= 128) or depth != 8 or colour not in (2, 6) or any((compression, filtering, interlace)): raise ValueError()
                dimensions = (width, height, 4 if colour == 6 else 3)
                chunks.append(data[position:position + size + 12])
            elif kind == b"IDAT":
                if dimensions is None: raise ValueError()
                compressed += payload
                chunks.append(data[position:position + size + 12])
            elif kind == b"IEND":
                if size != 0 or position + 12 != len(data): raise ValueError()
                chunks.append(data[position:position + 12]); ended = True
            elif kind[:1].isupper():
                raise ValueError()
            position += size + 12
        if not dimensions or not ended or not compressed: raise ValueError()
        width, height, channels = dimensions
        stride = width * channels + 1
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, stride * height + 1)
        if not decoder.eof or decoder.unused_data or len(pixels) != stride * height or any(pixels[i] > 4 for i in range(0, len(pixels), stride)): raise ValueError()
        return prefix + base64.b64encode(data[:8] + b"".join(chunks)).decode("ascii")
    except (ValueError, TypeError, struct.error, zlib.error):
        raise ValueError("Invalid avatar image. Choose a PNG, JPEG or WebP image and try again.") from None
