from PyQt5.QtCore import QRunnable, Qt, pyqtSignal, QSize, QPointF, QRectF
from PyQt5.QtGui import QPainter, QBrush, QColor, QIcon, QPalette, QCursor
import sys, math
from PyQt5.QtWidgets import (
    QWidget,
    QSizePolicy,
    QApplication
)

class Axis():
    def __init__(self, x, y, z, color, name):
        self.x = x
        self.y = y
        self.z = z
        self.color = color
        self.name = name

instance = Krita.instance()

class NavigateWidget(QWidget):
    rotateSignal = pyqtSignal(QPointF)
    panSignal = pyqtSignal(QPointF)
    zoomSignal = pyqtSignal(float)
    orthoSignal = pyqtSignal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed
        )
        self.setMouseTracking(True)
        self.highlight = 0
        self.dragStartPos = QPointF(0,0)
        self.dragStartVal = QPointF(0,0)
        self.rotation = QPointF(math.pi * (0.5 + 0.125), math.pi * 0.125)
        self.ortho = False
        self.fullscreenMode = False

    def setRotation(self, x, y):
        self.rotation = QPointF(x, y)
        self.update()
        
    def setOrtho(self, ortho):
        self.ortho = ortho
        self.update()

    def rotation(self):
        return self.rotation

    def sizeHint(self):
        return QSize(120,120)
        
    def mousePressEvent(self, event, fullscreenMode = False):
        self.fullscreenMode = fullscreenMode
        if event.buttons() == Qt.LeftButton or event.buttons() == Qt.MidButton:
            event.accept()
            self.highlight = self.getHighlight(event.pos())
            if self.highlight < 2:
                if (event.modifiers() & Qt.ShiftModifier) == Qt.ShiftModifier:
                    self.highlight = 3
                elif (event.modifiers() & Qt.ControlModifier) == Qt.ControlModifier:
                    self.highlight = 2  
            self.dragStartPos = QPointF(event.pos())
            self.dragStartVal = QPointF(self.rotation)
                
            if self.highlight == 4:
                self.ortho = not self.ortho
                self.orthoSignal.emit(self.ortho)
            else:
                self.grabMouse(QCursor(Qt.SizeVerCursor if self.highlight == 2 else (Qt.SizeAllCursor if self.highlight == 3 else Qt.BlankCursor)))
            self.update()
        elif event.buttons() == Qt.RightButton:
            event.accept()
            self.dragStartPos = QPointF(event.pos())
            self.dragStartVal = QPointF(0, 0)
            self.grabMouse(QCursor(Qt.SizeAllCursor))
            self.highlight = 3
            self.update()
        else:
            return super().mousePressEvent(event)
        
    def mouseReleaseEvent(self, event):
        event.accept()
        self.releaseMouse()
        self.highlight = self.getHighlight(event.pos())
        self.update()
        self.fullscreenMode = False
        
    def mouseMoveEvent(self, event):
        highlight = self.getHighlight(event.pos())
        if event.buttons() == Qt.LeftButton or event.buttons() == Qt.MidButton or event.buttons() == Qt.RightButton:
            if self.highlight == 0:
                self.highlight = 1 if highlight == 0 else highlight  
                
            w = self.geometry().width()
            h = self.geometry().height()
            m = min(w, h)
                    
            pos = event.pos()
            delta = pos - self.dragStartPos
            delta = QPointF((delta.x() + w / 2) % w - w / 2, (delta.y() + h / 2) % h - h / 2)
            delta = delta / m

            if self.highlight == 1:
                delta = delta * 0.5 * math.pi
                if self.dragStartVal.x() % (math.pi * 2) > math.pi:
                    delta.setX(-delta.x())
                self.rotation -= QPointF(delta.y(), delta.x())
                self.rotateSignal.emit(self.rotation)

            elif self.highlight == 2:
                self.zoomSignal.emit(delta.y())
            
            elif self.highlight == 3:
                self.panSignal.emit(delta)
                
            self.dragStartPos = QPointF(pos)
            cursor = QCursor()
            cursorPos = cursor.pos()
            if self.fullscreenMode:
                pos = cursorPos
                size = QApplication.primaryScreen().size()
                w = size.width()
                h = size.height()
            if pos.x() <= 0:
                cursor.setPos(cursorPos.x() + w - 2, cursorPos.y())
            elif pos.x() >= w - 1:
                cursor.setPos(cursorPos.x() - w + 2, cursorPos.y())
            if pos.y() <= 0:
                cursor.setPos(cursorPos.x(), cursorPos.y() + h - 2)
            elif pos.y() >= h - 1:
                cursor.setPos(cursorPos.x(), cursorPos.y() - h + 2)
            self.update()
                        
        elif self.highlight != highlight:
            self.highlight = highlight
            self.update()
            
        super().mouseMoveEvent(event)

    def getHighlight(self, pos): 
        w = self.geometry().width()
        h = self.geometry().height()
        m = min(w, h)
        
        r3 = m * 0.15
        x = w - r3 * 1.5
        y = h * 0.5 - r3 * 1.5 - m * 0.1
        
        if self.inCircle(pos, w * 0.5, h * 0.5, m * 0.5):
            return 1
        elif self.inCircle(pos, x, h * 0.5 - r3 * 1.5 - m * 0.1, r3):
            return 2
        elif self.inCircle(pos, x, h * 0.5, r3):
            return 3
        elif self.inCircle(pos, x, h * 0.5 + r3 * 1.5 + m * 0.1, r3):
            return 4
        return 0
        
    def inCircle(self, pos, x, y, rad):
        d = pos - QPointF(x, y)
        return d.x() * d.x() + d.y() * d.y() < rad * rad
    
    def leaveEvent(self, event):
        if self.highlight > 0:
            self.highlight = 0
            self.update()
        super().leaveEvent(event)    
        
    def wheelEvent(self, event):
        event.accept()
        self.zoomSignal.emit((event.angleDelta().x() + event.angleDelta().y()) / -360.0)
        #super().wheelEvent(event)
    
    def rotateAxis(self, a):     
        x = math.cos(-self.rotation.y()) * a.x - math.sin(-self.rotation.y()) * a.y
        y = math.sin(-self.rotation.y()) * a.x + math.cos(-self.rotation.y()) * a.y
        z = a.z

        a.x = x
        a.y = math.cos(-self.rotation.x()) * y - math.sin(-self.rotation.x()) * z
        a.z = math.sin(-self.rotation.x()) * y + math.cos(-self.rotation.x()) * z
        return a
        
    def paintEvent(self, e):
        painter = QPainter(self)
        w = painter.device().width()
        h = painter.device().height()
        m = min(w, h)
        r = m * 0.1
        r2 = m * 0.5 - r
        r3 = m * 0.15
        center = QPointF(w * 0.5, h * 0.5)
        
        brush = QBrush()
        brush.setStyle(Qt.SolidPattern)
        
        pen = painter.pen()
        pen.setWidthF(2)

        font = painter.font()
        #font.setFamily('Times')
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)

        #painter.begin(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        
        if self.highlight == 1:
            painter.setBrush(self.palette().light())
            painter.drawEllipse(center, m * 0.5, m * 0.5);
        
        if self.isEnabled():
            axis = [Axis(1, 0, 0, QColor(255, 51, 82, 255), 'X'), Axis(0, 1, 0, QColor(139, 220, 0, 255), 'Y'), Axis(0, 0, 1, QColor(40, 144, 255, 255), 'Z')]        
        else:
            axis = [Axis(1, 0, 0, QColor(97, 97, 97, 255), 'X'), Axis(0, 1, 0, QColor(187, 187, 187, 255), 'Y'), Axis(0, 0, 1, QColor(123, 123, 123, 255), 'Z')]
        axis = list(map(self.rotateAxis, axis))
        axis.sort(key=lambda a: abs(a.z))
        background = self.palette().color(QPalette.Window)
            
        for a in axis:
            f = ((a.z if a.z <= 0 else -a.z) + 1) / 2 * 0.5 + 0.5
            c = QColor(int(round(a.color.red() * f + background.red() * (1 - f))), int(round(a.color.green() * f + background.green() * (1 - f))), int(round(a.color.blue() * f + background.blue() * (1 - f))), 255)
            pen.setColor(c)
            if a.z <= 0:
                p = center + QPointF(r2 * a.x, r2 * -a.y)
                
                painter.setPen(pen)
                painter.drawLine(center, p);
            
                painter.setPen(Qt.NoPen)
                brush.setColor(c)
                painter.setBrush(brush)
                painter.drawEllipse(p, r, r);
                
                pen.setColor(QColor('black'))
                painter.setPen(pen)
                rect = QRectF(p.x() - r, p.y() - r - 1, r * 2, r * 2)
                painter.drawText(rect, Qt.AlignCenter, a.name)
            else:
                p = center + QPointF(r2 * -a.x, r2 * a.y)

                c = QColor(a.color.red() // 4 + background.red() * 3 // 4, a.color.green() // 4 + background.green() * 3 // 4, a.color.blue() // 4 + background.blue() * 3 // 4, 255)
                c.setAlphaF(1 - a.z)
                brush.setColor(c)
                painter.setBrush(brush)
                painter.setPen(pen)
                painter.drawEllipse(p, r - 2, r - 2)     
            
        for a in axis:
            f = ((a.z if a.z > 0 else -a.z) + 1) / 2 * 0.5 + 0.5
            c = QColor(int(round(a.color.red() * f + background.red() * (1 - f))), int(round(a.color.green() * f + background.green() * (1 - f))), int(round(a.color.blue() * f + background.blue() * (1 - f))), 255)
            pen.setColor(c)
            if a.z > 0:
                p = center + QPointF(r2 * a.x, r2 * -a.y)
                
                painter.setPen(pen)
                painter.drawLine(center, p);
            
                painter.setPen(Qt.NoPen)
                brush.setColor(c)
                painter.setBrush(brush)
                painter.drawEllipse(p, r, r);
                
                pen.setColor(QColor('black'))
                painter.setPen(pen)
                rect = QRectF(p.x() - r, p.y() - r - 1, r * 2, r * 2)
                painter.drawText(rect, Qt.AlignCenter, a.name)
            else:
                p = center + QPointF(r2 * -a.x, r2 * a.y)

                c = QColor(a.color.red() // 4 + background.red() * 3 // 4, a.color.green() // 4 + background.green() * 3 // 4, a.color.blue() // 4 + background.blue() * 3 // 4, 255)
                brush.setColor(c)
                painter.setBrush(brush)
                painter.setPen(pen)
                painter.drawEllipse(p, r - 2, r - 2)     
            
            
        painter.setPen(Qt.NoPen)
        brush = self.palette().base()
        painter.setBrush(brush)
        s = r3 * 1.15
        s2 = int(r3 * 1.25)
        x = w - r3 * 1.5
        y = h * 0.5 - r3 * 1.5 - m * 0.1
        if self.highlight == 2:
            painter.setBrush(self.palette().light())
        painter.drawEllipse(QPointF(x, y), r3, r3);
        instance.icon('tool_zoom').paint(painter, int(x - s * 0.5), int(y - s * 0.5), s2, s2, Qt.AlignCenter, QIcon.Normal if self.isEnabled() else QIcon.Disabled)
        if self.highlight == 2:
            painter.setBrush(brush)

        y = h * 0.5
        if self.highlight == 3:
            painter.setBrush(self.palette().light())
        painter.drawEllipse(QPointF(x, y), r3, r3);
        instance.icon('tool_pan').paint(painter, int(x - s * 0.5), int(y - s * 0.5), s2, s2, Qt.AlignCenter, QIcon.Normal if self.isEnabled() else QIcon.Disabled)
        if self.highlight == 3:
            painter.setBrush(brush)
            
        if self.highlight == 4:
            painter.setBrush(self.palette().light())
        y = h * 0.5 + r3 * 1.5 + m * 0.1
        painter.drawEllipse(QPointF(x, y), r3, r3);
        instance.icon('krita_tool_grid' if (self.ortho) else 'tool_perspectivegrid').paint(painter, int(x - s * 0.5), int(y - s * 0.5), s2, s2, Qt.AlignCenter, QIcon.Normal if self.isEnabled() else QIcon.Disabled)