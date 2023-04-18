from krita import DockWidgetFactory, DockWidgetFactoryBase
from .blenderLayer import BlenderLayer

Krita.instance().addDockWidgetFactory(DockWidgetFactory('blender_layer', DockWidgetFactoryBase.DockRight, BlenderLayer))