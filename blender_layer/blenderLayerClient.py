import threading, time, math
import bpy, gpu, numpy as np
import mathutils
import atexit
import os
from gpu_extras.presets import draw_texture_2d
import socket, sys, struct, pickle
from multiprocessing import shared_memory, SimpleQueue
from bpy.app.handlers import persistent

bl_info = {
    'name': "Connect to Krita (Blender Layer)",
    'author': "Yuntoko",
    'description': "Companion for the 'Blender Layer' Krita plugin",
    'version': (1, 0),
    'blender': (2, 80, 0),
    'category': '3D View',
}
client = None
HOST = 'localhost'
PORT = 65432
CONNECT = False
try:
    argv = sys.argv
    index = argv.index('--connect-to-krita')
    HOST = argv[index + 1]
    PORT = int(argv[index + 2])
    CONNECT = True
except ValueError:
    pass
    
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
    
def showMessageBox(message = "", title = "Blender Layer", icon = 'INFO'):
    def draw(self, context):
        self.layout.label(text=message)
    bpy.context.window_manager.popup_menu(draw, title = title, icon = icon)

class BlenderLayerClient():
    def __init__(self):
        self.connected = False
        self.thread = None
        self.s = None
        self.shm = None
        self.buf = []
        self.offscreen = None

    def connect(self, host, port):
        HOST = host
        PORT = port
        MAGIC = b'BLENDER_LAYER_V1'
        
        self.disconnect()
           
        self.recvQueue = SimpleQueue()    
        self.sendQueue = SimpleQueue()
        self.buf = []
        self.updateFlag = False
        self.requestFrame = True
        self.requestDelayedFrame = False
        self.frame = -1
        self.transparency_support = bpy.app.version >=(3, 6, 0)
        self.prevNumIds = None
        self.prevRot = None
        self.prevOrtho = None
        self.prevLens = None
        self.prevShading = None
        self.prevEngine = None
        self.prevPoseLib = None
        self.prevArmatures = None
        self.active_space = None
        self.active_region = None
        self.offscreen = None
        self.isRendering = False
        self.isAnimation = False
        self.animFrame = 0
        self.ticksWaitingForFrame = 0
        self.requestDisconnect = False

        print(f"[Blender Layer] Connecting to krita on port {PORT}...")
        try:
            self.connected = True
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.s.settimeout(5.0)
            self.s.connect((HOST, PORT))

            self.s.sendall(MAGIC)
            check = self.s.recv(len(MAGIC))
            if check != MAGIC:
                print("[Blender Layer] Protocol error: Expected " + MAGIC.decode('ASCII') + " not " + check.decode('ASCII'))
                raise RuntimeError("Protocol error")
                
            type, self.width, self.height, self.regionX, self.regionY, self.regionWidth, self.regionHeight, self.regionViewport, scale, framerateScale, self.formatDepth, self.bytesPerPixel, self.colorManagement, self.bgrConversion, self.transparency, self.gizmos, self.lensZoom, self.viewMode, self.updateMode, self.renderCurrentView, self.sharedMem, self.backgroundDraw = recvObj(self.s)
            self.scale = 2 ** scale
            self.framerateScale = 4 ** framerateScale
            self.orgWidth = self.width
            self.orgHeight = self.height
            self.dtype = np.uint8
            if self.formatDepth == 'RGBA16':
                self.dtype = np.uint16
            elif self.formatDepth == 'RGBA16F':
                self.dtype = np.float16
            elif self.formatDepth == 'RGBA32F':
                self.dtype = np.float32
                
            loaded = hasattr(bpy.data, 'filepath')
            
            self.prevFile = bpy.data.filepath if loaded else ''
            sendObj(self.s, ('Init', self.transparency_support, self.prevFile))
            if loaded:
                self.updatePoseLib()
                    
            if self.sharedMem:
                self.shm = shared_memory.SharedMemory(name=(f'krita_blender_layer:{PORT}'))
            else:
                self.shm = None
            bpy.app.timers.register(self.onUpdate, persistent = True)
            self.drawHandler = bpy.types.SpaceView3D.draw_handler_add(self.onDraw, (), 'WINDOW', 'POST_PIXEL' ) 
            bpy.app.handlers.render_write.append(self.onRenderFrame)
            bpy.app.handlers.render_cancel.append(self.onRenderCancelled)
            bpy.app.handlers.save_post.append(self.onFileSaved)
            bpy.app.handlers.load_post.append(self.onFileLoaded)
            bpy.app.handlers.depsgraph_update_post.append(self.onDepsGraphChanged)
            
            self.thread = threading.Thread(target=self.sendData, args=(), daemon=True)
            self.thread.start()
                  
            if loaded:
                self.tagForRedraw()
                showMessageBox('Connected')
            print('[Blender Layer] Connected')
            return True
        except Exception as e:
            self.disconnect()
            print(e)
            return False
                       
    def disconnect(self, atexit = False):
        if not self.connected:
            return
             
        self.connected = False
        self.requestDisconnect = False
        if not atexit:
            loaded = hasattr(bpy.data, 'filepath')
            if loaded:
                showMessageBox("Disconnected")       
        try:
            if self.thread:
                self.thread.join()
        except Exception as e:
            print(e)

        try:
            if self.isRendering and not atexit:
                self.isRendering = False
                self.revertRenderSettings()        
        except Exception as e:
            print(e)
            
        if not atexit:
            try:
                if bpy.app.timers.is_registered(self.onUpdate):
                    bpy.app.timers.unregister(self.onUpdate)
                bpy.types.SpaceView3D.draw_handler_remove(self.drawHandler, 'WINDOW')
                bpy.app.handlers.render_write.remove(self.onRenderFrame)
                bpy.app.handlers.render_cancel.remove(self.onRenderCancelled)
                bpy.app.handlers.save_post.remove(self.onFileSaved)
                bpy.app.handlers.load_post.remove(self.onFileLoaded)
                bpy.app.handlers.depsgraph_update_post.remove(self.onDepsGraphChanged)
            except Exception as e:
                print(e)
                      
        try:
            if self.s:
                self.s.close()
        except Exception as e:
            print(e)

        try:
            if self.shm:
                self.shm.close()
        except Exception as e:
            print(e)          
           
        if not atexit:
            self.freeOffscreen()

        print('[Blender Layer] Disconnected') 

    @persistent
    def onRenderFrame(self, scene, b):
        if self.isRendering:
            x = 0
            y = 0
            if self.renderOverrideRes:
                x = self.regionX
                y = self.regionY
            if self.isAnimation:              
                self.sendMessage(('renderProgress', scene.frame_current, scene.frame_start, scene.frame_end))
                path = bpy.context.scene.render.frame_path()
                self.sendMessage(('updateFrameFromFile', x, y, path, scene.frame_current))
                if scene.frame_current == scene.frame_end:
                    self.isRendering = False
                    self.isAnimation = False
                    self.revertRenderSettings()
            else:
                self.sendMessage(('renderProgress', 1, 0, 1))
                path = bpy.context.scene.render.filepath
                if bpy.context.scene.render.use_file_extension:
                    path = path + bpy.context.scene.render.file_extension
                self.sendMessage(('updateFromFile', x, y, path))
                self.isRendering = False
                self.revertRenderSettings()
     
    @persistent
    def onRenderCancelled(self, scene, b):
        if self.isRendering:
            self.sendMessage(('renderCancelled', True))
            self.isRendering = False
            self.isAnimation = False
            self.revertRenderSettings()
     
    def revertRenderSettings(self):
        scene = bpy.context.scene
        render = scene.render
        if self.renderTemporary:
            render.film_transparent = self.renderOrgTransparent
            render.resolution_x = self.renderOrgX
            render.resolution_y = self.renderOrgY
            render.resolution_percentage = self.renderOrgScale
            render.use_border = self.renderOrgBorder
            render.use_crop_to_border = self.renderOrgBorderCrop
            render.border_min_x = self.renderOrgBorderXMin
            render.border_max_x = self.renderOrgBorderXMax
            render.border_min_y = self.renderOrgBorderYMin
            render.border_max_y = self.renderOrgBorderYMax
        if self.animTemporary:
            scene.frame_start = self.sceneOrgStart
            scene.frame_end = self.sceneOrgEnd
            scene.frame_step = self.sceneOrgStep
            render.fps = self.renderOrgFps
            render.fps_base = self.renderOrgFpsBase
        if self.tmpCamera:
            bpy.context.scene.camera = self.prevCamera
            bpy.data.objects.remove(self.tmpCamera, do_unlink=True)
        self.tmpCamera = None
        self.prevCamera = None
     
    @persistent
    def onFileSaved(self, scene, b):
        self.sendMessage(('file', bpy.data.filepath))
      
    @persistent
    def onFileLoaded(self, scene, b):
        self.sendMessage(('file', bpy.data.filepath))
        self.freeOffscreen()
        self.updatePoseLib()
        self.active_space = None
        self.active_region = None
        self.prevEngine = None

    @persistent
    def onDepsGraphChanged(self, scene, depsGraph):
        numIds = len(depsGraph.ids)
        flag = False
        for update in depsGraph.updates:
            if isinstance(update.id, bpy.types.Action):
                flag = True
                
        if numIds != self.prevNumIds:
            flag = True
        if flag:
            self.updatePoseLib(False)
        self.prevNumIds = numIds
        
    def updatePoseLib(self, clear = True):
        armatures = {}
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                for m in obj.modifiers:
                    if m.type == 'ARMATURE' and m.object:
                        armatures[m.object.name] = None
        armatures = list(armatures)
        if self.prevArmatures != armatures:
            self.prevArmatures = armatures
            self.sendMessage(('armatures', armatures))
        
        poselib = [a.name for a in bpy.data.actions if a.asset_data]
        if self.prevPoseLib != poselib:
            self.prevPoseLib = poselib
            self.sendMessage(('poselib', poselib, clear))
        
    def getPosePreview(self, action):
        return np.array(action.preview.image_pixels, copy=False).ravel(order = 'F').reshape(128, 128)[::-1,:].ravel().tobytes()
        
    def sendMessage(self, msg):
        self.sendQueue.put(msg)
      
    def tagForRedraw(self):
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()                        
        
    def freeOffscreen(self):
        try:
            if self.offscreen:
                self.buf = []
                self.offscreen.free()
        except Exception as e:
            print(e)
        self.buf = []
        self.offscreen = None

    def onUpdate(self):    
        if self.requestDisconnect:
            self.disconnect()
        if not self.connected:
            return 0
            
        space = self.active_space
        region = self.active_region
        
        if space and not space.region_3d:
            space = None
            region = None
            
        if not space: 
            for area1 in bpy.context.screen.areas: 
                if area1.type == 'VIEW_3D':
                    for space1 in area1.spaces: 
                        if space1.type == 'VIEW_3D' and space1.region_3d:
                            space = space1
                            for region1 in area1.regions:
                                if region1.type == 'WINDOW':
                                    region = region1
                                    break
                            break
                if space:
                    break
            if not space:
                for screen in bpy.data.screens:
                    for area1 in screen.areas: 
                        if area1.type == 'VIEW_3D':
                            for space1 in area1.spaces: 
                                if space1.type == 'VIEW_3D' and space1.region_3d:
                                    space = space1
                                    for region1 in area1.regions:
                                        if region1.type == 'WINDOW':
                                            region = region1
                                            break
                                    break
                        if space:
                            break
                    if space:
                        break
                    
            self.active_space = space
            self.active_region = region

        flag = False
        try:
            while not self.recvQueue.empty():
                msg = self.recvQueue.get()
                type = msg[0]
                if type == 'rotate':
                    if space:
                        space.region_3d.view_rotation = mathutils.Euler((msg[1], -msg[3], msg[2])).to_quaternion()
                    flag = True
                elif type == 'pan':
                    if space:
                        space.region_3d.view_location += (mathutils.Quaternion(space.region_3d.view_rotation) @ mathutils.Vector((-msg[1], msg[2], 0))) * space.region_3d.view_distance * 0.25
                    flag = True
                elif type == 'zoom':
                    if space:
                        space.region_3d.view_distance *= math.exp(0.25 * msg[1])
                    flag = True
                elif type == 'lens':
                    if space:
                        prevLens = space.lens
                        space.lens = msg[1]
                        if self.lensZoom:
                            space.region_3d.view_distance *= space.lens / prevLens
                    flag = True
                elif type == 'lensZoom':
                    self.lensZoom = msg[1]
                elif type == 'ortho':
                    if space:
                        space.region_3d.view_perspective = 'ORTHO' if msg[1] else 'PERSP'
                    flag = True
                elif type == 'transparency':
                    self.transparency = msg[1]
                elif type == 'gizmos':
                    self.gizmos = msg[1]
                elif type == 'shading':
                    shading = ['WIREFRAME', 'SOLID', 'MATERIAL', 'RENDERED'][msg[1]]
                    if space:
                        space.shading.type = shading
                    flag = True
                elif type == 'region':
                    if self.regionWidth != msg[3] or self.regionHeight != msg[4]:
                        self.freeOffscreen()
                    if msg[3] > self.orgWidth or msg[4] > self.orgHeight:
                        self.sharedMem = False
                    self.regionX = msg[1]
                    self.regionY = msg[2]
                    self.regionWidth = msg[3]
                    self.regionHeight = msg[4]
                    self.regionViewport = msg[5]
                    self.updateFlag = False
                    self.sendMessage(('clear', True))
                elif type == 'renderCurrentView':
                    self.renderCurrentView = msg[1]
                elif type == 'resize':
                    self.freeOffscreen()
                    if msg[1] > self.orgWidth or msg[2] > self.orgHeight:
                        self.sharedMem = False
                    self.width = msg[1]
                    self.height = msg[2]
                elif type == 'scale':
                    self.scale = 2 ** msg[1]
                    self.freeOffscreen()
                elif type == 'framerateScale':
                    self.framerateScale = 4 ** msg[1]
                elif type == 'viewMode':
                    self.viewMode = msg[1]
                elif type == 'updateMode':
                    self.updateMode = msg[1]
                    if self.updateMode == 0:
                        region.tag_redraw()
                elif type == 'pose':
                    obj = bpy.data.objects.get(msg[1], None)
                    action =  bpy.data.actions.get(msg[2], None)
                    if obj and action:
                        if bpy.context.object and bpy.context.object.mode == 'EDIT':
                            bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
                        if msg[3]:
                            action = action.copy()
                            action.flip_with_pose(obj)
                        obj.animation_data_create()
                        obj.animation_data.action = action
                        self.requestDelayedFrame = True
                        self.sendMessage(('status', f"Applied pose {msg[2]}"))
                    else:
                        print(f"[Blender Layer] Cannot apply pose {msg[2]} to {msg[1]}, obj={obj}, action={action}")
                        self.updatePoseLib()
                        self.sendMessage(('status', f"Cannot apply pose {msg[2]} to {msg[1]}, obj={obj}, action={action}"))
                elif type == 'posePreviews':
                    previews = []
                    errorFlag = False
                    for name in msg[1]:
                        action = bpy.data.actions.get(name, None)
                        if action:
                            preview = self.getPosePreview(action)
                            previews.append((name, preview))
                        else:
                            errorFlag = True
                            print("[Blender Layer] Pose preview not found: ", name)
                            
                    if errorFlag:
                        self.updatePoseLib()
                        self.sendMessage(('status', "Pose preview not found"))
                    self.sendMessage(('posePreviews', previews))
                elif type == 'append':
                    objects = msg[3].split(';')
                    bpy.ops.object.mode_set(mode='OBJECT', toggle=False)

                    for obj in objects:
                        path = os.path.join(msg[2], obj)
                        directory = os.path.dirname(path)
                        base = os.path.basename(path)
                        bpy.ops.wm.append(filepath=path, directory=directory, filename=base, autoselect=False, active_collection=False)
                        
                    if msg[1] == 'Body-chan' or msg[1] == 'Body-kun':
                        armature = None
                        for obj in bpy.data.objects:
                            if 'WGT' in obj.name:
                                obj.hide_set(True)
                            if obj.type == 'MESH':
                                for m in obj.modifiers:
                                    if m.type == 'ARMATURE' and m.object:
                                        armature = m.object
                        for txt in bpy.data.texts:
                            if 'rig_ui' in txt.name:
                                txt.as_module()
                        bpy.context.view_layer.objects.active = armature
                        bpy.ops.object.mode_set(mode='POSE', toggle=False)

                    self.updatePoseLib()
                    self.requestDelayedFrame = True
                    self.sendMessage(('status', f"Added {msg[1]}"))
                elif type == 'render' or type == 'renderAnimation':
                    if not self.isRendering:                        
                        scene = bpy.context.scene
                        render = scene.render
                        self.renderOrgPath = render.filepath
                        self.renderOrgTransparent = render.film_transparent
                        self.renderOrgX = render.resolution_x
                        self.renderOrgY = render.resolution_y
                        self.renderOrgScale = render.resolution_percentage
                        self.renderOrgBorder = render.use_border
                        self.renderOrgBorderCrop = render.use_crop_to_border
                        self.renderOrgBorderXMin = render.border_min_x
                        self.renderOrgBorderXMax = render.border_max_x 
                        self.renderOrgBorderYMin = render.border_min_y
                        self.renderOrgBorderYMax = render.border_max_y 
                        self.sceneOrgStart = scene.frame_start
                        self.sceneOrgEnd = scene.frame_end
                        self.sceneOrgStep = scene.frame_step
                        self.renderOrgFps = render.fps
                        self.renderOrgFpsBase = render.fps_base
                        self.tmpCamera = None

                        self.renderTemporary = False
                        self.renderOverrideRes = False
                        self.animTemporary = False

                        if msg[1]:
                            self.renderTemporary = msg[2]
                            if msg[3]:
                                render.filepath = msg[4]
                            self.renderOverrideRes = msg[5]
                            if self.renderOverrideRes:
                                if self.regionViewport:
                                    render.resolution_x = self.width
                                    render.resolution_y = self.height
                                else:
                                    render.resolution_x = self.regionWidth
                                    render.resolution_y = self.regionHeight
                                render.resolution_percentage = 100
                                render.use_border = self.regionViewport
                                if render.use_border:               
                                    render.use_crop_to_border = True
                                    render.border_min_x = self.regionX / self.width
                                    render.border_max_x = (self.regionX + self.regionWidth) / self.width
                                    render.border_min_y = 1 - (self.regionY + self.regionHeight) / self.height
                                    render.border_max_y = 1 - self.regionY / self.height
                            if msg[6]:
                                render.film_transparent = True
                            
                        if type == 'renderAnimation':
                            if msg[7]:
                                self.animTemporary = msg[8]
                                render.fps = msg[10]
                                render.fps_base = 1 
                                scene.frame_start = msg[11]
                                scene.frame_end = msg[12]
                                scene.frame_step = msg[13]
                            self.sendMessage(('updateAnimation', msg[9], render.fps / render.fps_base, scene.frame_start, scene.frame_end, scene.frame_step))
                            
                        self.isRendering = True
                        self.isAnimation = type == 'renderAnimation'
                        self.requestFrame = False
                        self.updateFlag = False
                        self.updateMode = 2
                        try:
                            if self.renderCurrentView and space.region_3d.view_perspective != 'CAMERA':
                                cam = bpy.data.cameras.new('BlenderLayer_TMP')
                                cam.type = space.region_3d.view_perspective
                                if space.region_3d.view_perspective == 'ORTHO':
                                    cam.ortho_scale = 2.0 / (space.lens / 36.0 / space.region_3d.view_distance)
                                    cam.clip_start = space.clip_start
                                    cam.clip_end = space.clip_end
                                else:                               
                                    cam.lens = space.lens
                                    cam.sensor_width = 2.0 * 36.0
                                    cam.clip_start = space.clip_start
                                    cam.clip_end = space.clip_end
                                obj = bpy.data.objects.new('BlenderLayer_TMP', cam)
                                obj.location = space.region_3d.view_location + (space.region_3d.view_rotation @ mathutils.Vector((0, 0, space.region_3d.view_distance)))
                                obj.rotation_mode = 'QUATERNION'
                                obj.rotation_quaternion = space.region_3d.view_rotation
                                bpy.context.scene.collection.objects.link(obj)
                                self.prevCamera = bpy.context.scene.camera
                                self.tmpCamera = obj
                                bpy.context.scene.camera = obj
                            bpy.ops.render.render('INVOKE_DEFAULT', write_still=True, animation=self.isAnimation, use_viewport=False)
                            self.sendMessage(('status', "Started render..."))
                        except Exception as e:
                            self.onRenderCancelled(scene, None)
                            self.sendMessage(('status', str(e)))
                    else:
                        self.sendMessage(('status', "Render already in progress"))
                elif type == 'requestAnimation':
                    scene = bpy.context.scene
                    render = scene.render
                    if msg[1]:
                        self.animStart = msg[5]
                        self.animEnd = msg[6]
                        self.animSteps = msg[7]
                        fps = msg[4]
                        if not msg[2]:
                            render.fps = fps
                            render.fps_base = 1 
                            scene.frame_start = self.animStart
                            scene.frame_end = self.animEnd
                            scene.frame_step = self.animSteps
                    else:
                        fps = int(render.fps / render.fps_base)
                        self.animStart = scene.frame_start
                        self.animEnd = scene.frame_end
                        self.animSteps = scene.frame_step
                        
                    self.animFrame = self.animStart

                    self.isAnimation = True                
                    self.requestFrame = False
                    self.updateFlag = False
                    self.updateMode = 2
                        
                    self.sendMessage(('updateAnimation', msg[3], fps, self.animStart, self.animEnd, self.animSteps))
                elif type == 'requestFrame':
                    self.requestFrame = True
                    region.tag_redraw()                        
                elif type == 'assistants':
                    if space:
                        vm, pm = self.getMats(bpy.context, space)              
                        w = msg[2]
                        h = msg[3]
                        mat = mathutils.Matrix(pm) @ mathutils.Matrix(vm)
                        mat2 = mathutils.Matrix([[w * 0.5, 0, 0, w * 0.5], [0, -h * 0.5, 0, h * 0.5], [0, 0, 1, 0], [0, 0, 0, 1]])

                        vecs = [(1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0)]

                        for i in range(0, len(vecs)):
                            v = mat @ mathutils.Vector(vecs[i])
                            if abs(v.w) > 1.0e-5:
                                v = v / v.w
                            else:
                                v.w = 0.0
                            vecs[i] = mat2 @ v
                            
                        self.sendMessage(('assistants', msg[1], 
                        vecs[0].x, vecs[0].y, vecs[0].w == 0.0,
                        vecs[1].x, vecs[1].y, vecs[1].w == 0.0,
                        vecs[2].x, vecs[2].y, vecs[2].w == 0.0,
                        vecs[3].x, vecs[3].y))
                    else:
                        print("[Blender Layer] Cannot create assistants, since there is no active space")
                        self.sendMessage(('status', "Cannot create assistants, since there is no active space"))
                elif type == 'file':
                    bpy.ops.wm.open_mainfile(filepath=msg[1])
                    self.requestDelayedFrame = True
                    break
                else:
                    print("[Blender Layer] Received unrecognized message type: ", type)  
                    
                if self.updateMode == 1:
                    self.requestFrame = True
                    region.tag_redraw()                        
        except Exception as e:
            print(e)
            self.sendMessage(('status', str(e)))

        if space and space.region_3d:
            rot = mathutils.Quaternion(space.region_3d.view_rotation).to_euler()
            lens = space.lens
            ortho = space.region_3d.view_perspective == 'ORTHO'
            shading = ['WIREFRAME', 'SOLID', 'MATERIAL', 'RENDERED'].index(space.shading.type)
            engine = bpy.context.scene.render.engine
            
            if flag:
                if space.region_3d.view_perspective == 'CAMERA':
                    space.region_3d.view_perspective = 'PERSP'
                space.region_3d.update()
            else:
                if self.prevRot != rot:
                    roll = -rot.y
                    self.sendMessage(('rotate', rot.x, rot.z, roll))
                if self.prevLens != lens:
                    self.sendMessage(('lens', lens))
                if self.prevOrtho != ortho:
                    self.sendMessage(('ortho', ortho))
                if self.prevShading != shading:
                    self.sendMessage(('shading', shading))  
                    
            if self.prevEngine != engine:
                self.sendMessage(('engine', engine))
                
            if self.prevFile != bpy.data.filepath:
                self.sendMessage(('file', bpy.data.filepath))
                self.prevFile = bpy.data.filepath

            if self.prevShading != shading or self.prevEngine != engine:
                self.freeOffscreen()
                
            if self.isAnimation and not self.isRendering and bpy.context.scene.frame_current != self.animFrame:
                bpy.context.scene.frame_set(self.animFrame)
                
            self.prevRot = rot
            self.prevLens = lens
            self.prevOrtho = ortho
            self.prevShading = shading
            self.prevEngine = engine
            
            if self.requestFrame or self.updateMode == 0:
                self.ticksWaitingForFrame = self.ticksWaitingForFrame + 1
                if self.ticksWaitingForFrame == 60:
                    self.tagForRedraw()
                elif self.ticksWaitingForFrame == 120:
                    self.sendMessage(('status', "Waiting for on draw event... Make sure Blender is not minimized"))
            
            if self.backgroundDraw and (self.requestFrame or self.updateMode == 0 or self.isAnimation and not self.isRendering):
                self.draw(space, region)
                
        return 0.0166
    
    def onDraw(self):
        self.active_space = bpy.context.space_data
        self.active_region = bpy.context.region

        if self.requestDelayedFrame:
            if self.updateMode != 2:
                self.requestFrame = True
            self.requestDelayedFrame = False

        if self.connected and not self.backgroundDraw:
            self.draw(self.active_space, self.active_region)
            
    def sendData(self):
        try:
            while self.connected:
                msgs = []

                if self.updateFlag:
                    self.updateFlag = False
                    
                    scale = self.scale
                    x = self.regionX
                    y = self.regionY
                    h = self.regionHeight // scale
                    w = self.regionWidth // scale
                    if len(self.buf) == h and len(self.buf[0]) == w:
                        b = np.array(self.buf, copy=False, dtype=self.dtype).ravel(order = 'F')
                        if self.bgrConversion:
                            b = b.reshape(h, w, 4)[::-1,:,[2, 1, 0, 3]]
                        else:
                            b = b.reshape(h, w, 4)[::-1,:,[0, 1, 2, 3]]
                        if scale != 1:
                            b = b.repeat(scale, axis=0).repeat(scale, axis=1)
                        b = b.ravel().tobytes()
                        type = 'update'
                        frame = None
                        if self.isAnimation and not self.isRendering:
                            type = 'updateFrame'
                            frame = self.animFrame
                            self.sendMessage(('updateProgress', self.animFrame, self.animStart, self.animEnd))
                            self.animFrame = self.animFrame + self.animSteps
                            if self.animFrame > self.animEnd:
                                self.isAnimation = False
                                self.updateFlag = False
                                self.sendMessage(('updateProgress', self.animEnd, self.animStart, self.animEnd))
                        if self.sharedMem:
                            self.shm.buf[:len(b)] = b
                            msgs.append((type, x, y, w * scale, h * scale, None, frame))
                        else:
                            msgs.append((type, x, y, w * scale, h * scale, b, frame))
                    else:
                        print("[Blender Layer] Warning: Ignorig frame with outdated dimensions")

                lastType = None
                while not self.sendQueue.empty():
                    msg = self.sendQueue.get()
                    type = msg[0]
                    if type == lastType:
                        if type == 'posePreviews':
                            msg[1].extend(msgs[-1][1])
                        msgs[-1] = msg
                    else:
                        msgs.append(msg)
                    lastType = type

                if not self.connected:
                    break
                  
                sendObj(self.s, msgs)
                
                if not self.connected:
                    break
                    
                msgs = recvObj(self.s)
                while msgs == 'wait':
                    msgs = recvObj(self.s)
                if msgs:                        
                    for msg in msgs:
                        self.recvQueue.put(msg)

                #time.sleep(0.333)
        except Exception as e:
            print("[Blender Layer] Exception while communicating with Krita")
            print(e)                     
            self.requestDisconnect = True
            
    def draw(self, space, region):
        try:            
            context = bpy.context
            self.frame = self.frame + 1
            original_overlays = space.overlay.show_overlays
            gizmos = self.gizmos or (space.shading.type == 'RENDERED' and bpy.context.scene.render.engine == 'CYCLES')
             
            if self.connected and not self.isRendering and (self.updateMode == 0 and self.frame % self.framerateScale == 0 or self.updateMode != 0 and self.requestFrame or self.isAnimation and context.scene.frame_current == self.animFrame):
                if not self.offscreen:
                    self.offscreen = gpu.types.GPUOffScreen(self.regionWidth // self.scale, self.regionHeight // self.scale, format=self.formatDepth)
                                  
                space.overlay.show_overlays = gizmos                  
                vm, pm = self.getMats(context, space)
                if self.transparency_support and self.transparency:
                    self.offscreen.draw_view3d( context.scene, context.view_layer, space, region, vm, pm, do_color_management=self.colorManagement, draw_background=False)
                else:
                    self.offscreen.draw_view3d( context.scene, context.view_layer, space, region, vm, pm, do_color_management=self.colorManagement)
                space.overlay.show_overlays = original_overlays           
                self.buf = self.offscreen.texture_color.read()
                self.updateFlag = not self.isRendering and (self.updateMode == 0 and self.frame % self.framerateScale == 0 or self.updateMode != 0 and self.requestFrame or self.isAnimation and context.scene.frame_current == self.animFrame)
                self.requestFrame = False
            elif self.updateMode == 0:            
                space.overlay.show_overlays = original_overlays           
            if self.ticksWaitingForFrame >= 120:
                self.sendMessage(('status', "Updated frame"))
            self.ticksWaitingForFrame = 0
            
        except Exception as e:
            print("[Blender Layer] Exception while drawing")
            self.sendMessage(('status', str(e)))
            print(e)

    def getMats(self, context, space):
        if self.viewMode == 0 and not space.region_3d.view_perspective == 'CAMERA' or (self.viewMode == 2 and self.renderCurrentView) or not context.scene.camera:
            #vm = space.region_3d.view_matrix
            #pm = space.region_3d.window_matrix.copy()

            vm = (mathutils.Matrix.Translation(mathutils.Vector(space.region_3d.view_location)) @ mathutils.Quaternion(space.region_3d.view_rotation).to_matrix().to_4x4() @ mathutils.Matrix.Translation((0, 0, space.region_3d.view_distance))).inverted() 
            
            near = space.clip_start
            far = space.clip_end    
            dist = space.region_3d.view_distance
            size = space.lens / 36.0 / dist if space.region_3d.view_perspective == 'ORTHO' else space.lens / 36.0
            ratio = self.regionWidth / self.regionHeight
            
            if self.regionViewport:
                m = max(self.width, self.height)
                m = min(m / self.regionWidth, m / self.regionHeight)
                shiftX =  (self.regionX - (self.width  - self.regionWidth)  / 2) / self.regionWidth  * 2.0
                shiftY = -(self.regionY - (self.height - self.regionHeight) / 2) / self.regionHeight * 2.0
                size *= m
            else:
                shiftX = 0.0
                shiftY = 0.0
            
            p00 = size if ratio > 1 else size / ratio
            p11 = size * ratio if ratio > 1 else size
            div = (near - far)
            if div == 0:
                div = 1e-9
            if space.region_3d.view_perspective == 'ORTHO':
                pm = mathutils.Matrix((( p00, 0, 0, -shiftX),
                      ( 0, p11, 0, -shiftY),
                      ( 0, 0, -2.0 / far, 0),
                      ( 0, 0, 0, 1.0)))
                vm[2][3] += dist
            else:
                pm = mathutils.Matrix((( p00, 0, shiftX, 0),
                      ( 0, p11, shiftY, 0),
                      ( 0, 0, (far + near) / div, (2.0 * far * near) / div),
                      ( 0, 0, -1.0, 0)))        
        else:
            vm = context.scene.camera.matrix_world.inverted()
            pm = context.scene.camera.calc_matrix_camera(context.evaluated_depsgraph_get(), x=self.regionWidth, y=self.regionHeight)
            
            if self.regionViewport:
                m = max(self.width, self.height)
                m = min(m / self.regionWidth, m / self.regionHeight)
                shiftX =  (self.regionX - (self.width  - self.regionWidth)  / 2) / self.regionWidth  * 2.0
                shiftY = -(self.regionY - (self.height - self.regionHeight) / 2) / self.regionHeight * 2.0
                pm[0][0] *= m
                pm[1][1] *= m
                pm[0][2] = pm[0][2] * m + shiftX
                pm[1][2] = pm[1][2] * m + shiftY
            
        return (vm, pm)

class ConnectOperator(bpy.types.Operator):
    bl_idname = 'view.connect_krita'
    bl_label = "Connect to Krita"
    bl_description = "Connect to stream a 3d View into Krita (Blender Layer)"

    host: bpy.props.StringProperty(name="Host", default = HOST)
    port: bpy.props.IntProperty(name="Port", default = PORT)

    def execute(self, context):
        global client
        if client.connected:
            succeeded = client.disconnect()

        else:
            succeeded = client.connect(self.host, self.port)
            if succeeded:
                self.report({'INFO'}, "Connected")
            else:
                self.report({'ERROR'}, "Failed to connect")
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        global client

        if client.connected:
            col.label(text="Currently connected to krita! Disconnect?")
        else:
            row = col.row()
            row.prop(self, "host")
            row.prop(self, "port")     
        
def menu_func(self, context):
    self.layout.operator(ConnectOperator.bl_idname, text="Connect to Krita")

def register():  
    global client
    if client:
        client.disconnect()
    client = BlenderLayerClient()  
    
    bpy.utils.register_class(ConnectOperator)
    bpy.types.VIEW3D_MT_view.append(menu_func)

    if CONNECT:
        client.connect(HOST, PORT)
      
def unregister():  
    global client  
    bpy.utils.unregister_class(ConnectOperator)
    if client:
        client.disconnect()
  
if __name__ == '__main__':
    if 'blenderLayerClient' in bpy.context.preferences.addons.keys():
        print("[Blender Layer] Plugin is already registered")
    else:
        register()
        atexit.register(lambda: client.disconnect(True))
