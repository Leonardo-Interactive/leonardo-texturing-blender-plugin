bl_info = {
    "name" : "Leonardo Blender Plugin",
    "description" : "Texturize your 3D models with Leonardo.ai",
    "author" : "Leonardo.ai",
    "version" : (1, 0, 0),
    "blender" : (2, 80, 0),
    "location" : "View3D > Sidebar > Leonardo",
    "warning" : "",
    "doc_url" : "",
    "tracker_url" : "",
    "support": "COMMUNITY",
    "category" : "Texturing",
}

import bpy
import os
import asyncio
import requests
import functools
import shutil
import json
from . import async_computation
from bpy.app.handlers import persistent


async def get_user_id():
    url = "https://cloud.leonardo.ai/api/rest/v1/me"
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {bpy.context.preferences.addons[__name__].preferences.api_key}"
    }

    get_id = functools.partial(requests.get, url, headers=headers)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, get_id)
    user_id = result.json()['user_details'][0]['user']['id']
    return user_id


async def get_list_of_user_meshes(user_id):
    url = f"https://cloud.leonardo.ai/api/rest/v1/models-3d/user/{user_id}"

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {bpy.context.preferences.addons[__name__].preferences.api_key}"
    }

    partial = functools.partial(requests.get, url, headers=headers)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial)
    user_meshes = [{'name': mesh['name'], 'id': mesh['id']} for mesh in result.json()['model_assets']]

    return user_meshes


async def get_presigned_post_for_mesh_file(context):

    filename = bpy.data.filepath.split('/')[-1].split('.')[0] if bpy.data.filepath.split('/')[-1].split('.')[0] != "" else "Untitled"
    url = "https://cloud.leonardo.ai/api/rest/v1/models-3d/upload"

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {bpy.context.preferences.addons[__name__].preferences.api_key}"
    }

    payload = {
        "name": context.scene.leonardo_tools.mesh_name_input if context.scene.leonardo_tools.mesh_name_input else filename,
        "modelExtension": "obj"
    }
    partial = functools.partial(requests.post, url, json=payload, headers=headers)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial)
    return result

async def upload_mesh_file(presigned_post, file_path):
    presigned_post = presigned_post.json()['uploadModelAsset']
    modelId = presigned_post['modelId']
    url = presigned_post['modelUrl']
    fields = json.loads(presigned_post['modelFields'])
    files = {'file': open(file_path, 'rb')},

    #Upload file to S3 using presigned URL
    partial = functools.partial(requests.post, url, data=fields, files=files[0])
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial)
    if result.status_code == 204:
        print("File uploaded successfully!")
        return modelId
    else:
        print("File upload failed!")
        return False


def register_project_path(context):
    project_path = os.path.dirname(bpy.data.filepath)

    if not project_path:
        project_path = os.path.normpath(os.path.expanduser("~/Desktop"))

    sub_directory = "leonardo_tmp"
    path = os.path.join(project_path, sub_directory)
    context.scene.result_path = path
    

    if not os.path.exists(path):
        os.makedirs(path)

    export_path = os.path.join(path, "tmp.obj")
    context.scene.leonardo_tools.obj_export_path = export_path


def export_scene_as_tmp_objs(context):
    project_path = os.path.dirname(bpy.data.filepath)

    if not project_path:
        project_path = os.path.normpath(os.path.expanduser("~/Desktop"))

    sub_directory = "leonardo_tmp"
    path = os.path.join(project_path, sub_directory)
    context.scene.result_path = path

    if not os.path.exists(path):
        os.makedirs(path)

    export_path = os.path.join(path, "tmp.obj")
    context.scene.leonardo_tools.obj_export_path = export_path
    
    export_options = {
        'use_selection': True,
        'global_scale': 1.0,
    }

    bpy.ops.export_scene.obj(filepath=export_path, **export_options)


