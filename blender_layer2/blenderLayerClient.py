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
    'version': (2, 0),
    'blender': (2, 80, 0),
    'category': '3D View',
}
client = None
HOST = 'localhost'
PORT = 65432
CONNECT = False
TIMEOUT = 10.0
try:
    argv = sys.argv
    index = argv.index('--connect-to-krita')
    HOST = argv[index + 1]
    PORT = int(argv[index + 2])
    try:
        TIMEOUT = float(argv[index + 3])
    except ValueError:
        pass
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
        self.drawHandler = None
        self.buf = []
        self.texBuf = []
        self.offscreen = None

    def connect(self, host, port):
        HOST = host
        PORT = port
        MAGIC = b'BLENDER_LAYER_V2'
        
        self.disconnect()
           
        self.recvQueue = SimpleQueue()    
        self.sendQueue = SimpleQueue()
        self.buf = []
        self.texBuf = []
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
        self.prevShadingLight = None
        self.prevShadingColor = None
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
        self.cursorX = -1
        self.cursorY = -1
        self.cursorSizeX = -1
        self.cursorSizeY = -1
        self.refreshHack = False
        self.cursorColor = mathutils.Vector((1.0, 1.0, 0.0))
        
        self.viewLocation = mathutils.Vector((0, 0, 0))
        self.viewRotation = mathutils.Quaternion((0.71579509973526, 0.4389207363128662, 0.29061830043792725, 0.4588318467140198))
        self.viewDistance = 17.986562728881836   
        self.viewPerspective = 'PERSP'
        self.viewLens = 50.0
                
        print(f"[Blender Layer] Connecting to Krita on port {PORT}...")
        try:
            self.connected = True
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.s.settimeout(TIMEOUT)
            self.s.connect((HOST, PORT))

            self.s.sendall(MAGIC)
            check = self.s.recv(len(MAGIC))
            if check != MAGIC:
                print("[Blender Layer] Protocol error: Expected " + MAGIC.decode('ASCII') + " not " + check.decode('ASCII'))
                raise RuntimeError("Protocol error")
                
            type, self.width, self.height, self.regionX, self.regionY, self.regionWidth, self.regionHeight, self.regionViewport, scale, framerateScale, self.formatDepth, self.bytesPerPixel, self.colorManagement, self.bgrConversion, self.transparency, self.gizmos, self.lensZoom, self.viewMode, self.updateMode, self.renderCurrentView, self.sharedMem, self.backgroundDraw, cursorColorString = recvObj(self.s)
            self.scale = 2 ** scale
            self.framerateScale = 4 ** framerateScale
            try:
                h = cursorColorString.lstrip('#')
                self.cursorColor = mathutils.Vector(tuple(float(int(h[i:i+2], 16)) / 255.0 for i in (0, 2, 4)))
            except Exception as e:
                pass
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
                self.updateTextures()
                    
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
                if self.drawHandler:
                    bpy.types.SpaceView3D.draw_handler_remove(self.drawHandler, 'WINDOW')
                    self.drawHandler = None
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
        self.updateTextures()
        self.active_space = None
        self.active_region = None
        self.prevEngine = None       
        
        self.viewLocation = mathutils.Vector((0, 0, 0))
        self.viewRotation = mathutils.Quaternion((0.71579509973526, 0.4389207363128662, 0.29061830043792725, 0.4588318467140198))
        self.viewDistance = 17.986562728881836   
        self.viewPerspective = 'PERSP'
        self.viewLens = 50.0

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
       
    def updateTextures(self):
        textures = [(i.name, i.filepath) for i in bpy.data.images]
        self.sendMessage(('textures', textures))
            
    def createCamera(self, space, name):
        cam = bpy.data.cameras.new(name)
        cam.type = self.viewPerspective if self.viewMode == 1 else space.region_3d.view_perspective
        if space.region_3d.view_perspective == 'ORTHO':
            cam.ortho_scale = 2.0 / (self.viewLens / 36.0 / self.viewDistance) if self.viewMode == 1 else 2.0 / (space.lens / 36.0 / space.region_3d.view_distance)
            cam.clip_start = space.clip_start
            cam.clip_end = space.clip_end
        else:         
            cam.lens = self.viewLens if self.viewMode == 1 else space.lens
            cam.sensor_width = 2.0 * 36.0
            cam.clip_start = space.clip_start
            cam.clip_end = space.clip_end
        obj = bpy.data.objects.new(name, cam)
        obj.rotation_mode = 'QUATERNION'
        if self.viewMode == 1:
            obj.location = self.viewLocation + (self.viewRotation @ mathutils.Vector((0, 0, self.viewDistance)))
            obj.rotation_quaternion = self.viewRotation
            obj.data['view_distance'] = self.viewDistance

        else:
            obj.location = space.region_3d.view_location + (space.region_3d.view_rotation @ mathutils.Vector((0, 0, space.region_3d.view_distance)))
            obj.rotation_quaternion = space.region_3d.view_rotation
            obj.data['view_distance'] = space.region_3d.view_distance
        region = self.regionX != 0 or self.regionY != 0 or self.regionWidth != self.width or self.regionHeight != self.height 
        obj.data['region'] = region
        if region:
            obj.data['region_x'] = self.regionX
            obj.data['region_y'] = self.regionY
            obj.data['region_width'] = self.regionWidth
            obj.data['region_height'] = self.regionHeight
            obj.data['region_viewport'] = self.regionViewport
        bpy.context.scene.collection.objects.link(obj)
        return obj

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
                    if self.viewMode == 1:
                        self.viewRotation = mathutils.Euler((msg[1], -msg[3], msg[2])).to_quaternion()
                    elif space:
                        space.region_3d.view_rotation = mathutils.Euler((msg[1], -msg[3], msg[2])).to_quaternion()
                    flag = True
                elif type == 'pan':
                    if self.viewMode == 1:
                        self.viewLocation += (mathutils.Quaternion(self.viewRotation) @ mathutils.Vector((-msg[1], msg[2], 0))) * self.viewDistance * 0.25
                    elif space:
                        space.region_3d.view_location += (mathutils.Quaternion(space.region_3d.view_rotation) @ mathutils.Vector((-msg[1], msg[2], 0))) * space.region_3d.view_distance * 0.25
                    flag = True
                elif type == 'zoom':
                    if self.viewMode == 1:
                        self.viewDistance *= math.exp(0.25 * msg[1])
                    elif space:  
                        space.region_3d.view_distance *= math.exp(0.25 * msg[1])
                    flag = True
                elif type == 'lens':
                    if self.viewMode == 1:
                        prevLens = self.viewLens
                        self.viewLens = msg[1]
                        if self.lensZoom:
                            self.viewDistance *= self.viewLens / prevLens
                    elif space:
                        prevLens = space.lens
                        space.lens = msg[1]
                        if self.lensZoom:
                            space.region_3d.view_distance *= space.lens / prevLens
                    flag = True
                elif type == 'lensZoom':
                    self.lensZoom = msg[1]
                elif type == 'ortho':
                    if self.viewMode == 1:
                        self.viewPerspective = 'ORTHO' if msg[1] else 'PERSP'
                    elif space:
                        space.region_3d.view_perspective = 'ORTHO' if msg[1] else 'PERSP'
                    flag = True
                elif type == 'transparency':
                    self.transparency = msg[1]
                elif type == 'gizmos':
                    self.gizmos = msg[1]
                elif type == 'shading':
                    shading = ['WIREFRAME', 'SOLID', 'FLAT_TEXTURE', 'MATERIAL', 'RENDERED'][msg[1]]
                    if space:
                        if shading == 'FLAT_TEXTURE':
                            self.prevShadingColor = space.shading.color_type
                            self.prevShadingLight = space.shading.light
                            space.shading.type = 'SOLID'
                            space.shading.color_type = 'TEXTURE'
                            space.shading.light = 'FLAT'
                        else:
                            if self.prevShading == 2 and self.prevShadingColor:
                                space.shading.color_type = self.prevShadingColor
                                space.shading.light = self.prevShadingLight
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
                    self.cursorX = -1
                    self.cursorY = -1
                    self.cursorSizeX = -1
                    self.cursorSizeY = -1
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
                        self.updateMode = 3
                        try:
                            if self.renderCurrentView and space.region_3d.view_perspective != 'CAMERA':
                                obj = self.createCamera(space, "BlenderLayer_TMP")
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
                    self.updateMode = 3
                        
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
                elif type == 'createCamera':
                    if bpy.data.cameras.get(msg[1]) or bpy.data.objects.get(msg[1]):
                        self.sendMessage(('status', f"Camera '{msg[1]}' already exists!"))
                    else:
                        obj = self.createCamera(space, msg[1])
                        self.sendMessage(('status', f"Created camera '{msg[1]}'"))
                elif type == 'moveToCamera':
                    obj = bpy.data.objects[msg[1]]
                    cam = bpy.data.cameras[msg[1]]

                    dist = obj.data.get('view_distance')
                    if not dist:
                        dist = self.viewDistance if self.viewMode == 1 else space.region_3d.view_distance

                    if self.viewMode == 1:
                        self.viewPerspective = cam.type
                        self.viewDistance = dist
                        self.viewRotation = obj.matrix_world.to_quaternion()
                        self.viewLocation = obj.location - (self.viewRotation @ mathutils.Vector((0, 0, dist)))
                        self.viewLens = 2.0 * 36.0 * dist / cam.ortho_scale if self.viewPerspective == 'ORTHO' else cam.lens
                    else:
                        space.region_3d.view_perspective = cam.type
                        space.region_3d.view_distance = dist
                        space.region_3d.view_rotation = obj.matrix_world.to_quaternion()
                        space.region_3d.view_location = obj.location - (space.region_3d.view_rotation @ mathutils.Vector((0, 0, dist)))
                        space.lens = 2.0 * 36.0 * dist / cam.ortho_scale if self.viewPerspective == 'ORTHO' else cam.lens
                     
                    space.clip_start = space.clip_start
                    space.clip_end = space.clip_end
                    space.region_3d.update()

                    reg = obj.data.get('region')
                    if reg != None:
                        if reg:
                            self.sendMessage(('region', True, obj.data.get('region_x', 0), obj.data.get('region_y', 0), obj.data.get('region_width', self.width), obj.data.get('region_height', self.height), obj.data.get('region_viewport', True)))
                        else:
                            self.sendMessage(('region', False))

                    self.sendMessage(('status', f"Moved to camera '{msg[1]}'"))

                elif type == 'cameraList':
                    self.sendMessage(('cameraList', [obj.name for obj in bpy.data.objects if obj.type == "CAMERA"]))
                elif type == 'uvLayout':
                    bpy.ops.uv.export_layout(filepath=msg[1], export_all=False, modified=False, mode='SVG', size=(msg[2], msg[3]), opacity=0.25, check_existing=False)
                    self.sendMessage(('uvLayout', msg[1]))
                elif type == 'file':
                    bpy.ops.wm.open_mainfile(filepath=msg[1])
                    self.requestDelayedFrame = True
                    break
                elif type == 'cursor':
                    self.cursorX = msg[1]
                    self.cursorY = msg[2]
                    self.cursorSizeX = msg[3]
                    self.cursorSizeY = msg[4]
                    region.tag_redraw()
                elif type == 'uvCursor':
                    self.cursorX = -1
                    self.cursorY = -1
                    self.cursorSizeX = -1
                    self.cursorSizeY = -1
                    region.tag_redraw()
                elif type == 'newTexture':
                    img = bpy.data.images.new(msg[1], msg[2], msg[3], alpha=msg[4], float_buffer=msg[5])
                    img.filepath = msg[6]
                    self.updateTextures()
                elif type == 'updateTexture':
                    t = time.monotonic()
                    img = bpy.data.images.get(msg[1])
                    if not img:
                        img = bpy.data.images.new(msg[1], self.width, self.height, alpha=True)
                        self.updateTextures()
                    if img.size[0] != self.width or img.size[1] != self.height:
                        img.scale(self.width, self.height)
                    img.pixels.foreach_set(self.texBuf)
                    img.update()
                    space.region_3d.view_distance = space.region_3d.view_distance + (-0.00001 if self.refreshHack else 0.00001)
                    self.refreshHack = not self.refreshHack
                    for area in bpy.context.screen.areas:
                        if area.type in ['IMAGE_EDITOR', 'VIEW_3D']:
                            area.tag_redraw()

                    print("Time2:", (time.monotonic() - t) * 1000)
                elif type == 'falloff':
                    bpy.context.tool_settings.image_paint.use_normal_falloff = msg[1]

                elif type == 'falloffAngle':
                    bpy.context.tool_settings.image_paint.normal_angle = msg[1]
                   
                elif type == 'occlude':
                    bpy.context.tool_settings.image_paint.use_occlude = msg[1]
                    
                elif type == 'backface':
                    bpy.context.tool_settings.image_paint.use_backface_culling = msg[1]
                    
                elif type == 'bleed':
                    bpy.context.tool_settings.image_paint.seam_bleed = msg[1]
                                        
                elif type == 'projectTexture':
                    #print(bpy.context.active_object.material_slots[0].material.texture_paint_images[0])
                    t = time.monotonic()
                    if space.region_3d.view_perspective != 'CAMERA':
                        obj = self.createCamera(space, 'BlenderLayer_TMP')
                        self.prevCamera = bpy.context.scene.camera
                        self.tmpCamera = obj
                        bpy.context.scene.camera = self.tmpCamera
                    if bpy.ops.paint.project_image(image=msg[1]) == {'CANCELLED'}:
                        if bpy.context.tool_settings.image_paint.missing_uvs: 
                            self.sendMessage(('status', "Couldn't project image... UV layer missing"))
                        elif bpy.context.tool_settings.image_paint.missing_texture: 
                            self.sendMessage(('status', "Couldn't project image... Texture missing"))
                        elif bpy.context.tool_settings.image_paint.missing_materials: 
                            self.sendMessage(('status', "Couldn't project image... Materials missing"))
                        else:
                            self.sendMessage(('status', "Couldn't project image... Make sure you have set up texture painting in Blender"))

                    if self.tmpCamera:
                        bpy.context.scene.camera = self.prevCamera
                        bpy.data.objects.remove(self.tmpCamera, do_unlink=True)
                    self.tmpCamera = None
                    self.prevCamera = None
                    self.requestDelayedFrame = True
                    print("Time3:", (time.monotonic() - t) * 1000)
                elif type == 'undo':
                    print('UNDO')
                    bpy.ops.ed.undo()
                elif type == 'redo':
                    print('REDO')
                    bpy.ops.ed.redo()
                else:
                    print("[Blender Layer] Received unrecognized message type: ", type)  
                    
                if self.updateMode == 1 or self.updateMode == 2:
                    self.requestFrame = True
                    region.tag_redraw()                        
        except Exception as e:
            print(e)
            self.sendMessage(('status', str(e)))

        if space and space.region_3d:
            if self.viewMode == 1:
                rot = self.viewRotation.to_euler()
                lens = self.viewLens
                ortho = self.viewPerspective == 'ORTHO'

            else:
                rot = mathutils.Quaternion(space.region_3d.view_rotation).to_euler()
                lens = space.lens
                ortho = space.region_3d.view_perspective == 'ORTHO'
            shading = space.shading.type
            if shading == 'SOLID' and space.shading.color_type == 'TEXTURE' and space.shading.light == 'FLAT':
                shading = 'FLAT_TEXTURE'
            shading = ['WIREFRAME', 'SOLID', 'FLAT_TEXTURE', 'MATERIAL', 'RENDERED'].index(shading)
            engine = bpy.context.scene.render.engine
            
            flag1 = False
            if flag:
                if self.viewMode != 1:
                    if space.region_3d.view_perspective == 'CAMERA':
                        space.region_3d.view_perspective = 'PERSP'
                    space.region_3d.update()
            else:
                if self.prevRot != rot:
                    roll = -rot.y
                    self.sendMessage(('rotate', rot.x, rot.z, roll))
                    flag1 = True
                if self.prevLens != lens:
                    self.sendMessage(('lens', lens))
                    flag1 = True
                if self.prevOrtho != ortho:
                    self.sendMessage(('ortho', ortho))
                    flag1 = True
                if self.prevShading != shading:
                    self.sendMessage(('shading', shading))  
                    flag1 = True
                    
            if self.prevEngine != engine:
                self.sendMessage(('engine', engine))
                flag1 = True

            if self.prevFile != bpy.data.filepath:
                self.sendMessage(('file', bpy.data.filepath))
                self.prevFile = bpy.data.filepath
                flag1 = True

            if self.prevShading != shading or self.prevEngine != engine:
                self.freeOffscreen()
                
            if self.isAnimation and not self.isRendering and bpy.context.scene.frame_current != self.animFrame:
                bpy.context.scene.frame_set(self.animFrame)
                
            if self.updateMode == 2 and flag1:
                self.requestFrame = True
                
            self.prevRot = rot
            self.prevLens = lens
            self.prevOrtho = ortho
            self.prevShading = shading
            self.prevEngine = engine
            
            if (self.requestFrame or self.updateMode == 0) and self.viewMode != 4:
                self.ticksWaitingForFrame = self.ticksWaitingForFrame + 1
                if self.ticksWaitingForFrame == 60:
                    self.tagForRedraw()
                elif self.ticksWaitingForFrame == 120:
                    self.sendMessage(('status', "Waiting for on draw event... Make sure Blender is not minimized"))
            
            if self.backgroundDraw and self.viewMode != 4 and (self.requestFrame or self.updateMode == 0 or self.isAnimation and not self.isRendering):
                self.draw(space, region)
                
        return 0.0166
    
    def onDraw(self):
        self.active_space = bpy.context.space_data
        self.active_region = bpy.context.region

        if self.requestDelayedFrame:
            if self.updateMode != 3:
                self.requestFrame = True
            self.requestDelayedFrame = False

        if self.connected and not self.backgroundDraw and self.viewMode != 4:
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
                        type = msg[0]
                        if type == 'updateTexture':
                            t = time.monotonic()
                            x = msg[2]
                            y = msg[3]
                            w = msg[4]
                            h = msg[5]
                            s = msg[6]
                            buf = msg[7] if len(msg) > 7 else self.shm.buf[:s]
                            b = np.frombuffer(buf, dtype=self.dtype) 
                            if self.bgrConversion:
                                b = b.reshape(h, w, 4)[::-1,:,[2, 1, 0, 3]]
                            else:
                                b = b.reshape(h, w, 4)[::-1,:,[0, 1, 2, 3]]
                            b = b.astype(np.float32)
                            if self.dtype == np.uint8:
                                b = b / 255.0
                            elif self.dtype == np.uint16:
                                b = b / 65535.0
                            if w != self.width or h != self.height:
                                b = np.pad(b, [(self.height - h - y, y), (x, self.width - w - x), (0, 0)])
                            print("shape:", b.shape, x, y, w, h, self.width, self.height)
                            b = b.ravel()
                            self.texBuf = b
                            print("Time1:", (time.monotonic() - t) * 1000)
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
        if (self.viewMode <= 1 or self.viewMode >= 4) and not space.region_3d.view_perspective == 'CAMERA' or (self.viewMode == 3 and self.renderCurrentView) or not context.scene.camera:
            #vm = space.region_3d.view_matrix
            #pm = space.region_3d.window_matrix.copy()

            if self.viewMode == 1:
                vm = (mathutils.Matrix.Translation(mathutils.Vector(self.viewLocation)) @ mathutils.Quaternion(self.viewRotation).to_matrix().to_4x4() @ mathutils.Matrix.Translation((0, 0, self.viewDistance))).inverted()        
                dist = self.viewDistance
                ortho = self.viewPerspective == 'ORTHO'
                size = self.viewLens / 36.0 / dist if ortho else self.viewLens / 36.0

            else:
                vm = (mathutils.Matrix.Translation(mathutils.Vector(space.region_3d.view_location)) @ mathutils.Quaternion(space.region_3d.view_rotation).to_matrix().to_4x4() @ mathutils.Matrix.Translation((0, 0, space.region_3d.view_distance))).inverted()        
                dist = space.region_3d.view_distance
                ortho = space.region_3d.view_perspective == 'ORTHO'
                size = space.lens / 36.0 / dist if ortho else space.lens / 36.0
            
            near = space.clip_start
            far = space.clip_end    
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
            if ortho:
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
            pm = context.scene.camera.calc_matrix_camera(context.view_layer.depsgraph, x=self.regionWidth, y=self.regionHeight)
            
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
            preferences = context.preferences
            if not __name__ == '__main__':
                prefs = preferences.addons[__name__].preferences 
                prefs.host = self.host
                prefs.port = self.port
                TIMEOUT = prefs.timeout

            succeeded = client.connect(self.host, self.port)
            if succeeded:
                self.report({'INFO'}, "Connected")
            else:
                self.report({'ERROR'}, "Failed to connect")
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        preferences = context.preferences
        
        if not __name__ == '__main__':
            prefs = preferences.addons[__name__].preferences  
            self.host = prefs.host
            self.port = prefs.port

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

