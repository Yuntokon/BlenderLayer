import sys, math, threading, os
from krita import *

from PyQt5.QtCore import Qt, QThreadPool, QPoint
from os import path
from functools import partial
from types import SimpleNamespace
from PyQt5.QtWidgets import (
    QPushButton,
    QStatusBar,
    QLabel,
    QLineEdit,
    QHBoxLayout,
    QVBoxLayout,
    QGroupBox,
    QWidget,
    QSpinBox,
    QFrame,
    QScrollArea
)
from .navigateWidget import NavigateWidget
from .blenderLayerServer import BlenderLayerServer, BlenderRunnable

instance = Krita.instance()
    
class BlenderLayer(DockWidget):

    def __init__(self):
        super().__init__()
        
        instance.notifier().windowCreated.connect(self.createActions)

        self.settings = SimpleNamespace()
        self.settings.transparency = True
        self.settings.gizmos = False
        self.settings.scale = 0
        self.settings.framerateScale = 0
        self.settings.region = False
        self.settings.regionViewport = True
        self.settings.renderCurrentView = False
        self.settings.lensZoom = True
        self.settings.engine = ''
        self.settings.shading = 1
        self.settings.textures = []
        self.settings.documentTextureMap = {}

        self.readSettings()
        self.createdActions = False
        self.lastStatus = None
        self.blenderArgs = None
        self.currentPort = -1
        self.blenderRunning = False
        self.connected = False
        self.server = None
        self.activeInFile = None
        self.activeDocument = None
        self.blockServerSignal = False
        self.setWindowTitle(i18n("Blender Layer Dev"))

        scrollContainer = QWidget()
        scroll = QScrollArea()
        scroll.setWidget(scrollContainer)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFrameShadow(QFrame.Plain)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        updateProgress = QProgressBar()
        updateProgress.hide()
        
        settingsHBoxLayout = QHBoxLayout()
        settingsHBoxLayout.addStretch()
        settingsButton = QPushButton()
        settingsButton.setIcon(instance.icon('configure-thicker'))
        settingsButton.setToolTip(i18n("Settings"))
        settingsHBoxLayout.addWidget(settingsButton)
        
        connectionGroupBox = QGroupBox()
        connectionVBoxLayout = QVBoxLayout()
        connectionHBoxLayout = QHBoxLayout()
        startstopButton = QPushButton(i18n("Start Server"))
        startstopButton.setToolTip(i18n("Start/Stop the server.\nYou'll have to connect Blender manually via the companion plugin\n(In Blender View → Connect to Krita)"))
        startBlenderButton = QPushButton(i18n("Start Blender"))
        startBlenderButton.setToolTip(i18n("Start Blender and connect automagically"))
        statusBar = QLabel()
        statusBar.setWordWrap(True)

        connectionHBoxLayout.addWidget(startBlenderButton)
        connectionHBoxLayout.addWidget(startstopButton)
        connectionVBoxLayout.addLayout(connectionHBoxLayout)
        connectionVBoxLayout.addWidget(statusBar)
        connectionVBoxLayout.addWidget(updateProgress)
        connectionGroupBox.setLayout(connectionVBoxLayout)

        viewHBoxLayout = QHBoxLayout()
        viewLabel = QLabel(i18n("Mode")) 
        viewComboBox = QComboBox()
        viewComboBox.addItems([i18n("Current view"), i18n("Independent view"), i18n("Camera"), i18n("Render result"), i18n("UV painting"), i18n("Projection painting")])
        viewComboBox.setItemData(0, i18n("Show view as seen in the active 3D View"), QtCore.Qt.ToolTipRole)
        viewComboBox.setItemData(1, i18n("Show view from an independent angle"), QtCore.Qt.ToolTipRole)
        viewComboBox.setItemData(2, i18n("Show view from the active camera"), QtCore.Qt.ToolTipRole)
        viewComboBox.setItemData(3, i18n("Render and show result"), QtCore.Qt.ToolTipRole)
        viewComboBox.setItemData(4, i18n("Live paint on a texture"), QtCore.Qt.ToolTipRole)
        viewComboBox.setItemData(5, i18n("Live paint on a 3D Model"), QtCore.Qt.ToolTipRole)
        viewComboBox.setToolTip(i18n("Select view mode"))

        viewHBoxLayout.addWidget(viewLabel)
        viewHBoxLayout.addWidget(viewComboBox)
        
        renderGroupBox = QGroupBox()
        renderVBoxLayout = QVBoxLayout()
        
        renderCurrentViewCheck = QCheckBox(i18n("Render from current view"))
        renderCurrentViewCheck.setToolTip(i18n("When disabled, the active camera will be used"))
        renderOverrideCheck = QCheckBox(i18n("Override render settings"))
        renderOverrideCheck.setChecked(True)
        renderOverrideCheck.setToolTip(i18n("Override some of the settings in the .blend file"))

        renderOverrideVBoxLayout = QVBoxLayout()

        renderPathCheck = QCheckBox(i18n("Override path"))
        renderPathCheck.setChecked(True)
        renderPathCheck.setToolTip(i18n("Use the path specified in settings"))
        renderResCheck = QCheckBox(i18n("Override resolution"))
        renderResCheck.setChecked(True)
        renderResCheck.setToolTip(i18n("Adjust output size to the current document"))
        renderTransparencyCheck = QCheckBox(i18n("Transparent background"))
        renderTransparencyCheck.setChecked(True)
        renderTransparencyCheck.setToolTip(i18n("Render with transparency"))
        renderTemporaryCheck = QCheckBox(i18n("Only apply temporarily"))
        renderTemporaryCheck.setChecked(True)
        renderTemporaryCheck.setToolTip(i18n("Settings will be reverted once the render is done"))
        
        renderOverrideVBoxLayout.addWidget(renderPathCheck)
        renderOverrideVBoxLayout.addWidget(renderResCheck)
        renderOverrideVBoxLayout.addWidget(renderTransparencyCheck)
        renderOverrideVBoxLayout.addWidget(renderTemporaryCheck)

        line0 = QFrame()
        line0.setFrameShape(QFrame.HLine)
        line0.setFrameShadow(QFrame.Sunken)
        
        renderHBoxLayout = QHBoxLayout()
        renderButton = QPushButton(i18n("Render"))
        renderButton.setToolTip(i18n("Start a render"))
        renderAnimationButton = QPushButton(i18n("Render Animation"))
        renderAnimationButton.setToolTip(i18n("Render mulitple frames and import them as an animation"))

        renderHBoxLayout.addWidget(renderButton)
        renderHBoxLayout.addWidget(renderAnimationButton)
        
        renderVBoxLayout.addWidget(renderCurrentViewCheck)
        renderVBoxLayout.addWidget(renderOverrideCheck)
        renderVBoxLayout.addLayout(renderOverrideVBoxLayout)
        renderVBoxLayout.addWidget(line0)
        renderVBoxLayout.addLayout(renderHBoxLayout)
        renderGroupBox.setLayout(renderVBoxLayout)
        
        projGroupBox = QGroupBox()
        projVBoxLayout = QVBoxLayout()
        
        undoCheck = QCheckBox(i18n("Capture and forward undo"))
        undoCheck.setToolTip(i18n("When pressing undo inside of Krita, undo in Blender instead"))
        undoCheck.setChecked(True)

        falloffCheck = QCheckBox(i18n("Normal falloff"))
        falloffCheck.setToolTip(i18n("Paint most on faces pointing towards the view according to this angle"))
        falloffCheck.setChecked(True)
        
        falloffAngleLayout = QHBoxLayout()

        angleLabel = QLabel(i18n("Angle")) 
        angleSlider = QSlider(Qt.Horizontal)
        angleSlider.setRange(0, 90)
        angleSlider.setValue(80)
        angleSpinBox = QSpinBox()
        angleSpinBox.setRange(0, 90)
        angleSpinBox.setValue(80)
        angleSpinBox.setSuffix(i18n("°"))
        angleSlider.valueChanged.connect(angleSpinBox.setValue)
        angleSpinBox.valueChanged.connect(angleSlider.setValue)
        
        falloffAngleLayout.addWidget(angleLabel)
        falloffAngleLayout.addWidget(angleSlider)
        falloffAngleLayout.addWidget(angleSpinBox)

        occludeCheck = QCheckBox(i18n("Occlude"))
        occludeCheck.setToolTip(i18n("Only paint onto the faces directly under the brush"))
        occludeCheck.setChecked(True)
        
        backFaceCheck = QCheckBox(i18n("Backface culling"))
        backFaceCheck.setToolTip(i18n("Ignore faces pointing away from the viewer"))
        backFaceCheck.setChecked(True)
        
        bleedLayout = QHBoxLayout()

        bleedLabel = QLabel(i18n("Bleed")) 
        bleedSlider = QSlider(Qt.Horizontal)
        bleedSlider.setRange(0, 8)
        bleedSlider.setValue(2)
        bleedSpinBox = QSpinBox()
        bleedSpinBox.setRange(0, 8)
        bleedSpinBox.setValue(2)
        bleedSpinBox.setSuffix(i18n("px"))
        bleedSlider.valueChanged.connect(bleedSpinBox.setValue)
        bleedSpinBox.valueChanged.connect(bleedSlider.setValue)
        
        bleedLayout.addWidget(bleedLabel)
        bleedLayout.addWidget(bleedSlider)
        bleedLayout.addWidget(bleedSpinBox)
        
        projVBoxLayout.addWidget(undoCheck)
        projVBoxLayout.addWidget(falloffCheck)
        projVBoxLayout.addLayout(falloffAngleLayout)
        projVBoxLayout.addWidget(occludeCheck)
        projVBoxLayout.addWidget(backFaceCheck)
        projVBoxLayout.addLayout(bleedLayout)

        projGroupBox.setLayout(projVBoxLayout)

        uvGroupBox = QGroupBox()
        uvVBoxLayout = QVBoxLayout()
        
        textureLabel = QLabel(i18n("Texture")) 
        textureComboBox = QComboBox()
        textureComboBox.addItems([i18n("<None>"), i18n("<New>")])
        textureComboBox.setMinimumWidth(100)
        textureComboBox.setToolTip(i18n("The Blender texture to edit.\nThis can be set per document"))

        textureHBoxLayout = QHBoxLayout()
        textureHBoxLayout.addWidget(textureLabel)
        textureHBoxLayout.addWidget(textureComboBox)
        
        cursorCheck = QCheckBox(i18n("Show cursor on model (Experimental)"))
        cursorCheck.setChecked(True)
        cursorCheck.setToolTip(i18n("Shows the approximate location of Krita's cursor on the model in Blender"))

        lineUV = QFrame()
        lineUV.setFrameShape(QFrame.HLine)
        lineUV.setFrameShadow(QFrame.Sunken)
        
        uvLayoutButton = QPushButton(i18n("Import UV layout"))

        uvVBoxLayout.addLayout(textureHBoxLayout)
        uvVBoxLayout.addWidget(cursorCheck)
        uvVBoxLayout.addWidget(lineUV)
        uvVBoxLayout.addWidget(uvLayoutButton)

        uvGroupBox.setLayout(uvVBoxLayout)

        viewGroupBox = QGroupBox(i18n("View"))
        viewVBoxLayout = QVBoxLayout()
        currentViewVBoxLayout = QVBoxLayout()
        navigateWidget = NavigateWidget()
        viewGrid = QGridLayout()

        rollLabel = QLabel(i18n("Roll")) 
        rollSlider = QSlider(Qt.Horizontal)
        rollSlider.setRange(-1800, 1800)
        rollSpinBox = QDoubleSpinBox()
        rollSpinBox.setRange(-180, 180)
        rollSpinBox.setSuffix(i18n("°"))
        rollSlider.valueChanged.connect(partial(self.changeSpinBox,rollSpinBox))
        rollSpinBox.valueChanged.connect(partial(self.changeSlider,rollSlider))
        
        lensLabel = QLabel(i18n("Focal Length")) 
        lensSlider = QSlider(Qt.Horizontal)
        lensSlider.setRange(10, 2500)
        lensSlider.setValue(500)
        lensSpinBox = QDoubleSpinBox()
        lensSpinBox.setRange(1, 250)
        lensSpinBox.setSuffix(i18n(" mm"))
        lensSpinBox.setValue(50)
        lensSlider.valueChanged.connect(partial(self.changeSpinBox,lensSpinBox))
        lensSpinBox.valueChanged.connect(partial(self.changeSlider,lensSlider))

        viewGrid.addWidget(rollLabel, 0, 0)
        viewGrid.addWidget(rollSlider, 0, 1)
        viewGrid.addWidget(rollSpinBox, 0, 2)

        viewGrid.addWidget(lensLabel, 1, 0)
        viewGrid.addWidget(lensSlider, 1, 1)
        viewGrid.addWidget(lensSpinBox, 1, 2)

        lensZoomCheck = QCheckBox(i18n("Adjust zoom to focal length"))
        lensZoomCheck.setChecked(True)
        lensZoomCheck.setToolTip(i18n("Adjust camera zoom such that when changing the focal length\nan object in the center approximately stays the same size"))

        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setFrameShadow(QFrame.Sunken)
    
        cyclesWarning = QLabel('<i>'+i18n("Cycles is only supported in render result mode") + '</i>')
        cyclesWarning.setWordWrap(True)
        cyclesWarning.hide()
        
        transparentCheck = QCheckBox(i18n("Transparent background"))
        transparentCheck.setChecked(True)
        transparentCheck.setToolTip(i18n("Use transparency.\nSupported starting with Blender 3.6.0"))

        gizmoCheck = QCheckBox(i18n("Show gizmos"))
        gizmoCheck.setToolTip(i18n("Whether to show gizmos.\nDepends on the settings of the active 3D View"))

        shadingComboBox = QComboBox()
        shadingComboBox.addItems([i18n("Wireframe"), i18n("Solid"), i18n("Flat Texture"), i18n("Material Preview"), i18n("Rendered")])
        shadingComboBox.setCurrentIndex(1)

        viewFormLayout = QFormLayout()
        viewFormLayout.addRow(i18n("Viewport shadig:"), shadingComboBox)
        viewFormLayout.addRow(transparentCheck)
        viewFormLayout.addRow(gizmoCheck)
        
        manualWarning = QLabel('<i>' + i18n("Currently in manual mode, changes will become visible after pressing the update button") + '</i>')
        manualWarning.setWordWrap(True)
        manualWarning.hide()
        
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setFrameShadow(QFrame.Sunken)
             
        createCameraButton = QPushButton(i18n("Create Camera"))
        createCameraButton.setToolTip(i18n("Create a new Blender camera from the current view"))

        moveToCameraButton = QPushButton(i18n("Move to Camera"))
        moveToCameraButton.setToolTip(i18n("Set current view to a Blender camera"))

        cameraHBoxLayout = QHBoxLayout()
        cameraHBoxLayout.addWidget(createCameraButton)
        cameraHBoxLayout.addWidget(moveToCameraButton)

        assistantsButton = QPushButton(i18n("Create Assistant Set"))
        assistantsButton.setToolTip(i18n("Create drawing assistants matching the current view.\nThis will create an xml file which has to be loaded from the tool settings of the assistants tool.\n(Tool Settings → Load Assistant Set Button)"))

        currentViewVBoxLayout.addWidget(navigateWidget)
        currentViewVBoxLayout.addLayout(viewGrid)
        currentViewVBoxLayout.addWidget(lensZoomCheck)
        currentViewVBoxLayout.addWidget(line1)
        viewVBoxLayout.addLayout(currentViewVBoxLayout)
        viewVBoxLayout.addWidget(cyclesWarning)
        viewVBoxLayout.addLayout(viewFormLayout)
        viewVBoxLayout.addWidget(manualWarning)
        viewVBoxLayout.addWidget(line2)
        viewVBoxLayout.addLayout(cameraHBoxLayout)
        viewVBoxLayout.addWidget(assistantsButton)
        viewGroupBox.setLayout(viewVBoxLayout)

        texUpdateHBoxLayout = QHBoxLayout()
        texUpdateLabel = QLabel(i18n("Texture update mode")) 
        texUpdateComboBox = QComboBox()
        texUpdateComboBox.addItems([i18n("Auto"), i18n("Manual")])
        texUpdateComboBox.setCurrentIndex(0)
        texUpdateComboBox.setItemData(0, i18n("Update after each stroke"), QtCore.Qt.ToolTipRole)
        texUpdateComboBox.setItemData(1, i18n("Only update when the update button is pressed\n(Recommended for large resolutions)"), QtCore.Qt.ToolTipRole)
        texUpdateComboBox.setToolTip(i18n("Select when to update the texture"))

        texUpdateHBoxLayout.addWidget(texUpdateLabel)
        texUpdateHBoxLayout.addWidget(texUpdateComboBox)

        texUpdateGroupBox = QGroupBox()
        texUpdateVBoxLayout = QVBoxLayout()
        
        texUpdateButton = QPushButton(i18n("Update"))  

        texUpdateVBoxLayout.addWidget(texUpdateButton)
        texUpdateGroupBox.setLayout(texUpdateVBoxLayout)

        updateHBoxLayout = QHBoxLayout()
        updateLabel = QLabel(i18n("Update mode")) 
        updateComboBox = QComboBox()
        updateComboBox.addItems([i18n("Live"), i18n("Auto"), i18n("Auto (Update from Blender)"), i18n("Manual")])
        updateComboBox.setCurrentIndex(1)
        updateComboBox.setItemData(0, i18n("Periodically update even when Krita is not in focus"), QtCore.Qt.ToolTipRole)
        updateComboBox.setItemData(1, i18n("Only update when settings change in Krita or Krita regains focus\n(Recommended)"), QtCore.Qt.ToolTipRole)
        updateComboBox.setItemData(2, i18n("Only update when settings change in Blender or in Krita\n"), QtCore.Qt.ToolTipRole)
        updateComboBox.setItemData(3, i18n("Only update when the update button is pressed\n(Recommended for large resolutions)"), QtCore.Qt.ToolTipRole)
        updateComboBox.setToolTip(i18n("Select when to update the view"))
       
        updateHBoxLayout.addWidget(updateLabel)
        updateHBoxLayout.addWidget(updateComboBox)

        updateGroupBox = QGroupBox()
        updateVBoxLayout = QVBoxLayout()
        
        updateForm = QFormLayout()
        updateRateLabel = QLabel(i18n("Update")) 
        updateRateComboBox = QComboBox()
        updateRateComboBox.addItems([i18n("Every frame"), i18n("Every 4th frame"), i18n("Every 16th frame"), i18n("Every 64th frame")])
        
        updateResLabel = QLabel(i18n("Resolution")) 
        updateResComboBox = QComboBox()
        updateResComboBox.addItems([i18n("Full"), i18n("Half"), i18n("Quarter"), i18n("Eighth")])
        
        #updateForm.addRow(updateRateLabel, updateRateComboBox)
        updateForm.addRow(updateResLabel, updateResComboBox)

        line3 = QFrame()
        line3.setFrameShape(QFrame.HLine)
        line3.setFrameShadow(QFrame.Sunken)
        
        updateButtonsHBoxLayout = QHBoxLayout()
        updateButton = QPushButton(i18n("Update"))  
        updateButton.setToolTip(i18n("Update frame"))
        updateAnimButton = QPushButton(i18n("Update Animation"))
        updateAnimButton.setToolTip(i18n("Update multiple frames and import them as an animation"))

        updateButtonsHBoxLayout.addWidget(updateButton)
        updateButtonsHBoxLayout.addWidget(updateAnimButton)

        updateVBoxLayout.addLayout(updateForm)
        updateVBoxLayout.addWidget(line3)
        updateVBoxLayout.addLayout(updateButtonsHBoxLayout)
        updateGroupBox.setLayout(updateVBoxLayout)

        regionCheck = QCheckBox(i18n("Limit image region"))
        regionCheck.setToolTip(i18n("Limit the frame to a sub-region of the image"))
        regionGroupBox = QGroupBox(i18n("Image Region"))
        regionGroupBox.hide()
        regionVBoxLayout = QVBoxLayout()
        
        regionXSpinBox = QSpinBox()
        regionXSpinBox.setSuffix(i18n(" px"))
        regionYSpinBox = QSpinBox()
        regionYSpinBox.setSuffix(i18n(" px"))
        regionWidthSpinBox = QSpinBox()
        regionWidthSpinBox.setSuffix(i18n(" px"))
        regionHeightSpinBox = QSpinBox()
        regionHeightSpinBox.setSuffix(i18n(" px"))
        
        regionFormLayout = QFormLayout()
        regionFormLayout.addRow(i18n("X:"), regionXSpinBox)
        regionFormLayout.addRow(i18n("Y:"), regionYSpinBox)
        regionFormLayout.addRow(i18n("width:"), regionWidthSpinBox)
        regionFormLayout.addRow(i18n("height:"), regionHeightSpinBox)

        regionViewportCheck = QCheckBox(i18n("Fixed Viewport"))
        regionViewportCheck.setChecked(True)
        regionViewportCheck.setToolTip(i18n("Crop the frame instead of adjusting the viewport"))

        regionSelectionButton = QPushButton()
        regionSelectionButton.setIcon(instance.icon('tool_rect_selection'))
        regionSelectionButton.setToolTip(i18n("Set to current selection"))

        regionHBoxLayout = QHBoxLayout()
        regionHBoxLayout.addWidget(regionViewportCheck)
        regionHBoxLayout.addStretch()
        regionHBoxLayout.addWidget(regionSelectionButton)
                
        regionVBoxLayout.addLayout(regionFormLayout)
        regionVBoxLayout.addLayout(regionHBoxLayout)
        regionGroupBox.setLayout(regionVBoxLayout)

        libraryGroupBox = QGroupBox(i18n("Library"))
        libraryVBoxLayout = QVBoxLayout()
        libraryVBoxLayout.setContentsMargins(0, 0, 0, 0)

        libraryFormLayout = QFormLayout()
        libraryFormLayout.setContentsMargins(11, 11, 11, 11)
        
        libraryComboBox = QComboBox()
        libraryComboBox.addItems([i18n("<None>")])
        libraryComboBox.setMinimumWidth(100)

        libraryAppendButton = QToolButton()
        libraryAppendButton.setIcon(instance.icon('addlayer'))
        libraryAppendButton.setToolTip(i18n("Add object to the current scene"))
        
        libraryHBoxLayout = QHBoxLayout()
        libraryHBoxLayout.addWidget(libraryComboBox)
        libraryHBoxLayout.addWidget(libraryAppendButton)

        line4 = QFrame()
        line4.setFrameShape(QFrame.HLine)
        line4.setFrameShadow(QFrame.Sunken)
        
        poseLabel = QLabel(i18n("Apply to:"))
        
        poseComboBox = QComboBox()
        poseComboBox.addItems([i18n("<None>")])
        poseComboBox.setMinimumWidth(100)
        poseComboBox.setToolTip(i18n("The armature which the pose will be applied to"))

        poseList = QListWidget()
        poseList.setFlow(QListWidget.LeftToRight)
        poseList.setHorizontalScrollMode(QListWidget.ScrollPerPixel)
        poseList.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        poseList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        poseList.setMinimumHeight(190)
        poseList.setMaximumHeight(190)
        poseList.setSpacing(0)
        poseList.setSelectionMode(QAbstractItemView.NoSelection)
        poseList.setToolTip(i18n("Pose library assets.\nDouble click to apply"))

        libraryFormLayout.addRow(i18n("Add to scene:"), libraryHBoxLayout)
        #libraryFormLayout.addRow(line4)
        #libraryFormLayout.addRow(poseLabel, poseComboBox)
        libraryVBoxLayout.addLayout(libraryFormLayout)
        libraryVBoxLayout.addWidget(poseList)
        libraryGroupBox.setLayout(libraryVBoxLayout)
               
        vboxlayout = QVBoxLayout()
        vboxlayout.addWidget(connectionGroupBox)
        vboxlayout.addLayout(viewHBoxLayout)
        vboxlayout.addWidget(renderGroupBox)
        vboxlayout.addWidget(projGroupBox)
        vboxlayout.addWidget(uvGroupBox)
        vboxlayout.addWidget(viewGroupBox)
        vboxlayout.addLayout(texUpdateHBoxLayout)
        vboxlayout.addWidget(texUpdateGroupBox)
        vboxlayout.addLayout(updateHBoxLayout)
        vboxlayout.addWidget(updateGroupBox)
        vboxlayout.addWidget(regionCheck)
        vboxlayout.addWidget(regionGroupBox)
        vboxlayout.addWidget(libraryGroupBox)
        vboxlayout.addStretch(1)
        vboxlayout.addLayout(settingsHBoxLayout)
        scrollContainer.setLayout(vboxlayout)
        self.setWidget(scroll)
        
        self.progress = updateProgress
        self.settingsButton = settingsButton
        self.startstop = startstopButton
        self.startBlenderButton = startBlenderButton
        self.statusBar = statusBar
        self.projGroup = projGroupBox
        self.uvGroup = uvGroupBox
        self.renderGroup = renderGroupBox
        self.renderOverride = renderOverrideCheck
        self.renderOverridePath = renderPathCheck
        self.renderOverrideRes = renderResCheck
        self.renderTransparency = renderTransparencyCheck
        self.renderTemporary = renderTemporaryCheck
        self.renderButtonLayout = renderHBoxLayout
        self.view = viewComboBox
        self.viewGroup = viewGroupBox
        self.currentViewLayout = currentViewVBoxLayout
        self.cameraLayout = cameraHBoxLayout
        self.moveToCamera = moveToCameraButton
        self.navigate = navigateWidget
        self.roll = rollSpinBox
        self.lens = lensSpinBox
        self.transparentCheck = transparentCheck
        self.shading = shadingComboBox
        self.cyclesWarning = cyclesWarning
        self.manualWarning = manualWarning
        self.texUpdateLayout = texUpdateHBoxLayout
        self.texUpdateGroup = texUpdateGroupBox
        self.textures = textureComboBox
        self.update = updateComboBox
        self.updateLayout = updateHBoxLayout
        self.updateGroup = updateGroupBox
        self.updateRate = updateRateComboBox
        self.updateRateLabel = updateRateLabel
        self.updateRes = updateResComboBox
        self.updateResLabel = updateResLabel
        self.updateForm = updateForm
        self.updateSeperator = line3
        self.updateButtonLayout = updateButtonsHBoxLayout
        self.textureUpdate = texUpdateButton
        self.regionGroup = regionGroupBox
        self.region = regionCheck
        self.regionX = regionXSpinBox
        self.regionY = regionYSpinBox
        self.regionWidth  = regionWidthSpinBox
        self.regionHeight = regionHeightSpinBox
        self.regionViewport = regionViewportCheck
        self.libraryGroup = libraryGroupBox
        self.libraryForm = libraryFormLayout
        self.libraryObject = libraryComboBox
        self.libraryAppend = libraryAppendButton
        self.librarySeperator = line4
        self.poseArmaturesLabel = poseLabel
        self.poseArmatures = poseComboBox
        self.poseList = poseList

        settingsButton.clicked.connect(self.showSettings)    
        startstopButton.clicked.connect(self.startStopServer)    
        startBlenderButton.clicked.connect(self.startBlender)
        assistantsButton.clicked.connect(self.createAssistants)
        createCameraButton.clicked.connect(self.requestCreateCamera)
        moveToCameraButton.clicked.connect(lambda: self.server.sendMessage(('cameraList', 'moveToCamera')))
        regionSelectionButton.clicked.connect(self.regionFromSelection)
        updateButton.clicked.connect(self.updateFrame)
        updateAnimButton.clicked.connect(self.updateAnimation)
        texUpdateButton.clicked.connect(self.updateTexture)

        renderButton.clicked.connect(self.render)
        renderAnimationButton.clicked.connect(partial(self.updateAnimation, True))

        renderOverrideCheck.toggled.connect(partial(self.setLayoutVisible, renderOverrideVBoxLayout))
        renderCurrentViewCheck.toggled.connect(partial(self.setSettingsAndSend, 'renderCurrentView'))

        cursorCheck.toggled.connect(partial(self.setSettingsAndSend, 'uvCursor'))
        uvLayoutButton.clicked.connect(self.requestUVLayout)

        falloffCheck.toggled.connect(lambda b: self.sendBlockableMessage(('falloff', b)))
        falloffCheck.toggled.connect(partial(self.setLayoutVisible, falloffAngleLayout))
        angleSpinBox.valueChanged.connect(lambda v: self.sendBlockableMessage(('falloffAngle', v)))
        occludeCheck.toggled.connect(lambda b: self.sendBlockableMessage(('occlude', b)))
        backFaceCheck.toggled.connect(lambda b: self.sendBlockableMessage(('backface', b)))
        bleedSpinBox.valueChanged.connect(lambda v: self.sendBlockableMessage(('bleed', v)))

        textureComboBox.currentIndexChanged.connect(self.textureChanged)

        poseList.itemDoubleClicked.connect(self.applyPose)
        poseList.horizontalScrollBar().valueChanged.connect(self.requestPosePreviews)
        libraryAppendButton.clicked.connect(self.appendFromLibrary)
        
        viewComboBox.currentIndexChanged.connect(self.viewModeChanged)
        updateComboBox.currentIndexChanged.connect(self.updateModeChanged)
        regionCheck.toggled.connect(regionGroupBox.setVisible)
        regionCheck.toggled.connect(self.resetRegion)

        navigateWidget.rotateSignal.connect(lambda p: self.sendBlockableMessage(('rotate', p.x(), p.y(), float(rollSpinBox.value() / 180 * math.pi))))
        navigateWidget.panSignal.connect(lambda p: self.sendBlockableMessage(('pan', p.x(), p.y())))
        navigateWidget.zoomSignal.connect(lambda f: self.sendBlockableMessage(('zoom', f)))
        navigateWidget.orthoSignal.connect(lambda b: self.sendBlockableMessage(('ortho', b)))
        rollSpinBox.valueChanged.connect(lambda v: self.sendBlockableMessage(('rotate', navigateWidget.rotation.x(), navigateWidget.rotation.y(), float(v / 180 * math.pi))))
        lensSpinBox.valueChanged.connect(lambda v: self.sendBlockableMessage(('lens', v)))
        lensZoomCheck.toggled.connect(partial(self.setSettingsAndSend, 'lensZoom'))
        shadingComboBox.currentIndexChanged.connect(lambda v: self.sendBlockableMessage(('shading', v)))
        shadingComboBox.currentIndexChanged.connect(lambda v: self.updateCyclesWarning(self.settings.engine, v))

        transparentCheck.toggled.connect(partial(self.setSettingsAndSend, 'transparency'))
        gizmoCheck.toggled.connect(partial(self.setSettingsAndSend, 'gizmos'))

        updateRateComboBox.currentIndexChanged.connect(partial(self.setSettingsAndSend, 'framerateScale'))
        updateResComboBox.currentIndexChanged.connect(partial(self.setSettingsAndSend, 'scale'))

        regionXSpinBox.valueChanged.connect(self.regionChanged)
        regionYSpinBox.valueChanged.connect(self.regionChanged)
        regionWidthSpinBox.valueChanged.connect(self.regionChanged)
        regionHeightSpinBox.valueChanged.connect(self.regionChanged)
        regionViewportCheck.toggled.connect(self.regionChanged)

        self.setLayoutEnabled(self.updateButtonLayout, False)
        self.setLayoutEnabled(self.renderButtonLayout, False)
        self.textureUpdate.setEnabled(False)
        libraryGroupBox.setEnabled(False)
        viewGroupBox.setEnabled(False)
        projGroupBox.setEnabled(False)
        uvGroupBox.setEnabled(False)

        self.updatePoseLibrary([], True)
        self.updateLibraryObjects()
        self.updateModeChanged(1)
        self.viewModeChanged(0)
        self.setStatus(i18n("Start server to begin"))
        
        self.uiContainer = scrollContainer    
        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

    def createActions(self):
        if not self.createdActions:
            self.createdActions = True
            window = instance.activeWindow()
            window.createAction('blender_layer_blender').triggered.connect(self.startBlender)
            window.createAction('blender_layer_update').triggered.connect(self.updateFrame)
            window.createAction('blender_layer_render').triggered.connect(self.render)
            window.createAction('blender_layer_update_animation').triggered.connect(self.updateAnimation)
            window.createAction('blender_layer_render_animation').triggered.connect(partial(self.updateAnimation, True))
            window.createAction('blender_layer_update_texture').triggered.connect(self.updateTexture)


    def canvasChanged(self, canvas):
        self.uiContainer.setEnabled(canvas != None and instance.activeDocument() != None and instance.activeDocument().rootNode() != None)
          
    def eventFilter(self, source, event):
        if event.type() == QEvent.MouseButtonPress and event.buttons() == Qt.MidButton and (event.modifiers() & Qt.AltModifier) == Qt.AltModifier and self.settings.navigateAlt and self.navigate and self.navigate.isEnabled() and (self.settings.viewMode < 3 or  self.settings.viewMode == 5):
            self.navigate.mousePressEvent(event, True)
            return True
        elif event.type() == QEvent.MouseMove and event.buttons() == Qt.MidButton and (event.modifiers() & Qt.AltModifier) == Qt.AltModifier and self.settings.navigateAlt and self.navigate and self.navigate.isEnabled() and (self.settings.viewMode < 3 or  self.settings.viewMode == 5):
            self.navigate.mouseMoveEvent(event)
            return True
        elif event.type() == QEvent.Wheel and (event.modifiers() & Qt.AltModifier) == Qt.AltModifier and self.settings.navigateAlt and self.navigate and self.navigate.isEnabled() and (self.settings.viewMode < 3 or  self.settings.viewMode == 5):
            self.navigate.wheelEvent(event)
            return True
        elif event.type() == QEvent.Drop and self.uiContainer.isEnabled() and event.mimeData().hasUrls() and any(u.toLocalFile().endswith('.blend') for u in event.mimeData().urls()):
            self.dropEvent(event)
            return True
        elif type(source) == QMainWindow and event.type() == QEvent.WindowActivate and (self.settings.updateMode == 1 or self.settings.updateMode == 2) and self.server and self.server.running:
            self.server.sendMessage(('requestFrame', True))
        elif (event.type() == QEvent.ContextMenu and source is self.poseList):
            menu = QtWidgets.QMenu()
            menu.addAction(i18n("Apply Pose"))
            flipped = menu.addAction(i18n("Apply Flipped"))
            action = menu.exec_(event.globalPos())
            if action:
                item = source.itemAt(event.pos())
                self.applyPose(item, action == flipped)
            return True
        elif type(source) == QOpenGLWidget and (event.type() == QEvent.MouseMove or event.type() == QEvent.Leave) and self.server and self.server.running and instance.activeDocument() == self.activeDocument and self.settings.viewMode == 4 and self.settings.uvCursor:
            if event.type() == QEvent.MouseMove:
                w = instance.activeWindow()
                v = w.activeView()
                c = v.canvas()
                d = v.document()
                w0 = w.qwindow().centralWidget().currentWidget().currentSubWindow().widget().layout().itemAtPosition(1, 1).widget()
                pos = QPointF((event.pos().x() + w0.horizontalScrollBar().value()) / c.zoomLevel(), (event.pos().y() + w0.verticalScrollBar().value()) / c.zoomLevel())
                r = -c.rotation()
                w = d.width() * 72.0 / d.xRes()
                h = d.height() * 72.0 / d.yRes()
                if c.mirror():
                    pos = QPointF(w - pos.x(), pos.y())
                    r = -r
                if r != 0:
                    xo = w * 0.5
                    xo1 = 0#w * self.navigate.rotation.x() / math.pi
                    yo = h * 0.5
                    yo1 = 0#h * self.navigate.rotation.y() / math.pi
                    pos = QTransform().translate(xo, yo).rotate(r).translate(-xo + xo1, -yo + yo1).map(pos)
                pos = QPointF(pos.x() / w, pos.y() / h)
                b = v.brushSize() * 2
                self.server.sendMessage(('cursor', pos.x(), 1.0 - pos.y(), b / d.width(), b / d.height()))
            else:
                self.server.sendMessage(('cursor', -1, -1, -1, -1))

        elif self.settings.viewMode == 5 and self.server and self.server.running and (event.type() == QEvent.Shortcut and event.key() == QKeySequence.Undo):
            self.server.sendMessage(('undo', True))
            return True
        elif self.settings.viewMode == 5 and self.server and self.server.running and (event.type() == QEvent.Shortcut and event.key() == QKeySequence.Redo):
            self.server.sendMessage(('redo', True))
            return True
        elif type(source) == QOpenGLWidget and (event.type() == QEvent.MouseButtonPress and event.buttons() == Qt.LeftButton or event.type() == QEvent.ShortcutOverride) and self.server and self.server.running:
            if self.settings.viewMode == 4:
                self.server.requestTextureDelayed(1)
            elif self.settings.viewMode == 5:
                self.server.requestTextureDelayed(2)
        return super().eventFilter(source, event)

    def setSettingsAndSend(self, attr, v):
        setattr(self.settings, attr, v)
        if self.server and self.server.running:
            self.server.sendMessage((attr, v))

    def sendBlockableMessage(self, msg):
        if not self.blockServerSignal and self.server and self.server.running:
            self.server.sendMessage(msg)

    def changeSpinBox(self, box, value):
        box.setValue(value / 10.0)
            
    def changeSlider(self, slider, value):
        slider.setValue(int(value * 10.0))
        
    def dragEnterEvent(self, event):
        if self.uiContainer.isEnabled() and event.mimeData().hasUrls() and any(u.toLocalFile().endswith('.blend') for u in event.mimeData().urls()):
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().endswith('.blend')]
        self.startBlender(True, files[0])

    def showSettings(self):
        self.determineBlenderPath(False)
        self.settingsButton.setEnabled(False)

        dialog = QDialog(Application.activeWindow().qwindow())
        dialog.setWindowTitle(i18n("Blender Layer Settings"))
        buttonBox = QDialogButtonBox()
        buttonBox.setOrientation(QtCore.Qt.Horizontal)
        buttonBox.setStandardButtons(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(dialog.accept)
        buttonBox.rejected.connect(dialog.reject)
        
        blenderPathInput = QLineEdit()
        blenderPathInput.setText(self.settings.blenderPath)
        blenderPathInput.textEdited.connect(lambda s: setattr(self.settings, 'blenderPath', s))
        blenderPathInput.setToolTip(i18n("Path to the Blender executable"))

        def browseBlenderPath():
            dialog = QFileDialog(self, i18n("Open Blender executable"), self.settings.blenderPath if os.path.isfile(self.settings.blenderPath) else QStandardPaths.writableLocation(QStandardPaths.ApplicationsLocation))   
            if dialog.exec_() == QDialog.Accepted:
                self.settings.blenderPath = dialog.selectedUrls()[0].toLocalFile()   
            blenderPathInput.setText(self.settings.blenderPath)
            
        blenderPathBrowse = QPushButton()
        blenderPathBrowse.setIcon(instance.icon('folder'))
        blenderPathBrowse.clicked.connect(browseBlenderPath)
        blenderPathBrowse.setToolTip(i18n("Browse"))

        blenderPathHBoxLayout = QHBoxLayout()
        blenderPathHBoxLayout.addWidget(blenderPathInput)
        blenderPathHBoxLayout.addWidget(blenderPathBrowse)

        renderPathInput = QLineEdit()
        renderPathInput.setText(self.settings.renderPath)
        renderPathInput.textEdited.connect(lambda s: setattr(self.settings, 'renderPath', s))
        renderPathInput.setToolTip(i18n("Path where rendered frames will be saved"))

        def browseRenderPath():
            (fileName, mime) = QFileDialog.getSaveFileName(self, i18n("Select render output path"), self.settings.renderPath if os.path.isdir(os.path.dirname(self.settings.renderPath)) else '/tmp')
            if fileName:
                self.settings.renderPath = fileName
                renderPathInput.setText(self.settings.renderPath)
            
        renderPathBrowse = QPushButton()
        renderPathBrowse.setIcon(instance.icon('folder'))
        renderPathBrowse.clicked.connect(browseRenderPath)
        renderPathBrowse.setToolTip(i18n("Browse"))

        renderPathHBoxLayout = QHBoxLayout()
        renderPathHBoxLayout.addWidget(renderPathInput)
        renderPathHBoxLayout.addWidget(renderPathBrowse)
        
        layerNameInput = QLineEdit()
        layerNameInput.setText(self.settings.layerName)
        layerNameInput.textEdited.connect(lambda s: setattr(self.settings, 'layerName', s))
        layerNameInput.setToolTip(i18n("Name of the layer which shows the view from Blender"))

        projLayerNameInput = QLineEdit()
        projLayerNameInput.setText(self.settings.projLayerName)
        projLayerNameInput.textEdited.connect(lambda s: setattr(self.settings, 'projLayerName', s))
        projLayerNameInput.setToolTip(i18n("Name of the layer which gets projected onto the model"))

        uvLayerNameInput = QLineEdit()
        uvLayerNameInput.setText(self.settings.uvLayerName)
        uvLayerNameInput.textEdited.connect(lambda s: setattr(self.settings, 'uvLayerName', s))
        uvLayerNameInput.setToolTip(i18n("Name of the uv layout reference layer"))

        relPathCheckBox = QCheckBox(i18n("Use relative paths for .blend files"))
        relPathCheckBox.setChecked(self.settings.relPath)
        relPathCheckBox.toggled.connect(lambda v: setattr(self.settings, 'relPath', v))
        relPathCheckBox.setToolTip(i18n("Use a path relative to the current document\nwhen saving the name of the last open .blend file"))

        navigateAltCheckBox = QCheckBox(i18n("Enable navigation with Alt + Middle Button"))
        navigateAltCheckBox.setChecked(self.settings.navigateAlt)
        navigateAltCheckBox.toggled.connect(lambda v: setattr(self.settings, 'navigateAlt', v))
        navigateAltCheckBox.setToolTip(i18n("Enables rotating the view by holding Alt and pressing the Middle Mouse Button,\nYou can also pan by additionaly holding Ctrl\nand zoom by holding Shift or using the mouse wheel"))
        
        form = QFormLayout()
        form.addRow(i18n("Blender location:"), blenderPathHBoxLayout)
        form.addRow(i18n("Render location:"), renderPathHBoxLayout)
        form.addRow(i18n("Layer name"), layerNameInput)
        form.addRow(i18n("Projection layer name"), projLayerNameInput)
        form.addRow(i18n("UV Layout layer name"), uvLayerNameInput)
        form.addRow(relPathCheckBox)
        form.addRow(navigateAltCheckBox)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        
        libraryGroupBox = QGroupBox(i18n("Library"))
        
        libraryTable = QTableWidget(len(self.settings.library), 3)
        libraryTable.setHorizontalHeaderLabels([i18n("Name"), i18n("Path to .blend File"), i18n("Objects to append")])
        libraryTable.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        libraryTable.verticalHeader().setVisible(False)
        libraryTable.setSelectionMode(QAbstractItemView.NoSelection)
        libraryTable.setMinimumHeight(190)
        
        row = 0
        for (name, file, innerpath) in self.settings.library:
            libraryTable.setItem(row, 0, QTableWidgetItem(name))
            libraryTable.setItem(row, 1, QTableWidgetItem(file))
            libraryTable.setItem(row, 2, QTableWidgetItem(innerpath))
            row = row + 1

        def browseBlendFile():
            row = libraryTable.rowCount() if libraryTable.currentRow() < 0 else libraryTable.currentRow()
            lastItem = libraryTable.item(row, 0)
            lastPath = lastItem.text() if lastItem else ''
            dialog = QFileDialog(self, i18n("Open .blend file"), lastPath if os.path.isfile(lastPath) else QStandardPaths.writableLocation(QStandardPaths.PicturesLocation))   
            if dialog.exec_() == QDialog.Accepted:
                row = libraryTable.rowCount() if libraryTable.currentRow() < 0 else libraryTable.currentRow() + 1
                for file in dialog.selectedUrls():
                    file = file.toLocalFile()                  
                    libraryTable.insertRow(row)
                    libraryTable.setItem(row, 0, QTableWidgetItem(os.path.basename(file)))
                    libraryTable.setItem(row, 1, QTableWidgetItem(file))
                    libraryTable.setItem(row, 2, QTableWidgetItem(''))
                    row = row + 1

        creditLabel = QLabel(i18n("Body-chan models CC-0 by ") + '<a href=\"https://blendswap.com/blend/23521\">vinchau</a>')
        creditLabel.setTextInteractionFlags(Qt.TextBrowserInteraction);
        creditLabel.setOpenExternalLinks(True);
        addButton = QToolButton()
        addButton.setIcon(instance.icon('addlayer'))
        addButton.setToolTip(i18n("Add"))
        addButton.clicked.connect(browseBlendFile)
        removeButton = QToolButton()
        removeButton.setIcon(instance.icon('deletelayer'))
        removeButton.setToolTip(i18n("Remove"))
        removeButton.clicked.connect(lambda: libraryTable.removeRow(libraryTable.currentRow()))
        
        librarHBox = QHBoxLayout()
        librarHBox.setContentsMargins(11, 0, 11, 11)
        librarHBox.addWidget(creditLabel)
        librarHBox.addStretch()
        librarHBox.addWidget(addButton)
        librarHBox.addWidget(removeButton)
        
        libraryVBox = QVBoxLayout()
        libraryVBox.setContentsMargins(0, 0, 0, 0)
        libraryVBox.addWidget(libraryTable)
        libraryVBox.addLayout(librarHBox)
        libraryGroupBox.setLayout(libraryVBox)

        connectionGroupBox = QGroupBox(i18n("Connection"))
        
        portSpinBox = QSpinBox()
        portSpinBox.setRange(0, 65535)
        portSpinBox.setValue(self.settings.port)
        portSpinBox.valueChanged.connect(lambda v: setattr(self.settings, 'port', v))
        
        portTriesSpinBox = QSpinBox()
        portTriesSpinBox.setRange(1, 256)
        portTriesSpinBox.setValue(self.settings.portTries)
        portTriesSpinBox.setToolTip(i18n("When the port is occupied, increasing port and retry"))
        portTriesSpinBox.valueChanged.connect(lambda v: setattr(self.settings, 'portTries', v))

        hostInput = QLineEdit()
        hostInput.setText(self.settings.host)
        hostInput.textEdited.connect(lambda s: setattr(self.settings, 'host', s))
        
        timeoutSpinBox = QDoubleSpinBox()
        timeoutSpinBox.setRange(0, 30)
        timeoutSpinBox.setSuffix("s")
        timeoutSpinBox.setValue(self.settings.timeout)
        timeoutSpinBox.valueChanged.connect(lambda v: setattr(self.settings, 'timeout', v))
        
        sharedMemCheckBox = QCheckBox(i18n("Use shared memory buffer"))
        sharedMemCheckBox.setChecked(self.settings.sharedMem)
        sharedMemCheckBox.setToolTip(i18n("Use shared memory to transfer the pixels from Blender.\nShould have better performance than sending them via the socket"))
        sharedMemCheckBox.toggled.connect(lambda v: setattr(self.settings, 'sharedMem', v))

        connectionForm = QFormLayout()
        connectionForm.addRow(i18n("Host:"), hostInput)
        connectionForm.addRow(i18n("Port:"), portSpinBox)
        connectionForm.addRow(i18n("Try alternate ports:"), portTriesSpinBox)
        connectionForm.addRow(i18n("Timeout after:"), timeoutSpinBox)
        connectionForm.addRow(sharedMemCheckBox)
        connectionGroupBox.setLayout(connectionForm)
        
        assistantsGroupBox = QGroupBox(i18n("Assistants"))

        threePointCheckBox = QCheckBox(i18n("3 point perspective"))
        threePointCheckBox.setChecked(self.settings.assistantsThreePoint)
        threePointCheckBox.setToolTip(i18n("Include a third vanishing point in the assistant set"))
        threePointCheckBox.toggled.connect(lambda v: setattr(self.settings, 'assistantsThreePoint', v))

        axisCheckBox = QCheckBox(i18n("Colored axis"))
        axisCheckBox.setChecked(self.settings.assistantsAxis)
        axisCheckBox.setToolTip(i18n("Include colored lines representing the axis in the assistant set"))
        axisCheckBox.toggled.connect(lambda v: setattr(self.settings, 'assistantsAxis', v))

        regionCheckBox = QCheckBox(i18n("Limit to image region"))
        regionCheckBox.setChecked(self.settings.assistantsRegion)
        regionCheckBox.setToolTip(i18n("When the view is limited to a region, also limit assistants"))
        regionCheckBox.toggled.connect(lambda v: setattr(self.settings, 'assistantsRegion', v))

        cursorColorLabel = QLabel(i18n("Blender cursor color"))
        curorColorInput = QLineEdit()
        curorColorInput.setText(self.settings.cursorColor)
        curorColorInput.textEdited.connect(lambda s: setattr(self.settings, 'cursorColor', s))
        curorColorInput.setToolTip(i18n("Color of the UV painting cursor in Blender"))

        cursorHBox = QHBoxLayout()
        cursorHBox.addWidget(cursorColorLabel)
        cursorHBox.addWidget(curorColorInput)

        assistantsVBox = QVBoxLayout()
        assistantsVBox.addWidget(threePointCheckBox)
        assistantsVBox.addWidget(axisCheckBox)
        assistantsVBox.addWidget(regionCheckBox)
        assistantsVBox.addLayout(cursorHBox)
        assistantsVBox.addLayout(cursorHBox)
        assistantsGroupBox.setLayout(assistantsVBox)
        
        colorManagementGroupBox = QGroupBox(i18n("Color Management"))

        overrideSRGBCheckBox = QCheckBox(i18n("Override layer color profile with 'sRGB-elle-V2-srgbtrc.icc'"))
        overrideSRGBCheckBox.setChecked(self.settings.overrideSRGB)
        overrideSRGBCheckBox.setToolTip(i18n("When disabled the document's default color space will be used.\nOnly disable if you know what you're doing.\nSupport for different color depths is limited"))
        overrideSRGBCheckBox.toggled.connect(lambda v: setattr(self.settings, 'overrideSRGB', v))

        colorMangeBlenderCheckBox = QCheckBox(i18n("Perform Blender's color management"))
        colorMangeBlenderCheckBox.setChecked(self.settings.colorManageBlender)
        colorMangeBlenderCheckBox.setToolTip(i18n("Disable if you're using a linear gamma color space"))
        colorMangeBlenderCheckBox.toggled.connect(lambda v: setattr(self.settings, 'colorManageBlender', v))

        convertBGRCheckBox = QCheckBox(i18n("Perform BGR to RGB conversion"))
        convertBGRCheckBox.setChecked(self.settings.convertBGR)
        convertBGRCheckBox.setToolTip(i18n("Disable if R and B channels appear to be switched"))
        convertBGRCheckBox.toggled.connect(lambda v: setattr(self.settings, 'convertBGR', v))

        colorManagementVBox = QVBoxLayout()
        colorManagementVBox.addWidget(overrideSRGBCheckBox)
        colorManagementVBox.addWidget(colorMangeBlenderCheckBox)
        colorManagementVBox.addWidget(convertBGRCheckBox)
        colorManagementGroupBox.setLayout(colorManagementVBox)
        
        dangerGroupBox = QGroupBox(i18n("Danger Zone (Use at your own risk)"))

        backgroundDrawCheckBox = QCheckBox(i18n("Allow drawing while minimized"))
        backgroundDrawCheckBox.setChecked(self.settings.backgroundDraw)
        backgroundDrawCheckBox.setToolTip(i18n("Will crash once in a while"))
        backgroundDrawCheckBox.toggled.connect(lambda v: setattr(self.settings, 'backgroundDraw', v))
        
        lockFramesSpinBox = QSpinBox()
        lockFramesSpinBox.setRange(0, 120)
        lockFramesSpinBox.setSuffix(i18n(" frames"))
        lockFramesSpinBox.setValue(self.settings.lockFrames)
        lockFramesSpinBox.setToolTip(i18n("Hold krita's image lock for the specified number of frames\nSetting this to 0 will disable locking resulting in crashes if the image is edited at the same time the frame is updated"))
        lockFramesSpinBox.valueChanged.connect(lambda v: setattr(self.settings, 'lockFrames', v))

        dangerForm = QFormLayout()
        dangerForm.addRow(backgroundDrawCheckBox)
        dangerForm.addRow(i18n("Hold lock for: "), lockFramesSpinBox)
        dangerGroupBox.setLayout(dangerForm)
        
        scrollContainer = QWidget()
        vbox = QVBoxLayout(scrollContainer)
        vbox.addLayout(form)
        vbox.addWidget(line)
        vbox.addWidget(libraryGroupBox)
        vbox.addWidget(connectionGroupBox)
        vbox.addWidget(assistantsGroupBox)
        vbox.addWidget(colorManagementGroupBox)
        vbox.addWidget(dangerGroupBox)
        vbox.addStretch(1)
        
        scroll = QScrollArea()
        scroll.setWidget(scrollContainer)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFrameShadow(QFrame.Plain)
        scroll.setMinimumWidth(scrollContainer.minimumSizeHint().width() + scroll.verticalScrollBar().minimumSizeHint().width() + 22)

        dialogVbox = QVBoxLayout(dialog)
        dialogVbox.addWidget(scroll)
        dialogVbox.addWidget(buttonBox)
        dialog.show()
        dialog.activateWindow()
        if dialog.exec_() == QDialog.Accepted:
            lib = []
            for row in range(0, libraryTable.rowCount()):
                name = libraryTable.item(row, 0).text()
                file = libraryTable.item(row, 1).text()
                innerpath = libraryTable.item(row, 2).text()
                lib.append((name, file, innerpath))
            self.settings.library = lib
            self.writeSettings()
        else:
            self.readSettings()
            
        self.updateLibraryObjects()
        self.settingsButton.setEnabled(True)

    def onError(self, message):
        print(message)
        self.setStatus(message)

    def setStatus(self, message):
        if message == self.lastStatus:
            self.statusRepeated = self.statusRepeated + 1
            message = f'{message} ({self.statusRepeated})'
        else:
            self.statusRepeated = 1
            self.lastStatus = message
        if self.server and self.server.running:
            self.statusBar.setText("<font color='#da4453'>●</font> " + str(message))
        else:
            self.statusBar.setText(message)   
        
    def determineBlenderPath(self, dialog = True):
        if not self.settings.blenderPath:
            try:
                import shutil
                p = shutil.which('blender')
                if p and os.path.isfile(p):
                    self.settings.blenderPath = p
                    
                elif os.path.isdir('C:\Program Files\Blender Foundation'):
                    versions = sorted(os.listdir('C:\Program Files\Blender Foundation'), reverse=True)
                    for ver in versions:
                        p = os.path.join('C:\Program Files\Blender Foundation', ver, 'blender.exe')
                        if os.path.isfile(p):
                            self.settings.blenderPath = p
                            break
            except e:
                print(e)
                
            if not self.settings.blenderPath:
                if dialog:
                    dialog = QFileDialog(self, i18n("Open blender executable"), QStandardPaths.writableLocation(QStandardPaths.ApplicationsLocation))
                    if dialog.exec_() == QDialog.Accepted:
                        self.settings.blenderPath = dialog.selectedUrls()[0].toLocalFile()   
                        self.writeSettings()
            else:
                self.writeSettings()
               
        
    def startBlender(self, ignored = False, file = ''):   
        if not instance.activeDocument():
            return
        if self.blenderRunning:
            if file and self.server and self.server.running:
                self.server.sendMessage(('file', file))
            return
            
        self.determineBlenderPath()   
        if self.settings.blenderPath:
            args = [self.settings.blenderPath, '--python', str(path.abspath(os.path.join(os.path.dirname(__file__), 'blenderLayerClient.py'))), '--', '--connect-to-krita', str(self.settings.host), str(self.settings.port), str(self.settings.timeout)]
            
            if self.activeInFile == None:
                self.activeInFile = instance.activeDocument().fileName()
            
            if not file:
                file = self.getFilenameFromLayer()                    
            if file and os.path.isfile(file):
                args.insert(1, file)

            self.blenderArgs = args    
            if (not self.server) or (not self.server.running):
                self.startStopServer()
            elif self.currentPort != -1:  
                self.runBlenderOnPort(self.currentPort)
                
    def runBlenderOnPort(self, port):
        self.currentPort = port
        if self.blenderArgs:
            self.blenderArgs[-2] = str(port)
            runnable = BlenderRunnable(self.blenderArgs)
            runnable.signals.finished.connect(self.onBlenderStopped)
            
            self.blenderRunning = True
            self.startBlenderButton.setEnabled(False)
            self.startBlenderButton.setText(i18n("Blender running..."))   

            QThreadPool.globalInstance().start(runnable)
            self.blenderArgs = None
            
    def onBlenderStopped(self, result):
        if result:
            self.setStatus(result)
        self.blenderRunning = False
        self.startBlenderButton.setEnabled(True)
        self.startBlenderButton.setText(i18n("Start Blender"))
                    
    def startStopServer(self):
        if self.server and self.server.running:
            self.server.running = False
            self.startstop.setEnabled(False)
            self.startstop.setText(i18n("Stopping..."))
            self.activeInFile = None
            self.activeDocument = None
        elif instance.activeDocument():
            self.server = BlenderLayerServer(self.settings)
            self.server.signals.portFound.connect(self.runBlenderOnPort)
            self.server.signals.finished.connect(self.onServerStopped)
            self.server.signals.connected.connect(self.onServerConnected)
            self.server.signals.error.connect(self.onError)
            self.server.signals.msgReceived.connect(self.handleMessage)
            self.activeDocument = instance.activeDocument()
            self.activeInFile = self.activeDocument.fileName()

            QThreadPool.globalInstance().start(self.server)
            self.startstop.setText(i18n("Stop Server"))
            self.setStatus(i18n("Waiting for Blender..."))

    def onServerStopped(self, result):
        self.currentPort = -1
        self.onServerConnected(False, None)
        self.startstop.setEnabled(True)
        self.startstop.setText(i18n("Start Server"))
        if result:
            self.setStatus(result)
        else:
            self.setStatus(i18n("Server stopped"))
        if self.settings.region:
            self.saveRegionToLayer()
        
    def onServerConnected(self, connected, info):
        self.viewGroup.setEnabled(connected)
        self.uvGroup.setEnabled(connected)
        self.projGroup.setEnabled(connected)
        self.libraryGroup.setEnabled(connected)
        self.setLayoutEnabled(self.updateButtonLayout, connected)
        self.setLayoutEnabled(self.renderButtonLayout, connected)
        self.textureUpdate.setEnabled(connected)
        if connected:
            self.startBlenderButton.setEnabled(False)
            self.startBlenderButton.setText(i18n("Connected")) 
            file = ''
            if info:
                transparancySupported = info[1]
                file = info[2]
                
                self.transparentCheck.setEnabled(transparancySupported)
                if not transparancySupported:
                    self.transparentCheck.setChecked(False)
            if file:
                self.setStatus(i18n("Successfully connected")+'<br/>'+os.path.basename(file))
                self.saveFilenameToLayer(file)
            else:
                self.setStatus(i18n("Successfully connected"))
        else:
            self.progress.hide()
            self.updatePoseLibrary([], True)
            self.startBlenderButton.setEnabled(not self.blenderRunning)
            self.startBlenderButton.setText(i18n("Blender running...") if self.blenderRunning else i18n("Start Blender"))
            self.setStatus(i18n("Waiting for Blender..."))
            
    def handleMessage(self, msg):
        type = msg[0]
        if type == 'poselib':
            self.updatePoseLibrary(msg[1], msg[2])
        elif type == 'armatures':
            self.poseArmatures.clear()
            if len(msg[1]) == 0:
                self.poseArmatures.addItems([i18n("<None>")])
            else:
                self.poseArmatures.addItems(msg[1])
        elif type == 'posePreviews':
            for (name, pixels) in msg[1]:
                self.loadPosePreview(name, pixels)
        elif type == 'cameraList':
            menu = QtWidgets.QMenu()
            for cam in msg[1]:
                a = menu.addAction(cam)
                a.setIcon(QIcon())
                a.setIconVisibleInMenu(False)
            menu.setMinimumWidth(self.moveToCamera.width())
            action = menu.exec_(self.moveToCamera.mapToGlobal(QPoint(0, 0)))
            if action:
                self.server.sendMessage(('moveToCamera', action.text()))
        elif type == 'rotate':
            self.blockServerSignal = True
            self.navigate.setRotation(msg[1], msg[2])
            self.roll.setValue(msg[3] / math.pi * 180)
            self.blockServerSignal = False
        elif type == 'lens':
            self.blockServerSignal = True
            self.lens.setValue(msg[1])
            self.blockServerSignal = False
        elif type == 'ortho':    
            self.blockServerSignal = True        
            self.navigate.setOrtho(msg[1])
            self.blockServerSignal = False
        elif type == 'shading':
            self.blockServerSignal = True
            self.shading.setCurrentIndex(msg[1])
            self.blockServerSignal = False
        elif type == 'assistants':      
            self.writeAssistants(msg)
        elif type == 'uvLayout':      
            self.importUVLayout(msg)
        elif type == 'region':  
            self.resetRegion(msg[1])
            if (msg[1]):
                self.regionX.setValue(msg[2])
                self.regionY.setValue(msg[3])
                self.regionWidth.setValue(msg[4])
                self.regionHeight.setValue(msg[5])
                self.regionViewport.setChecked(msg[6])
            self.regionChanged()
        elif type == 'textures':       
            self.textures.clear()
            self.textures.addItems([i18n("<None>"), i18n("<New>")])
            self.textures.addItems([n for (n, f) in msg[1]])
            self.settings.textures = msg[1]
        elif type == 'file':
            file = msg[1]
            self.setStatus(i18n("Successfully connected")+ '<br/>'+os.path.basename(file))
            self.saveFilenameToLayer(file)
        elif type == 'engine':
            self.updateCyclesWarning(msg[1], self.settings.shading)
        elif type == 'updateProgress':
            self.update.setCurrentIndex(3)
            self.setStatus(i18n("Updated animation frame"))
            inProgress = msg[1] < msg[3]
            self.progress.setVisible(inProgress)
            self.setLayoutEnabled(self.renderButtonLayout, not inProgress)
            self.setLayoutEnabled(self.updateButtonLayout, not inProgress)
            self.progress.setRange(msg[2], msg[3])
            self.progress.setValue(msg[1])
        elif type == 'renderProgress':
            self.view.setCurrentIndex(3)
            self.update.setCurrentIndex(3)
            self.setStatus(i18n("Updated from render result"))
            inProgress = msg[1] < msg[3]
            self.progress.setVisible(inProgress)
            self.setLayoutEnabled(self.renderButtonLayout, not inProgress)
            self.setLayoutEnabled(self.updateButtonLayout, not inProgress)
            self.progress.setRange(msg[2], msg[3])
            self.progress.setValue(msg[1])
        elif type == 'renderCancelled':
            self.setStatus(i18n("Rendering was cancelled"))
            self.setLayoutEnabled(self.renderButtonLayout, True)
            self.setLayoutEnabled(self.updateButtonLayout, True)
            self.progress.hide()
        elif type == 'status':
            self.setStatus('[Blender] ' + i18n(msg[1]))
        else:
            print("Received unrecognized message type from Blender: ", type)  
            
    def createAssistants(self):
        (fileName, mime) = QFileDialog.getSaveFileName(self, i18n("Save File"), os.path.join(QStandardPaths.writableLocation(QStandardPaths.PicturesLocation), 'blenderlayer.paintingassistant'), i18n("Krita Assistant (*.paintingassistant)"))
        if fileName:
            instance.action('KisAssistantTool').trigger()
            d = self.activeDocument if self.activeDocument else instance.activeDocument()
            self.server.sendMessage(('assistants', fileName, d.width() / d.xRes() * 72.0, d.height() / d.yRes() * 72.0))

    def writeAssistants(self, msg):
        fileName = msg[1]
        third = self.settings.assistantsThreePoint
        axis = self.settings.assistantsAxis
        region = self.settings.assistantsRegion and self.region.isChecked()

        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        scaleX = 1.0 / d.xRes() * 72.0
        scaleY = 1.0  / d.yRes() * 72.0

        handleLength = 5
        #vanishing points
        vxx = msg[2]
        vxy = msg[3]
        vxOrtho = msg[4]
        vyx = msg[5]
        vyy = msg[6]
        vyOrtho = msg[7]
        vzx = msg[8]
        vzy = msg[9]  
        vzOrtho = msg[10]

        #center
        cx = msg[11]
        cy = msg[12] 
        
        if vxOrtho:
            v2xx = cx - vxx * 100
            v2xy = cy - vxy * 100
            vxx = cx + vxx * 100
            vxy = cy + vxy * 100
        else:
            v2xx = cx + (cx - vxx) * 100
            v2xy = cy + (cy - vxy) * 100
            
        if vyOrtho:
            v2yx = cx - vyx * 100
            v2yy = cy - vyy * 100
            vyx = cx + vyx * 100
            vyy = cy + vyy * 100
        else:
            v2yx = cx + (cx - vyx) * 100
            v2yy = cy + (cy - vyy) * 100
            
        if vzOrtho:
            v2zx = cx - vzx * 100
            v2zy = cy - vzy * 100
            vzx = cx + vzx * 100
            vzy = cy + vzy * 100
        else:
            v2zx = cx + (cx - vzx) * 100
            v2zy = cy + (cy - vzy) * 100
        
        local = '1' if region else '0'
        regionHandles = '<handle ref="10"/><handle ref="11"/>' if region else ''
        
        file = open(fileName,'w')
        file.write('<?xml version="1.0" encoding="UTF-8"?><paintingassistant color="176,176,176,255">')
        file.write('<handles><handle id="0" x="{0}" y="{1}"/><handle id="1" x="{2}" y="{3}"/><handle id="2" x="{4}" y="{5}"/><handle id="3" x="{4}" y="{5}"/><handle id="4" x="{0}" y="{1}"/><handle id="5" x="{6}" y="{7}"/><handle id="6" x="{2}" y="{3}"/><handle id="7" x="{8}" y="{9}"/><handle id="8" x="{4}" y="{5}"/><handle id="9" x="{10}" y="{11}"/><handle id="10" x="{24}" y="{25}"/><handle id="11" x="{26}" y="{27}"/></handles><sidehandles><sidehandle id="0" x="{12}" y="{1}"/><sidehandle id="1" x="{13}" y="{1}"/><sidehandle id="2" x="{14}" y="{1}"/><sidehandle id="3" x="{15}" y="{1}"/><sidehandle id="4" x="{16}" y="{3}"/><sidehandle id="5" x="{17}" y="{3}"/><sidehandle id="6" x="{18}" y="{3}"/><sidehandle id="7" x="{19}" y="{3}"/><sidehandle id="8" x="{20}" y="{5}"/><sidehandle id="9" x="{21}" y="{5}"/><sidehandle id="10" x="{22}" y="{5}"/><sidehandle id="11" x="{23}" y="{5}"/></sidehandles><assistants>'.format(
        vxx, vxy, vyx, vyy, vzx, vzy,
        v2xx, v2xy, v2yx, v2yy, v2zx, v2zy,
        vxx - handleLength * 2, vxx - handleLength, vxx + handleLength, vxx + handleLength * 2,
        vyx - handleLength * 2, vyx - handleLength, vyx + handleLength, vyx + handleLength * 2,
        vzx - handleLength * 2, vzx - handleLength, vzx + handleLength, vzx + handleLength * 2,
        self.settings.regionX * scaleX, self.settings.regionX * scaleY,
        (self.settings.regionX + self.settings.regionWidth) * scaleX, (self.settings.regionY + self.settings.regionHeight) * scaleY))
        if not vxOrtho and not vyOrtho:
            file.write('<assistant type="two point" useCustomColor="0" customColor="176,176,176,255"><gridDensity value="{0}"/><useVertical value="{1}"/><isLocal value="{2}"/><handles><handle ref="0"/><handle ref="1"/><handle ref="2"/>{3}</handles><sidehandles><sidehandle ref="0"/><sidehandle ref="1"/><sidehandle ref="2"/><sidehandle ref="3"/><sidehandle ref="4"/><sidehandle ref="5"/><sidehandle ref="6"/><sidehandle ref="7"/></sidehandles></assistant>'.format(1.0, 1 if not third else 0, local, regionHandles))
        else:
            if vxOrtho:
               file.write('<assistant type="parallel ruler" useCustomColor="{0}" customColor="255,51,82,127"><isLocal value="{1}"/><handles><handle ref="4"/><handle ref="5"/>{2}</handles></assistant>'.format(1 if axis else 0), local, regionHandles)
            else:
                file.write('<assistant type="vanishing point" useCustomColor="0" customColor="176,176,176,255"><angleDensity value="{0}"/><isLocal value="{1}"/><handles><handle ref="0"/>{2}</handles><sidehandles><sidehandle ref="0"/><sidehandle ref="1"/><sidehandle ref="2"/><sidehandle ref="3"/></sidehandles></assistant>'.format(10.0, local, regionHandles))
            if vyOrtho:
               file.write('<assistant type="parallel ruler" useCustomColor="{0}" customColor="139,220,0,127"><isLocal value="{1}"/><handles><handle ref="6"/><handle ref="7"/>{2}</handles></assistant>'.format(1 if axis else 0), local, regionHandles)
            else:
                file.write('<assistant type="vanishing point" useCustomColor="0" customColor="176,176,176,255"><angleDensity value="{0}"/><isLocal value="{1}"/><handles><handle ref="1"/>{2}</handles><sidehandles><sidehandle ref="4"/><sidehandle ref="5"/><sidehandle ref="6"/><sidehandle ref="7"/></sidehandles></assistant>'.format(10.0, local, regionHandles))
        if third:
            if vzOrtho:
               file.write('<assistant type="parallel ruler" useCustomColor="{0}" customColor="40,144,255,127"><isLocal value="{1}"/><handles><handle ref="8"/><handle ref="9"/>{2}</handles></assistant>'.format(1 if axis else 0), local, regionHandles)
            else:
                file.write('<assistant type="vanishing point" useCustomColor="0" customColor="176,176,176,255"><angleDensity value="{0}"/><isLocal value="{1}"/><handles><handle ref="3"/>{2}</handles><sidehandles><sidehandle ref="8"/><sidehandle ref="9"/><sidehandle ref="10"/><sidehandle ref="11"/></sidehandles></assistant>'.format(10.0, local, regionHandles))
        if axis:
            if not vxOrtho:
                file.write('<assistant type="ruler" useCustomColor="1" customColor="255,51,82,127"><subdivisions value="0"/><minorSubdivisions value="0"/><fixedLength value="0" enabled="0" unit="px"/><handles><handle ref="4"/><handle ref="5"/></handles><sidehandles/></assistant>')
            if not vyOrtho:
                file.write('<assistant type="ruler" useCustomColor="1" customColor="139,220,0,127"><subdivisions value="0"/><minorSubdivisions value="0"/><fixedLength value="0" enabled="0" unit="px"/><handles><handle ref="6"/><handle ref="7"/></handles><sidehandles/></assistant>')
            if third and not vzOrtho:
                file.write('<assistant type="ruler" useCustomColor="1" customColor="40,144,255,127"><subdivisions value="0"/><minorSubdivisions value="0"/><fixedLength value="0" enabled="0" unit="px"/><handles><handle ref="8"/><handle ref="9"/></handles><sidehandles/></assistant>')
                
        file.write('</assistants></paintingassistant>')
        file.close()

    def requestUVLayout(self):
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        if not os.path.exists(self.settings.renderPath):
            os.makedirs(self.settings.renderPath)
        self.server.sendMessage(('uvLayout', os.path.join(self.settings.renderPath, 'uvLayout.svg'), d.width(), d.height()))
       
    def importUVLayout(self, msg):
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        f = open(msg[1],'r')
        with f:
            data = f.read()            
            n = d.createVectorLayer(self.settings.uvLayerName)
            n.addShapesFromSvg(data)
            d.rootNode().addChildNode(n, None)
        os.remove(msg[1])
         
    def requestCreateCamera(self):
        name, ok = QInputDialog.getText(self, i18n("New Camera"), i18n("Name"), QLineEdit.Normal, self.settings.layerName)
    
        if ok:
            self.server.sendMessage(('createCamera', name))
                    
    def updatePoseLibrary(self, items, clearPreviews):
        visible = len(items) > 0
        if not self.librarySeperator.isVisible() and visible:
            self.libraryForm.insertRow(1, self.librarySeperator)
            self.libraryForm.insertRow(2, self.poseArmaturesLabel, self.poseArmatures)
        elif self.librarySeperator.isVisible() and not visible:
            self.libraryForm.removeWidget(self.librarySeperator)
            self.libraryForm.removeWidget(self.poseArmatures)
            self.libraryForm.removeWidget(self.poseArmaturesLabel)
        self.librarySeperator.setVisible(visible)
        self.poseArmatures.setVisible(visible)
        self.poseArmaturesLabel.setVisible(visible)
        self.poseList.setVisible(visible)

        self.poseList.clear()
        self.settings.poseLib = items
        if clearPreviews:
            self.settings.posePreviews = {}
            if self.server and self.server.running and visible:
                self.server.sendMessage(('posePreviews', self.settings.poseLib[:10]))
        for name in items:
            pixels = self.settings.posePreviews.get(name)
            widget = QWidget()
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 11)
            image = QLabel()
            image.setAlignment(Qt.AlignCenter)
            if pixels:
                image.setPixmap(QPixmap.fromImage(QImage(pixels, 128, 128, QImage.Format_RGBA8888)))
                image.setMinimumWidth(128)
            else:
                icon = instance.icon('folder-pictures')
                image.setPixmap(icon.pixmap(icon.actualSize(QSize(64, 64))))
                image.setMinimumWidth(128)
            text = QLabel(name)
            text.setAlignment(Qt.AlignCenter)
            layout.addStretch()
            layout.addWidget(image)
            layout.addStretch()
            layout.addWidget(text)
            #layout.setSizeConstraint(QLayout.SetFixedSize)
            widget.setLayout(layout)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())    
            self.poseList.addItem(item)
            self.poseList.setItemWidget(item, widget)
            
    def loadPosePreview(self, name, pixels):
        if pixels:
            self.settings.posePreviews[name] = pixels
            try:
                i = self.settings.poseLib.index(name)
                widget = self.poseList.itemWidget(self.poseList.item(i)).layout().itemAt(1).widget()
                widget.setPixmap(QPixmap.fromImage(QImage(pixels, 128, 128, QImage.Format_RGBA8888)))          
            except ValueError as e:
                print(e)
            
    def requestPosePreviews(self, scroll):
        item = self.poseList.itemAt(100, 100)
        i = self.poseList.row(item)
        if i >= 0 and i < len(self.settings.poseLib):
            action = self.settings.poseLib[i]
            if self.settings.posePreviews.get(action) == None:
                self.server.sendMessage(('posePreviews', [action]))
                self.settings.posePreviews[action] = False
                
        item = self.poseList.itemAt(self.poseList.width() - 100, 100)
        i = self.poseList.row(item)
        if i >= 0 and i < len(self.settings.poseLib):
            action = self.settings.poseLib[i]
            if self.settings.posePreviews.get(action) == None:
                self.server.sendMessage(('posePreviews', [action]))
                self.settings.posePreviews[action] = False
                
    def applyPose(self, item, flipped = False):
        i = self.poseList.row(item)
        if i >= 0 and i < len(self.settings.poseLib):
            action = self.settings.poseLib[i]
            self.server.sendMessage(('pose', str(self.poseArmatures.currentText()), action, flipped))
            
    def updateLibraryObjects(self):
        if not self.libraryObject:
            return

        self.libraryObject.clear()
        items = [name for (name, file, innerpath) in self.settings.library]
        if len(items) == 0:
            self.libraryObject.addItems([i18n("<None>")])
            self.libraryAppend.setEnabled(False)
        else:
            self.libraryObject.addItems(items)
            self.libraryAppend.setEnabled(True)

    def appendFromLibrary(self):
        i = self.libraryObject.currentIndex()
        name, file, innerpath = self.settings.library[i]
        if not os.path.isfile(file):
            abs = path.abspath(os.path.join(os.path.dirname(__file__), file))
            if os.path.isfile(abs):
                file = str(abs)
        self.server.sendMessage(('append', name, file, innerpath))
        
    def render(self):
        if not self.isLayoutEnabled(self.renderButtonLayout):
            return
        
        self.progress.setRange(0, 0)
        self.progress.show()
        self.setLayoutEnabled(self.renderButtonLayout, False)
        self.setLayoutEnabled(self.updateButtonLayout, False)
        self.server.sendMessage(('render', self.renderOverride.isChecked(), self.renderTemporary.isChecked(), self.renderOverridePath.isChecked(), self.settings.renderPath, self.renderOverrideRes.isChecked(), self.renderTransparency.isChecked()))
        
    def updateFrame(self):
        if not self.isLayoutEnabled(self.updateButtonLayout):
            return
        self.server.sendMessage(('requestFrame', True))
        
    def updateAnimation(self, render = False):
        if not self.isLayoutEnabled(self.renderButtonLayout if render else self.updateButtonLayout):
            return
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
    
        dialog = QDialog(Application.activeWindow().qwindow())
        dialog.setWindowTitle(i18n("Render Animation") if render else i18n("Update Animation"))
        buttonBox = QDialogButtonBox()
        buttonBox.setOrientation(QtCore.Qt.Horizontal)
        buttonBox.setStandardButtons(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(dialog.accept)
        buttonBox.rejected.connect(dialog.reject)
        
        overrideGroupBox = QGroupBox(i18n("Override Blender's settings"))
        overrideGroupBox.setToolTip(i18n("Override Blender's timeline settings"))
        overrideGroupBox.setCheckable(True)

        frameRateSpinBox = QSpinBox()
        frameRateSpinBox.setRange(0, 120)
        frameRateSpinBox.setValue(d.framesPerSecond())
        frameRateSpinBox.setSuffix(i18n(" fps"))
        
        clipStartSpinBox = QSpinBox()
        clipStartSpinBox.setRange(0, 10000)
        clipStartSpinBox.setValue(d.fullClipRangeStartTime())
        
        clipEndSpinBox = QSpinBox()
        clipEndSpinBox.setRange(0, 10000)
        clipEndSpinBox.setValue(d.fullClipRangeEndTime())
        
        stepSpinBox = QSpinBox()
        stepSpinBox.setRange(0, 10000)
        stepSpinBox.setValue(1)

        temporaryCheck = QCheckBox(i18n("Only apply temporarily"))
        temporaryCheck.setToolTip(i18n("Settings will be reverted once the animation is done"))
        temporaryCheck.setChecked(True)

        overrideForm = QFormLayout()
        overrideForm.addRow(i18n("Clip Start:"), clipStartSpinBox)
        overrideForm.addRow(i18n("Clip End:"), clipEndSpinBox)
        overrideForm.addRow(i18n("Step:"), stepSpinBox)
        overrideForm.addRow(i18n("Framerate:"), frameRateSpinBox)
        overrideForm.addRow(temporaryCheck)
        overrideGroupBox.setLayout(overrideForm)
        
        overrideKritaCheck = QCheckBox(i18n("Adjust Krita's settings to match Blender's"))
        overrideKritaCheck.setToolTip(i18n("Krita's clip settings will be set to Blender's timeline settings"))
        overrideKritaCheck.setChecked(True)

        vbox = QVBoxLayout(dialog)
        vbox.addWidget(overrideGroupBox)
        vbox.addWidget(overrideKritaCheck)
        vbox.addStretch(1)
        vbox.addWidget(buttonBox)
        vbox.setSizeConstraint(QLayout.SetFixedSize)
        dialog.show()
        dialog.activateWindow()
        if dialog.exec_() == QDialog.Accepted:
            self.update.setCurrentIndex(2)
            if render:
                self.server.sendMessage(('renderAnimation', self.renderOverride.isChecked(), self.renderTemporary.isChecked(), self.renderOverridePath.isChecked(), self.settings.renderPath, self.renderOverrideRes.isChecked(), self.renderTransparency.isChecked(),
                overrideGroupBox.isChecked(), temporaryCheck.isChecked(), overrideKritaCheck.isChecked(), frameRateSpinBox.value(), clipStartSpinBox.value(), clipEndSpinBox.value(), stepSpinBox.value()))
            else:      
                self.server.sendMessage(('requestAnimation', overrideGroupBox.isChecked(), temporaryCheck.isChecked(), overrideKritaCheck.isChecked(), frameRateSpinBox.value(), clipStartSpinBox.value(), clipEndSpinBox.value(), stepSpinBox.value()))
            self.progress.setRange(0, 0)
            self.progress.show()
            self.setLayoutEnabled(self.renderButtonLayout, False)
            self.setLayoutEnabled(self.updateButtonLayout, False)
            
    def updateTexture(self):
        if not self.textureUpdate.isEnabled:
            return
        self.server.requestTexture = 2 if self.settings.viewMode == 5 else 1
            
    def saveFilenameToLayer(self, fileName, overwrite = True):
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        if not d or not d.rootNode():
            return
        l = d.nodeByName(self.settings.layerName)
        if l == None or l == 0:
            l = d.createNode(self.settings.layerName, 'paintLayer')
            d.rootNode().addChildNode(l, None)

        name = fileName
        if name and self.settings.relPath and self.activeInFile:
            name = os.path.relpath(name, os.path.dirname(self.activeInFile))
            
        if len(l.childNodes()) == 0:
            l2 = d.createSelectionMask(name)
            s = Selection()
            s.select(0, 0, d.width(), d.height(), 255)
            l2.setSelection(s)
            l.addChildNode(l2, None)
        elif overwrite:
            l2 = l.childNodes()[0].setName(name)
     
    def getFilenameFromLayer(self):  
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        l = d.nodeByName(self.settings.layerName)
        if l and l != 0 and len(l.childNodes()) > 0:              
            name = l.childNodes()[0].name()
            if self.settings.relPath and self.activeInFile:
                rel = os.path.join(os.path.dirname(self.activeInFile), name)
                if os.path.os.path.isfile(rel):
                    name = rel
            return name
        return ''
        
    def resetRegion(self, b):
        self.settings.region = b
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        w = d.width()
        h = d.height()

        self.regionX.setRange(-w, w)
        self.regionY.setRange(-h, h)
        self.regionWidth.setRange(1, w)
        self.regionHeight.setRange(1, h)
        
        if b:
            self.getRegionFromLayer()
        else:
            self.saveRegionToLayer()
            self.regionX.setValue(0)
            self.regionY.setValue(0)
            self.regionWidth.setValue(w)
            self.regionHeight.setValue(h)
            
        self.regionChanged()

    def regionFromSelection(self):
        select = instance.activeDocument().selection()
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        if select:
            self.regionX.setValue(select.x())
            self.regionY.setValue(select.y())
            self.regionWidth.setValue(select.width())
            self.regionHeight.setValue(select.height())
        else:
            self.regionX.setValue(0)
            self.regionY.setValue(0)
            self.regionWidth.setValue(d.width())
            self.regionHeight.setValue(d.height())

    def saveRegionToLayer(self):
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        if not d or not d.rootNode():
            return
        x = self.regionX.value()
        y = self.regionY.value()
        w = self.regionWidth.value()
        h = self.regionHeight.value()
        v = self.regionViewport.isChecked()
        if x != 0 or y != 0 or w != d.width() or h != d.height() or not v:
            self.saveFilenameToLayer('', False)       
        l = d.nodeByName(self.settings.layerName)
        if l != None and l != 0 and len(l.childNodes()) > 0 and l.childNodes()[0].type() == 'selectionmask':
            s = Selection()
            s.select(x, y, w, h, 255 if v else 127)
            l.childNodes()[0].setSelection(s)
                
    def getRegionFromLayer(self):
        d = self.activeDocument if self.activeDocument else instance.activeDocument()
        l = d.nodeByName(self.settings.layerName)
        if l != None and l != 0 and len(l.childNodes()) > 0 and l.childNodes()[0].type() == 'selectionmask':
            select = l.childNodes()[0].selection()
            if select:
                self.regionX.setValue(select.x())
                self.regionY.setValue(select.y())
                self.regionWidth.setValue(select.width())
                self.regionHeight.setValue(select.height())
                self.regionViewport.setChecked(select.pixelData(select.x(), select.y(), 1, 1)[0] != b'\x7f')
            else:
                self.regionX.setValue(0)
                self.regionY.setValue(0)
                self.regionWidth.setValue(d.width())
                self.regionHeight.setValue(d.height())
        else:
            self.regionX.setValue(0)
            self.regionY.setValue(0)
            self.regionWidth.setValue(d.width())
            self.regionHeight.setValue(d.height())
        
    def regionChanged(self, v = 0):
        self.settings.regionX = self.regionX.value()
        self.settings.regionY = self.regionY.value()
        self.settings.regionWidth  = self.regionWidth.value()
        self.settings.regionHeight = self.regionHeight.value()
        self.settings.regionViewport = self.regionViewport.isChecked()
        if self.server and self.server.running:
            self.server.sendMessage(('region', self.settings.regionX, self.settings.regionY, self.settings.regionWidth, self.settings.regionHeight, self.settings.regionViewport))
            
    def updateCyclesWarning(self, engine, shading):
        self.settings.engine = engine
        self.settings.shading = shading
        self.cyclesWarning.setVisible(engine == 'CYCLES' and shading == 3)            

    def textureChanged(self, index):
        d = instance.activeDocument()
        k = instance.activeDocument().rootNode().uniqueId()

        if index == 0:
            if k in self.settings.documentTextureMap:
                self.settings.documentTextureMap.pop(k)
        elif index == 1:
            name, ok = QInputDialog.getText(self, i18n("New Texture"), i18n("Name"), QLineEdit.Normal, self.settings.layerName)
		
            if ok:
                self.server.sendMessage(('newTexture', name, d.width(), d.height(), True, False, d.fileName()))
                self.settings.documentTextureMap[k] = name
            else:
                self.textures.setCurrentIndex(0)     
        elif index >= 2:
            self.settings.documentTextureMap[k] = self.settings.textures[index - 2][0]

    def viewModeChanged(self, index, fromClient = False):
        self.settings.viewMode = index
        if self.server and self.server.running and not fromClient:
            self.server.sendMessage(('viewMode', index))
        self.setLayoutVisible(self.updateLayout, index != 3 and index != 4)
        self.updateGroup.setVisible(index != 3 and index != 4)
        self.setLayoutVisible(self.texUpdateLayout, index > 3)
        self.texUpdateGroup.setVisible(index > 3)
        self.viewGroup.setVisible(index < 3 or index == 5)
        self.libraryGroup.setVisible(index < 3)
        self.renderGroup.setVisible(index == 3)
        self.uvGroup.setVisible(index == 4)
        self.projGroup.setVisible(index == 5)
        self.setLayoutVisible(self.currentViewLayout, index < 2 or index == 5)
        self.setLayoutVisible(self.cameraLayout, index < 2 or index == 5)
        
        d = instance.activeDocument()
        
        if not d or not d.rootNode():
            return
        l2 = d.nodeByName(self.settings.projLayerName)
        if index == 5 and (l2 == None or l2 == 0 or l2.index() == -1):
            l2 = d.createNode(self.settings.projLayerName, 'paintLayer')
            d.rootNode().addChildNode(l2, None)   
        elif index != 5 and not (l2 == None or l2 == 0 or l2.index() == -1):
            l2.remove()
            
    def updateModeChanged(self, index, fromClient = False):
        self.settings.updateMode = index
        if self.server and self.server.running and not fromClient:
            self.server.sendMessage(('updateMode', index))
        if self.updateRate.isVisible() and index != 0:
            self.updateForm.removeWidget(self.updateRate)
            self.updateForm.removeWidget(self.updateRateLabel)
        elif not self.updateRate.isVisible() and index == 0:
            self.updateForm.insertRow(0, self.updateRateLabel, self.updateRate)
        self.updateRate.setVisible(index == 0)
        self.updateRateLabel.setVisible(index == 0)
        self.updateSeperator.setVisible(index != 0)
        self.manualWarning.setVisible(index == 3)
        self.setLayoutVisible(self.updateButtonLayout, index != 0)
            
    def setLayoutVisible(self, layout, visible):
        for i in range(layout.count()): 
            item = layout.itemAt(i)
            if item.layout():
                self.setLayoutVisible(item.layout(), visible)
            elif item.widget():
                item.widget().setVisible(visible)
                
    def setLayoutEnabled(self, layout, enabled):
        for i in range(layout.count()): 
            item = layout.itemAt(i)
            if item.layout():
                self.setLayoutEnabled(item.layout(), enabled)
            elif item.widget():
                item.widget().setEnabled(enabled)              
    
    def isLayoutEnabled(self, layout):
        if layout.count() == 0:
            return False
        elif layout.itemAt(0).widget():
            return layout.itemAt(0).widget().isEnabled()
        else:
            return self.isLayoutEnabled(layout.itemAt(0).layout())
        
    def readSettings(self):        
        self.settings.blenderPath = instance.readSetting('blender_layer', 'blenderPath', '')
        self.settings.renderPath = instance.readSetting('blender_layer', 'renderPath', '/tmp/BlenderLayer/')
        self.settings.layerName = instance.readSetting('blender_layer', 'layerName', 'Blender Layer')
        self.settings.projLayerName = instance.readSetting('blender_layer', 'projLayerName', 'Projection')
        self.settings.uvLayerName = instance.readSetting('blender_layer', 'uvLayerName', 'UV Layout')
        self.settings.relPath = instance.readSetting('blender_layer', 'relPath', 'True') == 'True'
        self.settings.navigateAlt = instance.readSetting('blender_layer', 'navigateAlt', 'True') == 'True'

        libraryStr = instance.readSetting('blender_layer', 'library', 'Body-chan\\\\library/bodychan-bodykun.blend\\\\Collection/BodyChan;Action/Standing;Action/Jumping////Body-kun\\\\library/bodychan-bodykun.blend\\\\Collection/BodyKun;Action/Standing////Monkey\\\\library/default.blend\\\\Object/Suzanne////Cube\\\\library/default.blend\\\\Object/Cube')
        
        self.settings.host = instance.readSetting('blender_layer', 'host', '127.0.0.1')
        portStr = instance.readSetting('blender_layer', 'port', '')
        portTriesStr = instance.readSetting('blender_layer', 'portTries', '')
        timeoutStr = instance.readSetting('blender_layer', 'timeout', '')
        self.settings.sharedMem = instance.readSetting('blender_layer', 'sharedMem', 'True') == 'True'

        self.settings.assistantsThreePoint = instance.readSetting('blender_layer', 'assistantsThreePoint', 'True') == 'True'
        self.settings.assistantsAxis = instance.readSetting('blender_layer', 'assistantsAxis', 'True') == 'True'
        self.settings.assistantsRegion = instance.readSetting('blender_layer', 'assistantsRegion', 'True') == 'True'
        self.settings.cursorColor = instance.readSetting('blender_layer', 'cursorColor', '#FFFF00')

        self.settings.overrideSRGB = instance.readSetting('blender_layer', 'overrideSRGB', 'True') == 'True'
        self.settings.colorManageBlender = instance.readSetting('blender_layer', 'colorManageBlender', 'True') == 'True'
        self.settings.convertBGR = instance.readSetting('blender_layer', 'convertBGR', 'True') == 'True'
       
        self.settings.backgroundDraw = instance.readSetting('blender_layer', 'backgroundDraw', 'False') == 'True'
        lockFramesStr = instance.readSetting('blender_layer', 'lockFrames', '')

        self.settings.uvCursor = instance.readSetting('blender_layer', 'uvCursor', 'True') == 'True'

        try:
            self.settings.port = int(portStr)
        except ValueError:
            self.settings.port = 65432
        try:
            self.settings.portTries = int(portTriesStr)
        except ValueError:
            self.settings.portTries = 10
        try:
            self.settings.timeout = float(timeoutStr)
        except ValueError:
            self.settings.timeout = 10.0
                
        try:
            lib = []
            for e in libraryStr.split('////'):
                s = e.split('\\\\')
                lib.append((s[0], s[1], s[2]))
            self.settings.library = lib
        except IndexError:
            self.settings.library = []

        try:
            self.settings.lockFrames = int(lockFramesStr)
        except ValueError:
            self.settings.lockFrames = 10
            
    def writeSettings(self):
        instance.writeSetting('blender_layer', 'blenderPath', self.settings.blenderPath)
        instance.writeSetting('blender_layer', 'renderPath', self.settings.renderPath)
        instance.writeSetting('blender_layer', 'layerName', self.settings.layerName)
        instance.writeSetting('blender_layer', 'uvLayerName', self.settings.uvLayerName)
        instance.writeSetting('blender_layer', 'projLayerName', self.settings.projLayerName)
        instance.writeSetting('blender_layer', 'relPath', str(self.settings.relPath))
        instance.writeSetting('blender_layer', 'navigateAlt', str(self.settings.navigateAlt))
        instance.writeSetting('blender_layer', 'library', '////'.join([name + '\\\\' + file + '\\\\' + innerpath for (name, file, innerpath) in self.settings.library]))
        instance.writeSetting('blender_layer', 'host', self.settings.host)
        instance.writeSetting('blender_layer', 'port', str(self.settings.port))
        instance.writeSetting('blender_layer', 'portTries', str(self.settings.portTries))
        instance.writeSetting('blender_layer', 'timeout', str(self.settings.timeout))
        instance.writeSetting('blender_layer', 'sharedMem', str(self.settings.sharedMem))
        instance.writeSetting('blender_layer', 'assistantsThreePoint', str(self.settings.assistantsThreePoint))
        instance.writeSetting('blender_layer', 'assistantsAxis', str(self.settings.assistantsAxis))
        instance.writeSetting('blender_layer', 'assistantsRegion', str(self.settings.assistantsRegion))
        instance.writeSetting('blender_layer', 'cursorColor', str(self.settings.cursorColor))
        instance.writeSetting('blender_layer', 'overrideSRGB', str(self.settings.overrideSRGB))
        instance.writeSetting('blender_layer', 'colorManageBlender', str(self.settings.colorManageBlender))
        instance.writeSetting('blender_layer', 'convertBGR', str(self.settings.convertBGR))
        instance.writeSetting('blender_layer', 'backgroundDraw', str(self.settings.backgroundDraw))
        instance.writeSetting('blender_layer', 'lockFrames', str(self.settings.lockFrames))
        instance.writeSetting('blender_layer', 'uvCursor', str(self.settings.uvCursor))