async def submit_texture_generation(context, args={}):
    register_project_path(context)
    selected_objs = []
        
    selected_objects = bpy.context.selected_objects
    for obj in selected_objects:
        if obj not in selected_objs:
            selected_objs.append(obj)
    
    scene = context.scene
    scene["selected_objs"] = selected_objs
        
    params = { 
        "prompt": context.scene.leonardo_tools.prompt_input,
        'front_rotation_offset': float(context.scene.leonardo_tools.obj_direction),
        'sd_version': context.scene.leonardo_tools.model_version,
        'modelAssetId': context.scene.leonardo_tools.current_mesh_id,
    }
    
    params.update(args)

    if int(context.scene.leonardo_tools.seed_input) > 0:
        params.update({"seed": int(context.scene.leonardo_tools.seed_input)})
    
    if context.scene.leonardo_tools.negative_prompt_input:
        params["negative_prompt"] = context.scene.leonardo_tools.negative_prompt_input

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {bpy.context.preferences.addons[__name__].preferences.api_key}"
    }

    # TODO make this a real API call with no mockup params
    partial = functools.partial(requests.post, 'https://cloud.leonardo.ai/api/rest/v1/generations-texture', json=params, headers=headers)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial)
    print(f"Texture Result: {result.json()}")
    
    if result.status_code == 200:
        context.scene.job_id = result.json()['textureGenerationJob'].get('id')
        context.scene.leonardo_tools.currently_running_prompt_input = context.scene.leonardo_tools.prompt_input
        print(f"Job ID: {context.scene.job_id}")
    else:
        print("Job submission failed!")
        context.scene.job_id = ""
        context.scene.leonardo_tools.currently_running_prompt_input = ""


def assign_textures_to_model(context, path):
    texture_image = None
    normalmap_image = None
    displacementmap_image = None
    roughnessmap_image = None

    for obj in context.scene["selected_objs"]:
        print(f"Assigning textures to {obj.name}")
        #create a new material
        if obj.data.materials:
            mat = obj.data.materials[0]
        else:
            mat = bpy.data.materials.new(name="New Material")
            obj.data.materials.append(mat)

        # Load the texture image
        if context.scene.leonardo_tools.albedo_path != "":
            texture_path = os.path.join(path, context.scene.leonardo_tools.albedo_path)
            texture_image = bpy.data.images.load(texture_path)

        # Load the normal map image
        if context.scene.leonardo_tools.normalmap_path != "":
            normalmap_path = os.path.join(path, context.scene.leonardo_tools.normalmap_path)
            normalmap_image = bpy.data.images.load(normalmap_path)

        # TODO: add support for displacement and roughness maps
        # Load the displacement map image
        if context.scene.leonardo_tools.displacementmap_path != "":
            displacementmap_path = os.path.join(path,  context.scene.leonardo_tools.displacementmap_path)
            displacementmap_image = bpy.data.images.load(displacementmap_path)

        # Load the roughness map image
        if context.scene.leonardo_tools.roughnessmap_path != "":
            roughnessmap_path = os.path.join(path,  context.scene.leonardo_tools.roughnessmap_path)
            roughnessmap_image = bpy.data.images.load(roughnessmap_path)
        
        # Add a new material to the object
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        
        if context.scene.leonardo_tools.albedo_path != "":
            texImage = mat.node_tree.nodes.get('Albedo', None)
            if texImage is None:
                texImage = mat.node_tree.nodes.new(type='ShaderNodeTexImage') 
                texImage.label = "Albedo"
                texImage.name = "Albedo"
            texImage.image = texture_image
            mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])
        
        if context.scene.leonardo_tools.normalmap_path != "":
            normalImage = mat.node_tree.nodes.get('NormalMap', None)
            if normalImage is None:
                normalImage = mat.node_tree.nodes.new(type='ShaderNodeTexImage')
                normalImage.image = normalmap_image
            normalImage.label = "NormalMap"
            normalImage.name = "NormalMap"
            mat.node_tree.links.new(bsdf.inputs['Normal'], normalImage.outputs['Color'])
        else:
            # TODO this doesn't work
            normalImage = mat.node_tree.nodes.get('NormalMap', None)
            if normalImage is not None:
                mat.node_tree.nodes.remove(normalImage)

        context.scene.is_running = False

async def download_file_wrapper(url, path, context):
    partial = functools.partial(download_file, url, path, context)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial)
    return result