class CursorWidget(bpy.types.Gizmo):
    bl_idname = 'VIEW3D_GT_blender_layer_cursor'
    bl_target_properties = ()

    __slots__ = (
        'valid',
    )

    def tris_barycentric(self, a, b, c, x, y):
        v0 = b - a
        v1 = c - a
        v2 =  mathutils.Vector((x, y)) - a
        det = v0.x * v1.y - v1.x * v0.y
        if det == 0:
            return [-1.0, -1.0, -1.0]
        v = (v2.x * v1.y - v1.x * v2.y) / det
        w = (v0.x * v2.y - v2.x * v0.y) / det
        u = 1.0 - v - w
        return [u, v, w]

    def update_offset_matrix(self, context):
        global client  

        u = client.cursorX
        v = client.cursorY
        
        self.valid = False 
        if u < 0 or u > 1.0 or v < 0 or v > 1.0:
            return
        
        ob = context.object
        me = ob.data
        uvs = me.uv_layers.active.data
        vertices = ob.data.vertices
        for pol in me.polygons:
            uvcoords = [uvs[i].uv for i in pol.loop_indices]
            coords = self.tris_barycentric(uvcoords[0], uvcoords[1], uvcoords[2], u, v)
            if coords[0] < 0 or coords[1] < 0 or coords[2] < 0:
                if len(uvcoords) == 4:
                    coords = self.tris_barycentric(uvcoords[0], uvcoords[2], uvcoords[3], u, v)
                    if coords[0] < 0 or coords[1] < 0 or coords[2] < 0:
                        continue
                    else:
                        verts = [vertices[i].co for i in [pol.vertices[0], pol.vertices[2], pol.vertices[3]]]  
                        coords2 = self.tris_barycentric(uvcoords[0], uvcoords[2], uvcoords[3], u + client.cursorSizeX, v)
                        coords3 = self.tris_barycentric(uvcoords[0], uvcoords[2], uvcoords[3], u, v + client.cursorSizeY)
                else:
                    continue
            else:
                verts = [vertices[i].co for i in pol.vertices]
                coords2 = self.tris_barycentric(uvcoords[0], uvcoords[1], uvcoords[2], u + client.cursorSizeX, v)
                coords3 = self.tris_barycentric(uvcoords[0], uvcoords[1], uvcoords[2], u, v + client.cursorSizeY)

            vert = verts[0] * coords[0] + verts[1] * coords[1] + verts[2] * coords[2]
            vec1 = verts[0] * coords2[0] + verts[1] * coords2[1] + verts[2] * coords2[2] - vert
            vec2 = verts[0] * coords3[0] + verts[1] * coords3[1] + verts[2] * coords3[2] - vert

            normal = pol.normal.to_4d()
            normal.w = 0.0
            normal = (self.matrix_basis @ normal).to_3d()
            normal.normalize()
            camNormal = mathutils.Quaternion(context.space_data.region_3d.view_rotation) @ mathutils.Vector((0, 0, -1))
            camNormal.normalize()
            mat = mathutils.Matrix()
            mat[0][0], mat[1][0], mat[2][0] = vec1 * 0.25
            mat[0][1], mat[1][1], mat[2][1] = vec2 * 0.25
            mat[0][2], mat[1][2], mat[2][2] = pol.normal * 0.25
            mat[0][3], mat[1][3], mat[2][3] = vert
            self.color = client.cursorColor if normal.dot(camNormal) < 0 else client.cursorColor * 0.5
            self.matrix_offset = mat
            self.valid = True    
            break

    def draw(self, context):
        self.update_offset_matrix(context)
        if self.valid:
            self.draw_preset_circle(self.matrix_basis @ self.matrix_offset)

    def draw_select(self, context, select_id):
        self.update_offset_matrix(context)
        if self.valid:
            self.draw_preset_circle(self.matrix_basis @ self.matrix_offset, select_id=select_id)

    def setup(self):
        self.valid = False
        

    def invoke(self, context, event):
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        pass

    def modal(self, context, event, tweak):
        return {'RUNNING_MODAL'}
    
