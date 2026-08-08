"""
Return-to-Sender Email Interceptor - Complete Application
"""
import os
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
import threading
import time
from queue import Queue

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key')
CORS(app)

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Storage
messages: Dict[str, Dict] = {}
active_rooms: Dict[str, set] = {}
message_queue = Queue()

# ============================================================================
# STAMP GENERATOR
# ============================================================================

class StampGenerator:
    @staticmethod
    def create_stamp(width: int = 40) -> List[str]:
        stamp = [
            "╔" + "═" * (width - 2) + "╗",
            "║" + " " * ((width - 15) // 2) + "RETURN TO SENDER" + " " * ((width - 15) // 2) + "║",
            "║" + " " * ((width - 19) // 2) + "✦ UNDELIVERABLE ✦" + " " * ((width - 19) // 2) + "║",
            "║" + " " * ((width - 12) // 2) + "🔴 REDIRECTED" + " " * ((width - 12) // 2) + "║",
            "║" + " " * ((width - 20) // 2) + f"⏰ {datetime.now().strftime('%H:%M')}" + " " * ((width - 20) // 2) + "║",
            "╚" + "═" * (width - 2) + "╗"
        ]
        return stamp
    
    @staticmethod
    def apply_stamp_to_content(content: str) -> str:
        stamp_lines = StampGenerator.create_stamp()
        stamp_text = '\n'.join(stamp_lines)
        return f"{content}\n\n{'─' * 40}\n{stamp_text}\n{'─' * 40}"

# ============================================================================
# CORE FUNCTIONS
# ============================================================================

def save_message(message: Dict):
    messages[message['id']] = message
    return True

def get_message(message_id: str) -> Optional[Dict]:
    return messages.get(message_id)

def get_all_messages() -> List[Dict]:
    return list(messages.values())

def apply_return_to_sender(message_id: str) -> bool:
    try:
        message = get_message(message_id)
        if not message or message['is_returned']:
            return False
        
        message['is_returned'] = True
        message['return_timestamp'] = datetime.now().isoformat()
        message['status'] = 'returned'
        message['stamp_overlay'] = '\n'.join(StampGenerator.create_stamp())
        message['content'] = StampGenerator.apply_stamp_to_content(message['content'])
        
        save_message(message)
        
        socketio.emit('message_returned', {
            'message_id': message_id,
            'sender': message['sender'],
            'recipient': message['recipient'],
            'subject': message['subject'],
            'timestamp': message['return_timestamp']
        }, room='dashboard')
        
        logger.info(f"📬 Message {message_id} returned to sender")
        return True
        
    except Exception as e:
        logger.error(f"Error returning message: {e}")
        return False

def process_messages_in_background():
    while True:
        try:
            message_id = message_queue.get(timeout=1)
            if message_id:
                time.sleep(1)
                apply_return_to_sender(message_id)
        except:
            pass
        time.sleep(0.1)

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/messages')
def get_messages_endpoint():
    all_messages = get_all_messages()
    all_messages.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify({
        'messages': all_messages,
        'total': len(all_messages)
    })

@app.route('/api/message/<message_id>')
def get_message_endpoint(message_id):
    message = get_message(message_id)
    if not message:
        return jsonify({'error': 'Message not found'}), 404
    return jsonify(message)

@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.json
    
    required_fields = ['sender', 'recipient', 'subject', 'content']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'Missing field: {field}'}), 400
    
    message_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    
    message = {
        'id': message_id,
        'sender': data['sender'],
        'recipient': data['recipient'],
        'subject': data['subject'],
        'content': data['content'],
        'timestamp': timestamp,
        'is_returned': False,
        'return_timestamp': None,
        'status': 'pending',
        'stamp_overlay': ''
    }
    
    save_message(message)
    message_queue.put(message_id)
    
    socketio.emit('message_received', {
        'message_id': message_id,
        'sender': data['sender'],
        'subject': data['subject'],
        'timestamp': timestamp
    }, room='dashboard')
    
    return jsonify({
        'message': 'Message intercepted successfully',
        'message_id': message_id,
        'status': 'processing'
    })

@app.route('/api/return/<message_id>', methods=['POST'])
def return_message_endpoint(message_id):
    result = apply_return_to_sender(message_id)
    if result:
        return jsonify({
            'message': 'Message returned to sender',
            'message_id': message_id,
            'status': 'returned'
        })
    else:
        return jsonify({'error': 'Failed to return message'}), 500

@app.route('/api/stats')
def get_stats():
    all_messages = get_all_messages()
    total = len(all_messages)
    returned = sum(1 for m in all_messages if m['is_returned'])
    pending = total - returned
    
    return jsonify({
        'total_messages': total,
        'returned_count': returned,
        'pending_count': pending,
        'active_users': len(active_rooms),
        'storage': 'in-memory'
    })

@app.route('/api/clear', methods=['POST'])
def clear_messages():
    messages.clear()
    return jsonify({'message': 'All messages cleared'})

# ============================================================================
# WEBSOCKET EVENTS
# ============================================================================

@socketio.on('connect')
def handle_connect():
    session_id = request.sid
    join_room('dashboard')
    active_rooms[session_id] = {'rooms': ['dashboard']}
    emit('connected', {'status': 'Connected'})

@socketio.on('disconnect')
def handle_disconnect():
    session_id = request.sid
    if session_id in active_rooms:
        del active_rooms[session_id]

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_sample_data():
    if not get_all_messages():
        sample_messages = [
            {
                'id': str(uuid.uuid4()),
                'sender': 'alice@company.com',
                'recipient': 'bob@company.com',
                'subject': 'Project Update Needed',
                'content': 'Bob, please send the Q3 projections by EOD.',
                'timestamp': datetime.now().isoformat(),
                'is_returned': False,
                'return_timestamp': None,
                'status': 'pending',
                'stamp_overlay': ''
            },
            {
                'id': str(uuid.uuid4()),
                'sender': 'carol@domain.com',
                'recipient': 'dave@domain.com',
                'subject': 'Meeting Reschedule',
                'content': 'Dave, can we move the 3pm meeting to 4pm?',
                'timestamp': datetime.now().isoformat(),
                'is_returned': True,
                'return_timestamp': datetime.now().isoformat(),
                'status': 'returned',
                'stamp_overlay': '\n'.join(StampGenerator.create_stamp())
            }
        ]
        
        sample_messages[1]['content'] = StampGenerator.apply_stamp_to_content(
            sample_messages[1]['content']
        )
        
        for msg in sample_messages:
            save_message(msg)

# Start background processor
processor_thread = threading.Thread(target=process_messages_in_background, daemon=True)
processor_thread.start()

# Initialize data
initialize_sample_data()

# ============================================================================
# MAIN APPLICATION
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(
        app,
        debug=False,
        host='0.0.0.0',
        port=port
  )
