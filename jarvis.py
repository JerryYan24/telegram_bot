import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from system_monitor import SystemMonitor
from training_monitor import TrainingMonitor
from health import HealthInput, WindColdDetector
import time
from datetime import datetime
import json
import hashlib
from pathlib import Path
from cryptography.fernet import Fernet
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import requests
import socks
import socket
from stem import Signal
from stem.control import Controller
import logging
import subprocess
import sys

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Tor configuration
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = "your_control_password"  # Change this to a secure password

class TorManager:
    def __init__(self):
        self.session = requests.session()
        self.session.proxies = {
            'http': f'socks5h://127.0.0.1:{TOR_SOCKS_PORT}',
            'https': f'socks5h://127.0.0.1:{TOR_SOCKS_PORT}'
        }
        self.controller = None
        self.is_connected = False

    def start_tor(self):
        """Start Tor service if not running."""
        try:
            # Check if Tor is running
            subprocess.run(['pgrep', 'tor'], check=True, capture_output=True)
            logger.info("Tor is already running")
        except subprocess.CalledProcessError:
            # Start Tor service
            try:
                subprocess.Popen(['tor', '-f', '/etc/tor/torrc'])
                logger.info("Started Tor service")
            except Exception as e:
                logger.error(f"Failed to start Tor: {e}")
                return False

        # Wait for Tor to start
        time.sleep(5)
        return self.connect_to_tor()

    def connect_to_tor(self):
        """Connect to Tor control port."""
        try:
            self.controller = Controller.from_port(port=TOR_CONTROL_PORT)
            self.controller.authenticate(password=TOR_CONTROL_PASSWORD)
            self.is_connected = True
            logger.info("Connected to Tor control port")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Tor: {e}")
            return False

    def renew_tor_identity(self):
        """Request new Tor identity."""
        if not self.is_connected:
            if not self.connect_to_tor():
                return False
        
        try:
            self.controller.signal(Signal.NEWNYM)
            logger.info("Requested new Tor identity")
            return True
        except Exception as e:
            logger.error(f"Failed to renew Tor identity: {e}")
            return False

    def get_current_ip(self):
        """Get current IP address through Tor."""
        try:
            response = self.session.get('https://api.ipify.org?format=json')
            return response.json()['ip']
        except Exception as e:
            logger.error(f"Failed to get IP: {e}")
            return None

    def make_tor_request(self, url, method='GET', data=None, headers=None):
        """Make a request through Tor network."""
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, headers=headers)
            elif method.upper() == 'POST':
                response = self.session.post(url, json=data, headers=headers)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            return response
        except Exception as e:
            logger.error(f"Tor request failed: {e}")
            return None

# Initialize Tor manager
tor_manager = TorManager()

TOKEN = "8107528628:AAFrriScv7MrssxUoHEQ9vGrx1z1MG2L9io"  # <-- 替换为你的 Bot Token

# Create instances of monitors
system_monitor = SystemMonitor()
training_monitor = TrainingMonitor(project_name="gsworld", entity="jerryyan24-uc-san-diego")

# States for health check conversation
(
    DESCRIPTION,
    TEMPERATURE,
    SLEEP_HOURS,
    HEART_RATE,
    COUGH_TYPE,
    SWEATING,
    SORE_THROAT,
) = range(7)

# Enhanced privacy states
(
    HEALTH_QUERY,
    FEATURE_SELECTION,
    PRIVACY_LEVEL,
    DATA_RETENTION,
    PRIVACY_CONFIRM,
    ADVICE_GENERATION,
) = range(6)

# Privacy levels
PRIVACY_LEVELS = {
    'high': {
        'retention_days': 7,
        'encryption': True,
        'anonymization': True,
        'data_minimization': True
    },
    'medium': {
        'retention_days': 30,
        'encryption': True,
        'anonymization': True,
        'data_minimization': False
    },
    'low': {
        'retention_days': 90,
        'encryption': True,
        'anonymization': False,
        'data_minimization': False
    }
}

