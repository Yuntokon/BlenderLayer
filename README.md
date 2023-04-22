# Blender Layer
## Getting Started
 - Install Blender
 - Make sure the 'Blender Layer' add-on is installed and enabled in Krita
 - Open a document
 - Connect to Blender:
   - **Drag and drop** a .blend File into Krita. This will start Blender, connect and directly open the .blend file.
(You might be asked to select the path to the blender executable. Under Windows and Blender 3.4 for example select 'C:\Program Files\Blender Foundation\Blender 3.4\blender.exe')
   - Or use the **'Start Blender'** button in the 'Blender Layer' Docker. This will start Blender with a fresh file and connect automatically.
   - (Advanced) Or manually install the **companion add-on** for Blender. Goto 'Preferences → Add-Ons → Install...' and select **'blenderLayerClient.py'** from the blender_layer folder of the add-on. (Under Windows installed Krita add-ons can be found in '%APPDATA%\krita\pykrita'). In Krita, start the server from the docker, then in Blender from the header of a 3D View choose 'View → Connect to Krita' make sure host and port match the settings in Krita and press ok.
## Usage
### Navigation
Using the navigation widget in the docker:
 - **Drag** to rotate
 - **Shift + Drag** to pan
 - **Ctrl + Drag** vertically to zoom
 
You can also change the view from outside the docker with **Alt + Middle Mouse Button**.
#### Assistants
In order to create painting assistants matching Blender's perspective use the **'Create Assistant Set'** button in the docker. You will be asked to save an xml file. Make sure the assistant tool is selected, then open the Tool Settings and press the **'Load Assistant Set'** Button (Folder icon) and select the file you have just created.
The colored axis can be disabled in settings.
#### Library
The docker allows you to append a selection of objects to the current scene. By default, this includes posable mannequin models 'Body-chan' and 'Body-kun'. (CC-0 License, created by [vinchau](https://blendswap.com/blend/23521)).
You can configure additional objects from the settings menu by providing a .blend file and the path to objects within the file e.g. Object/Cube. (See Blender's [append](https://docs.blender.org/manual/en/latest/files/linked_libraries/link_append.html) function for reference)

Blender Layer also supports Blender's asset based [Pose Library](https://docs.blender.org/manual/en/latest/animation/armatures/posing/editing/pose_library.html). If your Blender file contains pose assets, these will show up in the 'Library' section of the docker and can be directly applied from within Krita.
