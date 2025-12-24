import os
import time
import re
import numpy as np
import requests
import shutil 
import random 
import json 
from google import genai
from moviepy.editor import *
from moviepy.config import change_settings
from gtts import gTTS
from moviepy.audio.AudioClip import AudioArrayClip 
from PIL import Image, ImageDraw, ImageFont 
from io import BytesIO 
from bs4 import BeautifulSoup

# --- YouTube API Specific Imports ---
# These are required by the user's provided snippet
import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
global api_keys
# WARNING: Replace with your actual keys and paths
api_keys=[

]

# GEMINI_API_KEY = random.choice(api_keys)  # WARNING: Replace with your actual Gemini API key 
# print(f"Using Gemini API Key: {GEMINI_API_KEY}")
IMAGEMAGICK_BIN_PATH = r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe" 

# --- New User-Configurable Parameters ---
TARGET_LANGUAGE = 'Kannada' 
OUTPUT_VIDEO_COUNT = 10
EXPLICIT_WORD_CATEGORY = None 
VIDEO_DURATION_TYPE = None # Set to 'Short' or 'Long' or None (random)
# --- Global Topic Tracking (REQUIRED for guaranteeing uniqueness) ---
GENERATED_TOPICS = set()

# --- Dynamic Path & File Structure ---
OUTPUT_DIR_SHORTS = os.path.join(os.getcwd(), "youtubeShorts")
OUTPUT_DIR_VIDEOS = os.path.join(os.getcwd(), "youtubeVideos")
TTS_FONT_DIR = os.path.join(os.getcwd(), "ttf")
IMAGE_DIRECTORY = os.path.join(os.getcwd(), "video_temp_images") 

# --- Video Constants (Shared) ---
PAUSE_DURATION = 0.5 
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
INTRO_SPEED_FACTOR = 1.50 # Speed up the introduction by 50%
SHORT_DURATION_TARGET = random.randint(30, 40)
LONG_DURATION_TARGET = random.randint(300, 400)
ENGLISH_FONT_PATH = "C:/Windows/Fonts/arial.ttf" 

# --- Text Overlay Constants ---
TEXT_PANEL_WIDTH_PERCENT = 0.30 
TEXT_PANEL_WIDTH = int(VIDEO_WIDTH * TEXT_PANEL_WIDTH_PERCENT)
IMAGE_PANEL_WIDTH = VIDEO_WIDTH - TEXT_PANEL_WIDTH
TEXT_X_POSITION_CENTER = int(TEXT_PANEL_WIDTH / 2) 
TEXT_Y_POSITION_ENG = int(VIDEO_HEIGHT * 0.35) 
TEXT_Y_POSITION_TEL = int(VIDEO_HEIGHT * 0.55) 
MAX_FONT_SIZE = 80 
MIN_FONT_SIZE = 40 
PADDING = 20 
MAX_RETRIES = 3
RETRY_DELAY = 2 

# --- Language Mappings ---
LANG_CODE_MAP = {
    'Telugu': 'te',
    'Hindi': 'hi',
    'Tamil': 'ta',
    'Marathi': 'mr',
    'Bengali': 'bn',
    'Gujarati': 'gu',
    'Kannada': 'kn',
    'Malayalam':'ml'
}

# --- YouTube API Configuration ---
# REPLACE WITH YOUR ACTUAL CLIENT SECRETS PATH
CLIENT_SECRETS_FILE = r"C:\Users\Admin\Downloads\client_secret_133370834510-oao5kveck6p7ker46rafjtt2qvp61ur5.apps.googleusercontent.com.json"
TOKEN_FILE = 'token.json'

SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'
VIDEO_CATEGORY_ID = '27' # 27 = Education, 22 = People & Blogs

# Apply ImageMagick Configuration
try:
    change_settings({"IMAGICK_BINARY": IMAGEMAGICK_BIN_PATH})
except Exception as e:
    print(f"❌ Error configuring ImageMagick. Check the IMAGICK_BINARY_PATH. Error: {e}")

# ==============================================================================
# 1.5 HELPER FUNCTIONS (Non-Gemini/YouTube)
# ==============================================================================

def create_silence_clip(duration, fps=44100):
    """Creates a silent AudioArrayClip using numpy."""
    num_samples = int(fps * duration)
    return AudioArrayClip(np.zeros((num_samples, 1)), fps=fps).set_duration(duration)

def get_language_config(target_lang):
    """Returns lang_code and ttf_path for the target language."""
    lang_code = LANG_CODE_MAP.get(target_lang, 'te') 
    
    lang_font_dir = os.path.join(TTS_FONT_DIR, target_lang)
    print(lang_font_dir)
    
    ttf_file = None
    
    if os.path.exists(lang_font_dir):
        for f in os.listdir(lang_font_dir):
            if f.lower().endswith('.ttf'):
                ttf_file = os.path.join(lang_font_dir, f)
                break
    
    if ttf_file:
        print(f"✅ Found font file for {target_lang}: {ttf_file}")
    else:
        # Fallback to the hardcoded path if automatic detection fails
        # NOTE: User should replace this with a valid font path for their target language.
        ttf_file = r"C:\Users\Admin\Downloads\youtube_audio\AI_English\Telugu\Dhurjati-Regular.ttf"
        print(f"⚠️ Using hardcoded fallback font path for {target_lang}: {ttf_file}")
       
    return lang_code, ttf_file

def draw_text_on_image(img, text, font_path, color, x_pos_center, y_pos):
    """Draws text onto a PIL Image, dynamically reducing font size."""
    draw = ImageDraw.Draw(img)
    target_width = TEXT_PANEL_WIDTH - PADDING * 2
    final_font = None
    
    for size in range(MAX_FONT_SIZE, MIN_FONT_SIZE - 1, -5):
        try:
            current_font = ImageFont.truetype(font_path, size)
        except IOError:
            current_font = ImageFont.load_default()
            size = MIN_FONT_SIZE 
        
        bbox = draw.textbbox((0, 0), text, font=current_font)
        text_width = bbox[2] - bbox[0]
        
        if text_width <= target_width:
            final_font = current_font
            break
            
        if size == MIN_FONT_SIZE:
             final_font = current_font
             break 

    if final_font is None:
        final_font = ImageFont.load_default()
        
    bbox = draw.textbbox((0, 0), text, font=final_font)
    text_width = bbox[2] - bbox[0]
    x_centered = x_pos_center - (text_width / 2)
    
    if x_centered < PADDING: x_centered = PADDING
    
    draw.text((x_centered, y_pos), text, font=final_font, fill=color)
    
    return img, size 