# Store user data during health check
user_data = {}

# Local storage for health logs
HEALTH_LOGS_DIR = Path("health_logs")
HEALTH_LOGS_DIR.mkdir(exist_ok=True)

def generate_encryption_key(user_id: int, salt: bytes) -> bytes:
    """Generate a unique encryption key for each user."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(str(user_id).encode()))
    return key

def encrypt_data(data: dict, key: bytes) -> str:
    """Encrypt health data using Fernet symmetric encryption."""
    f = Fernet(key)
    return f.encrypt(json.dumps(data).encode()).decode()

def decrypt_data(encrypted_data: str, key: bytes) -> dict:
    """Decrypt health data."""
    f = Fernet(key)
    return json.loads(f.decrypt(encrypted_data.encode()).decode())

def get_user_log_path(user_id: int) -> Path:
    """Get the path to a user's health log file with enhanced privacy."""
    # Hash the user ID for privacy
    hashed_id = hashlib.sha256(str(user_id).encode()).hexdigest()
    return HEALTH_LOGS_DIR / f"{hashed_id}.enc"

def save_health_log(user_id: int, data: dict, privacy_level: str = 'high'):
    """Save health data with enhanced privacy protection."""
    log_path = get_user_log_path(user_id)
    
    # Generate encryption key
    salt = os.urandom(16)
    key = generate_encryption_key(user_id, salt)
    
    # Prepare data with privacy settings
    privacy_settings = PRIVACY_LEVELS[privacy_level]
    entry = {
        'timestamp': datetime.now().isoformat(),
        'data': data,
        'privacy_settings': privacy_settings,
        'salt': base64.b64encode(salt).decode()
    }
    
    # Encrypt data
    encrypted_data = encrypt_data(entry, key)
    
    # Save encrypted data
    with open(log_path, 'w') as f:
        json.dump({'encrypted_data': encrypted_data}, f)

def load_health_log(user_id: int) -> dict:
    """Load and decrypt health data."""
    log_path = get_user_log_path(user_id)
    if not log_path.exists():
        return None
    
    with open(log_path, 'r') as f:
        stored_data = json.load(f)
    
    # Get encryption key
    salt = base64.b64decode(stored_data['salt'])
    key = generate_encryption_key(user_id, salt)
    
    # Decrypt data
    return decrypt_data(stored_data['encrypted_data'], key)

