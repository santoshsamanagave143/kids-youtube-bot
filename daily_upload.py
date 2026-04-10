import os
import json
import requests
import urllib.parse
import time
from datetime import date, datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI
from google.cloud import texttospeech
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

OPENAI_API_KEY        = os.environ.get("OPENAI_API_KEY", "")
GCLOUD_CREDENTIALS    = os.environ.get("GCLOUD_CREDENTIALS", "")
YOUTUBE_CLIENT_ID     = os.environ.get("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")

OUTPUT_DIR = "output"
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")
AUDIO_FILE = os.path.join(OUTPUT_DIR, "narration.mp3")
VIDEO_FILE = os.path.join(OUTPUT_DIR, "final_video.mp4")
THUMB_FILE = os.path.join(OUTPUT_DIR, "thumbnail.jpg")

os.makedirs(IMAGES_DIR, exist_ok=True)

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────────────
#  STEP 1 — Write today's kids story with OpenAI
# ─────────────────────────────────────────────────
print("\n📖 STEP 1: Writing today's story...")

story_prompt = open("prompts/story.txt").read().replace("{date}", str(date.today()))

story = None
for attempt in range(3):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": story_prompt}],
            temperature=0.9
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        story = json.loads(raw.strip())
        print(f"✅ Story: {story['title']}")
        break
    except Exception as e:
        print(f"  Attempt {attempt+1} failed: {e}")
        time.sleep(3)

if story is None:
    raise RuntimeError("Failed to generate story after 3 attempts")

# ─────────────────────────────────────────────────
#  STEP 2 — Write SEO metadata with OpenAI
# ─────────────────────────────────────────────────
print("\n🏷️  STEP 2: Writing YouTube metadata...")
meta_prompt = (
    open("prompts/metadata.txt").read()
    .replace("{title}", story["title"])
    .replace("{moral}", story["moral"])
)

meta = None
for attempt in range(3):
    try:
        meta_resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": meta_prompt}],
            temperature=0.7
        )
        raw_meta = meta_resp.choices[0].message.content.strip()
        if raw_meta.startswith("```"):
            raw_meta = raw_meta.split("```")[1]
            if raw_meta.startswith("json"):
                raw_meta = raw_meta[4:]
        meta = json.loads(raw_meta.strip())
        print(f"✅ YouTube title: {meta['yt_title']}")
        break
    except Exception as e:
        print(f"  Attempt {attempt+1} failed: {e}")
        time.sleep(3)

if meta is None:
    raise RuntimeError("Failed to generate metadata after 3 attempts")

# ─────────────────────────────────────────────────
#  STEP 3 — Generate voiceover with Google TTS
# ─────────────────────────────────────────────────
print("\n🎙️  STEP 3: Generating voiceover...")
creds_info = json.loads(GCLOUD_CREDENTIALS)
tts_creds = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
tts_client = texttospeech.TextToSpeechClient(credentials=tts_creds)

synthesis_input = texttospeech.SynthesisInput(text=story["full_narration"])
voice_params = texttospeech.VoiceSelectionParams(
    language_code="en-US",
    name="en-US-Wavenet-F",
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE
)
audio_config = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.MP3,
    speaking_rate=0.82,
    pitch=2.0
)
tts_resp = tts_client.synthesize_speech(
    input=synthesis_input, voice=voice_params, audio_config=audio_config
)
with open(AUDIO_FILE, "wb") as f:
    f.write(tts_resp.audio_content)
print(f"✅ Voiceover saved")

# ─────────────────────────────────────────────────
#  STEP 4 — Generate cartoon images (Pollinations.ai)
# ─────────────────────────────────────────────────
print("\n🎨 STEP 4: Generating cartoon images...")

def generate_image(scene_prompt, filename, retries=4):
    full_prompt = (
        f"{scene_prompt}, children's storybook illustration, "
        "bright cheerful colors, soft warm lighting, cute cartoon characters, "
        "simple shapes, safe for kids, no text, no words, high quality, 16:9"
    )
    encoded = urllib.parse.quote(full_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1280&height=720&model=flux&nologo=true&seed={int(time.time())}"
    )
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=90)
            if resp.status_code == 200 and len(resp.content) > 5000:
                with open(filename, "wb") as f:
                    f.write(resp.content)
                return True
        except Exception as e:
            print(f"  Attempt {attempt+1}: {e}")
            time.sleep(5)
    return False