def unset_paths(context):
    # TODO use property unset instead:
    context.scene.leonardo_tools.albedo_path = ""
    context.scene.leonardo_tools.normalmap_path = ""
    context.scene.leonardo_tools.roughnessmap_path = ""
    context.scene.leonardo_tools.displacementmap_path = ""


def download_file(url, path, context):      
    filename = url.split('/')[-1]
    dl_path = os.path.join(path, filename)
    context.scene.leonardo_tools.status_label = f"Downloading {filename}"
    print(f"Downloading {url} to {dl_path}")

    if 'albedo.jpg' in filename:
        context.scene.leonardo_tools.albedo_path = dl_path
    elif 'normal.jpg' in filename:
         context.scene.leonardo_tools.normalmap_path = dl_path
    elif 'roughness.jpg' in filename:
         context.scene.leonardo_tools.roughnessmap_path = dl_path
    elif 'displacement.jpg' in filename:
         context.scene.leonardo_tools.displacementmap_path = dl_path

    with requests.Session() as session:
        response = session.get(url, stream=True)
        if response.status_code == 200:
            with open(dl_path, 'wb') as writer:
                response.raw.decode_content = True
                shutil.copyfileobj(response.raw, writer)

    print("Done downloading file")
    return response.status_code


async def check_texture_generation_job_status(context):
    # TODO make this a real API call with no mockup params
    print(f"Checking job status for {context.scene.job_id}...")

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {bpy.context.preferences.addons[__name__].preferences.api_key}"
    }

    partial = functools.partial(requests.get, f'https://cloud.leonardo.ai/api/rest/v1/generations-texture/{context.scene.job_id}', headers=headers)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, partial)
    print(f"Response: {response}")
    return response


def make_result_dirs(context, subfolder):
    path = os.path.join(context.scene.result_path, context.scene.leonardo_tools.currently_running_prompt_input)
    if not os.path.exists(path):
        os.makedirs(path)
    final_path = os.path.join(path, subfolder)
    if not os.path.exists(final_path):
        os.makedirs(final_path)
    return final_path

async def init_texture_generation_job(context, preview=False):
        context.scene.is_running = True
        context.scene.has_returned = False

        context.scene.leonardo_tools.status_label = "Generating"
        
        await submit_texture_generation(context, args={'preview': preview, 'preview_direction': context.scene.leonardo_tools.preview_direction})
        if context.scene.job_id == "":
            context.scene.is_running = False
            context.scene.has_returned = True
            return

        generation_is_running = True
        response = None
        while generation_is_running:
            await asyncio.sleep(10)
            response = await check_texture_generation_job_status(context)
            if response.json()['model_asset_texture_generations_by_pk'].get('status') == 'COMPLETE':
                generation_is_running = False

        context.scene.leonardo_tools.status_label = "Receiving results"
        unset_paths(context)
        final_result_path = make_result_dirs(context, str(response.json()['model_asset_texture_generations_by_pk'].get('seed')))
        for image in response.json()['model_asset_texture_generations_by_pk'].get('model_asset_texture_images'):
            await download_file_wrapper(image['url'], final_result_path, context)

        print("Done downloading images!")
        
        assign_textures_to_model(context, final_result_path)
        context.scene.last_seed = response.json()['model_asset_texture_generations_by_pk'].get('seed')
        context.scene.has_returned = True

        context.scene.leonardo_tools.status_label = ""

# ------------------------------------------------------------------------
#    Operators
# ------------------------------------------------------------------------

class SavePreferences(bpy.types.Operator):
    bl_idname = "wm.leonardo_save_prefs"
    bl_label = "Save preferences"

    def execute(self, context):
        preferences = context.preferences.addons[__name__].preferences
        preferences.save_preferences()
        self.report({'INFO'}, "Preferences saved!")
        return {'FINISHED'}

class NavigateToPreferencesButton(bpy.types.Operator):
    bl_idname = "wm.navigate_to_preferences_button"
    bl_label = "Go to preferences"

    def execute(self, context):
        bpy.ops.preferences.addon_show(module=__name__)
        return {'FINISHED'}    

class TexturizeButton(bpy.types.Operator, async_computation.AsyncModalOperatorMixin):
    """Send to Leonardo API"""
    bl_idname = "wm.submit_button"
    bl_label = "Submit!"
    
    async def async_execute(self, context):
        await init_texture_generation_job(context, preview=False)
        self.quit()


