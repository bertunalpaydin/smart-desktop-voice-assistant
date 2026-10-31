"""Small line icons for buttons."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def draw_symbol(kind, size=20, color="#ffffff"):
    """Small line icons for the buttons: a microphone or a send arrow."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 20, size / 20)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    if kind == "mic":
        painter.drawRoundedRect(QRectF(7, 2, 6, 10), 3, 3)
        painter.drawArc(QRectF(4, 5, 12, 10), 200 * 16, 140 * 16)
        painter.drawLine(QPointF(10, 15), QPointF(10, 18))
        painter.drawLine(QPointF(7, 18), QPointF(13, 18))
    else:  # send
        path = QPainterPath()
        path.moveTo(3, 10)
        path.lineTo(17, 3)
        path.lineTo(12, 17)
        path.lineTo(9.5, 11)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)
