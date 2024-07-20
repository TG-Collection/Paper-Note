from quart import Quart, request, jsonify, render_template, session, redirect, url_for, abort
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz
import random
import string
from bson.errors import InvalidId
import psutil
import time
import socket
from datetime import datetime, timedelta


start_time = time.time()

app = Quart(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your_secret_key")  # Make sure to set this in production

# MongoDB connection
client = AsyncIOMotorClient(os.environ.get("MONGODB_URL"))
db = client.floating_notes
notes_collection = db[os.environ.get('NOTES_COLLECTION_NAME', 'notes')]
users_collection = db['users']
public_spaces_collection = db['public_spaces']
status_collection = db['status']

def generate_short_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

async def is_space_locked(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    return space.get('locked', False) if space else False

async def is_space_hidden(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    return space.get('hidden', False) if space else False

@app.route('/api/public_spaces/<short_code>/toggle_hide', methods=['POST'])
async def toggle_hide_space(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space or space['creator'] != session['username']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    new_hidden_status = not space.get('hidden', False)
    await public_spaces_collection.update_one(
        {'short_code': short_code},
        {'$set': {'hidden': new_hidden_status}}
    )
    
    return jsonify({'hidden': new_hidden_status}), 200

@app.route('/api/create_public_space', methods=['POST'])
async def create_public_space():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    topic_name = data.get('topic_name', 'No Name')
    
    short_code = generate_short_code()  
    while await public_spaces_collection.find_one({'short_code': short_code}):
        short_code = generate_short_code()
    
    new_space = {
        'short_code': short_code,
        'creator': session['username'],
        'created_at': datetime.now(pytz.timezone('Asia/Kolkata')),
        'last_updated': datetime.now(pytz.timezone('Asia/Kolkata')),
        'topic_name': topic_name,
        'notes': [],
        'locked': False,
        'hidden': False
    }
    
    result = await public_spaces_collection.insert_one(new_space)
    
    return jsonify({'public_link': f'/go/{short_code}', 'space_id': str(result.inserted_id)}), 201

@app.route('/api/public_spaces', methods=['GET'])
async def list_public_spaces():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    username = session['username']
    
    cursor = public_spaces_collection.find({'creator': username})
    
    public_spaces = []
    async for space in cursor:
        space['_id'] = str(space['_id'])
        public_spaces.append({
            'id': space['_id'],
            'short_code': space['short_code'],
            'created_at': space['created_at'].isoformat(),
            'creator': space['creator'],
            'topic_name': space.get('topic_name', 'No Name'),
            'note_count': len(space['notes'])
        })
    
    return jsonify(public_spaces), 200

@app.route('/api/public_spaces/<short_code>/edit_topic', methods=['PUT'])
async def edit_topic_name(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = await request.json
    new_topic_name = data.get('topic_name')
    
    if not new_topic_name:
        return jsonify({'error': 'Topic name is required'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'creator': session['username']},
        {'$set': {'topic_name': new_topic_name}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Space not found or you do not have permission to edit it'}), 404
    
    return jsonify({'success': True, 'new_topic_name': new_topic_name}), 200


@app.route('/api/public_spaces/<short_code>/notes', methods=['POST'])
async def add_public_note(short_code):
    try:
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        
        space = await public_spaces_collection.find_one({'short_code': short_code})
        if not space:
            return jsonify({'error': 'Public space not found'}), 404
        
        if space.get('locked', False) and space['creator'] != session['username']:
            return jsonify({'error': 'This space is locked'}), 403

        data = await request.json
        content = data.get('content')
        
        if not content:
            return jsonify({'error': 'Content is required'}), 400
        
        if len(content) > 1000:
            return jsonify({'error': 'Note exceeds 1000 character limit'}), 400
        
        kolkata_tz = pytz.timezone('Asia/Kolkata')
        current_time = datetime.now(kolkata_tz)
        
        new_note = {
            '_id': ObjectId(),
            'content': content,
            'username': session['username'],
            'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
            'likes': 0,
            'dislikes': 0
        }
        result = await public_spaces_collection.update_one(
            {'short_code': short_code},
            {'$push': {'notes': new_note}}
        )  
        new_note['_id'] = str(new_note['_id'])
        return jsonify(new_note), 201
    except Exception as e:
        print(f"Error adding note: {str(e)}")  # Log the error
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500
    
@app.route('/api/public_spaces/<short_code>/notes/<note_id>', methods=['PATCH'])
async def edit_public_note(short_code, note_id):
    try:
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401

        space = await public_spaces_collection.find_one({'short_code': short_code})
        if not space:
            return jsonify({'error': 'Public space not found'}), 404

        if space.get('locked', False) and space['creator'] != session['username']:
            return jsonify({'error': 'This space is locked'}), 403

        data = await request.json
        new_content = data.get('content')
        if not new_content:
            return jsonify({'error': 'New content is required'}), 400

        if len(new_content) > 1000:
            return jsonify({'error': 'Note exceeds 1000 character limit'}), 400

        # Convert note_id from string to ObjectId for querying
        note_object_id = ObjectId(note_id)
        # Find and update the specific note
        result = await public_spaces_collection.update_one(
            {'short_code': short_code, 'notes._id': note_object_id},
            {'$set': {'notes.$.content': new_content}}
        )

        if result.modified_count == 0:
            return jsonify({'error': 'Note not found or content unchanged'}), 404

        return jsonify({'message': 'Note updated successfully'}), 200
    except Exception as e:
        print(f"Error editing note: {str(e)}")  # Log the error
        return jsonify({'error': f'An unexpected error occurred: {str(e)}'}), 500
    
@app.route('/api/public_spaces/<short_code>/toggle_lock', methods=['POST'])
async def toggle_lock(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space or space['creator'] != session['username']:
        return jsonify({'error': 'Unauthorized'}), 401
    
    new_lock_status = not space.get('locked', False)
    result = await public_spaces_collection.update_one(
        {'short_code': short_code},
        {'$set': {'locked': new_lock_status}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Failed to update lock status'}), 500
    
    return jsonify({'locked': new_lock_status}), 200


@app.route('/api/public_spaces/<short_code>/notes')
async def get_public_space_notes(short_code):
    try:
        space = await public_spaces_collection.find_one({'short_code': short_code})
        if not space:
            return jsonify({'error': 'Space not found'}), 404
        
        is_hidden = space.get('hidden', False)
        is_creator = session.get('username') == space['creator']
        
        if is_hidden and not is_creator:
            return jsonify({'error': 'Space is hidden'}), 403
        
        # Convert ObjectId to string if present
        if '_id' in space:
            space['_id'] = str(space['_id'])
        
        # Ensure any other ObjectId fields are converted to strings
        # This is just an example for 'notes', adjust according to your actual data structure
        if 'notes' in space and isinstance(space['notes'], list):
            for note in space['notes']:
                if '_id' in note:
                    note['_id'] = str(note['_id'])
        
        return jsonify({
            'topic': space['topic_name'],
            'creator': space['creator'],
            'locked': space['locked'],
            'hidden': space['hidden'],
            'notes': space['notes']
        })
    except Exception as e:
        print(f"Error in get_public_space_notes: {str(e)}")
        return jsonify({'error': 'An unexpected error occurred'}), 500

@app.route('/api/public_spaces/<short_code>/notes/<note_id>/like', methods=['POST'])
async def like_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'notes._id': object_id},
        {'$inc': {'notes.$.likes': 1}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found'}), 404
    
    return '', 204

@app.route('/api/public_spaces/<short_code>/notes/<note_id>/dislike', methods=['POST'])
async def dislike_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code, 'notes._id': object_id},
        {'$inc': {'notes.$.dislikes': 1}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found'}), 404
    
    return '', 204


@app.route('/api/public_spaces/<short_code>/notes/<note_id>', methods=['DELETE'])
async def delete_public_note(short_code, note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        object_id = ObjectId(note_id)
    except InvalidId:
        return jsonify({'error': 'Invalid note ID'}), 400
    
    result = await public_spaces_collection.update_one(
        {'short_code': short_code},
        {'$pull': {'notes': {'_id': object_id, 'username': session['username']}}}
    )
    
    if result.modified_count == 0:
        return jsonify({'error': 'Note not found or you do not have permission to delete it'}), 404
    
    return '', 204

@app.route('/api/public_spaces/<short_code>', methods=['DELETE'])
async def delete_public_space(short_code):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    result = await public_spaces_collection.delete_one({'short_code': short_code, 'creator': session['username']})
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Public space not found or you do not have permission to delete it'}), 404
    
    return '', 204

@app.route('/go/<short_code>')
async def public_space(short_code):
    space = await public_spaces_collection.find_one({'short_code': short_code})
    if not space:
        abort(404)
    
    is_hidden = space.get('hidden', False)
    is_creator = session.get('username') == space['creator']
    
    if is_hidden and not is_creator:
        return await render_template('hide.html')
    
    # Render the normal space view
    return await render_template('share.html', short_code=short_code)

@app.route('/api/notes', methods=['POST'])
async def add_note():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    content = data['content']
    
    # Check word limit (approximately 1000 characters)
    if len(content) > 1000:
        return jsonify({'error': 'Note exceeds 1000 character limit'}), 400
    
    kolkata_tz = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(kolkata_tz)
    note = {
        'content': content,
        'username': session['username'],
        'timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S %Z'),
    }
    result = await notes_collection.insert_one(note)
    note['_id'] = str(result.inserted_id)
    return jsonify(note), 201

@app.route('/api/notes', methods=['GET'])
async def get_notes():
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    search_query = request.args.get('search', '')
    query = {
        "username": session['username'],
        "content": {"$regex": search_query, "$options": "i"}
    } if search_query else {"username": session['username']}
    notes = await notes_collection.find(query).sort([("pinned", -1), ("timestamp", -1)]).to_list(length=None)
    return jsonify([{**note, '_id': str(note['_id'])} for note in notes])


@app.route('/api/notes/<note_id>', methods=['PUT'])
async def update_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = await request.json
    await notes_collection.update_one({'_id': ObjectId(note_id), 'username': session['username']}, {'$set': {'content': data['content']}})
    return '', 204

@app.route('/api/notes/<note_id>', methods=['DELETE'])
async def delete_note(note_id):
    if 'username' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    await notes_collection.delete_one({'_id': ObjectId(note_id), 'username': session['username']})
    return '', 204

@app.errorhandler(404)
async def not_found_error(error):
    return await render_template('error.html', error_code=404, error_message="Page Not Found", error_description="Sorry, the page you are looking for does not exist. It might have been moved or deleted."), 404

@app.errorhandler(500)
async def internal_error(error):
    return await render_template('error.html', error_code=500, error_message="Internal Server Error", error_description="The server encountered an internal error and was unable to complete your request."), 500

@app.errorhandler(Exception)
async def unhandled_exception(e):
    return await render_template('error.html', error_code=500, error_message="Unexpected Error", error_description=f"An unexpected error occurred: {str(e)}"), 500


@app.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'POST':
        data = await request.form
        username = data.get('username')
        password = data.get('password')
        user = await users_collection.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            next_url = request.args.get('next', url_for('index'))
            return redirect(next_url)
        return 'Invalid username or password', 401
    return await render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
async def register():
    if request.method == 'POST':
        data = await request.form
        username = data.get('username')
        password = data.get('password')
        existing_user = await users_collection.find_one({'username': username})
        if existing_user:
            return 'Username already exists', 400
        hashed_password = generate_password_hash(password)
        await users_collection.insert_one({'username': username, 'password': hashed_password})
        return redirect(url_for('login'))
    return await render_template('register.html')

@app.route('/api/check_login')
async def check_login():
    if 'username' in session:
        return jsonify({'logged_in': True, 'username': session['username']})
    return jsonify({'logged_in': False}), 401

@app.route('/logout')
async def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/auth')
async def auth():
    next_url = request.args.get('next', '/')
    return await render_template('auth.html', next_url=next_url)

@app.route('/')
async def index():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('index.html')

@app.route('/about')
async def about():
    return await render_template('about.html')

@app.route('/hide')
async def hidden_space():
    return await render_template('hide.html')

@app.route('/spaces')
async def spaces():
    if 'username' not in session:
        return redirect(url_for('login'))
    return await render_template('space.html')


def get_service_running_time():
    running_time = time.time() - start_time
    return str(timedelta(seconds=int(running_time)))

@app.route('/status')
async def status():
    return await render_template('status.html')

@app.route('/api/status')
async def api_status():
    # Fetch data from MongoDB
    total_users = await users_collection.count_documents({})
    total_spaces = await public_spaces_collection.count_documents({})
    # Get current system stats
    cpu_load = psutil.cpu_percent()
    ram_usage = psutil.virtual_memory().percent
    disk_usage = psutil.disk_usage('/').percent
    # Get MongoDB storage info
    storage_size = await db.command("dbStats")
    mongodb_storage = storage_size['storageSize'] / (1024 * 1024)  # Convert to MB
    # Get IP address info
    hostname = socket.gethostname()
    current_ip = socket.gethostbyname(hostname)
    # Update status collection with current IP if it's new
    await status_collection.update_one(
        {'ip': current_ip},
        {'$set': {'last_seen': datetime.now()}},
        upsert=True
    )
    status_data = {
        'total_users': total_users,
        'current_ip': current_ip,
        'mongodb_storage': f"{mongodb_storage:.2f} MB",
        'total_spaces': total_spaces,
        'service_running_time': get_service_running_time(),
        'cpu_load': f"{cpu_load:.1f}%",
        'ram_usage': f"{ram_usage:.1f}%",
        'disk_usage': f"{disk_usage:.1f}%"
    }
    return jsonify(status_data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))