class PreviewButton(bpy.types.Operator, async_computation.AsyncModalOperatorMixin):
    """Send to Leonardo API"""
    bl_idname = "wm.preview_button"
    bl_label = "Submit!"

    async def async_execute(self, context):
        await init_texture_generation_job(context, preview=True)
        self.quit()
    
    
class StopButton(bpy.types.Operator):
    """If it seems like your generation is taking too long, you can stop it here. This will make Blender stop listening to the API for updates.
    If the generation completes, it will still show up on the web app.
    """
    bl_idname = "wm.stop_button"
    bl_label = "Print"
    
    def execute(self, context):
        context.scene.is_running = False
        return {'FINISHED'}

class UploadMeshButton(bpy.types.Operator, async_computation.AsyncModalOperatorMixin):
    """Upload to Leonardo API"""
    bl_idname = "wm.upload_mesh_button"
    bl_label = "Upload selected Mesh(es)"

    async def async_execute(self, context):
        context.scene.leonardo_tools.status_label = "Uploading current mesh!"
        objects = bpy.context.selected_objects
        mesh_name = context.scene.leonardo_tools.mesh_name_input
        context.scene.is_running = True
        post = await get_presigned_post_for_mesh_file(context)    
        if post.status_code == 200:
            export_scene_as_tmp_objs(context)
            path = context.scene.leonardo_tools.obj_export_path
            mesh_id = await upload_mesh_file(post, path)
            if mesh_id:
                for obj in objects:
                    obj.data['leonardo_id'] = mesh_id
                    obj.data['leonardo_name'] = mesh_name
                if bpy.context.selected_objects == objects:
                    context.scene.leonardo_tools.current_mesh_name = mesh_name
                    context.scene.leonardo_tools.current_mesh_id = mesh_id
                context.scene.leonardo_tools.status_label = "Upload complete!"
        else:
            print("Failed to get presigned post")
            print(post.status_code)
        
        context.scene.is_running = False
        self.quit()


class QueryUserMeshesButton(bpy.types.Operator, async_computation.AsyncModalOperatorMixin):
    """Query user meshes from Leonardo API"""
    bl_idname = "wm.query_mesh_button"
    bl_label = "Get your models!"

    async def async_execute(self, context):
        user_id = await get_user_id()
        meshes = await get_list_of_user_meshes(user_id)
        context.scene.leonardo_tools.user_meshes.clear()
        for user_mesh in meshes:
            mesh = context.scene.leonardo_tools.user_meshes.add()
            mesh.id = user_mesh['id']
            mesh.name = user_mesh['name']
        self.quit()


class AddModelDataToSelectedMeshButton(bpy.types.Operator):
    """Add Leonardo mesh data to selected mesh"""
    bl_idname = "wm.add_mesh_info_button"
    bl_label = "Select!"
    def execute(self, context):
        
        selected_objects = bpy.context.selected_objects

        for user_mesh in context.scene.leonardo_tools.user_meshes:
            if user_mesh.id == context.scene.leonardo_tools.uploaded_user_meshes:
                context.scene.leonardo_tools.current_mesh_id = user_mesh.id
                context.scene.leonardo_tools.current_mesh_name = user_mesh.name
                for obj in selected_objects:
                    obj.data['leonardo_id'] = user_mesh.id
                    obj.data['leonardo_name'] = user_mesh.name
                break
    
        return {'FINISHED'}

# ------------------------------------------------------------------------
#    Properties
# ------------------------------------------------------------------------

class LeonardoTexturingToolPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    api_key: bpy.props.StringProperty(name="API Key",
                                        description="Enter your Leonardo.ai API key here",
                                        default="",
                                        maxlen=256,
                                        subtype="PASSWORD")
    
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key")
        layout.operator("wm.leonardo_save_prefs")

    def save_preferences(self):
        prefs = bpy.context.preferences.addons[__name__].preferences
        prefs.api_key = self.api_key
        bpy.ops.wm.save_userpref()

    def get_api_key():
        prefs = bpy.context.preferences.addons[__name__].preferences
        return prefs.api_key
    
    def has_api_key(self):
        return len(self.get_api_key()) > 0
    