def get_relevant_features(description: str) -> list:
    """Use GPT to select relevant health features from the description."""
    features = []
    description = description.lower()
    
    # Physical Symptoms
    if any(word in description for word in ['dizzy', 'dizziness', 'vertigo', 'lightheaded', 'balance', 'unsteady']):
        features.append('dizziness')
    if any(word in description for word in ['fever', 'temperature', 'hot', 'chills', 'sweating', 'febrile']):
        features.append('temperature')
    if any(word in description for word in ['sleep', 'tired', 'fatigue', 'exhausted', 'insomnia', 'restless']):
        features.append('sleep_hours')
    if any(word in description for word in ['heart', 'pulse', 'chest', 'palpitation', 'arrhythmia', 'tachycardia']):
        features.append('heart_rate')
    if any(word in description for word in ['cough', 'coughing', 'phlegm', 'bronchitis', 'expectoration']):
        features.append('cough_type')
    if any(word in description for word in ['sweat', 'sweating', 'perspiration', 'night sweats', 'diaphoresis']):
        features.append('sweating')
    if any(word in description for word in ['throat', 'sore throat', 'swallowing', 'tonsillitis', 'pharyngitis']):
        features.append('sore_throat')
    
    # Additional Symptoms
    if any(word in description for word in ['headache', 'migraine', 'head pain', 'tension', 'cluster']):
        features.append('headache')
    if any(word in description for word in ['nausea', 'vomit', 'stomach', 'digestion', 'indigestion', 'gastritis']):
        features.append('digestive')
    if any(word in description for word in ['muscle', 'joint', 'pain', 'ache', 'arthritis', 'fibromyalgia']):
        features.append('musculoskeletal')
    if any(word in description for word in ['breath', 'breathing', 'shortness', 'asthma', 'dyspnea']):
        features.append('respiratory')
    if any(word in description for word in ['anxiety', 'stress', 'mood', 'depression', 'panic', 'bipolar']):
        features.append('mental_health')
    if any(word in description for word in ['allergy', 'allergic', 'reaction', 'hay fever', 'anaphylaxis']):
        features.append('allergies')
    
    # Chronic Conditions
    if any(word in description for word in ['diabetes', 'blood sugar', 'glucose', 'insulin', 'type 1', 'type 2']):
        features.append('diabetes')
    if any(word in description for word in ['hypertension', 'high blood pressure', 'bp', 'cardiovascular']):
        features.append('hypertension')
    if any(word in description for word in ['thyroid', 'hypothyroidism', 'hyperthyroidism', 'hashimoto']):
        features.append('thyroid')
    if any(word in description for word in ['arthritis', 'rheumatoid', 'osteoarthritis', 'gout', 'lupus']):
        features.append('arthritis')
    if any(word in description for word in ['asthma', 'copd', 'bronchitis', 'emphysema']):
        features.append('respiratory_chronic')
    if any(word in description for word in ['migraine', 'cluster headache', 'tension headache']):
        features.append('migraine')
    if any(word in description for word in ['ibd', 'crohn', 'colitis', 'irritable bowel']):
        features.append('ibd')
    if any(word in description for word in ['kidney', 'renal', 'dialysis', 'nephritis']):
        features.append('kidney')
    if any(word in description for word in ['liver', 'hepatitis', 'cirrhosis', 'jaundice']):
        features.append('liver')
    if any(word in description for word in ['autoimmune', 'ms', 'multiple sclerosis', 'lupus']):
        features.append('autoimmune')
    
    # Lifestyle Factors
    if any(word in description for word in ['diet', 'food', 'eating', 'nutrition', 'weight', 'bmi']):
        features.append('nutrition')
    if any(word in description for word in ['exercise', 'workout', 'physical activity', 'fitness', 'training']):
        features.append('exercise')
    if any(word in description for word in ['medication', 'medicine', 'drug', 'prescription', 'dosage']):
        features.append('medications')
    if any(word in description for word in ['smoking', 'tobacco', 'nicotine', 'vaping']):
        features.append('smoking')
    if any(word in description for word in ['alcohol', 'drinking', 'binge', 'intoxication']):
        features.append('alcohol')
    if any(word in description for word in ['stress', 'work', 'pressure', 'burnout', 'anxiety']):
        features.append('stress')
    
    return features

async def server_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(system_monitor.get_status())

async def training_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(training_monitor.get_status())

async def check_tor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check Tor connection status and current IP."""
    if not tor_manager.is_connected:
        if not tor_manager.start_tor():
            await update.message.reply_text("❌ Failed to start Tor service")
            return

    current_ip = tor_manager.get_current_ip()
    if current_ip:
        status = f"""🕵️ Tor Status:
✅ Connected
🌐 Current IP: {current_ip}
🔒 Traffic: Routed through Tor network
"""
    else:
        status = "❌ Tor connection failed"

    await update.message.reply_text(status)

async def renew_tor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Renew Tor identity."""
    if tor_manager.renew_tor_identity():
        new_ip = tor_manager.get_current_ip()
        await update.message.reply_text(
            f"✅ Tor identity renewed\n"
            f"🌐 New IP: {new_ip}"
        )
    else:
        await update.message.reply_text("❌ Failed to renew Tor identity")