def extract_tags_from_description(description):
    """Extracts all hashtags from the description to be used as video tags."""
    # Find all words starting with #
    tags = re.findall(r'#(\w+)', description)
    # Convert to a comma-separated string
    return ','.join(tags)




# ==============================================================================
# 2. DATA GENERATION (Gemini API) (Topic Generation Finalized)
# ==============================================================================


def select_or_generate_topic():
    """
    Generates a unique, high-quality topic by providing a detailed list of examples
    to guide Gemini's generation, ensuring the resulting topic is new and high-quality.
    """
    global GENERATED_TOPICS
    
    # 1. Use Explicit Category if set
    if EXPLICIT_WORD_CATEGORY:
        topic = EXPLICIT_WORD_CATEGORY.strip().lower()
        GENERATED_TOPICS.add(topic)
        return topic

    # 2. Prepare Exclusions (FIX: Initialize exclusion_phrase first)
    topic_exclusions_list = list(GENERATED_TOPICS)
    
    if topic_exclusions_list:
        topic_exclusions_str = ", ".join(topic_exclusions_list)
        exclusion_phrase = f" and MUST NOT select any of these previously used topics: {topic_exclusions_str}"
    else:
        # 🟢 FIX: Define a clear default value if no topics have been generated yet.
        topic_exclusions_str = "" 
        exclusion_phrase = ""
        
    # 3. Comprehensive Example List for Guidance 
    example_topics = [
    "Vegetables", "Fruits", "Toys", "Arts", "Famous Places", "Electrical Goods",
    "Fuel Types", "Action Verbs", "Weapons and Tools", "Foods and Cuisine",
    "Small Creatures", "Games and Sports", "Seasons and Weather", "Pot Herbs",
    "Artistic Professions", "Water Resources", "Ailments and Diseases",
    "Descriptive Adjectives", "Geographical Places", "Months of the Year",
    "Days of the Week", "Conjunctions", "Interjections", "Prepositions",
    "Household Items", "Emotions", "Musical Instruments", "Animals", "Birds","verbs","verbs",
    "Flowers", "Vehicles", "Occupations", "Clothing Items", "Colors",
     "Dry Fruits", "Technology & Gadgets", "Festivals",
    "Natural Disasters", "Transport Modes", "Languages", "Shapes",
    "Trees & Plants", "Space & Astronomy", "Countries & Capitals",
    "Ocean Animals", "School Supplies", "Kitchen Items", "Furniture",
    "Moral Values", "Communication Devices", "Insects", "Reptiles",
    "Aquatic Animals", "Drinks & Beverages", "Spices", "Community Helpers",
    "Holiday Activities", "Weather Events", "Tools and Hardware",
    "Computer Parts", "Programming Languages", "Chemical Elements",
    "Famous Scientists", "Historical Events", "Medicinal Plants",
    "Transport Accessories", "Cooking Methods", "Jewellery Items",
    "Festivals Around the World", "Cereals and Grains", "Snacks",
    "Professional Skills", "Social Issues", "Business Terms",
    "Finance and Banking", "Famous Books", "Famous Authors", "Famous Leaders",
    "Mammals", "Cartoon Characters", "Mythological Characters", "Planets",
    "Constellations", "Rivers of the World", "Mountains", "Lakes",
    "Deserts", "Islands", "Religions of the World", "Crops",
    "Industrial Machines", "Construction Tools", "Office Supplies",
    "Road Signs", "Legal Terms", "Medical Instruments", "Surgical Tools",
    "Fitness Activities", "Yoga Poses", "Dance Forms", "Movie Genres",
    "Music Genres", "TV Shows", "Famous Actors", "Famous Athletes",
    "Sports Equipment", "Hobbies", "Pets", "Wild Animals", "Endangered Animals",
    "Modes of Communication", "Types of Energy", "Scientific Instruments",
    "Laboratory Equipment", "Transport Professions", "Cooking Ingredients",
    "Street Foods", "Dairy Products", "Baked Goods", "Desserts",
    "Beverage Types", "Garden Tools", "Stationery Items", "Factory Equipment",
    "Vehicles Parts", "Kitchen Appliances", "Electronics Accessories",
    "Internet Terms", "Cloud Computing Terms", "AI & ML Terms",
    "Networking Devices", "Database Terms", "Cybersecurity Terms",
    "Grammar Topics", "Sentence Types", "Punctuation Marks",
    "Common Idioms", "Phrasal Verbs", "Synonyms", "Antonyms",
    "Homophones", "Basic English Words", "Advanced English Words",
    "Innovation Topics", "Workplace Ethics", "Soft Skills",
    "Nature Wonders", "Cultural Traditions", "Languages of India",
    "Festivals of India", "Freedom Fighters", "Indian States & Capitals",
    "National Symbols", "Bird Species", "Fish Species", "Famous Beaches",
    "World Capitals", "World Cuisines", "Famous Bridges",
    "Engineering Branches", "Math Topics", "Physics Topics",
    "Chemistry Topics", "Biology Topics", "Robotics Terms",
    "Electronic Components", "Car Brands", "Bike Brands", "Airlines",
    "Airports", "Hotels", "Tourist Attractions", "Fashion Styles",
    "Beauty Products", "Skincare Items", "Perfume Types",
    "Home Decor Items", "Interior Design Styles", "Landforms",
    "Weather Phenomena", "Oceanography Topics", "Environmental Issues",
    "Recycling Items", "Green Energy Sources"

    ]
    examples_str = ", ".join(example_topics)
        
    # 4. Call Gemini with constraints and the final, specific prompt
    c=0
    try:
        new_api_keys= api_keys.copy()
        while c<=3:
            
            GEMINI_API_KEY = random.choice(new_api_keys) 
            print("🧠 Calling Gemini API to generate a unique topic...")
            print(GEMINI_API_KEY)
            client = genai.Client(api_key=GEMINI_API_KEY)
            new_api_keys.remove(GEMINI_API_KEY)
            # random_seed = random.randint(1000, 9999) nothing else. Use this seed: {random_seed}.
            
            prompt = f"""
            Generate a single, unique, and broad category for a vocabulary video in {TARGET_LANGUAGE}.
            The generated category should be specific enough to contain 40-50 unique words.
            
            Use the following list as **EXAMPLES** of the **quality and type** of educational topic desired:
            {examples_str}
            
            Your generated topic MUST NOT be any of the items in the example list.
            It MUST be a completely new, unique topic of similar educational value.
            {exclusion_phrase}
            
            Before Generate the topic you should see already generated topics, if you already generated you should not generate that topic name.
            these are already generated topics:{topic_exclusions_str}

            Provide ONLY the generated topic name, if you know any new topic give that also.

            Validatation Steps before you generate the topic:
            1) You should cross check the new generated topic is not present in {topic_exclusions_str}
            2) you should give only topic name don't give extra information prefix and sufix
            3) topic should not be related to human body parts

            
            """
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                
                # Clean and validate the topic
                topic = response.text.strip().split('\n')[0].lower().replace('.', '').replace('category:', '').strip()
                
                # Check if topic is empty or if it matches a used topic (case-insensitive)
                if not topic or (topic_exclusions_str and topic in topic_exclusions_str.lower()):
                    continue
                    # raise ValueError(f"Gemini returned an empty or already used topic: {topic}")
                    
                # 5. Update the Tracking Set
                GENERATED_TOPICS.add(topic)
                
                print(f"🧠 Gemini successfully generated a new, unique topic: '{topic}'")
                return topic
            except Exception as e:
                c+=1
                continue
        else:
            raise ValueError("Gemini API is not working change with new API keys")

    except Exception as e:
        print(f"❌ An error occurred during unique topic generation, defaulting to 'daily activities' and skipping exclusion check: {e}")
        # Use a safe fallback topic
        default_topic = "daily activities" 
        GENERATED_TOPICS.add(default_topic)
        return default_topic


