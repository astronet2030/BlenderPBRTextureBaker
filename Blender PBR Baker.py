bl_info = {
    "name": "PBR Texture Baker",
    "author": "Godwin Jimoh(Astronet)",
    "version": (1, 1, 2),
    "blender": (4, 4, 3),
    "location": "View3D > Sidebar > Bake Tab",
    "description": "Bake multiple maps across selected objects using shared UVs",
    "category": "Object",
}

import bpy
import os
import time
import blf
import gpu


# Define bake map configuration
BAKE_MAPS = {
    "Base Color": {
        "socket": "Base Color",
        "bake_type": "EMIT",
        "color_space": "sRGB",
        "fallback": "color"
    },
    "Roughness": {
        "socket": "Roughness",
        "bake_type": "EMIT",
        "color_space": "Non-Color",
        "fallback": "scalar"
    },
    "Metallic": {
        "socket": "Metallic",
        "bake_type": "EMIT",
        "color_space": "Non-Color",
        "fallback": "scalar"
    },
    "Specular": {
        "socket": "Specular IOR Level",
        "bake_type": "EMIT",
        "color_space": "Non-Color",
        "fallback": "scalar"
    },
    "Alpha": {
        "socket": "Alpha",
        "bake_type": "EMIT",
        "color_space": "sRGB",
        "fallback": "scalar"
    },
    "Normal": {
        "socket": "Normal",
        "bake_type": "NORMAL",
        "color_space": "sRGB",
        "fallback": "flat_normal"
    }
}

def create_image(name, width, height, color_space):
    image = bpy.data.images.new(name, width=width, height=height)
    image.colorspace_settings.name = color_space
    return image

def insert_image_node(material, image):
    nodes = material.node_tree.nodes
    tex_node = nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    nodes.active = tex_node
    tex_node.select = True
    return tex_node


def setup_emission(material, socket_name, fallback):
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    if not principled or not output:
        return None

    original_surface_link = None
    if output.inputs['Surface'].is_linked:
        original_surface_link = output.inputs['Surface'].links[0].from_socket

    input_socket = principled.inputs.get(socket_name)

    original_input_link = None
    if input_socket and input_socket.is_linked:
        original_input_link = input_socket.links[0].from_socket

    emission = nodes.new("ShaderNodeEmission")

    if input_socket and input_socket.is_linked:
        links.new(original_input_link, emission.inputs["Color"])
        emission.inputs["Strength"].default_value = 1.0
    else:
        if fallback == "color":
            color = input_socket.default_value[:3] if hasattr(input_socket, 'default_value') else (1.0, 1.0, 1.0)
            emission.inputs["Color"].default_value = (*color, 1.0)
            emission.inputs["Strength"].default_value = 1.0
        elif fallback == "scalar":
            value = input_socket.default_value if hasattr(input_socket, 'default_value') else 1.0
            emission.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            emission.inputs["Strength"].default_value = value
        elif fallback == "flat_normal":
            emission.inputs["Color"].default_value = (0.5, 0.5, 1.0, 1.0)
            emission.inputs["Strength"].default_value = 1.0

    links.new(emission.outputs["Emission"], output.inputs["Surface"])

    return {
        "emission": emission,
        "original_surface_link": original_surface_link,
        "material_output": output,
        "temporary": [emission]
    }

def bake_map_for_object(obj, map_config, image, bake_type):
    # Set object as active and select it exclusively
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)

    # Store cleanup information for all materials
    all_materials_cleanup_info = []

    # --- SETUP PHASE ---
    # Prepare all materials on the object for baking
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes:
            continue

        # CRITICAL FIX: Deselect all nodes to prevent baking to an existing texture
        for node in mat.node_tree.nodes:
            node.select = False

        # Insert the new image node that we will bake to.
        image_node = insert_image_node(mat, image)
        
        cleanup_info = {"material": mat, "image_node": image_node, "temporary": []}

        if bake_type == "EMIT":
            state = setup_emission(mat, map_config["socket"], map_config["fallback"])
            if state:
                cleanup_info.update(state)
        
        all_materials_cleanup_info.append(cleanup_info)

    # --- BAKE PHASE ---
    # With all materials prepared, perform the bake operation ONCE.
    if all_materials_cleanup_info:
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.bake_type = bake_type
        scene.render.bake.use_clear = False
        scene.render.bake.target = 'IMAGE_TEXTURES'
        bpy.ops.object.bake(type=bake_type)

    # --- CLEANUP PHASE ---
    # Restore all materials to their original state
    for cleanup in all_materials_cleanup_info:
        mat = cleanup["material"]
        links = mat.node_tree.links

        if cleanup.get("material_output") and cleanup.get("original_surface_link"):
            output = cleanup["material_output"]
            if output.inputs['Surface'].is_linked:
                links.remove(output.inputs['Surface'].links[0])
            links.new(cleanup["original_surface_link"], output.inputs["Surface"])

        for node in cleanup.get("temporary", []):
            mat.node_tree.nodes.remove(node)
        if cleanup.get("image_node"):
            mat.node_tree.nodes.remove(cleanup["image_node"])