for scene in story["scenes"]:
    img_path = os.path.join(IMAGES_DIR, f"scene_{scene['num']}.png")
    ok = generate_image(scene["image_prompt"], img_path)
    if ok:
        print(f"  ✅ Scene {scene['num']} done")
    else:
        print(f"  ⚠️  Scene {scene['num']} fallback colour used")
        Image.new("RGB", (1280, 720), color=(255, 220, 100)).save(img_path)

# ─────────────────────────────────────────────────
#  STEP 5 — Generate thumbnail with Pillow
# ─────────────────────────────────────────────────
print("\n🖼️  STEP 5: Generating thumbnail...")
thumb_base = Image.open(os.path.join(IMAGES_DIR, "scene_1.png")).resize((1280, 720))
draw = ImageDraw.Draw(thumb_base)
draw.rectangle([0, 540, 1280, 720], fill=(255, 210, 0))

font = None
for fp in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]:
    if os.path.exists(fp):
        font = ImageFont.truetype(fp, 64)
        break
if font is None:
    font = ImageFont.load_default()

title_text = story["title"].upper()
if len(title_text) > 36:
    title_text = title_text[:33] + "..."
draw.text((640, 630), title_text, font=font, fill=(20, 20, 20), anchor="mm")
thumb_base.save(THUMB_FILE, quality=95)
print(f"✅ Thumbnail saved")

# ─────────────────────────────────────────────────
#  STEP 6 — Assemble video with MoviePy
# ─────────────────────────────────────────────────
print("\n🎬 STEP 6: Assembling video...")
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

audio_clip = AudioFileClip(AUDIO_FILE)
audio_dur  = audio_clip.duration
n_scenes   = len(story["scenes"])
scene_dur  = audio_dur / n_scenes

clips = []
for scene in story["scenes"]:
    img_path = os.path.join(IMAGES_DIR, f"scene_{scene['num']}.png")
    clip = (
        ImageClip(img_path)
        .set_duration(scene_dur)
        .resize((1280, 720))
        .fadein(0.4)
        .fadeout(0.4)
    )
    clips.append(clip)

final = concatenate_videoclips(clips, method="compose")
final = final.set_audio(audio_clip)
final.write_videofile(
    VIDEO_FILE, fps=24, codec="libx264",
    audio_codec="aac", threads=2, logger=None
)
print(f"✅ Video assembled")

# ─────────────────────────────────────────────────
#  STEP 7 — Upload to YouTube
# ─────────────────────────────────────────────────
print("\n📤 STEP 7: Uploading to YouTube...")
yt_creds = Credentials(
    token=None,
    refresh_token=YOUTUBE_REFRESH_TOKEN,
    token_uri="https://oauth2.googleapis.com/token",
    client_id=YOUTUBE_CLIENT_ID,
    client_secret=YOUTUBE_CLIENT_SECRET,
    scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
yt_creds.refresh(Request())
youtube = build("youtube", "v3", credentials=yt_creds)

now_utc      = datetime.now(timezone.utc)
publish_time = now_utc.replace(hour=15, minute=0, second=0, microsecond=0)
if publish_time <= now_utc:
    publish_time += timedelta(days=1)
publish_str  = publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

request_body = {
    "snippet": {
        "title":           meta["yt_title"],
        "description":     meta["description"],
        "tags":            meta["tags"],
        "categoryId":      "27",
        "defaultLanguage": "en"
    },
    "status": {
        "privacyStatus":           "private",
        "publishAt":               publish_str,
        "selfDeclaredMadeForKids": True
    }
}

media = MediaFileUpload(VIDEO_FILE, chunksize=-1, resumable=True, mimetype="video/mp4")
upload_req = youtube.videos().insert(
    part="snippet,status", body=request_body, media_body=media
)
response = None
while response is None:
    status, response = upload_req.next_chunk()
    if status:
        print(f"  Uploading... {int(status.progress()*100)}%")

video_id = response["id"]
print(f"✅ Uploaded: https://youtube.com/watch?v={video_id}")

youtube.thumbnails().set(
    videoId=video_id,
    media_body=MediaFileUpload(THUMB_FILE, mimetype="image/jpeg")
).execute()
print(f"✅ Thumbnail set")
print(f"\n🎉 DONE! Goes live at {publish_str}")
print(f"   https://youtube.com/watch?v={video_id}")