def get_word_pairs(category, count=50):
    print(f"🧠 Calling Gemini API to fetch {count} English/{TARGET_LANGUAGE} word pairs for category: '{category}'...")
    
    try:
        new_api_keys= api_keys.copy()
        c=0
        while c<=3:
            print(new_api_keys)
            GEMINI_API_KEY = random.choice(new_api_keys) 
            client = genai.Client(api_key=GEMINI_API_KEY)
            new_api_keys.remove(GEMINI_API_KEY)
            prompt = f"""
            You are an experienced language teacher.

            Generate exactly {count} English vocabulary words with their {TARGET_LANGUAGE} meanings.
            All words must strictly belong to the category "{category}".

            Teaching Rules (VERY IMPORTANT):
            1. Words must be commonly used in daily life OR slightly advanced but useful.
            2. Avoid rare, poetic, or technical words.
            3. Translations must be 100% correct and natural for native speakers.
            4. If you are even slightly unsure about a translation, EXCLUDE that word.
            5. Do NOT translate English words into English unless both languages genuinely use the same word.
            6. Prefer words that are easy to visualize and explain to students.
            7. No duplicate words or meanings.

            Learning-Friendly Output Rules:
            - Keep words simple and clear
            - Avoid confusion words
            - Exactly {count} pairs only

            Output format (STRICT):
            English Word, {TARGET_LANGUAGE} Meaning
            English Word, {TARGET_LANGUAGE} Meaning

            No numbering.
            No explanations.
            No extra text.
            """

            
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                print(response.text)
                words_list = []
                for line in response.text.strip().split('\n'):
                    parts = [p.strip() for p in line.split(',', 1) if p.strip()] 
                    if len(parts) == 2:
                        english_word = re.sub(r'[^\w\s]', '', parts[0])
                        telugu_word = parts[1] 
                        words_list.append((english_word, telugu_word))
                
                print(f"✅ Gemini successfully returned {len(words_list)} word pairs for the category.")
                return words_list
            except Exception as e:
                print("Retrying with another key this key is not working",GEMINI_API_KEY)
                c+=1
                continue
        else:
            raise ValueError("Gemini API is not working change with new API keys")

    except Exception as e:
        print(f"❌ An error occurred during Gemini API call for words: {e}")
        return None


def generate_seo_metadata(topic, target_lang, word_samples, video_type):
    """
    Generates SEO-optimized title and description for YouTube using Gemini.
    """
    print(f"\n🧠 Generating SEO metadata for topic: '{topic}'...")
    
    sample_words_list = [f"{eng} ({tel})" for eng, tel in word_samples]
    sample_words_str = ", ".join(sample_words_list)

    if video_type == 'Short':
        title_length_prompt = "Keep the title under 60 characters for maximum Short visibility."
        title_keywords = f"Must include: {target_lang}, English, Vocabulary, and {topic}. Focus on trending search terms."
    else:
        title_length_prompt = "Keep the title detailed, up to 100 characters."
        title_keywords = f"Must include: Learn, {target_lang}, English, Vocabulary, and {topic}. Use strong keywords."

    prompt = f"""
    You are an expert YouTube SEO specialist. Generate an attractive, searchable title and description for a vocabulary video.

    --- Video Details ---
    1. **Primary Topic:** {topic.title()}
    2. **Target Language:** {target_lang}
    3. **Video Type:** {video_type}
    4. **Sample Words (for keyword stuffing):** {sample_words_str}

    --- Title Requirements ---
    - Generate a single, compelling title.
    - {title_length_prompt}
    - {title_keywords}
    - Use emojis relevant to the topic and learning.

    --- Description Requirements ---
    - Must be at least 4 paragraphs.
    - **Paragraph 1:** Engaging summary, mentioning the topic and target language.
    - **Paragraph 2:** List the words covered in the video (use the sample words provided and encourage watching for more).
    - **Paragraph 3:** Strong Call-to-Action (Like, Subscribe, Comment).
    - **Paragraph 4 (Keywords/Hashtags):** Include a list of relevant hashtags (#) and keywords. Include #{target_lang}vocabulary, #learn{target_lang}, #englishvocabulary, #{topic.replace(' ', '')}, #shorts (if Short), #longform (if Long).

    --- Output Format ---
    TITLE: [Your Generated Title]
    DESCRIPTION: [Your Generated Description]
    """

    try:
        new_api_keys= api_keys.copy()
        c=0
        while c<=3:
            GEMINI_API_KEY = random.choice(new_api_keys) 
            client = genai.Client(api_key=GEMINI_API_KEY)
            new_api_keys.remove(GEMINI_API_KEY)
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )

                text = response.text.strip()
                title_match = re.search(r"TITLE:\s*(.*)", text)
                desc_match = re.search(r"DESCRIPTION:\s*(.*)", text, re.DOTALL)

                title = title_match.group(1).strip() if title_match else f"Learn {topic} Vocabulary in {target_lang}"
                description = desc_match.group(1).strip() if desc_match else "A great video for learning new words! Like and subscribe."

                print("✅ Metadata generated successfully.")
                return title, description
            except Exception as e:
                c+=1
                continue
        else:
            raise ValueError("Gemini API is not working change with new API keys")


    except Exception as e:
        print(f"❌ Error generating metadata: {e}")
        return f"Learn {topic} Vocabulary in {target_lang}", "A great video for learning new words! Like and subscribe."


