import subprocess, time, socket, sys, math, struct, pickle, errno  
from multiprocessing import shared_memory, SimpleQueue
from PyQt5.QtCore import QRunnable, QObject, pyqtSignal, QByteArray
from PyQt5.QtGui import QImage

def sendObj(conn, obj):
    msg = pickle.dumps(obj, pickle.HIGHEST_PROTOCOL)
    msg = struct.pack('>I', len(msg)) + msg
    conn.sendall(msg)

def recvObj(conn):
    raw_msglen = recvAll(conn, 4)
    if not raw_msglen:
        return None
    msglen = struct.unpack('>I', raw_msglen)[0]
    msg = recvAll(conn, msglen)
    return pickle.loads(msg) 

def recvAll(conn, n):
    data = bytearray()
    while len(data) < n:
        packet = conn.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return data

instance = Krita.instance()

class RunnableSignals(QObject):
    finished = pyqtSignal(str)
    connected = pyqtSignal(bool, object)
    error = pyqtSignal(str)
    msgReceived = pyqtSignal(object)

class BlenderLayerServer(QRunnable):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.running = False
        self.signals = RunnableSignals()
        self.sendQueue = SimpleQueue()
        
    def sendMessage(self, msg):
        self.sendQueue.put(msg)

    def run(self):
        self.running = True
        shm = None
        s = None
        d = None
        l = None
        locked = False
        framesLocked = 0
            
        try:     
            d = instance.activeDocument()
            root = d.rootNode()

            l = d.nodeByName(self.settings.layerName)
            if l == None or l == 0:
                l = d.createNode(self.settings.layerName, 'paintLayer')
                root.addChildNode(l, None)               

            l.setLocked(False)

            format = "RGBA8"
            bytesPerPixel = 4
            convertBGR = self.settings.convertBGR
            if self.settings.overrideSRGB:    
                l.setColorSpace('RGBA', 'U8', 'sRGB-elle-V2-srgbtrc.icc')
            else:
                floating = False
                depth = d.colorDepth()
                if depth == 'U16':
                    format = 'RGBA16'
                    bytesPerPixel = 8
                    self.signals.error.emit(i18n("Warning: 16-bit integer format not supported"))
                elif depth == 'F16':
                    format = 'RGBA16F'
                    floating = True
                    bytesPerPixel = 8
                elif depth == 'F32':
                    format = 'RGBA32F' 
                    bytesPerPixel = 16
                    floating = True
                convertBGR = convertBGR and not floating and ('RGB' in d.colorModel())
                l.setColorSpace(d.colorModel(), d.colorDepth(), d.colorProfile())
                
            modifiedSupported = getattr(d, "setModified", None) != None

            width = d.width()
            height = d.height()
            orgWidth = width
            orgHeight = height
        
            HOST = self.settings.host
            PORT = self.settings.port
            MAGIC = b'BLENDER_LAYER_V1'
  
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.bind((HOST, PORT))
            s.listen(1)
            
            if self.settings.sharedMem:
                shm = shared_memory.SharedMemory(name=(f'krita_blender_layer:{PORT}'), create=True, size=bytesPerPixel * orgWidth * orgHeight)
            i = 0
            resultStr = ''
            while self.running:
                try:
                    i = i + 1
                    conn, addr = s.accept()
                    conn.sendall(MAGIC)
                    check = conn.recv(len(MAGIC))
                    if check != MAGIC:
                        conn.close()
                        self.signals.error.emit(i18n("Protocol error: Expected {0} not {1}!").format(MAGIC.decode('ASCII'),check.decode('ASCII')))
                        time.sleep(5.0)
                        continue

                    while not self.sendQueue.empty():
                        self.sendQueue.get()
                            
                    if not self.settings.region:
                        self.settings.regionX = 0
                        self.settings.regionY = 0
                        self.settings.regionWidth = width
                        self.settings.regionHeight = height
                    l.setLocked(True)

                    sendObj(conn, ('Init', width, height, self.settings.regionX, self.settings.regionY, self.settings.regionWidth, self.settings.regionHeight, self.settings.regionViewport, self.settings.scale, self.settings.framerateScale, format, bytesPerPixel, self.settings.colorManageBlender, convertBGR, self.settings.transparency, self.settings.gizmos, self.settings.lensZoom, self.settings.viewMode, self.settings.updateMode, self.settings.renderCurrentView, self.settings.sharedMem, self.settings.backgroundDraw))
                    self.signals.connected.emit(True, recvObj(conn))
                    
                    while self.running:
                        if l == None or l == 0:
                            l = d.nodeByName(self.settings.layerName)
                        if l == None or l == 0:
                            raise Exception(i18n("Error: Layer not found"))
                            
                        if d.width() != width or d.height() != height:
                            if d.width() > orgWidth or d.height() > orgHeight and shm:
                                self.signals.error.emit(i18n("Warning: Disabling shared memory since image size changed. Consider Reconnecting"))
                            width = d.width()
                            height = d.height()
                            if not width or not height or width <= 0 or height <= 0:
                                self.running = False
                                break
                            else:
                                self.sendMessage(('resize', width, height))
                                if not self.settings.region:
                                    self.settings.regionWidth = width
                                    self.settings.regionHeight = height
                                    self.sendMessage(('region', self.settings.regionX, self.settings.regionY, self.settings.regionWidth, self.settings.regionHeight, self.settings.regionViewport))

                        msgs = recvObj(conn)
                        if msgs:
                            for msg in msgs:
                                if msg[0] == 'update' or msg[0] == 'updateFrame' or msg[0] == 'updateFrameFromFile' or msg[0] == 'updateFromFile' or msg[0] == 'clear':
                                    if msg[0] == 'updateFrameFromFile' or msg[0] == 'updateFrame':
                                        t = msg[6] if msg[0] == 'updateFrame' else msg[4]
                                        if locked:
                                            d.unlock()
                                            locked = False
                                            framesLocked = 0
                                        d.setActiveNode(d.rootNode())                               
                                        d.setActiveNode(l)
                                        d.setCurrentTime(t)
                                        if t > 0:
                                            l.setLocked(False)
                                            instance.action('add_blank_frame').trigger() 
                                            while not l.hasKeyframeAtTime(t) and self.running:
                                                time.sleep(0.01)
                                            l.setLocked(True)
                                            d.waitForDone()
                                    if not locked and self.settings.lockFrames > 0:
                                        for i in range(1, 20):
                                            if d.tryBarrierLock():
                                                locked = True
                                                break
                                            time.sleep(0.01)
                                    if locked or self.settings.lockFrames == 0:
                                        if msg[0] == 'update' or msg[0] == 'updateFrame':
                                            x = msg[1]
                                            y = msg[2]
                                            w = msg[3]
                                            h = msg[4]
                                            if msg[0] == 'updateFrame':
                                                if x > 0 or y > 0 or w < d.width() or h < d.height():
                                                    l.setPixelData(QByteArray(bytes(d.width() * d.height() * 4)), 0, 0, d.width(), d.height())
                                            if msg[5]:
                                                l.setPixelData(QByteArray(msg[5]), x, y, w, h)
                                            elif shm:
                                                l.setPixelData(QByteArray(shm.buf.tobytes()), x, y, w, h)
                                            d.refreshProjection()
                                            if modifiedSupported:
                                                d.setModified(True)
                                        elif msg[0] == 'updateFromFile' or msg[0] == 'updateFrameFromFile':
                                            frame = QImage(msg[3])
                                            if format == 'RGBA8':
                                                frame = frame.convertToFormat(QImage.Format_RGBA8888)
                                            elif format == 'RGBA16':
                                                frame = frame.convertToFormat(QImage.Format_RGBA64)
                                            elif format == 'RGBA16F':
                                                #frame = frame.convertToFormat(QImage.Format_RGBA16FPx4)
                                                self.signals.error.emit(i18n("Warning: Float format conversion not supported"))
                                            elif format == 'RGBA32F':
                                                #frame = frame.convertToFormat(QImage.Format_RGBA32FPx4)
                                                self.signals.error.emit(i18n("Warning: Float format conversion not supported"))
                                            if convertBGR:
                                                frame = frame.rgbSwapped()
                                            bits = frame.constBits()

                                            if bits:
                                                x = msg[1]
                                                y = msg[2]
                                                w = frame.width()
                                                h = frame.height()
                                                if msg[0] == 'updateFrameFromFile':
                                                    #t = msg[4]
                                                    #framesLocked = self.settings.lockFrames
                                                    #time.sleep(0.05)
                                                    if x > 0 or y > 0 or w < d.width() or h < d.height():
                                                        l.setPixelData(QByteArray(bytes(d.width() * d.height() * 4)), 0, 0, d.width(), d.height())

                                                l.setPixelData(QByteArray.fromRawData(bits.asarray(frame.sizeInBytes())), x, y, w, h)
                                                d.refreshProjection()
                                            else:
                                                self.signals.error.emit(i18n("Warning: Failed to open a rendered frame"))
                                            if modifiedSupported:
                                                d.setModified(True)
                                        else:
                                            l.setPixelData(QByteArray(bytes(d.width() * d.height() * 4)), 0, 0, d.width(), d.height())

                                    elif self.settings.updateMode > 0:
                                        self.signals.error.emit(i18n("Warning: Failed to acquire lock. Dropping a frame"))
                                elif msg[0] == 'updateAnimation':
                                    start = msg[3]
                                    end = msg[4]
                                    steps = msg[5]
                                    if msg[1]:
                                        d.setFramesPerSecond(msg[2])
                                        d.setFullClipRangeStartTime(start)
                                        d.setFullClipRangeEndTime(end)
                                    if start == 0:
                                        start = 1
                                        
                                    if locked:
                                        d.unlock()
                                        locked = False
                                        framesLocked = 0
                                   
                                    d.setActiveNode(d.rootNode())                               
                                    d.setActiveNode(l)
                                    l.setLocked(False)

                                    if not l.animated():
                                        l.enableAnimation()                               
                                        l.setPinnedToTimeline(True)
                                        
                                    #for t in range(start, end + 1):
                                    #    d.setCurrentTime(start)
                                    #    instance.action('remove_frames_and_pull').trigger() 
                                    #    if self.running and t % 10 == 0:
                                    #        sendObj(conn, 'wait')
                                         
                                    d.waitForDone()                                         
                                    for t in range(start, end + 1):
                                        #keyframe = t % steps == 0
                                        #if keyframe and not l.hasKeyframeAtTime(t):
                                        #    d.setCurrentTime(t)
                                        #    instance.action('add_blank_frame').trigger() 
                                        #    while not l.hasKeyframeAtTime(t) and self.running:
                                        #        time.sleep(0.01)
                                        #elif not keyframe and l.hasKeyframeAtTime(t):
                                        #    d.setCurrentTime(t)
                                        #    instance.action('remove_frames').trigger() 
                                        #    while l.hasKeyframeAtTime(t) and self.running:
                                        #        time.sleep(0.01)
                                        if l.hasKeyframeAtTime(t):
                                            d.setCurrentTime(t)
                                            instance.action('remove_frames').trigger() 
                                            i = 0
                                            while l.hasKeyframeAtTime(t) and self.running:
                                                i = i + 1
                                                if i % 10 == 0:
                                                    d.setCurrentTime(t)
                                                    instance.action('remove_frames').trigger() 
                                                time.sleep(0.01)
                                            if self.running:
                                                sendObj(conn, 'wait')

                                    d.waitForDone()
                                    l.setLocked(True)
                                else:
                                    self.signals.msgReceived.emit(msg)
                             
                            
                        if locked:
                            framesLocked = framesLocked + 1
                            if framesLocked >= self.settings.lockFrames:
                                d.unlock()
                                locked = False
                                framesLocked = 0
                                
                        msgs = []
                        lastType = None
                        while not self.sendQueue.empty():
                            msg = self.sendQueue.get()
                            type = msg[0]
                            if type == lastType and type != 'append':
                                if type == 'zoom':
                                    msg = (type, msg[1] + msgs[-1][1])
                                elif type == 'pan':
                                    msg = (type, msg[1] + msgs[-1][1], msg[2] + msgs[-1][2])
                                elif type == 'posePreviews':
                                    msg[1].extend(msgs[-1][1])
                                msgs[-1] = msg
                            else:
                                msgs.append(msg)
                            lastType = type
                                                            
                        if self.running:
                            sendObj(conn, msgs)

                    conn.close()
                except socket.timeout:
                    pass
                l.setLocked(False)
                self.signals.connected.emit(False, None)       
        except socket.error as e:
            if e.errno == errno.ECONNRESET or e.errno == errno.ECONNABORTED:
                pass
            elif e.errno == errno.EADDRINUSE:
                resultStr = i18n("Port occupied. Change it in settings")
            else:
                resultStr = str(e)
        except Exception as e:
            resultStr = str(e)
                
        try:
            if locked:
                d.unlock()
        except Exception as e:
            print(e)
            
        try:
            if s:
                s.close()
        except Exception as e:
            print(e)
            
        try:
            if shm:
                shm.close()
                shm.unlink()
        except Exception as e:
            print(e)
        
        self.running = False
        if l:
            l.setLocked(False)
        self.signals.finished.emit(resultStr)

class BlenderRunnable(QRunnable):
    def __init__(self, popenArgs):
        super().__init__()
        self.popenArgs = popenArgs
        self.signals = RunnableSignals()

    def run(self):
        result = ''
        try:
            proc = subprocess.Popen(self.popenArgs)
            proc.wait()
        except Exception as e:
            result = str(e)
        self.signals.finished.emit(result)