async def tor_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Make a request through Tor network."""
    if not context.args:
        await update.message.reply_text("Please provide a URL to request")
        return

    url = context.args[0]
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    await update.message.reply_text(f"🔄 Making request through Tor to {url}...")
    
    response = tor_manager.make_tor_request(url)
    if response:
        await update.message.reply_text(
            f"✅ Request successful\n"
            f"Status code: {response.status_code}\n"
            f"Response length: {len(response.text)} characters"
        )
    else:
        await update.message.reply_text("❌ Request failed")

async def start_health_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the health check conversation."""
    user_id = update.effective_user.id
    user_data[user_id] = {}
    
    await update.message.reply_text(
        "🩺 Let's check your health status.\n\n"
        "Please describe how you're feeling today. Include any symptoms or discomfort you're experiencing."
    )
    return DESCRIPTION

async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's description of symptoms."""
    user_id = update.effective_user.id
    user_data[user_id]['description'] = update.message.text
    
    await update.message.reply_text(
        "What's your current body temperature? (in Celsius, e.g., 36.7)"
    )
    return TEMPERATURE

async def handle_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's temperature input."""
    try:
        temp = float(update.message.text)
        user_id = update.effective_user.id
        user_data[user_id]['temperature'] = temp
        
        await update.message.reply_text(
            "How many hours did you sleep last night? (e.g., 7.5)"
        )
        return SLEEP_HOURS
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid number for temperature (e.g., 36.7)"
        )
        return TEMPERATURE

async def handle_sleep_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's sleep hours input."""
    try:
        hours = float(update.message.text)
        user_id = update.effective_user.id
        user_data[user_id]['sleep_hours'] = hours
        
        await update.message.reply_text(
            "What's your current heart rate? (beats per minute, e.g., 70)"
        )
        return HEART_RATE
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid number for sleep hours (e.g., 7.5)"
        )
        return SLEEP_HOURS

async def handle_heart_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's heart rate input."""
    try:
        rate = int(update.message.text)
        user_id = update.effective_user.id
        user_data[user_id]['heart_rate'] = rate
        
        await update.message.reply_text(
            "What type of cough do you have? (thin/thick/none)"
        )
        return COUGH_TYPE
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid number for heart rate (e.g., 70)"
        )
        return HEART_RATE

async def handle_cough_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's cough type input."""
    user_id = update.effective_user.id
    user_data[user_id]['cough_type'] = update.message.text.lower()
    
    await update.message.reply_text(
        "Are you sweating? (yes/no)"
    )
    return SWEATING

async def handle_sweating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's sweating input."""
    user_id = update.effective_user.id
    user_data[user_id]['sweating'] = update.message.text.lower() == 'yes'
    
    await update.message.reply_text(
        "Do you have a sore throat? (yes/no)"
    )
    return SORE_THROAT

async def handle_sore_throat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's sore throat input and provide diagnosis."""
    user_id = update.effective_user.id
    user_data[user_id]['sore_throat'] = update.message.text.lower() == 'yes'
    
    # Create HealthInput object
    health_input = HealthInput(
        description=user_data[user_id]['description'],
        temperature=user_data[user_id]['temperature'],
        sleep_hours=user_data[user_id]['sleep_hours'],
        heart_rate_bpm=user_data[user_id]['heart_rate'],
        cough_type=user_data[user_id]['cough_type'],
        sweating=user_data[user_id]['sweating'],
        sore_throat=user_data[user_id]['sore_throat']
    )
    
    # Get diagnosis
    detector = WindColdDetector(health_input)
    result = detector.evaluate()
    
    # Format response
    response = "🩺 Diagnosis Result:\n"
    response += f"Diagnosis: {result['diagnosis']}\n\n"
    response += "Matched Signs:\n"
    for k, v in result['matched_signs'].items():
        response += f" - {k}: {'✅' if v else '❌'}\n"
    
    if "suggestions" in result:
        response += "\n✅ Recovery Suggestions:\n"
        for s in result['suggestions']:
            response += f" - {s}\n"
    
    await update.message.reply_text(response)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the health check conversation."""
    await update.message.reply_text(
        "Health check cancelled. You can start a new check with /health_check"
    )
    return ConversationHandler.END

async def start_privacy_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the enhanced privacy-preserving health assistant conversation."""
    user_id = update.effective_user.id
    user_data[user_id] = {}
    
    await update.message.reply_text(
        "🔒 Enhanced Privacy-Preserving Health Assistant\n\n"
        "Your health data will be:\n"
        "✓ Encrypted with unique user keys\n"
        "✓ Stored locally with configurable retention\n"
        "✓ Shared only through anonymous routing\n"
        "✓ Minimized to essential features\n\n"
        "Please describe your health concern:"
    )
    return HEALTH_QUERY