# ==============================================================================
# 3. AUDIO GENERATION (gTTS)
# ==============================================================================

def get_language_intro_outro(topic, target_lang):
    """
    Returns the English and Target Language intro/outro texts as lists of short phrases,
    to stabilize the gTTS voice selection and ensure ultra-short delivery.
    """
    
    if target_lang == 'Telugu':
        topic_telugu = f"{topic} గురించి" 
        
        intro_eng = ["Welcome to Naren’s English Vocabulary Channel!"]
        
        intro_phrases = [
            f"ఈరోజు, మనం {topic_telugu} తెలుసుకుందాం.",
            "వీడియో ని చివరి వరకు చూడండి.", 
            "తప్పకుండా subscribe చేసుకోండి."
        ]
        
        outro_phrases = [
            "మీకు ఈ వీడియో నచ్చినట్లయితే,", 
            "దయచేసి like చేసి,", 
            "subscribe చేయండి.", 
            "ధన్యవాదాలు."
        ]
    elif target_lang == 'Kannada':
        topic_kn = f"{topic} ಬಗ್ಗೆ"

        intro_eng = ["Welcome to Naren’s English Vocabulary Channel!"]

        intro_phrases = [
            f"ಇಂದು ನಾವು {topic_kn} ತಿಳಿದುಕೊಳ್ಳೋಣ.",
            "ಈ ವಿಡಿಯೋ ನಿಮಗೆ ತುಂಬಾ ಉಪಯುಕ್ತವಾಗಿರುತ್ತದೆ.",
            "ವಿಡಿಯೋವನ್ನು ಕೊನೆವರೆಗೂ ತಪ್ಪದೇ ನೋಡಿ."
        ]

        outro_phrases = [
            "ಈ ವಿಡಿಯೋ ನಿಮಗೆ ಇಷ್ಟವಾದರೆ,",
            "ದಯವಿಟ್ಟು like ಮಾಡಿ,",
            "ನಿಮ್ಮ ಸ್ನೇಹಿತರೊಂದಿಗೆ share ಮಾಡಿ,",
            "ಇಂತಹ ಇನ್ನಷ್ಟು ವಿಡಿಯೋಗಳಿಗಾಗಿ channel ಅನ್ನು subscribe ಮಾಡಿ.",
            "ಧನ್ಯವಾದಗಳು!"
        ]
    elif target_lang == 'Hindi':
        topic_hi = f"{topic} के बारे में"

        intro_eng = ["Welcome to Naren’s English Vocabulary Channel!"]

        intro_phrases = [
            f"आज हम {topic_hi} सीखेंगे।",
            "यह वीडियो आपके लिए बहुत उपयोगी होगा।",
            "वीडियो को अंत तक ज़रूर देखें।"
        ]

        outro_phrases = [
            "अगर आपको यह वीडियो पसंद आया,",
            "तो कृपया like करें,",
            "अपने दोस्तों के साथ share करें,",
            "ऐसे और वीडियो के लिए channel को subscribe करें।",
            "धन्यवाद!"
        ]
    elif target_lang == 'Tamil':
        topic_ta = f"{topic} பற்றி"

        intro_eng = ["Welcome to Naren’s English Vocabulary Channel!"]

        intro_phrases = [
            f"இன்று நாம் {topic_ta} கற்றுக்கொள்வோம்.",
            "இந்த வீடியோ உங்களுக்கு மிகவும் பயனுள்ளதாக இருக்கும்.",
            "இந்த வீடியோவை கடைசி வரை கண்டிப்பாக பாருங்கள்."
        ]

        outro_phrases = [
            "இந்த வீடியோ உங்களுக்கு பிடித்திருந்தால்,",
            "தயவுசெய்து like செய்யுங்கள்,",
            "உங்கள் நண்பர்களுடன் share செய்யுங்கள்,",
            "மேலும் இப்படியான வீடியோக்களுக்கு channel-ஐ subscribe செய்யுங்கள்.",
            "நன்றி!"
        ]
    elif target_lang == 'Malayalam':
        topic_ml = f"{topic}യെ കുറിച്ച്"

        intro_eng = ["Welcome to Naren’s English Vocabulary Channel!"]

        intro_phrases = [
            f"ഇന്ന് നമ്മൾ {topic_ml} പഠിക്കാം.",
            "ഈ വീഡിയോ നിങ്ങൾക്ക് വളരെ ഉപകാരപ്പെടും.",
            "വീഡിയോ അവസാനം വരെ തീർച്ചയായും കാണുക."
        ]

        outro_phrases = [
            "ഈ വീഡിയോ നിങ്ങൾക്ക് ഇഷ്ടമായെങ്കിൽ,",
            "ദയവായി like ചെയ്യൂ,",
            "നിങ്ങളുടെ സുഹൃത്തുകളുമായി share ചെയ്യൂ,",
            "ഇത്തരത്തിലുള്ള കൂടുതൽ വീഡിയോകൾക്കായി channel subscribe ചെയ്യൂ.",
            "നന്ദി!"
        ]
        
    # ... (other languages can be added here) ...
    else:
        intro_eng = ["Welcome! Today we learn " + topic]
        intro_phrases = [f"We will learn the words in {target_lang}.", "Watch till the end and subscribe."]
        outro_phrases = ["If you liked this video, please like and subscribe.", "Thank you!"]
        
    return intro_eng, intro_phrases, outro_phrases

