# Copyright (2025) Bytedance Ltd. and/or its affiliates

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Path2-owned copy of the memory construction implementation.

Copied from StreamMeCo/mmagent/memory_processing_qwen.py. Functions are installed
through the existing adaptor namespace so frame/backend hooks and shared transport
utilities continue to work, but path2 no longer calls the original construction
functions. Keep path2 construction changes here and its prompt under prompts/.
"""
from pathlib import Path
from m3_adaptors._shared import rebind
from m3_adaptors.construction import DELTA_INSTRUCTION


def generate_video_context(
    base64_frames, faces_list, voices_list, video_path=None, faces_input="face_only"
):
    face_frames = []
    face_only = []

    # Iterate through faces directly
    for char_id, faces in faces_list.items():
        if len(faces) == 0:
            continue
        face = faces[0]
        frame_id = face["frame_id"]
        frame_base64 = base64_frames[frame_id]

        # Convert base64 to PIL Image
        frame_bytes = base64.b64decode(frame_base64)
        frame_img = Image.open(BytesIO(frame_bytes))
        draw = ImageDraw.Draw(frame_img)

        # Draw current face
        bbox = face["bounding_box"]
        draw.rectangle(
            [(bbox[0], bbox[1]), (bbox[2], bbox[3])], outline=(0, 255, 0), width=4
        )

        # Convert back to base64
        buffered = BytesIO()
        frame_img.save(buffered, format="JPEG")
        frame_base64 = base64.b64encode(buffered.getvalue()).decode()
        face_frames.append((f"<face_{char_id}>:", frame_base64))
        face_only.append((f"<face_{char_id}>:", face["extra_data"]["face_base64"]))
    
    if faces_input == "face_only":
        faces_input = face_only
    elif faces_input == "face_frames":
        faces_input = face_frames
    else:
        raise ValueError(f"Invalid face input: {faces_input}")
    
    num_faces = len(faces_input)
    if num_faces == 0:
        logger.warning("No qualified faces detected")
    
    # Visualize face frames with IDs
    if logging_level == "DETAIL" and num_faces > 0:
        num_rows = (num_faces + 2) // 3  # Round up division to get number of rows needed

        _, axes = plt.subplots(num_rows, 3, figsize=(15, 5 * num_rows))
        axes = axes.ravel()  # Flatten axes array for easier indexing

        for i, face_pic in enumerate(faces_input):
            # Convert base64 to image array
            img_bytes = base64.b64decode(face_pic[1])
            img_array = np.array(Image.open(BytesIO(img_bytes)))

            axes[i].imshow(img_array)
            axes[i].set_title(face_pic[0])
            axes[i].axis("off")

        # Hide empty subplots
        for j in range(i + 1, len(axes)):
            axes[j].axis("off")

        plt.tight_layout()
        plt.show()

    voices_input = {}
    for id, voices in voices_list.items():
        if len(voices) == 0:
            continue
        voices_input[f"<voice_{id}>"] = [{
            "start_time": voice["start_time"],
            "end_time": voice["end_time"],
            "asr": voice["asr"]
        } for voice in voices]
    
    num_voices = len(voices_input)
    if num_voices == 0:
        logger.warning("No qualified voices detected")

    if logging_level == "DETAIL" and num_voices > 0:
        logger.debug(f"Diarized dialogues: {voices_input}")

    video_context = [
        {
            "type": "video_base64/mp4",
            "content": video_path,
        },
        {
            "type": "text",
            "content": "Face features:"
        },
        {
            "type": "images/jpeg",
            "content": faces_input,
        },
        {
            "type": "text",
            "content": "Voice features:"
        },
        {
            "type": "text",
            "content": json.dumps(voices_input),
        }
    ]

    return video_context

def generate_all_memories(video_context, model_type="sft"):
    input = [
        {
            "type": "text",
            "content": prompt_generate_memory_with_ids_sft,
        },
    ] + video_context
    
    messages = generate_messages(input)
    epi_key = "video_descriptions"
    sem_key = "high_level_conclusions"
    # The prompt (prompt_generate_memory_with_ids_sft) specifies singular keys
    # ("video_description"); accept both spellings, and treat any other schema
    # like an unparseable response (retry, then fall back to empty memories).
    key_variants = {
        epi_key: (epi_key, "video_description"),
        sem_key: (sem_key, "high_level_conclusion"),
    }

    memories = None
    for i in range(MAX_RETRIES):
        memories_string = get_response(messages)[0]
        if not memories_string:
            memories_string = "[]"
        parsed = validate_and_fix_json(memories_string)
        if isinstance(parsed, dict):
            memories = {}
            for canonical, variants in key_variants.items():
                value = next((parsed[v] for v in variants if v in parsed), None)
                if value is None:
                    memories = None
                    break
                memories[canonical] = value
        if memories is not None:
            break
    if memories is None:
        memories = {
            epi_key: [],
            sem_key: []
        }
        # raise Exception("Failed to generate memories")
    
    episodic_memories = memories[epi_key]
    semantic_memories = memories[sem_key]
    
    return episodic_memories, semantic_memories

def generate_memories(base64_frames, faces_list, voices_list, video_path,
                      model_type="sft", *, video_graph=None):
    from m3_adaptors.construction import construction_context, label_voice_transcripts
    video_context = generate_video_context(base64_frames, faces_list, voices_list, video_path)
    video_context = label_voice_transcripts(video_context, video_graph)
    return generate_all_memories(construction_context(video_graph) + video_context, model_type)


def install(module):
    """Bind our copied implementation without editing any StreamMeCo source."""
    prompt = Path(__file__).with_name('prompts') / 'memory_construction.md'
    module.prompt_generate_memory_with_ids_sft = prompt.read_text() + '\n\n' + DELTA_INSTRUCTION
    for function in (generate_video_context, generate_all_memories, generate_memories):
        setattr(module, function.__name__, rebind(function, module))