class BatchBakeProps(bpy.types.PropertyGroup):
    base_color: bpy.props.BoolProperty(name="Base Color", default=True)
    roughness: bpy.props.BoolProperty(name="Roughness", default=True)
    metallic: bpy.props.BoolProperty(name="Metallic", default=True)
    specular: bpy.props.BoolProperty(name="Specular", default=True)
    alpha: bpy.props.BoolProperty(name="Alpha", default=False)
    normal: bpy.props.BoolProperty(name="Normal", default=True)

    resolution_x: bpy.props.IntProperty(name="Width", default=4096, min=1)
    resolution_y: bpy.props.IntProperty(name="Height", default=4096, min=1)

    naming_convention: bpy.props.EnumProperty(
        name="File Naming",
        description="How to use the blend file name for the output textures",
        items=[
            ('PREFIX', "Prefix", "e.g., MyFile_BaseColor"),
            ('SUFFIX', "Suffix", "e.g., BaseColor_MyFile"),
            ('BOTH', "Prefix and Suffix", "e.g., MyFile_BaseColor_MyFile"),
            ('MAP_ONLY', "Map Name Only", "e.g., BaseColor")
        ],
        default='SUFFIX'
    )
    output_path: bpy.props.StringProperty(
        name="Output Path",
        default="//../FIN/TEXTURES/",
        subtype='DIR_PATH'
    )