def generate_audio(words_list, topic, lang_code):
    print("🔊 Generating and concatenating audio clips (gTTS)...")
    
    final_audio_clips = []
    word_pair_timings = []
    current_time = 0.0
    temp_files_to_clean = []
    
    try:
        pause_audio_clip = create_silence_clip(PAUSE_DURATION)
        
        # --- 1. INTRO AUDIO SEGMENT ---
        intro_eng_phrases, intro_tel_phrases, outro_tel_phrases = get_language_intro_outro(topic, TARGET_LANGUAGE)
        intro_duration = 0
        
        for i, phrase in enumerate(intro_eng_phrases):
            temp_intro_eng_file = f"temp_intro_eng_{i}.mp3"
            temp_files_to_clean.append(temp_intro_eng_file)
            tts_intro_en = gTTS(text=phrase, lang='en')
            tts_intro_en.save(temp_intro_eng_file)
            intro_eng_clip = AudioFileClip(temp_intro_eng_file)
            intro_eng_clip = intro_eng_clip.fx(vfx.speedx, INTRO_SPEED_FACTOR) 
            final_audio_clips.append(intro_eng_clip)
            intro_duration += intro_eng_clip.duration
            if i < len(intro_eng_phrases) - 1:
                final_audio_clips.append(create_silence_clip(0.3 / INTRO_SPEED_FACTOR))
                intro_duration += (0.3 / INTRO_SPEED_FACTOR)

        for i, phrase in enumerate(intro_tel_phrases):
            temp_intro_tel_file = f"temp_intro_tel_{i}.mp3"
            temp_files_to_clean.append(temp_intro_tel_file)
            tts_intro_tel = gTTS(text=phrase, lang=lang_code)
            tts_intro_tel.save(temp_intro_tel_file)
            intro_tel_clip = AudioFileClip(temp_intro_tel_file)
            intro_tel_clip = intro_tel_clip.fx(vfx.speedx, INTRO_SPEED_FACTOR) 
            final_audio_clips.append(intro_tel_clip)
            intro_duration += intro_tel_clip.duration
            if i < len(intro_tel_phrases) - 1:
                final_audio_clips.append(create_silence_clip(0.3 / INTRO_SPEED_FACTOR))
                intro_duration += (0.3 / INTRO_SPEED_FACTOR)
                
        final_audio_clips.append(create_silence_clip(1.0)) 
        intro_duration += 1.0
        current_time += intro_duration
        
        # --- 2. WORD PAIR AUDIO SEGMENTS ---
        for i, (eng, tel) in enumerate(words_list):
            temp_eng_file = f"temp_eng_{i}.mp3"
            temp_tel_file = f"temp_tel_{i}.mp3"
            temp_files_to_clean.extend([temp_eng_file, temp_tel_file])

            tts_en = gTTS(text=eng, lang='en')
            tts_en.save(temp_eng_file)
            eng_clip = AudioFileClip(temp_eng_file)

            tts_te = gTTS(text=tel, lang=lang_code)
            tts_te.save(temp_tel_file)
            tel_clip = AudioFileClip(temp_tel_file)
            tel_clip = tel_clip.fx(vfx.speedx, INTRO_SPEED_FACTOR) 

            pair_duration = eng_clip.duration + PAUSE_DURATION + tel_clip.duration
            
            word_pair_timings.append({
                'start': current_time,
                'end': current_time + pair_duration,
                'english': eng,
                'target_lang': tel
            })
            current_time += pair_duration
            
            final_audio_clips.append(eng_clip)
            final_audio_clips.append(pause_audio_clip)
            final_audio_clips.append(tel_clip)

        # --- 3. OUTRO AUDIO SEGMENT ---
        outro_duration = 0
        final_audio_clips.append(create_silence_clip(0.5))
        outro_duration += 0.5
        
        final_outro_text = ""
        for i, phrase in enumerate(outro_tel_phrases):
            temp_outro_file = f"temp_outro_{i}.mp3"
            temp_files_to_clean.append(temp_outro_file)
            tts_outro = gTTS(text=phrase, lang=lang_code)
            tts_outro.save(temp_outro_file)
            outro_clip = AudioFileClip(temp_outro_file)
            outro_clip = outro_clip.fx(vfx.speedx, INTRO_SPEED_FACTOR) 
            final_audio_clips.append(outro_clip)
            final_outro_text += phrase + " " 
            outro_duration += outro_clip.duration
            
            if i < len(outro_tel_phrases) - 1:
                final_audio_clips.append(create_silence_clip(0.3 / INTRO_SPEED_FACTOR))
                outro_duration += (0.3 / INTRO_SPEED_FACTOR)

        final_audio_clip = concatenate_audioclips(final_audio_clips)
        final_audio_clip.write_audiofile(f"temp_{topic}_audio.mp3", codec='mp3', verbose=False, logger=None)
        
        print(f"✅ Final audio clip saved (Total Duration: {final_audio_clip.duration:.2f}s).")
        
        return AudioFileClip(f"temp_{topic}_audio.mp3"), word_pair_timings, intro_duration, final_outro_text.strip()

    except Exception as e:
        print(f"❌ Error generating audio with gTTS/MoviePy: {e}")
        return None, None, 0, ""
    finally:
        for f in temp_files_to_clean:
            if os.path.exists(f):
                os.remove(f)

def download_image_for_word_bs4(topic,word, save_directory):
    """Fetches Google Images search results and picks a random image from the top candidates,
       with validation to ensure a proper image file is downloaded."""
    clean_word = word.replace(" ", "_").lower()
    save_path = os.path.join(save_directory, f"{clean_word}.jpg")

    if os.path.exists(save_path):
        return save_path
    
    strict_query = f"{word} high quality photo"  
    # 5. Assemble the Final Query
    if topic.lower() not in ["verbs","adjectives","adverbs","verb","adjective","adverb","conjuction","preposition"]:
        strict_query = f"{word} in {topic} high quality photo"
    
    # strict_query = f"{word} {topic} real hd photo high quality realistic natural true picture -cartoon -clipart -vector -diagram -logo -drawing"
    encoded_query = requests.utils.quote(strict_query)
    search_url = f"https://www.google.com/search?tbm=isch&q={encoded_query}" 
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    for attempt in range(1, MAX_RETRIES + 1):
        all_image_urls = [] 
        
        try:
            response = requests.get(search_url, headers=headers, timeout=10)
            print(response.status_code)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            primary_pattern = r'\["((?:http|https):\/\/[\w\d\.\/\?\&\=\-\_\%]+?)"\s*,\s*\d+\s*,\s*\d+\s*\]'
            ou_pattern = r'"ou":"(http[^"]+)"'
            
            for script in soup.find_all('script'):
                script_text = script.text
                all_image_urls.extend(re.findall(primary_pattern, script_text))
                all_image_urls.extend(re.findall(ou_pattern, script_text))
            
            if not all_image_urls:
                raise ValueError("No usable image URLs found.")

            candidate_urls = all_image_urls[:5] 
            if not candidate_urls:
                 raise ValueError("No viable image candidates found.")

            image_src = random.choice(candidate_urls)
            
            image_response = requests.get(image_src, stream=True, timeout=15, headers=headers)
            image_response.raise_for_status()

            with open(save_path, 'wb') as file:
                for chunk in image_response.iter_content(chunk_size=8192):
                    file.write(chunk)
                    
            # 🟢 FIX 2: Image Validation using PIL
            try:
                # 1. Verify file integrity
                img = Image.open(save_path)
                img.verify() 
                # 2. Re-open and save as JPEG to ensure a clean, common format
                Image.open(save_path).convert('RGB').save(save_path, 'JPEG')
                return save_path 
            except Exception as img_e:
                print(f"⚠️ Validation failed for downloaded file for '{word}'. Deleting and trying next candidate. Error: {img_e}")
                if os.path.exists(save_path):
                    os.remove(save_path)
                # Continue the inner loop to check the next candidate URL
                continue 

        except requests.exceptions.RequestException as e:
            pass
        except ValueError as e:
            pass
        except Exception as e:
            pass

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return None