class CursorWidgetGroup(bpy.types.GizmoGroup):
    bl_idname = 'OBJECT_GGT_blender_layer_cursor'
    bl_label = "Krita Cursor"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def setup(self, context):
        ob = context.object
        gz = self.gizmos.new(CursorWidget.bl_idname)
        gz.use_draw_modal = True
        gz.color = 1.0, 1.0, 0.0
        gz.color_highlight = 1.0, 1.0, 0.0
        gz.alpha = 1.0
        gz.alpha_highlight = 1.0
        self.refresh(context)

    def refresh(self, context):
        ob = context.object
        gz = self.gizmos[0]
        gz.matrix_basis = ob.matrix_world


class BlenderLayerPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    host: bpy.props.StringProperty(
        name="Host",
    )
    port: bpy.props.IntProperty(
        name="Port",
    )
    timeout: bpy.props.FloatProperty(
        name="Timeout",
    )
    def draw(self, context):
        layout = self.layout
        layout.label(text="Blender Layer connection settings")
        layout.prop(self, 'host')
        layout.prop(self, 'port')
        layout.prop(self, 'timeout')
        
def register():  
    global client
    if client:
        client.disconnect()
    client = BlenderLayerClient()  
    
    bpy.utils.register_class(BlenderLayerPreferences)
    bpy.utils.register_class(CursorWidget)
    bpy.utils.register_class(CursorWidgetGroup)
    bpy.utils.register_class(ConnectOperator)
    bpy.types.VIEW3D_MT_view.append(menu_func)

    if CONNECT:
        client.connect(HOST, PORT)
      
def unregister():  
    global client  
    bpy.utils.unregister_class(BlenderLayerPreferences)
    bpy.utils.unregister_class(CursorWidget)
    bpy.utils.unregister_class(CursorWidgetGroup)
    bpy.utils.unregister_class(ConnectOperator)
    if client:
        client.disconnect()
  
if __name__ == '__main__':
    if 'blenderLayerClient' in bpy.context.preferences.addons.keys():
        print("[Blender Layer] Plugin is already registered")
    else:
        register()
        atexit.register(lambda: client.disconnect(True))    