@persistent
def selection_handler(scene):
    if len(bpy.context.selected_objects) > 0:
        leonardo_id = bpy.context.selected_objects[0].data.get('leonardo_id', False)
        all_objects_with_leonardo_id = list(filter(lambda obj: obj.data.get('leonardo_id', False) and obj.data.get('leonardo_id', False) == leonardo_id, bpy.data.objects))
        if leonardo_id and len(all_objects_with_leonardo_id) == len(bpy.context.selected_objects):
            if all(obj.data.get('leonardo_id', "") == leonardo_id for obj in bpy.context.selected_objects):
                scene.leonardo_tools.current_mesh_id = leonardo_id
                scene.leonardo_tools.current_mesh_name = bpy.context.selected_objects[0].data.get('leonardo_name', "")
                return
        
    scene.leonardo_tools.current_mesh_name = ""
    scene.leonardo_tools.current_mesh_id = ""


class LeonardoUserModel(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    id: bpy.props.StringProperty()


def update_callback(self, context):
    for region in context.area.regions:
        if region.type == "UI":
            region.tag_redraw()
    return None

class LeonardoTexturingToolSettings(bpy.types.PropertyGroup):    
    prompt_input: bpy.props.StringProperty(name="",
                                        description="Enter your prompt here to texture your model",
                                        default="",
                                        maxlen=2048)

    currently_running_prompt_input: bpy.props.StringProperty(name="",
                                        description="Temporary storage for prompt input",
                                        default="",
                                        maxlen=2048)
    
    negative_prompt_input: bpy.props.StringProperty(name="",
                                        description="Enter your negative prompt here",
                                        default="",
                                        maxlen=2048)

    seed_input: bpy.props.FloatProperty(name="Seed",
                                        description="Leave blank for random seed",
                                        default=0,
                                        subtype='UNSIGNED',
                                        precision=0,
                                        step=1,
                                        min=0)
    
    mesh_name_input: bpy.props.StringProperty(name="",
                                        description="What is the name of your mesh? This will appear on the Leonardo website. Leave blank for default name.",
                                        default="",
                                        maxlen=2048)
    
    current_mesh_id: bpy.props.StringProperty(name="Leonardo Id of current mesh", default = "")
    
    
    current_mesh_name: bpy.props.StringProperty(name="Name of current mesh", default = "")

    albedo_path: bpy.props.StringProperty(name="Albedo Image Path",
                                        description="albedo_image_path",
                                        default="",
                                        maxlen=2048)
    
    normalmap_path: bpy.props.StringProperty(name="Normal Map Path",
                                        description="normal_map_path",
                                        default="",
                                        maxlen=2048)
    
    roughnessmap_path: bpy.props.StringProperty(name="Roughness Map Path",
                                        description="roughness_map_path",
                                        default="",
                                        maxlen=2048)
    
    displacementmap_path: bpy.props.StringProperty(name="Discplacement Map Path",
                                        description="displacement_map_path",
                                        default="",
                                        maxlen=2048)
    
    obj_export_path: bpy.props.StringProperty(name="OBJ Export Path",
                                        description="Path to export OBJ file",
                                        default="",
                                        maxlen=2048)
    
    status_label: bpy.props.StringProperty(name="Status Label",
                                        description="status_label",
                                        default="",
                                        maxlen=2048,
                                        update=update_callback)
    
    obj_direction: bpy.props.EnumProperty(items = [('-90','-x', ''),('0','-y', ''), ('90','x', ''), ('180','y', '')], default=1)
    
    model_version: bpy.props.EnumProperty(items = [('v1_5','v1', ''),('v2','v2', '')])

    preview_direction: bpy.props.EnumProperty(name="", items = [ ('front','front', ''), ('back','back', ''), ('left', 'left', ''), ('right','right', ''), ], default='front')
    
    user_meshes: bpy.props.CollectionProperty(type=LeonardoUserModel)

    collapse_mesh_settings: bpy.props.BoolProperty(name="Collapse Mesh Settings", default=True)
    
    collapse_preview_settings: bpy.props.BoolProperty(name="Collapse Preview Settings", default=True)


    def get_user_mesh_items(self, context):
        # Get all the LeonardoUserModel instances in the collection
        user_meshes = self.user_meshes
        # Create a list of tuples in the format required by the EnumProperty
        items = [(um.id, um.name, '') for um in user_meshes]
        return items
    

    uploaded_user_meshes: bpy.props.EnumProperty(
    name="Your meshes",
    items=get_user_mesh_items,
    )


# ------------------------------------------------------------------------
#    Panel in Object Mode
# ------------------------------------------------------------------------

class LeonardoPanel(bpy.types.Panel):
    bl_label = "Leonardo Texturizer"
    bl_idname = "LEONARDO_TOOLS_PT_LeonardoPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Leonardo Texturizer"
    
    @classmethod
    def poll(self,context):
        return context.object is not None
    
    def draw(self, context):
        layout = self.layout
        leonardo_tools = context.scene.leonardo_tools
        objects_are_selected = len([obj for obj in bpy.context.selected_objects]) > 0
        
        has_api_key = len(bpy.context.preferences.addons[__name__].preferences.api_key) > 0
        if not has_api_key:
            box = layout.box()
            box.label(text="API Key Required!", icon="ERROR")
            col = layout.column(align=True)
            col.operator(NavigateToPreferencesButton.bl_idname, text=NavigateToPreferencesButton.bl_label)
        
        else:
            box = layout.box()
            row = box.row()
            row.prop(leonardo_tools, "collapse_mesh_settings", text="", icon="TRIA_DOWN" if not leonardo_tools.collapse_mesh_settings else "TRIA_RIGHT", emboss=False)
            row.label(text="Mesh Settings", icon="SETTINGS")
            if not leonardo_tools.collapse_mesh_settings:
                box.alignment = 'CENTER'
                box.label(text="Mesh Name")
                box.prop(leonardo_tools, "mesh_name_input")
                row = box.row()
                row.operator(UploadMeshButton.bl_idname, text=UploadMeshButton.bl_label, icon="EXPORT")
                row.enabled = objects_are_selected and not context.scene.is_running and len(leonardo_tools.mesh_name_input) > 0
                row = box.row()
                row.label(text="Already uploaded?")
                row = box.row()
                row.operator(QueryUserMeshesButton.bl_idname, text=QueryUserMeshesButton.bl_label, icon="IMPORT")
                row.enabled = not context.scene.is_running
                if len(context.scene.leonardo_tools.uploaded_user_meshes) > 0:
                    row = box.row()
                    row.prop(context.scene.leonardo_tools, "uploaded_user_meshes")
                    row = box.row()
                    row.operator(AddModelDataToSelectedMeshButton.bl_idname, text=AddModelDataToSelectedMeshButton.bl_label, icon="EXPORT")
                    row.enabled = len([obj for obj in bpy.context.selected_objects]) > 0

            layout.separator(factor=2)
            layout.label(text="Prompt:")
            row = layout.row()
            row.prop(leonardo_tools, "prompt_input", icon="SMALL_CAPS")
            row.enabled = objects_are_selected

            layout.label(text="Negative Prompt:")
            row = layout.row()
            row.prop(leonardo_tools, "negative_prompt_input", icon="SMALL_CAPS")
            row.enabled = objects_are_selected
            
            layout.label(text="Seed:")
            layout.prop(leonardo_tools, "seed_input", icon="SMALL_CAPS")

            row = layout.row()
            row.label(text="Which way does you model face?")
            row = layout.row()
            row.prop(leonardo_tools, 'obj_direction', expand=True)

            row = layout.row()
            row.label(text="Gen. Model Version")
            row = layout.row()
            row.prop(leonardo_tools, 'model_version', expand=True)

            if not bpy.data.is_saved:
                box = layout.box()
                box.label(text="File not saved", icon="ERROR")

            layout.separator()

            box = layout.box()
            row = box.row()
            row.prop(leonardo_tools, "collapse_preview_settings", text="", icon="TRIA_DOWN" if not leonardo_tools.collapse_preview_settings else "TRIA_RIGHT", emboss=False)
            row.label(text="Preview Settings", icon="SETTINGS")
            if not leonardo_tools.collapse_preview_settings:
                box.alignment = 'CENTER'
                row = box.row(align=True)
                row.alignment = 'CENTER'
                row.label(text="Prompt preview")
                row = box.row(align=True)
                row.operator(PreviewButton.bl_idname, emboss=not context.scene.is_running, text="Preview!", icon="BRUSH_SMEAR")
                row.enabled = len([obj for obj in bpy.context.selected_objects]) > 0 and leonardo_tools.current_mesh_id != "" and not context.scene.is_running 
                
                row = box.row(align=True)
                row.alignment = 'CENTER'
                row.label(text="Preview direction:")
                row = box.row(align=True)
                row.prop(context.scene.leonardo_tools, "preview_direction")

            layout.separator()

            row = layout.row(align=True)
            row.scale_y = 2
            row.operator(TexturizeButton.bl_idname, text="Texturize!", icon="BRUSH_SMEAR")
            row.enabled = len([obj for obj in bpy.context.selected_objects]) > 0  and leonardo_tools.current_mesh_id != "" and not context.scene.is_running

        if context.scene.is_running:
            new_row = layout.row(align=True)
            new_row.alignment = 'CENTER'
            new_row.label(text="Status:")
            new_row = layout.row(align=True)
            new_row.alignment = 'CENTER'
            new_row.label(text=context.scene.leonardo_tools.status_label)
        
            new_col = layout.column()
            new_col.operator(StopButton.bl_idname, text="Stop...")

        if context.scene.has_returned:
            layout.separator(factor=3)
            row = layout.row(align=True)
            row.alignment = 'CENTER'
            row.label(text="Last used seed:")
            row = layout.row(align=True)
            row.prop(context.scene, "last_seed", icon="SMALL_CAPS")
                            
        if leonardo_tools.current_mesh_id != "" and leonardo_tools.current_mesh_name != "":
            row = layout.row()
            row.alignment = 'CENTER'
            row.label(text="Current Mesh:")
            row = layout.row()
            row.alignment = 'CENTER'
            row.label(text=leonardo_tools.current_mesh_name)

classes = (
    LeonardoUserModel,
    LeonardoTexturingToolSettings,
    LeonardoTexturingToolPreferences, 
    LeonardoPanel, 
    TexturizeButton, 
    NavigateToPreferencesButton, 
    StopButton,
    SavePreferences,
    async_computation.AsyncLoopModalOperator,
    PreviewButton,
    UploadMeshButton,
    QueryUserMeshesButton,
    AddModelDataToSelectedMeshButton
    )

def reset_properties():
    del bpy.types.Scene.leonardo_tools
    del bpy.types.Scene.result_path
    del bpy.types.Scene.is_running
    del bpy.types.Scene.job_id
    del bpy.types.Scene.has_returned
    del bpy.types.Scene.last_seed


def register():
    async_computation.setup_asyncio_executor()

    for cls in classes:    
        bpy.utils.register_class(cls)

    bpy.types.Scene.leonardo_tools = bpy.props.PointerProperty(type=LeonardoTexturingToolSettings)
    bpy.types.Scene.is_running = bpy.props.BoolProperty(name="Script is running", default = False)
    bpy.types.Scene.has_returned = bpy.props.BoolProperty(name="Script has returned", default = False)
    bpy.types.Scene.result_path = bpy.props.StringProperty(name="result_path", default = "")
    bpy.types.Scene.job_id = bpy.props.StringProperty(name="Id of current running job", default = "")
    bpy.types.Scene.last_seed = bpy.props.FloatProperty(name="Seed",
                                        description="Seed of the last generation",
                                        default=0,
                                        subtype='UNSIGNED',
                                        precision=0,
                                        step=1,
                                        min=0)
    
    # TODO this leads to a lot of updates because not only selection triggers the depsgraph update. This can be optimized
    # according to https://blender.stackexchange.com/questions/150809/how-to-get-an-event-when-an-object-is-selected for example
    bpy.app.handlers.depsgraph_update_post.append(selection_handler)
    
    
def unregister():
    for cls in classes:    
        bpy.utils.unregister_class(cls)

    reset_properties()
    bpy.app.handlers.depsgraph_update_post.remove(selection_handler)

if __name__ == "__main__":
    register()