def download_and_filter_images(topic,words_list):
    """Downloads images and returns a list of word pairs that were successful."""
    print("\n📦 Starting Google Images search process with retries...")
    os.makedirs(IMAGE_DIRECTORY, exist_ok=True)
    successful_words = []
    
    for i, (eng, tel) in enumerate(words_list):
        time.sleep(random.uniform(1.0, 3.0)) 
        print(f"Processing word {i+1}/{len(words_list)}: {eng} ({tel})")
        
        image_path = download_image_for_word_bs4(topic,eng, IMAGE_DIRECTORY)
        
        if image_path:
            successful_words.append((eng, tel))
        else:
            print(f"⚠️ Final failure for '{eng}'. This word will be **EXCLUDED** from the video.")

    print(f"\n✅ Image download and filtering complete. {len(successful_words)}/{len(words_list)} words will be used.")
    return successful_words
# ==============================================================================
# 5. VIDEO COMPOSITION
# ==============================================================================

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip # Assuming ImageClip is used later

def create_topic_intro_frame(topic, duration, lang_font_path):
    """
    Creates the first frame showing the topic name with clearer English formatting,
    with dynamic font size adjustment to prevent text cutoff.
    """
    base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color='#F0F0F0') 
    draw = ImageDraw.Draw(base_img)
    
    # Define a safety margin (e.g., 50 pixels on each side)
    MAX_TEXT_WIDTH = VIDEO_WIDTH - 100 
    
    # --- 1. English Header (No change needed, text is short) ---
    try:
        header_font = ImageFont.truetype(ENGLISH_FONT_PATH, 35)
    except IOError:
        header_font = ImageFont.load_default()
        print(f"⚠️ Warning: Could not find font at {ENGLISH_FONT_PATH}. Using default font for header.")
        
    draw.text(
        (VIDEO_WIDTH / 2, VIDEO_HEIGHT * 0.3), 
        "NAREN ENGLISH VOCABULARY", 
        font=header_font, 
        fill=(100, 100, 100), 
        anchor="mm"
    )
    
    # --- 2. Topic Name in English (Larger font, with dynamic scaling) ---
    topic_text = topic.upper()
    initial_font_size = 100 # Start with the desired size
    
    try:
        # Load font and check bounds, reducing size if necessary
        eng_font = ImageFont.truetype(ENGLISH_FONT_PATH, initial_font_size)
        
        current_font_size = initial_font_size
        while draw.textlength(topic_text, font=eng_font) > MAX_TEXT_WIDTH and current_font_size > 30:
            current_font_size -= 5
            eng_font = ImageFont.truetype(ENGLISH_FONT_PATH, current_font_size)
            
        if current_font_size <= 30:
            print(f"❌ Error: Topic text '{topic_text}' is too long even at minimum size.")

    except IOError:
        # Fallback if the font file is missing
        eng_font = ImageFont.load_default()
        print(f"⚠️ Warning: Could not find font at {ENGLISH_FONT_PATH}. Using default font for topic.")
        
    # Draw the text with the potentially smaller, safely-sized font
    draw.text(
        (VIDEO_WIDTH / 2, VIDEO_HEIGHT * 0.45), 
        topic_text, 
        font=eng_font, 
        fill=(0, 0, 0), 
        anchor="mm"
    )

    # --- 3. Target Language Context ---
    try:
        tel_font = ImageFont.truetype(lang_font_path, 60)
    except IOError:
        tel_font = ImageFont.load_default()
        print(f"⚠️ Warning: Could not find font at {lang_font_path}. Using default font for translation context.")

    draw.text(
        (VIDEO_WIDTH / 2, VIDEO_HEIGHT * 0.65), 
        f"({TARGET_LANGUAGE} Translation)", 
        font=tel_font, 
        fill=(100, 100, 100), 
        anchor="mm"
    )
    
    temp_intro_image_file = f"temp_intro_frame_{topic}.png"
    base_img.save(temp_intro_image_file)
    
    intro_clip = ImageClip(temp_intro_image_file, duration=duration)
    return intro_clip, temp_intro_image_file

def create_outro_frame(duration):
    """Creates the last frame with the hardcoded English call-to-action text."""
    base_img = Image.new('RGB', (VIDEO_WIDTH, VIDEO_HEIGHT), color='#2C3E50') 
    draw = ImageDraw.Draw(base_img)
    
    ENGLISH_CALL_TO_ACTION = "Please like and subscribe the channel"
    
    eng_font_large = ImageFont.truetype(ENGLISH_FONT_PATH, 100)
    draw.text(
        (VIDEO_WIDTH / 2, VIDEO_HEIGHT * 0.35), 
        "THANK YOU!", 
        font=eng_font_large, 
        fill=(230, 126, 34), 
        anchor="mm"
    )

    eng_font_small = ImageFont.truetype(ENGLISH_FONT_PATH, 40)
    draw.text(
        (VIDEO_WIDTH / 2, VIDEO_HEIGHT * 0.6), 
        ENGLISH_CALL_TO_ACTION, 
        font=eng_font_small, 
        fill=(255, 255, 255), 
        anchor="mm",
        align="center"
    )
    
    temp_outro_image_file = f"temp_outro_frame_english.png"
    base_img.save(temp_outro_image_file)
    
    outro_clip = ImageClip(temp_outro_image_file, duration=duration)
    return outro_clip, temp_outro_image_file


