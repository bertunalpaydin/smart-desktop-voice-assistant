"""Draws Jarvis's app icon and saves it as Jarvis.icns (the Mac's icon format).

    QT_QPA_PLATFORM=offscreen python macos/make_icon.py

The icon is drawn at every size the Mac uses, in the HUD's colours: a glowing
core inside turning rings, on a deep blue tile shaped like other Mac icons.
"""
import math
import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QRadialGradient

HERE = Path(__file__).resolve().parent
CYAN, ICE = "#3ddcff", "#d7f7ff"

# icns entry type -> pixel size; all are stored as PNG, which macOS reads for every one of them
SIZES = {"icp4": 16, "icp5": 32, "icp6": 64, "ic07": 128, "ic08": 256, "ic09": 512, "ic10": 1024,
         "ic11": 32, "ic12": 64, "ic13": 256, "ic14": 512}


def draw(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 1024, size / 1024)

    # the tile: Apple's grid puts an 824-pixel rounded square in the middle of 1024
    tile = QRectF(100, 100, 824, 824)
    path = QPainterPath()
    path.addRoundedRect(tile, 185, 185)
    shadow = QColor(0, 0, 0, 90)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shadow)
    p.drawPath(path.translated(0, 12))
    background = QRadialGradient(QPointF(512, 470), 600)
    background.setColorAt(0.0, QColor("#0c2a44"))
    background.setColorAt(0.6, QColor("#06162a"))
    background.setColorAt(1.0, QColor("#020812"))
    p.setBrush(background)
    p.drawPath(path)
    p.setClipPath(path)

    center = QPointF(512, 512)
    cyan = QColor(CYAN)

    def tinted(alpha):
        color = QColor(cyan)
        color.setAlphaF(alpha)
        return color

    glow = QRadialGradient(center, 380)
    glow.setColorAt(0.0, tinted(0.45))
    glow.setColorAt(0.5, tinted(0.10))
    glow.setColorAt(1.0, tinted(0.0))
    p.setBrush(glow)
    p.drawEllipse(center, 380, 380)

    # the scale around the edge
    for i in range(60):
        a = math.radians(i * 6)
        major = i % 5 == 0
        inner = 300 if major else 312
        p.setPen(QPen(tinted(0.9 if major else 0.35), 7 if major else 4))
        p.drawLine(QPointF(512 + inner * math.cos(a), 512 + inner * math.sin(a)),
                   QPointF(512 + 330 * math.cos(a), 512 + 330 * math.sin(a)))

    def arc(radius, width, alpha, start, span):
        pen = QPen(tinted(alpha), width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(512 - radius, 512 - radius, 2 * radius, 2 * radius), int(start * 16), int(span * 16))

    for k in range(6):
        arc(262, 26, 0.95, 20 + k * 60, 38)
    for k in range(3):
        arc(214, 10, 0.6, -10 + k * 120, 84)

    core = QRadialGradient(center, 170)
    core.setColorAt(0.0, QColor(ICE))
    core.setColorAt(0.35, tinted(0.95))
    core.setColorAt(0.75, tinted(0.25))
    core.setColorAt(1.0, tinted(0.0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(core)
    p.drawEllipse(center, 170, 170)
    p.setPen(QPen(tinted(0.9), 9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, 118, 118)
    p.end()
    return image


def png_bytes(image):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def icns(entries):
    """entries: {type: PNG bytes} -> the bytes of an .icns file."""
    body = b"".join(kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data for kind, data in entries.items())
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def main(target=HERE / "Jarvis.icns"):
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # needed to draw text-free images too
    images = {}
    for kind, size in SIZES.items():
        images.setdefault(size, png_bytes(draw(size)))
    Path(target).write_bytes(icns({kind: images[size] for kind, size in SIZES.items()}))
    del app
    print(f"Saved {target}")
    return target


if __name__ == "__main__":
    main(*sys.argv[1:2])