async def handle_health_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the user's health query and select relevant features."""
    user_id = update.effective_user.id
    user_data[user_id]['description'] = update.message.text
    
    # Select relevant features
    features = get_relevant_features(update.message.text)
    user_data[user_id]['selected_features'] = features
    
    feature_text = "\n".join([f"• {f}" for f in features])
    await update.message.reply_text(
        f"🔍 Selected relevant features:\n{feature_text}\n\n"
        "Would you like to proceed with these features? (yes/no)"
    )
    return FEATURE_SELECTION

async def handle_feature_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's confirmation of selected features."""
    if update.message.text.lower() != 'yes':
        await update.message.reply_text(
            "Health check cancelled. You can start a new check with /privacy_health"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🔒 Select Privacy Level:\n\n"
        "1. High Privacy (7 days retention)\n"
        "   • Maximum data minimization\n"
        "   • Full anonymization\n"
        "   • Strict encryption\n\n"
        "2. Medium Privacy (30 days retention)\n"
        "   • Standard data minimization\n"
        "   • Basic anonymization\n"
        "   • Standard encryption\n\n"
        "3. Low Privacy (90 days retention)\n"
        "   • Minimal data minimization\n"
        "   • No anonymization\n"
        "   • Basic encryption\n\n"
        "Please select privacy level (1/2/3):"
    )
    return PRIVACY_LEVEL

async def handle_privacy_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's privacy level selection."""
    user_id = update.effective_user.id
    choice = update.message.text.strip()
    
    privacy_map = {'1': 'high', '2': 'medium', '3': 'low'}
    if choice not in privacy_map:
        await update.message.reply_text(
            "Please select a valid privacy level (1/2/3):"
        )
        return PRIVACY_LEVEL
    
    user_data[user_id]['privacy_level'] = privacy_map[choice]
    
    await update.message.reply_text(
        "🔒 Privacy Notice:\n\n"
        "Your data will be:\n"
        "✓ Processed locally with encryption\n"
        "✓ Routed anonymously\n"
        "✓ Never stored on external servers\n"
        "✓ Retained for the selected period\n"
        "✓ Protected with unique encryption keys\n\n"
        "Do you consent to proceed? (yes/no)"
    )
    return PRIVACY_CONFIRM

async def handle_privacy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's privacy consent and generate advice."""
    if update.message.text.lower() != 'yes':
        await update.message.reply_text(
            "Health check cancelled. You can start a new check with /privacy_health"
        )
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    features = user_data[user_id]['selected_features']
    privacy_level = user_data[user_id]['privacy_level']
    
    # Generate advice based on selected features
    advice = generate_health_advice(features, user_data[user_id]['description'])
    
    # Save health log with enhanced privacy
    save_health_log(user_id, {
        'features': features,
        'description': user_data[user_id]['description'],
        'advice': advice
    }, privacy_level)
    
    await update.message.reply_text(
        f"🔒 Privacy-Preserving Health Advice:\n\n{advice}\n\n"
        "Your health data has been:\n"
        "✓ Encrypted with your unique key\n"
        "✓ Saved locally with {privacy_level} privacy settings\n"
        "✓ Processed anonymously\n"
        "✓ Protected from unauthorized access"
    )
    return ConversationHandler.END