def create_word_segment_clip(item, lang_font_path, duration):
    """Creates the video segment for a single word pair."""
    eng_word = item['english']
    tel_word = item['target_lang']
    
    english_word_clean = eng_word.replace(" ", "_").lower()
    image_path = os.path.join(IMAGE_DIRECTORY, f"{english_word_clean}.jpg") 
    
    if os.path.exists(image_path):
        img_clip = ImageClip(image_path).set_duration(duration)
        img_clip = img_clip.resize(newsize=(IMAGE_PANEL_WIDTH, VIDEO_HEIGHT))
        img_clip = img_clip.set_position((TEXT_PANEL_WIDTH, 'center'))
    else:
        img_clip = ColorClip(
            size=(IMAGE_PANEL_WIDTH, VIDEO_HEIGHT), 
            color=[0, 0, 0]
        ).set_duration(duration)
        img_clip = img_clip.set_position((TEXT_PANEL_WIDTH, 'center'))

    base_img = Image.new('RGB', (TEXT_PANEL_WIDTH, VIDEO_HEIGHT), color='white')
    
    base_img, _ = draw_text_on_image(
        base_img, eng_word, ENGLISH_FONT_PATH, (0, 0, 0), 
        TEXT_X_POSITION_CENTER, TEXT_Y_POSITION_ENG
    )

    base_img, _ = draw_text_on_image(
        base_img, tel_word, lang_font_path, (0, 0, 0), 
        TEXT_X_POSITION_CENTER, TEXT_Y_POSITION_TEL
    )
    
    temp_text_image_file = f"temp_text_panel_{eng_word.replace(' ', '')}.png" 
    base_img.save(temp_text_image_file)

    text_clip = ImageClip(temp_text_image_file, duration=duration)
    text_clip = text_clip.set_position((0, 'center')) 
    
    final_segment_clip = CompositeVideoClip(
        [text_clip, img_clip],
        size=(VIDEO_WIDTH, VIDEO_HEIGHT)
    )
    
    return final_segment_clip.set_duration(duration), temp_text_image_file


def create_full_video_track(word_pair_timings, audio_clip, lang_font_path, topic):
    print("\n⏱️ Creating synchronized full-screen content timeline...")
    
    all_composite_clips = []
    temp_files_to_clean = []
    final_duration = audio_clip.duration
    
    # --- 1. INTRO CLIP ---
    intro_end_time = word_pair_timings[0]['start'] if word_pair_timings else final_duration
    
    intro_clip_base, intro_file = create_topic_intro_frame(topic, intro_end_time, lang_font_path)
    intro_clip_base = intro_clip_base.fx(vfx.speedx, INTRO_SPEED_FACTOR) 
    intro_clip_base = intro_clip_base.set_duration(intro_end_time)
    
    all_composite_clips.append(intro_clip_base.set_start(0))
    temp_files_to_clean.append(intro_file)

    # --- 2. WORD SEGMENTS ---
    for i, item in enumerate(word_pair_timings):
        start_time = item['start']
        end_time = item['end']
        duration = end_time - start_time
        
        segment_clip, temp_text_file = create_word_segment_clip(item, lang_font_path, duration)
        segment_clip = segment_clip.set_start(start_time)
        
        all_composite_clips.append(segment_clip)
        temp_files_to_clean.append(temp_text_file)

    # --- 3. OUTRO CLIP ---
    outro_start_time = word_pair_timings[-1]['end'] if word_pair_timings else intro_end_time
    outro_duration = final_duration - outro_start_time
    
    if outro_duration > 0:
        outro_clip, outro_file = create_outro_frame(outro_duration)
        all_composite_clips.append(outro_clip.set_start(outro_start_time))
        temp_files_to_clean.append(outro_file)
        
    
    if not all_composite_clips:
        print("❌ Final composition failed as no clips were generated.")
        return None, []
        
    final_video_track = CompositeVideoClip(
        all_composite_clips, 
        size=(VIDEO_WIDTH, VIDEO_HEIGHT)
    ).set_duration(final_duration)
    
    return final_video_track, temp_files_to_clean


def create_video(audio_clip, video_clip, output_path, video_name): 
    
    if audio_clip.duration < 1 or video_clip.duration < 1:
        print("❌ Video generation aborted: Audio or video track is too short.")
        return None
        
    final_clip = video_clip.set_audio(audio_clip).set_duration(audio_clip.duration)

    final_output_file = os.path.join(output_path, video_name)
    print(f"\n🎬 Starting video export to {final_output_file} (Duration: {final_clip.duration:.2f}s)...")
    
    try:
        final_clip.write_videofile(
            final_output_file, 
            codec='libx264', 
            audio_codec='aac', 
            bitrate='5000k', 
            temp_audiofile='temp-video-audio.m4a', 
            remove_temp=True,
            fps=24,
            threads=4,
            verbose=False,
            logger=None
        )
        print(f"\n✨ Successfully created video: {final_output_file}")
        return final_output_file # Return the path on success
    except Exception as e:
        print(f"❌ Video export failed: {e}")
        return None # Return None on failure


# ==============================================================================
# 6. YOUTUBE UPLOAD FUNCTIONS (Integrated from user snippet)
# ==============================================================================

def get_authenticated_service(TOKEN_FILE, CLIENT_SECRETS_FILE):
    """
    Authenticates the user and returns the authorized YouTube API service object.
    """
    credentials = None

    if os.path.exists(TOKEN_FILE):
        print("Loading credentials from token.json...")
        try:
            credentials = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"Error loading credentials: {e}. Re-authenticating.")
            credentials = None

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            print("Refreshing access token...")
            try:
                credentials.refresh(Request())
            except Exception as e:
                print(f"Token refresh failed: {e}. Starting new OAuth flow.")
                credentials = None

        if not credentials:
            print("Starting new OAuth 2.0 flow...")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                credentials = flow.run_local_server(port=0)
            except Exception as e:
                print(f"❌ Critical error during OAuth flow. Check CLIENT_SECRETS_FILE path and contents: {e}")
                return None

        with open(TOKEN_FILE, 'w') as token:
            token.write(credentials.to_json())

    return build(API_SERVICE_NAME, API_VERSION, credentials=credentials)