class OBJECT_OT_BakeAllMaps(bpy.types.Operator):
    bl_idname = "object.bake_all_maps"
    bl_label = "Bake Selected Maps"

    _timer = None
    _objects = []
    _maps = {}
    _images_to_remove = []
    
    map_index: int
    object_index: int
    start_time: float
    
    current_image = None
    draw_handle = None
    is_baking = False

    def modal(self, context, event):
        if event.type == 'ESC' or not self.is_baking:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            if self.map_index >= len(self.map_keys):
                self.finish(context)
                return {'FINISHED'}
            
            self.execute_step(context)
            # After a step, always redraw to update the UI
            if context.area:
                context.area.tag_redraw()

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        props = context.scene.bake_props
        self._objects = [o for o in context.selected_objects if o.type == 'MESH']
        self._maps = {k: v for k, v in BAKE_MAPS.items() if getattr(props, k.lower().replace(" ", "_"))}
        self.map_keys = list(self._maps.keys())

        if not self._objects or not self._maps:
            self.report({'WARNING'}, "No objects selected or no maps to bake.")
            return {'CANCELLED'}

        self.map_index = 0
        self.object_index = 0
        self.start_time = time.time()
        self.is_baking = True
        self._images_to_remove = []

        # A slightly longer timer gives the UI a moment to refresh between bakes
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback, (context,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        
        # Trigger an initial redraw to show the UI immediately
        if context.area:
            context.area.tag_redraw()
            
        return {'RUNNING_MODAL'}

    def execute_step(self, context):
        props = context.scene.bake_props
        
        # If starting a new map, create the image for it
        if self.object_index == 0:
            map_key = self.map_keys[self.map_index]
            map_data = self._maps[map_key]
            blend_name = bpy.path.basename(bpy.data.filepath).replace(".blend", "") if bpy.data.filepath else "untitled"

            naming = props.naming_convention
            if naming == 'SUFFIX':
                image_name = f"{map_key}_{blend_name}"
            elif naming == 'PREFIX':
                image_name = f"{blend_name}_{map_key}"
            elif naming == 'BOTH':
                image_name = f"{blend_name}_{map_key}_{blend_name}"
            else: # MAP_ONLY
                image_name = map_key
            
            self.current_image = create_image(image_name, props.resolution_x, props.resolution_y, map_data["color_space"])
            self._images_to_remove.append(self.current_image)

        # Process one object
        obj = self._objects[self.object_index]
        map_key = self.map_keys[self.map_index]
        map_data = self._maps[map_key]
        
        bake_map_for_object(obj, map_data, self.current_image, map_data["bake_type"])

        # Move to the next object
        self.object_index += 1

        # If all objects for the current map are done, save the image and move to the next map
        if self.object_index >= len(self._objects):
            out_path = bpy.path.abspath(props.output_path)
            os.makedirs(out_path, exist_ok=True)
            self.current_image.filepath_raw = os.path.join(out_path, self.current_image.name + ".png")
            self.current_image.file_format = 'PNG'
            self.current_image.save()
            
            self.object_index = 0
            self.map_index += 1

    def finish(self, context):
        self.cleanup(context)
        total_time = time.time() - self.start_time
        self.report({'INFO'}, f"Baking complete in {total_time:.2f} seconds.")
        context.workspace.status_text_set(f"Baking complete in {total_time:.2f} seconds.")

    def cancel(self, context):
        self.cleanup(context)
        self.report({'INFO'}, "Baking cancelled.")
        context.workspace.status_text_set("Baking cancelled.")

    def cleanup(self, context):
        self.is_baking = False
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
        if self.draw_handle:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
        for img in self._images_to_remove:
            if img: bpy.data.images.remove(img, do_unlink=True)
        
        # Final redraw to clear the baking text
        if context.area:
            context.area.tag_redraw()

    def draw_callback(self, context):
        if not self.is_baking:
            return

        # Ensure we are in a 3D View
        if context.area.type != 'VIEW_3D':
            return

        font_id = 0
        
        # --- UI Scaling and Font Setup ---
        res_scale = context.preferences.system.ui_scale
        font_size = int(16 * res_scale) 
        blf.size(font_id, font_size)
        line_height = blf.dimensions(font_id, "M")[1] * 1.5
        
        # --- Gather Progress Information ---
        total_steps = len(self._maps) * len(self._objects)
        current_step = self.map_index * len(self._objects) + self.object_index
        progress = current_step / total_steps if total_steps > 0 else 0
        elapsed_time = time.time() - self.start_time
        
        map_key = self.map_keys[self.map_index] if self.map_index < len(self.map_keys) else "Done"
        obj_name = ""
        if self.map_index < len(self.map_keys) and self.object_index < len(self._objects):
            obj_name = self._objects[self.object_index].name

        # --- Build Text Lines ---
        lines = []
        if map_key != "Done":
            lines.append(f"Baking... [{progress:.0%}]")
            lines.append(f"Map: {map_key} ({self.map_index + 1}/{len(self._maps)})")
            if obj_name:
                lines.append(f"Object: {obj_name} ({self.object_index + 1}/{len(self._objects)})")
            lines.append(f"Elapsed Time: {elapsed_time:.1f}s")
        else:
            lines.append("Finishing up...")

        # --- Draw Text Centered ---
        region_width = context.region.width
        
        # Calculate y position for the first (top) line, starting from the bottom up
        start_y = (len(lines) * line_height) + (40 * res_scale)

        for i, text in enumerate(lines):
            text_width, _ = blf.dimensions(font_id, text)
            x_pos = (region_width - text_width) / 2
            y_pos = start_y - (i * line_height)
            
            blf.position(font_id, x_pos, y_pos, 0)
            blf.draw(font_id, text)

        # --- Update Workspace Status Bar ---
        if map_key != "Done" and obj_name:
            context.workspace.status_text_set(f"Baking {map_key} for {obj_name}... {progress:.0%}")
        else:
            context.workspace.status_text_set(f"Baking... [{progress:.0%}]")

class OBJECT_PT_BakePanel(bpy.types.Panel):
    bl_label = "Texture Map Baker"
    bl_idname = "OBJECT_PT_texture_baker"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Bake'

    def draw(self, context):
        layout = self.layout
        props = context.scene.bake_props

        col = layout.column()
        col.label(text="Bake Maps:")
        for key in BAKE_MAPS:
            col.prop(props, key.lower().replace(" ", "_"))

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Resolution:")
        col.prop(props, "resolution_x", text="Width")
        col.prop(props, "resolution_y", text="Height")
        
        layout.separator()
        col = layout.column()
        col.prop(props, "output_path")
        col.prop(props, "naming_convention")

        layout.separator()
        layout.operator("object.bake_all_maps", icon='RENDER_STILL')


classes = (
    BatchBakeProps,
    OBJECT_OT_BakeAllMaps,
    OBJECT_PT_BakePanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bake_props = bpy.props.PointerProperty(type=BatchBakeProps)

def unregister():
    del bpy.types.Scene.bake_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()