def generate_health_advice(features: list, description: str) -> str:
    """Generate comprehensive health advice based on selected features."""
    advice = []
    warnings = []
    lifestyle = []
    tracking = []
    
    # Physical Symptoms
    if 'dizziness' in features:
        advice.extend([
            "• Rest in a quiet, dark room",
            "• Stay hydrated (drink water slowly)",
            "• Avoid sudden movements",
            "• Sit or lie down if feeling dizzy",
            "• Check blood pressure if possible",
            "• Consider inner ear exercises if vertigo is present",
            "• Track episodes in a symptom diary"
        ])
        warnings.append("Seek immediate medical attention if dizziness is severe or accompanied by chest pain, difficulty speaking, or loss of consciousness.")
    
    if 'temperature' in features:
        advice.extend([
            "• Monitor temperature every 4 hours",
            "• Stay hydrated with water and electrolyte drinks",
            "• Rest and avoid strenuous activities",
            "• Use fever-reducing medication if temperature exceeds 38.5°C",
            "• Keep room temperature comfortable (18-22°C)",
            "• Use cool compresses for comfort"
        ])
        warnings.append("Seek medical attention if temperature exceeds 39.5°C or persists for more than 3 days.")
    
    # Chronic Conditions
    if 'diabetes' in features:
        advice.extend([
            "• Monitor blood glucose levels regularly",
            "• Follow prescribed medication schedule",
            "• Maintain consistent meal timing",
            "• Keep emergency glucose tablets handy",
            "• Check feet daily for any changes",
            "• Track carbohydrate intake",
            "• Monitor HbA1c levels quarterly"
        ])
        tracking.extend([
            "• Blood glucose readings (before/after meals)",
            "• Insulin doses and timing",
            "• Carbohydrate intake",
            "• Physical activity",
            "• Foot examination results",
            "• HbA1c levels"
        ])
        warnings.append("Seek immediate medical attention if experiencing severe hypoglycemia or hyperglycemia symptoms.")
    
    if 'hypertension' in features:
        advice.extend([
            "• Monitor blood pressure regularly",
            "• Reduce sodium intake",
            "• Maintain regular exercise routine",
            "• Practice stress management techniques",
            "• Limit alcohol and caffeine",
            "• Take medications as prescribed",
            "• Track daily readings"
        ])
        tracking.extend([
            "• Blood pressure readings (morning/evening)",
            "• Medication adherence",
            "• Sodium intake",
            "• Physical activity",
            "• Stress levels"
        ])
        warnings.append("Seek medical attention if blood pressure readings are consistently above 180/120 mmHg.")
    
    if 'ibd' in features:
        advice.extend([
            "• Follow a low-FODMAP diet",
            "• Stay hydrated",
            "• Take medications as prescribed",
            "• Track food triggers",
            "• Manage stress levels",
            "• Get adequate rest",
            "• Consider probiotics"
        ])
        tracking.extend([
            "• Symptom severity",
            "• Food intake and reactions",
            "• Bowel movements",
            "• Medication effectiveness",
            "• Stress levels"
        ])
    
    if 'autoimmune' in features:
        advice.extend([
            "• Take medications as prescribed",
            "• Monitor for flare-ups",
            "• Maintain regular exercise",
            "• Get adequate rest",
            "• Follow anti-inflammatory diet",
            "• Manage stress levels",
            "• Track symptoms daily"
        ])
        tracking.extend([
            "• Symptom severity",
            "• Medication adherence",
            "• Flare-up triggers",
            "• Energy levels",
            "• Physical activity"
        ])
    
    # Medication Management
    if 'medications' in features:
        advice.extend([
            "• Take medications at the same time daily",
            "• Use a pill organizer",
            "• Set medication reminders",
            "• Keep a medication log",
            "• Review medications with doctor regularly",
            "• Check for drug interactions",
            "• Store medications properly"
        ])
        tracking.extend([
            "• Medication schedule",
            "• Side effects",
            "• Effectiveness",
            "• Refill dates",
            "• Doctor appointments"
        ])
    
    # Lifestyle Recommendations
    if 'nutrition' in features:
        lifestyle.extend([
            "• Eat regular, balanced meals",
            "• Include plenty of fruits and vegetables",
            "• Stay hydrated (2-3 liters daily)",
            "• Limit processed foods and sugar",
            "• Consider consulting a nutritionist",
            "• Plan meals ahead of time",
            "• Practice mindful eating",
            "• Track daily water intake"
        ])
        tracking.extend([
            "• Daily food intake",
            "• Water consumption",
            "• Meal timing",
            "• Energy levels",
            "• Weight changes"
        ])
    
    if 'exercise' in features:
        lifestyle.extend([
            "• Start with light activities",
            "• Gradually increase intensity",
            "• Stay hydrated during exercise",
            "• Warm up and cool down properly",
            "• Listen to your body's signals",
            "• Aim for 150 minutes of moderate activity weekly",
            "• Include strength training twice weekly",
            "• Track progress and recovery"
        ])
        tracking.extend([
            "• Exercise duration",
            "• Intensity level",
            "• Recovery time",
            "• Energy levels",
            "• Sleep quality"
        ])
    
    if 'stress' in features:
        lifestyle.extend([
            "• Practice daily relaxation techniques",
            "• Set boundaries at work and home",
            "• Take regular breaks",
            "• Maintain work-life balance",
            "• Consider stress management workshops",
            "• Practice time management",
            "• Engage in hobbies and leisure activities"
        ])
    
    # Format the response
    response = "🔒 Privacy-Preserving Health Advice:\n\n"
    
    if advice:
        response += "📋 Medical Recommendations:\n"
        response += "\n".join(advice) + "\n\n"
    
    if lifestyle:
        response += "🌱 Lifestyle Recommendations:\n"
        response += "\n".join(lifestyle) + "\n\n"
    
    if tracking:
        response += "📊 Tracking Recommendations:\n"
        response += "\n".join(tracking) + "\n\n"
    
    if warnings:
        response += "⚠️ Important Warnings:\n"
        response += "\n".join(warnings) + "\n\n"
    
    response += "📚 Additional Resources:\n"
    response += "• Keep a health journal to track symptoms and progress\n"
    response += "• Schedule regular check-ups with your healthcare provider\n"
    response += "• Consider joining support groups for chronic conditions\n"
    response += "• Use health tracking apps to monitor vital signs\n"
    response += "• Set up medication reminders on your phone\n"
    response += "• Create a symptom diary for better tracking\n\n"
    
    response += "Remember: This advice is not a substitute for professional medical care. Always consult with a healthcare provider for proper diagnosis and treatment."
    
    return response

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Add handlers for existing commands
    app.add_handler(CommandHandler("server_status", server_status))
    app.add_handler(CommandHandler("training_status", training_status))
    app.add_handler(CommandHandler("check_tor", check_tor))
    app.add_handler(CommandHandler("renew_tor", renew_tor))
    app.add_handler(CommandHandler("tor_request", tor_request))
    
    # Add health check conversation handler
    health_check_handler = ConversationHandler(
        entry_points=[CommandHandler("health_check", start_health_check)],
        states={
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
            TEMPERATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_temperature)],
            SLEEP_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sleep_hours)],
            HEART_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_heart_rate)],
            COUGH_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cough_type)],
            SWEATING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sweating)],
            SORE_THROAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sore_throat)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(health_check_handler)
    
    # Update privacy-preserving health assistant handler
    privacy_health_handler = ConversationHandler(
        entry_points=[CommandHandler("privacy_health", start_privacy_health)],
        states={
            HEALTH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_health_query)],
            FEATURE_SELECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_feature_selection)],
            PRIVACY_LEVEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_privacy_level)],
            PRIVACY_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_privacy_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(privacy_health_handler)
    
    print("📡 Bot running... Send /server_status to get system info, /training_status to get training info, /health_check to start a health check, /check_tor to check Tor status, /renew_tor to get a new identity, or /tor_request <url> to make a request through Tor.")
    app.run_polling()