def upload_video_to_youtube(youtube_service, file_path, title, description, keywords, category_id, privacy_status):
    """Handles the actual video upload using the YouTube API service."""

    if not youtube_service:
        print("❌ Upload failed: YouTube service is not authenticated.")
        return False
    
    if not os.path.exists(file_path):
        print(f"❌ Upload failed: Video file not found at {file_path}")
        return False
    
    # Extract tags from the combined keywords/hashtags string
    tags = [tag.strip() for tag in keywords.split(',') if tag.strip()]

    body = {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': category_id
        },
        'status': {
            'privacyStatus': privacy_status
        }
    }

    media_file = MediaFileUpload(file_path, chunksize=-1, resumable=True)

    print(f"\n🚀 Attempting to upload: {title} ({os.path.basename(file_path)})")

    request = youtube_service.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media_file
    )

    max_retries = 10
    retry_delay = 5 

    for i in range(max_retries):
        try:
            print(f"Uploading... Attempt {i+1}/{max_retries}")

            response = request.execute()

            if response is not None:
                print('\n--- Upload Successful ---')
                print(f"Video ID: {response.get('id')}")
                print(f"Title: {response.get('snippet').get('title')}")
                print(f"Link: https://www.youtube.com/watch?v={response.get('id')}")
                print('------------------------\n')
                return True 

        except HttpError as e:
            if e.resp.status in [500, 502, 503, 504]:
                print(f"HTTP Error {e.resp.status}: Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                print(f"❌ An unrecoverable HTTP error occurred: {e}")
                break 
        except Exception as e:
            print(f"❌ An unknown error occurred during upload: {e}")
            break
            
    return False

# ==============================================================================
# 7. MAIN EXECUTION LOOP (FIXED)
# ==============================================================================
def run_video_generation(video_index, youtube_service):
    
    # 1. Determine Video Type and Target Duration & Dynamically Set DIMENSIONS
    # NOTE: We use 'global' to change the module-level configuration constants 
    # that the video functions (section 5) rely on.
    global VIDEO_WIDTH, VIDEO_HEIGHT, TEXT_PANEL_WIDTH, IMAGE_PANEL_WIDTH, TEXT_X_POSITION_CENTER
    
    duration_type = VIDEO_DURATION_TYPE if VIDEO_DURATION_TYPE else random.choice(['Short', 'Long'])
    
    if duration_type == 'Short':
        target_duration = SHORT_DURATION_TARGET
        output_path = OUTPUT_DIR_SHORTS
        
        # 🟢 FIX 1A: Set Vertical Dimensions for Shorts (9:16)
        VIDEO_WIDTH = 720
        VIDEO_HEIGHT = 1280 
    else:
        target_duration = LONG_DURATION_TARGET
        output_path = OUTPUT_DIR_VIDEOS
        
        # 🟢 FIX 1B: Set/Restore Horizontal Dimensions for Long Videos (16:9)
        VIDEO_WIDTH = 1280 
        VIDEO_HEIGHT = 720 
        
    # 🟢 FIX 1C: Recalculate ALL constants derived from VIDEO_WIDTH/HEIGHT
    TEXT_PANEL_WIDTH = int(VIDEO_WIDTH * TEXT_PANEL_WIDTH_PERCENT)
    IMAGE_PANEL_WIDTH = VIDEO_WIDTH - TEXT_PANEL_WIDTH
    TEXT_X_POSITION_CENTER = int(TEXT_PANEL_WIDTH / 2) 

    print(f"\n--- 🎥 Starting Video {video_index+1} ({TARGET_LANGUAGE}/{duration_type} - WxH: {VIDEO_WIDTH}x{VIDEO_HEIGHT}) ---")
    
    lang_code, lang_font_path = get_language_config(TARGET_LANGUAGE)
    
    # 2. Generate Topic and Word Pairs
    topic = select_or_generate_topic()
    estimated_word_count = max(8, int(target_duration / 3.5)) 
    words = get_word_pairs(topic, count=estimated_word_count)
    if not words: return

    # 3. Download Images
    successful_words = download_and_filter_images(topic,words)
    if not successful_words:
        print(f"\n🚫 FATAL: No images were successfully downloaded for video {video_index+1}.")
        return

    # 4. Generate Metadata (Title and Description)
    sample_words = random.sample(successful_words, min(10, len(successful_words)))
    title, description = generate_seo_metadata(topic, TARGET_LANGUAGE, sample_words, duration_type)
    keywords = extract_tags_from_description(description)
    
    # 5. Generate Audio and Video Tracks
    audio_clip, word_pair_timings, _, _ = generate_audio(successful_words, topic, lang_code)
    if not audio_clip or not word_pair_timings: return
        
    final_video_track, temp_files_to_clean = create_full_video_track(
        word_pair_timings, audio_clip, lang_font_path, topic
    )
    if not final_video_track: return
        
    # 6. Export Video File
    video_name = f"{TARGET_LANGUAGE}_{topic.replace(' ', '_')}_{duration_type}_{video_index+1}.mp4"
    final_video_file_path = create_video(audio_clip, final_video_track, output_path, video_name)
    
    # 7. Upload to YouTube and Cleanup
    if final_video_file_path:
        # We set the privacy status to 'public' or 'unlisted' for automated uploads
        privacy_status = 'public'
        upload_successful = upload_video_to_youtube(
            youtube_service, 
            final_video_file_path, 
            title, 
            description, 
            keywords, 
            VIDEO_CATEGORY_ID, 
            privacy_status
        )

        if upload_successful:
            os.remove(final_video_file_path)
            print(f"🗑️ Successfully deleted local video file: {final_video_file_path}")
        else:
            TOKEN_FILE="main_token.json"
            CLIENT_SECRETS_FILE=r"c:\Users\Admin\Downloads\client_secret_148555146728-i8s3u7mplv4414emjrmrmhnuvdqeicrp.apps.googleusercontent.com.json"
            youtube_service = get_authenticated_service(TOKEN_FILE, CLIENT_SECRETS_FILE)
            upload_successful = upload_video_to_youtube(
            youtube_service, 
            final_video_file_path, 
            title, 
            description, 
            keywords, 
            VIDEO_CATEGORY_ID, 
            privacy_status
            )
            if upload_successful:
                os.remove(final_video_file_path)
                print(f"🗑️ Successfully deleted local video file: {final_video_file_path}")
            else:
                print(f"⚠️ Video upload failed. Keeping local file: {final_video_file_path}")

    # 8. Final Local Cleanup
    for f in temp_files_to_clean:
        if os.path.exists(f):
            os.remove(f)

    temp_audio_file = f"temp_{topic}_audio.mp3"
    if os.path.exists(temp_audio_file):
        os.remove(temp_audio_file)
        
    if os.path.exists(IMAGE_DIRECTORY):
        shutil.rmtree(IMAGE_DIRECTORY)
        print(f"🗑️ Successfully removed temporary image directory: {IMAGE_DIRECTORY}")


if __name__ == "__main__":
    
    # 1. Initialize Directories
    os.makedirs(OUTPUT_DIR_SHORTS, exist_ok=True)
    os.makedirs(OUTPUT_DIR_VIDEOS, exist_ok=True)
    
    # 2. Authenticate YouTube Service (Once)
    print("\nStarting YouTube API authentication...")

    youtube = get_authenticated_service(TOKEN_FILE, CLIENT_SECRETS_FILE)

    if not youtube:
        print("❌ Cannot proceed without a valid YouTube API service. Please check client_secrets.json.")
    else:
        print("✅ YouTube service authenticated successfully.")
        
        # 3. Main Loop
        for i in range(OUTPUT_VIDEO_COUNT):
            run_video_generation(i, youtube)
            time.sleep(5) 
        
        print("\n\n#################################################")
        print(f"🎬 Video generation and upload process complete. Attempted {OUTPUT_VIDEO_COUNT} videos.")
        print("#################################################")

